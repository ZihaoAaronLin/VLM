"""
CLIP 攻击小玩具:同一张图,贴补丁前 vs 后,CLIP 觉得它更像"停止"还是"通行"。
跑法(在 ~/VLM 下): ~/miniconda3/envs/vlm/bin/python play_clip.py
"""
import sys
import torch
import torch.nn.functional as F
from PIL import Image
from torchvision import transforms
import open_clip

# ===== 想玩就改这几行 =====
IMAGE    = sys.argv[1] if len(sys.argv) > 1 else "data/test/30.png"
PATCH    = "outputs/final_stop_patch.png"   # 想对比原始补丁: /home/s2489780/vlm_stop_patch/outputs/final_stop_patch.png
POSITION = sys.argv[2] if len(sys.argv) > 2 else "center"                          # center / bottom_right / top_left / top_right / bottom_left
PATCH_SIZE = 64
STOP_TEXTS = ["a stop sign", "the command stop", "halt", "the safest action is stop"]
NEG_TEXTS  = ["go forward", "a normal street scene", "a green traffic light", "safe to continue"]
# =========================

device = "cuda" if torch.cuda.is_available() else "cpu"
model, _, _ = open_clip.create_model_and_transforms("ViT-B-32-quickgelu", pretrained="openai", device=device)
tokenizer = open_clip.get_tokenizer("ViT-B-32-quickgelu")
model.eval()
normalize = transforms.Normalize((0.48145466, 0.4578275, 0.40821073), (0.26862954, 0.26130258, 0.27577711))
load_img = transforms.Compose([transforms.Resize(224, interpolation=transforms.InterpolationMode.BICUBIC),
                               transforms.CenterCrop(224), transforms.ToTensor()])

def load_patch(path):
    t = transforms.Compose([transforms.Resize((PATCH_SIZE, PATCH_SIZE)), transforms.ToTensor()])
    return t(Image.open(path).convert("RGB")).to(device)

def place(img, patch, pos):
    out = img.clone(); _, h, w = out.shape; p = patch.shape[-1]
    coords = {"center": ((h-p)//2, (w-p)//2), "bottom_right": (h-p, w-p),
              "top_left": (0, 0), "top_right": (0, w-p), "bottom_left": (h-p, 0)}
    top, left = coords[pos]
    out[:, top:top+p, left:left+p] = patch
    return out

@torch.no_grad()
def report(name, img):
    feats = F.normalize(model.encode_image(normalize(img).unsqueeze(0).to(device)), dim=-1)
    texts = STOP_TEXTS + NEG_TEXTS
    tfeat = F.normalize(model.encode_text(tokenizer(texts).to(device)), dim=-1)
    s = (model.logit_scale.exp() * feats @ tfeat.T)[0]
    n = len(STOP_TEXTS); top = s.argmax().item()
    print(f"\n=== {name} ===")
    print(f'  Top-1: "{texts[top]}"  ->  {"STOP ✅" if top < n else "NOT-STOP"}')
    print(f"  stop组最高分={s[:n].max():.2f} | 非stop组最高分={s[n:].max():.2f}")

img = load_img(Image.open(IMAGE).convert("RGB")).to(device)
patch = load_patch(PATCH)
print(f"图: {IMAGE} | 位置: {POSITION} | 设备: {device}")
report("CLEAN 干净图", img)
report(f"PATCHED 贴补丁@{POSITION}", place(img, patch, POSITION))
