"""Data preparation: turn Excel/CSV tables into the clean layers the 3D viewer needs."""
import os
from collections import OrderedDict
from qgis.core import (NULL, QgsPointXY, QgsCoordinateTransform, QgsProject, QgsVectorLayer,
                       QgsFeature, QgsGeometry)

EPS = 1e-6
ND_WORDS = {"nd", "n.d.", "n.d", "bdl", "<dl", "<mdl", "<lod", "<loq", "<rl", "nondetect", "non-detect"}


# ---------------------------------------------------------------- parsing helpers
def to_float(v):
    if v is None or v == NULL:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v == v else None
    t = str(v).strip().replace("\u00a0", "")
    if not t:
        return None
    if "," in t and "." not in t:
        t = t.replace(",", ".")
    else:
        t = t.replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def hole_id(v):
    if v is None or v == NULL:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def parse_conc(v):
    """Returns (value, kind, detection_limit); kind is num / nd / blank / bad."""
    if v is None or v == NULL:
        return None, "blank", None
    if isinstance(v, (int, float)):
        return (float(v), "num", None) if v == v else (None, "blank", None)
    t = str(v).strip()
    if not t:
        return None, "blank", None
    low = t.lower().replace(" ", "")
    if low.startswith("<"):
        return None, "nd", to_float(t.lstrip("<"))
    if low in ND_WORDS:
        return None, "nd", None
    x = to_float(t)
    return (x, "num", None) if x is not None else (None, "bad", None)


def _ex(items, n=5):
    items = list(items)
    s = ", ".join(str(i) for i in items[:n])
    return s + (" ..." if len(items) > n else "")


def _transformer(src, dst):
    if src is not None and dst is not None and src.isValid() and dst.isValid() and src != dst:
        return QgsCoordinateTransform(src, dst, QgsProject.instance())
    return None


def _xy(f, fx, fy):
    if fx and fy:
        x, y = to_float(f[fx]), to_float(f[fy])
        return None if x is None or y is None else (x, y)
    g = f.geometry()
    if g is None or g.isNull():
        return None
    try:
        p = g.asMultiPoint()[0] if g.isMultipart() else g.asPoint()
        return p.x(), p.y()
    except Exception:
        return None


class _Dem:
    def __init__(self, layer, dst_crs):
        self.p = layer.dataProvider()
        self.t = _transformer(dst_crs, layer.crs())

    def at(self, x, y):
        pt = QgsPointXY(x, y)
        if self.t is not None:
            pt = self.t.transform(pt)
        v, ok = self.p.sample(pt, 1)
        return float(v) if ok and v == v else None


# ---------------------------------------------------------------- main routine
def prepare(cfg):
    """Reads the tables described in cfg and returns {collars, lith, plume, msgs, ok}.

    cfg: collar / lith / plume = dict(layer=..., f_id=..., ...) or None; dem = raster layer or None;
    src_crs, dst_crs; nd_mode = half|zero|skip; nd_default_dl = number.
    msgs is a list of (level, text) with level error / warn / info / ok.
    """
    msgs = []
    add = lambda level, text: msgs.append((level, text))
    res = {"collars": [], "lith": [], "plume": [], "msgs": msgs, "ok": False}
    src, dst = cfg["src_crs"], cfg["dst_crs"]
    c, l, p = cfg.get("collar"), cfg.get("lith"), cfg.get("plume")
    if not any([c, l, p]):
        add("error", "Choose at least one table (collars, lithology or plume).")
        return res
    if dst is None or not dst.isValid():
        add("error", "Choose an output coordinate system.")
        return res
    if dst.isGeographic():
        add("error", "The output coordinate system uses degrees (%s). Choose a projected system in metres, "
                     "for example UTM." % dst.authid())
    elif src is not None and src.isValid() and src.isGeographic() and src == dst:
        pass
    dem = _Dem(cfg["dem"], dst) if cfg.get("dem") is not None else None

    # ---------------- collars
    collars = OrderedDict()
    if c:
        L = c["layer"]
        use_geom = not (c["f_x"] and c["f_y"])
        if use_geom and not L.isSpatial():
            add("error", "Collars: choose the X and Y columns (the table has no geometry).")
        tr = _transformer(L.crs() if (use_geom and L.isSpatial()) else src, dst)
        blank = noxy = noz = dup = dem_n = baddepth = 0
        noz_ids, noxy_ids, dup_ids = [], [], []
        total = 0
        for f in L.getFeatures():
            total += 1
            hid = hole_id(f[c["f_id"]])
            if not hid:
                blank += 1
                continue
            xy = _xy(f, c["f_x"], c["f_y"])
            if xy is None:
                noxy += 1
                noxy_ids.append(hid)
                continue
            x, y = xy
            if tr is not None:
                q = tr.transform(QgsPointXY(x, y))
                x, y = q.x(), q.y()
            z = to_float(f[c["f_z"]]) if c["f_z"] else None
            if z is None and dem is not None:
                z = dem.at(x, y)
                if z is not None:
                    dem_n += 1
            if z is None:
                noz += 1
                noz_ids.append(hid)
                continue
            depth = to_float(f[c["f_depth"]]) if c["f_depth"] else None
            if depth is not None and depth <= 0:
                baddepth += 1
                depth = None
            k = hid.upper()
            if k in collars:
                dup += 1
                dup_ids.append(hid)
                continue
            collars[k] = dict(id=hid, x=x, y=y, z=z, depth=depth)
        add("ok" if collars else "error", "Collars: %d usable of %d rows." % (len(collars), total))
        if blank:
            add("warn", "Collars: %d row(s) have no hole ID and were skipped." % blank)
        if noxy:
            add("warn", "Collars: %d hole(s) have no usable X/Y and were skipped (%s)." % (noxy, _ex(noxy_ids)))
        if noz:
            add("warn", "Collars: %d hole(s) have no ground elevation and were skipped (%s). Fill the column "
                        "or choose a DEM." % (noz, _ex(noz_ids)))
        if dem_n:
            add("info", "Collars: ground elevation taken from the DEM for %d hole(s)." % dem_n)
        if dup:
            add("warn", "Collars: duplicate hole ID(s) ignored, first row kept (%s)." % _ex(dup_ids))
        if baddepth:
            add("warn", "Collars: %d total depth value(s) were zero or negative and were ignored." % baddepth)
    elif l:
        add("error", "Lithology needs a collars table (to know where each hole is).")

    # ---------------- lithology
    if l and collars:
        L = l["layer"]
        rows = OrderedDict()
        total = blank = bad = inv = 0
        nocollar, inv_ids = set(), []
        for f in L.getFeatures():
            total += 1
            hid = hole_id(f[l["f_id"]])
            if not hid:
                blank += 1
                continue
            a, b = to_float(f[l["f_from"]]), to_float(f[l["f_to"]])
            if a is None or b is None:
                bad += 1
                continue
            if b <= a:
                inv += 1
                inv_ids.append(hid)
                continue
            code = ""
            if l["f_code"]:
                v = f[l["f_code"]]
                code = "" if v is None or v == NULL else str(v).strip()
            k = hid.upper()
            if k not in collars:
                nocollar.add(hid)
                continue
            rows.setdefault(k, []).append((a, b, code or "Unknown"))
        overlaps, gaps, deeper, nodepth = [], [], [], 0
        for k, segs in rows.items():
            segs.sort(key=lambda s: s[0])
            cid = collars[k]["id"]
            for i in range(len(segs) - 1):
                g = segs[i + 1][0] - segs[i][1]
                if g < -EPS:
                    overlaps.append("%s from %.2f m" % (cid, segs[i + 1][0]))
                elif g > EPS:
                    gaps.append("%s %.2f-%.2f m" % (cid, segs[i][1], segs[i + 1][0]))
            maxto = max(s[1] for s in segs)
            dep = collars[k]["depth"]
            if dep is None:
                collars[k]["depth"] = maxto
                nodepth += 1
            elif maxto > dep + EPS:
                deeper.append("%s (%.1f > %.1f)" % (cid, maxto, dep))
            for a, b, code in segs:
                res["lith"].append(dict(id=cid, a=a, b=b, code=code))
        n_int = len(res["lith"])
        add("ok" if n_int else "error", "Lithology: %d interval(s) for %d hole(s) from %d rows."
            % (n_int, len(rows), total))
        if blank:
            add("warn", "Lithology: %d row(s) have no hole ID and were skipped." % blank)
        if bad:
            add("warn", "Lithology: %d row(s) have a missing or non-numeric From/To and were skipped." % bad)
        if inv:
            add("warn", "Lithology: %d row(s) have To <= From and were skipped (%s)." % (inv, _ex(inv_ids)))
        if nocollar:
            add("warn", "Lithology: hole(s) without a collar were skipped (%s)." % _ex(sorted(nocollar)))
        if overlaps:
            add("warn", "Lithology: overlapping intervals at %s. They will be drawn on top of each other." % _ex(overlaps))
        if gaps:
            add("info", "Lithology: gaps between intervals at %s. The gaps stay empty." % _ex(gaps))
        if deeper:
            add("warn", "Lithology: intervals go deeper than the collar's total depth: %s." % _ex(deeper))
        if nodepth:
            add("info", "Collars: total depth was missing for %d hole(s), so the deepest lithology interval was used." % nodepth)
        nolith = [v["id"] for k, v in collars.items() if k not in rows]
        if nolith:
            add("info", "Collars without lithology (drawn as plain grey holes): %s." % _ex(nolith))

    # ---------------- plume
    if p:
        L = p["layer"]
        has_xy = bool(p["f_x"] and p["f_y"])
        use_geom = (not has_xy) and L.isSpatial()
        mode = None
        if p["f_elev"]:
            mode = "elev"
            if p["f_depth"]:
                add("info", "Plume: both depth and elevation columns are set, so elevation is used.")
        elif p["f_depth"]:
            mode = "depth"
        else:
            add("error", "Plume: choose either a Depth column or an Elevation column.")
        if not p["f_conc"]:
            add("error", "Plume: choose the Concentration column.")
        if mode and p["f_conc"]:
            tr = _transformer(L.crs() if use_geom else src, dst)
            total = nd = nd_skipped = blank_c = neg = nopos = noelev = nodepth = 0
            bad_vals, nocollar = [], set()
            for f in L.getFeatures():
                total += 1
                hid = hole_id(f[p["f_id"]]) if p["f_id"] else ""
                col = collars.get(hid.upper()) if hid else None
                xy = _xy(f, p["f_x"], p["f_y"]) if (has_xy or use_geom) else None
                if xy is not None and tr is not None:
                    q = tr.transform(QgsPointXY(*xy))
                    xy = (q.x(), q.y())
                if xy is None and col is not None:
                    xy = (col["x"], col["y"])
                if xy is None:
                    nopos += 1
                    if hid and not col:
                        nocollar.add(hid)
                    continue
                x, y = xy
                ground = col["z"] if col else (dem.at(x, y) if dem is not None else None)
                d = None
                if mode == "elev":
                    z = to_float(f[p["f_elev"]])
                    if z is None:
                        noelev += 1
                        continue
                else:
                    d = to_float(f[p["f_depth"]])
                    if d is None:
                        nodepth += 1
                        continue
                    if ground is None:
                        noelev += 1
                        continue
                    z = ground - d
                if d is None and ground is not None:
                    d = ground - z
                conc, kind, dl = parse_conc(f[p["f_conc"]])
                flag = ""
                if kind == "blank":
                    blank_c += 1
                    continue
                if kind == "bad":
                    bad_vals.append(str(f[p["f_conc"]]))
                    continue
                if kind == "nd":
                    if cfg.get("nd_mode", "half") == "skip":
                        nd_skipped += 1
                        continue
                    use_dl = dl if dl is not None else float(cfg.get("nd_default_dl", 0.0) or 0.0)
                    conc = 0.0 if cfg.get("nd_mode", "half") == "zero" else use_dl / 2.0
                    flag = "ND"
                    nd += 1
                if conc < 0:
                    neg += 1
                    continue
                res["plume"].append(dict(id=col["id"] if col else hid, x=x, y=y, z=z, depth=d, conc=conc, flag=flag))
            n = len(res["plume"])
            add("ok" if n else "error", "Plume: %d usable sample(s) of %d rows." % (n, total))
            if nopos:
                add("warn", "Plume: %d row(s) have no position (no X/Y and no matching collar) and were skipped%s."
                    % (nopos, " (%s)" % _ex(sorted(nocollar)) if nocollar else ""))
            if noelev:
                add("warn", "Plume: %d row(s) have no usable elevation or ground elevation and were skipped. "
                            "Add the hole to the collars table, or choose a DEM." % noelev)
            if nodepth:
                add("warn", "Plume: %d row(s) have no depth and were skipped." % nodepth)
            if blank_c:
                add("warn", "Plume: %d row(s) have an empty concentration and were skipped." % blank_c)
            if bad_vals:
                add("warn", "Plume: %d concentration value(s) are not numbers and were skipped (%s)." % (len(bad_vals), _ex(bad_vals)))
            if neg:
                add("warn", "Plume: %d negative concentration(s) were skipped." % neg)
            if nd:
                mode_txt = {"half": "half the detection limit", "zero": "zero"}.get(cfg.get("nd_mode", "half"))
                add("info", "Plume: %d non-detect value(s) set to %s (marked ND)." % (nd, mode_txt))
            if nd_skipped:
                add("info", "Plume: %d non-detect value(s) were skipped." % nd_skipped)
            if n:
                wells = {(round(r["x"], 1), round(r["y"], 1)) for r in res["plume"]}
                vals = [r["conc"] for r in res["plume"]]
                if n < 8:
                    add("warn", "Plume: only %d samples. Interpolation between so few points is mostly guesswork." % n)
                if len(wells) < 3:
                    add("warn", "Plume: samples come from %d location(s). At least 3 are needed for a horizontal shape." % len(wells))
                if max(vals) <= 0:
                    add("warn", "Plume: every value is zero, so no isosurface will appear.")
                elif max(vals) - min(vals) < EPS:
                    add("warn", "Plume: all concentrations are identical, so no isosurface will appear.")

    res["collars"] = list(collars.values())
    res["ok"] = not any(m[0] == "error" for m in msgs) and bool(res["collars"] or res["plume"] or res["lith"])
    if res["ok"]:
        add("ok", "No blocking problems. You can create the layers.")
    return res


# ---------------------------------------------------------------- layers
def _mem(geom, name, fields, crs):
    lyr = QgsVectorLayer("%s?%s" % (geom, "&".join("field=" + f for f in fields)), name, "memory")
    if crs is not None and geom != "None":
        lyr.setCrs(crs)
    return lyr


def make_layers(res, dst_crs, gpkg=None):
    """Builds SS_Collars / SS_Lithology / SS_Plume. Saved in a GeoPackage if gpkg is given.
    Returns (dict name->layer, list of notes)."""
    layers, notes = {}, []
    if res["collars"]:
        lyr = _mem("Point", "SS_Collars", ["hole_id:string", "elev:double", "depth:double"], dst_crs)
        feats = []
        for r in res["collars"]:
            f = QgsFeature(lyr.fields())
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r["x"], r["y"])))
            f.setAttributes([r["id"], r["z"], r["depth"]])
            feats.append(f)
        lyr.dataProvider().addFeatures(feats)
        layers["SS_Collars"] = lyr
    if res["lith"]:
        lyr = _mem("None", "SS_Lithology", ["hole_id:string", "from_m:double", "to_m:double", "lith:string"], None)
        feats = []
        for r in res["lith"]:
            f = QgsFeature(lyr.fields())
            f.setAttributes([r["id"], r["a"], r["b"], r["code"]])
            feats.append(f)
        lyr.dataProvider().addFeatures(feats)
        layers["SS_Lithology"] = lyr
    if res["plume"]:
        lyr = _mem("Point", "SS_Plume", ["hole_id:string", "depth_m:double", "elev:double", "conc:double", "flag:string"], dst_crs)
        feats = []
        for r in res["plume"]:
            f = QgsFeature(lyr.fields())
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r["x"], r["y"])))
            f.setAttributes([r["id"], r["depth"], r["z"], r["conc"], r["flag"]])
            feats.append(f)
        lyr.dataProvider().addFeatures(feats)
        layers["SS_Plume"] = lyr
    if gpkg and layers:
        try:
            import processing
            processing.run("native:package", {"LAYERS": list(layers.values()), "OUTPUT": gpkg,
                                              "OVERWRITE": True, "SAVE_STYLES": False, "SAVE_METADATA": False})
            saved = {}
            for name in layers:
                lyr = QgsVectorLayer("%s|layername=%s" % (gpkg, name), name, "ogr")
                if not lyr.isValid():
                    raise RuntimeError("could not reopen %s from the GeoPackage" % name)
                saved[name] = lyr
            layers = saved
            notes.append("Saved to %s" % gpkg)
        except Exception as e:
            notes.append("Could not save the GeoPackage (%s). Using temporary layers instead." % e)
    elif layers:
        notes.append("These layers are temporary and disappear when QGIS closes. "
                     "Choose an output file to keep them.")
    return layers, notes


# ---------------------------------------------------------------- Excel template
README = [
    "AV GeoTools 3D - data template",
    "",
    "Fill in the sheets Collars, Lithology and Plume, then delete the grey example rows.",
    "Keep the column names in row 1 (you can add extra columns, they are ignored).",
    "",
    "Collars: one row per borehole. x and y must be projected coordinates in metres (for example UTM), "
    "all in the same coordinate system. ground_elev is the elevation of the ground at the hole. "
    "total_depth is optional.",
    "Lithology: one row per layer in a borehole. from_m and to_m are depths below ground in metres. "
    "Use the same hole_id as in Collars.",
    "Plume: one row per sample. depth_m is the depth below ground in metres. conc is the concentration. "
    "Write non-detects as <0.5 (with the detection limit) or ND. x and y are optional: leave them empty "
    "when the hole_id exists in Collars.",
    "One contaminant and one sampling round per Plume sheet.",
]
SHEETS = OrderedDict([
    ("Collars", (["hole_id", "x", "y", "ground_elev", "total_depth"],
                 [["BH01", 760000.0, 1015000.0, 50.2, 20.0],
                  ["BH02", 760070.0, 1015000.0, 52.9, 20.0],
                  ["BH03", 760000.0, 1015070.0, 51.6, 18.5]])),
    ("Lithology", (["hole_id", "from_m", "to_m", "lith"],
                   [["BH01", 0.0, 1.5, "Fill"], ["BH01", 1.5, 6.0, "Clay"], ["BH01", 6.0, 12.0, "Sand"],
                    ["BH01", 12.0, 20.0, "Gravel"], ["BH02", 0.0, 2.0, "Fill"], ["BH02", 2.0, 20.0, "Sand"]])),
    ("Plume", (["hole_id", "depth_m", "conc", "x", "y"],
               [["BH01", 3.0, 12.4, None, None], ["BH01", 6.0, 85.0, None, None],
                ["BH02", 3.0, 0.5, None, None], ["BH02", 6.0, "<0.5", None, None]])),
])


def write_template(path):
    """Writes an Excel template (or CSV files if openpyxl is missing). Returns a message."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill
    except Exception:
        base = os.path.splitext(path)[0]
        import csv
        for name, (head, rows) in SHEETS.items():
            with open("%s_%s.csv" % (base, name), "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(head)
                w.writerows(rows)
        return "Excel writing is not available here, so three CSV files were written next to %s." % base
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "READ ME"
    for i, line in enumerate(README, 1):
        ws.cell(row=i, column=1, value=line)
    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 120
    for name, (head, rows) in SHEETS.items():
        ws = wb.create_sheet(name)
        ws.append(head)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="DDEBF7")
        for r in rows:
            ws.append(r)
            for cell in ws[ws.max_row]:
                cell.font = Font(color="808080")
        for i in range(1, len(head) + 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 16
        ws.freeze_panes = "A2"
    wb.save(path)
    return "Template saved to %s" % path
