"""Raster/vector image -> clean 2D polygons (for emboss/engrave) and image loading helpers."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union


def load_rgba(path: str, max_side: int = 2048) -> Image.Image:
    p = Path(path)
    if p.suffix.lower() == ".svg":
        import cairosvg

        png = cairosvg.svg2png(url=str(p), output_width=1024)
        img = Image.open(io.BytesIO(png))
    else:
        img = Image.open(p)
    img = img.convert("RGBA")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    return img


def _otsu(gray: np.ndarray) -> float:
    hist, _ = np.histogram(gray, bins=256, range=(0, 256))
    total = gray.size
    sum_total = np.dot(np.arange(256), hist)
    w_b = sum_b = 0.0
    best, thr = 0.0, 127.0
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b, m_f = sum_b / w_b, (sum_total - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, thr = between, t
    return thr


def image_mask(path: str, resolution: int = 180, threshold: float | None = None, invert: bool = False) -> np.ndarray:
    """Boolean mask (True = logo ink). Uses alpha if the image is transparent, else Otsu on luminance
    assuming dark ink on a light background (invert=True for light-on-dark)."""
    img = load_rgba(path)
    img.thumbnail((resolution, resolution), Image.LANCZOS)
    arr = np.asarray(img).astype(np.float32)
    alpha = arr[..., 3]
    if alpha.min() < 250 and (alpha < 128).mean() > 0.02:
        mask = alpha >= 128
    else:
        gray = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
        thr = threshold if threshold is not None else _otsu(gray)
        mask = gray < thr
    if invert:
        mask = ~mask
    return mask


def mask_to_polygons(mask: np.ndarray, simplify: float = 0.6) -> list[Polygon]:
    """Vectorise a mask: union of horizontal pixel runs, then simplify. Coordinates in pixels with
    origin at image centre, y up."""
    h, w = mask.shape
    rects = []
    for y in range(h):
        row = mask[y]
        if not row.any():
            continue
        padded = np.concatenate([[False], row, [False]])
        d = np.diff(padded.astype(np.int8))
        starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
        for s, e in zip(starts, ends):
            rects.append(box(s, h - y - 1, e, h - y))
    if not rects:
        return []
    geom = unary_union(rects).simplify(simplify, preserve_topology=True)
    polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    out = []
    for p in polys:
        if p.is_empty or p.area < 4:  # drop specks
            continue
        p = Polygon([(x - w / 2, y - h / 2) for x, y in p.exterior.coords],
                    [[(x - w / 2, y - h / 2) for x, y in i.coords] for i in p.interiors if Polygon(i).area >= 2])
        out.append(p.buffer(0))
    return out


def image_polygons_mm(path: str, width_mm: float, resolution: int = 180, threshold: float | None = None,
                      invert: bool = False) -> list[Polygon]:
    mask = image_mask(path, resolution, threshold, invert)
    polys = mask_to_polygons(mask)
    if not polys:
        raise ValueError("No shapes could be extracted from the image (try invert=true or another threshold).")
    u = unary_union(polys)
    minx, miny, maxx, maxy = u.bounds
    scale = width_mm / max(maxx - minx, 1e-6)
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    from shapely import affinity

    res = []
    for p in polys:
        p = affinity.translate(p, -cx, -cy)
        res.append(affinity.scale(p, scale, scale, origin=(0, 0)))
    return res


def dominant_colors(path: str, k: int = 5) -> list[str]:
    """Simple k-means palette extraction (used to colour game assets from reference images)."""
    img = load_rgba(path, 256)
    arr = np.asarray(img).reshape(-1, 4)
    arr = arr[arr[:, 3] > 128][:, :3].astype(np.float32)
    if len(arr) == 0:
        return []
    rng = np.random.default_rng(0)
    sample = arr[rng.choice(len(arr), min(4000, len(arr)), replace=False)]
    cent = sample[rng.choice(len(sample), k, replace=False)]
    for _ in range(12):
        d = ((sample[:, None, :] - cent[None]) ** 2).sum(-1)
        lab = d.argmin(1)
        for i in range(k):
            if (lab == i).any():
                cent[i] = sample[lab == i].mean(0)
    counts = np.bincount(lab, minlength=k)
    order = np.argsort(-counts)
    return ["#%02x%02x%02x" % tuple(int(c) for c in cent[i]) for i in order]
