"""Mesh generators with proper UVs and smooth normals (Y-up, metres — glTF convention)."""
from __future__ import annotations

import math

import numpy as np
import trimesh


def _mesh(v, f, uv, smooth=True) -> trimesh.Trimesh:
    m = trimesh.Trimesh(np.asarray(v, float), np.asarray(f, np.int64), process=False)
    m.visual = trimesh.visual.TextureVisuals(uv=np.asarray(uv, float))
    if not smooth:
        m.vertex_normals  # noqa: B018  (trimesh caches normals)
    return m


def grid_surface(P: np.ndarray, close_u: bool, uv_scale=(1.0, 1.0)) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """P: (nv, nu, 3) grid of points. Returns verts, faces, uvs (u wraps by duplicating the seam)."""
    nv, nu, _ = P.shape
    if close_u:
        P = np.concatenate([P, P[:, :1]], axis=1)
        nu += 1
    verts = P.reshape(-1, 3)
    # arc-length based UVs for less stretching
    du = np.linalg.norm(np.diff(P, axis=1), axis=2).mean(0)
    dv = np.linalg.norm(np.diff(P, axis=0), axis=2).mean(1)
    u = np.concatenate([[0], np.cumsum(du)]) * uv_scale[0]
    v = np.concatenate([[0], np.cumsum(dv)]) * uv_scale[1]
    U, V = np.meshgrid(u, v)
    uvs = np.stack([U.ravel(), V.ravel()], 1)
    idx = np.arange(nv * nu).reshape(nv, nu)
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]
    faces = np.concatenate([np.stack([a, b, c], -1).reshape(-1, 3), np.stack([a, c, d], -1).reshape(-1, 3)])
    return verts, faces, uvs


def _cap(ring: np.ndarray, center: np.ndarray, flip: bool, v_off: int):
    """Fan cap with planar UVs. ring: (n,3) closed ring (no duplicate)."""
    n = len(ring)
    verts = np.vstack([ring, center[None]])
    ext = ring - center
    r = np.abs(ext).max() + 1e-9
    # project onto the two dominant axes
    ax = np.argsort(np.ptp(ring, axis=0))[-2:]
    uv = np.vstack([(ext[:, ax] / (2 * r)) + 0.5, [[0.5, 0.5]]])
    faces = [[i, (i + 1) % n, n] for i in range(n)]
    faces = np.array(faces) + v_off
    if flip:
        faces = faces[:, ::-1]
    return verts, faces, uv


def orient(m: trimesh.Trimesh) -> trimesh.Trimesh:
    """Make winding consistent and normals point outward without destroying UV seams."""
    mm = trimesh.Trimesh(m.vertices.copy(), m.faces.copy(), process=False)
    mm.merge_vertices(merge_tex=True, merge_norm=True)
    if mm.is_watertight:
        trimesh.repair.fix_normals(mm)
        flip = (np.einsum("ij,ij->i", mm.face_normals, m.face_normals) < 0)
    else:
        trimesh.repair.fix_winding(mm)
        flip = (np.einsum("ij,ij->i", mm.face_normals, m.face_normals) < 0)
        out_dir = m.triangles_center - m.vertices.mean(0)
        sgn = np.where(flip, -1, 1)[:, None] * m.face_normals
        if np.einsum("ij,ij->i", sgn, out_dir).sum() < 0:
            flip = ~flip
    if flip.any():
        f = m.faces.copy()
        f[flip] = f[flip][:, ::-1]
        uv = m.visual.uv if hasattr(m.visual, "uv") else None
        m = trimesh.Trimesh(m.vertices, f, process=False)
        if uv is not None:
            m.visual = trimesh.visual.TextureVisuals(uv=uv)
    return m


def watertight_copy(m: trimesh.Trimesh) -> trimesh.Trimesh:
    mm = trimesh.Trimesh(m.vertices.copy(), m.faces.copy(), process=False)
    mm.merge_vertices(merge_tex=True, merge_norm=True)
    mm.update_faces(mm.nondegenerate_faces())
    mm.remove_unreferenced_vertices()
    trimesh.repair.fix_normals(mm)
    return mm


def combine(parts) -> trimesh.Trimesh:
    vs, fs, us, off = [], [], [], 0
    for v, f, u in parts:
        vs.append(v)
        fs.append(np.asarray(f) + off)
        us.append(u)
        off += len(v)
    return orient(_mesh(np.vstack(vs), np.vstack(fs), np.vstack(us)))


# ---------------------------------------------------------------- primitives
def box(w, h, d) -> trimesh.Trimesh:
    return rounded_box(w, h, d, 0.0, 1)


def rounded_box(w, h, d, r, seg=4) -> trimesh.Trimesh:
    """Rounded box via 'inner box + radius' mapping of a subdivided cube. Per-face UVs in metres."""
    half = np.array([w, h, d]) / 2
    r = float(min(r, *(half - 1e-4))) if r > 0 else 0.0
    n = max(2, seg * 2) if r > 0 else 1
    parts = []
    off = 0
    vs, fs, us = [], [], []
    for axis in range(3):
        for sign in (-1, 1):
            a1, a2 = [i for i in range(3) if i != axis]
            t = np.linspace(-1, 1, n + 1)
            A, B = np.meshgrid(t, t)
            P = np.zeros(A.shape + (3,))
            P[..., axis] = sign
            P[..., a1] = A
            P[..., a2] = B
            pts = P.reshape(-1, 3) * half
            if r > 0:
                inner = np.clip(pts, -(half - r), half - r)
                d_ = pts - inner
                ln = np.linalg.norm(d_, axis=1, keepdims=True)
                ln[ln == 0] = 1
                pts = inner + d_ / ln * r
            uv = np.stack([A.ravel() * half[a1], B.ravel() * half[a2]], 1)
            idx = np.arange((n + 1) ** 2).reshape(n + 1, n + 1)
            a, b, c, dd = idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]
            f = np.concatenate([np.stack([a, b, c], -1).reshape(-1, 3), np.stack([a, c, dd], -1).reshape(-1, 3)])
            # orientation: normal must point to +sign*axis
            nrm = np.cross(pts[f[0, 1]] - pts[f[0, 0]], pts[f[0, 2]] - pts[f[0, 0]])
            if nrm[axis] * sign < 0:
                f = f[:, ::-1]
            vs.append(pts)
            fs.append(f + off)
            us.append(uv)
            off += len(pts)
    m = _mesh(np.vstack(vs), np.vstack(fs), np.vstack(us))
    if r > 0:
        m = _merge_keep_uv(m)
    return m


def _merge_keep_uv(m: trimesh.Trimesh) -> trimesh.Trimesh:
    """Smooth normals across seams of the rounded box while keeping per-face UVs."""
    v = m.vertices
    key = np.round(v / 1e-6).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    tmp = trimesh.Trimesh(v, m.faces, process=False)
    vn = tmp.vertex_normals
    acc = np.zeros((inv.max() + 1, 3))
    np.add.at(acc, inv, vn)
    acc /= np.linalg.norm(acc, axis=1, keepdims=True) + 1e-12
    out = trimesh.Trimesh(v, m.faces, process=False)
    out.visual = m.visual
    out.vertex_normals = acc[inv]
    return out


def lathe(profile, segments=48, angle=360.0, cap=True) -> trimesh.Trimesh:
    """Revolve [(radius, y), ...] around the Y axis."""
    prof = np.asarray(profile, float)
    full = abs(angle - 360) < 1e-6
    n = segments
    th = np.linspace(0, math.radians(angle), n, endpoint=not full)
    P = np.zeros((len(prof), n, 3))
    P[..., 0] = prof[:, :1] * np.cos(th)[None]
    P[..., 2] = -prof[:, :1] * np.sin(th)[None]
    P[..., 1] = prof[:, 1:2]
    v, f, uv = grid_surface(P, close_u=full)
    m_parts = [(v, f[:, ::-1], uv)]
    off = len(v)
    if cap and full:
        for row, flip in ((0, False), (len(prof) - 1, True)):
            if prof[row, 0] > 1e-6:
                ring = P[row]
                cv, cf, cu = _cap(ring, np.array([0, prof[row, 1], 0]), flip, off)
                m_parts.append((cv, cf - off, cu))
                off += len(cv)
    return combine(m_parts)


def cylinder(radius, height, segments=32, radius_top=None) -> trimesh.Trimesh:
    rt = radius if radius_top is None else radius_top
    h = height / 2
    return lathe([(radius, -h), (rt, h)], segments)


def cone(radius, height, segments=32) -> trimesh.Trimesh:
    return cylinder(radius, height, segments, radius_top=1e-4)


def sphere(radius, segments=32, rings=20) -> trimesh.Trimesh:
    t = np.linspace(-math.pi / 2, math.pi / 2, rings + 1)
    prof = np.stack([np.cos(t) * radius, np.sin(t) * radius], 1)
    prof[0, 0] = prof[-1, 0] = 0.0
    return lathe(prof, segments, cap=False)


def capsule(radius, length, segments=24, rings=8) -> trimesh.Trimesh:
    t = np.linspace(-math.pi / 2, 0, rings + 1)
    bottom = np.stack([np.cos(t) * radius, np.sin(t) * radius - length / 2], 1)
    top = np.stack([np.cos(-t[::-1]) * radius, np.sin(-t[::-1]) * radius + length / 2], 1)
    prof = np.vstack([bottom, top])
    prof[0, 0] = prof[-1, 0] = 0.0
    return lathe(prof, segments, cap=False)


def torus(major, minor, segments=48, tube_segments=16) -> trimesh.Trimesh:
    u = np.linspace(0, 2 * math.pi, segments, endpoint=False)
    v = np.linspace(0, 2 * math.pi, tube_segments, endpoint=False)
    V, U = np.meshgrid(v, u)  # rows = around ring
    P = np.stack([(major + minor * np.cos(V)) * np.cos(U), minor * np.sin(V), (major + minor * np.cos(V)) * np.sin(U)], -1)
    P = np.concatenate([P, P[:1]], 0)  # close rows
    v_, f, uv = grid_surface(P, close_u=True)
    return orient(_mesh(v_, f, uv))


def superellipse_ring(w, h, n_top, n_bottom, segments) -> np.ndarray:
    """Points (y,z) of a superellipse cross-section, half-width w/2, half-height h/2."""
    t = np.linspace(0, 2 * math.pi, segments, endpoint=False)
    c, s = np.cos(t), np.sin(t)
    exps = np.where(s >= 0, n_top, n_bottom)
    y = np.sign(c) * np.abs(c) ** (2 / exps) * w / 2
    z = np.sign(s) * np.abs(s) ** (2 / exps) * h / 2
    return np.stack([y, z], 1)


def loft(sections, segments=48, cap=True, smooth_steps=3) -> trimesh.Trimesh:
    """Loft superellipse sections along X. Each section: {x, width, height, y (centre height),
    exp_top, exp_bottom}. Sections are interpolated with Catmull-Rom for smooth surfaces."""
    secs = sorted(sections, key=lambda s: s["x"])
    keys = ["x", "width", "height", "y", "exp_top", "exp_bottom", "z"]
    arr = np.array([[float(s.get(k, d)) for k, d in zip(keys, [0, 1, 1, 0, 2.5, 2.5, 0])] for s in secs])
    if len(arr) < 2:
        raise ValueError("loft needs ≥2 sections")
    # Catmull-Rom densify
    dense = []
    pts = np.vstack([arr[:1], arr, arr[-1:]])
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for t in np.linspace(0, 1, smooth_steps, endpoint=False):
            t2, t3 = t * t, t * t * t
            dense.append(0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    dense.append(arr[-1])
    dense = np.array(dense)
    dense[:, 1:3] = np.maximum(dense[:, 1:3], 1e-4)
    rings = []
    for x, w, h, yc, et, eb, zc in dense:
        yz = superellipse_ring(w, h, max(et, 0.5), max(eb, 0.5), segments)
        rings.append(np.stack([np.full(segments, x), yz[:, 1] + yc, yz[:, 0] + zc], 1))
    P = np.array(rings)  # (nsec, seg, 3)
    v, f, uv = grid_surface(P, close_u=True)
    parts = [(v, f, uv)]
    off = len(v)
    if cap:
        for row, flip in ((0, True), (len(P) - 1, False)):
            cv, cf, cu = _cap(P[row], P[row].mean(0), flip, off)
            parts.append((cv, cf - off, cu))
            off += len(cv)
    return combine(parts)


def extrude_polygon(points, depth, bevel=0.0) -> trimesh.Trimesh:
    """Extrude a 2D polygon (x, y) along +Z (depth), planar UVs."""
    from shapely.geometry import Polygon

    poly = Polygon(points)
    if not poly.is_valid:
        poly = poly.buffer(0)
    m = trimesh.creation.extrude_polygon(poly, depth)
    return uv_project(m, "box")


def tube(path, radius, segments=12, closed=False) -> trimesh.Trimesh:
    """Sweep a circle along a 3D polyline (parallel-transport frames)."""
    P = np.asarray(path, float)
    if closed:
        P = np.vstack([P, P[:1]])
    T = np.gradient(P, axis=0)
    T /= np.linalg.norm(T, axis=1, keepdims=True) + 1e-12
    ref = np.array([0, 1, 0]) if abs(T[0, 1]) < 0.9 else np.array([1, 0, 0])
    N = np.cross(T[0], ref)
    N /= np.linalg.norm(N)
    rings = []
    th = np.linspace(0, 2 * math.pi, segments, endpoint=False)
    for i in range(len(P)):
        if i:
            N = N - T[i] * np.dot(N, T[i])
            N /= np.linalg.norm(N) + 1e-12
        B = np.cross(T[i], N)
        rings.append(P[i] + radius * (np.cos(th)[:, None] * N + np.sin(th)[:, None] * B))
    v, f, uv = grid_surface(np.array(rings), close_u=True)
    parts = [(v, f[:, ::-1], uv)]
    if not closed:
        off = len(v)
        for row, flip in ((0, False), (len(rings) - 1, True)):
            cv, cf, cu = _cap(rings[row], P[row], not flip, off)
            parts.append((cv, cf - off, cu))
            off += len(cv)
    return combine(parts)


def plane(w, d, subdivisions=1, height_fn=None) -> trimesh.Trimesh:
    n = max(1, subdivisions)
    x = np.linspace(-w / 2, w / 2, n + 1)
    z = np.linspace(-d / 2, d / 2, n + 1)
    X, Z = np.meshgrid(x, z)
    Y = height_fn(X, Z) if height_fn else np.zeros_like(X)
    P = np.stack([X, Y, Z], -1)
    v, f, uv = grid_surface(P, close_u=False)
    m = _mesh(v, f, uv)
    if m.face_normals[:, 1].mean() < 0:
        m = _mesh(v, f[:, ::-1], uv)
    return m


def wedge(w, h, d) -> trimesh.Trimesh:
    m = trimesh.Trimesh([[-w / 2, 0, -d / 2], [w / 2, 0, -d / 2], [w / 2, 0, d / 2], [-w / 2, 0, d / 2],
                         [-w / 2, h, -d / 2], [-w / 2, h, d / 2]],
                        [[0, 1, 2], [0, 2, 3], [0, 4, 1], [3, 2, 5], [0, 3, 5], [0, 5, 4], [1, 4, 5], [1, 5, 2]],
                        process=False)
    m.fix_normals()
    m = m.subdivide()
    m = trimesh.Trimesh(m.vertices[m.faces].reshape(-1, 3), np.arange(len(m.faces) * 3).reshape(-1, 3), process=False)
    return uv_project(m, "box")


# ---------------------------------------------------------------- UV utilities
def uv_project(m: trimesh.Trimesh, method: str = "box", scale: float = 1.0) -> trimesh.Trimesh:
    """Real UV generation by projection: box (tri-planar per face), cylindrical or spherical."""
    v = m.vertices
    if method == "box":
        # split vertices per face so each face gets its own dominant-axis projection
        fv = v[m.faces].reshape(-1, 3)
        fn = np.repeat(m.face_normals, 3, axis=0)
        ax = np.abs(fn).argmax(1)
        uv = np.where(ax[:, None] == 0, fv[:, [2, 1]], np.where(ax[:, None] == 1, fv[:, [0, 2]], fv[:, [0, 1]]))
        out = trimesh.Trimesh(fv, np.arange(len(fv)).reshape(-1, 3), process=False)
        # smooth normals from the original topology
        vn = m.vertex_normals[m.faces].reshape(-1, 3)
        out = trimesh.Trimesh(fv, np.arange(len(fv)).reshape(-1, 3), process=False)
        out.visual = trimesh.visual.TextureVisuals(uv=uv * scale)
        out.vertex_normals = vn
        return out
    c = v.mean(0)
    d = v - c
    if method == "cylindrical":
        u = (np.arctan2(d[:, 2], d[:, 0]) / (2 * math.pi) + 0.5) * scale * 2 * math.pi * np.abs(d[:, [0, 2]]).max()
        uv = np.stack([u, d[:, 1] * scale], 1)
    elif method == "spherical":
        r = np.linalg.norm(d, axis=1) + 1e-12
        uv = np.stack([np.arctan2(d[:, 2], d[:, 0]) / (2 * math.pi) + 0.5, np.arccos(np.clip(d[:, 1] / r, -1, 1)) / math.pi], 1) * scale
    else:
        raise ValueError("method must be box|cylindrical|spherical")
    m2 = m.copy()
    m2.visual = trimesh.visual.TextureVisuals(uv=uv)
    return m2
