# Encoder-level transfer of the stop-patch (zero-shot STOP-sign rate)

n=8 images, 224x224, 64px center patch.
Cell = fraction of images whose top zero-shot label is in the STOP text group.
`random_center` is the essential control: effect above it is the adversarial signal.

| encoder | clean | whitebox_center | heldout_center | random_center |
|---|--:|--:|--:|--:|
| ViT-B-32-quickgelu:openai | 0.000 | 0.000 | 0.125 | 0.000 |
