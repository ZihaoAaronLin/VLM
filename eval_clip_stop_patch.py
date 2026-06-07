import random
from pathlib import Path

import torch
import torch.nn.functional as F
from PIL import Image
from torchvision import transforms
from torchvision.utils import save_image
import pandas as pd
import open_clip
from tqdm import tqdm


DATA_DIR = Path("data/test")
PATCH_PATH = Path("outputs/final_stop_patch.png")
OUT_DIR = Path("outputs_test")
OUT_DIR.mkdir(exist_ok=True)

MODEL_NAME = "ViT-B-32"
PRETRAINED = "openai"

IMAGE_SIZE = 224
PATCH_SIZE = 64
BATCH_SIZE = 4

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


if torch.backends.mps.is_available():
    device = torch.device("mps")
else:
    device = torch.device("cpu")

print(f"Using device: {device}")


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


def load_patch(path):
    patch_img = Image.open(path).convert("RGB")
    transform = transforms.Compose([
        transforms.Resize((PATCH_SIZE, PATCH_SIZE)),
        transforms.ToTensor(),
    ])
    return transform(patch_img).to(device)


def apply_patch_bottom_right(images, patch):
    patched = images.clone()
    p = patch.shape[-1]
    patched[:, :, -p:, -p:] = patch
    return patched


def apply_patch_center(images, patch):
    patched = images.clone()
    _, _, h, w = patched.shape
    p = patch.shape[-1]
    top = (h - p) // 2
    left = (w - p) // 2
    patched[:, :, top:top+p, left:left+p] = patch
    return patched


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
    patch = load_patch(PATCH_PATH)

    rows = []

    clean_stop = 0
    br_stop = 0
    center_stop = 0
    total = 0

    saved_examples = 0

    for images, paths in tqdm(loader):
        images = images.to(device)

        patched_br = apply_patch_bottom_right(images, patch)
        patched_center = apply_patch_center(images, patch)

        clean_scores = clip_scores(model, images, text_features)
        br_scores = clip_scores(model, patched_br, text_features)
        center_scores = clip_scores(model, patched_center, text_features)

        clean_top = clean_scores.argmax(dim=1)
        br_top = br_scores.argmax(dim=1)
        center_top = center_scores.argmax(dim=1)

        for i in range(images.shape[0]):
            clean_label = all_texts[clean_top[i].item()]
            br_label = all_texts[br_top[i].item()]
            center_label = all_texts[center_top[i].item()]

            clean_is_stop = clean_top[i].item() < n_stop
            br_is_stop = br_top[i].item() < n_stop
            center_is_stop = center_top[i].item() < n_stop

            clean_stop += int(clean_is_stop)
            br_stop += int(br_is_stop)
            center_stop += int(center_is_stop)
            total += 1

            rows.append({
                "path": paths[i],
                "clean_top": clean_label,
                "bottom_right_patch_top": br_label,
                "center_patch_top": center_label,
                "clean_is_stop": clean_is_stop,
                "bottom_right_is_stop": br_is_stop,
                "center_is_stop": center_is_stop,
            })

            if saved_examples < 10:
                stem = Path(paths[i]).stem
                save_image(images[i].cpu(), OUT_DIR / f"{saved_examples}_{stem}_clean.png")
                save_image(patched_br[i].cpu(), OUT_DIR / f"{saved_examples}_{stem}_patched_br.png")
                save_image(patched_center[i].cpu(), OUT_DIR / f"{saved_examples}_{stem}_patched_center.png")
                saved_examples += 1

    result = {
        "total": total,
        "clean_stop_rate": clean_stop / max(total, 1),
        "bottom_right_patch_stop_rate": br_stop / max(total, 1),
        "center_patch_stop_rate": center_stop / max(total, 1),
    }

    print("\nEvaluation result:")
    for k, v in result.items():
        print(f"{k}: {v}")

    pd.DataFrame(rows).to_csv(OUT_DIR / "test_eval.csv", index=False)
    pd.DataFrame([result]).to_csv(OUT_DIR / "test_summary.csv", index=False)

    print(f"\nSaved test results to {OUT_DIR}")


if __name__ == "__main__":
    main()
