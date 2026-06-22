"""Encoder<->VLM reconciliation, encoder side: a prompt-free measure of how far
the stop-patch moves a vision encoder's image embedding, and how much of that
move is *toward the stop concept*.

eval_clip_transfer.py measured discrete argmax zero-shot flips (does the top label
land in the STOP group). This script measures the same effect continuously, with
no decision threshold, so we can say *how much* the representation moves and *in
what direction* -- and compare it to the n-seed random control. Combined with the
already-measured VLM-output null (LLaVA-OneVision / SmolVLM, which are built on
SigLIP, do NOT change their answer), it pins down the reconciliation: foreign
encoders ARE perturbed toward "stop", yet the VLM output is not fooled -> the
adversarial signal is attenuated downstream in the projection + LM.

Per (encoder, variant), averaged over images, vs the random control:
  cos_disp     = mean( 1 - cos(emb_patched, emb_clean) )          # raw movement, any direction
  stop_align   = mean( cos(emb_patched, stop_dir) )               # absolute alignment to stop concept
  d_stop_align = mean( cos(emb_patched, stop_dir) - cos(emb_clean, stop_dir) )  # directional shift toward stop
where stop_dir = normalize( mean(STOP texts) - mean(NEG texts) ) in that encoder's
embedding space. d_stop_align is the headline: whitebox/heldout above the random
mean is the adversarial directional push.

Run on w6908 GPU via the nested hop (see project memory). Free.

Example:
    CUDA_VISIBLE_DEVICES=1 ~/miniconda3/envs/vlm/bin/python analyze_encoder_shift.py \
        --out-dir outputs_encoder_shift --clip-patch-path outputs/final_stop_patch.png --n-random 5
"""
import argparse
import json
import statistics
from argparse import Namespace
from pathlib import Path

import torch
import torch.nn.functional as F
import open_clip

import eval_qwen_multprompt_patch as base
from eval_clip_transfer import STOP_TEXTS, NEG_TEXTS, DEFAULT_MODELS, build_variant_pils, encode_texts


def parse_args():
    p = argparse.ArgumentParser(description="Prompt-free encoder embedding-shift analysis for the stop-patch.")
    p.add_argument("--out-dir", type=Path, default=Path("outputs_encoder_shift"))
    p.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    p.add_argument("--patch-path", type=Path, default=Path("outputs_qwen_whitebox/qwen_stop_patch.png"))
    p.add_argument("--heldout-path", type=Path, default=Path("outputs_qwen_heldout/qwen_heldout_patch.png"))
    p.add_argument("--clip-patch-path", type=Path, default=None)
    p.add_argument("--manifest-file", type=Path, default=Path("qwen_eval_manifest.json"))
    p.add_argument("--image-size", type=int, default=224)
    p.add_argument("--patch-size", type=int, default=64)
    p.add_argument("--position", type=str, default="center",
                   choices=["top_left", "top_right", "bottom_left", "bottom_right", "center"])
    p.add_argument("--max-images-per-category", type=int, default=0)
    p.add_argument("--random-seed", type=int, default=42)
    p.add_argument("--n-random", type=int, default=5)
    p.add_argument("--no-random", action="store_true", default=False)
    p.add_argument("--batch-size", type=int, default=32)
    return p.parse_args()


@torch.no_grad()
def embed_all(model, preprocess, pils, device, bs):
    """Return L2-normalized image embeddings [N, D] for a list of PILs."""
    out = []
    for i in range(0, len(pils), bs):
        x = torch.stack([preprocess(p) for p in pils[i:i + bs]]).to(device)
        out.append(F.normalize(model.encode_image(x), dim=-1))
    return torch.cat(out, 0)


@torch.no_grad()
def eval_one_encoder(name, pretrained, preprocess, model, tokenizer, device,
                     selected, pil_by_variant, batch_size):
    n_stop = len(STOP_TEXTS)
    text_feats = encode_texts(model, tokenizer, STOP_TEXTS + NEG_TEXTS, device)  # normalized
    stop_dir = F.normalize(text_feats[:n_stop].mean(0) - text_feats[n_stop:].mean(0), dim=-1)

    clean_emb = embed_all(model, preprocess, pil_by_variant["clean"], device, batch_size)
    clean_align = clean_emb @ stop_dir                                            # [N]

    rows = []
    encoder_id = f"{name}:{pretrained}"
    for v in selected:
        emb = clean_emb if v == "clean" else embed_all(model, preprocess, pil_by_variant[v], device, batch_size)
        cos_to_clean = (emb * clean_emb).sum(-1)                                  # [N]
        align = emb @ stop_dir                                                    # [N]
        rows.append({
            "encoder": encoder_id, "variant": v,
            "cos_disp": round((1 - cos_to_clean).mean().item(), 4),
            "stop_align": round(align.mean().item(), 4),
            "d_stop_align": round((align - clean_align).mean().item(), 4),
        })
    return rows


def collapse_random(enc_rows, random_names, unique_n):
    """enc_rows: list for ONE encoder. Collapse random_s* into one random_center
    row (mean+-std over seeds for each metric)."""
    out = []
    by_v = {r["variant"]: r for r in enc_rows}
    for v in [r["variant"] for r in enc_rows if r["variant"] not in random_names]:
        out.append({**by_v[v], "std_d_stop_align": "", "n_seeds": 1})
    rand = [by_v[v] for v in random_names if v in by_v]
    if rand:
        def ms(key):
            vals = [r[key] for r in rand]
            return round(sum(vals) / len(vals), 4), (round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0)
        m_disp, _ = ms("cos_disp")
        m_align, _ = ms("stop_align")
        m_da, s_da = ms("d_stop_align")
        out.append({"encoder": rand[0]["encoder"], "variant": "random_center",
                    "cos_disp": m_disp, "stop_align": m_align, "d_stop_align": m_da,
                    "std_d_stop_align": s_da, "n_seeds": len(rand)})
    return out


def main():
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    device = base.pick_device()[0]

    ns = Namespace(manifest_file=args.manifest_file, max_images_per_category=args.max_images_per_category,
                   image_size=args.image_size, patch_size=args.patch_size, position=args.position,
                   patch_path=args.patch_path, heldout_path=args.heldout_path,
                   clip_patch_path=args.clip_patch_path, no_random=args.no_random,
                   n_random=args.n_random, random_seed=args.random_seed)
    selected, pil_by_variant, unique_paths, random_names = build_variant_pils(ns, device)
    nonrandom = [v for v in selected if v != "clean" and v not in random_names]
    display_variants = ["clean"] + nonrandom + (["random_center"] if random_names else [])
    print(f"device={device} images={len(unique_paths)} variants={selected} n_random={len(random_names)}", flush=True)

    display_rows = []
    skipped = []
    for entry in args.models:
        name, _, pretrained = entry.partition(":")
        pretrained = pretrained or "openai"
        print(f"\n=== {name}:{pretrained} ===", flush=True)
        model = None
        try:
            model, _, preprocess = open_clip.create_model_and_transforms(name, pretrained=pretrained, device=device)
            tokenizer = open_clip.get_tokenizer(name)
            model.eval()
            for p in model.parameters():
                p.requires_grad = False
            enc_rows = eval_one_encoder(name, pretrained, preprocess, model, tokenizer, device,
                                        selected, pil_by_variant, args.batch_size)
        except Exception as e:  # noqa: BLE001
            print(f"  SKIP -> {type(e).__name__}: {str(e)[:160]}", flush=True)
            skipped.append({"encoder": f"{name}:{pretrained}", "error": f"{type(e).__name__}: {str(e)[:160]}"})
            continue
        finally:
            del model
            if device.type == "cuda":
                torch.cuda.empty_cache()
        crows = collapse_random(enc_rows, random_names, len(unique_paths))
        display_rows.extend(crows)
        for r in crows:
            tag = f"±{r['std_d_stop_align']}" if r.get("n_seeds", 1) > 1 else ""
            print(f"    {r['variant']:>20}: cos_disp={r['cos_disp']:.3f}  "
                  f"stop_align={r['stop_align']:.3f}  d_stop_align={r['d_stop_align']:+.3f}{tag}", flush=True)

    base.write_csv(display_rows, args.out_dir / "encoder_shift_summary.csv",
                   ["encoder", "variant", "cos_disp", "stop_align", "d_stop_align", "std_d_stop_align", "n_seeds"])

    # Markdown: focus column is d_stop_align (directional push toward the stop concept).
    encoders = [f"{e.partition(':')[0]}:{e.partition(':')[2] or 'openai'}" for e in args.models]
    encoders = [e for e in encoders if any(r["encoder"] == e for r in display_rows)]
    by = {(r["encoder"], r["variant"]): r for r in display_rows}

    def cell(enc, v):
        r = by.get((enc, v))
        if r is None:
            return "—"
        s = f"{r['d_stop_align']:+.3f}"
        if v == "random_center" and r.get("n_seeds", 1) > 1:
            s += f"±{r['std_d_stop_align']:.3f}"
        return s

    lines = [
        "# Encoder embedding-shift toward the stop concept (prompt-free)",
        "",
        f"n={len(unique_paths)} images, {args.image_size}x{args.image_size}, {args.patch_size}px {args.position} patch.",
        "Cell = **d_stop_align** = mean increase in cosine to `normalize(mean STOP - mean NEG)` vs the clean image.",
        f"`random_center` = mean±std over {len(random_names) or 1} seeds; push ABOVE it is the adversarial directional signal.",
        "(Full metrics incl. cos_disp / absolute stop_align in encoder_shift_summary.csv.)",
        "",
        "| encoder | " + " | ".join(display_variants) + " |",
        "|---|" + "|".join(["--:"] * len(display_variants)) + "|",
    ]
    for enc in encoders:
        lines.append(f"| {enc} | " + " | ".join(cell(enc, v) for v in display_variants) + " |")
    if skipped:
        lines += ["", "## Skipped", ""] + [f"- `{s['encoder']}`: {s['error']}" for s in skipped]
    (args.out_dir / "encoder_shift_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    (args.out_dir / "meta.json").write_text(json.dumps({
        "models": args.models, "skipped": skipped, "display_variants": display_variants,
        "random_seeds": list(range(args.random_seed, args.random_seed + args.n_random)) if random_names else [],
        "n_images": len(unique_paths), "patch_size": args.patch_size, "position": args.position,
    }, indent=2), encoding="utf-8")

    print(f"\n=== wrote {args.out_dir}/encoder_shift_summary.md (+ .csv, meta.json) ===", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
