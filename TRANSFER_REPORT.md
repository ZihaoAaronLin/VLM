# Cross-Model Transfer of the Qwen White-Box Stop-Patch

**Date:** 2026-06-17
**Question:** Our universal stop-patch was optimized white-box against `Qwen2.5-VL-3B-Instruct`.
Does it transfer (black-box) to *other* vision-language models?

## Setup

- **Patches tested** (both trained white-box on Qwen2.5-VL-3B):
  - `whitebox_center` — in-sample patch (`outputs_qwen_whitebox/qwen_stop_patch.png`)
  - `heldout_center` — generalizing patch trained on a disjoint image split (`outputs_qwen_heldout/qwen_heldout_patch.png`)
- **Controls:** `clean` (no patch) and `random_center` (random 64×64 noise patch). The random
  control is essential: it separates *our adversarial patch* from generic "any sticker" disruption.
- **Transfer protocol:** the **identical** 224×224 patched image (clean resized + 64px patch at
  center) is fed to every model; only the model varies. Each model does its own native
  preprocessing on that image.
- **Prompts:** an English battery (so the English-centric SmolVLM / LLaVA have a meaningful clean
  baseline). The source model was **re-run on the same English prompts** as an in-experiment reference.
- **Data:** 40 held-out test images (no real stop signs — so "model reports a stop sign" ≡ the patch worked).
- **Metric:** per-prompt rates (the forced binary "STOP or GO" prompt saturates to STOP on small
  models, so it is reported but not used for conclusions).

## Models (chosen to vary vision-encoder vs LM independently)

| model | role | LM | vision encoder |
|---|---|---|---|
| Qwen2.5-VL-3B-Instruct | **source** (white-box target) | Qwen2.5 | Qwen2-VL ViT |
| Qwen2-VL-2B-Instruct | same family, different version/size | Qwen2 | Qwen2-VL ViT |
| LLaVA-OneVision-0.5B | different family, **same LM family** | Qwen2-0.5B | SigLIP |
| SmolVLM-Instruct (2.2B) | different family, different LM | SmolLM2 | SigLIP |

## Headline result — "perceives a STOP sign" (sign_stop prompt), n=40

| model | clean | random | **whitebox** | **heldout** |
|---|--:|--:|--:|--:|
| Qwen2.5-VL-3B (source) | 0% | 0% | **97.5%** | **100%** |
| Qwen2-VL-2B (same family) | 5% | 5% | **27.5%** | **55%** |
| LLaVA-OneVision-0.5B (diff family) | 7.5% | 7.5% | 7.5% | 7.5% |
| SmolVLM-2.2B (diff family) | 0% | 0% | 0% | 0% |

## Decision metrics, n=40

Free-form "best action" → STOP:

| model | clean | random | whitebox | heldout |
|---|--:|--:|--:|--:|
| Qwen2.5-VL-3B | 7.5% | 15% | 35% | **72.5%** |
| Qwen2-VL-2B | 15% | 15% | 15% | 20% |
| LLaVA-OneVision-0.5B | 5% | 17.5% | 15% | 15% |
| SmolVLM-2.2B | 5% | 27.5% | 15% | 17.5% |

"Must you stop now?" (risk) → yes:

| model | clean | random | whitebox | heldout |
|---|--:|--:|--:|--:|
| Qwen2.5-VL-3B | 0% | 0% | 45% | **60%** |
| Qwen2-VL-2B | 2.5% | 5% | 2.5% | 5% |
| LLaVA-OneVision-0.5B | 100%* | 95%* | 100%* | 100%* |
| SmolVLM-2.2B | 10% | 12.5% | 7.5% | 35% |

\* LLaVA-OneVision answers "yes, stop" to almost everything (clean=100%) — a model-specific
prompt bias, not attack success.

## Findings

1. **Perception transfers within the model family; decision does not.** On the same-family
   Qwen2-VL-2B, the generalizing patch makes **55% vs a 5% clean/random floor** of scenes read as
   containing a stop sign — a real transfer of the *"looks like a stop sign"* feature. But the
   downstream stop-*decision* stays at baseline: the 2B model sees the (nonexistent) sign yet does
   not change its driving action.

2. **No transfer across model families.** For LLaVA-OneVision and SmolVLM the perception rate is
   pinned at the clean/random floor (7.5% / 0%) — the patch does nothing. Apparent decision shifts
   are matched or *exceeded* by the random-patch control, i.e. generic patch-presence noise, not a
   targeted attack. A shared SigLIP vision encoder is **not** enough; what matters is the full
   feature pathway the patch was optimized against.

3. **The generalizing (held-out) patch transfers ~2× better than the in-sample patch**
   (55% vs 27.5% perception on Qwen2-VL-2B; 72.5% vs 35% source decision). Training for image-level
   generalization also reduces over-fitting to source-model-specific features, improving transfer.

4. **Random patch = floor everywhere** on the perception metric, confirming the effect comes from
   the patch's adversarial structure, not from merely occluding the image center.

## Conclusion

This universal stop-patch is a **white-box, family-specific** attack. Its perceptual effect
(inducing a STOP-sign hallucination) transfers *partially* to an architecturally similar same-family
model and *not at all* to different-family VLMs; its decision effect does not transfer beyond the
source. It is **not** a black-box-universal attack across architectures — the expected and honest
outcome for a gradient-optimized patch that exploits one model's vision features.

## Caveats / scope

- 40 images, fp16, single 224×224 center placement, greedy decoding.
- Forced binary STOP/GO prompt saturates on small models → reported but excluded from conclusions;
  per-prompt rates + the random-patch control are what carry the analysis.
- Cross-family "no transfer" is verified against raw responses (models give coherent answers; they
  simply are not fooled), so it is a genuine null, not a harness artifact.

## Reproduce

```bash
conda activate vlm   # on a host with a live GPU (w6908)
cd ~/VLM
for M in "Qwen/Qwen2.5-VL-3B-Instruct:outputs_transfer_qwen25_3b" \
         "Qwen/Qwen2-VL-2B-Instruct:outputs_transfer_qwen2_2b" \
         "llava-hf/llava-onevision-qwen2-0.5b-ov-hf:outputs_transfer_llavaov_05b" \
         "HuggingFaceTB/SmolVLM-Instruct:outputs_transfer_smolvlm"; do
  python eval_transfer.py --model-id "${M%%:*}" --out-dir "${M##*:}"
done
```

Outputs per model: `transfer_summary_metrics.csv`, `transfer_prompt_labels.csv`,
`transfer_results.csv` (raw responses), `transfer_summary.md`.
