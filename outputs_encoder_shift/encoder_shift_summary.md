# Encoder embedding-shift toward the stop concept (prompt-free)

n=40 images, 224x224, 64px center patch.
Cell = **d_stop_align** = mean increase in cosine to `normalize(mean STOP - mean NEG)` vs the clean image.
`random_center` = mean±std over 5 seeds; push ABOVE it is the adversarial directional signal.
(Full metrics incl. cos_disp / absolute stop_align in encoder_shift_summary.csv.)

| encoder | clean | whitebox_center | heldout_center | clip_whitebox_center | random_center |
|---|--:|--:|--:|--:|--:|
| ViT-B-32-quickgelu:openai | +0.000 | +0.004 | +0.006 | +0.136 | -0.017±0.002 |
| ViT-B-16-quickgelu:openai | +0.000 | +0.020 | +0.040 | +0.116 | -0.005±0.003 |
| ViT-L-14-quickgelu:openai | +0.000 | +0.049 | +0.042 | +0.126 | +0.017±0.002 |
| ViT-B-32:laion2b_s34b_b79k | +0.000 | +0.069 | +0.048 | +0.139 | +0.014±0.003 |
| ViT-B-16-SigLIP:webli | +0.000 | +0.061 | +0.033 | +0.102 | +0.018±0.004 |
