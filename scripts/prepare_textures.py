"""Extract 3D textures and product photos from the original menu photos.

Reads  : menu/original/*.jpg, assets/sources.json
Writes : assets/textures/<slug>/*  (inputs for blender/build_models.py)
         assets/products/<slug>.jpg (product photo shown on the product page)

Everything is derived from the photos; nothing is painted by hand. Pizzas are
photographed straight from above, so the top of each pizza is used directly as
its albedo, and a relief map is estimated from how far each pixel's colour is
from the base cheese colour (toppings / drizzles rise above the cheese).

Usage:  python scripts/prepare_textures.py
"""
import json
import os

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "menu", "original")
TEX = os.path.join(ROOT, "assets", "textures")
PHOTOS = os.path.join(ROOT, "assets", "products")
TEX_SIZE = 1024

cfg = json.load(open(os.path.join(ROOT, "assets", "sources.json"), encoding="utf-8"))
_cache = {}


def load(name):
    if name not in _cache:
        img = cv2.imread(os.path.join(SRC, name), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(name)
        _cache[name] = img
    return _cache[name]


def out_dir(slug):
    d = os.path.join(TEX, slug)
    os.makedirs(d, exist_ok=True)
    return d


def pot(n):
    return 1 << max(6, round(np.log2(n)))


def save_jpg(path, img, q=86, power_of_two=True):
    """Textures are saved power-of-two sized (mipmaps on every mobile GPU)."""
    if power_of_two:
        h, w = img.shape[:2]
        if (pot(w), pot(h)) != (w, h):
            img = cv2.resize(img, (pot(w), pot(h)), interpolation=cv2.INTER_AREA if pot(w) < w else cv2.INTER_CUBIC)
    cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, q, cv2.IMWRITE_JPEG_OPTIMIZE, 1])


def polar_clamped_disc(img, cx, cy, r, size, clamp=0.975):
    """Square texture of a circular region; pixels outside the circle repeat the
    rim colour radially so stretched side faces never pick up background."""
    t = (np.arange(size) + 0.5) / size * 2 - 1
    u, v = np.meshgrid(t, t)
    rho = np.sqrt(u * u + v * v)
    k = np.where(rho > clamp, clamp / np.maximum(rho, 1e-6), 1.0)
    mx = (cx + u * k * r).astype(np.float32)
    my = (cy + v * k * r).astype(np.float32)
    return cv2.remap(img, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT), rho


def rotated_patch(img, cx, cy, w, h, ang, long_side=512):
    m = cv2.getRotationMatrix2D((cx, cy), ang, 1.0)
    rot = cv2.warpAffine(img, m, (img.shape[1], img.shape[0]), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    x0, y0 = int(cx - w / 2), int(cy - h / 2)
    patch = rot[y0:y0 + int(h), x0:x0 + int(w)]
    s = long_side / max(patch.shape[:2])
    return cv2.resize(patch, (round(patch.shape[1] * s), round(patch.shape[0] * s)), interpolation=cv2.INTER_CUBIC)


def normal_from_height(h_m, texel_m, strength=1.0):
    """Tangent-space normal map (OpenGL / glTF convention, +Y = up in image)."""
    h_m = np.asarray(h_m, np.float32)
    gx = cv2.Sobel(h_m, cv2.CV_32F, 1, 0, ksize=3) / (8 * texel_m) * strength
    gy = cv2.Sobel(h_m, cv2.CV_32F, 0, 1, ksize=3) / (8 * texel_m) * strength
    # image rows grow downward, texture V grows upward -> flip gy sign
    n = np.dstack([-gx, gy, np.ones_like(gx)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    rgb = ((n * 0.5 + 0.5) * 255).astype(np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def fine_detail(bgr, sigma=3.0):
    l = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float32) / 255
    return l - cv2.GaussianBlur(l, (0, 0), sigma)


def write_maps(slug, albedo, height_m, texel_m, normal_strength=1.0, height_png=True):
    d = out_dir(slug)
    save_jpg(os.path.join(d, "albedo.jpg"), albedo)
    nrm = normal_from_height(height_m, texel_m, normal_strength)
    if nrm.shape[0] > 512:  # normal detail survives downscaling well; saves ~40% per model
        nrm = cv2.resize(nrm, (512, 512), interpolation=cv2.INTER_AREA)
    save_jpg(os.path.join(d, "normal.jpg"), nrm, 88)
    if height_png:
        hmax = max(float(height_m.max()), 1e-6)
        h16 = (np.clip(height_m / hmax, 0, 1) * 65535).astype(np.uint16)
        cv2.imwrite(os.path.join(d, "height.png"), cv2.resize(h16, (256, 256), interpolation=cv2.INTER_AREA))
        json.dump({"height_max_m": hmax}, open(os.path.join(d, "height.json"), "w"))


def product_photo(slug, img, x, y, size):
    x0, y0 = max(0, int(x)), max(0, int(y))
    size = int(min(size, img.shape[1] - x0, img.shape[0] - y0))
    crop = img[y0:y0 + size, x0:x0 + size]
    os.makedirs(PHOTOS, exist_ok=True)
    save_jpg(os.path.join(PHOTOS, f"{slug}.jpg"), cv2.resize(crop, (800, 800), interpolation=cv2.INTER_AREA), 84,
             power_of_two=False)


# ---------------------------------------------------------------- pizzas
PIZZA_DIAMETER_M = 0.30


def do_pizza(slug, spec):
    img = load(spec["image"])
    cx, cy, r = spec["circle"]
    albedo, rho = polar_clamped_disc(img, cx, cy, r, TEX_SIZE)
    lab = cv2.cvtColor(albedo, cv2.COLOR_BGR2LAB).astype(np.float32)
    inner = (rho > 0.12) & (rho < 1 - spec["crust"] - 0.04)
    base = np.median(lab[inner], axis=0)  # dominant cheese colour
    dist = np.linalg.norm((lab - base) * np.array([0.6, 1.0, 1.0]), axis=2)
    top = np.clip((dist - 14) / 34, 0, 1).astype(np.float32)
    top = cv2.morphologyEx(top, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    top = cv2.GaussianBlur(top, (0, 0), 4)
    fade = np.clip((1 - spec["crust"] - rho) / 0.05, 0, 1)  # toppings stop at the crust
    texel = PIZZA_DIAMETER_M / TEX_SIZE
    topping_h = top * fade * 0.006  # up to 6 mm of topping relief
    fine_h = fine_detail(albedo) * 0.0012
    write_maps(slug, albedo, topping_h + fine_h, texel, height_png=False)
    # geometry only uses the smooth topping relief; fine detail lives in the normal map
    d = out_dir(slug)
    hm = topping_h.max() or 1e-6
    cv2.imwrite(os.path.join(d, "height.png"),
                cv2.resize((topping_h / hm * 65535).astype(np.uint16), (256, 256), interpolation=cv2.INTER_AREA))
    json.dump({"height_max_m": float(hm), "crust": spec["crust"], "diameter_m": PIZZA_DIAMETER_M},
              open(os.path.join(d, "height.json"), "w"))
    side = r * 2.24
    product_photo(slug, img, cx - side / 2, cy - side / 2, side)


# ---------------------------------------------------------------- other dishes
def do_patch(slug, spec, relief_m, sigma=2.5):
    img = load(spec["image"])
    cx, cy, w, h, a = spec["top"]
    patch = rotated_patch(img, cx, cy, w, h, a)
    texel = 0.25 / max(patch.shape[:2])
    write_maps(slug, patch, fine_detail(patch, sigma) * relief_m, texel)
    return patch


def mean_color(img):
    return [int(v) for v in np.median(img.reshape(-1, 3), axis=0)]


def speckle_texture(base_bgr, size=256, seed=1, dots=900, dark=(40, 55, 90)):
    rng = np.random.default_rng(seed)
    t = np.zeros((size, size, 3), np.float32) + np.array(base_bgr, np.float32)
    noise = cv2.GaussianBlur(rng.normal(0, 1, (size, size)).astype(np.float32), (0, 0), 6)
    t += noise[..., None] * 18
    for _ in range(dots):
        x, y = rng.integers(0, size, 2)
        cv2.circle(t, (int(x), int(y)), int(rng.integers(1, 3)), dark, -1)
    return np.clip(t, 0, 255).astype(np.uint8)


def layered_side(top_bgr, sauce_bgr, size=(512, 256), layers=7, seed=3):
    """Side view of lasagna: alternating pasta / bolognese layers (from photo colours)."""
    rng = np.random.default_rng(seed)
    w, h = size
    img = np.zeros((h, w, 3), np.float32)
    ys = np.linspace(0, h, layers + 1)
    xs = np.arange(w)
    for i in range(layers):
        wob = (np.sin(xs / 37.0 + i) * 3 + rng.normal(0, 1, w).cumsum() * 0.05).astype(int)
        col = np.array(top_bgr if i % 2 == 0 else sauce_bgr, np.float32)
        for x in range(w):
            y0 = int(np.clip(ys[i] + wob[x], 0, h))
            img[y0:, x] = col
    img += cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 2)[..., None] * 14
    return np.clip(img, 0, 255).astype(np.uint8)


def main():
    # wood (pizza peel / boards), mirrored so it tiles without seams
    wspec = cfg["wood"]
    x, y, w, h = wspec["rect"]
    wood = load(wspec["image"])[y:y + h, x:x + w]
    wood = np.hstack([wood, wood[:, ::-1]])
    wood = np.vstack([wood, wood[::-1]])
    wood = cv2.resize(wood, (512, 512), interpolation=cv2.INTER_CUBIC)
    d = out_dir("_shared")
    save_jpg(os.path.join(d, "wood.jpg"), wood)
    save_jpg(os.path.join(d, "wood_normal.jpg"), normal_from_height(fine_detail(wood, 4) * 0.0006, 0.4 / 512), 90)

    for slug, spec in cfg["pizzas"].items():
        do_pizza(slug, spec)
        print("pizza", slug)

    p = cfg["patches"]
    hot = load("01-appetizers-hot.jpg")
    bread = do_patch("garlic-bread", p["garlic-bread"], 0.003)
    las = do_patch("lasagna", p["lasagna"], 0.003)
    fries = do_patch("fries", p["fries"], 0.002)

    colors = {
        "bread_crust": mean_color(bread[: bread.shape[0] // 8]),
        "bread_crumb": mean_color(hot[770:810, 140:200]),
        "lasagna_cheese": mean_color(las),
        "lasagna_sauce": mean_color(hot[360:420, 780:800]),
        "fries": mean_color(fries[fries.shape[0] // 3: 2 * fries.shape[0] // 3, fries.shape[1] // 3: 2 * fries.shape[1] // 3]),
        "fries_dish": mean_color(hot[1450:1480, 450:520]),
    }
    save_jpg(os.path.join(out_dir("lasagna"), "side.jpg"), layered_side(colors["lasagna_cheese"], colors["lasagna_sauce"]))
    save_jpg(os.path.join(out_dir("fries"), "fry.jpg"), speckle_texture(colors["fries"], dots=320, dark=(30, 70, 120)))

    # salad: the three bowls of the first board on the cold page
    cold = load("02-appetizers-cold.jpg")
    sd = out_dir("salad-plate")
    for i, (bx, by, br) in enumerate(p["salad-plate"]["bowls"]):
        disc, _ = polar_clamped_disc(cold, bx, by, br, 384, clamp=0.95)
        save_jpg(os.path.join(sd, f"bowl{i + 1}.jpg"), disc)
        save_jpg(os.path.join(sd, f"bowl{i + 1}_normal.jpg"),
                 normal_from_height(fine_detail(disc, 2) * 0.004, 0.12 / 384), 90)
    colors["salad_board"] = mean_color(cold[355:370, 120:500])
    colors["salad_bowl"] = mean_color(cold[310:320, 120:140])

    # stored as RGB 0-255 for Blender
    rgb = {k: [v[2], v[1], v[0]] for k, v in colors.items()}
    json.dump(rgb, open(os.path.join(TEX, "_shared", "colors.json"), "w"), indent=2)
    for slug, spec in cfg["productPhotos"].items():
        if slug.startswith("_"):
            continue
        name, x, y, size = spec
        product_photo(slug, load(name), x, y, size)
    print("done:", rgb)


def brand_assets():
    """The round pi mark, cut from the cover photo (menu/original/00-cover-logo.jpg)."""
    cx, cy, r = cfg["logo"]["circle"]
    img = load(cfg["logo"]["image"])
    crop = img[cy - r:cy + r, cx - r:cx + r]
    d = os.path.join(ROOT, "assets", "brand")
    os.makedirs(d, exist_ok=True)
    for size in (512, 192, 64):
        c = cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)
        yy, xx = np.mgrid[:size, :size]
        dist = np.sqrt((xx - size / 2 + 0.5) ** 2 + (yy - size / 2 + 0.5) ** 2)
        alpha = np.clip((size / 2 - 0.5 - dist) * 2 + 0.5, 0, 1) * 255  # anti-aliased circle
        cv2.imwrite(os.path.join(d, f"pi-{size}.png"), np.dstack([c, alpha.astype(np.uint8)]))


if __name__ == "__main__":
    main()
    brand_assets()
