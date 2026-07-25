from pathlib import Path
from PIL import Image, ImageOps
OP=Path("physical_photos_paired_outdoor"); IP=Path("physical_photos_paired_indoor")
dst=Path("probe_cv"); import shutil; shutil.rmtree(dst,ignore_errors=True)
def crop_low(im,z):
    w,h=im.size;s=int(min(w,h)*z);l=max(0,min(w-s,(w-s)//2));cy=int(h*0.66);t=max(0,min(h-s,cy-s//2));return im.crop((l,t,l+s,t+s))
def save(im,p):
    im=ImageOps.exif_transpose(im.convert("RGB")); im.thumbnail((900,900),Image.LANCZOS); im.save(p,quality=90)
def units(base,role):
    return sorted(base.glob(f"*/{role}.jpg"), key=lambda p:int(p.parent.name.split("_")[1]))
for tag,base in [("clean_outdoor",OP),("clean_indoor",IP)]:
    d=dst/tag; d.mkdir(parents=True)
    for u in units(base,"clean"): save(crop_low(Image.open(u),0.70), d/f"{u.parent.name.split(chr(95))[1]}.jpg")
for z in [1.0,0.85,0.70,0.60]:
    d=dst/f"sz{int(z*100)}"; d.mkdir(parents=True)
    for u in units(OP,"mine"): save(crop_low(Image.open(u),z), d/f"{u.parent.name.split(chr(95))[1]}.jpg")
print("built:",[p.name+f"({len(list((dst/p.name).iterdir()))})" for p in sorted(dst.iterdir())])
