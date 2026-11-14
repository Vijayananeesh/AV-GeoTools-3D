import logging
import math
import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
from vtkmodules.vtkCommonCore import vtkUnsignedCharArray, vtkPoints, vtkLookupTable, vtkStringArray, vtkFloatArray
from vtkmodules.vtkCommonDataModel import vtkPolyData, vtkCellArray, vtkImageData, vtkStructuredGrid, vtkImplicitVolume
from vtkmodules.vtkFiltersCore import vtkTubeFilter, vtkContourFilter, vtkGlyph3D, vtkWindowedSincPolyDataFilter, vtkPolyDataNormals, vtkStripper, vtkClipPolyData
from vtkmodules.vtkFiltersSources import vtkSphereSource, vtkCubeSource
from vtkmodules.vtkFiltersGeometry import vtkDataSetSurfaceFilter
from vtkmodules.vtkRenderingCore import vtkTexture, vtkActor, vtkPolyDataMapper, vtkActor2D, vtkPolyDataMapper2D, vtkTextActor, vtkBillboardTextActor3D, vtkTextActor3D
from vtkmodules.vtkRenderingLabel import vtkLabeledDataMapper
from vtkmodules.vtkRenderingAnnotation import vtkScalarBarActor, vtkLegendBoxActor
from . import cut as _cut
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401  (registers the OpenGL backend)
try:
    import vtkmodules.vtkRenderingVolumeOpenGL2  # noqa: F401  (volume rendering for the plume cloud)
except ImportError:
    _log.debug("volume rendering is not available", exc_info=True)
import vtkmodules.vtkRenderingFreeType  # noqa: F401  (text support)
import vtkmodules.vtkInteractionStyle  # noqa: F401

PALETTE = [(0.76, 0.70, 0.50), (0.55, 0.35, 0.20), (0.90, 0.80, 0.30), (0.45, 0.62, 0.35),
           (0.40, 0.55, 0.78), (0.72, 0.45, 0.58), (0.62, 0.62, 0.62), (0.92, 0.55, 0.25),
           (0.30, 0.48, 0.32), (0.66, 0.52, 0.78), (0.82, 0.30, 0.30), (0.35, 0.72, 0.72)]

# Lithology look, set by the viewer before every draw: colours per unit, legend text, order and legend options.
STYLE = {"colours": {}, "labels": {}, "show": {}, "order": [],
         "legend": {"show": True, "title": "", "corner": "bottom-left", "scale": 1.0, "background": True, "border": False,
               "text_size": 12, "symbol_size": 11, "gap": 8, "spacing": 6, "padding": 8, "text_mode": "auto",
               "text_colour": (0.0, 0.0, 0.0), "bold": False, "italic": False, "font": "arial", "shape": "square", "dpr": 1.0}}


_log = logging.getLogger("av_geotools_3d")


def set_style(colours=None, labels=None, show=None, order=None, legend=None):
    STYLE["colours"] = {k: tuple(float(x) for x in v) for k, v in (colours or {}).items()}
    STYLE["labels"] = dict(labels or {})
    STYLE["show"] = dict(show or {})
    STYLE["order"] = list(order or [])
    base = dict(STYLE["legend"]) if False else {"show": True, "title": "", "corner": "bottom-left", "scale": 1.0, "background": True, "border": False,
               "text_size": 12, "symbol_size": 11, "gap": 8, "spacing": 6, "padding": 8, "text_mode": "auto",
               "text_colour": (0.0, 0.0, 0.0), "bold": False, "italic": False, "font": "arial", "shape": "square", "dpr": 1.0}
    base.update(legend or {})
    STYLE["legend"] = base


def unit_label(unit):
    return STYLE["labels"].get(unit) or unit


def unit_colour(unit, codes):
    c = STYLE["colours"].get(unit)
    if c:
        return c
    idx = (codes or {}).get(unit, 0)
    return PALETTE[(idx - 1) % len(PALETTE)] if idx else (0.7, 0.7, 0.7)


def legend_units(codes):
    """Units shown in the legend, in the order chosen by the user."""
    names = [u for u in STYLE["order"] if u in codes] + [u for u, i in sorted(codes.items(), key=lambda kv: kv[1])
                                                          if u not in STYLE["order"]]
    return [u for u in names if STYLE["show"].get(u, True)]


ZANISO = 3.0  # vertical distances count 3x more than horizontal when interpolating the plume


def _lith_lut(codes):
    n = (max(codes.values()) + 1) if codes else 1
    inv = {i: u for u, i in codes.items()}
    lut = vtkLookupTable()
    lut.SetNumberOfTableValues(n)
    lut.SetTableRange(-0.5, n - 0.5)
    lut.SetTableValue(0, 0.70, 0.70, 0.70, 1.0)
    for i in range(1, n):
        r, g, b = unit_colour(inv[i], codes) if i in inv else (0.7, 0.7, 0.7)
        lut.SetTableValue(i, r, g, b, 1.0)
    lut.Build()
    return lut


def _boreholes(cs, intervals, codes, ve, radius):
    pts, lines = vtkPoints(), vtkCellArray()
    sc = vtkFloatArray()
    sc.SetName("lith")
    k = 0
    for h, (x, y, z, depth) in cs.items():
        segs = intervals.get(h) or [(0.0, depth, "")]
        for a, b, code in segs:
            if b <= a:
                continue
            v = codes.get(code, 0)
            pts.InsertNextPoint(x, y, (z - a) * ve)
            pts.InsertNextPoint(x, y, (z - b) * ve)
            lines.InsertNextCell(2)
            lines.InsertCellPoint(k)
            lines.InsertCellPoint(k + 1)
            sc.InsertNextValue(v)
            sc.InsertNextValue(v)
            k += 2
    pd = vtkPolyData()
    pd.SetPoints(pts)
    pd.SetLines(lines)
    pd.GetPointData().SetScalars(sc)
    tube = vtkTubeFilter()
    tube.SetInputData(pd)
    tube.SetRadius(radius)
    tube.SetNumberOfSides(14)
    tube.CappingOn()
    tube.Update()
    return tube.GetOutput()


def _labels(cs, ve, fg=(1.0, 1.0, 1.0)):
    """Hole names as text that faces the camera, one actor per hole (so crowded names can be tidied like the result labels)."""
    acts = []
    bg = (0.05, 0.06, 0.08) if sum(fg) > 1.5 else (1.0, 1.0, 1.0)
    for h, (x, y, z, d) in cs.items():
        ta = vtkBillboardTextActor3D()
        ta.SetPosition(float(x), float(y), float(z * ve))
        ta.SetInput(str(h))
        ta.SetDisplayOffset(6, 6)
        tp = ta.GetTextProperty()
        tp.SetFontFamilyToArial()
        tp.SetFontSize(13)
        tp.SetBold(1)
        tp.SetColor(*fg)
        tp.SetJustificationToLeft()
        tp.SetVerticalJustificationToCentered()
        tp.SetBackgroundColor(*bg)
        tp.SetBackgroundOpacity(0.45)
        acts.append(ta)
    return acts


def _surface(xs, ys, z, ve):
    ny, nx = z.shape
    zf = np.where(np.isfinite(z), z, np.nanmean(z))
    X, Y = np.meshgrid(xs, ys)
    xyz = np.column_stack([X.ravel(), Y.ravel(), (zf * ve).ravel()]).astype(np.float32)
    vp = vtkPoints()
    vp.SetData(numpy_to_vtk(xyz, deep=1))
    sg = vtkStructuredGrid()
    sg.SetDimensions(nx, ny, 1)
    sg.SetPoints(vp)
    el = numpy_to_vtk(zf.ravel().astype(np.float32), deep=1)
    el.SetName("elev")
    sg.GetPointData().SetScalars(el)
    f = vtkDataSetSurfaceFilter()
    f.SetInputData(sg)
    f.Update()
    return f.GetOutput(), float(zf.min()), float(zf.max())


def hull_distance(wells_xy, px, py):
    """Signed distance (m) from points to the convex hull of the wells: positive inside, negative outside.
    Returns None when the wells do not make an area (fewer than 3, or all on one line)."""
    from scipy.spatial import ConvexHull
    pts = np.unique(np.round(np.asarray(wells_xy, dtype=float).reshape(-1, 2), 6), axis=0)
    if len(pts) < 3:
        return None
    try:
        poly = pts[ConvexHull(pts).vertices]              # counter-clockwise
    except Exception:
        return None
    A = poly
    e = np.roll(poly, -1, axis=0) - A
    L = np.maximum(np.linalg.norm(e, axis=1), 1e-12)
    P = np.column_stack([np.asarray(px, dtype=float).ravel(), np.asarray(py, dtype=float).ravel()])
    dx, dy = P[:, None, 0] - A[None, :, 0], P[:, None, 1] - A[None, :, 1]
    inside = ((e[None, :, 0] * dy - e[None, :, 1] * dx) / L[None, :]).min(axis=1)        # >= 0 inside the hull
    t = np.clip((dx * e[None, :, 0] + dy * e[None, :, 1]) / (L ** 2)[None, :], 0.0, 1.0)
    out = np.hypot(dx - t * e[None, :, 0], dy - t * e[None, :, 1]).min(axis=1)            # distance to the outline
    return np.where(inside >= 0, inside, -out)


def _plume_grid(pl, ve, influence, method="kriging", detail=48, zaniso=3.0, ground=None, water=None, hull=None, water_below=False, bottom=None):
    """Concentration estimated on a 3D grid around the samples. Returns (image data, info about the method used).
    `detail` is the number of grid cells along X and Y. Kriging is computed on at most 56 cells and then
    interpolated to the finer grid, which keeps it quick."""
    from . import plumeest
    mn, mx = pl[:, :3].min(axis=0), pl[:, :3].max(axis=0)
    span = np.maximum(mx - mn, 1.0)
    sxy = max(span[0], span[1])
    mh, mv = 0.25 * sxy, 0.5 * span[2]
    detail = int(min(max(detail, 16), 140))
    nx = ny = detail
    nz = max(18, int(round(detail * 0.75)))
    xs = np.linspace(mn[0] - mh, mx[0] + mh, nx)
    ys = np.linspace(mn[1] - mh, mx[1] + mh, ny)
    zs = np.linspace(mn[2] - mv, mx[2] + mv, nz)
    base = min(detail, 56) if method == "kriging" else detail
    if base < detail:
        nx0 = ny0 = base
        nz0 = max(18, int(round(base * 0.75)))
        xs0, ys0, zs0 = (np.linspace(mn[0] - mh, mx[0] + mh, nx0), np.linspace(mn[1] - mh, mx[1] + mh, ny0),
                         np.linspace(mn[2] - mv, mx[2] + mv, nz0))
        Z, Y, X = np.meshgrid(zs0, ys0, xs0, indexing="ij")
        vals0, info = plumeest.estimate(pl, np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]), method, influence, zaniso)
        from scipy.interpolate import RegularGridInterpolator
        interp = RegularGridInterpolator((zs0, ys0, xs0), vals0.reshape(nz0, ny0, nx0), method="linear")
        Z, Y, X = np.meshgrid(zs, ys, xs, indexing="ij")
        vals = interp(np.column_stack([Z.ravel(), Y.ravel(), X.ravel()]))
    else:
        Z, Y, X = np.meshgrid(zs, ys, xs, indexing="ij")
        vals, info = plumeest.estimate(pl, np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]), method, influence, zaniso)
    info["grid"] = (nx, ny, nz)
    info["clip"] = None
    info["hull_ok"] = None
    info["arrays"] = None
    if ground is not None or water is not None or hull is not None or bottom is not None:
        # a field that is positive where the plume is allowed: below the ground, above the water table, inside the hull
        from scipy.interpolate import RegularGridInterpolator
        Y2, X2 = np.meshgrid(ys, xs, indexing="ij")

        def at(grid):
            gx, gy, gz = grid
            gz = np.where(np.isfinite(gz), gz, np.nanmean(gz))
            gi = RegularGridInterpolator((gy, gx), gz, method="linear")
            return gi(np.column_stack([np.clip(Y2.ravel(), gy.min(), gy.max()), np.clip(X2.ravel(), gx.min(), gx.max())])).reshape(ny, nx)
        diff = None
        if ground is not None:
            diff = at(ground)[None, :, :] - zs[:, None, None]
        if water is not None:
            up = (at(water)[None, :, :] - zs[:, None, None]) if water_below else (zs[:, None, None] - at(water)[None, :, :])
            diff = up if diff is None else np.minimum(diff, up)
        if bottom is not None:
            dn = zs[:, None, None] - at(bottom)[None, :, :]          # positive above the imaginary surface through the well bottoms
            diff = dn if diff is None else np.minimum(diff, dn)
        if hull is not None:
            hd = hull_distance(hull["xy"], X2.ravel(), Y2.ravel())
            info["hull_ok"] = hd is not None
            if hd is not None:
                hf = np.broadcast_to((hd + float(hull.get("buffer", 0.0))).reshape(1, ny, nx), (nz, ny, nx))
                diff = hf if diff is None else np.minimum(diff, hf)
        if diff is None:
            diff = np.ones((nz, ny, nx))
        cimg = vtkImageData()
        cimg.SetDimensions(nx, ny, nz)
        cimg.SetOrigin(xs[0], ys[0], zs[0] * ve)
        cimg.SetSpacing(xs[1] - xs[0], ys[1] - ys[0], (zs[1] - zs[0]) * ve)
        ca = numpy_to_vtk(np.asarray(diff.ravel(), dtype=np.float32), deep=1)
        ca.SetName("allowed")
        cimg.GetPointData().SetScalars(ca)
        info["clip"] = cimg
        info["allowed_arr"] = diff
    img = vtkImageData()
    img.SetDimensions(nx, ny, nz)
    img.SetOrigin(xs[0], ys[0], zs[0] * ve)
    img.SetSpacing(xs[1] - xs[0], ys[1] - ys[0], (zs[1] - zs[0]) * ve)
    arr = numpy_to_vtk(np.asarray(vals, dtype=np.float32), deep=1)
    arr.SetName("conc")
    img.GetPointData().SetScalars(arr)
    info["arrays"] = (xs, ys, zs, np.asarray(vals, dtype=np.float32).reshape(nz, ny, nx), info.pop("allowed_arr", None))
    return img, info


def _contour(img, level):
    cf = vtkContourFilter()
    cf.SetInputData(img)
    cf.SetValue(0, float(level))
    cf.ComputeNormalsOn()
    cf.Update()
    return cf.GetOutput()


def _smooth_poly(poly, level):
    """Smooths a surface without making it shrink (windowed-sinc filter); level 0 leaves it as it is."""
    level = float(level or 0)
    if level <= 0 or poly.GetNumberOfCells() == 0:
        return poly
    f = vtkWindowedSincPolyDataFilter()
    f.SetInputData(poly)
    f.SetNumberOfIterations(int(6 + 6 * level))
    f.SetPassBand(max(0.02, 0.26 - 0.022 * level))
    f.BoundarySmoothingOff()
    f.FeatureEdgeSmoothingOff()
    f.NonManifoldSmoothingOn()
    f.NormalizeCoordinatesOn()
    f.Update()
    nrm = vtkPolyDataNormals()
    nrm.SetInputData(f.GetOutput())
    nrm.SplittingOff()
    nrm.ConsistencyOn()
    nrm.Update()
    return nrm.GetOutput()


def _clip_ground(poly, cimg):
    """Cuts a surface exactly at the ground: only the part below the topography is kept."""
    if cimg is None or poly.GetNumberOfCells() == 0:
        return poly
    imp = vtkImplicitVolume()
    imp.SetVolume(cimg)
    clip = vtkClipPolyData()
    clip.SetInputData(poly)
    clip.SetClipFunction(imp)
    clip.SetValue(0.0)
    clip.Update()
    out = vtkPolyData()
    out.DeepCopy(clip.GetOutput())
    return out


def _shell_build(img, pinfo, level, nxt, smooth, ve, lut, cmin, cmax, closed=True, cutcol=True):
    """One plume shell. Returns (poly, point colours or None).
    closed: the shell is built from one field (concentration above the level AND inside the allowed zone), so it is cut exactly
    at the ground, water table, well bottoms and hull and the cut face is part of the same surface, not hollow.
    The cut face of each shell only covers the band between its level and the next one, so the cut faces of the nested
    shells tile without overlapping, and with `cutcol` they are coloured by the concentration (a cross-section)."""
    arrs = pinfo.get("arrays")
    if not closed or not arrs or pinfo.get("clip") is None:
        return _clip_ground(_smooth_poly(_contour(img, level), smooth), pinfo["clip"]), None
    from scipy import ndimage
    from scipy.ndimage import map_coordinates
    from vtkmodules.vtkFiltersCore import vtkAppendPolyData
    xs, ys, zs, vals, allowed = arrs
    vals = np.asarray(vals, dtype=float)
    smooth = float(smooth or 0)
    vs = ndimage.gaussian_filter(vals, sigma=0.3 + 0.1 * smooth) if smooth > 0 else vals
    nz, ny, nx = vals.shape
    dx, dy, dz = float(xs[1] - xs[0]), float(ys[1] - ys[0]), float(zs[1] - zs[0])
    cell = min(dx, dy)

    def image_of(arr):
        im = vtkImageData()
        im.SetDimensions(nx, ny, nz)
        im.SetOrigin(float(xs[0]), float(ys[0]), float(zs[0]) * ve)
        im.SetSpacing(dx, dy, dz * ve)
        a = numpy_to_vtk(np.ascontiguousarray(arr, dtype=np.float32).ravel(), deep=1)
        a.SetName("f")
        im.GetPointData().SetScalars(a)
        return im

    F = np.minimum((vs - level) / max(level, 1e-12), np.asarray(allowed, dtype=float) / cell)
    F[0, :, :] = F[-1, :, :] = -1.0                      # a shell that reaches the edge of the grid is closed there too
    F[:, 0, :] = F[:, -1, :] = -1.0
    F[:, :, 0] = F[:, :, -1] = -1.0
    cf = vtkContourFilter()
    cf.SetInputData(image_of(F))
    cf.SetValue(0, 0.0)
    cf.ComputeNormalsOn()
    cf.ComputeScalarsOff()
    cf.Update()
    poly = cf.GetOutput()
    if poly.GetNumberOfCells() == 0:
        return poly, None
    eps = 0.35 * cell
    amp = vtkImplicitVolume()
    amp.SetVolume(image_of(np.asarray(allowed, dtype=float)))
    parts_in = []
    for inside in (False, True):                           # skin: away from the cut faces; face: the part lying on them
        cl = vtkClipPolyData()
        cl.SetInputData(poly)
        cl.SetClipFunction(amp)
        cl.SetValue(eps)
        if inside:
            cl.InsideOutOn()
        cl.Update()
        q = vtkPolyData()
        q.DeepCopy(cl.GetOutput())
        parts_in.append(q)
    skin, face = parts_in
    if nxt is not None and face.GetNumberOfCells() > 0:
        imp = vtkImplicitVolume()
        imp.SetVolume(image_of(vs))
        clip = vtkClipPolyData()
        clip.SetInputData(face)
        clip.SetClipFunction(imp)
        clip.SetValue(float(nxt))
        clip.InsideOutOn()                                # only the band below the next level
        clip.Update()
        face = clip.GetOutput()
    parts = []
    for part, flag in ((skin, 0), (face, 1)):
        if part.GetNumberOfCells() == 0:
            continue
        q = vtkPolyData()
        q.DeepCopy(part)
        fl = numpy_to_vtk(np.full(q.GetNumberOfPoints(), flag, dtype=np.uint8), deep=1)
        fl.SetName("cutface")
        q.GetPointData().AddArray(fl)
        parts.append(q)
    if not parts:
        return vtkPolyData(), None
    ap = vtkAppendPolyData()
    for q in parts:
        ap.AddInputData(q)
    ap.Update()
    out = vtkPolyData()
    out.DeepCopy(ap.GetOutput())
    if not cutcol:
        return out, None
    P = vtk_to_numpy(out.GetPoints().GetData()).astype(float)
    flags = vtk_to_numpy(out.GetPointData().GetArray("cutface")).astype(bool)
    co = np.array([(P[:, 2] - zs[0] * ve) / (dz * ve), (P[:, 1] - ys[0]) / dy, (P[:, 0] - xs[0]) / dx])
    conc = map_coordinates(vs, co, order=1, mode="nearest")
    table = np.linspace(cmin, cmax, 256)
    cols = np.empty((256, 3))
    for i, v in enumerate(table):
        c = [0.0, 0.0, 0.0]
        lut.GetColor(float(v), c)
        cols[i] = c
    pos = np.clip((conc - cmin) / max(cmax - cmin, 1e-12), 0.0, 1.0) * 255.0
    own = [0.0, 0.0, 0.0]
    lut.GetColor(float(min(max(level, cmin), cmax)), own)
    prgb = np.tile(np.array(own), (len(P), 1))
    for k in range(3):
        prgb[flags, k] = np.interp(pos[flags], np.arange(256), cols[:, k])
    return out, prgb


def _plume(pl, ve, iso, influence, method="kriging", detail=48, zaniso=3.0, smooth=0, ground=None):
    img, info = _plume_grid(pl, ve, influence, method, detail, zaniso, ground)
    return _clip_ground(_smooth_poly(_contour(img, iso), smooth), info["clip"])


def _fade_ctx(fo, pinfo, influence, samples, ve, zaniso):
    """Settings for fading a shell away from the samples, or None when it is switched off.
    Distance is measured like the estimate does: a vertical metre counts `zaniso` times a horizontal one.
    Full fade is reached at `ref`: the distance set by the user, else the variogram range (kriging), else the influence radius."""
    if not fo or not fo.get("on") or samples is None or len(samples) == 0:
        return None
    sam = np.asarray(samples, dtype=float)[:, :3]
    ref = float(fo.get("dist") or 0.0)
    how = "set by you"
    if ref <= 0:
        if pinfo.get("method") == "kriging" and pinfo.get("range"):
            ref, how = float(pinfo["range"]), "the variogram range"
        else:
            sxy = max(np.ptp(sam[:, 0]), np.ptp(sam[:, 1]), 1.0)
            ref = float(influence) if influence and influence > 0 else max(0.3 * sxy, 5.0)
            how = "the influence radius"
    st = min(max(float(fo.get("strength", 0.6)), 0.0), 0.95)
    return {"samples": sam, "ref": ref, "strength": st, "ve": float(ve or 1.0), "zaniso": max(float(zaniso), 0.1),
            "note": "Shells fade with distance from the nearest sample: down to %d%% opacity at %.3g m or more (%s)." % (
                round((1.0 - st) * 100), ref, how)}


def _apply_fade(mapper, poly, rgb, fd, pt_rgb=None):
    """Colours the vertices of a shell: its colour (or `pt_rgb`, one colour per vertex) and an opacity that falls with the
    distance to the nearest sample when the fade is on."""
    if poly.GetNumberOfPoints() == 0 or (fd is None and pt_rgb is None):
        mapper.ScalarVisibilityOff()
        return
    n = poly.GetNumberOfPoints()
    alpha = np.ones(n)
    if fd is not None:
        from scipy.spatial import cKDTree
        P = vtk_to_numpy(poly.GetPoints().GetData()).astype(float).copy()
        P[:, 2] /= fd["ve"]                                # the scene is stretched by the vertical exaggeration
        sc = np.array([1.0, 1.0, fd["zaniso"]])
        good = np.isfinite(P).all(axis=1)                  # odd points must never stop the drawing
        dist = np.zeros(n)
        if good.any():
            dist[good], _ = cKDTree(fd["samples"] * sc).query(P[good] * sc)
        alpha = 1.0 - fd["strength"] * np.clip(dist / fd["ref"], 0.0, 1.0)
    rgba = np.empty((n, 4), dtype=np.uint8)
    if pt_rgb is None:
        rgba[:, 0], rgba[:, 1], rgba[:, 2] = [int(round(float(c) * 255)) for c in rgb]
    else:
        rgba[:, :3] = np.round(np.clip(np.asarray(pt_rgb), 0.0, 1.0) * 255).astype(np.uint8)
    rgba[:, 3] = np.round(alpha * 255).astype(np.uint8)
    arr = numpy_to_vtk(rgba, deep=1)
    arr.SetName("fade")
    poly.GetPointData().SetScalars(arr)
    mapper.SetColorModeToDirectScalars()
    mapper.ScalarVisibilityOn()


def _keep_volume(info, kind, pinfo, cmax):
    """Remembers the grid of the plume that was drawn, so its volume above a guideline can be worked out afterwards."""
    if info is not None and pinfo.get("arrays") is not None:
        info["vol"] = {"kind": kind, "arrays": pinfo["arrays"], "cmax": float(cmax), "hull_ok": pinfo.get("hull_ok")}


def _plume_method_note(info, wanted, po=None):
    po = po or {}
    if info["method"] == "kriging":
        txt = "Plume estimated by kriging on log values (spherical variogram: range %.0f m, sill %.2f, nugget %.2f)" % (
            info["range"], info["sill"], info["nugget"])
    elif wanted == "kriging":
        txt = "Plume: %s" % info["note"]
    else:
        txt = "Plume estimated by inverse-distance weighting"
    g = info.get("grid")
    extra = []
    if g:
        extra.append("grid %d x %d x %d" % g)
    extra.append("vertical weight %g" % po.get("zaniso", 3.0))
    if po.get("smooth", 0):
        extra.append("shells smoothed at %g of 10" % po.get("smooth"))
    if po.get("ground") is not None:
        extra.append("cut at the ground surface: %s" % po.get("ground_source", "topography"))
    if po.get("water") is not None:
        extra.append("kept above the groundwater surface")
    if po.get("hull") is not None:
        extra.append("limited sideways to the wells' convex hull + %g m" % po["hull"].get("buffer", 0.0) if info.get("hull_ok")
                     else "NOT limited to the wells: they need to cover an area (at least 3, not in a line)")
    return txt + " (" + ", ".join(extra) + ")."


# colour ramps for the nested shells: key -> (name, colour stops from the lowest shell to the highest)
SHELL_RAMPS = {
    "rainbow": ("Blue - cyan - green - yellow - red", [(0.0, 0.0, 1.0), (0.0, 1.0, 1.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0), (1.0, 0.0, 0.0)]),
    "ramp": ("Pale yellow - orange - red", [(1.0, 0.94, 0.55), (1.0, 0.60, 0.15), (0.82, 0.08, 0.06)]),
    "reds": ("Reds", [(1.0, 0.96, 0.94), (0.98, 0.41, 0.29), (0.65, 0.06, 0.08)]),
    "blues": ("Blues", [(0.87, 0.92, 0.97), (0.42, 0.68, 0.84), (0.03, 0.32, 0.61)]),
    "greens": ("Greens", [(0.90, 0.96, 0.88), (0.45, 0.77, 0.46), (0.00, 0.43, 0.17)]),
    "purples": ("Purples", [(0.94, 0.93, 0.96), (0.62, 0.60, 0.78), (0.33, 0.15, 0.56)]),
    "ylgnbu": ("Yellow - green - blue", [(1.0, 1.0, 0.85), (0.25, 0.71, 0.77), (0.03, 0.11, 0.35)]),
    "viridis": ("Viridis (colour-blind safe)", [(0.267, 0.005, 0.329), (0.231, 0.318, 0.545), (0.128, 0.567, 0.551),
                                                 (0.369, 0.789, 0.383), (0.993, 0.906, 0.144)]),
    "plasma": ("Plasma", [(0.05, 0.03, 0.53), (0.49, 0.01, 0.66), (0.80, 0.28, 0.47), (0.97, 0.58, 0.25), (0.94, 0.98, 0.13)]),
    "bluered": ("Blue - white - red", [(0.23, 0.30, 0.75), (0.87, 0.87, 0.87), (0.71, 0.02, 0.15)]),
    "greys": ("Greys", [(0.85, 0.85, 0.85), (0.50, 0.50, 0.50), (0.15, 0.15, 0.15)]),
}


def norm_ramp(key):
    """Older setups used "samples" for the rainbow scale of the sample points."""
    key = "rainbow" if key in (None, "", "samples") else key
    return key if key in SHELL_RAMPS else "rainbow"


def plume_lut(key, cmin, cmax):
    """One colour scale for the plume: used by the sample spheres, the shells and the legend bar."""
    lut = vtkLookupTable()
    lut.SetNumberOfTableValues(256)
    for i in range(256):
        r, g, b = ramp_colour(norm_ramp(key), i / 255.0)
        lut.SetTableValue(i, r, g, b, 1.0)
    lut.SetTableRange(cmin, cmax if cmax > cmin else cmin + 1e-9)
    return lut


def ramp_colour(key, t):
    """Colour on the ramp `key` at t from 0 (lowest shell) to 1 (highest shell)."""
    stops = SHELL_RAMPS.get(key, SHELL_RAMPS["ramp"])[1]
    n = len(stops) - 1
    t = min(max(float(t), 0.0), 1.0) * n
    i = min(int(t), n - 1)
    f = t - i
    return tuple(a + (b - a) * f for a, b in zip(stops[i], stops[i + 1]))


def shell_ramp(t, key="ramp"):
    """Colour of a shell, t from 0 (outer, lowest shell) to 1 (inner, highest shell)."""
    return ramp_colour(key, t)


def shell_levels(cmax, base, mult, absolute=False):
    """Concentrations of the nested shells: base x each multiplier (or the typed concentrations themselves when
    `absolute` is set), only those the data can reach."""
    if absolute:
        out = []
        for m in (mult or []):
            v = float(m)
            if 0 < v < cmax * 0.999 and all(abs(v - o) > 1e-9 for o in out):
                out.append(v)
        return sorted(out)
    base = base if base and base > 0 else 0.05 * cmax
    out = []
    for m in (mult or [1.0]):
        v = base * float(m)
        if 0 < v < cmax * 0.999 and all(abs(v - o) > 1e-9 for o in out):
            out.append(v)
    return sorted(out)


class _LegendOverlay:
    """Lithology legend made of 2D text and symbols placed in pixels from a corner of the picture.
    Text size, symbol size, colours and font are all independent; it re-lays itself out on every render."""

    def __init__(self, ren, rows, title, fg, opts):
        self.ren, self.rows, self.title, self.fg, self.o = ren, rows, title, fg, opts
        dark = sum(fg) > 1.5
        self.bg = self._poly_actor(1, 0.88, (0.1, 0.1, 0.12) if dark else (1.0, 1.0, 1.0), layer=0)
        self.border = self._poly_actor(2, 1.0, fg, layer=1)
        self.sym = self._poly_actor(0, 1.0, (1, 1, 1), layer=2)
        self.texts = []
        for k, (label, col) in enumerate(rows):
            self.texts.append(self._text(label, self._text_colour(col)))
        self.title_actor = self._text(title, fg, bold=True) if title else None
        self.actors = [self.bg, self.border, self.sym] + self.texts + ([self.title_actor] if self.title_actor else [])
        self._built = None

    def _text_colour(self, unit_rgb):
        mode = self.o.get("text_mode", "auto")
        if mode == "unit":
            return unit_rgb
        if mode == "custom":
            return tuple(self.o.get("text_colour", (0, 0, 0)))
        return self.fg

    def _text(self, text, rgb, bold=None):
        ta = vtkTextActor()
        ta.SetInput(text)
        tp = ta.GetTextProperty()
        fam = self.o.get("font", "arial")
        (tp.SetFontFamilyToTimes if fam == "times" else tp.SetFontFamilyToCourier if fam == "courier" else tp.SetFontFamilyToArial)()
        tp.SetColor(*rgb)
        tp.SetBold(bool(self.o.get("bold", False)) if bold is None else bold)
        tp.SetItalic(bool(self.o.get("italic", False)))
        tp.SetShadow(0)
        tp.SetJustificationToLeft()
        tp.SetVerticalJustificationToCentered()
        ta.SetLayerNumber(3)
        return ta

    def _poly_actor(self, kind, opacity, rgb, layer):
        """kind 0: symbols (coloured cells), 1: filled quad, 2: outline"""
        pd = vtkPolyData()
        pd.SetPoints(vtkPoints())
        pd.GetPoints().SetNumberOfPoints(4)
        mp = vtkPolyDataMapper2D()
        mp.SetInputData(pd)
        act = vtkActor2D()
        act.SetMapper(mp)
        act.GetProperty().SetOpacity(opacity)
        act.GetProperty().SetColor(*rgb)
        act.SetLayerNumber(layer)
        if kind == 2:
            act.GetProperty().SetLineWidth(1.5)
        act._pd = pd
        return act

    def layout(self, *args):
        try:
            self._layout()
        except Exception:
            _log.debug('Could not refresh the layout of the legend', exc_info=True)

    def _layout(self):
        o = self.o
        W, H = self.ren.GetSize()
        if W < 20 or H < 20:
            return
        k = max(0.3, float(o.get("scale", 1.0) or 1.0)) * max(0.5, float(o.get("dpr", 1.0) or 1.0))
        text_px = max(6, int(round(float(o.get("text_size", 16)) * k)))
        S = float(o.get("symbol_size", 14)) * k
        gap, pad, rowgap = float(o.get("gap", 8)) * k, float(o.get("padding", 8)) * k, float(o.get("spacing", 6)) * k
        margin = 12.0 * k
        for ta in self.texts:
            ta.GetTextProperty().SetFontSize(text_px)
        if self.title_actor is not None:
            self.title_actor.GetTextProperty().SetFontSize(text_px)
        sizes = []
        for ta in self.texts + ([self.title_actor] if self.title_actor else []):
            sz = [0.0, 0.0]
            ta.GetSize(self.ren, sz)
            sizes.append(sz)
        row_sizes = sizes[:len(self.texts)]
        title_sz = sizes[-1] if self.title_actor is not None else [0.0, 0.0]
        rh = [max(sz[1], S) for sz in row_sizes]
        text_w = max([sz[0] for sz in row_sizes] or [0.0])
        bw = max(2 * pad + S + gap + text_w, 2 * pad + title_sz[0])
        bh = 2 * pad + sum(rh) + rowgap * max(len(rh) - 1, 0) + ((title_sz[1] + rowgap) if self.title_actor is not None else 0.0)
        corner = o.get("corner", "bottom-left")
        x0 = margin if "left" in corner else W - margin - bw
        y0 = margin if "bottom" in corner else H - margin - bh
        # background and outline
        quad = [(x0, y0), (x0 + bw, y0), (x0 + bw, y0 + bh), (x0, y0 + bh)]
        pd = self.bg._pd
        pts = pd.GetPoints()
        pts.SetNumberOfPoints(4)
        for i, (x, y) in enumerate(quad):
            pts.SetPoint(i, x, y, 0)
        ca = vtkCellArray()
        ca.InsertNextCell(4)
        for i in range(4):
            ca.InsertCellPoint(i)
        pd.SetPolys(ca)
        pts.Modified()
        pd.Modified()
        self.bg.SetVisibility(1 if o.get("background", True) and self.rows else 0)
        pd2 = self.border._pd
        p2 = pd2.GetPoints()
        p2.SetNumberOfPoints(4)
        for i, (x, y) in enumerate(quad):
            p2.SetPoint(i, x, y, 0)
        la = vtkCellArray()
        la.InsertNextCell(5)
        for i in (0, 1, 2, 3, 0):
            la.InsertCellPoint(i)
        pd2.SetLines(la)
        p2.Modified()
        pd2.Modified()
        self.border.SetVisibility(1 if o.get("border", False) else 0)
        # rows from the top
        y = y0 + bh - pad
        if self.title_actor is not None:
            self.title_actor.SetPosition(x0 + pad, y - title_sz[1] / 2.0)
            y -= title_sz[1] + rowgap
        sp = self.sym._pd
        spts = vtkPoints()
        cells = vtkCellArray()
        cols = vtkUnsignedCharArray()
        cols.SetNumberOfComponents(3)
        circle = o.get("shape", "square") == "circle"
        n_pts = 0
        for (label, col), ta, h_row in zip(self.rows, self.texts, rh):
            yc = y - h_row / 2.0
            xc = x0 + pad + S / 2.0
            ta.SetPosition(x0 + pad + S + gap, yc)
            if circle:
                m = 20
                for j in range(m):
                    ang = 2 * math.pi * j / m
                    spts.InsertNextPoint(xc + S / 2.0 * math.cos(ang), yc + S / 2.0 * math.sin(ang), 0)
                cells.InsertNextCell(m)
                for j in range(m):
                    cells.InsertCellPoint(n_pts + j)
                n_pts += m
            else:
                for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                    spts.InsertNextPoint(xc + dx * S / 2.0, yc + dy * S / 2.0, 0)
                cells.InsertNextCell(4)
                for j in range(4):
                    cells.InsertCellPoint(n_pts + j)
                n_pts += 4
            cols.InsertNextTuple3(*[int(round(v * 255)) for v in col])
            y -= h_row + rowgap
        sp.SetPoints(spts)
        sp.SetPolys(cells)
        sp.GetCellData().SetScalars(cols)
        sp.Modified()
        mp = self.sym.GetMapper()
        mp.SetColorModeToDirectScalars()
        mp.SetScalarModeToUseCellData()
        mp.ScalarVisibilityOn()


def _drop_legend(ren):
    tag = getattr(ren, "_ss_legend_tag", None)
    if tag is not None:
        try:
            ren.RemoveObserver(tag)
        except Exception:
            _log.debug('Could not remove the render observer', exc_info=True)
        ren._ss_legend_tag = None


def _legend(codes, fg, ren):
    """Returns the 2D actors of the legend (already linked to re-layout on every render), or None."""
    opts = STYLE["legend"]
    _drop_legend(ren)
    if not opts.get("show", True):
        return None
    names = legend_units(codes)
    if not names:
        return None
    rows = [(unit_label(u) or "(none)", unit_colour(u, codes)) for u in names]
    ov = _LegendOverlay(ren, rows, (opts.get("title") or "").strip(), fg, dict(opts))
    ren._ss_legend_tag = ren.AddObserver("StartEvent", ov.layout)
    ren._ss_legend_overlay = ov
    ov.layout()
    return ov.actors


def _unit_tops(cs, intervals):
    """{unit: [(x, y, top_elevation)]}: first interval of each unit in each hole."""
    out = {}
    for h, (x, y, z, d) in cs.items():
        seen = set()
        for a, b, code in intervals.get(h, []):
            if code in seen:
                continue
            seen.add(code)
            out.setdefault(code, []).append((x, y, z - a))
    return out


def _interp_surface(P, X, Y):
    """Surface through the points P (n,3): linear between holes, flat outside them."""
    from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
    from scipy.spatial import cKDTree
    pts, vals = P[:, :2], P[:, 2]
    nn = NearestNDInterpolator(pts, vals)(X, Y)
    try:
        lin = LinearNDInterpolator(pts, vals)(X, Y)
        return np.where(np.isnan(lin), nn, lin)
    except Exception:  # points in a straight line: fall back to inverse-distance weighting
        k = min(6, len(pts))
        q = np.column_stack([X.ravel(), Y.ravel()])
        d, i = cKDTree(pts).query(q, k=k)
        d, i = d.reshape(len(q), -1), i.reshape(len(q), -1)
        w = 1.0 / np.maximum(d, 1e-6) ** 2
        return ((w * vals[i]).sum(axis=1) / w.sum(axis=1)).reshape(X.shape)


def _geology(ren, cs, intervals, codes, ve, opts, reg):
    """One coloured surface per lithology unit (its top), interpolated between the boreholes."""
    tops = _unit_tops(cs, intervals)
    if not tops:
        return []
    xy = np.array([(c[0], c[1]) for c in cs.values()])
    span = max(np.ptp(xy[:, 0]), np.ptp(xy[:, 1]), 1.0)
    m = max(0.0, float(opts.get("margin", 0.1))) * span
    n = int(opts.get("n", 60))
    xs = np.linspace(xy[:, 0].min() - m, xy[:, 0].max() + m, n)
    ys = np.linspace(xy[:, 1].min() - m, xy[:, 1].max() + m, n)
    X, Y = np.meshgrid(xs, ys)
    drawn, skipped = [], []
    for unit in sorted(tops, key=lambda u: -np.mean([t[2] for t in tops[u]])):
        P = np.array(tops[unit], dtype=float)
        if len(P) < 3:
            skipped.append("%s (%d hole%s)" % (unit or "Unknown", len(P), "" if len(P) == 1 else "s"))
            continue
        Z = _interp_surface(P, X, Y)
        poly, _, _ = _surface(xs, ys, Z, ve)
        mp = vtkPolyDataMapper()
        mp.SetInputData(poly)
        mp.ScalarVisibilityOff()
        act = vtkActor()
        act.SetMapper(mp)
        idx = codes.get(unit, 0)
        r, g, b = unit_colour(unit, codes)
        act.GetProperty().SetColor(r, g, b)
        act.GetProperty().SetOpacity(0.65)
        ren.AddActor(act)
        reg("Geology: %s (top)" % (unit or "Unknown"), [act], True)
        drawn.append(unit or "Unknown")
    notes = []
    if drawn:
        notes.append("Geology: %d surface(s) drawn (%s)." % (len(drawn), ", ".join(drawn)))
    if skipped:
        notes.append("Geology: skipped units found in fewer than 3 holes: %s." % ", ".join(skipped))
    return notes


def build_scene(ren, collars, intervals, surface, plume, ve=1.0, radius=0.0, iso=0.0, influence=0.0,
                registry=None, geology=None, theme="dark", info=None, sample_radius=1.0, shells=None, plume_method="kriging", plume_opts=None, plume_title="Concentration", plume_legend=None, drape=None):
    """Clears the renderer and draws everything. Returns a list of text notes for the user."""
    ren.RemoveAllViewProps()
    _drop_legend(ren)
    notes = []
    fg = (0.08, 0.08, 0.10) if theme == "light" else (1.0, 1.0, 1.0)

    def reg(name, actors, can_fade):
        if registry is not None:
            registry.append({"name": name, "actors": actors, "fade": can_fade})
    pts = [(c[0], c[1], c[2]) for c in collars.values()] + [tuple(p[:3]) for p in plume]
    if pts:
        a = np.array(pts)
        x0, y0, z0 = a.mean(axis=0)
        span = max(np.ptp(a[:, 0]), np.ptp(a[:, 1]))
    elif surface is not None:
        xs, ys, z = surface
        x0, y0, z0 = float(xs.mean()), float(ys.mean()), float(np.nanmean(z))
        span = max(np.ptp(xs), np.ptp(ys))
    else:
        return ["Nothing to draw: choose at least one layer."]
    if span <= 0:
        span = 10.0
    if info is not None:
        info["origin"] = (float(x0), float(y0), float(z0))
        info["ve"] = float(ve)
    # radius and sample_radius are multipliers of an automatic size (1 = automatic)
    auto_r = max(span * 0.006, 0.05)
    radius = auto_r * (radius if radius > 0 else 1.0)
    sample_r = auto_r * 1.8 * (sample_radius if sample_radius > 0 else 1.0)
    if info is not None:
        info["auto_r"] = float(auto_r)
    cs = {}

    # boreholes + lithology
    if collars:
        cs = {}
        for h, (x, y, z, dep) in collars.items():
            if dep is None:
                segs = intervals.get(h)
                dep = max(s[1] for s in segs) if segs else 10.0
            cs[h] = (x - x0, y - y0, z - z0, dep)
        missing = [h for h in intervals if h not in cs]
        if missing:
            notes.append("%d lithology hole(s) have no matching collar (e.g. %s)." % (len(missing), missing[0]))
        names = sorted({s[2] for h in cs for s in intervals.get(h, [])})
        codes = {nm: i + 1 for i, nm in enumerate(names)}
        if info is not None:
            info["codes"] = codes
            info["lith_ctx"] = (cs, intervals)
        poly = _boreholes(cs, intervals, codes, ve, radius)
        m = vtkPolyDataMapper()
        m.SetInputData(poly)
        lut = _lith_lut(codes)
        m.SetLookupTable(lut)
        m.SetScalarRange(-0.5, len(codes) + 0.5)
        m.UseLookupTableScalarRangeOn()
        m.SetScalarModeToUsePointData()
        act = vtkActor()
        act.SetMapper(m)
        ren.AddActor(act)
        reg("Boreholes (lithology)", [act], True)
        labs = _labels(cs, ve, fg)
        for ta in labs:
            ren.AddActor(ta)
        lab_entry = {"name": "Borehole labels", "actors": labs, "fade": False, "ren": ren, "declutter": True,
                     "size_px": 13, "off_px": 6, "label_vals": []}
        if registry is not None and labs:
            registry.append(lab_entry)
            lab_entry["dtag"] = ren.AddObserver("StartEvent", lambda *a, e=lab_entry: _declutter(e))
        lgs = _legend(codes, fg, ren) if codes else None
        if lgs:
            for a_ in lgs:
                ren.AddViewProp(a_)
            reg("Lithology legend", lgs, False)
        if geology and geology.get("on") and intervals:
            notes += _geology(ren, cs, intervals, codes, ve, geology, reg)
        notes.append("%d borehole(s), %d lithology unit(s)." % (len(cs), len(codes)))

    # top surface
    if surface is not None:
        xs, ys, z = surface
        poly, zmin, zmax = _surface(xs - x0, ys - y0, z - z0, ve)
        lut = vtkLookupTable()
        lut.SetHueRange(0.40, 0.08)
        lut.SetSaturationRange(0.45, 0.45)
        lut.SetValueRange(0.80, 0.80)
        lut.SetTableRange(zmin, zmax)
        lut.Build()
        m = vtkPolyDataMapper()
        m.SetInputData(poly)
        m.SetLookupTable(lut)
        m.SetScalarRange(zmin, zmax)
        m.UseLookupTableScalarRangeOn()
        act = vtkActor()
        act.SetMapper(m)
        act.GetProperty().SetOpacity(0.55)
        if drape is not None:
            # a satellite / aerial picture draped over the surface: every point gets its place in the picture
            P = vtk_to_numpy(poly.GetPoints().GetData())
            xmn, xmx, ymn, ymx = drape["extent"]
            tc = np.column_stack([(P[:, 0] + x0 - xmn) / max(xmx - xmn, 1e-9),
                                  (P[:, 1] + y0 - ymn) / max(ymx - ymn, 1e-9)]).astype(np.float32)
            tca = numpy_to_vtk(tc, deep=1)
            tca.SetName("tcoords")
            poly.GetPointData().SetTCoords(tca)
            rgb = np.ascontiguousarray(drape["rgb"], dtype=np.uint8)
            hh, ww = rgb.shape[:2]
            img = vtkImageData()
            img.SetDimensions(ww, hh, 1)
            ia = numpy_to_vtk(rgb.reshape(-1, 3), deep=1)
            ia.SetName("rgb")
            img.GetPointData().SetScalars(ia)
            tex = vtkTexture()
            tex.SetInputData(img)
            tex.InterpolateOn()
            tex.MipmapOn()
            tex.RepeatOff()
            tex.EdgeClampOn()
            m.ScalarVisibilityOff()
            act.SetTexture(tex)
            act.GetProperty().SetOpacity(0.8)
            act.GetProperty().SetAmbient(0.4)
            act.GetProperty().SetDiffuse(0.7)
            act._jpeg = drape.get("jpeg")
        act.SetPosition(0, 0, -0.1 * ve)  # 10 cm lower, so it does not flicker against the top geology surface
        ren.AddActor(act)
        reg("Topography (DEM)", [act], True)
        notes.append("Surface: %d x %d cells." % (len(xs), len(ys)))

    # plume
    if len(plume):
        pl = plume.copy()
        pl[:, 0] -= x0
        pl[:, 1] -= y0
        pl[:, 2] -= z0
        cmin, cmax = float(pl[:, 3].min()), float(pl[:, 3].max())
        ramp_key = norm_ramp((shells or {}).get("colours"))
        info["plume_ramp"] = ramp_key
        lut = plume_lut(ramp_key, cmin, cmax)
        pp, sc = vtkPoints(), vtkFloatArray()
        sc.SetName("conc")
        for x, y, z, c in pl:
            pp.InsertNextPoint(x, y, z * ve)
            sc.InsertNextValue(c)
        pd = vtkPolyData()
        pd.SetPoints(pp)
        pd.GetPointData().SetScalars(sc)
        sph = vtkSphereSource()
        sph.SetRadius(sample_r)
        sph.SetThetaResolution(12)
        sph.SetPhiResolution(12)
        gl = vtkGlyph3D()
        gl.SetSourceConnection(sph.GetOutputPort())
        gl.SetInputData(pd)
        gl.ScalingOff()
        gl.SetColorModeToColorByScalar()
        gm = vtkPolyDataMapper()
        gm.SetInputConnection(gl.GetOutputPort())
        gm.SetLookupTable(lut)
        gm.SetScalarRange(cmin, cmax)
        gm.UseLookupTableScalarRangeOn()
        ga = vtkActor()
        ga.SetMapper(gm)
        ren.AddActor(ga)
        reg("Plume samples", [ga], True)
        bar = vtkScalarBarActor()
        bar.SetLookupTable(lut)
        bar.SetTitle(plume_title or "Concentration")
        style_plume_bar(bar, plume_legend, fg, (plume_opts or {}).get("dpr", 1.0), plume_title)
        ren.AddViewProp(bar)
        reg("Plume scale bar", [bar], False)
        shells = shells or {}
        po = plume_opts or {}
        info["plume_pts"] = pl
        info["sample_r"] = float(sample_r)
        if po.get("labels") is not None:
            add_sample_labels(ren, pl, po.get("nd"), ve, po["labels"], registry, fg, po.get("dpr", 1.0))
        water = None
        if po.get("water") is not None:
            wx, wy, wz = po["water"]
            water = (np.asarray(wx) - x0, np.asarray(wy) - y0, np.asarray(wz) - z0)
        hull = None
        if po.get("hull") is not None:
            hull = {"xy": np.asarray(po["hull"]["xy"], dtype=float) - np.array([x0, y0]), "buffer": float(po["hull"].get("buffer", 0.0))}
        ground = None
        if po.get("ground") is not None:
            gx, gy, gz = po["ground"]
            ground = (np.asarray(gx) - x0, np.asarray(gy) - y0, np.asarray(gz) - z0)
        levels = shell_levels(cmax, iso, shells.get("mult"), shells.get("absolute")) if shells.get("on") else []
        if shells.get("on") and shells.get("absolute"):
            over = sorted(float(m) for m in (shells.get("mult") or []) if float(m) >= cmax * 0.999)
            if over:
                notes.append("Shell level(s) %s left out: they are above the highest result (%.4g)." % (", ".join("%.4g" % m for m in over), cmax))
        if po.get("display") == "cloud":
            img, pinfo = _plume_grid(pl, ve, influence, plume_method, po.get("detail", 48), po.get("zaniso", 3.0), ground, water, hull)
            _keep_volume(info, "soil", pinfo, cmax)
            fd = _fade_ctx(po.get("fade"), pinfo, influence, pl, ve, po.get("zaniso", 3.0))
            if fd:
                notes.append(fd["note"])
            notes.append(_plume_method_note(pinfo, plume_method, po))
            base = iso if iso > 0 else 0.05 * cmax
            if _add_cloud(ren, pinfo, lut, cmin, cmax, base, fd, ve, po.get("smooth", 0), po.get("density", 0.8), registry, levels=levels):
                notes.append("Plume cloud from %.4g (faint) to %.4g (dense), data range %.4g to %.4g." % (base, cmax, cmin, cmax))
            else:
                notes.append("No plume cloud could be drawn above %.4g: try a lower lowest value (data range %.4g to %.4g)." % (base, cmin, cmax))
        elif levels:
            # nested see-through shells: the lowest level is the outermost and the faintest
            img, pinfo = _plume_grid(pl, ve, influence, plume_method, po.get("detail", 48), po.get("zaniso", 3.0), ground, water, hull)
            _keep_volume(info, "soil", pinfo, cmax)
            fd = _fade_ctx(po.get("fade"), pinfo, influence, pl, ve, po.get("zaniso", 3.0))
            if fd:
                notes.append(fd["note"])
            notes.append(_plume_method_note(pinfo, plume_method, po))
            drawn = []
            for j, level in enumerate(levels):
                poly, prgb = _shell_build(img, pinfo, level, levels[j + 1] if j + 1 < len(levels) else None, po.get("smooth", 0), ve, lut, cmin, cmax,
                                          po.get("cap", True), po.get("cutcol", True))
                if poly.GetNumberOfCells() == 0:
                    continue
                rgb = [0.0, 0.0, 0.0]                              # the same colour scale as the sample spheres and the legend
                lut.GetColor(float(min(max(level, cmin), cmax)), rgb)
                sm = vtkPolyDataMapper()
                sm.SetInputData(poly)
                _apply_fade(sm, poly, rgb, fd, prgb)
                sa = vtkActor()
                sa.SetMapper(sm)
                sa.GetProperty().SetColor(*rgb)
                sa.GetProperty().SetOpacity(0.22 + 0.38 * j / max(len(levels) - 1, 1))
                ren.AddActor(sa)
                reg("Plume shell >= %.4g" % level, [sa], True)
                drawn.append(level)
            if drawn:
                notes.append("Plume shells at %s (data range %.4g to %.4g)." % (", ".join("%.4g" % v for v in drawn), cmin, cmax))
            else:
                notes.append("No plume shell could be drawn at %s (data range %.4g to %.4g)." % (", ".join("%.4g" % v for v in levels), cmin, cmax))
        else:
            level = iso if iso > 0 else 0.25 * cmax
            img, pinfo = _plume_grid(pl, ve, influence, plume_method, po.get("detail", 48), po.get("zaniso", 3.0), ground, water, hull)
            _keep_volume(info, "soil", pinfo, cmax)
            fd = _fade_ctx(po.get("fade"), pinfo, influence, pl, ve, po.get("zaniso", 3.0))
            if fd:
                notes.append(fd["note"])
            notes.append(_plume_method_note(pinfo, plume_method, po))
            iso_poly, iprgb = _shell_build(img, pinfo, level, None, po.get("smooth", 0), ve, lut, cmin, cmax, po.get("cap", True), po.get("cutcol", True))
            if iso_poly.GetNumberOfCells() > 0:
                im = vtkPolyDataMapper()
                im.SetInputData(iso_poly)
                ia = vtkActor()
                ia.SetMapper(im)
                irgb = [0.0, 0.0, 0.0]                              # same colour scale as the spheres and the legend
                lut.GetColor(float(min(max(level, cmin), cmax)), irgb)
                _apply_fade(im, iso_poly, irgb, fd, iprgb)
                ia.GetProperty().SetColor(*irgb)
                ia.GetProperty().SetOpacity(0.45)
                ren.AddActor(ia)
                reg("Plume isosurface", [ia], True)
                notes.append("Plume isosurface at %.4g (data range %.4g to %.4g)." % (level, cmin, cmax))
            else:
                notes.append("No isosurface at %.4g: try a lower value (data range %.4g to %.4g)." % (level, cmin, cmax))
    return notes


def reset_camera(ren):
    b = ren.ComputeVisiblePropBounds()
    cx, cy, cz = (b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2
    diag = math.sqrt((b[1] - b[0]) ** 2 + (b[3] - b[2]) ** 2 + (b[5] - b[4]) ** 2) or 1.0
    cam = ren.GetActiveCamera()
    cam.SetFocalPoint(cx, cy, cz)
    cam.SetPosition(cx - 0.5 * diag, cy - 1.0 * diag, cz + 0.7 * diag)
    cam.SetViewUp(0, 0, 1)
    ren.ResetCamera()


def set_view(ren, mode):
    """mode: 'top', 'south' (look north), 'west' (look east) or '3d'"""
    if mode == "3d":
        reset_camera(ren)
        return
    b = ren.ComputeVisiblePropBounds()
    cx, cy, cz = (b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2
    diag = math.sqrt((b[1] - b[0]) ** 2 + (b[3] - b[2]) ** 2 + (b[5] - b[4]) ** 2) or 1.0
    cam = ren.GetActiveCamera()
    cam.SetFocalPoint(cx, cy, cz)
    if mode == "top":
        cam.SetPosition(cx, cy, cz + diag)
        cam.SetViewUp(0, 1, 0)
    elif mode == "south":
        cam.SetPosition(cx, cy - diag, cz)
        cam.SetViewUp(0, 0, 1)
    elif mode == "west":
        cam.SetPosition(cx - diag, cy, cz)
        cam.SetViewUp(0, 0, 1)
    ren.ResetCamera()


def add_axes(ren, info, theme="dark", registry=None):
    """Adds a labelled box with grid lines showing real coordinates (E, N, elevation)."""
    from vtkmodules.vtkRenderingAnnotation import vtkCubeAxesActor
    if not info or "origin" not in info:
        return
    x0, y0, z0 = info["origin"]
    ve = info.get("ve", 1.0) or 1.0
    b = ren.ComputeVisiblePropBounds()
    if b[0] > b[1]:
        return
    col = (0.2, 0.2, 0.25) if theme == "light" else (0.85, 0.85, 0.9)
    grid = (0.78, 0.78, 0.82) if theme == "light" else (0.35, 0.35, 0.4)
    ax = vtkCubeAxesActor()
    ax.SetBounds(b)
    ax.SetCamera(ren.GetActiveCamera())
    ax.SetXAxisRange(b[0] + x0, b[1] + x0)
    ax.SetYAxisRange(b[2] + y0, b[3] + y0)
    ax.SetZAxisRange(b[4] / ve + z0, b[5] / ve + z0)
    ax.SetXTitle("Easting (m)")
    ax.SetYTitle("Northing (m)")
    ax.SetZTitle("Elevation (m)")
    ax.SetLabelScaling(False, 0, 0, 0)                  # full coordinates (1015250), not "1.01525 x 10^6" pieces that run together
    ax.SetXLabelFormat("%.0f")
    ax.SetYLabelFormat("%.0f")
    ax.SetZLabelFormat("%.1f")
    ax.SetFlyModeToOuterEdges()
    ax.SetGridLineLocation(ax.VTK_GRID_LINES_FURTHEST)
    ax.DrawXGridlinesOn()
    ax.DrawYGridlinesOn()
    ax.DrawZGridlinesOn()
    for i in range(3):
        ax.GetTitleTextProperty(i).SetColor(*col)
        ax.GetLabelTextProperty(i).SetColor(*col)
        ax.GetTitleTextProperty(i).SetFontSize(14)
        ax.GetLabelTextProperty(i).SetFontSize(10)
        ax.GetLabelTextProperty(i).SetShadow(0)
    for prop in (ax.GetXAxesLinesProperty(), ax.GetYAxesLinesProperty(), ax.GetZAxesLinesProperty()):
        prop.SetColor(*col)
    for prop in (ax.GetXAxesGridlinesProperty(), ax.GetYAxesGridlinesProperty(), ax.GetZAxesGridlinesProperty()):
        prop.SetColor(*grid)
    ren.AddActor(ax)
    if registry is not None:
        registry.append({"name": "Axes and grid", "actors": [ax], "fade": False})


# ---------------------------------------------------------------- stratigraphic model drawing
def _grid_quads(xs, ys, Z, ve, valid):
    """Quad mesh over a grid; only cells whose four corners are valid are drawn (so pinch-outs leave holes)."""
    from vtkmodules.util.numpy_support import numpy_to_vtkIdTypeArray
    ny, nx = Z.shape
    X, Y = np.meshgrid(xs, ys)
    pts = np.column_stack([X.ravel(), Y.ravel(), (Z * ve).ravel()]).astype(np.float32)
    ok = np.isfinite(Z) & valid
    a = (np.arange(ny - 1)[:, None] * nx + np.arange(nx - 1)[None, :]).ravel()
    okc = (ok[:-1, :-1] & ok[:-1, 1:] & ok[1:, 1:] & ok[1:, :-1]).ravel()
    a = a[okc]
    if len(a) == 0:
        return None
    cells = np.column_stack([np.full(len(a), 4), a, a + 1, a + nx + 1, a + nx]).astype(np.int64).ravel()
    vp = vtkPoints()
    vp.SetData(numpy_to_vtk(pts, deep=1))
    pd = vtkPolyData()
    pd.SetPoints(vp)
    ca = vtkCellArray()
    ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(cells, deep=1))
    pd.SetPolys(ca)
    return pd


def _hull_planes(model, x0, y0):
    """Vertical planes (scene coordinates) along the outline of the boreholes; they keep the inside."""
    from vtkmodules.vtkCommonDataModel import vtkPlane
    from . import model as _m
    planes = []
    for p0, n in _m.hull_edges(model):
        pl = vtkPlane()
        pl.SetOrigin(float(p0[0] - x0), float(p0[1] - y0), 0.0)
        pl.SetNormal(float(n[0]), float(n[1]), 0.0)
        planes.append(pl)
    return planes


def _hull_clip_closed(raw, planes):
    """Cuts a closed solid with the outline planes; the cut walls are filled, so it stays closed."""
    from vtkmodules.vtkCommonDataModel import vtkPlaneCollection
    from vtkmodules.vtkFiltersGeneral import vtkClipClosedSurface
    ccs = vtkClipClosedSurface()
    ccs.SetInputData(raw)
    ccs.GenerateFacesOn()
    ccs.GenerateOutlineOff()
    coll = vtkPlaneCollection()
    for pl in planes:
        coll.AddItem(pl)
    ccs.SetClippingPlanes(coll)
    ccs.Update()
    out = _cut.prepare_closed(ccs.GetOutput())      # merge the duplicated points first, so open edges are real
    # where a unit is very thin at the wall the capping can leave small holes: close any that remain
    from vtkmodules.vtkFiltersCore import vtkFeatureEdges
    fe = vtkFeatureEdges()
    fe.SetInputData(out)
    fe.BoundaryEdgesOn()
    fe.NonManifoldEdgesOff()
    fe.FeatureEdgesOff()
    fe.ManifoldEdgesOff()
    fe.Update()
    if fe.GetOutput().GetNumberOfCells() > 0:
        from vtkmodules.vtkFiltersModeling import vtkFillHolesFilter
        fh = vtkFillHolesFilter()
        fh.SetInputData(out)
        fh.SetHoleSize(1.0e9)
        fh.Update()
        out = _cut.prepare_closed(fh.GetOutput())
    return out


def _hull_clip_poly(poly, planes):
    """Cuts an open surface with the outline planes."""
    from vtkmodules.vtkFiltersCore import vtkClipPolyData
    cur = poly
    for pl in planes:
        c = vtkClipPolyData()
        c.SetInputData(cur)
        c.SetClipFunction(pl)
        c.Update()
        cur = c.GetOutput()
    return cur


def _normals_of(poly):
    from vtkmodules.vtkFiltersCore import vtkPolyDataNormals
    nf = vtkPolyDataNormals()
    nf.SetInputData(poly)
    nf.SetFeatureAngle(50.0)
    nf.SplittingOn()
    nf.ConsistencyOn()
    nf.Update()
    return nf.GetOutput()


def _solid_hull(xs, ys, top, base, ve, planes, eps=0.02):
    """Closed solid between two grid surfaces, cut exactly by vertical planes. The solid is built as a volume
    (one hexahedron per grid cell), the volume is cut and its outside skin is taken, so it is watertight by
    construction, also where a thin unit meets the wall. Returns a closed triangulated surface or None."""
    from vtkmodules.util.numpy_support import numpy_to_vtkIdTypeArray
    from vtkmodules.vtkCommonDataModel import vtkUnstructuredGrid
    from vtkmodules.vtkFiltersGeneral import vtkClipDataSet
    ny, nx = top.shape
    ok = np.isfinite(top) & np.isfinite(base) & ((top - base) > eps)
    cell_ok = ok[:-1, :-1] & ok[:-1, 1:] & ok[1:, 1:] & ok[1:, :-1]
    if not cell_ok.any():
        return None
    X, Y = np.meshgrid(xs, ys)
    n = ny * nx
    pts = np.vstack([np.column_stack([X.ravel(), Y.ravel(), (top * ve).ravel()]),
                     np.column_stack([X.ravel(), Y.ravel(), (base * ve).ravel()])]).astype(np.float64)
    J, I = np.nonzero(cell_ok)
    a = J * nx + I
    b, c, dd = a + 1, a + nx + 1, a + nx
    hexes = np.column_stack([n + a, n + b, n + c, n + dd, a, b, c, dd]).astype(np.int64)
    cells = np.column_stack([np.full(len(hexes), 8, dtype=np.int64), hexes]).ravel()
    vp = vtkPoints()
    vp.SetData(numpy_to_vtk(pts, deep=1))
    ug = vtkUnstructuredGrid()
    ug.SetPoints(vp)
    ca = vtkCellArray()
    ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(cells, deep=1))
    ug.SetCells(12, ca)
    cur = ug
    for pl in planes:
        cd = vtkClipDataSet()
        cd.SetInputData(cur)
        cd.SetClipFunction(pl)
        cd.Update()
        cur = cd.GetOutput()
    sf = vtkDataSetSurfaceFilter()
    sf.SetInputData(cur)
    sf.Update()
    out = sf.GetOutput()
    return out if out.GetNumberOfPoints() else None


def _solid_mesh(xs, ys, top, base, ve, eps=0.02, normals=True):
    """Closed, smooth-shaded solid between two grid surfaces (arrays of shape (ny, nx)).
    Cells where the unit is thinner than eps are left out, so pinch-outs close up properly."""
    from vtkmodules.util.numpy_support import numpy_to_vtkIdTypeArray
    from vtkmodules.vtkFiltersCore import vtkPolyDataNormals
    ny, nx = top.shape
    ok = np.isfinite(top) & np.isfinite(base) & ((top - base) > eps)
    cell_ok = ok[:-1, :-1] & ok[:-1, 1:] & ok[1:, 1:] & ok[1:, :-1]
    if not cell_ok.any():
        return None
    X, Y = np.meshgrid(xs, ys)
    n = ny * nx
    pts = np.vstack([np.column_stack([X.ravel(), Y.ravel(), (top * ve).ravel()]),
                     np.column_stack([X.ravel(), Y.ravel(), (base * ve).ravel()])]).astype(np.float32)
    J, I = np.nonzero(cell_ok)
    a = J * nx + I
    b, c, dd = a + 1, a + nx + 1, a + nx
    quads = [np.column_stack([a, b, c, dd]), np.column_stack([n + dd, n + c, n + b, n + a])]
    cp = np.pad(cell_ok, 1, constant_values=False)
    for missing, cols in ((~cp[J + 1, I], (a, dd)),        # west edge
                          (~cp[J + 1, I + 2], (b, c)),      # east edge
                          (~cp[J, I + 1], (a, b)),          # south edge
                          (~cp[J + 2, I + 1], (dd, c))):    # north edge
        if missing.any():
            p0, p1 = cols[0][missing], cols[1][missing]
            quads.append(np.column_stack([p0, p1, n + p1, n + p0]))
    q = np.vstack(quads).astype(np.int64)
    cells = np.column_stack([np.full(len(q), 4, dtype=np.int64), q]).ravel()
    vp = vtkPoints()
    vp.SetData(numpy_to_vtk(pts, deep=1))
    pd = vtkPolyData()
    pd.SetPoints(vp)
    ca = vtkCellArray()
    ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(cells, deep=1))
    pd.SetPolys(ca)
    if not normals:
        return pd
    nf = vtkPolyDataNormals()
    nf.SetInputData(pd)
    nf.SetFeatureAngle(50.0)
    nf.SplittingOn()
    nf.ConsistencyOn()
    nf.Update()
    return nf.GetOutput()


def make_block_actor(model, info, ve, nz=40, cuts=None, codes=None):
    """Block model: cells coloured by unit, following the topography. cut = fractions removed from the
    west, south and top, so the inside can be looked at."""
    from vtkmodules.vtkFiltersCore import vtkThreshold
    x0, y0, z0 = info["origin"]
    xs, ys, dx, dy = model["xs"], model["ys"], model["dx"], model["dy"]
    nx, ny = len(xs), len(ys)
    tops, units = model["tops"], model["units"]
    zb = model["zbot"]
    ztop = float(np.nanmax(tops[0]))
    nz = max(int(nz), 2)
    dz = (ztop - zb) / nz
    zc = zb + (np.arange(nz) + 0.5) * dz
    ids = np.zeros((nz, ny, nx), dtype=np.int32)
    for k in range(1, len(units)):
        ids += (zc[:, None, None] < tops[k][None, :, :]).astype(np.int32)
    codes = codes or {u: i + 1 for i, u in enumerate(sorted(units))}
    lut_idx = np.array([codes.get(u, 0) for u in units], dtype=np.float32)
    vals = lut_idx[ids]
    vals[zc[:, None, None] > tops[0][None, :, :]] = -1.0   # above the ground: no cell
    for axis, value, keep in (cuts or []):      # cutting planes, in real coordinates
        if axis == 0:
            vals[:, :, (xs < value) if keep > 0 else (xs > value)] = -1.0
        elif axis == 1:
            vals[:, (ys < value) if keep > 0 else (ys > value), :] = -1.0
        else:
            vals[(zc < value) if keep > 0 else (zc > value), :, :] = -1.0
    img = vtkImageData()
    img.SetDimensions(nx + 1, ny + 1, nz + 1)
    img.SetOrigin(xs[0] - dx / 2 - x0, ys[0] - dy / 2 - y0, (zb - z0) * ve)
    img.SetSpacing(dx, dy, dz * ve)
    arr = numpy_to_vtk(vals.ravel(), deep=1)
    arr.SetName("unit")
    img.GetCellData().SetScalars(arr)
    th = vtkThreshold()
    th.SetInputData(img)
    th.SetInputArrayToProcess(0, 0, 0, 1, "unit")
    th.SetLowerThreshold(-0.5)
    th.SetUpperThreshold(1e6)
    th.SetThresholdFunction(vtkThreshold.THRESHOLD_BETWEEN)
    th.Update()
    grid = th.GetOutput()
    for pl in _hull_planes(model, x0, y0):
        from vtkmodules.vtkFiltersGeneral import vtkClipDataSet
        cd = vtkClipDataSet()
        cd.SetInputData(grid)
        cd.SetClipFunction(pl)
        cd.Update()
        grid = cd.GetOutput()
    surf = vtkDataSetSurfaceFilter()
    surf.SetInputData(grid)
    surf.Update()
    n_codes = max(codes.values()) if codes else len(units)
    mp = vtkPolyDataMapper()
    mp.SetInputData(surf.GetOutput())
    mp.SetLookupTable(_lith_lut(codes))
    mp.SetScalarModeToUseCellData()
    mp.SelectColorArray("unit")
    mp.SetScalarRange(-0.5, n_codes + 0.5)
    mp.UseLookupTableScalarRangeOn()
    act = vtkActor()
    act.SetMapper(mp)
    return act


def add_model(ren, model, info, ve, nz=40, cuts=None, registry=None, smooth=0.0):
    """Draws the contact surfaces of a stratigraphic model and its block model. Returns notes."""
    if not info or "origin" not in info or model is None:
        return []
    x0, y0, z0 = info["origin"]
    codes = info.get("codes") or {u: i + 1 for i, u in enumerate(sorted(model["units"]))}
    xs, ys = model["xs"] - x0, model["ys"] - y0
    drawn = []
    # solid units: smooth closed bodies between each unit's top and base (padded out to the model box edges)
    units, tops, zbot = model["units"], model["tops"], model["zbot"]
    xe = np.concatenate([[model["xs"][0] - model["dx"] / 2], model["xs"], [model["xs"][-1] + model["dx"] / 2]]) - x0
    ye = np.concatenate([[model["ys"][0] - model["dy"] / 2], model["ys"], [model["ys"][-1] + model["dy"] / 2]]) - y0
    solids = 0
    hplanes = _hull_planes(model, x0, y0)
    fac = 3 if smooth and smooth > 0 else 1
    if fac > 1:
        from scipy import ndimage as _ndi
        xf = np.interp(np.linspace(0, len(xe) - 1, (len(xe) - 1) * fac + 1), np.arange(len(xe)), xe)
        yf = np.interp(np.linspace(0, len(ye) - 1, (len(ye) - 1) * fac + 1), np.arange(len(ye)), ye)
        sig = float(smooth) * fac

        def _refine(a):
            # one and the same smoothing for every surface keeps the layers in order (it is a weighted average)
            ny_, nx_ = a.shape
            jj, ii = np.meshgrid(np.linspace(0, ny_ - 1, (ny_ - 1) * fac + 1), np.linspace(0, nx_ - 1, (nx_ - 1) * fac + 1), indexing="ij")
            return _ndi.gaussian_filter(_ndi.map_coordinates(a, [jj, ii], order=1, mode="nearest"), sig, mode="nearest")
    for k, unit in enumerate(units):
        top = np.pad(tops[k] - z0, 1, mode="edge")
        base = np.pad((tops[k + 1] if k + 1 < len(units) else np.full_like(tops[k], zbot)) - z0, 1, mode="edge")
        gx, gy = xe, ye
        if fac > 1:
            top, base, gx, gy = _refine(top), _refine(base), xf, yf
        mesh = _solid_mesh(gx, gy, top, base, ve)
        if mesh is None:
            continue
        raw = _cut.prepare_closed(_solid_mesh(gx, gy, top, base, ve, normals=False))
        if hplanes:
            hs = _solid_hull(gx, gy, top, base, ve, hplanes)
            if hs is None:
                continue
            raw = _cut.prepare_closed(hs)
            mesh = _normals_of(raw)
        mp = vtkPolyDataMapper()
        mp.SetInputData(mesh)
        mp.ScalarVisibilityOff()
        act = vtkActor()
        act.SetMapper(mp)
        idx = codes.get(unit, 0)
        r, g, b = unit_colour(unit, codes)
        act.GetProperty().SetColor(r, g, b)
        act.GetProperty().SetSpecular(0.15)
        ren.AddActor(act)
        if registry is not None:
            registry.append({"name": "Solid: %s" % unit, "actors": [act], "fade": True, "closed": True, "raw": raw})
        solids += 1
    for k in range(1, len(model["units"])):
        unit = model["units"][k]
        quads = _grid_quads(xs, ys, model["tops"][k] - z0, ve, model["thick"][k] > 0.05)
        if quads is None:
            continue
        if hplanes:
            quads = _hull_clip_poly(quads, hplanes)
            if quads.GetNumberOfPoints() == 0:
                continue
        mp = vtkPolyDataMapper()
        mp.SetInputData(quads)
        mp.ScalarVisibilityOff()
        act = vtkActor()
        act.SetMapper(mp)
        idx = codes.get(unit, 0)
        r, g, b = unit_colour(unit, codes)
        act.GetProperty().SetColor(r, g, b)
        act.GetProperty().SetOpacity(0.65)
        ren.AddActor(act)
        if registry is not None:
            registry.append({"name": "Model contact: %s" % unit, "actors": [act], "fade": True, "default_visible": False})
        drawn.append(unit)
    block = make_block_actor(model, info, ve, nz, cuts, codes)
    ren.AddActor(block)
    if registry is not None:
        registry.append({"name": "Block model", "actors": [block], "fade": True, "default_visible": False})
    return ["Model: %d solid unit(s) drawn (contact surfaces and the block model are hidden until ticked)." % solids]



def _add_cloud(ren, pinfo, lut, cmin, cmax, base, fd, ve, smooth, density, registry, name="Plume cloud", levels=None):
    """Draws the plume as a smooth cloud (volume rendering): colour from the plume colour ramp, density rising with the
    concentration (log scale between the lowest level and the highest result), cut at the limits, optionally thinner far from the samples.
    Returns True when something was drawn."""
    from vtkmodules.vtkRenderingCore import vtkVolume, vtkVolumeProperty
    from vtkmodules.vtkRenderingVolumeOpenGL2 import vtkSmartVolumeMapper
    arrs = pinfo.get("arrays")
    if not arrs:
        return False
    xs, ys, zs, vals, allowed = arrs
    vals = np.asarray(vals, dtype=float)
    smooth = float(smooth or 0)
    if smooth > 0:
        from scipy import ndimage
        vals = ndimage.gaussian_filter(vals, sigma=0.4 + 0.12 * smooth)
    base = max(float(base), cmax * 1e-6)
    if cmax <= base:
        return False
    lv = sorted({float(x) for x in (levels or []) if cmax > float(x) > 0})
    if lv:
        # the shell levels set the cloud: nothing below the lowest level, then denser and denser at each higher level
        n = len(lv)
        xp = [lv[0] * 0.8] + lv + [cmax]
        fp = [0.0] + [0.30 + 0.65 * (k / max(n, 1)) ** 1.0 for k in range(n)] + [1.0]
        alpha = np.interp(vals, xp, fp)
    else:
        t = np.clip(np.log(np.maximum(vals, 1e-300) / base) / math.log(cmax / base), 0.0, 1.0)
        t[vals < base] = 0.0
        alpha = np.power(t, 0.85)
    if allowed is not None:
        alpha = np.where(np.asarray(allowed) > 0, alpha, 0.0)
    nz, ny, nx = vals.shape
    if fd is not None:
        from scipy.spatial import cKDTree
        Z, Y, X = np.meshgrid(zs, ys, xs, indexing="ij")
        G = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
        sc = np.array([1.0, 1.0, fd["zaniso"]])
        dist, _ = cKDTree(fd["samples"] * sc).query(G * sc)
        alpha = alpha * (1.0 - fd["strength"] * np.clip(dist / fd["ref"], 0.0, 1.0)).reshape(nz, ny, nx)
    alpha = np.clip(alpha * min(max(float(density), 0.05), 1.0), 0.0, 1.0)
    if not (alpha > 0.01).any():
        return False
    table = np.linspace(cmin, cmax, 256)
    cols = np.empty((256, 3))
    for i, v in enumerate(table):
        rgb = [0.0, 0.0, 0.0]
        lut.GetColor(float(v), rgb)
        cols[i] = rgb
    pos = np.clip((vals - cmin) / max(cmax - cmin, 1e-12), 0.0, 1.0) * 255.0
    rgba = np.empty((nz, ny, nx, 4), dtype=np.uint8)
    for k in range(3):
        rgba[..., k] = np.round(np.interp(pos, np.arange(256), cols[:, k]) * 255).astype(np.uint8)
    rgba[..., 3] = np.round(alpha * 255).astype(np.uint8)
    img = vtkImageData()
    img.SetDimensions(nx, ny, nz)
    img.SetOrigin(float(xs[0]), float(ys[0]), float(zs[0]) * ve)
    img.SetSpacing(float(xs[1] - xs[0]), float(ys[1] - ys[0]), float(zs[1] - zs[0]) * ve)
    arr = numpy_to_vtk(rgba.reshape(-1, 4), deep=1)
    arr.SetName("cloud")
    img.GetPointData().SetScalars(arr)
    mp = vtkSmartVolumeMapper()
    mp.SetInputData(img)
    prop = vtkVolumeProperty()
    prop.IndependentComponentsOff()
    from vtkmodules.vtkCommonDataModel import vtkPiecewiseFunction
    ramp = vtkPiecewiseFunction()                       # opacity of an RGBA voxel = its own alpha (the default function would make everything opaque)
    ramp.AddPoint(0.0, 0.0)
    ramp.AddPoint(255.0, 1.0)
    prop.SetScalarOpacity(ramp)
    prop.SetInterpolationTypeToLinear()
    prop.ShadeOff()
    cell = float(min(xs[1] - xs[0], ys[1] - ys[0]))
    prop.SetScalarOpacityUnitDistance(cell * 0.7)
    vol = vtkVolume()
    vol.SetMapper(mp)
    vol.SetProperty(prop)
    ren.AddVolume(vol)
    if registry is not None:
        registry.append({"name": name, "actors": [vol], "fade": False})
    return True


def add_boundary(ren, parts, surface, info, ve, o, registry=None):
    """Drapes outlines (list of (n, 2) arrays) over the DEM: every point gets the surface height (plus a small lift).
    Returns notes."""
    from scipy.interpolate import RegularGridInterpolator
    from vtkmodules.util.numpy_support import numpy_to_vtkIdTypeArray
    if not parts or surface is None or not info or "origin" not in info:
        return []
    x0, y0, z0 = info["origin"]
    sx, sy, sz = np.asarray(surface[0], float), np.asarray(surface[1], float), np.asarray(surface[2], float)
    if sz.shape != (len(sy), len(sx)) or len(sx) < 2 or len(sy) < 2:
        return []
    f = RegularGridInterpolator((sy, sx), sz, bounds_error=False, fill_value=np.nan, method="linear")
    step = max(min(abs(sx[1] - sx[0]), abs(sy[1] - sy[0])) / 2.0, 1e-6)
    lift = float(o.get("lift", 0.3))
    pts, cells, n_pts, n_lines = [], [], 0, 0
    for P in parts:
        seg = np.hypot(np.diff(P[:, 0]), np.diff(P[:, 1]))
        xs_, ys_ = [P[0, 0]], [P[0, 1]]
        for i, L in enumerate(seg):
            k = max(int(np.ceil(L / step)), 1)
            t = np.arange(1, k + 1) / k
            xs_.extend(P[i, 0] + (P[i + 1, 0] - P[i, 0]) * t)
            ys_.extend(P[i, 1] + (P[i + 1, 1] - P[i, 1]) * t)
        x, y = np.array(xs_), np.array(ys_)
        z = f(np.column_stack([y, x]))
        ok = np.isfinite(z)
        if ok.sum() < 2:
            continue
        xyz = np.column_stack([x - x0, y - y0, (z + lift - z0) * ve])
        idx = np.arange(len(x))
        for run in np.split(idx, np.nonzero(~ok)[0]):
            run = run[ok[run]] if len(run) else run
            if len(run) >= 2:
                cells.append(np.concatenate([[len(run)], run + n_pts]))
                n_lines += 1
        pts.append(xyz)
        n_pts += len(x)
    if not n_lines:
        return ["The site boundary does not overlap the DEM."]
    vp = vtkPoints()
    vp.SetData(numpy_to_vtk(np.vstack(pts).astype(np.float32), deep=1))
    pd = vtkPolyData()
    pd.SetPoints(vp)
    ca = vtkCellArray()
    ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(np.concatenate(cells).astype(np.int64), deep=1))
    pd.SetLines(ca)
    mp = vtkPolyDataMapper()
    mp.SetInputData(pd)
    mp.ScalarVisibilityOff()
    act = vtkActor()
    act.SetMapper(mp)
    pr = act.GetProperty()
    c = o.get("colour", (0.88, 0.1, 0.2))
    pr.SetColor(*c)
    pr.SetLineWidth(float(o.get("width", 3.0)))
    pr.SetLighting(False)
    try:
        pr.RenderLinesAsTubesOn()
    except Exception:
        _log.debug("tube rendering unavailable", exc_info=True)
    ren.AddActor(act)
    if registry is not None:
        registry.append({"name": "Site boundary", "actors": [act], "fade": False})
    return ["Site boundary draped on the DEM (%d line(s))." % n_lines]


def add_fence(ren, model, info, ve, polylines, registry=None):
    """Fence diagram: vertical panels of the model along one or more polylines (lists of (x, y) points).
    One coloured panel per lithology unit, plus a thin outline. Returns notes."""
    from . import section
    if not info or "origin" not in info or model is None or not polylines:
        return []
    x0, y0, z0 = info["origin"]
    codes = info.get("codes") or {u: i + 1 for i, u in enumerate(sorted(model["units"]))}
    units, zbot = model["units"], model["zbot"]
    n_units = len(units)
    step = max(min(model["dx"], model["dy"]) / 2.0, 0.5)
    pts = [[] for _ in range(n_units)]
    cells = [[] for _ in range(n_units)]
    counts = [0] * n_units
    ol_pts, ol_lines, ol_count = [], [], 0
    total_len, panels = 0.0, 0
    for line in polylines:
        P, seg, L, cum = section.line_geometry(line)
        total = float(cum[-1])
        if total <= 0:
            continue
        total_len += total
        n = max(int(total / step) + 1, 2)
        dist = np.unique(np.concatenate([np.linspace(0.0, total, n), cum]))
        if model.get("hull"):
            # add the exact points where the line crosses the outline, so the panels end on it
            from . import model as _m
            extra = []
            for p0, nrm in _m.hull_edges(model):
                f = (P[:, 0] - p0[0]) * nrm[0] + (P[:, 1] - p0[1]) * nrm[1]
                for i in range(len(P) - 1):
                    if f[i] * f[i + 1] < 0:
                        extra.append(cum[i] + (cum[i + 1] - cum[i]) * f[i] / (f[i] - f[i + 1]))
            if extra:
                dist = np.unique(np.concatenate([dist, np.array(extra)]))
        x = np.interp(dist, cum, P[:, 0])
        y = np.interp(dist, cum, P[:, 1])
        tops = section.sample_model(model, x, y)
        m = len(dist)
        for k in range(n_units):
            top = tops[k]
            base = tops[k + 1] if k + 1 < n_units else np.full(m, zbot)
            ok = np.isfinite(top) & np.isfinite(base) & ((top - base) > 0.02)
            xyz_top = np.column_stack([x - x0, y - y0, (np.nan_to_num(top) - z0) * ve])
            xyz_base = np.column_stack([x - x0, y - y0, (np.nan_to_num(base) - z0) * ve])
            pts[k].append(np.vstack([xyz_top, xyz_base]))
            idx = np.nonzero(ok[:-1] & ok[1:])[0]
            if len(idx):
                q = np.column_stack([idx, idx + 1, m + idx + 1, m + idx]) + counts[k]
                cells[k].append(q)
                panels += len(idx)
            counts[k] += 2 * m
        # outline: ground line, bottom line and the two end edges
        ground = tops[0]
        okg = np.isfinite(ground)
        if okg.any():
            gz = (ground - z0) * ve
            ol_pts.append(np.vstack([np.column_stack([x - x0, y - y0, gz]),
                                     np.column_stack([x - x0, y - y0, np.full(m, (zbot - z0) * ve)])]))
            for run in np.split(np.arange(m), np.nonzero(~okg)[0]):
                run = run[okg[run]] if len(run) else run
                if len(run) >= 2:
                    ol_lines.append(np.column_stack([np.full(len(run) - 1, 2), run[:-1], run[1:]]) + [0, ol_count, ol_count])
            ol_lines.append(np.column_stack([np.full(m - 1, 2), np.arange(m - 1), np.arange(1, m)]) * [1, 1, 1] + [0, ol_count + m, ol_count + m])
            for e in (0, m - 1):
                if okg[e]:
                    ol_lines.append(np.array([[2, ol_count + e, ol_count + m + e]]))
            ol_count += 2 * m
    if total_len <= 0 or panels == 0:
        return ["Fence: nothing to draw (the line lies outside the model, or the model has no thickness there)."]
    from vtkmodules.util.numpy_support import numpy_to_vtkIdTypeArray
    for k, unit in enumerate(units):
        if not cells[k]:
            continue
        P3 = np.vstack(pts[k]).astype(np.float32)
        q = np.vstack(cells[k]).astype(np.int64)
        flat = np.column_stack([np.full(len(q), 4, dtype=np.int64), q]).ravel()
        vp = vtkPoints()
        vp.SetData(numpy_to_vtk(P3, deep=1))
        pd = vtkPolyData()
        pd.SetPoints(vp)
        ca = vtkCellArray()
        ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(flat, deep=1))
        pd.SetPolys(ca)
        mp = vtkPolyDataMapper()
        mp.SetInputData(pd)
        mp.ScalarVisibilityOff()
        act = vtkActor()
        act.SetMapper(mp)
        idx = codes.get(unit, 0)
        r, g, b = unit_colour(unit, codes)
        act.GetProperty().SetColor(r, g, b)
        ren.AddActor(act)
        if registry is not None:
            registry.append({"name": "Fence: %s" % unit, "actors": [act], "fade": True})
    if ol_pts and ol_lines:
        from vtkmodules.util.numpy_support import numpy_to_vtkIdTypeArray as _ids
        vp = vtkPoints()
        vp.SetData(numpy_to_vtk(np.vstack(ol_pts).astype(np.float32), deep=1))
        pd = vtkPolyData()
        pd.SetPoints(vp)
        ca = vtkCellArray()
        ca.ImportLegacyFormat(_ids(np.vstack(ol_lines).astype(np.int64).ravel(), deep=1))
        pd.SetLines(ca)
        mp = vtkPolyDataMapper()
        mp.SetInputData(pd)
        mp.ScalarVisibilityOff()
        act = vtkActor()
        act.SetMapper(mp)
        act.GetProperty().SetColor(0.15, 0.15, 0.15)
        act.GetProperty().SetLineWidth(1.5)
        ren.AddActor(act)
        if registry is not None:
            registry.append({"name": "Fence outline", "actors": [act], "fade": False})
    return ["Fence: %d line(s), %.0f m in total." % (len([1 for ln in polylines if len(ln) > 1]), total_len)]



def _nice_step(span, target=8):
    raw = max(span, 1e-9) / target
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def groundwater_grid(gw, opts):
    """Water-level surface on a grid, in real coordinates. Returns (xs, ys, Z[ny, nx], notes)."""
    from . import model as strat
    gw = np.asarray(gw, dtype=float)
    n = int(opts.get("grid", 80))
    span = max(np.ptp(gw[:, 0]), np.ptp(gw[:, 1]), 1.0)
    m = max(0.0, float(opts.get("margin", 0.1))) * span
    xs = np.linspace(gw[:, 0].min() - m, gw[:, 0].max() + m, n)
    ys = np.linspace(gw[:, 1].min() - m, gw[:, 1].max() + m, n)
    X, Y = np.meshgrid(xs, ys)
    warn = set()
    Z = strat.interp2(gw, X, Y, opts.get("method", "kriging"), warn, "Groundwater surface")
    return xs, ys, Z, sorted("Groundwater: " + w for w in warn)


def add_groundwater(ren, gw, info, ve, opts, registry=None, fg=(0.1, 0.1, 0.1), dpr=1.0):
    """Groundwater surface from measured water levels, with contour lines and labels.
    gw: array (n, 3) of x, y, water-level elevation (real coordinates). Returns notes."""
    from . import model as strat
    notes = []
    if not info or "origin" not in info:
        return ["Groundwater: draw some boreholes, a plume or a DEM first, so the scene has a position."]
    gw = np.asarray(gw, dtype=float)
    if len(gw) < 3:
        return ["Groundwater: at least 3 wells with a water level are needed (found %d)." % len(gw)]
    x0, y0, z0 = info["origin"]
    gg = opts.get("grid_data")
    if gg is None:
        gg = groundwater_grid(gw, opts)
    xs, ys, Z, gnotes = gg
    notes += gnotes
    span = max(np.ptp(gw[:, 0]), np.ptp(gw[:, 1]), 1.0)
    # the surface
    quads = _grid_quads(xs - x0, ys - y0, Z - z0, ve, np.ones_like(Z, dtype=bool))
    if quads is None:
        return notes
    quads.GetPointData().SetScalars(numpy_to_vtk(np.asarray(Z.ravel(), dtype=np.float32), deep=1))
    quads.GetPointData().GetScalars().SetName("level")
    mp = vtkPolyDataMapper()
    mp.SetInputData(quads)
    mp.ScalarVisibilityOff()
    act = vtkActor()
    act.SetMapper(mp)
    act.GetProperty().SetColor(0.22, 0.52, 0.95)
    act.GetProperty().SetOpacity(0.5)
    ren.AddActor(act)
    if registry is not None:
        registry.append({"name": "Groundwater surface", "actors": [act], "fade": True})
    zmin, zmax = float(Z.min()), float(Z.max())
    notes.append("Groundwater surface from %d wells: level %.2f to %.2f m (%s)." % (len(gw), zmin, zmax, strat.METHODS.get(opts.get("method", "kriging"), "")))
    # contour lines
    step = float(opts.get("interval", 0) or 0) or _nice_step(zmax - zmin)
    levels = list(np.arange(math.ceil(zmin / step) * step, zmax + step * 1e-6, step))
    if not levels:
        notes.append("Groundwater: no contour line falls inside the water-level range (%.2f to %.2f m)." % (zmin, zmax))
        return notes
    cf = vtkContourFilter()
    cf.SetInputData(quads)
    for i, lv in enumerate(levels):
        cf.SetValue(i, float(lv))
    cf.ComputeScalarsOn()
    cf.Update()
    strip = vtkStripper()
    strip.SetInputData(cf.GetOutput())
    strip.JoinContiguousSegmentsOn()
    strip.Update()
    lines = strip.GetOutput()
    lift = 0.05 * ve
    P = vtk_to_numpy(lines.GetPoints().GetData()).astype(np.float64)
    P[:, 2] += lift
    pts = vtkPoints()
    pts.SetData(numpy_to_vtk(P.astype(np.float32), deep=1))
    ln = vtkPolyData()
    ln.SetPoints(pts)
    ln.SetLines(lines.GetLines())
    lm = vtkPolyDataMapper()
    lm.SetInputData(ln)
    lm.ScalarVisibilityOff()
    la = vtkActor()
    la.SetMapper(lm)
    la.GetProperty().SetColor(0.05, 0.25, 0.6)
    la.GetProperty().SetLineWidth(1.6)
    ren.AddActor(la)
    if registry is not None:
        registry.append({"name": "Groundwater contours", "actors": [la], "fade": False})
    notes.append("Groundwater contours every %g m (%d levels)." % (step, len(levels)))
    # labels along each contour
    conn = vtk_to_numpy(lines.GetLines().GetConnectivityArray()).astype(np.int64)
    off = vtk_to_numpy(lines.GetLines().GetOffsetsArray()).astype(np.int64)
    lvl_arr = vtk_to_numpy(lines.GetPointData().GetScalars()) if lines.GetPointData().GetScalars() is not None else None
    dec = 0 if step >= 1 else (1 if step >= 0.1 else 2)
    spacing = float(opts.get("label_spacing", 0) or 0) or max(span * 0.45, 20.0)
    size = int(round(float(opts.get("label_size", 13)) * max(dpr, 0.5)))
    labels, label_actors, angles = [], [], []
    for i in range(len(off) - 1):
        idx = conn[off[i]:off[i + 1]]
        if len(idx) < 2:
            continue
        Q = P[idx]
        seg = np.linalg.norm(np.diff(Q[:, :2], axis=0), axis=1)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        total = float(cum[-1])
        if total < 0.08 * span:
            continue
        level = float(lvl_arr[idx[0]]) if lvl_arr is not None else float(levels[0])
        fracs = [0.5]
        k = 1
        while 0.5 * total - k * spacing > 0.12 * total:
            fracs += [0.5 + k * spacing / total, 0.5 - k * spacing / total]
            k += 1
        for fr in fracs:
            j = int(np.searchsorted(cum, fr * total))
            j = min(max(j, 0), len(Q) - 1)
            a0, a1 = Q[max(j - 2, 0)], Q[min(j + 2, len(Q) - 1)]
            ang = math.degrees(math.atan2(a1[1] - a0[1], a1[0] - a0[0]))
            if ang > 90:
                ang -= 180
            elif ang <= -90:
                ang += 180                                  # keep the text readable, never upside down
            angles.append(ang)
            labels.append(("%.*f" % (dec, level), float(Q[j, 0]), float(Q[j, 1]), float(Q[j, 2])))
    flat = bool(opts.get("flat", True))
    world_h = float(opts.get("label_size", 13)) * span / 1000.0     # height of the text on the surface, in metres
    for (text, lx, ly, lz), ang in zip(labels, angles):
        if flat:
            ta = vtkTextActor3D()                           # lies on the surface, like writing on a map
            ta.SetInput(text)
            ta.SetScale(world_h / 48.0)
            ta.SetOrientation(0.0, 0.0, ang)
            ta.SetPosition(lx, ly, lz)
            tp = ta.GetTextProperty()
            tp.SetFontSize(48)
        else:
            ta = vtkBillboardTextActor3D()                  # always faces the camera
            ta.SetInput(text)
            ta.SetPosition(lx, ly, lz)
            tp = ta.GetTextProperty()
            tp.SetFontSize(size)
        tp.SetColor(0.02, 0.15, 0.5)
        tp.SetBold(True)
        tp.SetJustificationToCentered()
        tp.SetVerticalJustificationToCentered()
        tp.SetBackgroundColor(1.0, 1.0, 1.0)
        tp.SetBackgroundOpacity(0.7)
        ren.AddActor(ta)
        label_actors.append(ta)
    if label_actors and registry is not None:
        registry.append({"name": "Groundwater contour labels", "actors": label_actors, "fade": False, "labels3d": labels,
                         "flat": flat, "label_angles": angles, "label_h": world_h})
    notes.append("%d contour label(s)." % len(labels))
    return notes



LEGEND_POSITIONS = [("right", "Right, middle"), ("left", "Left, middle"), ("top_right", "Top right"), ("top_left", "Top left"),
                    ("bottom_right", "Bottom right"), ("bottom_left", "Bottom left"), ("top", "Top, centre"), ("bottom", "Bottom, centre")]


def _wrap_title(t):
    """A long title such as 'PFOS (mg/kg)' goes on two lines, so it fits inside the colour bar box."""
    t = str(t or "")
    return t.replace(" (", "\n(", 1) if len(t) > 11 and " (" in t and "\n" not in t else t


def style_plume_bar(bar, o, fg, dpr=1.0, title=None):
    """Size, shape, orientation, position and text of the plume colour bar (the legend of the samples and shells)."""
    o = o or {}
    vertical = o.get("orientation", "vertical") == "vertical"
    length = min(max(float(o.get("length", 50.0)), 10.0), 100.0) / 100.0
    thick = min(max(float(o.get("thickness", 3.0)), 1.0), 15.0) / 100.0
    big = 0.10                                                       # room for the title and the numbers
    W, H = (thick + big, length) if vertical else (length, thick + big + 0.02)
    m = 0.02
    pos = o.get("position", "right")
    x = {"right": 1 - m - W, "top_right": 1 - m - W, "bottom_right": 1 - m - W, "left": m, "top_left": m, "bottom_left": m}.get(pos, 0.5 - W / 2)
    y = {"top": 1 - m - H, "top_left": 1 - m - H, "top_right": 1 - m - H, "bottom": m, "bottom_left": m, "bottom_right": m}.get(pos, 0.5 - H / 2)
    if vertical:
        bar.SetOrientationToVertical()
    else:
        bar.SetOrientationToHorizontal()
    bar.SetPosition(max(min(x, 1 - W), 0.0), max(min(y, 1 - H), 0.0))
    bar.SetWidth(W)
    bar.SetHeight(H)
    bar.SetBarRatio(min(0.9, thick / (W if vertical else H)))
    bar.SetNumberOfLabels(int(o.get("labels", 5)))
    dec = int(o.get("decimals", -1))
    bar.SetLabelFormat("%-#6.3g" if dec < 0 else "%." + str(dec) + "f")
    steps = int(o.get("steps", 0))
    bar.SetMaximumNumberOfColors(steps if steps >= 2 else 256)
    if title is not None:
        bar.SetTitle(_wrap_title(title))
    custom = o.get("mode") == "custom"
    rgb = tuple(o.get("colour", (0, 0, 0))) if custom else tuple(fg)
    fam = o.get("font", "arial")
    bar.UnconstrainedFontSizeOn()
    for tp, size in ((bar.GetTitleTextProperty(), o.get("title_size", 12)), (bar.GetLabelTextProperty(), o.get("label_size", 11))):
        (tp.SetFontFamilyToTimes if fam == "times" else tp.SetFontFamilyToCourier if fam == "courier" else tp.SetFontFamilyToArial)()
        tp.SetFontSize(int(round(float(size) * max(dpr, 0.5))))
        tp.SetColor(*rgb)
        tp.SetBold(bool(o.get("bold", False)))
        tp.SetItalic(bool(o.get("italic", False)))
        tp.SetShadow(0)
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    bg = (1.0, 1.0, 1.0) if lum < 0.5 else (0.05, 0.06, 0.08)
    bar.SetDrawBackground(1 if o.get("background", False) else 0)
    bar.GetBackgroundProperty().SetColor(*bg)
    bar.GetBackgroundProperty().SetOpacity(0.7)
    bar.SetDrawFrame(1 if o.get("border", False) else 0)
    bar.GetFrameProperty().SetColor(*rgb)


def theme_fg(theme):
    return (0.08, 0.08, 0.10) if theme == "light" else (1.0, 1.0, 1.0)


_FONTS_CSS = {"arial": "Arial, Helvetica, sans-serif", "times": "'Times New Roman', Times, serif", "courier": "'Courier New', Courier, monospace"}


def restyle_sample_labels(entry, o, fg, dpr=1.0):
    """Applies text, font, size and colour to the result labels already in the scene (no rebuild needed)."""
    dec = int(o.get("decimals", 2))
    custom = o.get("mode") == "custom"
    rgb = tuple(o.get("colour", (0, 0, 0))) if custom else tuple(fg)
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    bg = (1.0, 1.0, 1.0) if lum < 0.5 else (0.05, 0.06, 0.08)
    size = int(round(float(o.get("size", 11)) * max(dpr, 0.5)))
    off = 12.0 * float(o.get("offset", 1.0))          # pixels away from the sample, diagonally up and to the right
    dxy = int(round(0.7071 * off * max(dpr, 0.5)))
    fam = o.get("font", "arial")
    texts = []
    for ta, (val, nd) in zip(entry["actors"], entry["label_vals"]):
        text = "ND" if nd else "%.*f" % (dec, val)
        ta.SetInput(text)
        ta.SetDisplayOffset(dxy, dxy)
        tp = ta.GetTextProperty()
        (tp.SetFontFamilyToTimes if fam == "times" else tp.SetFontFamilyToCourier if fam == "courier" else tp.SetFontFamilyToArial)()
        tp.SetFontSize(size)
        tp.SetColor(*rgb)
        tp.SetBold(bool(o.get("bold", False)))
        tp.SetItalic(bool(o.get("italic", False)))
        tp.SetJustificationToLeft()
        tp.SetVerticalJustificationToCentered()
        tp.SetBackgroundColor(*bg)
        tp.SetBackgroundOpacity(0.6 if o.get("background", True) else 0.0)
        texts.append(text)
    entry["labels3d"] = [(t, x, y, z) for t, (_, x, y, z) in zip(texts, entry["labels3d"])]
    entry["declutter"] = bool(o.get("declutter", True))
    entry["size_px"] = size
    entry["off_px"] = dxy
    entry["label_style"] = {"auto": not custom, "color": "#%02x%02x%02x" % tuple(int(round(c * 255)) for c in rgb),
                            "size": float(o.get("size", 11)), "font": _FONTS_CSS.get(fam, _FONTS_CSS["arial"]),
                            "bold": bool(o.get("bold", False)), "italic": bool(o.get("italic", False)),
                            "background": bool(o.get("background", True)), "offset": round(0.7071 * off, 1)}


def restyle_hole_labels(entry, o, fg, dpr=1.0):
    """Applies font, size, colour and background to the borehole names already in the scene (no rebuild needed)."""
    custom = o.get("mode") == "custom"
    rgb = tuple(o.get("colour", (0, 0, 0))) if custom else tuple(fg)
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    bg = (1.0, 1.0, 1.0) if lum < 0.5 else (0.05, 0.06, 0.08)
    size = int(round(float(o.get("size", 13)) * max(dpr, 0.5)))
    sc = max(dpr, 0.5)
    offx = int(round(float(o.get("dx", 6.0)) * sc))
    offy = int(round(float(o.get("dy", 6.0)) * sc))
    fam = o.get("font", "arial")
    for ta in entry.get("actors") or []:
        ta.SetDisplayOffset(offx, offy)
        tp = ta.GetTextProperty()
        (tp.SetFontFamilyToTimes if fam == "times" else tp.SetFontFamilyToCourier if fam == "courier" else tp.SetFontFamilyToArial)()
        tp.SetFontSize(size)
        tp.SetColor(*rgb)
        tp.SetBold(bool(o.get("bold", True)))
        tp.SetItalic(bool(o.get("italic", False)))
        tp.SetBackgroundColor(*bg)
        tp.SetBackgroundOpacity(0.45 if o.get("background", True) else 0.0)
    entry["declutter"] = bool(o.get("declutter", True))
    entry["size_px"] = size
    entry["off_px"] = offx
    entry["off_x_px"], entry["off_y_px"] = offx, offy


def _declutter(entry):
    """Hides result labels that would sit on top of a label with a higher result, so the picture stays readable.
    Runs before every render; the labels come back when you zoom in or turn the view."""
    ren, acts = entry.get("ren"), entry.get("actors") or []
    try:
        if ren is None or not acts:
            return
        if not ren.HasViewProp(acts[0]):                 # the scene was redrawn: this entry is gone
            tag = entry.pop("dtag", None)
            if tag is not None:
                ren.RemoveObserver(tag)
            return
        if not entry.get("user_visible", True):          # the layer is switched off by the user
            return
        hid = entry.get("cut_hidden") or ()
        if not entry.get("declutter", True) or len(acts) > 600:
            for i, a in enumerate(acts):
                a.SetVisibility(0 if i in hid else 1)
            return
        size, off = float(entry.get("size_px", 12)), float(entry.get("off_px", 8))
        offx, offy = float(entry.get("off_x_px", off)), float(entry.get("off_y_px", off))
        vals = entry.get("label_vals") or []
        order = sorted(range(len(acts)), key=lambda i: -(vals[i][0] if i < len(vals) and not vals[i][1] else -1.0))
        kept = []
        for i in order:
            a = acts[i]
            if i in hid:                                 # on the cut-away side
                a.SetVisibility(0)
                continue
            x, y, z = a.GetPosition()
            ren.SetWorldPoint(x, y, z, 1.0)
            ren.WorldToDisplay()
            dx, dy, _ = ren.GetDisplayPoint()
            w, h = 0.62 * size * max(len(a.GetInput() or ""), 1) + 8.0, 1.5 * size
            box = (dx + offx, dy + offy - h / 2.0, dx + offx + w, dy + offy + h / 2.0)
            hit = any(box[0] < k[2] and box[2] > k[0] and box[1] < k[3] and box[3] > k[1] for k in kept)
            a.SetVisibility(0 if hit else 1)
            if not hit:
                kept.append(box)
    except Exception:
        _log.debug("could not tidy the result labels", exc_info=True)


def add_sample_labels(ren, pl, nd, ve, o, registry, fg, dpr=1.0, name="Plume sample labels"):
    """One text label with the analytical result next to every plume sample.
    pl: (n, 4) shifted x, y, z, concentration. nd: list of non-detect flags (or None)."""
    nd = list(nd) if nd is not None and len(nd) == len(pl) else [False] * len(pl)
    acts, l3d, vals = [], [], []
    for (x, y, z, c), flag in zip(pl, nd):
        ta = vtkBillboardTextActor3D()
        ta.SetPosition(float(x), float(y), float(z * ve))
        ta.SetInput(" ")
        ren.AddActor(ta)
        acts.append(ta)
        l3d.append(("", float(x), float(y), float(z * ve)))
        vals.append((float(c), bool(flag)))
    entry = {"name": name, "actors": acts, "fade": False, "labels3d": l3d, "label_vals": vals, "ren": ren}
    restyle_sample_labels(entry, o, fg, dpr)
    entry["dtag"] = ren.AddObserver("StartEvent", lambda *a, e=entry: _declutter(e))
    if registry is not None:
        registry.append(entry)
    return entry


def add_gw_plume(ren, rows, nd, info, ve, o, registry, fg, dpr=1.0):
    """Groundwater results from screened intervals: a tube for every screen (top to bottom of the screen, coloured by the
    result), the interpolated plume as nested shells, result labels and its own colour bar.
    rows: [(x, y, z_top, z_bottom, conc, id)] in real coordinates (elevations). o: options of the groundwater plume."""
    notes = []
    if not rows:
        return ["Groundwater plume: no screen results could be read (check the fields and that every hole is in the collar layer)."]
    x0, y0, z0 = info["origin"]
    arr = np.array([(r[0] - x0, r[1] - y0, r[2] - z0, r[3] - z0, r[4]) for r in rows], dtype=float)
    cmin, cmax = float(arr[:, 4].min()), float(arr[:, 4].max())
    ramp_key = norm_ramp(o.get("ramp"))
    info["gwp_ramp"] = ramp_key
    info["plume_ramp"] = ramp_key
    lut = plume_lut(ramp_key, cmin, cmax)
    # tubes
    pts, lines, sc = vtkPoints(), vtkCellArray(), vtkFloatArray()
    sc.SetName("conc")
    for k, (x, y, zt, zb, c) in enumerate(arr):
        pts.InsertNextPoint(x, y, zt * ve)
        pts.InsertNextPoint(x, y, zb * ve)
        lines.InsertNextCell(2)
        lines.InsertCellPoint(2 * k)
        lines.InsertCellPoint(2 * k + 1)
        sc.InsertNextValue(c)
        sc.InsertNextValue(c)
    pd = vtkPolyData()
    pd.SetPoints(pts)
    pd.SetLines(lines)
    pd.GetPointData().SetScalars(sc)
    tube = vtkTubeFilter()
    tube.SetInputData(pd)
    tube.SetRadius(float(info.get("auto_r", 1.0)) * 1.8 * float(o.get("tube_scale", 1.0)))
    tube.SetNumberOfSides(18)
    tube.CappingOn()
    tube.Update()
    tm = vtkPolyDataMapper()
    tm.SetInputData(tube.GetOutput())
    tm.SetLookupTable(lut)
    tm.SetScalarRange(cmin, cmax)
    tm.UseLookupTableScalarRangeOn()
    ta = vtkActor()
    ta.SetMapper(tm)
    ren.AddActor(ta)
    if registry is not None:
        registry.append({"name": "Plume screen tubes", "actors": [ta], "fade": True})
    info["gwp_tubes"] = [(float(a), float(b), float(c), float(d_), float(e)) for a, b, c, d_, e in arr]
    # colour bar
    bar = vtkScalarBarActor()
    bar.SetLookupTable(lut)
    bar.SetTitle(_wrap_title(o.get("title") or "Concentration"))
    style_plume_bar(bar, o.get("legend"), fg, dpr, o.get("title"))
    ren.AddViewProp(bar)
    if registry is not None:
        registry.append({"name": "Plume scale bar", "actors": [bar], "fade": False})
    # labels at the middle of every screen
    mid = np.column_stack([arr[:, 0], arr[:, 1], 0.5 * (arr[:, 2] + arr[:, 3]), arr[:, 4]])
    if o.get("labels") is not None:
        add_sample_labels(ren, mid, nd, ve, o["labels"], registry, fg, dpr, name="Plume sample labels")
    # points for the estimate: along every screen, about one per metre
    P = []
    for x, y, zt, zb, c in arr:
        n = max(2, int(round(abs(zt - zb))) + 1)
        for z in np.linspace(zt, zb, n):
            P.append((x, y, z, c))
    P = np.array(P)
    water = ground = hull = None
    if o.get("water") is not None:
        wx, wy, wz = o["water"]
        water = (np.asarray(wx) - x0, np.asarray(wy) - y0, np.asarray(wz) - z0)
    if o.get("ground") is not None:
        gx, gy, gz = o["ground"]
        ground = (np.asarray(gx) - x0, np.asarray(gy) - y0, np.asarray(gz) - z0)
    bottom = None
    if o.get("bottom") is not None:
        bx, by, bz = o["bottom"]
        bottom = (np.asarray(bx) - x0, np.asarray(by) - y0, np.asarray(bz) - z0)
    if o.get("hull") is not None:
        hull = {"xy": np.asarray(o["hull"]["xy"], dtype=float) - np.array([x0, y0]), "buffer": float(o["hull"].get("buffer", 0.0))}
    shells = o.get("shells") or {}
    levels = shell_levels(cmax, o.get("base", 0.0), shells.get("mult"), shells.get("absolute")) if shells.get("on") else []
    if not shells.get("on"):
        levels = [o.get("base") if o.get("base", 0) > 0 else 0.25 * cmax]
    if shells.get("on") and shells.get("absolute"):
        over = sorted(float(m) for m in (shells.get("mult") or []) if float(m) >= cmax * 0.999)
        if over:
            notes.append("Groundwater shell level(s) %s left out: they are above the highest result (%.4g)." % (", ".join("%.4g" % m for m in over), cmax))
    img, pinfo = _plume_grid(P, ve, o.get("influence", 0.0), o.get("method", "kriging"), o.get("detail", 48), o.get("zaniso", 3.0),
                             ground, water, hull, water_below=True, bottom=bottom)
    _keep_volume(info, "gw", pinfo, cmax)
    fd = _fade_ctx(o.get("fade"), pinfo, o.get("influence", 0.0), P, ve, o.get("zaniso", 3.0))
    if fd:
        notes.append(fd["note"])
    nt = _plume_method_note(pinfo, o.get("method", "kriging"), dict(o, water=None))
    nt = "Groundwater " + (nt[0].lower() + nt[1:])
    notes.append(nt[:-1] + (", kept below the groundwater surface" if water is not None else "")
                 + (", cut at the bottom of the wells" if bottom is not None else "") + ".")
    if o.get("display") == "cloud":
        base = o.get("base", 0.0) if o.get("base", 0.0) > 0 else 0.05 * cmax
        if _add_cloud(ren, pinfo, lut, cmin, cmax, base, fd, ve, o.get("smooth", 0), o.get("density", 0.8), registry, "Groundwater plume cloud", levels=levels):
            notes.append("Groundwater plume cloud from %.4g (faint) to %.4g (dense), data range %.4g to %.4g." % (base, cmax, cmin, cmax))
        else:
            notes.append("No groundwater plume cloud could be drawn above %.4g (data range %.4g to %.4g)." % (base, cmin, cmax))
        return notes
    drawn = []
    for j, level in enumerate(levels):
        poly, prgb = _shell_build(img, pinfo, level, levels[j + 1] if (shells.get("on") and j + 1 < len(levels)) else None, o.get("smooth", 0), ve, lut, cmin, cmax,
                                  o.get("cap", True), o.get("cutcol", True))
        if poly.GetNumberOfCells() == 0:
            continue
        rgb = [0.0, 0.0, 0.0]
        lut.GetColor(float(min(max(level, cmin), cmax)), rgb)
        sm = vtkPolyDataMapper()
        sm.SetInputData(poly)
        _apply_fade(sm, poly, rgb, fd, prgb)
        sa = vtkActor()
        sa.SetMapper(sm)
        sa.GetProperty().SetColor(*rgb)
        sa.GetProperty().SetOpacity(0.22 + 0.38 * j / max(len(levels) - 1, 1) if shells.get("on") else 0.45)
        ren.AddActor(sa)
        if registry is not None:
            registry.append({"name": ("Plume shell >= %.4g" % level) if shells.get("on") else "Plume isosurface",
                             "actors": [sa], "fade": True})
        drawn.append(level)
    if drawn:
        notes.append("Groundwater plume at %s (data range %.4g to %.4g)." % (", ".join("%.4g" % v for v in drawn), cmin, cmax))
    else:
        notes.append("No groundwater plume shell could be drawn at %s (data range %.4g to %.4g)." % (", ".join("%.4g" % v for v in levels), cmin, cmax))
    return notes
