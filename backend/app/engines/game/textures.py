"""Procedural PBR texture generator (numpy). Produces tileable baseColor, normal, and packed
occlusion/roughness/metallic maps (glTF ORM layout: R=AO, G=roughness, B=metallic)."""
from __future__ import annotations

import functools
import math

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter


def as_jpeg(img: Image.Image | None, quality: int = 88) -> Image.Image | None:
    """Re-open as a JPEG-backed image: glTF exporters embed it without PNG re-encoding (5-10x smaller)."""
    if img is None:
        return None
    import io

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    buf.seek(0)
    out = Image.open(buf)
    out.load()
    return out


def hex_rgb(h: str) -> np.ndarray:
    h = (h or "#808080").lstrip("#")
    if len(h) != 6:
        h = "808080"
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255.0


def _value_noise(n: int, freq: int, rng: np.random.Generator) -> np.ndarray:
    g = rng.random((freq, freq)).astype(np.float32)
    g = np.pad(g, 2, mode="wrap")
    img = Image.fromarray(g, mode="F").resize(((freq + 4) * n // freq, (freq + 4) * n // freq), Image.BICUBIC)
    a = np.asarray(img)
    s = 2 * n // freq
    return a[s:s + n, s:s + n]


def fbm(n: int, base: int = 4, octaves: int = 5, seed: int = 0, stretch: tuple[int, int] = (1, 1)) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.zeros((n, n), np.float32)
    amp, tot = 1.0, 0.0
    f = base
    for _ in range(octaves):
        if f > n:
            break
        layer = _value_noise(n, f, rng)
        if stretch != (1, 1):
            sy, sx = stretch
            layer = np.asarray(Image.fromarray(layer, mode="F").resize((n * sx, n * sy), Image.BICUBIC))[:n, :n]
        out += layer * amp
        tot += amp
        amp *= 0.5
        f *= 2
    out /= tot
    return (out - out.min()) / (np.ptp(out) + 1e-6)


def worley(n: int, cells: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    pts = rng.random((cells * cells, 2)) * n
    # tile points for seamless wrap
    offs = np.array([[dx, dy] for dx in (-n, 0, n) for dy in (-n, 0, n)])
    allp = (pts[None] + offs[:, None]).reshape(-1, 2)
    step = max(1, n // 128)
    ys, xs = np.mgrid[0:n:step, 0:n:step]
    grid = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float32)
    from scipy.spatial import cKDTree

    d, _ = cKDTree(allp).query(grid, k=1)
    d = d.reshape(ys.shape)
    img = Image.fromarray(d.astype(np.float32), mode="F").resize((n, n), Image.BILINEAR)
    a = np.asarray(img)
    return a / (a.max() + 1e-6)


def normal_from_height(h: np.ndarray, strength: float) -> np.ndarray:
    dx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * strength
    dy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * strength
    n = np.stack([-dx, dy, np.ones_like(h)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return (n * 0.5 + 0.5)


def ao_from_height(h: np.ndarray, strength: float = 1.0) -> np.ndarray:
    blur = gaussian_filter(h.astype(np.float32), 4, mode="wrap")
    cav = np.clip((blur - h) * 4 * strength, 0, 1)
    return 1 - cav * 0.8


# preset -> defaults (roughness, metallic, normal strength, tile size m, pattern)
PRESETS: dict[str, dict] = {
    "plastic": dict(roughness=0.45, metallic=0.0, normal=0.6, tile=0.5, pattern="fine"),
    "car_paint": dict(roughness=0.18, metallic=0.45, normal=0.12, tile=0.08, pattern="flakes"),
    "chrome": dict(roughness=0.06, metallic=1.0, normal=0.0, tile=1.0, pattern="none", color="#e8ecf0"),
    "metal_brushed": dict(roughness=0.32, metallic=1.0, normal=0.5, tile=0.4, pattern="brushed", color="#a9afb6"),
    "metal_painted": dict(roughness=0.5, metallic=0.3, normal=1.0, tile=0.6, pattern="worn"),
    "rubber": dict(roughness=0.88, metallic=0.0, normal=0.8, tile=0.3, pattern="fine", color="#1b1b1d"),
    "tire": dict(roughness=0.9, metallic=0.0, normal=4.0, tile=0.25, pattern="tread", color="#151517"),
    "carbon_fiber": dict(roughness=0.28, metallic=0.25, normal=2.0, tile=0.08, pattern="carbon", color="#1a1c20"),
    "glass": dict(roughness=0.04, metallic=0.1, normal=0.0, tile=1.0, pattern="none", opacity=0.32, color="#9fb7c9"),
    "leather": dict(roughness=0.62, metallic=0.0, normal=2.5, tile=0.15, pattern="leather", color="#3b2a22"),
    "fabric": dict(roughness=0.92, metallic=0.0, normal=2.5, tile=0.06, pattern="weave", color="#556070"),
    "wood": dict(roughness=0.6, metallic=0.0, normal=1.2, tile=0.8, pattern="wood", color="#8a5a33"),
    "wood_planks": dict(roughness=0.65, metallic=0.0, normal=2.0, tile=1.2, pattern="planks", color="#8f6038"),
    "brick": dict(roughness=0.85, metallic=0.0, normal=4.0, tile=1.0, pattern="brick", color="#9b4a35"),
    "concrete": dict(roughness=0.9, metallic=0.0, normal=1.5, tile=1.5, pattern="concrete", color="#9a9a96"),
    "stone": dict(roughness=0.85, metallic=0.0, normal=4.0, tile=1.5, pattern="stone", color="#7d7b76"),
    "asphalt": dict(roughness=0.95, metallic=0.0, normal=2.0, tile=1.0, pattern="concrete", color="#2e2f31"),
    "grass": dict(roughness=0.95, metallic=0.0, normal=2.0, tile=2.0, pattern="grass", color="#4a7a32"),
    "bark": dict(roughness=0.95, metallic=0.0, normal=5.0, tile=0.6, pattern="bark", color="#4d3727"),
    "foliage": dict(roughness=0.8, metallic=0.0, normal=2.0, tile=0.6, pattern="grass", color="#3f7d2c"),
    "skin": dict(roughness=0.55, metallic=0.0, normal=0.3, tile=0.3, pattern="fine", color="#e0ac8a"),
    "tiles": dict(roughness=0.3, metallic=0.0, normal=3.0, tile=1.0, pattern="tiles", color="#d8d4cc"),
    "emissive": dict(roughness=0.3, metallic=0.0, normal=0.0, tile=1.0, pattern="none", emissive=3.0),
    "rust": dict(roughness=0.9, metallic=0.4, normal=3.0, tile=0.8, pattern="worn", color="#7a3f22"),
    "gold": dict(roughness=0.18, metallic=1.0, normal=0.2, tile=1.0, pattern="fine", color="#d4a93a"),
}


def _pattern(kind: str, n: int, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """returns height [0,1], albedo multiplier, roughness offset"""
    one = np.ones((n, n), np.float32)
    zero = np.zeros((n, n), np.float32)
    if kind == "none":
        return zero, one, zero
    if kind == "fine":
        h = fbm(n, 16, 4, seed)
        return h, 0.96 + 0.08 * fbm(n, 4, 4, seed + 1), (h - 0.5) * 0.08
    if kind == "flakes":
        rng = np.random.default_rng(seed)
        fl = rng.random((n, n)).astype(np.float32)
        h = fbm(n, 8, 3, seed) * 0.3 + fl * 0.7
        # metallic flakes live in the normal/roughness channels; albedo stays smooth like a real clear-coat
        return h, 0.99 + 0.02 * fbm(n, 4, 3, seed + 1), (fl - 0.5) * 0.06
    if kind == "brushed":
        rng = np.random.default_rng(seed)
        streak = np.asarray(Image.fromarray(rng.random((n, 8)).astype(np.float32), mode="F").resize((n, n), Image.BILINEAR))
        line = np.repeat(rng.random((n, 1)).astype(np.float32), n, 1)
        h = 0.7 * line + 0.3 * streak.T[:n, :n]
        return h, 0.92 + 0.12 * h, (h - 0.5) * 0.15
    if kind == "worn":
        h = fbm(n, 4, 6, seed)
        scratches = (fbm(n, 32, 2, seed + 5, (1, 8)) > 0.78).astype(np.float32)
        return h * 0.5 + scratches * 0.5, 0.85 + 0.2 * h - 0.25 * scratches, scratches * 0.3 + (h - 0.5) * 0.2
    if kind == "tread":
        y, x = np.mgrid[0:n, 0:n] / n
        chev = np.abs(((x * 6) % 1.0) - 0.5) * 0.6 + y * 4
        groove = ((chev % 1.0) < 0.22).astype(np.float32)
        center = (np.abs(x - 0.5) < 0.03).astype(np.float32)
        h = 1 - np.clip(groove + center, 0, 1)
        h = gaussian_filter(h.astype(np.float32), 1.5, mode="wrap")
        return h, 0.85 + 0.2 * h, (1 - h) * 0.05
    if kind == "carbon":
        y, x = np.mgrid[0:n, 0:n] / n * 16 * math.pi
        a = (np.sin(x) * 0.5 + 0.5)
        b = (np.sin(y) * 0.5 + 0.5)
        check = ((np.floor(x / math.pi) + np.floor(y / math.pi)) % 2).astype(np.float32)
        h = np.where(check > 0, a, b)
        return h, 0.6 + 0.8 * h, (h - 0.5) * 0.1
    if kind == "leather":
        w = worley(n, 40, seed)
        h = 1 - np.clip(w * 1.6, 0, 1) + fbm(n, 32, 3, seed) * 0.2
        return h, 0.9 + 0.15 * h, (0.5 - h) * 0.2
    if kind == "weave":
        y, x = np.mgrid[0:n, 0:n] / n * 64 * math.pi
        h = (np.sin(x) * np.sin(y) * 0.5 + 0.5) * 0.8 + fbm(n, 32, 2, seed) * 0.2
        return h, 0.88 + 0.2 * h, zero
    if kind in ("wood", "planks"):
        y, x = np.mgrid[0:n, 0:n] / n
        warp = fbm(n, 4, 4, seed) * 2.5
        rings = np.sin((y * 14 + warp) * math.pi * 2) * 0.5 + 0.5
        grain = fbm(n, 64, 2, seed + 3, (1, 6))
        h = rings * 0.6 + grain * 0.4
        alb = 0.75 + 0.35 * h
        if kind == "planks":
            plank = (x * 5) % 1.0
            seam = ((plank < 0.02) | (plank > 0.98)).astype(np.float32)
            rng = np.random.default_rng(seed)
            tint = rng.uniform(0.8, 1.15, 5)[np.minimum((x * 5).astype(int), 4)]
            alb = alb * tint * (1 - seam * 0.6)
            h = h * (1 - seam)
        return h, alb, (0.5 - h) * 0.15
    if kind == "brick":
        y, x = np.mgrid[0:n, 0:n] / n
        rows = 8
        r = np.floor(y * rows)
        xs = x * 4 + (r % 2) * 0.5
        mortar = (((y * rows) % 1.0) < 0.08) | ((xs % 1.0) < 0.04)
        rng = np.random.default_rng(seed)
        tint = rng.uniform(0.75, 1.15, (rows, 9))[r.astype(int) % rows, np.floor(xs).astype(int) % 9]
        noise = fbm(n, 16, 4, seed)
        h = np.where(mortar, 0.0, 0.8 + 0.2 * noise)
        alb = np.where(mortar, 1.25, tint * (0.85 + 0.25 * noise))
        return h.astype(np.float32), alb.astype(np.float32), np.where(mortar, 0.1, 0).astype(np.float32)
    if kind == "tiles":
        y, x = np.mgrid[0:n, 0:n] / n
        grout = (((x * 4) % 1.0) < 0.03) | (((y * 4) % 1.0) < 0.03)
        noise = fbm(n, 8, 3, seed)
        h = np.where(grout, 0.0, 1.0)
        return h.astype(np.float32), np.where(grout, 0.55, 0.95 + 0.08 * noise).astype(np.float32), np.where(grout, 0.5, 0).astype(np.float32)
    if kind in ("concrete", "stone"):
        h = fbm(n, 4 if kind == "stone" else 8, 7, seed)
        if kind == "stone":
            h = h * 0.6 + (1 - worley(n, 6, seed)) * 0.4
        pores = (fbm(n, 64, 2, seed + 9) > 0.8).astype(np.float32)
        return h - pores * 0.3, 0.8 + 0.35 * h - pores * 0.2, (0.5 - h) * 0.1
    if kind == "grass":
        h = fbm(n, 32, 5, seed)
        return h, 0.7 + 0.5 * fbm(n, 4, 4, seed + 2), zero
    if kind == "bark":
        h = fbm(n, 16, 5, seed, (1, 1)) * 0.4 + fbm(n, 8, 3, seed + 1, (6, 1)) * 0.6
        return h, 0.7 + 0.5 * h, (0.5 - h) * 0.1
    return zero, one, zero


@functools.lru_cache(maxsize=96)
def generate(preset: str, color: str, res: int = 512, seed: int = 7, roughness: float | None = None,
             metallic: float | None = None) -> dict:
    p = PRESETS.get(preset, PRESETS["plastic"])
    base = hex_rgb(color or p.get("color", "#808080"))
    h, alb, rough_off = _pattern(p["pattern"], res, seed)
    rgb = np.clip(base[None, None] * alb[..., None], 0, 1)
    rough = np.clip((roughness if roughness is not None else p["roughness"]) + rough_off, 0.02, 1)
    metal = np.full_like(h, metallic if metallic is not None else p["metallic"])
    ao = ao_from_height(h) if p["normal"] > 0 else np.ones_like(h)
    nrm = normal_from_height(h, p["normal"]) if p["normal"] > 0 else None
    out = {
        "baseColor": as_jpeg(Image.fromarray((rgb * 255).astype(np.uint8), "RGB")),
        "orm": as_jpeg(Image.fromarray((np.stack([ao, rough, metal], -1) * 255).astype(np.uint8), "RGB"), 92),
        "normal": as_jpeg(Image.fromarray((nrm * 255).astype(np.uint8), "RGB"), 92) if nrm is not None else None,
        "roughness": float(rough.mean()), "metallic": float(metal.mean()),
        "opacity": float(p.get("opacity", 1.0)), "emissive": float(p.get("emissive", 0.0)), "tile": float(p["tile"]),
    }
    return out
