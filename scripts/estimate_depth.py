"""Estimate per-pixel depth (relief) from the menu photos with Depth Anything V2.

The menu photos are flat-lays shot straight from above, so a monocular depth
estimate is effectively a height map of each dish. It drives the 3D geometry
(toppings, cheese, crust, fries...) and the segmentation of boards and dishes.

Reads  : menu/original/*.jpg, assets/sources.json
Writes : assets/depth/page_<image>.npy   whole page (used to cut out boards / dishes)
         assets/depth/<slug>.npy + .json high-resolution crop around each dish
The cache is not committed (it is regenerated in ~10 minutes on a CPU).

Requires: pip install torch torchvision transformers   (CPU is fine)
Usage   : python scripts/estimate_depth.py [slug ...]
"""
import json
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "menu", "original")
OUT = os.path.join(ROOT, "assets", "depth")
MODEL = "depth-anything/Depth-Anything-V2-Large-hf"
RES = 1022  # network input (multiple of 14); higher than the default 518 keeps cheese/fries detail

cfg = json.load(open(os.path.join(ROOT, "assets", "sources.json"), encoding="utf-8"))


def jobs():
    """(slug, image, crop[x, y, w, h]) for every dish."""
    for slug, spec in cfg["pizzas"].items():
        cx, cy, r = spec["circle"]
        m = int(r * 1.12)
        yield slug, spec["image"], [cx - m, cy - m, 2 * m, 2 * m]
    for slug, spec in cfg["dishes"].items():
        if not slug.startswith("_"):
            yield slug, spec["image"], spec["crop"]


def save(path, d):
    """Write via a temp file so an interrupted run never leaves a truncated cache."""
    tmp = path + ".tmp.npy"
    np.save(tmp, d.astype(np.float16))
    os.replace(tmp, path)


def main(only):
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    proc = AutoImageProcessor.from_pretrained(MODEL)
    model = AutoModelForDepthEstimation.from_pretrained(MODEL).eval()
    os.makedirs(OUT, exist_ok=True)

    def infer(img, w, h):
        inp = proc(images=img, return_tensors="pt", size={"height": h, "width": w}, keep_aspect_ratio=False)
        with torch.no_grad():
            d = model(**inp).predicted_depth[0].numpy()
        return cv2.resize(d, img.size, interpolation=cv2.INTER_CUBIC).astype(np.float32)

    todo = [j for j in jobs() if not only or j[0] in only]
    pages = sorted({j[1] for j in todo})
    for name in pages:
        path = os.path.join(OUT, "page_" + name.replace(".jpg", ".npy"))
        if os.path.exists(path) and not only:
            continue
        img = Image.open(os.path.join(SRC, name)).convert("RGB")
        w = int(round(img.size[0] / img.size[1] * RES / 14)) * 14
        save(path, infer(img, w, RES))
        print("page", name, flush=True)
    for slug, name, (x, y, w, h) in todo:
        if os.path.exists(os.path.join(OUT, slug + ".json")) and not only:
            continue
        img = Image.open(os.path.join(SRC, name)).convert("RGB")
        # crops may run past the page edge: pad with white like the table top
        canvas = Image.new("RGB", (w, h), (250, 250, 250))
        canvas.paste(img.crop((max(0, x), max(0, y), min(img.size[0], x + w), min(img.size[1], y + h))),
                     (max(0, -x), max(0, -y)))
        s = RES / max(w, h)
        d = infer(canvas, max(14, int(round(w * s / 14)) * 14), max(14, int(round(h * s / 14)) * 14))
        save(os.path.join(OUT, slug + ".npy"), d)
        json.dump({"image": name, "crop": [x, y, w, h], "model": MODEL, "input": RES},
                  open(os.path.join(OUT, slug + ".json"), "w"))
        print("dish", slug, flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
