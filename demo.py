import os
import tempfile
import numpy as np
from qgis.core import QgsVectorLayer, QgsRasterLayer, QgsFeature, QgsGeometry, QgsPointXY

X0, Y0 = 760000.0, 1015000.0
CRS = "EPSG:32643"


def ground(x, y):
    return 50.0 + 0.04 * (x - X0) + 0.02 * (y - Y0) + 1.5 * np.sin((x - X0) / 40.0) * np.cos((y - Y0) / 55.0)


def create_demo_layers():
    """Returns [collars, lithology, plume, dem] (not yet added to the project)."""
    collars = QgsVectorLayer("Point?crs=%s&field=hole_id:string&field=elev:double&field=depth:double" % CRS,
                             "Demo_Collars", "memory")
    lith = QgsVectorLayer("None?field=hole_id:string&field=from_m:double&field=to_m:double&field=lith:string",
                          "Demo_Lithology", "memory")
    plume = QgsVectorLayer("Point?crs=%s&field=hole_id:string&field=elev:double&field=conc:double" % CRS,
                           "Demo_Plume", "memory")
    cx, cy = X0 + 70.0, Y0 + 70.0  # plume centre
    holes = []
    n = 1
    for i in range(3):
        for j in range(3):
            holes.append(("BH%02d" % n, X0 + 70.0 * i, Y0 + 70.0 * j))
            n += 1
    fc, fl, fp = [], [], []
    for hid, x, y in holes:
        z = float(ground(x, y))
        f = QgsFeature(collars.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        f.setAttributes([hid, round(z, 2), 20.0])
        fc.append(f)
        shift = 0.6 * ((x - X0) / 70.0) - 0.4 * ((y - Y0) / 70.0)
        layers = [(0.0, 1.5, "Fill"), (1.5, 6.0 + shift, "Clay"), (6.0 + shift, 12.0 + shift, "Sand"),
                  (12.0 + shift, 16.0, "Silty sand"), (16.0, 20.0, "Gravel")]
        for a, b, name in layers:
            g = QgsFeature(lith.fields())
            g.setAttributes([hid, round(a, 2), round(b, 2), name])
            fl.append(g)
        for dep in (2, 4, 6, 8, 10, 12, 14):
            r2 = (x - cx) ** 2 + (y - cy) ** 2
            c = 120.0 * np.exp(-r2 / (2 * 55.0 ** 2)) * np.exp(-((dep - 7.0) ** 2) / (2 * 3.0 ** 2))
            p = QgsFeature(plume.fields())
            p.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
            p.setAttributes([hid, round(z - dep, 2), round(float(c), 3)])
            fp.append(p)
    collars.dataProvider().addFeatures(fc)
    lith.dataProvider().addFeatures(fl)
    plume.dataProvider().addFeatures(fp)

    # small DEM GeoTIFF
    from osgeo import gdal, osr
    path = os.path.join(tempfile.mkdtemp(prefix="subsurface3d_demo_"), "demo_dem.tif")
    nx = ny = 100
    res = 3.0
    xmin, ymax = X0 - 30.0, Y0 + 170.0 + 100.0
    xs = xmin + (np.arange(nx) + 0.5) * res
    ys = ymax - (np.arange(ny) + 0.5) * res
    XX, YY = np.meshgrid(xs, ys)
    arr = ground(XX, YY).astype(np.float32)
    ds = gdal.GetDriverByName("GTiff").Create(path, nx, ny, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((xmin, res, 0, ymax, 0, -res))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32643)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(arr)
    ds.FlushCache()
    ds = None
    dem = QgsRasterLayer(path, "Demo_DEM")
    return [collars, lith, plume, dem]


def create_demo_line():
    """Two demo section lines across the demo boreholes."""
    layer = QgsVectorLayer("LineString?crs=%s&field=name:string" % CRS, "Demo_Section_Lines", "memory")
    feats = []
    for name, pts in (("A-A' (west to east)", [(X0 - 10, Y0 + 5), (X0 + 70, Y0 + 70), (X0 + 150, Y0 + 70)]),
                      ("B-B' (south to north)", [(X0 + 35, Y0 - 10), (X0 + 70, Y0 + 70), (X0 + 35, Y0 + 150)])):
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in pts]))
        f.setAttributes([name])
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    return layer


def _demo20_dem(path, T):
    from osgeo import gdal, osr
    res = 4.0
    xmin, ymax = T.X0 - 40.0, T.Y0 + T.H + 40.0
    nx, ny = int((T.W + 80.0) / res), int((T.H + 80.0) / res)
    xs = xmin + (np.arange(nx) + 0.5) * res
    ys = ymax - (np.arange(ny) + 0.5) * res
    XX, YY = np.meshgrid(xs, ys)
    ds = gdal.GetDriverByName("GTiff").Create(path, nx, ny, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((xmin, res, 0, ymax, 0, -res))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32643)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(T.ground(XX, YY).astype(np.float32))
    ds.FlushCache()
    ds = None


def _demo20_image(path, T, tab):
    """A made-up aerial picture of the demo site (a synthetic image, not a real photograph): grass and fields with a
    gravel yard and buildings at the contamination source, a road, a pond, trees and a pad at every well.
    Saved as a georeferenced RGB GeoTIFF that covers the same area as the DEM."""
    from osgeo import gdal, osr
    from scipy.ndimage import gaussian_filter
    res = 0.5
    xmin, ymax = T.X0 - 40.0, T.Y0 + T.H + 40.0
    nx, ny = int((T.W + 80.0) / res), int((T.H + 80.0) / res)
    xs = xmin + (np.arange(nx) + 0.5) * res
    ys = ymax - (np.arange(ny) + 0.5) * res
    X, Y = np.meshgrid(xs, ys)
    rng = np.random.default_rng(11)

    def noise(sigma):
        n = gaussian_filter(rng.normal(0, 1, (ny, nx)), sigma)
        return n / (n.std() + 1e-9)
    n1, n2, n3 = noise(20), noise(6), noise(1.3)
    t = np.clip(0.5 + 0.25 * n1 + 0.12 * n2, 0, 1)
    grass, dry = np.array([62, 108, 46.0]), np.array([150, 138, 84.0])
    img = grass[None, None, :] * (1 - t[..., None]) + dry[None, None, :] * t[..., None]
    img *= (1.0 + 0.07 * n3)[..., None]

    def paint(mask, colour, soft=None):
        nonlocal img
        c = np.asarray(colour, dtype=float)
        m = mask.astype(float) if soft is None else soft
        img = img * (1 - m[..., None]) + c[None, None, :] * m[..., None]

    # farm fields in the north: stripes in two tones
    for k, (xa, xb, ya, yb, ang) in enumerate(((T.X0 - 40, T.X0 + 170, T.Y0 + 245, T.Y0 + 340, 0.0),
                                                (T.X0 + 175, T.X0 + 330, T.Y0 + 235, T.Y0 + 340, 0.5),
                                                (T.X0 + 335, T.X0 + 440, T.Y0 + 250, T.Y0 + 340, -0.3))):
        m = (X > xa) & (X < xb) & (Y > ya) & (Y < yb)
        s = np.sin((X * np.cos(ang) + Y * np.sin(ang)) / 1.6)
        col = np.array([[112, 150, 70], [168, 150, 92], [96, 134, 62]][k], dtype=float)
        paint(m, col)
        img[m] *= (1.0 + 0.07 * s[m])[:, None]
    # road across the south of the site
    roadpts = np.array([[T.X0 - 40, T.Y0 + 38], [T.X0 + 120, T.Y0 + 62], [T.X0 + 260, T.Y0 + 40], [T.X0 + 440, T.Y0 + 70]])
    dist = np.full(X.shape, 1e9)
    for a, b in zip(roadpts[:-1], roadpts[1:]):
        ab = b - a
        tt = np.clip(((X - a[0]) * ab[0] + (Y - a[1]) * ab[1]) / (ab @ ab), 0, 1)
        dist = np.minimum(dist, np.hypot(X - (a[0] + tt * ab[0]), Y - (a[1] + tt * ab[1])))
    paint(dist < 4.2, (150, 140, 120))                       # gravel shoulder
    paint(dist < 3.0, (78, 78, 84))                          # asphalt
    paint((dist < 0.12) & (np.sin((X + Y) / 3.0) > 0), (225, 220, 200))   # dashed centre line
    # pond in the south-east
    pond = ((X - (T.X0 + 335)) / 26.0) ** 2 + ((Y - (T.Y0 + 118)) / 15.0) ** 2
    paint(pond < 1.25, (110, 120, 80))                       # muddy bank
    paint(pond < 1.0, (46, 92, 128))
    paint(pond < 0.55, (38, 78, 112))
    # the yard and buildings at the source
    sx, sy = float(T.SRC[0]), float(T.SRC[1])
    yard = (np.abs(X - sx) < 34) & (np.abs(Y - sy) < 24)
    paint(yard, (158, 152, 140))
    img[yard] *= (1.0 + 0.06 * n3[yard])[:, None]
    for (cx, cy, wx, wy, col) in ((sx - 14, sy + 8, 15, 8, (205, 205, 210)), (sx + 10, sy - 6, 10, 12, (176, 104, 84)),
                                  (sx + 18, sy + 10, 8, 6, (222, 222, 226))):
        body = (np.abs(X - cx) < wx / 2) & (np.abs(Y - cy) < wy / 2)
        shade = (np.abs(X - (cx + 1.0)) < wx / 2) & (np.abs(Y - (cy - 1.0)) < wy / 2)
        paint(shade & ~body, (70, 68, 64), soft=0.55 * (shade & ~body))
        paint(body, col)
    def disc(cx, cy, r, colour, alpha=1.0):
        """Paints a round spot, touching only the small part of the picture around it (fast)."""
        i0, i1 = max(int((ymax - (cy + r)) / res), 0), min(int((ymax - (cy - r)) / res) + 2, ny)
        j0, j1 = max(int((cx - r - xmin) / res), 0), min(int((cx + r - xmin) / res) + 2, nx)
        if i1 <= i0 or j1 <= j0:
            return
        m = alpha * (((X[i0:i1, j0:j1] - cx) ** 2 + (Y[i0:i1, j0:j1] - cy) ** 2) < r * r)
        sub = img[i0:i1, j0:j1]
        sub[:] = sub * (1 - m[..., None]) + np.asarray(colour, dtype=float)[None, None, :] * m[..., None]

    # clusters of trees
    for _ in range(70):
        cx, cy = rng.uniform(T.X0 - 35, T.X0 + T.W + 35), rng.uniform(T.Y0 - 35, T.Y0 + T.H + 35)
        if abs(cx - sx) < 40 and abs(cy - sy) < 30:
            continue
        for _k in range(int(rng.integers(3, 9))):
            px, py, r = cx + rng.normal(0, 4), cy + rng.normal(0, 4), rng.uniform(1.6, 3.6)
            disc(px, py, r * 1.15, (28, 52, 26), 0.5)
            disc(px, py, r, (40, 82, 36))
            disc(px, py, r * 0.45, (64, 112, 52))
    # pads at the wells
    for hid, x, y, *_ in tab["collars"]:
        disc(x, y, 3.2, (176, 172, 160))
        disc(x, y, 0.9, (50, 50, 52))
    # light and shade from the ground shape, and a little grain
    gz = T.ground(X[::8, ::8], Y[::8, ::8])
    gy, gx = np.gradient(gz, res * 8, res * 8)
    shade = np.clip(0.92 + 3.0 * (-gx * 0.6 + gy * 0.8), 0.7, 1.2)
    from scipy.ndimage import zoom
    shade = zoom(shade, (ny / shade.shape[0], nx / shade.shape[1]), order=1)[:ny, :nx]
    img *= shade[..., None]
    img += rng.normal(0, 2.0, img.shape)
    out = np.clip(img, 0, 255).astype(np.uint8)
    ds = gdal.GetDriverByName("GTiff").Create(path, nx, ny, 3, gdal.GDT_Byte, ["COMPRESS=DEFLATE"])
    ds.SetGeoTransform((xmin, res, 0, ymax, 0, -res))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32643)
    ds.SetProjection(srs.ExportToWkt())
    for b in range(3):
        ds.GetRasterBand(b + 1).WriteArray(out[..., b])
    ds.FlushCache()
    ds = None


def create_demo20_layers():
    """Demo with 20 wells at irregular places and a plume that follows the groundwater flow.
    Returns [collars, lithology, plume, dem, section_lines] (not yet added to the project)."""
    from . import demo_tables as T
    tab = T.make_demo20()
    collars = QgsVectorLayer("Point?crs=%s&field=hole_id:string&field=elev:double&field=depth:double" % CRS, "Demo20_Collars", "memory")
    lith = QgsVectorLayer("None?field=hole_id:string&field=from_m:double&field=to_m:double&field=lith:string", "Demo20_Lithology", "memory")
    plume = QgsVectorLayer("Point?crs=%s&field=hole_id:string&field=depth_m:double&field=elev:double&field=PFOS:double&field=detected:string" % CRS,
                           "Demo20_Plume", "memory")
    gwl = QgsVectorLayer("Point?crs=%s&field=hole_id:string&field=depth_to_water:double&field=water_elev:double" % CRS,
                         "Demo20_Groundwater", "memory")
    gwr = QgsVectorLayer("Point?crs=%s&field=hole_id:string&field=screen_top:double&field=screen_bottom:double&field=PFOS:double&field=detected:string" % CRS,
                         "Demo20_GW_Screens", "memory")
    xy = {}
    fc, fl, fp = [], [], []
    for hid, x, y, z, depth in tab["collars"]:
        xy[hid] = (x, y)
        f = QgsFeature(collars.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        f.setAttributes([hid, z, depth])
        fc.append(f)
    for hid, a, b, u in tab["lith"]:
        f = QgsFeature(lith.fields())
        f.setAttributes([hid, a, b, u])
        fl.append(f)
    for hid, dep, elev, conc, det in tab["plume"]:
        f = QgsFeature(plume.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*xy[hid])))
        f.setAttributes([hid, dep, elev, round(conc, 3) if det else T.DL / 2, "yes" if det else "no (half of <%.1f)" % T.DL])
        fp.append(f)
    fg = []
    for hid, dtw, head in tab["gw"]:
        f = QgsFeature(gwl.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*xy[hid])))
        f.setAttributes([hid, dtw, head])
        fg.append(f)
    collars.dataProvider().addFeatures(fc)
    lith.dataProvider().addFeatures(fl)
    plume.dataProvider().addFeatures(fp)
    gwl.dataProvider().addFeatures(fg)
    fr = []
    for hid, top, bot, conc, det in tab["gwres"]:
        f = QgsFeature(gwr.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*xy[hid])))
        f.setAttributes([hid, top, bot, round(conc, 3) if det else T.DL / 2, "yes" if det else "no (half of <%.1f)" % T.DL])
        fr.append(f)
    gwr.dataProvider().addFeatures(fr)
    path = os.path.join(tempfile.mkdtemp(prefix="subsurface3d_demo20_"), "demo20_dem.tif")
    _demo20_dem(path, T)
    dem = QgsRasterLayer(path, "Demo20_DEM")
    ipath = os.path.join(os.path.dirname(path), "demo20_satellite_synthetic.tif")
    _demo20_image(ipath, T, tab)
    sat = QgsRasterLayer(ipath, "Demo20_Satellite_synthetic")
    lines = QgsVectorLayer("LineString?crs=%s&field=name:string" % CRS, "Demo20_Section_Lines", "memory")
    feats = []
    for name, pts in (("P-P' (along the flow)", [T.SRC - 70 * T.U, T.SRC + 240 * T.U]),
                      ("Q-Q' (across the flow)", [T.SRC + 90 * T.U - 110 * T.N, T.SRC + 90 * T.U + 110 * T.N])):
        f = QgsFeature(lines.fields())
        f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(float(p[0]), float(p[1])) for p in pts]))
        f.setAttributes([name])
        feats.append(f)
    lines.dataProvider().addFeatures(feats)
    return [collars, lith, plume, dem, lines, gwl, gwr, sat]


def write_demo20_workbook(path):
    """The same 20-well demo as an Excel workbook (non-detects written as <0.5), for the Prepare data tool."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from . import demo_tables as T
    tab = T.make_demo20()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "READ ME"
    for i, line in enumerate(["Demo data: 20 wells at random places and a plume that follows the groundwater flow.",
                              "Coordinates are in EPSG:32643 (UTM zone 43N), elevations in metres, concentrations in ug/L.",
                              "Non-detects are written as <0.5. The Groundwater sheet holds the measured water levels. Open this workbook with 'Prepare data' in the plugin."], 1):
        ws.cell(row=i, column=1, value=line)
    ws.column_dimensions["A"].width = 110
    sheets = {
        "Collars": (["hole_id", "x", "y", "ground_elev", "total_depth"], [list(r) for r in tab["collars"]]),
        "Lithology": (["hole_id", "from_m", "to_m", "lith"], [list(r) for r in tab["lith"]]),
        "Plume": (["hole_id", "depth_m", "conc"], [[h, dep, (round(c, 2) if det else "<%g" % T.DL)] for h, dep, e, c, det in tab["plume"]]),
        "Groundwater": (["hole_id", "depth_to_water", "water_elev"], [list(r) for r in tab["gw"]]),
    }
    for name, (head, rows) in sheets.items():
        s = wb.create_sheet(name)
        s.append(head)
        for cell in s[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="DDEBF7")
        for r in rows:
            s.append(r)
        for i in range(1, len(head) + 1):
            s.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 16
        s.freeze_panes = "A2"
    wb.save(path)
    return path
