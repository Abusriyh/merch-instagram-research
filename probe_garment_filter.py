from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import torch
from PIL import Image, ImageDraw, ImageFont, ImageOps
from transformers import AutoModelForZeroShotImageClassification, AutoProcessor
from ultralytics import YOLO

FASHION_MODEL = "AltaDaily/yolo26n-fashionpedia/best.pt"
CLIP_MODEL = "openai/clip-vit-base-patch32"

PROMPTS = [
    "a person wearing a regular short sleeve T-shirt",
    "a person wearing a sleeveless tank top or camisole",
    "a person wearing a polo shirt with a collar",
    "a person wearing a soccer jersey, basketball jersey, or sports jersey",
    "a person wearing a button-up shirt or blouse",
    "a person wearing a sweatshirt or hoodie",
    "a person wearing a sweater or knit top",
    "a person wearing a jacket or coat",
    "a person wearing a dress or one-piece outfit",
]
PROMPT_NAMES = ["tshirt", "tank", "polo", "sports_jersey", "button_up", "sweatshirt", "sweater", "jacket", "dress"]
CANDIDATE_FASHION = {"top/t-shirt/sweatshirt", "shirt/blouse", "sweater", "cardigan", "jacket", "vest", "coat", "dress", "jumpsuit"}


def font(size: int, bold: bool=False):
    p = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(p, size=size)
    except Exception:
        return ImageFont.load_default()


def expand(box, w, h, f=0.10):
    x1,y1,x2,y2 = box
    bw=max(1,x2-x1); bh=max(1,y2-y1)
    return (max(0,int(x1-bw*f)), max(0,int(y1-bh*f)), min(w,int(x2+bw*f)), min(h,int(y2+bh*f)))


def main():
    root = Path("source")
    out = Path("garment_probe")
    out.mkdir(exist_ok=True)
    imgs = sorted(root.rglob("*.jpg"))[:120]
    print("images", len(imgs), flush=True)

    fashion = YOLO(FASHION_MODEL)
    processor = AutoProcessor.from_pretrained(CLIP_MODEL)
    clip = AutoModelForZeroShotImageClassification.from_pretrained(CLIP_MODEL)
    clip.eval()

    rows: list[dict[str,Any]]=[]
    crops=[]
    refs=[]
    fashion_results = fashion.predict([str(p) for p in imgs], imgsz=640, conf=0.20, verbose=False, stream=True, device="cpu")
    for p,res in zip(imgs, fashion_results):
        im=Image.open(p).convert("RGB"); w,h=im.size; area=w*h
        boxes=getattr(res,"boxes",None)
        if boxes is None: continue
        for j,(xyxy,cf,cl) in enumerate(zip(boxes.xyxy.cpu().tolist(), boxes.conf.cpu().tolist(), boxes.cls.cpu().tolist())):
            label=str(res.names[int(cl)])
            if label not in CANDIDATE_FASHION: continue
            x1,y1,x2,y2=[float(v) for v in xyxy]
            ar=max(0,x2-x1)*max(0,y2-y1)/max(1,area)
            if ar < 0.012: continue
            cb=expand((x1,y1,x2,y2),w,h,0.12)
            crop=im.crop(cb)
            if crop.width<70 or crop.height<70: continue
            refs.append((p,label,float(cf),[x1,y1,x2,y2],list(cb)))
            crops.append(crop)

    print("garment crops",len(crops),flush=True)
    for start in range(0,len(crops),12):
        batch=crops[start:start+12]
        inputs=processor(text=PROMPTS, images=batch, return_tensors="pt", padding=True)
        with torch.inference_mode():
            logits=clip(**inputs).logits_per_image
            probs=torch.softmax(logits,dim=1).cpu().tolist()
        for k,pr in enumerate(probs):
            p,label,det,box,cb=refs[start+k]
            order=sorted(range(len(pr)), key=lambda i:pr[i], reverse=True)
            top=order[0]; second=order[1]
            rows.append({
                "source":str(p),"fashion_label":label,"fashion_conf":det,"garment_box":box,"crop_box":cb,
                "clip_top":PROMPT_NAMES[top],"clip_top_prob":pr[top],"clip_second":PROMPT_NAMES[second],"clip_second_prob":pr[second],
                "tshirt_prob":pr[0],"tshirt_margin":pr[0]-max(pr[1:]),"all_probs":{PROMPT_NAMES[i]:pr[i] for i in range(len(pr))}
            })

    # Keep best tee-like garment per image.
    best={}
    for r in rows:
        key=r["source"]
        score=r["tshirt_prob"] + (0.10 if r["fashion_label"]=="top/t-shirt/sweatshirt" else 0)
        if key not in best or score > best[key][0]: best[key]=(score,r)
    ranked=[v[1] for v in best.values()]
    ranked.sort(key=lambda r:(r["clip_top"]=="tshirt", r["tshirt_margin"], r["tshirt_prob"]), reverse=True)

    (out/"results.json").write_text(json.dumps(ranked,indent=2),encoding="utf-8")
    summary={
        "input_images":len(imgs),"garment_crops":len(crops),"images_with_upper_garment":len(ranked),
        "clip_top_counts":{},"fashion_label_counts":{}
    }
    from collections import Counter
    summary["clip_top_counts"]=dict(Counter(r["clip_top"] for r in ranked))
    summary["fashion_label_counts"]=dict(Counter(r["fashion_label"] for r in ranked))
    (out/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2),flush=True)

    # Contact sheet: top 32 tee-ranked candidates, full image + garment box.
    chosen=ranked[:32]
    cw,ch=520,520; cols=4; rowsn=8; lh=105
    canvas=Image.new("RGB",(cw*cols,ch*rowsn),"white"); d=ImageDraw.Draw(canvas)
    f1=font(19,True); f2=font(14)
    for n,r in enumerate(chosen):
        rr,cc=divmod(n,cols); x0=cc*cw; y0=rr*ch
        im=Image.open(r["source"]).convert("RGB")
        x1,y1,x2,y2=[int(x) for x in r["garment_box"]]
        ImageDraw.Draw(im).rectangle((x1,y1,x2,y2),outline="lime" if r["clip_top"]=="tshirt" else "red",width=max(3,im.width//250))
        fit=ImageOps.contain(im,(cw-8,ch-lh-8),Image.Resampling.LANCZOS)
        canvas.paste(fit,(x0+(cw-fit.width)//2,y0+lh+(ch-lh-fit.height)//2))
        d.rectangle((x0,y0,x0+cw-1,y0+ch-1),outline="black",width=2)
        d.text((x0+6,y0+5),f"{n+1}. {r['clip_top']} T={r['tshirt_prob']:.2f} m={r['tshirt_margin']:.2f}",fill="black",font=f1)
        d.text((x0+6,y0+36),f"2nd={r['clip_second']} {r['clip_second_prob']:.2f}",fill="black",font=f2)
        d.text((x0+6,y0+59),f"FP={r['fashion_label']} {r['fashion_conf']:.2f}",fill="black",font=f2)
        d.text((x0+6,y0+81),Path(r["source"]).name[:55],fill="black",font=f2)
    canvas.save(out/"top32.jpg","JPEG",quality=88,optimize=True)

if __name__=="__main__": main()
