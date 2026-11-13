"""Plume concentration estimated between samples: ordinary kriging (on log values) or inverse-distance weighting."""
import numpy as np

METHODS = {"kriging": "Kriging (ordinary, on log values)", "idw": "Inverse distance (IDW)"}
ZANISO = 3.0   # a vertical metre counts 3 times more than a horizontal metre


def _spherical(h, nug, sill, rng):
    h = np.asarray(h, dtype=float)
    g = np.where(h < rng, nug + sill * (1.5 * h / rng - 0.5 * (h / rng) ** 3), nug + sill)
    return np.where(h <= 0, 0.0, g)


def _fit_variogram(pts, vals):
    from scipy.spatial.distance import pdist
    from scipy.optimize import curve_fit
    d = pdist(pts)
    g = 0.5 * pdist(vals[:, None], "sqeuclidean")
    dmax = float(d.max())
    if dmax <= 0:
        return None
    edges = np.linspace(0.0, 0.6 * dmax, 11)
    H, G, S = [], [], []
    for i in range(10):
        m = (d > edges[i]) & (d <= edges[i + 1])
        if m.sum() >= 5:
            H.append(d[m].mean())
            G.append(g[m].mean())
            S.append(1.0 / np.sqrt(m.sum()))
    if len(H) < 3:
        return None
    var = max(float(np.var(vals)), 1e-9)
    try:
        popt, _ = curve_fit(_spherical, np.array(H), np.array(G), p0=[0.2 * var, var, 0.4 * dmax], sigma=np.array(S),
                            bounds=([0.0, 1e-9, 0.05 * dmax], [var, 3.0 * var, dmax]), maxfev=8000)
        return popt
    except Exception:
        return None


def _idw(tree_d, tree_i, conc):
    w = 1.0 / np.maximum(tree_d, 1e-3) ** 2
    return (w * conc[tree_i]).sum(axis=1) / w.sum(axis=1)


def _krige(pts_s, vals, Gs, d12, i12, nug, sill, rng):
    k = d12.shape[1]
    out = np.empty(len(Gs))
    step = 6000
    for a in range(0, len(Gs), step):
        sl = slice(a, a + step)
        idx, dd0 = i12[sl], d12[sl]
        C = pts_s[idx]
        dd = np.linalg.norm(C[:, :, None, :] - C[:, None, :, :], axis=-1)
        m = len(idx)
        A = np.zeros((m, k + 1, k + 1))
        A[:, :k, :k] = _spherical(dd, nug, sill, rng) + np.eye(k)[None] * (1e-9 * sill)
        A[:, :k, k] = 1.0
        A[:, k, :k] = 1.0
        b = np.ones((m, k + 1))
        b[:, :k] = _spherical(dd0, nug, sill, rng)
        sol = np.linalg.solve(A, b[:, :, None])[:, :, 0]
        out[sl] = (sol[:, :k] * vals[idx]).sum(axis=1)
    return out


def estimate(pl, G, method="kriging", influence=0.0, zaniso=ZANISO):
    """pl: (n, 4) samples x, y, z, concentration. G: (m, 3) points to estimate.
    Returns (values (m,), info dict with the method really used and a note)."""
    from scipy.spatial import cKDTree
    pl = np.asarray(pl, dtype=float)
    sc = np.array([1.0, 1.0, max(float(zaniso), 0.1)])
    pts = pl[:, :3] * sc
    conc = pl[:, 3]
    sxy = max(np.ptp(pl[:, 0]), np.ptp(pl[:, 1]), 1.0)
    infl = influence if influence > 0 else max(0.3 * sxy, 5.0)
    tree = cKDTree(pts)
    info = {"method": "idw", "note": ""}
    want_k = method == "kriging"
    k = min(12 if want_k else 8, len(pl))
    d, i = tree.query(G * sc, k=k)
    d, i = d.reshape(len(G), -1), i.reshape(len(G), -1)
    vals = None
    if want_k:
        if len(pl) < 8:
            info["note"] = "kriging needs at least 8 samples (found %d), inverse distance used" % len(pl)
        else:
            floor = max(float(conc[conc > 0].min()) / 10.0 if (conc > 0).any() else 1e-3, 1e-6)
            logc = np.log10(np.maximum(conc, floor))
            fit = _fit_variogram(pts, logc) if np.ptp(logc) > 1e-6 else None
            if fit is None:
                info["note"] = "no usable variogram could be fitted to the samples, inverse distance used"
            else:
                nug, sill, rng = fit
                try:
                    z = _krige(pts, logc, G * sc, d, i, nug, sill, rng)
                    vals = np.clip(10.0 ** z, 0.0, 1.3 * float(conc.max()))
                    info = {"method": "kriging", "note": "", "nugget": float(nug), "sill": float(sill), "range": float(rng)}
                except np.linalg.LinAlgError:
                    info["note"] = "the kriging equations could not be solved, inverse distance used"
    if vals is None:
        vals = _idw(d, i, conc)
    vals = np.where(d[:, 0] > infl, 0.0, vals)          # too far from every sample: no estimate
    return vals, info
