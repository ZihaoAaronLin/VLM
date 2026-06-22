"""Query-based black-box attack on InternVL2 with a SMOOTH (logprob) score.

The hard-label SPSA stalled: the decision is a cliff (any nudge flips all images to
0 -> zero gradient). Here the score is continuous -- the log-prob the model assigns
to answering "Yes" (you must stop) vs "No" on a forced yes/no decision prompt -- so
SPSA gets a gradient even when the discrete answer hasn't flipped yet.

score(image) = logsumexp(logp[Yes tokens]) - logsumexp(logp[No tokens])
at the first generated token. Read from InternVL2's own generate(output_scores=True)
(no gradients through the model = still black-box; the same signal Gemini exposes via
responseLogprobs, so this loop ports to the paid API later).

Patch = base (div5, full-res, intact) + bounded low-dim nudge (SPSA over the nudge).

--smoke: just print, for a couple images, the top next-token candidates + the Yes/No
logprobs + the score, to validate the extraction and see the score scale BEFORE a full
run. Run detached on w6908 (see project memory); read the log.
"""
import argparse
import sys
from pathlib import Path

import torch
import torch.nn.functional as F
from PIL import Image
from torchvision import transforms

from transformers import AutoModel, AutoTokenizer
from eval_transfer_internvl import build_internvl_pixel_values
from attack_query_blackbox import load_images, apply_center, patch_from_z

OPT_Q = ("You are driving. Based only on the image, must you STOP the vehicle right now? "
         "Answer only Yes or No.")


def token_first_ids(tok, words):
    ids = set()
    for w in words:
        e = tok.encode(w, add_special_tokens=False)
        if e:
            ids.add(e[0])
    return sorted(ids)


def resolve_bits(model):
    """InternVL2 image-token constants + get_conv_template, with fallbacks (this version
    does NOT export IMG_*_TOKEN into the modeling module namespace)."""
    import importlib
    mmod = sys.modules[type(model).__module__]
    IMG_START = getattr(mmod, "IMG_START_TOKEN", "<img>")
    IMG_END = getattr(mmod, "IMG_END_TOKEN", "</img>")
    IMG_CTX = getattr(mmod, "IMG_CONTEXT_TOKEN", "<IMG_CONTEXT>")
    gct = getattr(mmod, "get_conv_template", None)
    if gct is None:
        base_pkg = type(model).__module__.rsplit(".", 1)[0]
        gct = importlib.import_module(base_pkg + ".conversation").get_conv_template
    return IMG_START, IMG_END, IMG_CTX, gct


@torch.no_grad()
def stop_score(model, tok, bits, pixel_values, question, pos_ids, neg_ids, device, debug=False):
    """logp(Yes) - logp(No) for the first answer token. Reuses InternVL2's chat prep."""
    IMG_START, IMG_END, IMG_CTX, get_conv = bits
    num_patches = pixel_values.shape[0]
    model.img_context_token_id = tok.convert_tokens_to_ids(IMG_CTX)
    template = get_conv(getattr(model, "template", None) or model.config.template)
    template.system_message = getattr(model, "system_message", "") or ""
    template.append_message(template.roles[0], "<image>\n" + question)
    template.append_message(template.roles[1], None)
    query = template.get_prompt()
    image_tokens = IMG_START + IMG_CTX * (model.num_image_token * num_patches) + IMG_END
    query = query.replace("<image>", image_tokens, 1)
    inp = tok(query, return_tensors="pt")
    out = model.generate(pixel_values=pixel_values, input_ids=inp["input_ids"].to(device),
                         attention_mask=inp["attention_mask"].to(device),
                         max_new_tokens=1, do_sample=False,
                         output_scores=True, return_dict_in_generate=True)
    logp = torch.log_softmax(out.scores[0][0].float(), dim=-1)
    pos = torch.logsumexp(logp[torch.tensor(pos_ids, device=device)], 0).item()
    neg = torch.logsumexp(logp[torch.tensor(neg_ids, device=device)], 0).item()
    if debug:
        tv, ti = logp.topk(6)
        top = [(tok.decode([i]).strip(), round(v, 2)) for i, v in zip(ti.tolist(), tv.tolist())]
        return pos - neg, pos, neg, top
    return pos - neg


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--model-id", default="OpenGVLab/InternVL2-2B")
    p.add_argument("--out-dir", type=Path, default=Path("outputs_query_attack"))
    p.add_argument("--warm-start", type=Path, default=Path("outputs_ensemble_div5/ensemble_div5.png"))
    p.add_argument("--opt-dir", type=Path, default=Path("data/train"))
    p.add_argument("--n-opt", type=int, default=12)
    p.add_argument("--image-size", type=int, default=224)
    p.add_argument("--patch-size", type=int, default=64)
    p.add_argument("--tile-size", type=int, default=448)
    p.add_argument("--low-dim", type=int, default=16)
    p.add_argument("--eps", type=float, default=0.15)
    p.add_argument("--c", type=float, default=0.1)
    p.add_argument("--lr", type=float, default=0.05)
    p.add_argument("--max-queries", type=int, default=800)
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
    pos_ids = token_first_ids(tok, ["Yes", "yes", " Yes", " yes"])
    neg_ids = token_first_ids(tok, ["No", "no", " No", " no"])
    print(f"pos_ids={pos_ids} neg_ids={neg_ids} | num_image_token={model.num_image_token} "
          f"template={getattr(model,'template',None)}", flush=True)

    images = load_images(args.opt_dir, args.image_size, args.n_opt, args.seed)
    base = transforms.ToTensor()(Image.open(args.warm_start).convert("RGB"))
    base = F.interpolate(base.unsqueeze(0), size=args.patch_size, mode="bilinear",
                         align_corners=False).squeeze(0).to(device)

    def pv_of(img, patch):
        a = img if patch is None else apply_center(img, patch.detach().cpu())
        pil = transforms.functional.to_pil_image(a.clamp(0, 1))
        return build_internvl_pixel_values(pil, args.tile_size, device, dtype)

    qcount = [0]

    def mean_score(z, patch_override="z"):
        patch = None if patch_override is None else patch_from_z(z, base, args.eps, args.patch_size)
        tot = 0.0
        for img in images:
            tot += stop_score(model, tok, bits, pv_of(img, patch), OPT_Q, pos_ids, neg_ids, device)
            qcount[0] += 1
        return tot / len(images)

    z = torch.zeros(3, args.low_dim, args.low_dim, device=device)

    # ---- SMOKE: validate extraction + see scale on a couple images ----
    print("=== SMOKE (img0/img1: clean vs base(div5)) ===", flush=True)
    for idx in range(min(2, len(images))):
        for tag, patch in [("clean", None), ("div5", base)]:
            s, pos, neg, top = stop_score(model, tok, bits, pv_of(images[idx], patch),
                                          OPT_Q, pos_ids, neg_ids, device, debug=True)
            print(f"  img{idx} {tag:>5}: score(Yes-No)={s:+.3f}  logp(Yes)={pos:.2f} logp(No)={neg:.2f} | top: {top}", flush=True)
    if args.smoke:
        print("SMOKE DONE", flush=True)
        return

    clean_mean = mean_score(z, patch_override=None)
    s0 = mean_score(z)
    best_z, best_s = z.clone(), s0
    print(f"[baseline] clean mean-score={clean_mean:+.3f} | base(div5) mean-score={s0:+.3f} (n={len(images)})", flush=True)

    it = 0
    while qcount[0] + 2 * len(images) <= args.max_queries:
        it += 1
        delta = (torch.randint(0, 2, z.shape, device=device).float() * 2 - 1)
        zp, zm = z + args.c * delta, z - args.c * delta
        sp, sm = mean_score(zp), mean_score(zm)
        z = z + args.lr * ((sp - sm) / (2 * args.c)) * delta
        if sp > best_s:
            best_z, best_s = zp.clone(), sp
        if sm > best_s:
            best_z, best_s = zm.clone(), sm
        print(f"iter {it:3d} | s+={sp:+.3f} s-={sm:+.3f} | best={best_s:+.3f} | q={qcount[0]}/{args.max_queries}", flush=True)

    final = patch_from_z(best_z, base, args.eps, args.patch_size).detach().cpu().clamp(0, 1)
    transforms.functional.to_pil_image(final).save(args.out_dir / "query_patch_logprob.png")
    print(f"\nDONE best_mean_score={best_s:+.3f} queries={qcount[0]}", flush=True)
    print(f"saved -> {args.out_dir/'query_patch_logprob.png'}", flush=True)


if __name__ == "__main__":
    main()
