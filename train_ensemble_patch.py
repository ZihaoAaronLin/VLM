"""Ensemble-transfer training of the stop-patch.

Goal: a patch that breaks *new / held-out* models, not just the one it was trained
on. The single-model patches (Qwen white-box / CLIP-B32 white-box) overfit one
feature pathway and barely transfer cross-architecture (see TRANSFER_REPORT.md +
the encoder-shift analysis). The standard fix is to optimize the patch against an
ENSEMBLE of diverse surrogate encoders at once, with random placement + input
diversity (DIM), so it learns a model-agnostic "stop" direction.

We train on a diverse encoder set (OpenAI CLIP B/32 + B/16, OpenCLIP-laion B/32,
SigLIP B/16) and HOLD OUT ViT-L/14 and the VLMs (Qwen2-VL-2B, LLaVA-OneVision,
SmolVLM) for honest transfer evaluation with the existing eval scripts:

    python eval_clip_transfer.py --patch-path outputs_ensemble_patch/ensemble_stop_patch.png \
        --heldout-path "" --clip-patch-path outputs/final_stop_patch.png --n-random 5

All encoders here take 224x224 input, so the differentiable pipeline is just
per-encoder normalization of the [0,1] patched image (no resize). A uniform
temperature (not each model's logit_scale) keeps every member's loss comparable.

Run on w6908 GPU via the nested hop (see project memory). Free.
"""
import argparse
import math
import random
from pathlib import Path

import torch
import torch.nn.functional as F
from torch import nn
from PIL import Image
from torchvision import transforms
from torchvision.transforms.functional import gaussian_blur as tv_gaussian_blur
from torchvision.utils import save_image
import pandas as pd
import open_clip

from eval_clip_transfer import STOP_TEXTS, NEG_TEXTS

# Positive text set for the contrastive loss. main() may EXTEND STOP_TEXTS with
# decision/urgency phrases (--extra-stop-texts) -> pushes the encoder toward the
# "must stop" concept, not just "a sign". We AUGMENT (keep the sign phrases) so the
# patch can't trade away perception for the decision concept.
N_STOP = len(STOP_TEXTS)
_ALL_TEXTS = STOP_TEXTS + NEG_TEXTS

DEFAULT_TRAIN = [
    "ViT-B-32-quickgelu:openai",
    "ViT-B-16-quickgelu:openai",
    "ViT-B-32:laion2b_s34b_b79k",
    "ViT-B-16-SigLIP:webli",
]
DEFAULT_HOLDOUT = ["ViT-L-14-quickgelu:openai"]  # never trained on; quick transfer sanity each epoch


def parse_args():
    p = argparse.ArgumentParser(description="Ensemble-transfer training of the stop-patch.")
    p.add_argument("--train-models", nargs="+", default=DEFAULT_TRAIN)
    p.add_argument("--holdout-models", nargs="*", default=DEFAULT_HOLDOUT)
    p.add_argument("--data-dir", type=Path, default=Path("data/train"))
    p.add_argument("--eval-dir", type=Path, default=Path("data/test"))
    p.add_argument("--out-dir", type=Path, default=Path("outputs_ensemble_patch"))
    p.add_argument("--save-name", type=str, default="ensemble_stop_patch.png")
    p.add_argument("--epochs", type=int, default=8)
    p.add_argument("--lr", type=float, default=0.03)
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--image-size", type=int, default=224)
    p.add_argument("--patch-size", type=int, default=64)
    p.add_argument("--tv-weight", type=float, default=5e-4)
    p.add_argument("--nps-weight", type=float, default=0.0,
                   help="non-printability score weight (Sharif 2016 / Thys 2019); >0 pushes pixels toward a printable palette")
    p.add_argument("--ti", action="store_true", default=False,
                   help="translation-invariant attack: gaussian-smooth the patch gradient (Dong 2019) for better transfer")
    p.add_argument("--ti-sigma", type=float, default=1.0)
    p.add_argument("--inv-temp", type=float, default=100.0, help="uniform logit scale across members")
    p.add_argument("--dim-prob", type=float, default=0.5, help="input-diversity prob (0 disables DIM)")
    p.add_argument("--dim-lo", type=float, default=0.85, help="min relative scale in DIM resize")
    p.add_argument("--eot", action="store_true", default=False,
                   help="expectation-over-transformations: random brightness/contrast/noise (transfer robustness)")
    p.add_argument("--phys-eot", action="store_true", default=False,
                   help="PHYSICAL EOT: affine/scale/blur/gamma + photometric -> patch survives print+photograph")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--extra-stop-texts", nargs="*", default=[],
                   help="phrases to ADD to the positive set (keeps the default sign phrases)")
    p.add_argument("--stop-texts", nargs="*", default=[],
                   help="REPLACE the positive set entirely (e.g. concrete hazard objects: a pedestrian, an obstacle)")
    return p.parse_args()


class ImageFolder(torch.utils.data.Dataset):
    EXTS = {".jpg", ".jpeg", ".png"}

    def __init__(self, root, image_size):
        self.paths = [p for p in Path(root).rglob("*") if p.suffix.lower() in self.EXTS]
        if not self.paths:
            raise RuntimeError(f"no images in {root}")
        self.t = transforms.Compose([
            transforms.Resize(image_size, interpolation=transforms.InterpolationMode.BICUBIC),
            transforms.CenterCrop(image_size),
            transforms.ToTensor(),
        ])

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, i):
        return self.t(Image.open(self.paths[i]).convert("RGB")), str(self.paths[i])


def load_member(entry, device):
    name, _, pretrained = entry.partition(":")
    pretrained = pretrained or "openai"
    model, _, pp = open_clip.create_model_and_transforms(name, pretrained=pretrained, device=device)
    model.eval()
    for p in model.parameters():
        p.requires_grad_(False)
    tok = open_clip.get_tokenizer(name)
    norm = next(t for t in pp.transforms if t.__class__.__name__ == "Normalize")
    mean = torch.tensor(norm.mean, device=device).view(1, 3, 1, 1)
    std = torch.tensor(norm.std, device=device).view(1, 3, 1, 1)
    size = getattr(model.visual, "image_size", (224, 224))
    size = size[0] if isinstance(size, (tuple, list)) else size
    with torch.no_grad():
        tf = F.normalize(model.encode_text(tok(_ALL_TEXTS).to(device)), dim=-1)
    return {"name": f"{name}:{pretrained}", "model": model, "mean": mean, "std": std,
            "size": size, "text_feats": tf}


def member_scores(m, imgs01, inv_temp):
    x = imgs01
    if m["size"] != x.shape[-1]:
        x = F.interpolate(x, size=m["size"], mode="bicubic", align_corners=False).clamp(0, 1)
    x = (x - m["mean"]) / m["std"]
    feats = F.normalize(m["model"].encode_image(x), dim=-1)
    return inv_temp * (feats @ m["text_feats"].T)


def stop_contrastive_loss(scores):
    stop = torch.logsumexp(scores[:, :N_STOP], dim=1)
    neg = torch.logsumexp(scores[:, N_STOP:], dim=1)
    return -F.logsigmoid(stop - neg).mean()


def tv_loss(patch):
    return (patch[:, 1:, :] - patch[:, :-1, :]).abs().mean() + \
           (patch[:, :, 1:] - patch[:, :, :-1]).abs().mean()


# coarse printable RGB grid (3^3=27 colors) — a defensible proxy for a printer's gamut.
# Ideally calibrate to the actual printer via a printed color chart; this is a soft prior.
_PAL = torch.tensor([[r, g, b] for r in (0.0, 0.5, 1.0)
                               for g in (0.0, 0.5, 1.0)
                               for b in (0.0, 0.5, 1.0)])


def nps_loss(patch, palette):
    """Non-printability score (Sharif 2016 'Accessorize to a Crime' / Thys 2019): min L2
    distance of each patch pixel to a printable palette -> penalizes colors the printer
    can't reproduce, shrinking the print->photo gap. (min-distance variant, numerically
    stable.) palette (K,3), patch (3,P,P) in [0,1]."""
    px = patch.permute(1, 2, 0).reshape(-1, 3)
    return torch.cdist(px, palette).min(dim=1).values.mean()


def gaussian_kernel(ksize, sigma, device):
    ax = torch.arange(ksize, device=device, dtype=torch.float32) - (ksize - 1) / 2
    g = torch.exp(-(ax ** 2) / (2 * sigma ** 2))
    k = g[:, None] * g[None, :]
    return (k / k.sum()).view(1, 1, ksize, ksize)


def apply_patch_random(images, patch):
    adv = images.clone()
    h, w = images.shape[-2:]
    p = patch.shape[-1]
    for i in range(images.shape[0]):
        top, left = random.randint(0, h - p), random.randint(0, w - p)
        adv[i, :, top:top + p, left:left + p] = patch
    return adv


def apply_patch_center(images, patch):
    adv = images.clone()
    h, w = images.shape[-2:]
    p = patch.shape[-1]
    top, left = (h - p) // 2, (w - p) // 2
    adv[:, :, top:top + p, left:left + p] = patch
    return adv


def input_diversity(x, prob, lo):
    if prob <= 0 or random.random() > prob:
        return x
    h, w = x.shape[-2:]
    s = random.uniform(lo, 1.0)
    nh, nw = max(1, int(h * s)), max(1, int(w * s))
    r = F.interpolate(x, size=(nh, nw), mode="bilinear", align_corners=False)
    pt, pl = random.randint(0, h - nh), random.randint(0, w - nw)
    return F.pad(r, (pl, w - nw - pl, pt, h - nh - pt))


def eot_augment(x):
    """Differentiable photometric EOT (brightness/contrast/noise) -> patch robust to the
    rendering/preprocessing differences across target models, which boosts transfer."""
    if random.random() < 0.9:
        x = x * random.uniform(0.8, 1.2)
    if random.random() < 0.9:
        m = x.mean(dim=(-3, -2, -1), keepdim=True)
        x = (x - m) * random.uniform(0.8, 1.2) + m
    if random.random() < 0.5:
        x = x + torch.randn_like(x) * random.uniform(0.0, 0.03)
    return x.clamp(0, 1)


def phys_augment(x):
    """Differentiable PHYSICAL EOT: simulate the print->camera->model pipeline so the
    patch survives being printed on paper and photographed. All ops keep grad wrt the
    patch. Chain: random affine (viewing angle) -> downscale/upscale (distance +
    resampling) -> gaussian blur (lens) -> gamma (print/lighting) -> photometric+noise."""
    B, _, H, W = x.shape
    if random.random() < 0.8:  # viewing angle / perspective (mild affine)
        ang = math.radians(random.uniform(-12, 12)); sc = random.uniform(0.8, 1.12)
        ca, sa = math.cos(ang) / sc, math.sin(ang) / sc
        tx, ty = random.uniform(-0.08, 0.08), random.uniform(-0.08, 0.08)
        theta = torch.tensor([[ca, -sa, tx], [sa, ca, ty]], dtype=x.dtype, device=x.device).repeat(B, 1, 1)
        grid = F.affine_grid(theta, x.shape, align_corners=False)
        x = F.grid_sample(x, grid, align_corners=False, padding_mode="border")
    if random.random() < 0.8:  # distance + camera/model resampling
        s = random.uniform(0.4, 1.0); h2, w2 = max(8, int(H * s)), max(8, int(W * s))
        x = F.interpolate(x, size=(h2, w2), mode="bilinear", align_corners=False)
        x = F.interpolate(x, size=(H, W), mode="bilinear", align_corners=False)
    if random.random() < 0.5:  # lens blur
        x = tv_gaussian_blur(x, kernel_size=5, sigma=random.uniform(0.4, 1.6))
    if random.random() < 0.7:  # print/lighting gamma
        x = x.clamp(1e-4, 1.0).pow(random.uniform(0.7, 1.4))
    if random.random() < 0.6:  # camera auto-white-balance: per-channel gain jitter
        gains = torch.tensor([random.uniform(0.9, 1.1) for _ in range(3)],
                             device=x.device, dtype=x.dtype).view(1, 3, 1, 1)
        x = (x * gains).clamp(0, 1)
    if random.random() < 0.5:  # saturation jitter (camera ISP)
        gray = x.mean(dim=1, keepdim=True)
        x = (gray + (x - gray) * random.uniform(0.7, 1.3)).clamp(0, 1)
    if random.random() < 0.6:  # JPEG chroma-subsampling proxy: blur chroma, keep luma
        luma = x.mean(dim=1, keepdim=True)
        x = (luma + tv_gaussian_blur(x - luma, kernel_size=3,
                                     sigma=random.uniform(0.6, 1.2))).clamp(0, 1)
    return eot_augment(x)  # brightness/contrast/noise + clamp


@torch.no_grad()
def stop_rate(m, images01, patch, inv_temp):
    top = member_scores(m, apply_patch_center(images01, patch), inv_temp).argmax(1)
    return (top < N_STOP).float().mean().item()


def main():
    args = parse_args()
    pos_texts = (args.stop_texts if args.stop_texts else STOP_TEXTS) + args.extra_stop_texts
    if pos_texts != STOP_TEXTS:
        global N_STOP, _ALL_TEXTS
        N_STOP = len(pos_texts)
        _ALL_TEXTS = pos_texts + NEG_TEXTS
        print(f"positive texts -> {pos_texts}", flush=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available()
                          else ("mps" if torch.backends.mps.is_available() else "cpu"))

    train_loader = torch.utils.data.DataLoader(
        ImageFolder(args.data_dir, args.image_size), batch_size=args.batch_size, shuffle=True)
    eval_imgs = torch.stack([x for x, _ in ImageFolder(args.eval_dir, args.image_size)]).to(device)

    print(f"device={device} train_imgs={len(train_loader.dataset)} eval_imgs={len(eval_imgs)}", flush=True)
    print(f"train_models={args.train_models}\nholdout_models={args.holdout_models}", flush=True)

    train_members = [load_member(e, device) for e in args.train_models]
    holdout_members = [load_member(e, device) for e in args.holdout_models]
    palette = _PAL.to(device)

    patch_logits = nn.Parameter(torch.randn(3, args.patch_size, args.patch_size, device=device) * 0.1)
    opt = torch.optim.Adam([patch_logits], lr=args.lr)

    logs = []
    for epoch in range(1, args.epochs + 1):
        running = 0.0
        for images, _ in train_loader:
            images = images.to(device)
            opt.zero_grad()
            # Per-member backward (memory-flat: one encoder's graph alive at a time) so a
            # large ensemble fits; each member gets fresh random placement/DIM/EOT (extra aug).
            for m in train_members:
                patch = torch.sigmoid(patch_logits)
                adv = input_diversity(apply_patch_random(images, patch), args.dim_prob, args.dim_lo)
                if args.phys_eot:
                    adv = phys_augment(adv)
                elif args.eot:
                    adv = eot_augment(adv)
                loss_m = stop_contrastive_loss(member_scores(m, adv, args.inv_temp)) / len(train_members)
                loss_m.backward()
                running += loss_m.item()
            patch_now = torch.sigmoid(patch_logits)
            reg = args.tv_weight * tv_loss(patch_now)
            if args.nps_weight > 0:
                reg = reg + args.nps_weight * nps_loss(patch_now, palette)
            reg.backward()
            if args.ti and patch_logits.grad is not None:  # translation-invariant: smooth the grad
                k = gaussian_kernel(5, args.ti_sigma, device).repeat(3, 1, 1, 1)
                patch_logits.grad = F.conv2d(patch_logits.grad.unsqueeze(0), k,
                                             padding=2, groups=3).squeeze(0)
            opt.step()

        patch = torch.sigmoid(patch_logits).detach()
        save_image(patch.cpu(), args.out_dir / f"patch_epoch_{epoch}.png")
        train_asr = {m["name"]: round(stop_rate(m, eval_imgs, patch, args.inv_temp), 3) for m in train_members}
        held_asr = {m["name"]: round(stop_rate(m, eval_imgs, patch, args.inv_temp), 3) for m in holdout_members}
        row = {"epoch": epoch, "loss": round(running / len(train_loader), 4),
               **{f"train:{k}": v for k, v in train_asr.items()},
               **{f"HELDOUT:{k}": v for k, v in held_asr.items()}}
        logs.append(row)
        pd.DataFrame(logs).to_csv(args.out_dir / "training_log.csv", index=False)
        print(f"epoch {epoch}: loss={row['loss']:.4f} | train_asr={train_asr} | HELDOUT_asr={held_asr}", flush=True)

    save_image(torch.sigmoid(patch_logits).detach().cpu(), args.out_dir / args.save_name)
    print(f"\nsaved patch -> {args.out_dir / args.save_name}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
