"""
Qwen 攻击小玩具:同一张图、同一个问题,Qwen 在贴补丁前 vs 后的回答怎么变。
第一次加载约 20s(模型已缓存)。跑法(在 ~/VLM 下): ~/miniconda3/envs/vlm/bin/python play_qwen.py
"""
import sys
import torch
from PIL import Image
from torchvision import transforms
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

# ===== 想玩就改这几行 =====
IMAGE    = sys.argv[1] if len(sys.argv) > 1 else "data/test/21.png"
PATCH    = "outputs/final_stop_patch.png"
POSITION = sys.argv[2] if len(sys.argv) > 2 else "center"
PROMPT   = "图中是否出现了停止相关的标志、文字或信号？请直接回答并说明依据。"
PATCH_SIZE = 64
MODEL_ID = "Qwen/Qwen2.5-VL-3B-Instruct"
# =========================

device = "cuda" if torch.cuda.is_available() else "cpu"
dtype = torch.float16 if device == "cuda" else torch.float32
load_img = transforms.Compose([transforms.Resize(224, interpolation=transforms.InterpolationMode.BICUBIC),
                               transforms.CenterCrop(224), transforms.ToTensor()])

def load_patch(path):
    t = transforms.Compose([transforms.Resize((PATCH_SIZE, PATCH_SIZE)), transforms.ToTensor()])
    return t(Image.open(path).convert("RGB"))

def place(img, patch, pos):
    out = img.clone(); _, h, w = out.shape; p = patch.shape[-1]
    coords = {"center": ((h-p)//2, (w-p)//2), "bottom_right": (h-p, w-p),
              "top_left": (0, 0), "top_right": (0, w-p), "bottom_left": (h-p, 0)}
    top, left = coords[pos]
    out[:, top:top+p, left:left+p] = patch
    return out

to_pil = lambda t: transforms.ToPILImage()(t.clamp(0, 1))

print("加载 Qwen ...")
model = Qwen2_5_VLForConditionalGeneration.from_pretrained(MODEL_ID, torch_dtype=dtype).to(device).eval()
proc = AutoProcessor.from_pretrained(MODEL_ID)

@torch.inference_mode()
def ask(pil):
    msgs = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": PROMPT}]}]
    text = proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    inp = proc(text=[text], images=[pil], return_tensors="pt")
    inp = {k: (v.to(device, dtype) if torch.is_floating_point(v) else v.to(device)) for k, v in inp.items()}
    out = model.generate(**inp, max_new_tokens=64, do_sample=False)
    gen = out[0][inp["input_ids"].shape[1]:]
    return proc.batch_decode([gen], skip_special_tokens=True)[0].strip()

img = load_img(Image.open(IMAGE).convert("RGB"))
patch = load_patch(PATCH)
print(f"\n图: {IMAGE} | 位置: {POSITION}\n问题: {PROMPT}")
print(f"\n[CLEAN  干净图] {ask(to_pil(img))}")
print(f"[PATCHED 贴补丁] {ask(to_pil(place(img, patch, POSITION)))}")
