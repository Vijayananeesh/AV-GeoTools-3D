"""Vertical cross-section along a line (matplotlib): ground, model units, boreholes and plume."""
import numpy as np
from .builders import PALETTE, unit_colour, unit_label, legend_units


def line_geometry(pts):
    P = np.asarray(pts, dtype=float)
    seg = np.diff(P, axis=0)
    L = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(L)])
    return P, seg, L, cum


def sample_line(pts, step):
    """Points along a (bent) line every `step` metres: distance, x, y."""
    P, seg, L, cum = line_geometry(pts)
    total = float(cum[-1])
    n = max(int(total / max(step, 1e-6)) + 1, 2)
    d = np.linspace(0.0, total, n)
    return d, np.interp(d, cum, P[:, 0]), np.interp(d, cum, P[:, 1])


def project(pts, Q):
    """For points Q (m, 2): distance along the line to the nearest spot on it, and the distance away from it."""
    P, seg, L, cum = line_geometry(pts)
    Q = np.asarray(Q, dtype=float).reshape(-1, 2)
    best = np.full(len(Q), np.inf)
    along = np.zeros(len(Q))
    for i in range(len(seg)):
        if L[i] <= 0:
            continue
        t = np.clip(((Q - P[i]) @ seg[i]) / (L[i] ** 2), 0.0, 1.0)
        proj = P[i] + t[:, None] * seg[i]
        dist = np.hypot(Q[:, 0] - proj[:, 0], Q[:, 1] - proj[:, 1])
        better = dist < best
        best[better] = dist[better]
        along[better] = cum[i] + t[better] * L[i]
    return along, best


def sample_model(model, x, y):
    """Top elevation of every unit at the points (x, y). NaN outside the model box."""
    from scipy.interpolate import RegularGridInterpolator
    xs, ys = model["xs"], model["ys"]
    hx, hy = model["dx"] / 2.0, model["dy"] / 2.0
    inside = (x >= xs[0] - hx) & (x <= xs[-1] + hx) & (y >= ys[0] - hy) & (y <= ys[-1] + hy)
    q = np.column_stack([np.clip(y, ys[0], ys[-1]), np.clip(x, xs[0], xs[-1])])
    out = np.empty((len(model["units"]), len(x)))
    for k in range(len(model["units"])):
        out[k] = RegularGridInterpolator((ys, xs), model["tops"][k])(q)
    out[:, ~inside] = np.nan
    return out


def _plume_cmap(key):
    """Matplotlib colour map with the same colours as the 3D plume ramp."""
    from matplotlib.colors import LinearSegmentedColormap
    from . import builders
    key = builders.norm_ramp(key)
    return LinearSegmentedColormap.from_list("plume_" + key, builders.SHELL_RAMPS[key][1])


def plume_section(pl, x, y, z_lo, z_hi, influence=0.0, nz=110, method="kriging", zaniso=3.0, hull=None):
    """Concentration on a vertical grid under the line, estimated like the 3D plume."""
    from . import plumeest
    n = len(x)
    zs = np.linspace(z_lo, z_hi, nz)
    G = np.column_stack([np.tile(x, nz), np.tile(y, nz), np.repeat(zs, n)])
    vals, info = plumeest.estimate(pl, G, method, influence, zaniso)
    if hull is not None:
        from . import builders
        hd = builders.hull_distance(hull["xy"], G[:, 0], G[:, 1])
        if hd is not None:
            vals = np.where(hd + float(hull.get("buffer", 0.0)) >= 0, vals, 0.0)      # outside the wells' area: no plume
    return zs, vals.reshape(nz, n)


def make_section(fig, line_pts, collars, intervals, plume, model, codes, opts):
    """Draws the section on `fig`. Returns a list of text notes."""
    from matplotlib.patches import Rectangle
    notes = []
    fig.clear()
    ax = fig.add_subplot(111)
    P, seg, L, cum = line_geometry(line_pts)
    total = float(cum[-1])
    if total <= 0:
        ax.text(0.5, 0.5, "The line has no length", ha="center", va="center", transform=ax.transAxes)
        return ["The chosen line has no length."]
    buf = float(opts.get("buffer", 25.0))
    codes = codes or {}

    def colour(unit):
        return unit_colour(unit, codes)

    in_legend = set(legend_units(codes))
    zs_all = []
    ground, d_s = None, None
    if opts.get("show_model", True) and model is not None:
        step = max(min(model["dx"], model["dy"]) / 2.0, total / 600.0)
        d_s, xs_s, ys_s = sample_line(line_pts, step)
        tops = sample_model(model, xs_s, ys_s)
        n_units = len(model["units"])
        for k, u in enumerate(model["units"]):
            upper = tops[k]
            lower = tops[k + 1] if k + 1 < n_units else np.full_like(upper, model["zbot"])
            ok = np.isfinite(upper) & np.isfinite(lower)
            ax.fill_between(d_s, lower, upper, where=ok, color=colour(u), alpha=0.9, linewidth=0, label=unit_label(u) if u in in_legend else "_nolegend_", zorder=2)
            if k >= 1:
                ax.plot(d_s, upper, color=(0.2, 0.2, 0.2), lw=0.6, zorder=3)
        ground = tops[0]
        ax.plot(d_s, ground, color="k", lw=1.6, zorder=4)
        if np.isfinite(ground).any():
            zs_all += [np.nanmax(ground), model["zbot"]]
        outside = ~np.isfinite(ground)
        if outside.any():
            notes.append("Part of the line lies outside the model box (no model drawn there).")
    elif opts.get("show_model", True):
        notes.append("No model built, so only boreholes and plume are shown.")

    holes = []
    if opts.get("show_holes", True) and collars:
        ids = list(collars)
        along, off = project(line_pts, [[collars[h][0], collars[h][1]] for h in ids])
        holes = [(h, a, o) for h, a, o in zip(ids, along, off) if o <= buf]
        w = max(0.012 * total, 0.5)
        for h, a, o in holes:
            x, y, z, dep = collars[h]
            segs = intervals.get(h, [])
            if segs:
                for f, t, u in segs:
                    ax.add_patch(Rectangle((a - w / 2, z - t), w, t - f, facecolor=colour(u), edgecolor="k", linewidth=0.7, zorder=6))
                bottom = z - max(s[1] for s in segs)
            else:
                dd = dep or 10.0
                ax.add_patch(Rectangle((a - w / 2, z - dd), w, dd, facecolor=(0.75, 0.75, 0.75), edgecolor="k", linewidth=0.7, zorder=6))
                bottom = z - dd
            ax.annotate(h, (a, z), xytext=(0, 4), textcoords="offset points", ha="center", va="bottom", fontsize=8, zorder=7)
            zs_all += [z, bottom]
        notes.append("%d borehole(s) within %g m of the line: %s." % (len(holes), buf, ", ".join(h for h, a, o in holes[:12]) + (" ..." if len(holes) > 12 else "")))
        if not holes:
            notes.append("No borehole lies that close to the line. Increase the buffer distance.")

    sc = None
    if opts.get("show_plume", True) and plume is not None and len(plume):
        along_p, off_p = project(line_pts, plume[:, :2])
        near = off_p <= buf
        zlo, zhi = float(plume[:, 2].min()) - 2.0, float(plume[:, 2].max()) + 2.0
        dd, xx, yy = sample_line(line_pts, max(total / 250.0, 0.1))
        zgrid, vals = plume_section(plume, xx, yy, zlo, zhi, float(opts.get("influence", 0) or 0), method=opts.get("plume_method", "kriging"), zaniso=float(opts.get("plume_zaniso", 3.0)), hull=opts.get("plume_hull"))
        cmax = float(plume[:, 3].max())
        iso = float(opts.get("iso", 0) or 0) or 0.25 * cmax
        if ground is not None and d_s is not None and np.isfinite(ground).any():
            gnd = np.interp(dd, d_s, np.nan_to_num(ground, nan=np.nanmax(ground)))
            vals = np.ma.masked_where(zgrid[:, None] > gnd[None, :] + 0.01, vals)
        if cmax >= iso and np.ma.max(vals) >= iso:
            ax.contourf(dd, zgrid, vals, levels=[iso, max(float(np.ma.max(vals)), iso) * 1.001],
                        colors=[(0.95, 0.25, 0.15)], alpha=0.45, zorder=3.5)
            ax.contour(dd, zgrid, vals, levels=[iso], colors=[(0.7, 0.1, 0.05)], linewidths=1.2, zorder=3.6)
            notes.append("Plume contour at %.4g." % iso)
        else:
            notes.append("The plume does not reach %.4g along this line." % iso)
        if near.any():
            sc = ax.scatter(along_p[near], plume[near, 2], c=plume[near, 3], cmap=_plume_cmap(opts.get("plume_ramp")), vmin=float(plume[:, 3].min()),
                            vmax=cmax, s=38, edgecolors="k", linewidths=0.5, zorder=8)
            fig.colorbar(sc, ax=ax, pad=0.01, fraction=0.035, label=opts.get("plume_title") or "Concentration")
            notes.append("%d plume sample(s) within the buffer." % int(near.sum()))
        zs_all += [zlo, zhi]

    fin = [v for v in zs_all if np.isfinite(v)]
    if fin:
        lo, hi = min(fin), max(fin)
        pad = 0.06 * max(hi - lo, 1.0)
        ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(0, total)
    ax.set_xlabel("Distance along the line (m)")
    ax.set_ylabel("Elevation (m)")
    ax.grid(True, color=(0.85, 0.85, 0.85), lw=0.5, zorder=0)
    ve = float(opts.get("ve", 1.0) or 1.0)
    if not opts.get("fit", False):
        ax.set_aspect(ve, adjustable="box", anchor="C")
    ttl = opts.get("title") or "Cross-section"
    ax.set_title("%s   (length %.0f m, vertical exaggeration %g)" % (ttl, total, ve if not opts.get("fit", False) else 0) if not opts.get("fit", False)
                 else "%s   (length %.0f m, fitted to window)" % (ttl, total), fontsize=10)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=min(len(labels), 6),
                  fontsize=8, frameon=False)
    return notes
