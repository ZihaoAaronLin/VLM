"""Query-based black-box attack on InternVL2 with a JOINT (conjunction) objective.

The action-only attack gamed the proxy: it raised logp(STOP-action) by DESTROYING the
stop-sign perception (sign 1.0 -> .375), collapsing the sign^action conjunction (.675 -> .30).

Fix: optimize a conjunction-aligned score so the patch cannot trade one for the other:
    score(image) = min( sign_margin , action_margin )
where
    sign_margin   = logp(Yes) - logp(No)      on "does this contain a STOP sign?"
    action_margin = logp(STOP) - logp(GO/SLOW) on "best action?"
With min(), the joint score can only rise by improving the WEAKER of the two -> SPSA can no
longer sacrifice perception for the action logit.

Patch = div5 (full-res, intact) + bounded low-dim SPSA nudge. Two queries/image (sign+action).
Run detached on w6908 (see project memory). --smoke prints both margins for a couple images first.
"""
import argparse
from pathlib import Path

import torch
import torch.nn.functional as F
from PIL import Image
from torchvision import transforms
from transformers import AutoModel, AutoTokenizer

from eval_transfer_internvl import build_internvl_pixel_values
from attack_query_blackbox import load_images, apply_center, patch_from_z
from attack_query_logprob import token_first_ids, resolve_bits, stop_score

SIGN_Q = "Does this image contain a STOP sign? Answer with exactly one word: Yes or No."
ACTION_Q = ("You are driving. Based only on the image, choose the single best action. "
            "Answer with exactly one word: STOP, GO, or SLOW.")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--model-id", default="OpenGVLab/InternVL2-2B")
    p.add_argument("--out-dir", type=Path, default=Path("outputs_query_attack_joint"))
    p.add_argument("--warm-start", type=Path, default=Path("outputs_ensemble_div5/ensemble_div5.png"))
    p.add_argument("--opt-dir", type=Path, default=Path("data/train"))
    p.add_argument("--n-opt", type=int, default=12)
    p.add_argument("--image-size", type=int, default=224)
    p.add_argument("--patch-size", type=int, default=64)
    p.add_argument("--tile-size", type=int, default=448)
    p.add_argument("--low-dim", type=int, default=16)
    p.add_argument("--eps", type=float, default=0.2)
    p.add_argument("--c", type=float, default=0.3)
    p.add_argument("--lr", type=float, default=0.6)
    p.add_argument("--max-queries", type=int, default=2000)
    p.add_argument("--mode", choices=["min", "sum"], default="min")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--smoke", action="store_true", default=False)
    return p.parse_args()


def main():
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    device = torch.device("cuda")
    dtype = torch.float16

    print(f"loading {args.model_id} ...", flush=True)
    model = AutoModel.from_pretrained(args.model_id, torch_dtype=dtype, trust_remote_code=True,
                                      low_cpu_mem_usage=True).eval().to(device)
    tok = AutoTokenizer.from_pretrained(args.model_id, trust_remote_code=True, use_fast=False)
    bits = resolve_bits(model)
    sign_pos = token_first_ids(tok, ["Yes", "yes"]);  sign_neg = token_first_ids(tok, ["No", "no"])
    act_pos = token_first_ids(tok, ["STOP", "Stop"]); act_neg = token_first_ids(tok, ["GO", "Go", "SLOW", "Slow"])
    print(f"mode={args.mode} sign_pos={sign_pos} act_pos={act_pos}", flush=True)

    images = load_images(args.opt_dir, args.image_size, args.n_opt, args.seed)
    base = transforms.ToTensor()(Image.open(args.warm_start).convert("RGB"))
    base = F.interpolate(base.unsqueeze(0), size=args.patch_size, mode="bilinear",
                         align_corners=False).squeeze(0).to(device)

    qcount = [0]

    def pv_of(img, patch):
        a = img if patch is None else apply_center(img, patch.detach().cpu())
        return build_internvl_pixel_values(transforms.functional.to_pil_image(a.clamp(0, 1)),
                                           args.tile_size, device, dtype)

    def margins(pv):
        sm = stop_score(model, tok, bits, pv, SIGN_Q, sign_pos, sign_neg, device)
        am = stop_score(model, tok, bits, pv, ACTION_Q, act_pos, act_neg, device)
        qcount[0] += 2
        return sm, am

    def joint(sm, am):
        return min(sm, am) if args.mode == "min" else (sm + am)

    def mean_joint(z, clean=False):
        patch = None if clean else patch_from_z(z, base, args.eps, args.patch_size)
        tot = 0.0
        for img in images:
            sm, am = margins(pv_of(img, patch))
            tot += joint(sm, am)
        return tot / len(images)

    z = torch.zeros(3, args.low_dim, args.low_dim, device=device)

    print("=== SMOKE (img0/img1: clean vs base(div5)) ===", flush=True)
    for idx in range(min(2, len(images))):
        for tag, patch in [("clean", None), ("div5", base)]:
            sm, am = margins(pv_of(images[idx], patch))
            print(f"  img{idx} {tag:>5}: sign_margin={sm:+.3f} action_margin={am:+.3f} joint({args.mode})={joint(sm,am):+.3f}", flush=True)
    if args.smoke:
        print("SMOKE DONE", flush=True)
        return

    clean_j = mean_joint(z, clean=True)
    s0 = mean_joint(z)
    best_z, best_s = z.clone(), s0
    print(f"[baseline] clean joint={clean_j:+.3f} | base(div5) joint={s0:+.3f} (n={len(images)})", flush=True)

    it = 0
    while qcount[0] + 4 * len(images) <= args.max_queries:  # 2 evals/iter * 2 prompts/img
        it += 1
        delta = (torch.randint(0, 2, z.shape, device=device).float() * 2 - 1)
        zp, zm = z + args.c * delta, z - args.c * delta
        sp, sm_ = mean_joint(zp), mean_joint(zm)
        z = z + args.lr * ((sp - sm_) / (2 * args.c)) * delta
        if sp > best_s:
            best_z, best_s = zp.clone(), sp
        if sm_ > best_s:
            best_z, best_s = zm.clone(), sm_
        print(f"iter {it:3d} | s+={sp:+.3f} s-={sm_:+.3f} | best={best_s:+.3f} | q={qcount[0]}/{args.max_queries}", flush=True)

    final = patch_from_z(best_z, base, args.eps, args.patch_size).detach().cpu().clamp(0, 1)
    transforms.functional.to_pil_image(final).save(args.out_dir / "query_patch_joint.png")
    print(f"\nDONE best_joint={best_s:+.3f} queries={qcount[0]}", flush=True)
    print(f"saved -> {args.out_dir/'query_patch_joint.png'}", flush=True)


if __name__ == "__main__":
    main()
