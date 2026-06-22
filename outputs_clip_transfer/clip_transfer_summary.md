# Encoder-level transfer of the stop-patch (zero-shot STOP-sign rate)

n=40 images, 224x224, 64px center patch.
Cell = fraction of images whose top zero-shot label is in the STOP text group.
`random_center` = mean over 5 random-patch seed(s); effect ABOVE it is the adversarial signal (random isolates generic patch-presence).

| encoder | clean | whitebox_center | heldout_center | clip_whitebox_center | random_center |
|---|--:|--:|--:|--:|--:|
| ViT-B-32-quickgelu:openai | 0.025 | 0.100 | 0.050 | 0.975 | 0.085±0.012 |
| ViT-B-16-quickgelu:openai | 0.075 | 0.200 | 0.375 | 0.975 | 0.010±0.012 |
| ViT-L-14-quickgelu:openai | 0.050 | 0.375 | 0.325 | 0.950 | 0.235±0.025 |
| ViT-B-32:laion2b_s34b_b79k | 0.100 | 0.675 | 0.375 | 0.975 | 0.130±0.051 |
| ViT-B-16-SigLIP:webli | 0.000 | 0.400 | 0.125 | 0.725 | 0.000±0.000 |
