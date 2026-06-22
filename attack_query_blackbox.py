"""Query-based black-box attack on InternVL2 (decision-based SPSA).

Transfer (path A) maxed PERCEPTION on InternVL2 (sign ~1.0) but plateaued on the
DECISION (action ~.6, sign^action ~.65) -- the wall is the LM, which transfer
can't touch. This attack uses InternVL2's OWN OUTPUT as feedback (no gradients =
black-box) to push past that wall.

- Score = fraction of optimization images where InternVL2 answers "STOP" to a
  FORCED STOP/GO driving prompt (directly targets the decision).
- Optimizer = SPSA (2 queries / gradient estimate) in a reduced 16x16x3 space
  upsampled to the 64x64 patch -> few enough effective dims for query efficiency.
- Warm-start = the best transfer patch (div5), which already sits near the
  decision boundary, so only a few hundred queries are needed.
- The optimization prompt (forced STOP/GO) is DISTINCT from the eval prompts
  (sign_stop + decision_safe_action), so the reported sign^action conjunction is
  not optimized against directly.

InternVL2 is local, so queries are free -- this is also the validated dry-run for
the same loop on the paid Gemini API (swap the score() backend for Gemini logprobs).

Run on w6908 GPU via cm-w6908.sock (see project memory). Score uses only model.chat.
"""
import argparse
import random
from pathlib import Path

import torch
import torch.nn.functional as F
from PIL import Image
from torchvision import transforms
from torchvision.utils import save_image
from transformers import AutoModel, AutoTokenizer

from eval_transfer_internvl import build_internvl_pixel_values
from eval_transfer import EN_PROMPTS, classify_response
from eval_qwen_multprompt_patch import LABEL_STOP_ACTION

# Optimize the DECISION ("best action") -- it has headroom (clean ~.05, transfer ~.625)
# and is the wall. The forced STOP/GO prompt is useless here: InternVL2 saturates to
# STOP on it even for clean images (conservative prior), so there is nothing to optimize.
# Since transfer already maxes perception (sign ~1.0), pushing action ~= pushing sign^action.
OPT_PROMPT_ID = "decision_safe_action"
OPT_PROMPT = next(p.text for p in EN_PROMPTS if p.prompt_id == OPT_PROMPT_ID)

EXTS = {".jpg", ".jpeg", ".png"}


def load_images(root, image_size, n, seed):
    paths = sorted(p for p in Path(root).rglob("*") if p.suffix.lower() in EXTS)
    random.Random(seed).shuffle(paths)
    paths = paths[:n]
    t = transforms.Compose([
        transforms.Resize(image_size, interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.CenterCrop(image_size), transforms.ToTensor()])
    return [t(Image.open(p).convert("RGB")) for p in paths]


def apply_center(img, patch):
    a = img.clone()
    h, w = img.shape[-2:]
    p = patch.shape[-1]
    t, l = (h - p) // 2, (w - p) // 2
    a[:, t:t + p, l:l + p] = patch
    return a


def patch_from_z(z, base, eps, patch_size):
    """patch = base (full-res transfer patch, kept intact) + a bounded low-dim nudge.

    Adversarial patches need high-frequency structure, so we do NOT re-parameterize
    the whole patch at low resolution (that destroys it). Instead we keep `base`
    (div5) and let SPSA add delta = tanh(z)*eps (low_dim, upsampled) on top.
    z=0 -> patch == base, so warm-start preserves the transfer patch's effect.
    """
    delta = (torch.tanh(z) * eps).unsqueeze(0)
    delta = F.interpolate(delta, size=patch_size, mode="bilinear", align_corners=False).squeeze(0)
    return (base + delta).clamp(0, 1)


def says_stop(resp):
    return classify_response(OPT_PROMPT_ID, resp if isinstance(resp, str) else str(resp)) == LABEL_STOP_ACTION


def parse_args():
    p = argparse.ArgumentParser(description="SPSA query-based black-box attack on InternVL2.")
    p.add_argument("--model-id", default="OpenGVLab/InternVL2-2B")
    p.add_argument("--out-dir", type=Path, default=Path("outputs_query_attack"))
    p.add_argument("--warm-start", type=Path, default=Path("outputs_ensemble_div5/ensemble_div5.png"))
    p.add_argument("--opt-dir", type=Path, default=Path("data/train"), help="optimization images (disjoint from test eval)")
    p.add_argument("--n-opt", type=int, default=12)
    p.add_argument("--image-size", type=int, default=224)
    p.add_argument("--patch-size", type=int, default=64)
    p.add_argument("--tile-size", type=int, default=448)
    p.add_argument("--low-dim", type=int, default=16, help="SPSA optimizes a low_dim x low_dim additive nudge, upsampled to patch_size")
    p.add_argument("--eps", type=float, default=0.15, help="max per-pixel nudge added on top of the base patch")
    p.add_argument("--c", type=float, default=0.4, help="SPSA perturbation size (z space)")
    p.add_argument("--lr", type=float, default=4.0, help="SPSA ascent step")
    p.add_argument("--max-queries", type=int, default=600)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--max-new-tokens", type=int, default=16)
    return p.parse_args()


def main():
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device("cuda")
    dtype = torch.float16

    print(f"loading {args.model_id} ...", flush=True)
    model = AutoModel.from_pretrained(args.model_id, torch_dtype=dtype, trust_remote_code=True,
                                      low_cpu_mem_usage=True).eval().to(device)
    tok = AutoTokenizer.from_pretrained(args.model_id, trust_remote_code=True, use_fast=False)
    gen = dict(max_new_tokens=args.max_new_tokens, do_sample=False)

    images = load_images(args.opt_dir, args.image_size, args.n_opt, args.seed)
    print(f"opt images={len(images)} from {args.opt_dir}", flush=True)

    qcount = [0]

    def score_patch(patch):
        """fraction of opt images where InternVL2 answers STOP. patch=None -> clean."""
        hits = 0
        patch_cpu = None if patch is None else patch.detach().cpu()
        for img in images:
            a = img if patch_cpu is None else apply_center(img, patch_cpu)
            pil_img = transforms.functional.to_pil_image(a.clamp(0, 1))
            pv = build_internvl_pixel_values(pil_img, args.tile_size, device, dtype)
            r = model.chat(tok, pv, OPT_PROMPT, gen)
            qcount[0] += 1
            if says_stop(r if isinstance(r, str) else str(r)):
                hits += 1
        return hits / len(images)

    # base = transfer patch (div5) at FULL res, kept intact; SPSA adds a low-dim nudge.
    d = args.low_dim
    base = transforms.ToTensor()(Image.open(args.warm_start).convert("RGB"))
    base = F.interpolate(base.unsqueeze(0), size=args.patch_size, mode="bilinear",
                         align_corners=False).squeeze(0).to(device)

    def score(z):
        return score_patch(patch_from_z(z, base, args.eps, args.patch_size))

    z = torch.zeros(3, d, d, device=device)  # z=0 -> patch == base (div5)

    # baselines (clean = no patch; warm-start = base patch == div5)
    clean_score = score_patch(None)
    s0 = score(z)
    best_z, best_s = z.clone(), s0
    print(f"base={args.warm_start} full {args.patch_size}px; nudge eps={args.eps} low_dim={d}", flush=True)
    print(f"[baseline] clean STOP-rate={clean_score:.3f} | base(div5) STOP-rate={s0:.3f} "
          f"(opt prompt = {OPT_PROMPT_ID}, n={len(images)})", flush=True)

    it = 0
    while qcount[0] + 2 * len(images) <= args.max_queries:
        it += 1
        delta = (torch.randint(0, 2, z.shape, device=device).float() * 2 - 1)
        zp, zm = z + args.c * delta, z - args.c * delta
        sp, sm = score(zp), score(zm)
        z = z + args.lr * ((sp - sm) / (2 * args.c)) * delta
        if sp > best_s:
            best_z, best_s = zp.clone(), sp
        if sm > best_s:
            best_z, best_s = zm.clone(), sm
        print(f"iter {it:3d} | s+={sp:.3f} s-={sm:.3f} | best={best_s:.3f} | queries={qcount[0]}/{args.max_queries}", flush=True)

    final_patch = patch_from_z(best_z, base, args.eps, args.patch_size).detach().cpu().clamp(0, 1)
    save_image(final_patch, args.out_dir / "query_patch.png")
    print(f"\nDONE  best_opt_STOP_rate={best_s:.3f}  queries_used={qcount[0]}", flush=True)
    print(f"saved -> {args.out_dir/'query_patch.png'}", flush=True)


if __name__ == "__main__":
    main()
