import numpy as np
from qgis.core import NULL, QgsRectangle


def num(v):
    try:
        if v is None or v == NULL:
            return None
        return float(v)
    except Exception:
        return None


def point_of(feature):
    g = feature.geometry()
    if g is None or g.isNull():
        return None
    try:
        p = g.asMultiPoint()[0] if g.isMultipart() else g.asPoint()
        return float(p.x()), float(p.y())
    except Exception:
        return None


def read_collars(layer, f_id, f_z, f_depth):
    """Returns {hole_id: [x, y, ground_z, total_depth_or_None]}"""
    out = {}
    for f in layer.getFeatures():
        xy = point_of(f)
        if xy is None:
            continue
        z = num(f[f_z]) if f_z else None
        d = num(f[f_depth]) if f_depth else None
        out[str(f[f_id])] = [xy[0], xy[1], 0.0 if z is None else z, d]
    return out


def read_intervals(layer, f_id, f_from, f_to, f_code):
    """Returns {hole_id: [(from, to, code), ...]} sorted by from"""
    out = {}
    for f in layer.getFeatures():
        a, b = num(f[f_from]), num(f[f_to])
        if a is None or b is None:
            continue
        code = str(f[f_code]) if f_code else ""
        out.setdefault(str(f[f_id]), []).append((a, b, code))
    for k in out:
        out[k].sort(key=lambda s: s[0])
    return out


def read_plume(layer, f_z, f_c):
    """Returns array (n, 4): x, y, z (elevation), concentration"""
    pts = []
    for f in layer.getFeatures():
        xy = point_of(f)
        z, c = num(f[f_z]), num(f[f_c])
        if xy is None or z is None or c is None:
            continue
        pts.append((xy[0], xy[1], z, c))
    return np.array(pts, dtype=float) if pts else np.zeros((0, 4))


def read_raster(rl, bbox=None, maxn=120):
    """Samples band 1 on a coarse grid. Returns (xs, ys, z[ny, nx]) with NaN for nodata."""
    ext = rl.extent()
    if bbox is not None:
        ext = ext.intersect(QgsRectangle(*bbox))
    if ext.isNull() or ext.isEmpty():
        return None
    step = max(ext.width(), ext.height()) / maxn
    nx = max(2, int(round(ext.width() / step)))
    ny = max(2, int(round(ext.height() / step)))
    blk = rl.dataProvider().block(1, ext, nx, ny)
    z = np.full((ny, nx), np.nan)
    for r in range(ny):
        for c in range(nx):
            if not blk.isNoData(r, c):
                z[ny - 1 - r, c] = blk.value(r, c)
    xs = ext.xMinimum() + (np.arange(nx) + 0.5) * ext.width() / nx
    ys = ext.yMinimum() + (np.arange(ny) + 0.5) * ext.height() / ny
    return xs, ys, z


def read_groundwater(layer, f_elev, f_depth, f_id="", collars=None, ground_at=None):
    """Groundwater level points -> array (n, 3): x, y, water-level elevation.
    Uses the water-level elevation field, or the depth to water below the ground (ground level taken from the
    collar with the same hole ID, or from `ground_at(x, y)`)."""
    out = []
    for f in layer.getFeatures():
        xy = point_of(f)
        if xy is None:
            continue
        z = num(f[f_elev]) if f_elev else None
        if z is None and f_depth:
            dep = num(f[f_depth])
            if dep is None:
                continue
            ground = None
            if f_id and collars:
                c = collars.get(str(f[f_id]))
                ground = c[2] if c else None
            if ground is None and ground_at is not None:
                ground = ground_at(xy[0], xy[1])
            if ground is None:
                continue
            z = ground - dep
        if z is not None:
            out.append((xy[0], xy[1], z))
    return np.array(out, dtype=float) if out else np.zeros((0, 3))


def read_plume_nd(layer, f_z, f_c):
    """True for each plume row that read_plume keeps and that is a non-detect (flag / detected column)."""
    names = [f.name() for f in layer.fields()]
    col = next((n for n in names if n.lower() in ("flag", "detected")), None)
    out = []
    for f in layer.getFeatures():
        xy = point_of(f)
        z, c = num(f[f_z]), num(f[f_c])
        if xy is None or z is None or c is None:
            continue
        v = str(f[col]).strip().lower() if col else ""
        out.append(v.startswith("nd") or v.startswith("no") or v.startswith("<"))
    return out


def read_gw_results(layer, f_id, f_top, f_bot, f_conc, collars):
    """Groundwater results from screened intervals. Depths are metres below the ground at the collar.
    Returns (rows, nd, skipped): rows = [(x, y, z_top, z_bottom, conc, hole_id)] in elevations, nd = non-detect flags."""
    names = [f.name() for f in layer.fields()]
    col = next((n for n in names if n.lower() in ("flag", "detected")), None)
    rows, nd, skipped = [], [], 0
    for f in layer.getFeatures():
        hid = str(f[f_id]).strip() if f_id else ""
        top, bot, c = num(f[f_top]), num(f[f_bot]), num(f[f_conc])
        xy = point_of(f)
        if hid in collars:
            xy = (collars[hid][0], collars[hid][1]) if xy is None else xy
            gz = collars[hid][2]
        else:
            gz = None
        if xy is None or gz is None or top is None or bot is None or c is None:
            skipped += 1
            continue
        if bot < top:
            top, bot = bot, top
        v = str(f[col]).strip().lower() if col else ""
        rows.append((xy[0], xy[1], gz - top, gz - bot, c, hid))
        nd.append(v.startswith("nd") or v.startswith("no") or v.startswith("<"))
    return rows, nd, skipped


def read_boundary(layer, dest_crs=None):
    """Outlines of a polygon or line layer as a list of (n, 2) arrays (polygon rings and lines), in dest_crs."""
    from qgis.core import Qgis, QgsCoordinateTransform, QgsGeometry, QgsProject
    tr = None
    if dest_crs is not None and dest_crs.isValid() and layer.crs().isValid() and layer.crs() != dest_crs:
        tr = QgsCoordinateTransform(layer.crs(), dest_crs, QgsProject.instance())
    parts = []
    for f in layer.getFeatures():
        g = f.geometry()
        if g is None or g.isNull() or g.isEmpty():
            continue
        if tr is not None:
            g = QgsGeometry(g)
            if g.transform(tr) != Qgis.GeometryOperationResult.Success:
                continue
        gt = g.type()
        if gt == Qgis.GeometryType.Polygon:
            polys = g.asMultiPolygon() if g.isMultipart() else [g.asPolygon()]
            rings = [r for poly in polys for r in poly]
        elif gt == Qgis.GeometryType.Line:
            rings = g.asMultiPolyline() if g.isMultipart() else [g.asPolyline()]
        else:
            continue
        for r in rings:
            if len(r) >= 2:
                parts.append(np.array([[p.x(), p.y()] for p in r], dtype=float))
    return parts
