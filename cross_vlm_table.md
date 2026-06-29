# Cross-VLM transfer (one row per target, cleanest valid patch per target)

`action` = STOP-decision ASR. `ens` cells = ensemble-attack sign/action/risk. `conj` = per-image sign^action (Wilson 95% CI). clean/random/base are floors.

| VLM | patch | clean(act) | random(act) | Qwen-single(act) | **ens** sign/act/risk | **conj** [CI] | note |
|---|---|--:|--:|--:|--:|--:|---|
| InternVL2-2B | div5 | 0.050 | 0.025 | 0.050 | 1.00/0.68/0.00 | 0.675 [0.52,0.80] | CLEAN cross-arch (InternViT + InternLM2 both foreign) |
| Qwen2-VL-2B | ens4 | 0.150 | 0.150 | 0.150 | 0.97/0.82/0.45 | 0.800 [0.65,0.90] | SAME-FAMILY as source (Qwen LM) -- not cross-family |
| SmolVLM | ensemble3 (no-SigLIP) | 0.050 | 0.275 | 0.150 | 0.00/0.80/0.75 | 0.000 [0.00,0.09] | clean cross-LM; sign/action DISSOCIATION |
| LLaVA-OV | ensemble3 (no-SigLIP) | 0.050 | 0.175 | 0.150 | 0.45/0.42/0.95 | 0.250 [0.14,0.40] | LM = Qwen (confounded) -> appendix |
