# Encoder-level transfer of the stop-patch (zero-shot STOP-sign rate)

n=40 images, 224x224, 64px center patch.
Cell = fraction of images whose top zero-shot label is in the STOP text group.
`random_center` = mean over 5 random-patch seed(s); effect ABOVE it is the adversarial signal (random isolates generic patch-presence).

| encoder | clean | whitebox_center | heldout_center | clip_whitebox_center | random_center |
|---|--:|--:|--:|--:|--:|
| ViT-B-32-quickgelu:openai | 0.025 | 1.000 | 1.000 | 0.850 | 0.085±0.012 |
| ViT-B-16-quickgelu:openai | 0.075 | 1.000 | 1.000 | 1.000 | 0.010±0.012 |
| ViT-B-32:laion2b_s34b_b79k | 0.100 | 1.000 | 0.975 | 0.825 | 0.130±0.051 |
| ViT-L-14-quickgelu:openai | 0.050 | 1.000 | 1.000 | 1.000 | 0.235±0.025 |
| ViT-B-16-SigLIP:webli | 0.000 | 1.000 | 1.000 | 1.000 | 0.000±0.000 |
| EVA02-B-16:merged2b_s8b_b131k | 0.025 | 1.000 | 0.900 | 0.875 | 0.080±0.025 |
| convnext_base_w:laion2b_s13b_b82k | 0.025 | 0.950 | 0.875 | 0.950 | 0.210±0.012 |
