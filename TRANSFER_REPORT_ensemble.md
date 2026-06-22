# Cross-Model Transfer & Ensemble Attack of the Stop-Patch

**Question.** A universal "stop-patch" was trained white-box on Qwen2.5-VL-3B (~100% there).
Does it transfer to *other* models — and if not, can we train one that does?

## TL;DR

- A **single-model** white-box patch does **not** transfer cross-family (the expected null).
- Training the patch against an **ensemble of diverse vision encoders** makes it transfer to
  **unseen encoders** at near-ceiling — including architecturally distinct ones (EVA, ConvNeXt).
- At the **full-VLM** level the perception is hijacked on genuinely foreign models
  (InternVL2: "reports a STOP sign" up to ~1.0), but the **decision** only partly follows:
  there is a **projection+LM "decision wall"** that vision-encoder transfer cannot cross.
- Best transfer result on the clean held-out target **InternVL2-2B**: **sign∧action = 0.675**
  (n=40, 95% CI [0.52,0.80]) vs 0.00 clean/random — strong, but below an "80%-broken" bar.
- A **query-based black-box** attack (path B) is the route past the wall; hard-label SPSA
  stalled on a decision cliff, a logprob-scored version is the fix (in progress).

## 1. Encoder-level transfer (free, CLIP/OpenCLIP/SigLIP/EVA/ConvNeXt)

Same patched 224px image, zero-shot STOP-rate per encoder. Control = 5-seed random patch.

| encoder (role) | Qwen single-model | **ensemble (3xCLIP, no SigLIP)** | random |
|---|--:|--:|--:|
| CLIP B/32, B/16, laion (trained) | .10-.675 | 1.00 | .01-.13 |
| CLIP L/14 (held-out) | .375 | **1.00** | .235 |
| SigLIP (held-out) | .400 | **1.00** | .000 |
| EVA02-B/16 (held-out) | .400 | **1.00** | .080 |
| ConvNeXt-base (held-out) | .400 | **0.95** | .210 |

Ensemble training generalizes across architectures (incl. the non-transformer ConvNeXt).

## 2. Reconciliation — why the single-model patch's encoder shift doesn't reach the VLM

Prompt-free embedding metric. `cos_disp` = movement (any direction); `d_stop_align` = movement
toward the STOP-concept direction.

- `cos_disp` is ~equal for adversarial and random patches (~0.2): the advantage is **direction,
  not magnitude**.
- Single-model patch `d_stop_align` is **real but weak**: SigLIP +.043 (~12 sigma over random),
  laion +.055 — about **half** a native CLIP-trained patch (+.10..+.14). Sub-threshold for the LM.

## 3. Ensemble-for-transfer training (method)

`train_ensemble_patch.py`: optimize one 64px patch against several frozen vision encoders at once
(per-encoder normalization), with **random placement + input diversity (DIM)** and optional **EOT**
(brightness/contrast/noise). One patch must fool all members -> a model-agnostic STOP direction.
Trains on data/train; all VLMs are held out (only bare encoders are trained on).

## 4. Ablation — is it "ensemble", or the pipeline? (matched pipeline, only #encoders varies)

Encoder zero-shot is "easy": single-encoder ~= ensemble (all ~0.9-1.0 on held-out encoders).
The decisive target is **InternVL2** (InternViT + InternLM2, both foreign):

| training set (same pipeline) | InternVL2 sign | InternVL2 action |
|---|--:|--:|
| single CLIP-B/32 | .10 (fail) | .10 |
| 3xCLIP ensemble | .70 | .325 |
| single SigLIP | .625 | **.675** |

**Refined conclusion:** transfer to a foreign VLM needs **encoder diversity (ensemble)** OR a
**representation-matched surrogate** (SigLIP ~ InternViT). A single mismatched encoder fails even
with the transfer pipeline. (Not "ensemble" as a single cause.)

## 5. Cross-family VLM results (held-out; clean rows use the no-SigLIP patch)

Held-out audit: encoder must be outside the training set (hard); LM is never trained on (we only
train encoders), so LM only matters for showing generality.

| VLM | encoder / LM | sign | action | risk | note |
|---|---|--:|--:|--:|---|
| Qwen2-VL-2B | Qwen-VL ViT / Qwen2 | .975 | .825 | .45 | same-family |
| InternVL2-2B | InternViT / InternLM2 | .70-1.0 | .33-.63 | .00 | both foreign (gold) |
| SmolVLM | SigLIP / SmolLM2 | .00 | **.80** | .75 | decision hijacked, perception NOT |
| LLaVA-OV | SigLIP / Qwen2 | .45 | .43 | - | encoder+LM both confounded |

## 6. The decision wall

Pushing transfer harder (7 encoders + EOT, 20 ep) maxes **perception** (InternVL2 sign .975-1.0)
but **action plateaus ~.58-.68** -> conjunction ceiling ~.65-.68. The bottleneck is the LM decision,
which vision-encoder transfer cannot target.

- **SmolVLM dissociation**: action=.80 / sign=.00 — the attack changes the *behavior* without the
  model ever *reporting* a sign (worse, for a driving threat model).
- **InternVL2 risk=.000 is genuine** (not a classifier artifact): it literally "sees a STOP sign"
  in its explanation but reasons "a stop sign is not an emergency requiring an immediate stop."

## 7. Success metric & best result

Primary = per-image **sign∧action conjunction** above the 5-seed random floor, with Wilson 95% CI.
On InternVL2 (n=40): div5 (CLIPx3+SigLIP+EVA) = **0.675 [0.52,0.80]**, ens4 .50, strong .55,
single_siglip .45; clean/random = 0.00. (n is capped at ~40-50 by available no-sign test images.)

## 8. Query-based black-box (path B, in progress)

Optimize the patch with SPSA using only InternVL2's *outputs* (no gradients), warm-started from the
best transfer patch, adding a bounded low-dim nudge on top of it (keeping its high-freq structure).

- **Hard-label SPSA stalled**: the decision is a cliff — any nudge flips all images to 0, so
  (s+ - s-) = 0 and the gradient vanishes. (Also: a 16px re-parameterization destroys the patch;
  must keep the base full-res and only add a nudge.)
- **Fix = logprob score**: score = logp(Yes) - logp(No) on a forced "must you stop now?" prompt;
  continuous, so SPSA has a gradient even before the discrete answer flips. Portable to Gemini
  (responseLogprobs). `attack_query_logprob.py` (pending a stable connection to run).

## Caveats

- n=40 (CI half-width ~+/-0.15 near .5-.7); 80% vs 90% not distinguishable at this n.
- Single 224px center placement, greedy decoding, fp16.
- InternVL2 risk excluded from the "broken" bar (genuine reasoning robustness, not attack failure).

## 9. Attack boundary -- query-based & concept-retargeted attacks all fail to beat transfer

Goal: push the InternVL2-2B sign^action conjunction above the transfer ceiling (div5 = .675).
Five principled attempts on the clean held-out target (InternVL2, n=40). None beat div5; each
failure exposes a different facet of the decision wall.

| attempt | method | result | why it failed |
|---|---|--:|---|
| div5 (baseline) | ensemble transfer, target text "a stop sign" | conj .675 | -- (the ceiling) |
| query: risk door | SPSA on logp("must you stop now?"=Yes), div5 warm-start | score -1.07 -> -0.99, no flip | the "is it an emergency" reasoning is robust to image perturbation |
| query: action door | SPSA on logp(STOP-action) | conj .675 -> .30 | gamed the proxy: raised STOP-logit by DESTROYING perception (sign 1.0 -> .375) |
| query: joint min(sign,action) | SPSA on min of both margins | conj = .675 (no gain) | div5 is a local optimum; any nudge lowers the weaker margin |
| concept: decision text | train ensemble toward "emergency stop / danger ahead" | conj .05 | abstract concept -> CLIP-specific direction, no visual prototype, does not transfer to the VLM |
| concept: hazard object | train ensemble toward "a pedestrian / obstacle / red light" | action .025, risk .025 | perception transfers (39/40 say "watch for pedestrian crossing") but the decision is graded caution, not a full STOP |

### The decision wall, characterized

- Attacks targeting the model's OWN decision logits (query/SPSA) cannot beat transfer: the guarded
  "emergency" reasoning won't move (risk door); a single-decision proxy decouples from the semantic
  attack and backfires (action door); and the transfer patch is already a local optimum (joint).
- Retargeting the surrogate-encoder concept fails two ways: abstract concepts ("emergency") have no
  visual prototype and don't transfer; concrete hazards ("pedestrian") DO transfer at perception,
  but InternVL2 maps a perceived hazard to graded caution ("watch for pedestrians"), not a STOP.
- Only the explicit "STOP sign" rule reliably yields a STOP decision -- which is why the stop-sign
  transfer patch (div5) is both the strongest attack (.675) and the conceptually-right one.

### Conclusion

Perception transfers across architectures with near-ceiling success: a foreign VLM (InternViT +
InternLM2, neither in the training ensemble) can be made to hallucinate a STOP sign, or even a
pedestrian. The agentic DECISION, however, is robust -- it maps perception to graded caution and
reserves "STOP" for the explicit stop-sign rule, resisting both transfer beyond ~.675 and direct
black-box query optimization. The .675 sign^action conjunction on InternVL2 is therefore a
mechanism-backed ceiling for this attack family, not merely an unoptimized number.
