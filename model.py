"""Lightweight stratigraphic model: units stacked in chronological order, with thickness interpolated between boreholes."""
import numpy as np


METHODS = {
    "linear": "Linear (triangles)",
    "smooth": "Smooth (thin-plate spline)",
    "idw": "Inverse distance",
    "kriging": "Kriging (ordinary)",
}


def _idw(P, X, Y, k=6, power=2.0):
    from scipy.spatial import cKDTree
    pts, vals = P[:, :2], P[:, 2]
    k = min(k, len(pts))
    q = np.column_stack([X.ravel(), Y.ravel()])
    d, i = cKDTree(pts).query(q, k=k)
    d, i = d.reshape(len(q), -1), i.reshape(len(q), -1)
    w = 1.0 / np.maximum(d, 1e-6) ** power
    return ((w * vals[i]).sum(axis=1) / w.sum(axis=1)).reshape(X.shape)


def _linear(P, X, Y):
    from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
    pts, vals = P[:, :2], P[:, 2]
    nn = NearestNDInterpolator(pts, vals)(X, Y)
    try:
        lin = LinearNDInterpolator(pts, vals)(X, Y)
        return np.where(np.isnan(lin), nn, lin)
    except Exception:  # points in a straight line
        return _idw(P, X, Y)


def _rbf(P, X, Y, warn, label):
    """Smooth surface through the points (thin-plate spline), limited to a sensible range."""
    from scipy.interpolate import RBFInterpolator
    pts, vals = P[:, :2], P[:, 2]
    c = pts.mean(axis=0)
    sc = max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1]), 1.0)
    q = np.column_stack([X.ravel(), Y.ravel()])
    try:
        rbf = RBFInterpolator((pts - c) / sc, vals, kernel="thin_plate_spline", degree=1, smoothing=0.0,
                              neighbors=None if len(pts) <= 400 else 100)
        z = rbf((q - c) / sc).reshape(X.shape)
    except Exception:
        warn.add("%s: smooth interpolation was not possible here (too few points or in a line), linear used." % label)
        return None
    rng = np.ptp(vals) or 1.0
    return np.clip(z, vals.min() - 0.5 * rng, vals.max() + 0.5 * rng)


def _spherical(h, nug, sill, rng):
    h = np.asarray(h, dtype=float)
    g = np.where(h < rng, nug + sill * (1.5 * h / rng - 0.5 * (h / rng) ** 3), nug + sill)
    return np.where(h <= 0, 0.0, g)


def _fit_variogram(pts, vals):
    from scipy.spatial.distance import pdist
    from scipy.optimize import curve_fit
    d = pdist(pts)
    g = 0.5 * pdist(vals[:, None], "sqeuclidean")
    dmax = d.max()
    if dmax <= 0:
        return None
    edges = np.linspace(0.0, 0.6 * dmax, 9)
    H, G = [], []
    for i in range(8):
        m = (d > edges[i]) & (d <= edges[i + 1])
        if m.sum() >= 2:
            H.append(d[m].mean())
            G.append(g[m].mean())
    if len(H) < 3:
        return None
    var = max(float(np.var(vals)), 1e-12)
    try:
        popt, _ = curve_fit(_spherical, np.array(H), np.array(G), p0=[0.1 * var, var, 0.5 * dmax],
                            bounds=([0.0, 1e-12, 0.05 * dmax], [var, 3.0 * var, dmax]), maxfev=5000)
        return popt
    except Exception:
        return None


def _krige(P, X, Y, warn, label):
    """Ordinary kriging with a spherical variogram fitted to the points (12 nearest points per cell)."""
    from scipy.spatial import cKDTree
    pts, vals = P[:, :2], P[:, 2]
    n = len(pts)
    if n < 6:
        warn.add("%s: kriging needs at least 6 boreholes (found %d), linear used." % (label, n))
        return None
    popt = _fit_variogram(pts, vals)
    if popt is None:
        warn.add("%s: no usable variogram could be fitted, linear used." % label)
        return None
    nug, sill, rng = popt
    k = min(n, 12)
    q = np.column_stack([X.ravel(), Y.ravel()])
    d, idx = cKDTree(pts).query(q, k=k)
    C = pts[idx]
    dd = np.linalg.norm(C[:, :, None, :] - C[:, None, :, :], axis=-1)
    m = len(q)
    A = np.zeros((m, k + 1, k + 1))
    A[:, :k, :k] = _spherical(dd, nug, sill, rng) + np.eye(k)[None] * (1e-9 * sill)
    A[:, :k, k] = 1.0
    A[:, k, :k] = 1.0
    b = np.ones((m, k + 1))
    b[:, :k] = _spherical(d, nug, sill, rng)
    try:
        sol = np.linalg.solve(A, b[:, :, None])[:, :, 0]
    except np.linalg.LinAlgError:
        warn.add("%s: the kriging equations could not be solved, linear used." % label)
        return None
    z = (sol[:, :k] * vals[idx]).sum(axis=1).reshape(X.shape)
    r = np.ptp(vals) or 1.0
    return np.clip(z, vals.min() - 0.25 * r, vals.max() + 0.25 * r)


def interp2(P, X, Y, method="linear", warn=None, label=""):
    """P: array (n, 3) of x, y, value. method: linear / smooth / idw / kriging. Falls back to simpler
    methods when there are too few points; messages go into the set `warn`."""
    warn = warn if warn is not None else set()
    P = np.asarray(P, dtype=float)
    if len(P) == 0:
        return np.zeros_like(X)
    if np.ptp(P[:, 2]) < 1e-9:
        return np.full_like(X, P[0, 2], dtype=float)
    if len(P) < 3:
        if method != "linear":
            warn.add("%s: fewer than 3 boreholes, inverse distance used." % label)
        return _idw(P, X, Y)
    z = None
    if method == "smooth":
        z = _rbf(P, X, Y, warn, label)
    elif method == "kriging":
        z = _krige(P, X, Y, warn, label)
    elif method == "idw":
        z = _idw(P, X, Y, k=12)
    return z if z is not None else _linear(P, X, Y)


def default_order(collars, intervals):
    """Unit names from top to bottom, by the average elevation of their tops."""
    tops = {}
    for h, (x, y, z, d) in collars.items():
        seen = set()
        for a, b, code in intervals.get(h, []):
            if code in seen:
                continue
            seen.add(code)
            tops.setdefault(code, []).append(z - a)
    return sorted(tops, key=lambda u: -float(np.mean(tops[u])))


def build_model(collars, intervals, order, nx=50, ny=50, margin=0.1, method="linear"):
    """Returns (model dict or None, list of notes).

    collars: {hole: (x, y, ground_z, depth_or_None)}, intervals: {hole: [(from, to, unit)]}, order: top to bottom.
    Unit tops are stacked down from the ground surface using interpolated thicknesses, so units never cross,
    follow the order given, and can pinch out (a unit missing in a hole that goes deeper has zero thickness there).
    """
    notes = []
    warn = set()
    method = method if method in METHODS else "linear"
    idx = {u: i for i, u in enumerate(order)}
    holes, late, flipped, unknown = [], [], [], set()
    for h, (x, y, z, depth) in collars.items():
        segs = sorted(intervals.get(h, []), key=lambda s: s[0])
        for a, b, c in segs:
            if c not in idx:
                unknown.add(c)
        segs = [(a, b, c) for a, b, c in segs if c in idx and b > a]
        if not segs:
            continue
        if segs[0][0] > 1e-6:
            late.append(h)
            segs[0] = (0.0, segs[0][1], segs[0][2])
        tops, seq = {}, []
        for a, b, c in segs:
            k = idx[c]
            tops.setdefault(k, z - a)
            seq.append(k)
        if any(seq[i + 1] < seq[i] for i in range(len(seq) - 1)):
            flipped.append(h)
        bottom = z - max(depth or 0.0, segs[-1][1])
        holes.append(dict(id=h, x=x, y=y, z=z, tops=tops, kmax=max(seq), bottom=bottom))
    if not holes:
        return None, ["No borehole has lithology that matches the chronology list."]
    if unknown:
        notes.append("Units not in the chronology list were ignored: %s." % ", ".join(sorted(unknown)))
    if late:
        notes.append("Lithology starts below the ground in %d hole(s) (e.g. %s); the first unit was extended up to the ground."
                     % (len(late), late[0]))
    if flipped:
        notes.append("Units appear in a different order than the chronology in %d hole(s) (e.g. %s). "
                     "Only the first appearance of each unit is used." % (len(flipped), flipped[0]))

    kdeep = max(h["kmax"] for h in holes)
    units = list(order[:kdeep + 1])
    n = len(units)
    if len(order) > n:
        notes.append("Units below the deepest one found in any hole were left out: %s." % ", ".join(order[n:]))

    # grid
    xy = np.array([(h["x"], h["y"]) for h in holes])
    span = max(np.ptp(xy[:, 0]), np.ptp(xy[:, 1]), 1.0)
    m = max(0.0, margin) * span
    x_lo, x_hi = xy[:, 0].min() - m, xy[:, 0].max() + m
    y_lo, y_hi = xy[:, 1].min() - m, xy[:, 1].max() + m
    dx, dy = (x_hi - x_lo) / nx, (y_hi - y_lo) / ny
    xs = x_lo + (np.arange(nx) + 0.5) * dx
    ys = y_lo + (np.arange(ny) + 0.5) * dy
    X, Y = np.meshgrid(xs, ys)

    ground = interp2(np.array([(h["x"], h["y"], h["z"]) for h in holes]), X, Y, method, warn, "Ground surface")

    # thickness observations per unit (the last unit has no thickness: it runs down to the model bottom)
    pts = [[] for _ in range(max(n - 1, 0))]
    for h in holes:
        present = sorted(h["tops"])
        for k in range(n - 1):
            if k in h["tops"]:
                below = [j for j in present if j > k]
                if below:
                    pts[k].append((h["x"], h["y"], max(h["tops"][k] - h["tops"][below[0]], 0.0)))
            elif k < h["kmax"]:
                pts[k].append((h["x"], h["y"], 0.0))
    tops = np.empty((n, ny, nx))
    tops[0] = ground
    thick = np.zeros((n, ny, nx))
    for k in range(n - 1):
        t = interp2(pts[k], X, Y, method, warn, units[k]) if pts[k] else np.zeros_like(X)
        t = np.clip(t, 0.0, None)
        t[t < 0.02] = 0.0
        thick[k] = t
        tops[k + 1] = tops[k] - t
    zbot = min(h["bottom"] for h in holes)
    zbot = min(zbot, float(np.nanmin(tops[-1])) - 1e-3) if n > 1 and np.nanmin(tops[-1]) < zbot else zbot
    thick[n - 1] = np.clip(tops[n - 1] - zbot, 0.0, None)

    vol = [float(thick[k].sum() * dx * dy) for k in range(n)]
    mean_t = [float(thick[k].mean()) for k in range(n)]
    pinch = [units[k] for k in range(n) if (thick[k] <= 0.0).any()]
    if pinch:
        notes.append("Pinch-outs (zero thickness somewhere): %s." % ", ".join(pinch))
    notes.append("Model box: %.0f x %.0f m, down to elevation %.1f m." % (x_hi - x_lo, y_hi - y_lo, zbot))
    notes.insert(0, "Interpolation: %s." % METHODS[method])
    notes.extend(sorted(warn))
    model = dict(units=units, xs=xs, ys=ys, dx=dx, dy=dy, tops=tops, thick=thick, zbot=float(zbot),
                 vol=vol, mean_t=mean_t, n_holes=len(holes), method=method)
    return model, notes
