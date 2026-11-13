"""Exports the 3D scene as ONE self-contained HTML file with an embedded 3D viewer (works offline)."""
import base64
import datetime
import json
import os

import numpy as np
from vtkmodules.util.numpy_support import vtk_to_numpy
from vtkmodules.vtkFiltersCore import vtkTriangleFilter
from vtkmodules.vtkRenderingCore import vtkActor

from .builders import PALETTE, unit_colour, unit_label, legend_units, SHELL_RAMPS, norm_ramp

WEB = os.path.join(os.path.dirname(__file__), "web")
GROUPS = [("solid", "Solid model", True), ("contact", "Model contact", False), ("fence", "Fence diagram", False),
          ("drill", "Boreholes", False), ("topo", "Surfaces", False), ("plume", "Plume", False)]


def group_of(name):
    if name.startswith("Solid:"):
        return "solid"
    if name.startswith("Model contact"):
        return "contact"
    if name.startswith("Fence"):
        return "fence"
    if name.startswith("Groundwater plume"):
        return "plume"
    if name == "Topography (DEM)" or name.startswith("Groundwater"):
        return "topo"
    if name.startswith("Plume"):
        return "plume"
    return "drill"


def label_of(name):
    for pre in ("Solid: ", "Model contact: ", "Fence: "):
        if name.startswith(pre):
            return unit_label(name[len(pre):])
    return name


def exportable_elements(registry):
    """[(name, actor, visible, fade)] for the 3D elements with geometry (2D labels, legend and axes are left out)."""
    out = []
    for e in registry:
        if not e["actors"]:
            continue
        a = e["actors"][0]
        if not isinstance(a, vtkActor):
            continue
        m = a.GetMapper()
        if m is None or not hasattr(m, "GetInput"):
            continue
        out.append((e["name"], a, bool(a.GetVisibility()), bool(e.get("fade"))))
    return out


def label_sets(registry):
    """Text labels placed in 3D (such as the groundwater contour labels), in the page's Y-up coordinates."""
    out = []
    for e in registry:
        items = e.get("labels3d")
        if not items:
            continue
        vis = bool(e["actors"][0].GetVisibility()) if e["actors"] else True
        out.append({"name": e["name"], "group": group_of(e["name"]), "visible": vis, "style": e.get("label_style"),
                    "flat": bool(e.get("flat")), "angles": e.get("label_angles"), "h": e.get("label_h"),
                    "items": [{"t": t, "x": float(x), "y": float(z), "z": float(-y)} for t, x, y, z in items]})
    return out


def _b64(arr):
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")


def _y_up(p):
    """Scene is Z-up; web viewers are Y-up: (x, y, z) -> (x, z, -y)."""
    return np.column_stack([p[:, 0], p[:, 2], -p[:, 1]])


def mesh_of(actor):
    """Geometry of one actor as arrays, or None. Returns dict(kind, positions, normals, colors, indices)."""
    m = actor.GetMapper()
    m.Update()
    poly = m.GetInput()
    if poly is None or poly.GetNumberOfPoints() == 0:
        return None
    if poly.GetNumberOfPolys() > 0:
        tri = vtkTriangleFilter()
        tri.SetInputData(poly)
        tri.PassLinesOff()
        tri.PassVertsOff()
        tri.Update()
        out = tri.GetOutput()
        if out.GetNumberOfPolys() == 0:
            return None
        pts = vtk_to_numpy(out.GetPoints().GetData()).astype(np.float32)
        idx = vtk_to_numpy(out.GetPolys().GetConnectivityArray()).astype(np.uint32)
        nrm = out.GetPointData().GetNormals()
        normals = _y_up(vtk_to_numpy(nrm).astype(np.float32)).astype(np.float32) if nrm is not None else None
        colors = None
        if m.GetScalarVisibility() and poly.GetPointData().GetScalars() is not None:
            m.MapScalars(1.0)
            cols = m.GetColorMapColors()
            if cols is not None and cols.GetNumberOfTuples() == poly.GetNumberOfPoints():
                colors = vtk_to_numpy(cols).astype(np.uint8)
                if colors.shape[1] == 3:
                    colors = np.column_stack([colors, np.full(len(colors), 255, dtype=np.uint8)])
        uv = None
        tc = out.GetPointData().GetTCoords()
        if tc is not None and getattr(actor, "_jpeg", None):
            uv = vtk_to_numpy(tc).astype(np.float32)
        return {"kind": "mesh", "positions": _y_up(pts).astype(np.float32), "normals": normals, "colors": colors, "indices": idx,
                "uv": uv, "jpeg": getattr(actor, "_jpeg", None) if uv is not None else None}
    if poly.GetNumberOfLines() > 0:
        pts = vtk_to_numpy(poly.GetPoints().GetData()).astype(np.float32)
        conn = vtk_to_numpy(poly.GetLines().GetConnectivityArray()).astype(np.int64)
        off = vtk_to_numpy(poly.GetLines().GetOffsetsArray()).astype(np.int64)
        seg = []
        for i in range(len(off) - 1):
            run = conn[off[i]:off[i + 1]]
            if len(run) >= 2:
                seg.append(np.column_stack([run[:-1], run[1:]]))
        if not seg:
            return None
        return {"kind": "lines", "positions": _y_up(pts).astype(np.float32), "normals": None, "colors": None,
                "indices": np.vstack(seg).astype(np.uint32).ravel()}
    return None


def _css(rgb):
    return "rgb(%d,%d,%d)" % tuple(int(round(float(v) * 255)) for v in rgb)


def build_data(elements, info, title, crs_text, holes, theme="light", plume_range=None, unit_order=None, labels=None, plume_title=None, plume_ramp=None, overlay=None):
    """elements: [(name, actor, visible, fade)] already filtered. holes: {id: (x, y, ground_z)} in real coordinates."""
    x0, y0, z0 = info["origin"]
    ve = float(info.get("ve", 1.0) or 1.0)
    els, lo, hi = [], np.array([np.inf] * 3), np.array([-np.inf] * 3)
    for name, a, visible, fade in elements:
        mesh = mesh_of(a)
        if mesh is None:
            continue
        P = mesh["positions"]
        lo, hi = np.minimum(lo, P.min(axis=0)), np.maximum(hi, P.max(axis=0))
        gid = group_of(name)
        prop = a.GetProperty()
        els.append({
            "name": name, "label": label_of(name), "group": gid, "kind": mesh["kind"],
            "color": [float(v) for v in prop.GetColor()], "opacity": float(prop.GetOpacity()),
            "visible": bool(visible), "fade": bool(fade),
            "swatch": _css(prop.GetColor()) if gid in ("solid", "contact", "fence") and name != "Fence outline" else None,
            "positions": _b64(P), "normals": _b64(mesh["normals"]) if mesh["normals"] is not None else None,
            "colors": _b64(mesh["colors"]) if mesh["colors"] is not None else None,
            "uv": _b64(mesh["uv"]) if mesh.get("uv") is not None else None,
            "tex": ("data:image/jpeg;base64," + base64.b64encode(mesh["jpeg"]).decode("ascii")) if mesh.get("jpeg") else None,
            "idx16": bool(len(P) < 65536), "indices": _b64(mesh["indices"].astype(np.uint16) if len(P) < 65536 else mesh["indices"]),
        })
    if not els:
        raise ValueError("Nothing to export: none of the chosen elements has geometry.")
    codes = info.get("codes") or {}
    names = legend_units(codes) if codes else []
    units = [{"name": unit_label(n), "color": _css(unit_colour(n, codes))} for n in names]
    ranges = [[float(lo[0] + x0), float(hi[0] + x0)], [float(-hi[2] + y0), float(-lo[2] + y0)],
              [float(lo[1] / ve + z0), float(hi[1] / ve + z0)]]
    hl = [{"id": h, "x": float(x - x0), "y": float((z - z0) * ve), "z": float(-(y - y0))} for h, (x, y, z) in holes.items()]
    meta = "%s | Easting %.0f to %.0f | Northing %.0f to %.0f | Elevation %.1f to %.1f m | exported %s" % (
        crs_text, ranges[0][0], ranges[0][1], ranges[1][0], ranges[1][1], ranges[2][0], ranges[2][1],
        datetime.date.today().isoformat())
    return {"title": title, "meta": meta, "theme": theme, "origin": [float(x0), float(y0), float(z0)], "ve": ve,
            "bounds": [float(lo[0]), float(hi[0]), float(lo[1]), float(hi[1]), float(lo[2]), float(hi[2])],
            "ranges": ranges, "groups": [{"id": g, "title": t, "open": o} for g, t, o in GROUPS],
            "elements": els, "holes": hl, "units": units, "labels3d": labels or [], "overlay": overlay or {},
            "plume": {"min": float(plume_range[0]), "max": float(plume_range[1]), "title": plume_title or "Concentration",
                       "ramp": [list(c) for c in SHELL_RAMPS[norm_ramp(plume_ramp)][1]]} if plume_range else None}


def write_page(path, data):
    """Writes the HTML file (the viewer library is embedded, so it works offline). Returns its size in bytes."""
    def lib(name):
        with open(os.path.join(WEB, "three", name), "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii")
    with open(os.path.join(WEB, "viewer.html"), encoding="utf-8") as fh:
        page = fh.read()
    js = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    title = str(data["title"]).replace("&", "&amp;").replace("<", "&lt;")
    for key, val in (("%%TITLE%%", title), ("%%LIB_CORE%%", lib("three.core.js")), ("%%LIB_MODULE%%", lib("three.module.js")),
                     ("%%LIB_ORBIT%%", lib("OrbitControls.js")), ("%%DATA%%", js)):
        page = page.replace(key, val)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return os.path.getsize(path)
