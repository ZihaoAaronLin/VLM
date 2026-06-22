"""Cross-family transfer of the stop-patch to commercial VLM APIs (OpenAI / Gemini).

Sends the SAME 224x224 patched image used in the local-model transfer test to a
hosted model via its REST API. Reuses the English prompt battery + classifier +
metrics from eval_transfer.py so the numbers line up with the open-model table.

The API key is read from an environment variable and is NEVER printed or written
to disk. Provide it yourself (e.g. `export OPENAI_API_KEY=...`); do not commit it.
"""
import argparse
import base64
import io
import json
import os
import time
from pathlib import Path

import requests
import torch
from PIL import Image

import eval_qwen_multprompt_patch as base
from eval_transfer import EN_PROMPTS, classify_response


def pil_to_b64(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def call_openai(model, prompt, b64, key, max_tokens, timeout=60, base_url="https://api.openai.com/v1"):
    # Works with any OpenAI-compatible endpoint: OpenAI, Groq, OpenRouter, GitHub Models, ...
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"}},
        ]}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    r = requests.post(url, headers=headers, json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def call_gemini(model, prompt, b64, key, max_tokens, timeout=60):
    # key passed via header (not URL) so it never lands in any log line
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    body = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": "image/png", "data": b64}},
        ]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens},
    }
    r = requests.post(url, headers=headers, json=body, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    cands = j.get("candidates", [])
    if not cands:
        return f"__APIERROR__ no-candidates {json.dumps(j.get('promptFeedback', {}))[:160]}"
    parts = cands[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    return text if text else f"__BLOCKED__ {cands[0].get('finishReason', '?')}"


def call_with_retry(fn, model, prompt, b64, key, max_tokens, retries=5, base_delay=2.0):
    for i in range(retries):
        try:
            return fn(model, prompt, b64, key, max_tokens)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code in (429, 500, 502, 503, 529) and i < retries - 1:
                time.sleep(base_delay * (2 ** i))
                continue
            body = e.response.text[:200] if e.response is not None else ""
            return f"__APIERROR__ http{code} {body}"
        except Exception as e:  # noqa: BLE001
            if i < retries - 1:
                time.sleep(base_delay * (2 ** i))
                continue
            return f"__APIERROR__ {type(e).__name__} {str(e)[:140]}"


def parse_args():
    p = argparse.ArgumentParser(description="API cross-family transfer eval for the stop-patch.")
    p.add_argument("--provider", choices=["openai", "gemini"], required=True)
    p.add_argument("--model", required=True, help="e.g. gpt-4o, gemini-1.5-flash, or a Groq/OpenRouter vision model id")
    p.add_argument("--base-url", default=None,
                   help="OpenAI-compatible base URL for --provider openai, e.g. "
                        "https://api.groq.com/openai/v1 or https://openrouter.ai/api/v1. Default = OpenAI.")
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--key-env", default=None, help="env var holding the API key (default OPENAI_API_KEY / GEMINI_API_KEY)")
    p.add_argument("--patch-path", type=Path, default=Path("outputs_qwen_whitebox/qwen_stop_patch.png"))
    p.add_argument("--heldout-path", type=Path, default=Path("outputs_qwen_heldout/qwen_heldout_patch.png"))
    p.add_argument("--manifest-file", type=Path, default=Path("qwen_eval_manifest.json"))
    p.add_argument("--prompt-ids", nargs="+", default=None,
                   help="subset of: decision_stop_or_go decision_safe_action decision_risk sign_presence sign_stop")
    p.add_argument("--image-size", type=int, default=224)
    p.add_argument("--patch-size", type=int, default=64)
    p.add_argument("--position", type=str, default="center")
    p.add_argument("--max-images-per-category", type=int, default=0)
    p.add_argument("--max-new-tokens", type=int, default=48)
    p.add_argument("--random-seed", type=int, default=42)
    p.add_argument("--no-random", action="store_true", default=False)
    p.add_argument("--delay", type=float, default=0.0, help="seconds to sleep between calls (for rate limits)")
    return p.parse_args()


def main():
    args = parse_args()
    key_env = args.key_env or ("OPENAI_API_KEY" if args.provider == "openai" else "GEMINI_API_KEY")
    key = os.environ.get(key_env)
    if not key:
        raise SystemExit(f"Missing API key: set ${key_env} in your environment (do NOT commit it).")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    prompts = [p for p in EN_PROMPTS if (not args.prompt_ids or p.prompt_id in set(args.prompt_ids))]
    manifest = base.load_manifest(args.manifest_file, args.max_images_per_category)
    base.validate_manifest(manifest)
    category_index = base.reverse_category_index(manifest)
    unique_paths = base.unique_manifest_paths(manifest)

    device = torch.device("cpu")
    transform = base.build_transform(args.image_size)
    patch_variants = {}
    if args.patch_path and Path(args.patch_path).exists():
        patch_variants["whitebox_center"] = base.load_patch(Path(args.patch_path), args.patch_size, device)
    if args.heldout_path and Path(args.heldout_path).exists():
        patch_variants["heldout_center"] = base.load_patch(Path(args.heldout_path), args.patch_size, device)
    if not args.no_random:
        patch_variants["random_center"] = base.make_random_patch(args.patch_size, device, args.random_seed)
    selected = ["clean"] + list(patch_variants.keys())

    if args.provider == "openai":
        base_url = args.base_url or "https://api.openai.com/v1"

        def caller(model, prompt, b64, key, max_tokens):
            return call_openai(model, prompt, b64, key, max_tokens, base_url=base_url)
    else:
        caller = call_gemini
    total = len(unique_paths) * len(selected) * len(prompts)
    print(f"provider={args.provider} model={args.model} key=${key_env} "
          f"prompts={[p.prompt_id for p in prompts]} variants={selected} calls~={total}", flush=True)

    detail_csv = args.out_dir / "transfer_results.csv"
    fieldnames = ["image_path", "categories", "variant", "position", "prompt_id",
                  "prompt_category", "prompt_text", "response", "response_label"]
    if detail_csv.exists():
        detail_csv.unlink()

    detail_rows = []
    n = 0
    errors = 0
    for idx, image_path in enumerate(unique_paths):
        image = Image.open(image_path).convert("RGB")
        clean_tensor = transform(image).to(device)
        variants = base.build_variants(clean_tensor, patch_variants, args.position)
        for vname, pos, vtensor in variants:
            if vname not in selected:
                continue
            b64 = pil_to_b64(base.tensor_to_pil(vtensor))
            for prompt in prompts:
                resp = call_with_retry(caller, args.model, prompt.text, b64, key, args.max_new_tokens)
                if resp.startswith("__APIERROR__"):
                    errors += 1
                label = classify_response(prompt.prompt_id, resp)
                row = {
                    "image_path": str(image_path),
                    "categories": "|".join(category_index[str(image_path)]),
                    "variant": vname, "position": pos,
                    "prompt_id": prompt.prompt_id, "prompt_category": prompt.category,
                    "prompt_text": prompt.text, "response": resp, "response_label": label,
                }
                detail_rows.append(row)
                base.append_csv_row(row, detail_csv, fieldnames)
                n += 1
                if args.delay > 0:
                    time.sleep(args.delay)
        print(f"[{idx+1}/{len(unique_paths)}] {image_path} (calls={n}, errors={errors})", flush=True)

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
        "provider": args.provider, "model": args.model, "n_images": len(unique_paths),
        "variants": selected, "prompts": [p.prompt_id for p in prompts], "errors": errors,
    }, indent=2), encoding="utf-8")

    print(f"=== SUMMARY (ALL_UNIQUE) errors={errors} ===", flush=True)
    for r in summary_rows:
        if r["category"] == "ALL_UNIQUE":
            print(f"  {r['variant']:>16}: sign_asr={r['sign_level_asr']:.3f} "
                  f"risk_asr={r['risk_level_asr']:.3f} action_asr={r['action_level_asr']:.3f} "
                  f"(n={r['total_images']})", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
