"""Compose the final menu: original pages + one QR card per product.

Reads  : menu/original/*.jpg (never modified), menu/layout.json, qrcodes/*.png,
         products/products.json, config/site.config.json
Writes : menu/final/<page>.jpg  and  menu/final/<pdf>  (all pages, print-ready)

Every placed QR is decoded back from the composed page with OpenCV and must
match the product URL from qrcodes/manifest.json, otherwise the script fails.

Usage:  python scripts/compose_menu.py
"""
import json
import os
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIG = os.path.join(ROOT, "menu", "original")
FINAL = os.path.join(ROOT, "menu", "final")

cfg = json.load(open(os.path.join(ROOT, "config", "site.config.json"), encoding="utf-8"))
layout = json.load(open(os.path.join(ROOT, "menu", "layout.json"), encoding="utf-8"))
products = {p["slug"]: p for p in json.load(open(os.path.join(ROOT, "products", "products.json"), encoding="utf-8"))["products"]}
manifest = json.load(open(os.path.join(ROOT, "qrcodes", "manifest.json"), encoding="utf-8"))

CHARCOAL = (43, 38, 38)
RED = (224, 58, 47)  # footer accent line of the original menu
WHITE = (255, 255, 255)


def first_font(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


FONT_LATIN = first_font(cfg["menu"]["fontLatin"])
FONT_ARABIC = first_font(cfg["menu"]["fontArabic"])


def font(path, size, arabic=False):
    if path is None:
        return ImageFont.load_default(size)
    kw = {"layout_engine": ImageFont.Layout.RAQM} if arabic else {}
    return ImageFont.truetype(path, size, **kw)


def fit_text(draw, text, path, max_w, size, arabic=False, min_size=11):
    while size > min_size:
        f = font(path, size, arabic)
        kw = {"direction": "rtl", "language": "ar"} if arabic else {}
        w = draw.textlength(text, font=f, **kw)
        if w <= max_w:
            return f, w, kw
        size -= 1
    f = font(path, min_size, arabic)
    kw = {"direction": "rtl", "language": "ar"} if arabic else {}
    return f, draw.textlength(text, font=f, **kw), kw


def qr_image(slug, size):
    q = Image.open(os.path.join(ROOT, products[slug]["qr"].lstrip("/"))).convert("RGB")
    return q.resize((size, size), Image.NEAREST)  # keep modules crisp


def card(slug, qr, style):
    """Render a QR card (RGBA) in the menu's style: white, rounded, soft shadow."""
    pad = 12 if style == "compact" else 16
    label = cfg["menu"]["label"]
    tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    if style == "horizontal":
        text_w = int(qr * 1.9)
        w, h = pad * 3 + text_w + qr, pad * 2 + qr
    elif style == "compact":
        w, h = qr + pad * 2, pad + qr + 6 + 22 + pad - 2
    else:
        w, h = qr + pad * 2, pad + qr + 8 + 28 + 28 + pad
    shadow = 14
    img = Image.new("RGBA", (w + shadow * 2, h + shadow * 2), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((shadow, shadow + 4, shadow + w, shadow + h + 4), 18, fill=(0, 0, 0, 70))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(7)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((shadow, shadow, shadow + w, shadow + h), 18, fill=WHITE + (255,))
    ox, oy = shadow, shadow
    if style == "horizontal":
        img.paste(qr_image(slug, qr), (ox + w - pad - qr, oy + pad))
        tx, tw = ox + pad + 4, text_w - 8
        f1, w1, k1 = fit_text(tmp, label["en"], FONT_LATIN, tw, 34)
        f2, w2, k2 = fit_text(tmp, label["ar"], FONT_ARABIC, tw, 32, arabic=True)
        cy = oy + h // 2
        d.text((tx + tw - w1, cy - 50), label["en"], font=f1, fill=CHARCOAL, **k1)
        d.text((tx + tw - w2, cy + 2), label["ar"], font=f2, fill=CHARCOAL, **k2)
        d.rounded_rectangle((tx + tw - 70, cy - 6, tx + tw, cy - 2), 2, fill=RED)
    else:
        img.paste(qr_image(slug, qr), (ox + pad, oy + pad))
        y = oy + pad + qr + (4 if style == "compact" else 8)
        if style == "compact":
            f, tw_, k = fit_text(tmp, "Scan · 3D", FONT_LATIN, qr, 20)
            d.text((ox + (w - tw_) / 2, y), "Scan · 3D", font=f, fill=CHARCOAL, **k)
        else:
            f1, w1, k1 = fit_text(tmp, label["en"], FONT_LATIN, qr, 22)
            f2, w2, k2 = fit_text(tmp, label["ar"], FONT_ARABIC, qr, 21, arabic=True)
            d.text((ox + (w - w1) / 2, y), label["en"], font=f1, fill=CHARCOAL, **k1)
            d.rounded_rectangle((ox + w / 2 - 22, y + 31, ox + w / 2 + 22, y + 34), 2, fill=RED)
            d.text((ox + (w - w2) / 2, y + 34), label["ar"], font=f2, fill=CHARCOAL, **k2)
    return img, shadow


def decode_all(img_rgb):
    """Decode QR codes in a page region (white quiet zone + several scales, two detectors)."""
    gray = cv2.cvtColor(np.asarray(img_rgb), cv2.COLOR_RGB2GRAY)
    gray = cv2.copyMakeBorder(gray, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    found = set()
    detectors = [cv2.QRCodeDetector()]
    if hasattr(cv2, "QRCodeDetectorAruco"):
        detectors.append(cv2.QRCodeDetectorAruco())
    for scale in (1.0, 2.0, 0.75):
        im = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST if scale > 1 else cv2.INTER_AREA)
        for det in detectors:
            text, _, _ = det.detectAndDecode(im)
            if text:
                found.add(text)
        if found:
            break
    return sorted(found)


def main():
    os.makedirs(FINAL, exist_ok=True)
    W, H = layout["output"]["width"], layout["output"]["height"]
    pages, errors, placed = [], [], set()
    for page in layout["pages"]:
        src = Image.open(os.path.join(ORIG, page["file"])).convert("RGB")
        if src.size != (W, H):  # e.g. the cold page is 720x1280: upscale for one consistent print size
            src = src.resize((W, H), Image.LANCZOS)
        canvas = src.convert("RGBA")
        for q in page["qrs"]:
            slug = q["slug"]
            if slug not in products:
                errors.append(f"{page['file']}: unknown product '{slug}'")
                continue
            c, shadow = card(slug, q["qr"], q.get("style", "full"))
            canvas.alpha_composite(c, (q["x"] - shadow, q["y"] - shadow))
            # verify this exact QR on the composed page
            region = canvas.crop((q["x"], q["y"], q["x"] + c.width - 2 * shadow, q["y"] + c.height - 2 * shadow)).convert("RGB")
            found = decode_all(region)
            expected = manifest["codes"][slug]["url"]
            if expected not in found:
                errors.append(f"{page['file']}: QR for {slug} did not decode to {expected} (got {found})")
            placed.add(slug)
        out = canvas.convert("RGB")
        out.save(os.path.join(FINAL, page["file"]), quality=layout["output"]["jpegQuality"], optimize=True, subsampling=0)
        pages.append(out)
        print(f"{page['file']:32s} {len(page['qrs'])} QR")
    missing = sorted(set(products) - placed)
    if missing:
        errors.append("products without a QR on the menu: " + ", ".join(missing))
    pdf = os.path.join(FINAL, layout["output"]["pdf"])
    pages[0].save(pdf, save_all=True, append_images=pages[1:], resolution=200, quality=90)
    print("pdf:", os.path.relpath(pdf, ROOT))
    if errors:
        print("\n".join("ERROR " + e for e in errors), file=sys.stderr)
        sys.exit(1)
    print(f"OK: {len(placed)} product QR codes placed and verified by decoding")


if __name__ == "__main__":
    main()
