from pathlib import Path
import torch
import torch.nn.functional as F
from PIL import Image
from torchvision import transforms
import pandas as pd
import open_clip
from tqdm import tqdm


# =========================
# Config
# =========================

DATA_DIR = Path("data/test")
OPT_PATCH_PATH = Path("outputs/final_stop_patch.png")
OUT_DIR = Path("outputs_baselines")
OUT_DIR.mkdir(exist_ok=True)

MODEL_NAME = "ViT-B-32"
PRETRAINED = "openai"

IMAGE_SIZE = 224
PATCH_SIZE = 64
BATCH_SIZE = 4
SEED = 42

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png"}

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
    "a person",
    "a building",
    "an indoor room",
    "a car",
    "a tree",
    "a road",
    "a table",
    "an animal",
]

torch.manual_seed(SEED)

if torch.backends.mps.is_available():
    device = torch.device("mps")
else:
    device = torch.device("cpu")

print(f"Using device: {device}")


# =========================
# Dataset
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
            transforms.ToTensor(),
        ])

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, idx):
        path = self.paths[idx]
        img = Image.open(path).convert("RGB")
        return self.transform(img), str(path)


clip_normalize = transforms.Normalize(
    mean=(0.48145466, 0.4578275, 0.40821073),
    std=(0.26862954, 0.26130258, 0.27577711),
)


# =========================
# CLIP helpers
# =========================

@torch.no_grad()
def encode_texts(model, tokenizer, texts):
    tokens = tokenizer(texts).to(device)
    text_features = model.encode_text(tokens)
    text_features = F.normalize(text_features, dim=-1)
    return text_features


@torch.no_grad()
def clip_scores(model, images, text_features):
    images = clip_normalize(images).to(device)
    image_features = model.encode_image(images)
    image_features = F.normalize(image_features, dim=-1)

    logit_scale = model.logit_scale.exp()
    scores = logit_scale * image_features @ text_features.T
    return scores


# =========================
# Patch builders
# =========================

def load_patch(path):
    patch_img = Image.open(path).convert("RGB")
    transform = transforms.Compose([
        transforms.Resize((PATCH_SIZE, PATCH_SIZE)),
        transforms.ToTensor(),
    ])
    return transform(patch_img).to(device)


def make_random_patch():
    return torch.rand(3, PATCH_SIZE, PATCH_SIZE, device=device)


def make_solid_patch(rgb):
    """
    rgb values in [0,1], e.g. (1,0,0) for red
    """
    patch = torch.zeros(3, PATCH_SIZE, PATCH_SIZE, device=device)
    patch[0, :, :] = rgb[0]
    patch[1, :, :] = rgb[1]
    patch[2, :, :] = rgb[2]
    return patch


# =========================
# Patch placement
# =========================

def apply_patch(images, patch, position="bottom_right"):
    patched = images.clone()
    _, _, h, w = patched.shape
    p = patch.shape[-1]

    if position == "top_left":
        top = 0
        left = 0
    elif position == "top_right":
        top = 0
        left = w - p
    elif position == "bottom_left":
        top = h - p
        left = 0
    elif position == "bottom_right":
        top = h - p
        left = w - p
    elif position == "center":
        top = (h - p) // 2
        left = (w - p) // 2
    else:
        raise ValueError(f"Unknown position: {position}")

    patched[:, :, top:top+p, left:left+p] = patch
    return patched


# =========================
# Evaluation
# =========================

@torch.no_grad()
def evaluate_patch(model, loader, patch, patch_name, position, text_features, all_texts, n_stop):
    stop_count = 0
    total = 0
    rows = []

    for images, paths in tqdm(loader, desc=f"{patch_name}-{position}", leave=False):
        images = images.to(device)
        patched = apply_patch(images, patch, position=position)

        scores = clip_scores(model, patched, text_features)
        top_idx = scores.argmax(dim=1)

        for i in range(images.shape[0]):
            label = all_texts[top_idx[i].item()]
            is_stop = top_idx[i].item() < n_stop

            stop_count += int(is_stop)
            total += 1

            rows.append({
                "path": paths[i],
                "patch_name": patch_name,
                "position": position,
                "top_label": label,
                "is_stop": is_stop,
            })

    stop_rate = stop_count / max(total, 1)
    return stop_rate, rows


@torch.no_grad()
def evaluate_clean(model, loader, text_features, all_texts, n_stop):
    stop_count = 0
    total = 0
    rows = []

    for images, paths in tqdm(loader, desc="clean", leave=False):
        images = images.to(device)
        scores = clip_scores(model, images, text_features)
        top_idx = scores.argmax(dim=1)

        for i in range(images.shape[0]):
            label = all_texts[top_idx[i].item()]
            is_stop = top_idx[i].item() < n_stop

            stop_count += int(is_stop)
            total += 1

            rows.append({
                "path": paths[i],
                "patch_name": "clean",
                "position": "none",
                "top_label": label,
                "is_stop": is_stop,
            })

    stop_rate = stop_count / max(total, 1)
    return stop_rate, rows


# =========================
# Main
# =========================

def main():
    dataset = ImageFolderDataset(DATA_DIR)
    loader = torch.utils.data.DataLoader(
        dataset,
        batch_size=BATCH_SIZE,
        shuffle=False,
        num_workers=0,
    )

    print(f"Found {len(dataset)} test images.")

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

    # Build patches
    optimized_patch = load_patch(OPT_PATCH_PATH)
    random_patch = make_random_patch()
    solid_red_patch = make_solid_patch((1.0, 0.0, 0.0))
    solid_gray_patch = make_solid_patch((0.5, 0.5, 0.5))

    patch_dict = {
        "optimized": optimized_patch,
        "random": random_patch,
        "solid_red": solid_red_patch,
        "solid_gray": solid_gray_patch,
    }

    positions = ["top_left", "top_right", "bottom_left", "bottom_right", "center"]

    summary_rows = []
    detail_rows = []

    # Clean result
    clean_rate, clean_rows = evaluate_clean(
        model=model,
        loader=loader,
        text_features=text_features,
        all_texts=all_texts,
        n_stop=n_stop,
    )
    detail_rows.extend(clean_rows)
    summary_rows.append({
        "patch_name": "clean",
        "position": "none",
        "stop_rate": clean_rate,
    })

    # Baseline patches
    for patch_name, patch in patch_dict.items():
        for position in positions:
            stop_rate, rows = evaluate_patch(
                model=model,
                loader=loader,
                patch=patch,
                patch_name=patch_name,
                position=position,
                text_features=text_features,
                all_texts=all_texts,
                n_stop=n_stop,
            )
            detail_rows.extend(rows)
            summary_rows.append({
                "patch_name": patch_name,
                "position": position,
                "stop_rate": stop_rate,
            })

    summary_df = pd.DataFrame(summary_rows)
    detail_df = pd.DataFrame(detail_rows)

    summary_df.to_csv(OUT_DIR / "baseline_summary.csv", index=False)
    detail_df.to_csv(OUT_DIR / "baseline_details.csv", index=False)

    print("\n=== Baseline Summary ===")
    print(summary_df)

    print(f"\nSaved summary to: {OUT_DIR / 'baseline_summary.csv'}")
    print(f"Saved details to: {OUT_DIR / 'baseline_details.csv'}")


if __name__ == "__main__":
    main()
