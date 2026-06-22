"""Cross-model transfer eval for the stop-patch on InternVL2 (custom loader).

InternVL2-2B is the cleanest "new model" target: its vision encoder (InternViT)
and its LM (InternLM2) are BOTH foreign to the source (Qwen2.5-VL) AND to the
ensemble training set (CLIP/SigLIP encoders). So unlike LLaVA-OneVision (SigLIP
encoder + Qwen2 LM, confounded on both axes), a hit here is genuine cross-
architecture transfer.

InternVL2 is not an AutoModelForVision2Seq model -- it loads via AutoModel
(trust_remote_code) and is queried through model.chat(...). We feed the SAME
224x224 patched image used everywhere else (single 448 tile, ImageNet norm) so
only the model varies. Reuses the prompt battery + classifier + metric writers
from the rest of the transfer suite so the numbers line up.

Run on w6908 GPU via the nested hop. Free.
"""
import argparse
import json
from pathlib import Path

import torch
import torchvision.transforms as T
from PIL import Image
from transformers import AutoModel, AutoTokenizer

import eval_qwen_multprompt_patch as base
from eval_transfer import EN_PROMPTS, classify_response

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def build_internvl_pixel_values(pil, size, device, dtype):
    tf = T.Compose([
        T.Resize((size, size), interpolation=T.InterpolationMode.BICUBIC),
        T.ToTensor(),
        T.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])
    return tf(pil).unsqueeze(0).to(device=device, dtype=dtype)


def parse_args():
    p = argparse.ArgumentParser(description="InternVL2 cross-model transfer eval for the stop-patch.")
    p.add_argument("--model-id", default="OpenGVLab/InternVL2-2B")
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--patch-path", type=Path, default=Path("outputs_ensemble_patch_noSigLIP/ensemble_noSigLIP.png"))
    p.add_argument("--heldout-path", type=Path, default=Path("outputs_qwen_whitebox/qwen_stop_patch.png"))
    p.add_argument("--manifest-file", type=Path, default=Path("qwen_eval_manifest.json"))
    p.add_argument("--prompt-ids", nargs="+", default=["sign_stop", "decision_safe_action", "decision_risk"])
    p.add_argument("--image-size", type=int, default=224)
    p.add_argument("--patch-size", type=int, default=64)
    p.add_argument("--position", type=str, default="center")
    p.add_argument("--tile-size", type=int, default=448, help="InternVL single-tile input size")
    p.add_argument("--max-images-per-category", type=int, default=0)
    p.add_argument("--max-new-tokens", type=int, default=48)
    p.add_argument("--random-seed", type=int, default=42)
    p.add_argument("--no-random", action="store_true", default=False)
    return p.parse_args()


def main():
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    prompts = [p for p in EN_PROMPTS if (not args.prompt_ids or p.prompt_id in set(args.prompt_ids))]
    manifest = base.load_manifest(args.manifest_file, args.max_images_per_category)
    base.validate_manifest(manifest)
    category_index = base.reverse_category_index(manifest)
    unique_paths = base.unique_manifest_paths(manifest)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.float16  # Turing/Pascal: fp16, not bf16
    cpu = torch.device("cpu")
    transform = base.build_transform(args.image_size)

    patch_variants = {}
    if args.patch_path and Path(args.patch_path).exists():
        patch_variants["whitebox_center"] = base.load_patch(Path(args.patch_path), args.patch_size, cpu)
    if args.heldout_path and Path(args.heldout_path).exists():
        patch_variants["heldout_center"] = base.load_patch(Path(args.heldout_path), args.patch_size, cpu)
    if not args.no_random:
        patch_variants["random_center"] = base.make_random_patch(args.patch_size, cpu, args.random_seed)
    selected = ["clean"] + list(patch_variants.keys())

    print(f"loading {args.model_id} (fp16) ...", flush=True)
    model = AutoModel.from_pretrained(
        args.model_id, torch_dtype=dtype, trust_remote_code=True, low_cpu_mem_usage=True).eval().to(device)
    tokenizer = AutoTokenizer.from_pretrained(args.model_id, trust_remote_code=True, use_fast=False)
    gen_cfg = dict(max_new_tokens=args.max_new_tokens, do_sample=False)
    print(f"device={device} variants={selected} prompts={[p.prompt_id for p in prompts]}", flush=True)

    detail_csv = args.out_dir / "transfer_results.csv"
    fieldnames = ["image_path", "categories", "variant", "position", "prompt_id",
                  "prompt_category", "prompt_text", "response", "response_label"]
    if detail_csv.exists():
        detail_csv.unlink()

    detail_rows = []
    for idx, image_path in enumerate(unique_paths):
        image = Image.open(image_path).convert("RGB")
        clean_tensor = transform(image).to(cpu)
        for vname, pos, vtensor in base.build_variants(clean_tensor, patch_variants, args.position):
            if vname not in selected:
                continue
            pil = base.tensor_to_pil(vtensor)
            pv = build_internvl_pixel_values(pil, args.tile_size, device, dtype)
            for prompt in prompts:
                try:
                    resp = model.chat(tokenizer, pv, prompt.text, gen_cfg)
                    resp = resp.strip() if isinstance(resp, str) else str(resp)
                except Exception as e:  # noqa: BLE001
                    resp = f"__APIERROR__ {type(e).__name__} {str(e)[:140]}"
                label = classify_response(prompt.prompt_id, resp)
                detail_rows.append({
                    "image_path": str(image_path), "categories": "|".join(category_index[str(image_path)]),
                    "variant": vname, "position": pos, "prompt_id": prompt.prompt_id,
                    "prompt_category": prompt.category, "prompt_text": prompt.text,
                    "response": resp, "response_label": label,
                })
                base.append_csv_row(detail_rows[-1], detail_csv, fieldnames)
        print(f"[{idx+1}/{len(unique_paths)}] {image_path}", flush=True)

    image_metric_rows = base.build_image_metrics(detail_rows, manifest, selected)
    summary_rows = base.summarize_metrics(image_metric_rows)
    prompt_label_rows = base.summarize_prompt_labels(detail_rows)
    base.write_csv(detail_rows, detail_csv, fieldnames)
    base.write_csv(image_metric_rows, args.out_dir / "transfer_image_metrics.csv", [
        "category", "image_path", "variant", "sign_success", "risk_success",
        "action_success", "sign_labels", "risk_label", "action_labels"])
    base.write_csv(summary_rows, args.out_dir / "transfer_summary_metrics.csv", [
        "category", "variant", "total_images", "sign_successes", "sign_level_asr",
        "risk_successes", "risk_level_asr", "action_successes", "action_level_asr"])
    base.write_csv(prompt_label_rows, args.out_dir / "transfer_prompt_labels.csv", [
        "variant", "prompt_id", "prompt_category", "response_label", "count"])
    (args.out_dir / "transfer_results.json").write_text(
        json.dumps(detail_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    base.write_markdown(summary_rows, detail_rows, prompts, args.out_dir / "transfer_summary.md")
    (args.out_dir / "meta.json").write_text(json.dumps({
        "model_id": args.model_id, "variants": selected, "n_images": len(unique_paths),
        "prompts": [p.prompt_id for p in prompts], "tile_size": args.tile_size,
    }, indent=2), encoding="utf-8")

    print("=== SUMMARY (ALL_UNIQUE) ===", flush=True)
    for r in summary_rows:
        if r["category"] == "ALL_UNIQUE":
            print(f"  {r['variant']:>16}: sign_asr={r['sign_level_asr']:.3f} "
                  f"risk_asr={r['risk_level_asr']:.3f} action_asr={r['action_level_asr']:.3f} "
                  f"(n={r['total_images']})", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
