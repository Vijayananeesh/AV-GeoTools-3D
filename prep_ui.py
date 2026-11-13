import os
import traceback
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QPushButton, QLabel,
                                 QScrollArea, QWidget, QPlainTextEdit, QFileDialog, QInputDialog, QComboBox,
                                 QDoubleSpinBox, QCheckBox, QMessageBox)
from qgis.core import Qgis, QgsProject, QgsVectorLayer, QgsProviderRegistry
from qgis.gui import QgsFileWidget, QgsProjectionSelectionWidget
from . import prep
from .viewer import _layer_combo, _field_combo, G_ID, G_FROM, G_TO, G_CODE, G_CONC, G_DEPTH

G_X = ("x", "easting", "east", "utm_x", "lon", "longitude", "x_coord")
G_Y = ("y", "northing", "north", "utm_y", "lat", "latitude", "y_coord")
G_GROUND = ("ground_elev", "elev", "elevation", "ground_z", "rl", "collar_z", "z")
G_SDEPTH = ("depth_m", "depth", "sample_depth", "depth_bgl", "mid_depth", "depth_mid")
G_ELEV = ("elev", "elevation", "rl", "z", "sample_elev")
SHEET_WORDS = {
    "collar": ("collar", "borehole", "location", "bh"),
    "lith": ("litho", "lith", "geolog", "strat"),
    "plume": ("plume", "conc", "chem", "sample", "result"),
}
LEVEL = {"error": "X  ", "warn": "!  ", "info": "-  ", "ok": "OK "}


class PrepareDialog(QDialog):
    def __init__(self, parent, on_created):
        super().__init__(parent)
        self.on_created = on_created
        self.setWindowTitle("Prepare data for the 3D viewer")
        self.resize(720, 860)
        self._extra = []      # table layers opened from files (kept alive here)
        self.result = None

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        lay = QVBoxLayout(host)
        scroll.setWidget(host)
        outer.addWidget(scroll)

        intro = QLabel(
            "<b>How it works:</b> 1) load your tables, 2) check the settings, 3) press <i>Check data</i> "
            "to see problems, 4) press <i>Create layers</i>. Your original files are never changed.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        row = QHBoxLayout()
        b = QPushButton("Save blank Excel template...")
        b.clicked.connect(self.save_template)
        row.addWidget(b)
        b = QPushButton("Load a filled-in workbook (.xlsx)...")
        b.clicked.connect(self.load_workbook)
        row.addWidget(b)
        lay.addLayout(row)

        # ---- step 1: tables
        g = QGroupBox("Step 1a - Collars (one row per borehole)")
        f = QFormLayout(g)
        self.cb_c = self._table_combo(f)
        self.c_id = _field_combo(self.cb_c, G_ID)
        self.c_x = _field_combo(self.cb_c, G_X, optional=True)
        self.c_y = _field_combo(self.cb_c, G_Y, optional=True)
        self.c_z = _field_combo(self.cb_c, G_GROUND, optional=True)
        self.c_d = _field_combo(self.cb_c, G_DEPTH, optional=True)
        f.addRow("Hole ID", self.c_id)
        f.addRow("X (empty = use the layer's points)", self.c_x)
        f.addRow("Y", self.c_y)
        f.addRow("Ground elevation (empty = take from DEM)", self.c_z)
        f.addRow("Total depth (optional)", self.c_d)
        lay.addWidget(g)

        g = QGroupBox("Step 1b - Lithology (one row per layer in a borehole)")
        f = QFormLayout(g)
        self.cb_l = self._table_combo(f)
        self.l_id = _field_combo(self.cb_l, G_ID)
        self.l_from = _field_combo(self.cb_l, G_FROM)
        self.l_to = _field_combo(self.cb_l, G_TO)
        self.l_code = _field_combo(self.cb_l, G_CODE, optional=True)
        f.addRow("Hole ID", self.l_id)
        f.addRow("From (depth)", self.l_from)
        f.addRow("To (depth)", self.l_to)
        f.addRow("Lithology name", self.l_code)
        lay.addWidget(g)

        g = QGroupBox("Step 1c - Plume samples (one row per sample)")
        f = QFormLayout(g)
        self.cb_p = self._table_combo(f)
        self.p_id = _field_combo(self.cb_p, G_ID, optional=True)
        self.p_x = _field_combo(self.cb_p, G_X, optional=True)
        self.p_y = _field_combo(self.cb_p, G_Y, optional=True)
        self.p_d = _field_combo(self.cb_p, G_SDEPTH, optional=True)
        self.p_e = _field_combo(self.cb_p, G_ELEV, optional=True)
        self.p_c = _field_combo(self.cb_p, G_CONC)
        f.addRow("Hole ID (to take X/Y and ground level from Collars)", self.p_id)
        f.addRow("X (optional)", self.p_x)
        f.addRow("Y (optional)", self.p_y)
        f.addRow("Depth below ground (use this OR elevation)", self.p_d)
        f.addRow("Elevation of the sample", self.p_e)
        f.addRow("Concentration (numbers, <0.5 or ND are fine)", self.p_c)
        lay.addWidget(g)

        g = QGroupBox("Step 1d - DEM (optional)")
        f = QFormLayout(g)
        self.cb_dem = _layer_combo(Qgis.LayerFilter.RasterLayer)
        f.addRow("Used only to fill in missing ground elevations", self.cb_dem)
        lay.addWidget(g)

        # ---- step 2: settings
        g = QGroupBox("Step 2 - Settings")
        f = QFormLayout(g)
        self.src = QgsProjectionSelectionWidget()
        self.src.setCrs(QgsProject.instance().crs())
        f.addRow("Coordinates in your tables are in", self.src)
        self.same = QCheckBox("Keep the same coordinate system for the output")
        self.same.setChecked(True)
        f.addRow(self.same)
        self.dst = QgsProjectionSelectionWidget()
        self.dst.setCrs(QgsProject.instance().crs())
        self.dst.setEnabled(False)
        self.same.toggled.connect(lambda on: self.dst.setEnabled(not on))
        f.addRow("Output coordinate system (metres, e.g. UTM)", self.dst)
        self.nd = QComboBox()
        self.nd.addItem("Half the detection limit (recommended)", "half")
        self.nd.addItem("Zero", "zero")
        self.nd.addItem("Leave these samples out", "skip")
        f.addRow("Non-detects (<0.5, ND)", self.nd)
        self.dl = QDoubleSpinBox()
        self.dl.setRange(0, 1e9)
        self.dl.setDecimals(4)
        self.dl.setToolTip("Used for non-detects written without a number, such as ND.")
        f.addRow("Detection limit for plain 'ND'", self.dl)
        lay.addWidget(g)

        # ---- step 3: check
        g = QGroupBox("Step 3 - Check")
        v = QVBoxLayout(g)
        b = QPushButton("Check data")
        b.clicked.connect(self.check)
        v.addWidget(b)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMinimumHeight(190)
        v.addWidget(self.report)
        lay.addWidget(g)

        # ---- step 4: create
        g = QGroupBox("Step 4 - Create layers")
        v = QVBoxLayout(g)
        v.addWidget(QLabel("Save to a GeoPackage file so the layers stay after QGIS closes (recommended):"))
        self.out = QgsFileWidget()
        self.out.setStorageMode(QgsFileWidget.StorageMode.SaveFile)
        self.out.setFilter("GeoPackage (*.gpkg)")
        v.addWidget(self.out)
        b = QPushButton("Create layers for the viewer")
        b.clicked.connect(self.create)
        v.addWidget(b)
        lay.addWidget(g)
        lay.addStretch(1)

    # ---------------------------------------------------------------- helpers
    def _table_combo(self, form):
        cb = _layer_combo(Qgis.LayerFilter.VectorLayer)
        btn = QPushButton("Open Excel / CSV file...")
        btn.clicked.connect(lambda checked=False, c=cb: self.browse(c))
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(cb, 1)
        h.addWidget(btn)
        form.addRow("Table", box)
        return cb

    def say(self, text):
        self.report.setPlainText(text)

    def _open_table(self, path, sheet_hint=None):
        ext = os.path.splitext(path)[1].lower()
        base = os.path.basename(path)
        if ext in (".csv", ".txt"):
            uri = QUrl.fromLocalFile(path).toString() + "?type=csv&detectTypes=yes&geomType=none&subsetIndex=no&watchFile=no"
            lyr = QgsVectorLayer(uri, base, "delimitedtext")
        elif ext in (".xlsx", ".xlsm"):
            names = [s.name() for s in QgsProviderRegistry.instance().querySublayers(path)]
            if not names:
                QMessageBox.warning(self, "Prepare data", "No sheets found in " + base)
                return None
            name = sheet_hint if sheet_hint in names else (names[0] if len(names) == 1 else None)
            if name is None:
                name, ok = QInputDialog.getItem(self, "Choose a sheet", "Sheet to use from " + base, names, 0, False)
                if not ok:
                    return None
            lyr = QgsVectorLayer("%s|layername=%s" % (path, name), "%s - %s" % (base, name), "ogr")
        elif ext == ".xls":
            QMessageBox.information(self, "Prepare data", "Old .xls files are not supported. In Excel, use "
                                    "File > Save As and choose .xlsx, then open that file.")
            return None
        else:
            QMessageBox.information(self, "Prepare data", "Please choose an .xlsx or .csv file.")
            return None
        if not lyr.isValid():
            QMessageBox.warning(self, "Prepare data", "Could not read " + base + ". Is it open and locked in Excel?")
            return None
        self._extra.append(lyr)
        for cb in (self.cb_c, self.cb_l, self.cb_p):
            cb.setAdditionalLayers(self._extra)
        return lyr

    def browse(self, cb):
        path, _ = QFileDialog.getOpenFileName(self, "Open table", "", "Excel or CSV (*.xlsx *.xlsm *.csv *.txt *.xls)")
        if path:
            lyr = self._open_table(path)
            if lyr is not None:
                cb.setLayer(lyr)

    def load_workbook(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open workbook", "", "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        names = [s.name() for s in QgsProviderRegistry.instance().querySublayers(path)]
        found, missing = [], []
        for key, cb, label in (("collar", self.cb_c, "Collars"), ("lith", self.cb_l, "Lithology"),
                               ("plume", self.cb_p, "Plume")):
            hit = next((n for n in names if any(w in n.lower() for w in SHEET_WORDS[key])), None)
            lyr = self._open_table(path, hit) if hit else None
            if lyr is not None and hit:
                cb.setLayer(lyr)
                found.append("%s <- sheet '%s'" % (label, hit))
            else:
                missing.append(label)
        text = "Loaded from %s:\n  " % os.path.basename(path) + "\n  ".join(found or ["(nothing matched)"])
        if missing:
            text += "\nNot found (use 'Open Excel / CSV file...' for these, or leave them out): " + ", ".join(missing)
        self.say(text + "\n\nNext: check the settings in Step 2, then press Check data.")

    def save_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Excel template", "subsurface3d_template.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            self.say(prep.write_template(path))
        except Exception as e:
            self.say("Could not save the template: %s\n(Is the file open in Excel?)" % e)

    # ---------------------------------------------------------------- run
    def _cfg(self):
        def need(label, **fields):
            for name, val in fields.items():
                if not val:
                    raise ValueError("%s: please choose the '%s' column." % (label, name))
        cfg = {"src_crs": self.src.crs(), "dst_crs": self.src.crs() if self.same.isChecked() else self.dst.crs(),
               "nd_mode": self.nd.currentData(), "nd_default_dl": self.dl.value(),
               "dem": self.cb_dem.currentLayer(), "collar": None, "lith": None, "plume": None}
        L = self.cb_c.currentLayer()
        if L is not None:
            need("Collars", **{"Hole ID": self.c_id.currentField()})
            cfg["collar"] = dict(layer=L, f_id=self.c_id.currentField(), f_x=self.c_x.currentField(),
                                 f_y=self.c_y.currentField(), f_z=self.c_z.currentField(),
                                 f_depth=self.c_d.currentField())
        L = self.cb_l.currentLayer()
        if L is not None:
            need("Lithology", **{"Hole ID": self.l_id.currentField(), "From": self.l_from.currentField(),
                                 "To": self.l_to.currentField()})
            cfg["lith"] = dict(layer=L, f_id=self.l_id.currentField(), f_from=self.l_from.currentField(),
                               f_to=self.l_to.currentField(), f_code=self.l_code.currentField())
        L = self.cb_p.currentLayer()
        if L is not None:
            cfg["plume"] = dict(layer=L, f_id=self.p_id.currentField(), f_x=self.p_x.currentField(),
                                f_y=self.p_y.currentField(), f_depth=self.p_d.currentField(),
                                f_elev=self.p_e.currentField(), f_conc=self.p_c.currentField())
        return cfg

    def check(self):
        try:
            self.result = prep.prepare(self._cfg())
            self._dst = self.src.crs() if self.same.isChecked() else self.dst.crs()
            self.say("\n".join(LEVEL[l] + t for l, t in self.result["msgs"]))
        except ValueError as e:
            self.result = None
            self.say(str(e))
        except Exception:
            self.result = None
            self.say("Something went wrong:\n" + traceback.format_exc(limit=3))

    def create(self):
        self.check()
        if self.result is None:
            return
        if not self.result["ok"]:
            QMessageBox.warning(self, "Prepare data", "Please fix the problems marked X in the report first.")
            return
        try:
            proj = QgsProject.instance()
            old = [l.id() for l in proj.mapLayers().values() if l.name() in ("SS_Collars", "SS_Lithology", "SS_Plume")]
            if old:
                proj.removeMapLayers(old)
            gp = self.out.filePath().strip()
            if gp and not gp.lower().endswith(".gpkg"):
                gp += ".gpkg"
            layers, notes = prep.make_layers(self.result, self._dst, gp or None)
            root = proj.layerTreeRoot()
            group = root.findGroup("AV GeoTools 3D data") or root.addGroup("AV GeoTools 3D data")
            for lyr in layers.values():
                proj.addMapLayer(lyr, False)
                group.addLayer(lyr)
            self.on_created(layers)
            lines = [LEVEL[l] + t for l, t in self.result["msgs"]]
            lines += ["", "Created: " + ", ".join("%s (%d)" % (n, l.featureCount()) for n, l in layers.items())]
            lines += notes + ["The viewer's dropdowns were filled in. Go to the viewer and press Update model."]
            self.say("\n".join(lines))
        except Exception:
            self.say("Something went wrong:\n" + traceback.format_exc(limit=4))
