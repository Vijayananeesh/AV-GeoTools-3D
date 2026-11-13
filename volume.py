"""Volume of a plume above a guideline value, from the same grid that draws the plume shells."""
import numpy as np


def _refine(a, f):
    from scipy.ndimage import zoom
    return a if f <= 1 else zoom(np.asarray(a, dtype=float), f, order=1)


def plume_volume(arrays, guideline, lith=None, porosity=None, max_points=3_000_000):
    """arrays: (xs, ys, zs, conc[nz,ny,nx], allowed[nz,ny,nx] or None) as made by the plume grid.
    The cells counted are those with concentration >= guideline AND inside the limits used for drawing
    (allowed > 0: below the ground, above or below the water table, above the well bottoms, inside the hull).
    lith: (cs, intervals) in the same shifted coordinates, for a split by lithology unit (or None).
    Returns a dict with the total, the volume per unit, the highest value, and whether the plume touches the grid edge."""
    xs, ys, zs, conc, allowed = arrays
    xs, ys, zs = np.asarray(xs, float), np.asarray(ys, float), np.asarray(zs, float)
    nz, ny, nx = conc.shape
    f = int(max(1, min(4, (max_points / float(conc.size)) ** (1.0 / 3.0))))
    c = _refine(conc, f)
    a = _refine(allowed, f) if allowed is not None else None
    n2z, n2y, n2x = c.shape
    X = np.linspace(xs[0], xs[-1], n2x)
    Y = np.linspace(ys[0], ys[-1], n2y)
    Z = np.linspace(zs[0], zs[-1], n2z)
    cell = (X[1] - X[0]) * (Y[1] - Y[0]) * (Z[1] - Z[0])
    inside = c >= float(guideline)
    if a is not None:
        inside &= a > 0
    count = int(inside.sum())
    out = {"total": count * cell, "cells": count, "cell": cell, "refine": f, "units": [], "unit_note": "",
           "touches_edge": False, "max": float(c[inside].max()) if count else None}
    if count:
        edge = np.zeros_like(inside)
        edge[:, 0, :] = edge[:, -1, :] = True
        edge[:, :, 0] = edge[:, :, -1] = True
        out["touches_edge"] = bool((inside & edge).any())
        zi, yi, xi = np.nonzero(inside)
        out["footprint"] = float(len(set(zip(yi.tolist(), xi.tolist()))) * (X[1] - X[0]) * (Y[1] - Y[0]))
        out["z_range"] = (float(Z[zi].min()), float(Z[zi].max()))
    if porosity is not None:
        out["pore"] = out["total"] * float(porosity)
    if lith and count:
        try:
            out["units"], out["unit_note"] = _by_unit(inside, X, Y, Z, lith, cell)
        except Exception as e:                                   # the split is a bonus: never lose the total
            out["unit_note"] = "The split by lithology unit could not be made (%s)." % e
    return out


def _by_unit(inside, X, Y, Z, lith, cell):
    from .builders import _unit_tops, _interp_surface
    cs, intervals = lith
    tops = _unit_tops(cs, intervals)
    use = [u for u in tops if len(tops[u]) >= 3]
    if not use:
        return [], "No lithology unit is present in 3 or more holes, so the split by unit is not available."
    use.sort(key=lambda u: -np.mean([t[2] for t in tops[u]]))     # highest unit first, like the geology surfaces
    skipped = [u for u in tops if len(tops[u]) < 3]
    zi, yi, xi = np.nonzero(inside)
    px, py, pz = X[xi], Y[yi], Z[zi]
    idx = np.zeros(len(px), dtype=int)                            # above every top: counts as the first unit
    for k, u in enumerate(use):
        T = _interp_surface(np.array(tops[u], dtype=float), px, py)
        idx = np.where(pz <= T, k, idx)                           # the deepest top that is still above the point
    res = []
    for k, u in enumerate(use):
        n = int((idx == k).sum())
        if n:
            res.append((u or "Unknown", n * cell))
    note = ""
    if skipped:
        note = "Units found in fewer than 3 holes are not split out (%s); their share is counted in the unit above." % ", ".join(
            (u or "Unknown") for u in skipped)
    return res, note
