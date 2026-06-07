import os
import random
from pathlib import Path

import torch
import torch.nn.functional as F
from torch import nn
from PIL import Image
from tqdm import tqdm
import pandas as pd
import open_clip
from torchvision import transforms
from torchvision.utils import save_image


# =========================
# 1. Basic config
# =========================

DATA_DIR = Path("data/train")
OUT_DIR = Path("outputs")
OUT_DIR.mkdir(exist_ok=True)

MODEL_NAME = "ViT-B-32-quickgelu"
PRETRAINED = "openai"

IMAGE_SIZE = 224
PATCH_SIZE = 64

BATCH_SIZE = 4
EPOCHS = 5
LR = 0.05

TV_WEIGHT = 0.0005
SEED = 42

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png"}


# =========================
# 2. Reproducibility
# =========================

random.seed(SEED)
torch.manual_seed(SEED)


# =========================
# 3. Device
# =========================

if torch.cuda.is_available():
    device = torch.device("cuda")
elif torch.backends.mps.is_available():
    device = torch.device("mps")
else:
    device = torch.device("cpu")

print(f"Using device: {device}")


# =========================
# 4. Text prompts
# =========================

STOP_TEXTS = [
    "a stop sign",
    "a sign that says stop",
    "the command stop",
    "the agent should stop",
    "halt",
    "do not proceed",
    "the safest action is stop",
]

NEG_TEXTS = [
    "go forward",
    "continue moving",
    "the agent should proceed",
    "a normal street scene",
    "a green traffic light",
    "no stop sign",
    "safe to continue",
]


# =========================
# 5. Dataset
# =========================

class ImageFolderDataset(torch.utils.data.Dataset):
    def __init__(self, root):
        self.paths = []
        for p in Path(root).rglob("*"):
            if p.suffix.lower() in SUPPORTED_EXTS:
                self.paths.append(p)

        if len(self.paths) == 0:
            raise RuntimeError(f"No images found in {root}")

        self.transform = transforms.Compose([
            transforms.Resize(IMAGE_SIZE, interpolation=transforms.InterpolationMode.BICUBIC),
            transforms.CenterCrop(IMAGE_SIZE),
            transforms.ToTensor(),  # [0, 1]
        ])

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, idx):
        path = self.paths[idx]
        img = Image.open(path).convert("RGB")
        img = self.transform(img)
        return img, str(path)


# CLIP normalization
clip_normalize = transforms.Normalize(
    mean=(0.48145466, 0.4578275, 0.40821073),
    std=(0.26862954, 0.26130258, 0.27577711),
)


# =========================
# 6. Patch utilities
# =========================

def total_variation_loss(patch):
    """
    Encourage patch smoothness.
    patch: [3, H, W]
    """
    tv_h = torch.abs(patch[:, 1:, :] - patch[:, :-1, :]).mean()
    tv_w = torch.abs(patch[:, :, 1:] - patch[:, :, :-1]).mean()
    return tv_h + tv_w


def apply_random_patch(images, patch):
    """
    Apply the same universal patch to each image at a random location.
    images: [B, 3, H, W], values in [0,1]
    patch: [3, P, P], values in [0,1]
    """
    b, c, h, w = images.shape
    p = patch.shape[-1]

    adv = images.clone()

    for i in range(b):
        top = random.randint(0, h - p)
        left = random.randint(0, w - p)
        adv[i, :, top:top + p, left:left + p] = patch

    return adv


@torch.no_grad()
def encode_texts(model, tokenizer, texts):
    tokens = tokenizer(texts).to(device)
    text_features = model.encode_text(tokens)
    text_features = F.normalize(text_features, dim=-1)
    return text_features


def clip_scores(model, images, text_features):
    """
    images: [B, 3, 224, 224], values in [0,1]
    text_features: [T, D]
    return: [B, T]
    """
    images = clip_normalize(images).to(device)
    image_features = model.encode_image(images)
    image_features = F.normalize(image_features, dim=-1)

    logit_scale = model.logit_scale.exp()
    scores = logit_scale * image_features @ text_features.T
    return scores


def stop_contrastive_loss(scores, n_stop):
    """
    scores: [B, n_stop + n_neg]
    Make STOP text group score higher than negative text group score.
    """
    stop_scores = scores[:, :n_stop]
    neg_scores = scores[:, n_stop:]

    stop_logsum = torch.logsumexp(stop_scores, dim=1)
    neg_logsum = torch.logsumexp(neg_scores, dim=1)

    # maximize stop_logsum - neg_logsum
    loss = -F.logsigmoid(stop_logsum - neg_logsum).mean()
    return loss


# =========================
# 7. Evaluation
# =========================

@torch.no_grad()
def evaluate(model, loader, patch, text_features, all_texts, n_stop, max_batches=None):
    model.eval()

    clean_stop_count = 0
    patched_stop_count = 0
    total = 0

    rows = []

    for batch_idx, (images, paths) in enumerate(loader):
        if max_batches is not None and batch_idx >= max_batches:
            break

        images = images.to(device)

        patched = images.clone()
        # fixed bottom-right patch for evaluation
        p = patch.shape[-1]
        patched[:, :, -p:, -p:] = patch

        clean_scores = clip_scores(model, images, text_features)
        patched_scores = clip_scores(model, patched, text_features)

        clean_top = clean_scores.argmax(dim=1)
        patched_top = patched_scores.argmax(dim=1)

        for i in range(images.shape[0]):
            clean_label = all_texts[clean_top[i].item()]
            patched_label = all_texts[patched_top[i].item()]

            clean_is_stop = clean_top[i].item() < n_stop
            patched_is_stop = patched_top[i].item() < n_stop

            clean_stop_count += int(clean_is_stop)
            patched_stop_count += int(patched_is_stop)
            total += 1

            rows.append({
                "path": paths[i],
                "clean_top": clean_label,
                "patched_top": patched_label,
                "clean_is_stop": clean_is_stop,
                "patched_is_stop": patched_is_stop,
            })

    clean_asr = clean_stop_count / max(total, 1)
    patched_asr = patched_stop_count / max(total, 1)

    return clean_asr, patched_asr, rows


# =========================
# 8. Main training
# =========================

def main():
    dataset = ImageFolderDataset(DATA_DIR)

    loader = torch.utils.data.DataLoader(
        dataset,
        batch_size=BATCH_SIZE,
        shuffle=True,
        num_workers=0,
        drop_last=False,
    )

    print(f"Found {len(dataset)} images.")

    print("Loading CLIP model...")
    model, _, _ = open_clip.create_model_and_transforms(
        MODEL_NAME,
        pretrained=PRETRAINED,
        device=device,
    )
    tokenizer = open_clip.get_tokenizer(MODEL_NAME)

    model.eval()
    for param in model.parameters():
        param.requires_grad = False

    all_texts = STOP_TEXTS + NEG_TEXTS
    n_stop = len(STOP_TEXTS)

    text_features = encode_texts(model, tokenizer, all_texts)

    # Patch parameterization:
    # Use logits + sigmoid so the visible patch always stays in [0,1]
    patch_logits = nn.Parameter(torch.randn(3, PATCH_SIZE, PATCH_SIZE, device=device) * 0.1)
    optimizer = torch.optim.Adam([patch_logits], lr=LR)

    logs = []

    for epoch in range(1, EPOCHS + 1):
        model.eval()
        running_loss = 0.0

        pbar = tqdm(loader, desc=f"Epoch {epoch}/{EPOCHS}")

        for images, _paths in pbar:
            images = images.to(device)

            patch = torch.sigmoid(patch_logits)
            patched_images = apply_random_patch(images, patch)

            scores = clip_scores(model, patched_images, text_features)

            loss_target = stop_contrastive_loss(scores, n_stop)
            loss_tv = total_variation_loss(patch)

            loss = loss_target + TV_WEIGHT * loss_tv

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            running_loss += loss.item()
            pbar.set_postfix({
                "loss": f"{loss.item():.4f}",
                "target": f"{loss_target.item():.4f}",
                "tv": f"{loss_tv.item():.4f}",
            })

        patch = torch.sigmoid(patch_logits).detach()

        clean_asr, patched_asr, rows = evaluate(
            model=model,
            loader=loader,
            patch=patch,
            text_features=text_features,
            all_texts=all_texts,
            n_stop=n_stop,
            max_batches=None,
        )

        avg_loss = running_loss / max(len(loader), 1)

        log_row = {
            "epoch": epoch,
            "avg_loss": avg_loss,
            "clean_stop_rate": clean_asr,
            "patched_stop_rate": patched_asr,
        }
        logs.append(log_row)

        print(
            f"\nEpoch {epoch}: "
            f"avg_loss={avg_loss:.4f}, "
            f"clean_stop_rate={clean_asr:.3f}, "
            f"patched_stop_rate={patched_asr:.3f}"
        )

        save_image(patch.cpu(), OUT_DIR / f"patch_epoch_{epoch}.png")
        pd.DataFrame(rows).to_csv(OUT_DIR / f"eval_epoch_{epoch}.csv", index=False)
        pd.DataFrame(logs).to_csv(OUT_DIR / "training_log.csv", index=False)

    # Save final patch
    final_patch = torch.sigmoid(patch_logits).detach().cpu()
    save_image(final_patch, OUT_DIR / "final_stop_patch.png")

    print("\nDone.")
    print(f"Final patch saved to: {OUT_DIR / 'final_stop_patch.png'}")
    print(f"Training log saved to: {OUT_DIR / 'training_log.csv'}")


if __name__ == "__main__":
    main()
