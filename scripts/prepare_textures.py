"""Turn the original menu photos (+ their depth estimates) into 3D-ready maps.

Reads  : menu/original/*.jpg, assets/sources.json,
         assets/depth/* (made by scripts/estimate_depth.py)
Writes : assets/textures/<slug>/*  inputs for blender/build_models.py
         assets/products/<slug>.jpg  product photo shown on the product page

Every dish was photographed straight from above, so for each dish we get:
  * albedo   - the photo itself, cut out along the dish outline;
  * height   - a height field in metres: a physical base shape (pizza rim,
               bread slab, ...) plus the relief measured by the depth model
               (cheese bubbles, toppings, fries...), smoothed for the mesh;
  * normal   - the fine relief the mesh is too coarse to carry;
  * outlines - boards / dishes are cut out of the page with depth + colour.
Nothing is painted by hand; hidden or overprinted parts (under the pizza,
under menu text) are filled from the surrounding photo.

Usage:  python scripts/prepare_textures.py [slug ...]
"""
import json
import math
import os
import sys

import cv2
from scipy.ndimage import median_filter
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "menu", "original")
TEX = os.path.join(ROOT, "assets", "textures")
DEPTH = os.path.join(ROOT, "assets", "depth")
PHOTOS = os.path.join(ROOT, "assets", "products")
DEBUG = os.environ.get("PREP_DEBUG")  # folder for diagnostic overlays

cfg = json.load(open(os.path.join(ROOT, "assets", "sources.json"), encoding="utf-8"))
_cache = {}

PIZZA_DIAMETER_M = 0.30
# Depth Anything gives relative depth. All pizzas were shot with the same set-up, so one
# scale converts depth units to metres; it was calibrated on the Margherita, whose cherry
# tomato (about 3 cm across) stands about 2.2 cm above the cheese.
RELIEF_M_PER_UNIT = 0.022 / 115


# ---------------------------------------------------------------- io helpers
def load(name):
    if name not in _cache:
        img = cv2.imread(os.path.join(SRC, name), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(name)
        _cache[name] = img
    return _cache[name]


def page_depth(name):
    return np.load(os.path.join(DEPTH, "page_" + name.replace(".jpg", ".npy"))).astype(np.float32)


def dish_depth(slug):
    meta = json.load(open(os.path.join(DEPTH, slug + ".json")))
    return np.load(os.path.join(DEPTH, slug + ".npy")).astype(np.float32), meta


def out_dir(slug):
    d = os.path.join(TEX, slug)
    os.makedirs(d, exist_ok=True)
    return d


def save_jpg(path, img, q=88):
    cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, q, cv2.IMWRITE_JPEG_OPTIMIZE, 1])


def save_height(path, h_m):
    """16-bit PNG + scale; row 0 = top of the image (Blender flips on load)."""
    hmax = max(float(h_m.max()), 1e-6)
    cv2.imwrite(path, (np.clip(h_m / hmax, 0, 1) * 65535).astype(np.uint16))
    return hmax


def debug(name, img):
    if DEBUG:
        os.makedirs(DEBUG, exist_ok=True)
        cv2.imwrite(os.path.join(DEBUG, name), img)


# ---------------------------------------------------------------- image helpers
def smoothstep(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


def disk(r):
    r = max(1, int(r))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def hsv(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2HSV)


def wood_mask(img, lenient=False):
    h, s, v = cv2.split(hsv(img))
    if lenient:  # also shadowed wood next to the food
        return (h >= 3) & (h <= 30) & (s >= 35) & (v >= 70)
    return (h >= 5) & (h <= 28) & (s >= 45) & (s <= 215) & (v >= 95)


def white_mask(img):
    _, s, v = cv2.split(hsv(img))
    return (s < 38) & (v > 180)


def largest_component(mask, seed=None):
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if n <= 1:
        return mask.astype(bool)
    if seed is not None:
        ids = np.unique(lab[seed & (lab > 0)])
        if len(ids):
            best = max(ids, key=lambda i: stats[i, cv2.CC_STAT_AREA])
            return lab == best
    return lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))


def fill_holes(mask):
    m = mask.astype(np.uint8)
    h, w = m.shape
    ff = np.pad(m, 1).copy()
    cv2.floodFill(ff, None, (0, 0), 1)
    holes = ff[1:-1, 1:-1] == 0
    return mask.astype(bool) | holes


def normal_from_height(h_m, texel_m, strength=1.0):
    """Tangent-space normal map (glTF / OpenGL convention, +Y = up in the image)."""
    h_m = np.asarray(h_m, np.float32)
    gx = cv2.Sobel(h_m, cv2.CV_32F, 1, 0, ksize=3) / (8 * texel_m) * strength
    gy = cv2.Sobel(h_m, cv2.CV_32F, 0, 1, ksize=3) / (8 * texel_m) * strength
    n = np.dstack([-gx, gy, np.ones_like(gx)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    return cv2.cvtColor(((n * 0.5 + 0.5) * 255 + 0.5).astype(np.uint8), cv2.COLOR_RGB2BGR)


def directional_fill(img, valid, squash=6):
    """Fill invalid pixels along the wood grain (vertical): inpaint in a space that is
    squashed vertically, so information travels mostly along columns."""
    h, w = valid.shape
    hs = max(8, h // squash)
    small = cv2.resize(img, (w, hs), interpolation=cv2.INTER_AREA)
    vs = cv2.resize(valid.astype(np.float32), (w, hs), interpolation=cv2.INTER_AREA) > 0.999
    filled = cv2.inpaint(small, (~vs).astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)
    up = cv2.resize(filled, (w, h), interpolation=cv2.INTER_CUBIC)
    out = img.copy()
    out[~valid] = up[~valid]
    # keep the fine grain texture: add high-frequency detail copied from valid rows nearby
    return out


def fit_background_plane(depth, bg_mask, step=6):
    ys, xs = np.nonzero(bg_mask[::step, ::step])
    ys, xs = ys * step, xs * step
    z = depth[ys, xs]
    keep = np.ones(len(z), bool)
    for _ in range(4):
        A = np.c_[xs[keep], ys[keep], np.ones(keep.sum())]
        coef, *_ = np.linalg.lstsq(A, z[keep], rcond=None)
        res = z - (coef[0] * xs + coef[1] * ys + coef[2])
        keep = np.abs(res) < 2.5 * res[keep].std() + 1e-3
    yy, xx = np.mgrid[:depth.shape[0], :depth.shape[1]]
    return depth - (coef[0] * xx + coef[1] * yy + coef[2])


# ---------------------------------------------------------------- pizzas
def pizza_outline(spec, D, crop, img):
    """Exact pizza outline from the depth step between pizza and board."""
    cx, cy, r = spec["circle"]
    x0, y0, w, h = crop
    yy, xx = np.mgrid[:h, :w]
    rho = np.hypot(xx + x0 - cx, yy + y0 - cy) / r
    cheese = float(np.median(D[rho < 0.5]))
    sub = img[max(0, y0):y0 + h, max(0, x0):x0 + w]
    wood = np.zeros((h, w), bool)
    wood[max(0, -y0):max(0, -y0) + sub.shape[0], max(0, -x0):max(0, -x0) + sub.shape[1]] = wood_mask(sub)
    ring = (rho > 1.04) & (rho < 1.12) & wood
    board = float(np.median(D[ring])) if ring.sum() > 400 else float(np.percentile(D[(rho > 1.04) & (rho < 1.12)], 70))
    m = (D > board + 0.45 * (cheese - board)) & (rho < 1.12)
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, disk(3)).astype(bool)
    m = fill_holes(largest_component(m, seed=rho < 0.1))
    # radial outline: first exit from the mask along each ray
    th = np.linspace(0, 2 * np.pi, 720, endpoint=False)
    radii = np.arange(0.55 * r, 1.12 * r, 0.5, dtype=np.float32)
    mx = (cx - x0 + np.cos(th)[:, None] * radii[None]).astype(np.float32)
    my = (cy - y0 + np.sin(th)[:, None] * radii[None]).astype(np.float32)
    on = cv2.remap(m.astype(np.float32), mx, my, cv2.INTER_LINEAR) > 0.5
    first_out = np.argmax(~on, axis=1)
    first_out[on.all(axis=1)] = len(radii) - 1
    r_th = radii[first_out] - 0.25
    # a touching bowl / napkin can leak into the mask: a pizza outline is smooth, so
    # clamp it to a robust low-order Fourier fit (iteratively down-weighting outliers)
    # where the pizza runs off the photo the outline is the page edge: ignore those rays
    ex, ey = cx + np.cos(th) * r_th, cy + np.sin(th) * r_th
    H_, W_ = img.shape[:2]
    clipped = (ex < 3) | (ey < 3) | (ex > W_ - 4) | (ey > H_ - 4)
    A = np.column_stack([np.ones_like(th)] + [f(k * th) for k in range(1, 5) for f in (np.cos, np.sin)])
    w = np.where(clipped, 0.0, 1.0)
    for _ in range(6):
        coef, *_ = np.linalg.lstsq(A * w[:, None], r_th * w, rcond=None)
        ref = A @ coef
        w = np.where(clipped, 0.0, np.where(np.abs(r_th - ref) < 0.012 * ref, 1.0, 0.05))
    r_th = np.where(clipped, ref, np.clip(r_th, ref * 0.985, ref * 1.015))
    # light circular smoothing (depth edges are a little noisy)
    k = cv2.getGaussianKernel(9, 1.6).ravel()
    r_th = np.convolve(np.r_[r_th[-4:], r_th, r_th[:4]], k, mode="same")[4:-4]
    return th, r_th.astype(np.float32), m, cheese, board


def edge_radius(th_grid, th, r_th):
    """Interpolate the outline radius r(theta) at arbitrary angles."""
    t = np.mod(th_grid, 2 * np.pi) / (2 * np.pi) * len(th)
    i0 = np.floor(t).astype(int) % len(th)
    f = t - np.floor(t)
    return r_th[i0] * (1 - f) + r_th[(i0 + 1) % len(th)] * f


def onto_page(X, Y, cx, cy, shape, margin=2):
    """Sample points that fall off the photo (a pizza touching the page edge) are
    rotated about the pizza centre onto the nearest photographed part of the same ring."""
    h, w = shape[:2]
    X, Y = X.astype(np.float32).copy(), Y.astype(np.float32).copy()
    off = (X < margin) | (Y < margin) | (X > w - 1 - margin) | (Y > h - 1 - margin)
    for deg in range(4, 90, 4):
        if not off.any():
            break
        for sgn in (1, -1):
            a = math.radians(sgn * deg)
            dx, dy = X[off] - cx, Y[off] - cy
            nx, ny = cx + dx * math.cos(a) - dy * math.sin(a), cy + dx * math.sin(a) + dy * math.cos(a)
            ok = (nx >= margin) & (ny >= margin) & (nx <= w - 1 - margin) & (ny <= h - 1 - margin)
            idx = np.nonzero(off)
            X[idx[0][ok], idx[1][ok]] = nx[ok]
            Y[idx[0][ok], idx[1][ok]] = ny[ok]
            off[idx[0][ok], idx[1][ok]] = False
    return X, Y


def rim_profile(t, h_edge, h_peak, h_inner, t_peak, t_end=1.2):
    """Height of a pizza crust vs. t = distance from the edge / crust width:
    a rounded bun (quarter ellipse) up to the peak, then easing down to the cheese."""
    out = np.full(t.shape, h_inner, np.float32)
    a = t <= t_peak
    q = np.clip(t[a] / t_peak, 0, 1)
    out[a] = h_edge + (h_peak - h_edge) * np.sqrt(1 - (1 - q) ** 2)
    b = (t > t_peak) & (t < t_end)
    out[b] = h_peak + (h_inner - h_peak) * smoothstep((t[b] - t_peak) / (t_end - t_peak))
    return out


# crust cross-section (metres above the board) and where its top is, as a fraction of the crust width
RIM = {"h_edge": 0.002, "h_peak": 0.019, "h_cheese": 0.011, "t_peak": 0.55}
SIDE_BAND_M = 0.032  # texture margin outside the outline that the unrolled crust side uses


def do_pizza(slug, spec, size=1024):
    img = load(spec["image"])
    D, meta = dish_depth(slug)
    crop = meta["crop"]
    x0, y0 = crop[0], crop[1]
    th, r_th, mask, cheese, board = pizza_outline(spec, D, crop, img)
    cx, cy, _ = spec["circle"]
    r_mean = float(r_th.mean())
    s = PIZZA_DIAMETER_M / (2 * r_mean)          # metres per photo pixel
    crust_px = spec["crust"] * r_mean

    # relief = depth minus its large-scale trend (grey opening removes toppings)
    inside = cv2.erode(mask.astype(np.uint8), disk(4)).astype(bool)
    Dm = np.where(inside, D, cheese).astype(np.float32)
    low = cv2.morphologyEx(Dm, cv2.MORPH_OPEN, disk(0.11 * r_mean))
    low = cv2.GaussianBlur(low, (0, 0), 0.04 * r_mean)
    relief = np.where(inside, (D - low) * RELIEF_M_PER_UNIT, 0).astype(np.float32)
    relief = np.clip(relief, -0.002, 0.028)

    # texture square around the pizza, with a margin for the crust's side
    Rt = float(r_th.max()) + SIDE_BAND_M / s
    t = (np.arange(size) + 0.5) / size * 2 - 1
    U, V = np.meshgrid(t, t)                     # V grows downward (image rows)
    phi = np.arctan2(V, U)
    rho_px = np.hypot(U, V) * Rt
    r_edge = edge_radius(phi, th, r_th)
    lip = r_edge * 0.99                          # stay off the crust's shadow on the board
    # outside the outline the side of the crust is unrolled (see blender polar_relief):
    # mirror the crust ring there so the side shows real crust, not stretched edge pixels
    mirrored = np.maximum(lip - (rho_px - lip) * 0.8, r_edge - 0.9 * crust_px)
    src_r = np.where(rho_px > lip, mirrored, rho_px)
    X = (cx + np.cos(phi) * src_r).astype(np.float32)
    Y = (cy + np.sin(phi) * src_r).astype(np.float32)
    X, Y = onto_page(X, Y, cx, cy, img.shape)
    albedo = cv2.remap(img, X, Y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT).astype(np.float32)
    toast = 1 - 0.16 * smoothstep((rho_px - r_edge) / (0.02 / s))  # the underside is a little darker
    albedo = np.clip(albedo * toast[..., None], 0, 255).astype(np.uint8)

    clamp = np.minimum(rho_px, lip)
    Xc = (cx + np.cos(phi) * clamp).astype(np.float32)
    Yc = (cy + np.sin(phi) * clamp).astype(np.float32)
    Xc, Yc = onto_page(Xc, Yc, cx, cy, img.shape, margin=8)
    rel = cv2.remap(relief, Xc - x0, Yc - y0, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    e = (r_edge - rho_px) / crust_px             # 0 at the edge, 1 at the inner side of the crust
    base = rim_profile(np.maximum(e, 0), RIM["h_edge"], RIM["h_peak"], RIM["h_cheese"], RIM["t_peak"])
    H = base + rel * smoothstep(e / 0.3)
    texel = 2 * Rt * s / size
    Hg = cv2.GaussianBlur(H, (0, 0), 0.0015 / texel)   # what the mesh carries
    detail = H - Hg
    # the side band gets the mirrored detail too (same mapping as the albedo)
    mx = ((np.cos(phi) * src_r / Rt + 1) / 2 * size - 0.5).astype(np.float32)
    my = ((np.sin(phi) * src_r / Rt + 1) / 2 * size - 0.5).astype(np.float32)
    detail = np.where(rho_px > lip, cv2.remap(detail, mx, my, cv2.INTER_LINEAR), detail)
    nrm = normal_from_height(detail, texel)

    d = out_dir(slug)
    save_jpg(os.path.join(d, "albedo.jpg"), albedo, 90)
    save_jpg(os.path.join(d, "normal.jpg"), nrm, 92)
    hmax = save_height(os.path.join(d, "height.png"), Hg)
    peel = do_peel(slug, spec, img, (cx, cy), s, th, r_th)
    json.dump({
        "kind": "pizza", "diameter_m": PIZZA_DIAMETER_M, "half_extent_m": float(Rt * s), "height_max_m": hmax,
        "outline_m": (r_th * s).round(5).tolist(), "rim_peak_from_edge_m": round(RIM["t_peak"] * crust_px * s, 5),
        "grain_m_per_px": s,
        "peel": peel,
    }, open(os.path.join(d, "model.json"), "w"))
    side = r_mean * 2.24
    product_photo(slug, img, cx - side / 2, cy - side / 2, side)
    if DEBUG:
        sh = cv2.normalize(Hg, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        debug(f"{slug}_maps.jpg", np.hstack([albedo, cv2.cvtColor(sh, cv2.COLOR_GRAY2BGR), nrm]))


def do_peel(slug, spec, img, center, s, th, r_th):
    """Cut the wooden peel out of the page: outline (symmetric), hole and texture."""
    cx, cy = center
    H, W = img.shape[:2]
    E = fit_background_plane(page_depth(spec["image"]), cv2.erode(white_mask(img).astype(np.uint8), disk(12)) > 0)
    yy, xx = np.mgrid[:H, :W]
    phi = np.arctan2(yy - cy, xx - cx)
    rho = np.hypot(xx - cx, yy - cy)
    pizza = rho <= edge_radius(phi, th, r_th)
    pizza_d = cv2.dilate(pizza.astype(np.uint8), disk(5)).astype(bool)
    wood = wood_mask(img)
    ring = wood & ~pizza_d & (rho < r_th.max() * 1.12)
    e_board = float(np.median(E[ring]))
    # the table is ~0 after removing its plane; the board stands clearly above it
    cand = (E > 0.2 * e_board) & wood_mask(img, lenient=True) & ~pizza_d
    # the peel never reaches far above or beside the pizza (keeps props like chicken pieces out)
    rmax = float(r_th.max())
    pizza_top = cy - edge_radius(np.array([-np.pi / 2]), th, r_th)[0]
    cand &= (yy > pizza_top - 0.06 * rmax) & (np.abs(xx - cx) < 1.1 * rmax)
    cand = cv2.morphologyEx(cand.astype(np.uint8), cv2.MORPH_CLOSE, disk(4))
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, disk(6))  # drop crumbs, garnish, chicken bits
    seed = cv2.dilate(pizza.astype(np.uint8), disk(14)).astype(bool) & ~pizza_d
    # the pizza splits the visible board into pieces (top corners, handle): keep all of them
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cand, 8)
    keep = [i for i in np.unique(lab[seed & (lab > 0)]) if stats[i, cv2.CC_STAT_AREA] > 1500]
    board = np.isin(lab, keep)
    bg = white_mask(img)

    rows = np.nonzero(board.any(axis=1))[0]
    y_top, y_bot = int(rows.min()), int(rows.max())
    L = np.full(H, np.nan)
    R = np.full(H, np.nan)
    validL = np.zeros(H, bool)
    validR = np.zeros(H, bool)
    for y in range(y_top, y_bot + 1):
        xs = np.nonzero(board[y])[0]
        if not len(xs):
            continue
        l, r = xs.min(), xs.max()
        pz = np.nonzero(pizza_d[y])[0]
        L[y], R[y] = l, r
        out_l, out_r = max(0, l - 6), min(W - 1, r + 6)
        validL[y] = bg[y, out_l] and l > 2 and not (len(pz) and pz.min() <= l + 3)
        validR[y] = bg[y, out_r] and r < W - 3 and not (len(pz) and pz.max() >= r - 3)
    # board axis (allowed to tilt slightly) from rows where both edges are visible;
    # a hidden edge is mirrored from the visible one, rows with none are interpolated
    ys = np.arange(y_top, y_bot + 1)
    both = (validL & validR)[ys]
    if both.sum() > 20:
        k, c0 = np.polyfit(ys[both], ((L + R) / 2)[ys][both], 1)
    else:
        k, c0 = 0.0, float(cx)
    axis = k * ys + c0
    vl, vr = validL[ys], validR[ys]
    half = np.where(vl & vr, (R[ys] - L[ys]) / 2,
                    np.where(vl, axis - L[ys], np.where(vr, R[ys] - axis, np.nan)))
    ok = ~np.isnan(half)
    half = np.interp(ys, ys[ok], half[ok]).astype(np.float32)
    half = median_filter(half, size=9, mode="nearest")
    half = cv2.GaussianBlur(half.reshape(-1, 1), (1, 0), 2.0).ravel()
    tail = len(ys) * 9 // 10  # the handle end only narrows ...
    half[tail:] = np.minimum.accumulate(half[tail:])
    hb = float(half[len(ys) * 92 // 100])  # ... and is rounded like the real peel
    yc = ys[-1] - hb
    cap = ys > yc
    half[cap] = np.minimum(half[cap], np.sqrt(np.maximum(hb * hb - (ys[cap] - yc) ** 2, 0)) + 0.5)
    sel = np.r_[np.arange(0, len(ys), 3), len(ys) - 1] if (len(ys) - 1) % 3 else np.arange(0, len(ys), 3)
    poly = np.array([(axis[i] + half[i], ys[i]) for i in sel] +
                    [(axis[i] - half[i], ys[i]) for i in sel[::-1]], np.float32)

    # handle hole: a compact low spot well inside the handle
    filled = np.zeros((H, W), np.uint8)
    cv2.fillPoly(filled, [poly.astype(np.int32)], 1)
    inner = cv2.erode(filled, disk(6)).astype(bool)
    pz_bottom = cy + edge_radius(np.array([np.pi / 2]), th, r_th)[0]
    # the hole is too small for the depth model; it shows as a round non-wood spot
    not_wood = ~wood_mask(img, lenient=True) | (hsv(img)[:, :, 2] < 90)
    spot = inner & not_wood & (yy > pz_bottom + 0.4 * (y_bot - pz_bottom))
    spot = cv2.morphologyEx(spot.astype(np.uint8), cv2.MORPH_OPEN, disk(2))
    hole = None
    n, lab, stats, cent = cv2.connectedComponentsWithStats(spot, 8)
    best = []
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if 120 < area < 3000:
            (hx, hy), hr = cv2.minEnclosingCircle(np.argwhere(lab == i)[:, ::-1].astype(np.float32))
            roundness = area / (math.pi * hr * hr)
            if roundness > 0.55:
                best.append((roundness, [float(hx), float(hy), float(hr)]))
    if best:
        hole = max(best)[1]
    else:  # not readable in this photo: same peel model as the others
        hole = [float(axis[-1]), float(y_bot - 0.075 * (y_bot - y_top)), 0.0044 / s]

    # a clean strip of the handle (straight grain): reused to grow wood on other boards
    hy, hr = hole[1], hole[2]
    rows = ys[(ys > pz_bottom + 0.3 * (y_bot - pz_bottom)) & (ys < hy - hr - 10)]
    if len(rows) > 40:
        hw = int(0.6 * half[np.searchsorted(ys, rows)].min())
        xc_ = int(round(float(np.mean(axis[np.searchsorted(ys, rows)]))))
        save_jpg(os.path.join(out_dir(slug), "grain.jpg"), img[rows[0]:rows[-1], xc_ - hw:xc_ + hw], 92)

    # texture: the photographed board; hidden / overprinted pixels filled along the grain
    x_min, x_max = int(poly[:, 0].min()) - 6, int(math.ceil(poly[:, 0].max())) + 6
    y_min, y_max = y_top - 6, y_bot + 6
    pad = cv2.copyMakeBorder(img, 64, 64, 64, 64, cv2.BORDER_REPLICATE)
    tex = pad[y_min + 64:y_max + 64, x_min + 64:x_max + 64].copy()
    sub = (slice(max(0, y_min), min(H, y_max)), slice(max(0, x_min), min(W, x_max)))
    valid = np.zeros(tex.shape[:2], bool)
    v_page = board & wood & ~cv2.dilate(pizza.astype(np.uint8), disk(3)).astype(bool) & (hsv(img)[:, :, 2] > 80)
    v_page = cv2.erode(v_page.astype(np.uint8), disk(1)).astype(bool)
    oy, ox = sub[0].start - y_min, sub[1].start - x_min
    valid[oy:oy + sub[0].stop - sub[0].start, ox:ox + sub[1].stop - sub[1].start] = v_page[sub]
    tex = directional_fill(tex, valid)
    tex = cv2.resize(tex, (512, 1024), interpolation=cv2.INTER_AREA)
    d = out_dir(slug)
    save_jpg(os.path.join(d, "board.jpg"), tex, 88)
    if DEBUG:
        vis = img.copy()
        cv2.polylines(vis, [poly.astype(np.int32)], True, (0, 0, 255), 2)
        if hole:
            cv2.circle(vis, (int(hole[0]), int(hole[1])), int(hole[2]), (255, 0, 0), 2)
        debug(f"{slug}_peel.jpg", np.hstack([cv2.resize(vis, (540, 960)), cv2.resize(tex, (540, 960))]))

    def m(x, y):
        return [round(float(x - cx) * s, 5), round(-float(y - cy) * s, 5)]

    return {
        "outline_m": [m(x, y) for x, y in poly[::-1]],  # counter-clockwise in model space
        "hole_m": (m(hole[0], hole[1]) + [round(hole[2] * s, 5)]) if hole else None,
        "uv_rect_m": m(x_min, y_max) + [round((x_max - x_min) * s, 5), round((y_max - y_min) * s, 5)],
        "thickness_m": 0.014,
    }


# ---------------------------------------------------------------- wood for hidden board areas
def lowpass_fill(img, valid, sigma):
    """Smooth colour field from the valid pixels, extended over the whole image
    (normalised convolution, falling back to coarser scales where data is far)."""
    flat = img.ndim == 2
    img = (img[..., None] if flat else img).astype(np.float32)
    v = valid.astype(np.float32)
    res = np.broadcast_to(img[valid].mean(axis=0), img.shape).astype(np.float32).copy()
    for sg in (sigma * 64, sigma * 16, sigma * 4, sigma):
        w = cv2.GaussianBlur(v, (0, 0), sg)
        est = cv2.GaussianBlur(img * v[..., None], (0, 0), sg).reshape(img.shape) / np.maximum(w, 1e-6)[..., None]
        a = np.clip(w / 0.25, 0, 1)[..., None]
        res = est * a + res * (1 - a)
    return res[..., 0] if flat else res


def grain_texture(size, m_per_px, angle_deg, seed=0):
    """Straight-grain wood (grain along angle_deg, image degrees) built from the clean
    peel handles photographed on the pizza pages (assets/textures/<pizza>/grain.jpg)."""
    rng = np.random.default_rng(seed)
    strips = []
    for slug in cfg["pizzas"]:
        f = os.path.join(TEX, slug, "grain.jpg")
        if os.path.exists(f):
            meta = json.load(open(os.path.join(TEX, slug, "model.json")))
            g = cv2.imread(f)
            k = meta["grain_m_per_px"] / m_per_px
            strips.append(cv2.resize(g, None, fx=k, fy=k, interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC))
    L = int(size * 1.5)
    canvas = np.zeros((L, L, 3), np.float32)
    weight = np.zeros((L, L), np.float32)
    x = 0
    while x < L:
        st = strips[rng.integers(len(strips))].astype(np.float32)
        h, w = st.shape[:2]
        if rng.random() < 0.5:
            st = st[:, ::-1]
        col = np.zeros((L + h, w, 3), np.float32)
        cw = np.zeros((L + h, w), np.float32)
        ramp_v = np.minimum(1, np.minimum(np.arange(h) + 1, h - np.arange(h)) / (0.2 * h))
        y = -int(rng.integers(h))
        flip = False
        while y < L:
            piece = st[::-1] if flip else st
            y0, y1 = max(0, y), min(L + h, y + h)
            col[y0:y1] += piece[y0 - y:y1 - y] * ramp_v[y0 - y:y1 - y, None, None]
            cw[y0:y1] += ramp_v[y0 - y:y1 - y, None]
            y += int(h * 0.8)
            flip = not flip
        col = col[:L] / np.maximum(cw[:L], 1e-6)[..., None]
        ramp_h = np.minimum(1, np.minimum(np.arange(w) + 1, w - np.arange(w)) / (0.25 * w))
        x1 = min(L, x + w)
        canvas[:, x:x1] += col[:, :x1 - x] * ramp_h[None, :x1 - x, None]
        weight[:, x:x1] += ramp_h[None, :x1 - x]
        x += int(w * 0.75)
    canvas /= np.maximum(weight, 1e-6)[..., None]
    rot = cv2.getRotationMatrix2D((L / 2, L / 2), 90 - angle_deg, 1.0)
    canvas = cv2.warpAffine(canvas, rot, (L, L), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    o = (L - size) // 2
    return canvas[o:o + size, o:o + size]


def grain_angle(img, valid):
    """Dominant wood-grain direction (image degrees) from the structure tensor."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g = g - cv2.GaussianBlur(g, (0, 0), 6)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0) * valid
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1) * valid
    a = 0.5 * math.degrees(math.atan2(2 * (gx * gy).sum(), (gx * gx).sum() - (gy * gy).sum()))
    return a + 90  # grain runs across the strongest gradients


def board_texture(photo, valid, m_per_px, seed=0, angle=None):
    """Board texture: the photo's colour and shading (incl. food shadows) where the board
    is visible, smoothly extended elsewhere, carrying real wood grain everywhere."""
    sigma = 0.0015 / m_per_px                    # keep shading features above ~1.5 mm
    base = lowpass_fill(photo, valid, sigma)
    if angle is None:
        angle = grain_angle(photo, valid)
    synth = grain_texture(photo.shape[0], m_per_px, angle, seed)
    detail = synth - cv2.GaussianBlur(synth, (0, 0), sigma)
    hp = photo.astype(np.float32) - cv2.GaussianBlur(photo.astype(np.float32), (0, 0), sigma)
    gain = float(hp[valid].std() / max(detail.std(), 1e-6))
    return np.clip(base + detail * gain, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- generic relief items
def depth_on_page(slug, shape):
    """The dish's high-resolution depth crop placed in page coordinates (NaN elsewhere)."""
    D, meta = dish_depth(slug)
    x0, y0, w, h = meta["crop"]
    out = np.full(shape[:2], np.nan, np.float32)
    ys, xs = slice(max(0, y0), min(shape[0], y0 + h)), slice(max(0, x0), min(shape[1], x0 + w))
    out[ys, xs] = D[ys.start - y0:ys.stop - y0, xs.start - x0:xs.stop - x0]
    return out, meta


def sample(arr, X, Y):
    return cv2.remap(np.nan_to_num(arr).astype(np.float32), X.astype(np.float32), Y.astype(np.float32),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def mask_outline(mask, center, n=720, sigma=1.5):
    """Radius of a star-shaped mask along n rays from center (first exit)."""
    cx, cy = center
    h, w = mask.shape
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    radii = np.arange(1.0, np.hypot(w, h), 0.5, dtype=np.float32)
    mx = (cx + np.cos(th)[:, None] * radii[None]).astype(np.float32)
    my = (cy + np.sin(th)[:, None] * radii[None]).astype(np.float32)
    on = cv2.remap(mask.astype(np.float32), mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                   borderValue=0) > 0.5
    r = radii[np.argmax(~on, axis=1)] - 0.25
    k = cv2.getGaussianKernel(int(6 * sigma) | 1, sigma).ravel()
    p = len(k) // 2
    r = np.convolve(np.r_[r[-p:], r, r[:p]], k, mode="same")[p:-p]
    return th, r.astype(np.float32)


def relief_maps(img, center, th, r_th, s, height_fn, size=1024, side_band_m=0.03, mirror_px=None, toast=0.16,
                side_color=None, side_texture=None):
    """Albedo / height / detail-normal maps of a star-shaped item seen from above.

    The texture square is centred on `center` (page px) and reaches side_band_m beyond
    the outline: there the photo is mirrored inwards across the edge (the band is
    squeezed into a strip mirror_px deep), because the mesh unrolls its sides into
    that band (blender polar_relief). side_color (BGR) paints the sides plain instead
    (e.g. the white outside wall of a dish); side_texture = (image, height_m) wraps an
    image around the sides (row 0 at the top edge, tiled along the outline).
    height_fn(X, Y, e_px) -> metres, with (X, Y) page coordinates clamped inside the
    outline and e_px the distance to the edge."""
    cx, cy = center
    Rt = float(r_th.max()) + side_band_m / s
    t = (np.arange(size) + 0.5) / size * 2 - 1
    U, V = np.meshgrid(t, t)                     # V grows downward (image rows)
    phi = np.arctan2(V, U)
    rho = np.hypot(U, V) * Rt
    r_edge = edge_radius(phi, th, r_th)
    lip = r_edge * 0.99                          # stay off the shadow at the very edge
    depth = mirror_px if mirror_px else 0.3 * float(r_th.mean())
    squeeze = min(0.8, depth / (side_band_m / s))
    src_r = np.where(rho > lip, np.maximum(lip - (rho - lip) * squeeze, r_edge - depth), rho)
    X = (cx + np.cos(phi) * src_r).astype(np.float32)
    Y = (cy + np.sin(phi) * src_r).astype(np.float32)
    albedo = cv2.remap(img, X, Y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT).astype(np.float32)
    if side_color is not None:
        albedo[rho > lip] = np.asarray(side_color, np.float32)
    if side_texture is not None:
        stex, sh_m = side_texture
        p = np.c_[r_th * np.cos(th), r_th * np.sin(th)]
        L = np.r_[0, np.cumsum(np.linalg.norm(np.diff(np.vstack([p, p[:1]]), axis=0), axis=1))]
        t_idx = np.mod(phi, 2 * np.pi) / (2 * np.pi) * len(th)
        arc = np.interp(t_idx, np.arange(len(th) + 1), L)          # px along the outline
        drop = np.maximum(rho - r_edge, 0) * s                      # metres below the top edge
        su = np.mod(arc, stex.shape[1]).astype(np.float32)
        sv = (np.clip(drop / sh_m, 0, 1) * (stex.shape[0] - 1)).astype(np.float32)
        wrapped = cv2.remap(stex, su, sv, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP).astype(np.float32)
        albedo[rho > lip] = wrapped[rho > lip]
    shade = 1 - toast * smoothstep((rho - r_edge) / (0.02 / s))  # undersides are a little darker
    albedo = np.clip(albedo * shade[..., None], 0, 255).astype(np.uint8)
    clamp = np.minimum(rho, lip)
    Xc, Yc = cx + np.cos(phi) * clamp, cy + np.sin(phi) * clamp
    H = height_fn(Xc, Yc, r_edge - rho).astype(np.float32)
    texel = 2 * Rt * s / size
    Hg = cv2.GaussianBlur(H, (0, 0), 0.0015 / texel)   # what the mesh carries
    detail = H - Hg
    mx = ((np.cos(phi) * src_r / Rt + 1) / 2 * size - 0.5).astype(np.float32)
    my = ((np.sin(phi) * src_r / Rt + 1) / 2 * size - 0.5).astype(np.float32)
    plain = side_color is not None or side_texture is not None
    outside = 0.0 if plain else cv2.remap(detail, mx, my, cv2.INTER_LINEAR)
    detail = np.where(rho > lip, outside, detail)
    return albedo, Hg, normal_from_height(detail, texel), Rt


def relief_part(slug, name, img, center, th, r_th, s, origin, height_fn, size=1024, side_band_m=0.03,
                mirror_px=None, rim_peak_m=0.004, edge_drop=0.5, tuck=0.97, roughness=0.55, target_tris=12000,
                toast=0.16, side_color=None, side_texture=None):
    albedo, Hg, nrm, Rt = relief_maps(img, center, th, r_th, s, height_fn, size, side_band_m, mirror_px, toast,
                                      side_color, side_texture)
    d = out_dir(slug)
    save_jpg(os.path.join(d, f"{name}_albedo.jpg"), albedo, 90)
    save_jpg(os.path.join(d, f"{name}_normal.jpg"), nrm, 92)
    hmax = save_height(os.path.join(d, f"{name}_height.png"), Hg)
    if DEBUG:
        sh = cv2.normalize(Hg, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        debug(f"{slug}_{name}_maps.jpg", np.hstack([albedo, cv2.cvtColor(sh, cv2.COLOR_GRAY2BGR), nrm]))
    return {
        "type": "relief", "name": name, "albedo": f"{name}_albedo.jpg", "normal": f"{name}_normal.jpg",
        "height": f"{name}_height.png", "height_max_m": hmax, "half_extent_m": float(Rt * s),
        "outline_m": (r_th * s).round(5).tolist(), "offset_m": to_model(center, origin, s) + [0.0],
        "rim_peak_m": rim_peak_m, "edge_drop": edge_drop, "tuck": tuck, "roughness": roughness,
        "target_tris": target_tris,
    }


def to_model(p, origin, s):
    """Page pixel -> model metres (x right, y up) relative to origin."""
    return [round(float(p[0] - origin[0]) * s, 5), round(-float(p[1] - origin[1]) * s, 5)]


def contour_polygon(mask, origin, s, step_px=3.0, smooth_px=2.0):
    """Largest outer contour of a mask as a smooth CCW polygon in model metres."""
    cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cnts, key=cv2.contourArea)[:, 0, :].astype(np.float64)
    k = cv2.getGaussianKernel(int(6 * smooth_px) | 1, smooth_px).ravel()
    p = len(k) // 2
    c = np.c_[[np.convolve(np.r_[v[-p:], v, v[:p]], k, mode="same")[p:-p] for v in c.T]].T
    seg = np.linalg.norm(np.diff(np.vstack([c, c[:1]]), axis=0), axis=1)
    keep = np.r_[0, np.nonzero(np.diff(np.floor(np.cumsum(seg) / step_px)))[0] + 1]
    pts = [to_model(q, origin, s) for q in c[keep]]
    return pts


def circle_of(mask):
    (x, y), r = cv2.minEnclosingCircle(np.argwhere(mask)[:, ::-1].astype(np.float32))
    return float(x), float(y), float(r)


def fit_circle(pts):
    """Least-squares circle (Kasa) with trimming of the worst 25 % points."""
    p = np.asarray(pts, np.float64)
    for _ in range(4):
        A = np.c_[p[:, 0], p[:, 1], np.ones(len(p))]
        b = -(p[:, 0] ** 2 + p[:, 1] ** 2)
        (D, E, F), *_ = np.linalg.lstsq(A, b, rcond=None)
        cx, cy = -D / 2, -E / 2
        r = math.sqrt(max(cx * cx + cy * cy - F, 1e-9))
        res = np.abs(np.hypot(p[:, 0] - cx, p[:, 1] - cy) - r)
        p = p[res <= np.percentile(res, 75)] if len(p) > 40 else p
    return cx, cy, r


def overlay_mask(img, region):
    """Menu graphics drawn over the photo inside `region`: the near-black, colourless
    lettering (food darks are tinted) and the white hand-drawn arrows next to it."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.int16)
    dark = (lab[..., 0] < 95) & (np.abs(lab[..., 1] - 128) < 9) & (np.abs(lab[..., 2] - 128) < 12) & region
    n, lab_, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(dark.astype(np.uint8), disk(1)), 8)
    strokes = np.isin(lab_, [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 25])
    near = cv2.dilate(strokes.astype(np.uint8), disk(9)).astype(bool)
    _, ss, vv = cv2.split(hsv(img))
    white = (ss < 30) & (vv > 225) & near            # arrow fill always sits next to its outline
    return (cv2.dilate(strokes.astype(np.uint8), disk(2)).astype(bool) | white) & region


def inpaint(img, mask, radius=5):
    return cv2.inpaint(img, mask.astype(np.uint8) * 255, radius, cv2.INPAINT_TELEA)


def otsu_level(values):
    v = values[np.isfinite(values)]
    lo, hi = np.percentile(v, 1), np.percentile(v, 99)
    v8 = np.clip((v - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)
    t, _ = cv2.threshold(v8.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return lo + t / 255 * (hi - lo)


def write_parts(slug, parts, s, extra=None):
    json.dump({"kind": "parts", "m_per_px": s, "parts": parts, **(extra or {})},
              open(os.path.join(out_dir(slug), "model.json"), "w"), indent=1)


# ---------------------------------------------------------------- fries (white serving dish)
def do_fries(slug, spec):
    img = load(spec["image"])
    s = spec["m_per_px"]
    D, meta = depth_on_page(slug, img.shape)
    x0, y0, w, h = meta["crop"]
    inside = np.isfinite(D)
    raw = largest_component(inside & (np.nan_to_num(D) > otsu_level(D[inside])))
    dish = fill_holes(raw)
    body = cv2.morphologyEx(dish.astype(np.uint8), cv2.MORPH_OPEN, disk(0.12 * math.sqrt(dish.sum())))
    body = largest_component(body)
    # the dish is convex: fries hanging over the rim or a shadowed rim must not notch it
    hull = cv2.convexHull(np.argwhere(body)[:, ::-1].astype(np.int32))
    body = np.zeros_like(body, np.uint8)
    cv2.fillPoly(body, [hull[:, 0, :]], 1)
    body = body.astype(bool)
    handle = largest_component(dish & ~cv2.dilate(body.astype(np.uint8), disk(3)).astype(bool))
    hole_m = (dish & ~raw) & cv2.dilate(handle.astype(np.uint8), disk(4)).astype(bool)
    ys, xs = np.nonzero(body)
    center = (float(xs.mean()), float(ys.mean()))
    th, r_th = mask_outline(body, center)

    ring = cv2.dilate(dish.astype(np.uint8), disk(25)).astype(bool) & ~cv2.dilate(dish.astype(np.uint8), disk(8)).astype(bool)
    d_table = float(np.median(D[ring & inside]))
    rim = body & ~cv2.erode(body.astype(np.uint8), disk(10)).astype(bool)
    d_rim = float(np.median(D[rim]))
    k = RIM_HEIGHT["fries"] / (d_rim - d_table)  # depth units -> metres, from the dish rim height

    def height(X, Y, e):
        return np.clip((sample(D, X, Y) - d_table) * k, 0.004, 0.08)

    ceramic = np.median(img[rim & white_mask(img)], axis=0)
    parts = [relief_part(slug, "dish", img, center, th, r_th, s, center, height, size=1024,
                         mirror_px=0.006 / s, rim_peak_m=0.004, edge_drop=0.55, tuck=0.96, roughness=0.42,
                         target_tris=36000, toast=0.08, side_color=ceramic)]
    # handle tab with its hole; tucked under the rim
    tab = handle | (cv2.dilate(handle.astype(np.uint8), disk(16)).astype(bool) & body)
    z = (D[handle] - d_table) * k
    hx, hy, hr = circle_of(largest_component(hole_m)) if hole_m.any() else (None, None, None)
    ys, xs = np.nonzero(tab)
    bx0, bx1, by0, by1 = xs.min() - 6, xs.max() + 6, ys.min() - 6, ys.max() + 6
    tex = img[by0:by1, bx0:bx1].copy()
    tex[~cv2.erode(handle.astype(np.uint8), disk(2)).astype(bool)[by0:by1, bx0:bx1]] = ceramic  # sides: plain ceramic
    tex = cv2.resize(tex, (512, 256), interpolation=cv2.INTER_AREA)
    save_jpg(os.path.join(out_dir(slug), "handle.jpg"), tex, 88)
    parts.append({
        "type": "board", "name": "handle", "outline_m": contour_polygon(tab, center, s),
        "hole_m": (to_model((hx, hy), center, s) + [round(hr * s, 5)]) if hx is not None else None,
        "thickness_m": 0.007, "z_top_m": round(min(float(np.median(z)), RIM_HEIGHT["fries"] - 0.004), 4),
        "albedo": "handle.jpg",
        "uv_rect_m": to_model((bx0, by1), center, s) + [round((bx1 - bx0) * s, 5), round((by1 - by0) * s, 5)],
        "roughness": 0.35,
    })
    write_parts(slug, parts, s)


# ---------------------------------------------------------------- garlic bread on a round board
def do_garlic_bread(slug, spec):
    img = load(spec["image"])
    s = spec["m_per_px"]
    H, W = img.shape[:2]
    E = fit_background_plane(page_depth(spec["image"]), cv2.erode(white_mask(img).astype(np.uint8), disk(12)) > 0)
    D, meta = depth_on_page(slug, img.shape)
    x0, y0, w, h = spec["board_roi"]
    roi = np.zeros((H, W), bool)
    roi[y0:y0 + h, x0:x0 + w] = True
    wood = wood_mask(img, lenient=True)
    e_b = float(np.median(E[wood & roi & (E > 8)]))
    board = largest_component((E > 0.1 * e_b) & wood & roi)  # the far side of the board is low in the depth map
    board = fill_holes(cv2.morphologyEx(board.astype(np.uint8), cv2.MORPH_CLOSE, disk(9)).astype(bool))
    # circle through the board's visible rim (points next to the white table, not the page edge)
    cnts, _ = cv2.findContours(board.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cnts, key=cv2.contourArea)[:, 0, :]
    bg = cv2.dilate(white_mask(img).astype(np.uint8), disk(6)).astype(bool)
    rim_pts = [p for p in c if 3 < p[0] < W - 3 and 3 < p[1] < H - 3 and bg[p[1], p[0]]]
    bx, by, br = fit_circle(rim_pts)

    # bread slices: clearly above the board in the high-resolution depth
    inside = np.isfinite(D) & board
    tall = inside & (np.nan_to_num(D) > otsu_level(D[inside]))
    tall = cv2.morphologyEx(tall.astype(np.uint8), cv2.MORPH_OPEN, disk(5))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(tall, 8)
    slices = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] > 6000 and stats[i, cv2.CC_STAT_LEFT] > 2]
    slices = sorted(slices, key=lambda i: -stats[i, cv2.CC_STAT_AREA])[:spec.get("pieces", 2)]
    over = overlay_mask(img, roi)
    clean = inpaint(img, over)
    origin = (bx, by)
    parts = []
    taken = np.zeros((H, W), bool)
    for j, i in enumerate(slices):
        m = fill_holes(lab == i)
        taken |= m
        ys, xs = np.nonzero(m)
        center = (float(xs.mean()), float(ys.mean()))
        th, r_th = mask_outline(m, center)
        ring = cv2.dilate(m.astype(np.uint8), disk(20)).astype(bool) & ~cv2.dilate(m.astype(np.uint8), disk(6)).astype(bool)
        d_b = float(np.median(D[ring & inside]))
        d_in = float(np.median(D[cv2.erode(m.astype(np.uint8), disk(12)).astype(bool)]))
        k = spec["thickness_m"] / (d_in - d_b)

        def height(X, Y, e, d_b=d_b, k=k):
            return np.clip((sample(D, X, Y) - d_b) * k, 0.003, 0.06)

        parts.append(relief_part(slug, f"bread{j + 1}", clean, center, th, r_th, s, origin, height, size=1024,
                                 mirror_px=0.012 / s, rim_peak_m=0.008, edge_drop=0.6, tuck=0.97, roughness=0.6,
                                 target_tris=14000, toast=0.22))
    # board texture: the photo's colour/shading where the board is visible and clean,
    # extended beyond the page edge, under the bread and behind the menu lettering,
    # with real wood grain over all of it
    size = 1024
    t = (np.arange(size) + 0.5) / size * 2 - 1
    U, V = np.meshgrid(t, t)
    X = (bx + U * (br + 4)).astype(np.float32)
    Y = (by + V * (br + 4)).astype(np.float32)
    over_d = cv2.dilate(over.astype(np.uint8), disk(3)).astype(bool)
    # (the bread is modelled in 3D, so its photographed shadow ring is left out as well)
    valid_page = board & wood & ~cv2.dilate(taken.astype(np.uint8), disk(16)).astype(bool) & ~over_d
    valid_page &= hsv(img)[:, :, 1] > 50
    valid_page = cv2.erode(valid_page.astype(np.uint8), disk(4)).astype(bool)
    tex = cv2.remap(clean, X, Y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    valid = cv2.remap(valid_page.astype(np.uint8), X, Y, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT,
                      borderValue=0).astype(bool)
    tex = board_texture(tex, valid, 2 * (br + 4) * s / size, seed=3)
    save_jpg(os.path.join(out_dir(slug), "board.jpg"), tex, 88)
    a = np.linspace(0, 2 * np.pi, 180, endpoint=False)
    parts.insert(0, {
        "type": "board", "name": "board", "outline_m": [[round(br * s * math.cos(q), 5), round(br * s * math.sin(q), 5)] for q in a],
        "hole_m": None, "thickness_m": 0.02, "z_top_m": 0.0, "albedo": "board.jpg",
        "uv_rect_m": [round(-(br + 4) * s, 5), round(-(br + 4) * s, 5), round(2 * (br + 4) * s, 5), round(2 * (br + 4) * s, 5)],
        "roughness": 0.62,
    })
    if DEBUG:
        vis = img.copy()
        cv2.circle(vis, (int(bx), int(by)), int(br), (0, 0, 255), 2)
        vis[over] = (255, 0, 255)
        debug(f"{slug}_board.jpg", np.hstack([cv2.resize(vis[600:1500, 0:600], (400, 600)), cv2.resize(tex, (600, 600))]))
    write_parts(slug, parts, s)


# ---------------------------------------------------------------- salad plate (3 bowls on a slate paddle)
def do_salad(slug, spec):
    img = load(spec["image"])
    s = spec["m_per_px"]
    H, W = img.shape[:2]
    E = fit_background_plane(page_depth(spec["image"]), cv2.erode(white_mask(img).astype(np.uint8), disk(8)) > 0)
    D, meta = depth_on_page(slug, img.shape)
    x0, y0, w, h = spec["tray_roi"]
    roi = np.zeros((H, W), bool)
    roi[y0:y0 + h, x0:x0 + w] = True
    tray = largest_component((E > 0.35 * np.percentile(E[roi], 60)) & roi)
    tray = fill_holes(cv2.morphologyEx(tray.astype(np.uint8), cv2.MORPH_CLOSE, disk(4)).astype(bool))
    ys, xs = np.nonzero(tray)
    origin = (float(xs.mean()), float(ys.mean()))
    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (5, 5), 1.2)
    parts = [{
        "type": "board", "name": "tray", "outline_m": contour_polygon(tray, origin, s, step_px=2.0, smooth_px=2.5),
        "hole_m": None, "thickness_m": 0.012, "z_top_m": 0.0, "albedo": None,
        "color": [int(v) for v in np.median(img[tray & (hsv(img)[:, :, 2] < 70)], axis=0)[::-1]],
        "roughness": 0.38,
    }]
    dark_tray = tray & (hsv(img)[:, :, 2] < 70)
    d_tray = float(np.nanmedian(D[dark_tray & np.isfinite(D)]))
    for j, (bx, by, br) in enumerate(spec["bowls"]):
        yy, xx = np.mgrid[:H, :W]
        rr = np.hypot(xx - bx, yy - by)
        rim = (rr > br - 0.09 * br) & (rr < br - 0.01 * br)
        d_rim = float(np.nanmedian(D[rim]))
        k = spec["bowl_height_m"] / (d_rim - d_tray)
        inner = br * (1 - spec["bowl_wall_frac"])
        th = np.linspace(0, 2 * np.pi, 720, endpoint=False)
        r_th = np.full(720, inner, np.float32)
        z_rim = spec["bowl_height_m"]

        def height(X, Y, e, d_rim=d_rim, k=k):
            # X, Y are in the 2x upsampled page used for the texture
            # the low-resolution page exaggerates depth inside the bowls: halve it
            return np.clip(z_rim + 0.5 * (sample(D, X / 2, Y / 2) - d_rim) * k, z_rim - 0.012, z_rim + 0.018)

        up = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_LANCZOS4)  # low-resolution page
        part = relief_part(slug, f"salad{j + 1}", up, (bx * 2, by * 2), th, r_th * 2, s / 2, (origin[0] * 2, origin[1] * 2),
                           height, size=512, side_band_m=0.01, mirror_px=0.01 / (s / 2), rim_peak_m=None,
                           edge_drop=0.92, tuck=0.8, roughness=0.5, target_tris=6000, toast=0.0)
        part["base_z"] = 0.015  # the salad's sides end on the bowl floor, inside the bowl
        parts.append(part)
        parts.append({"type": "bowl", "name": f"bowl{j + 1}", "center_m": to_model((bx, by), origin, s),
                      "radius_m": round(br * s, 5), "height_m": spec["bowl_height_m"],
                      "wall_m": round(br * s * spec["bowl_wall_frac"], 5), "color": [246, 244, 238], "roughness": 0.18})
    if DEBUG:
        vis = img.copy()
        cnts, _ = cv2.findContours(tray.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(vis, cnts, -1, (0, 0, 255), 1)
        for bx, by, br in spec["bowls"]:
            cv2.circle(vis, (int(bx), int(by)), int(br), (255, 0, 0), 1)
        debug(f"{slug}_tray.jpg", vis[100:450])
    write_parts(slug, parts, s)


RIM_HEIGHT = {"fries": 0.03}  # height of the fries dish rim above the table (m)


# ---------------------------------------------------------------- exemplar fill (overprinted / off-page areas)
def patch_fill(img, missing, source, patch=24, candidates=500, seed=0):
    """Fill `missing` pixels with patches copied from `source` pixels of the same photo,
    from the hole's border inwards, each patch chosen to match its known surroundings
    (exemplar-based inpainting). Keeps real texture: cheese spots, sauce, bamboo."""
    rng = np.random.default_rng(seed)
    out = img.astype(np.float32).copy()
    known = ~missing
    h, w = missing.shape
    ok_src = cv2.erode(source.astype(np.uint8), np.ones((patch, patch), np.uint8), anchor=(0, 0),
                       borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool)
    ok_src[h - patch + 1:, :] = False
    ok_src[:, w - patch + 1:] = False
    sy, sx = np.nonzero(ok_src)
    if not len(sy):
        return inpaint(img, missing)
    half = patch // 2
    for _ in range(200):
        if known.all():
            break
        dist = cv2.distanceTransform((~known).astype(np.uint8), cv2.DIST_L2, 3)
        front = np.argwhere((dist > 0) & (dist <= 1.5))
        if not len(front):
            break
        rng.shuffle(front)
        done = np.zeros_like(known)
        for cy, cx in front:
            if known[cy, cx] or done[cy, cx]:
                continue
            y0 = int(np.clip(cy - half, 0, h - patch))
            x0 = int(np.clip(cx - half, 0, w - patch))
            kn = known[y0:y0 + patch, x0:x0 + patch]
            tgt = out[y0:y0 + patch, x0:x0 + patch]
            pick = rng.integers(0, len(sy), candidates)
            cand = np.stack([out[sy[i]:sy[i] + patch, sx[i]:sx[i] + patch] for i in pick])
            err = (((cand - tgt) ** 2).sum(axis=3) * kn).sum(axis=(1, 2)) / max(kn.sum(), 1)
            best = cand[int(np.argmin(err))]
            a = np.where(kn, 0.0, 1.0)  # paste only into unknown pixels
            out[y0:y0 + patch, x0:x0 + patch] = tgt * (1 - a[..., None]) + best * a[..., None]
            known[y0:y0 + patch, x0:x0 + patch] = True
            done[y0:y0 + patch, x0:x0 + patch] = True
    return np.clip(out, 0, 255).astype(np.uint8)


def rounded_rect_mask(shape, x0, y0, x1, y1, r):
    m = np.zeros(shape, np.uint8)
    cv2.rectangle(m, (int(x0 + r), int(y0)), (int(x1 - r), int(y1)), 1, -1)
    cv2.rectangle(m, (int(x0), int(y0 + r)), (int(x1), int(y1 - r)), 1, -1)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
        cv2.circle(m, (int(cx), int(cy)), int(r), 1, -1)
    return m.astype(bool)


def side_band_texture(strip, cheese_bgr, pasta_bgr, height_px, layers=3):
    """Side of a lasagna: the photographed bolognese band stacked into layers separated
    by thin pasta sheets, under a cheese crust (colours sampled from the photo)."""
    w = strip.shape[1]
    band = max(4, int(height_px * 0.9 / layers) - 4)
    rows = [np.tile(np.asarray(cheese_bgr, np.uint8), (max(3, int(height_px * 0.1)), w, 1))]
    for i in range(layers):
        b = cv2.resize(strip if i % 2 == 0 else strip[::-1], (w, band), interpolation=cv2.INTER_AREA)
        rows += [b, np.tile(np.asarray(pasta_bgr, np.uint8), (4, w, 1))]
    side = np.vstack(rows)
    side = cv2.resize(side, (w, height_px), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(side, (3, 3), 0.8)


# ---------------------------------------------------------------- lasagna (board and slab cut by the page edge)
def do_lasagna(slug, spec):
    img = load(spec["image"])
    s = spec["m_per_px"]
    H, W = img.shape[:2]
    D, meta = depth_on_page(slug, img.shape)
    # work in a frame where the board is upright (measured by hand, see sources.json)
    (rcx, rcy), ang = spec["rectify_center"], spec["rectify_deg"]
    M = cv2.getRotationMatrix2D((rcx, rcy), ang, 1.0)
    OW, OH = spec["rectify_size"]
    R = cv2.warpAffine(img, M, (OW, OH), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    inpage = cv2.warpAffine(np.ones((H, W), np.uint8), M, (OW, OH), flags=cv2.INTER_NEAREST).astype(bool)
    dv = cv2.warpAffine(np.isfinite(D).astype(np.uint8), M, (OW, OH), flags=cv2.INTER_NEAREST).astype(bool)
    RD = cv2.warpAffine(np.nan_to_num(D), M, (OW, OH), flags=cv2.INTER_LINEAR)
    inpage = cv2.erode(inpage.astype(np.uint8), disk(3)).astype(bool)
    dv &= inpage
    bx0, by0, bx1, by1 = spec["board_rect"]
    lx0, ly0, lx1, ly1 = spec["lasagna_rect"]
    board = rounded_rect_mask(R.shape[:2], bx0, by0, bx1, by1, 12)
    las = rounded_rect_mask(R.shape[:2], lx0, ly0, lx1, ly1, 8)
    over = cv2.dilate(overlay_mask(R, board).astype(np.uint8), disk(4)).astype(bool)

    # lasagna top: overprint and the part beyond the page grown from the lasagna itself
    las_src = las & inpage & ~over & ~cv2.dilate((~las).astype(np.uint8), disk(3)).astype(bool)
    ys_ = np.arange(OH)
    mid = int(round(ly0 + ly1))
    top = R.copy()
    need = las & ~las_src
    flip_y = np.clip(mid - ys_, 0, OH - 1)        # mirror across the slab's middle (long axis)
    mirrored = R[flip_y, :]
    use = need & las_src[flip_y, :]
    top[use] = mirrored[use]
    # feather the seam where the mirrored part meets the photographed one
    both = las_src & las_src[flip_y, :]
    a_ = cv2.GaussianBlur(use.astype(np.float32), (0, 0), 5)[..., None]
    top = np.where(both[..., None], R * (1 - a_) + mirrored * a_, top).astype(np.uint8)
    top = patch_fill(top, need & ~use, las_src, patch=28, seed=1)
    # board: mirrored across its long axis where the other side is visible, then exemplar fill
    bsrc = board & inpage & ~over & ~cv2.dilate(las.astype(np.uint8), disk(6)).astype(bool)
    btex = R.copy()
    need = board & ~bsrc
    xs = np.arange(OW)
    mx = np.clip(np.round(bx0 + bx1 - xs).astype(int), 0, OW - 1)
    mirror_ok = bsrc[:, mx]
    use = need & mirror_ok
    btex[use] = R[:, mx][use]
    need &= ~use
    # the far end of the board: mirror of the near end (its frame lines), but not the flour
    flour = (hsv(R)[:, :, 1] < 45) & (hsv(R)[:, :, 2] > 190)
    my_ = np.clip(int(round(by0 + by1)) - np.arange(OH), 0, OH - 1)
    src2 = bsrc & ~cv2.dilate(flour.astype(np.uint8), disk(8)).astype(bool)
    use = need & src2[my_, :] & ~las
    btex[use] = R[my_, :][use]
    need &= ~use
    btex = patch_fill(btex, need & ~las, bsrc & ~flour, patch=28, seed=2)

    # heights from depth (overprint and off-page excluded, then smoothly extended)
    ring = board & dv & ~over & ~cv2.dilate(las.astype(np.uint8), disk(10)).astype(bool)
    d_board = float(np.median(RD[ring]))
    interior = cv2.erode(las.astype(np.uint8), disk(25)).astype(bool) & dv & ~over
    k = spec["height_m"] / (float(np.median(RD[interior])) - d_board)
    hv = las & dv & ~over
    Hm = np.clip((RD - d_board) * k, spec["height_m"] * 0.7, spec["height_m"] * 1.2).astype(np.float32)
    Hm = np.where(hv, Hm, lowpass_fill(Hm, hv, 6))

    cx, cy = (lx0 + lx1) / 2, (ly0 + ly1) / 2
    th, r_th = mask_outline(las, (cx, cy), sigma=1.0)
    origin = ((bx0 + bx1) / 2, (by0 + by1) / 2)
    # side texture from the photographed bolognese band along the lasagna's left edge
    sx0, sy0, sw, sh = spec["side_strip"]
    strip = cv2.rotate(top[sy0:sy0 + sh, sx0:sx0 + sw], cv2.ROTATE_90_CLOCKWISE)
    cheese = np.median(top[interior], axis=0)
    side = side_band_texture(strip, cheese * 0.92, cheese * 1.03, int(spec["height_m"] / s))

    def height(X, Y, e):
        return sample(Hm, X, Y)

    part = relief_part(slug, "lasagna", top, (cx, cy), th, r_th, s, origin, height, size=1024,
                       side_band_m=0.065, mirror_px=0.004 / s, rim_peak_m=0.004, edge_drop=0.1, tuck=0.995,
                       roughness=0.5, target_tris=16000, toast=0.0, side_texture=(side, spec["height_m"]))
    # board
    size = 1024
    crop = btex[int(by0) - 4:int(by1) + 4, int(bx0) - 4:int(bx1) + 4]
    tex = cv2.resize(crop, (512, 1024), interpolation=cv2.INTER_AREA)
    save_jpg(os.path.join(out_dir(slug), "board.jpg"), tex, 88)
    a = to_model((bx0 - 4, by1 + 4), origin, s)
    pts = [to_model((x, y), origin, s) for x, y in rounded_rect_points(bx0, by0, bx1, by1, 14)]
    parts = [{
        "type": "board", "name": "board", "outline_m": pts, "hole_m": None, "thickness_m": 0.018,
        "z_top_m": 0.0, "albedo": "board.jpg",
        "uv_rect_m": a + [round((bx1 - bx0 + 8) * s, 5), round((by1 - by0 + 8) * s, 5)], "roughness": 0.55,
    }, part]
    if DEBUG:
        vis = R.copy()
        cv2.rectangle(vis, (int(bx0), int(by0)), (int(bx1), int(by1)), (0, 0, 255), 2)
        cv2.rectangle(vis, (int(lx0), int(ly0)), (int(lx1), int(ly1)), (255, 0, 0), 2)
        cv2.rectangle(vis, (sx0, sy0), (sx0 + sw, sy0 + sh), (0, 255, 0), 2)
        y_a, y_b, x_a, x_b = int(by0) - 20, int(by1) + 20, int(bx0) - 20, int(bx1) + 20
        debug(f"{slug}_rect.jpg", np.hstack([vis[y_a:y_b, x_a:x_b], btex[y_a:y_b, x_a:x_b], top[y_a:y_b, x_a:x_b]]))
        debug(f"{slug}_side.jpg", side)
    write_parts(slug, parts, s)


def rounded_rect_points(x0, y0, x1, y1, r, n=8):
    """Clockwise in image space (= counter-clockwise once y is flipped to model space)."""
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for i in range(n + 1):
            a = math.radians(a0 + 90 * i / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


# ---------------------------------------------------------------- shared
def product_photo(slug, img, x, y, size):
    x0, y0 = max(0, int(x)), max(0, int(y))
    size = int(min(size, img.shape[1] - x0, img.shape[0] - y0))
    crop = img[y0:y0 + size, x0:x0 + size]
    os.makedirs(PHOTOS, exist_ok=True)
    save_jpg(os.path.join(PHOTOS, f"{slug}.jpg"), cv2.resize(crop, (800, 800), interpolation=cv2.INTER_AREA), 84)


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


def main(only):
    for slug, spec in cfg["pizzas"].items():
        if not only or slug in only:
            do_pizza(slug, spec)
            print("pizza", slug, flush=True)
    for slug, spec in cfg["dishes"].items():
        if slug.startswith("_") or (only and slug not in only) or slug not in DISHES:
            continue
        DISHES[slug](slug, spec)
        print("dish", slug, flush=True)
    for slug, spec in cfg["productPhotos"].items():
        if not slug.startswith("_") and (not only or slug in only):
            name, x, y, size = spec
            product_photo(slug, load(name), x, y, size)
    if not only:
        brand_assets()


DISHES = {"fries": do_fries, "garlic-bread": do_garlic_bread, "salad-plate": do_salad, "lasagna": do_lasagna}

if __name__ == "__main__":
    main(sys.argv[1:])
