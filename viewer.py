import os
import logging
import json
import traceback
import numpy as np
from qgis.PyQt.QtCore import Qt, QTimer, QPointF, QSize
from qgis.PyQt.QtGui import QPixmap, QPainter, QPen, QColor, QPolygonF
from qgis.PyQt.QtWidgets import (QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton,
                                 QDoubleSpinBox, QLabel, QSplitter, QScrollArea, QFileDialog, QCheckBox, QSlider,
                                 QComboBox, QTabWidget, QTableWidget, QTableWidgetItem, QFrame, QListWidget, QListWidgetItem, QAbstractItemView,
                                 QPlainTextEdit, QLineEdit, QHeaderView, QDialog)
from qgis.core import Qgis, QgsProject, QgsApplication, QgsVectorLayer, QgsCoordinateTransform, QgsPointXY
from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox, QgsColorButton, QgsCollapsibleGroupBox
from . import data, builders, demo, panelstyle
from . import model as strat
from . import model as _model_mod
from . import cut as cutmod

G_ID = ("hole_id", "holeid", "bh_id", "borehole", "id", "name")
G_Z = ("elev", "elevation", "z", "rl", "collar_z", "ground_z", "elev_m")
G_DEPTH = ("depth", "total_depth", "td", "max_depth")
G_FROM = ("from_m", "from", "depth_from", "top", "top_m")
G_TO = ("to_m", "to", "depth_to", "bottom", "bottom_m", "base")
G_CODE = ("lith", "lithology", "lith_code", "code", "unit", "description")
G_CONC = ("conc", "concentration", "value", "result", "pfos")



# ------------------------------------------------------------------ small helpers
_log = logging.getLogger("av_geotools_3d")


def _ico(name):
    return QgsApplication.getThemeIcon(name)


def _cube_icon(face):
    """A small cube pictogram with one face highlighted (iso / top / front / side)."""
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    faces = {"top": [(16, 3), (28, 10), (16, 17), (4, 10)],
             "front": [(4, 10), (16, 17), (16, 30), (4, 23)],
             "side": [(16, 17), (28, 10), (28, 23), (16, 30)]}
    p.setPen(QPen(QColor(70, 70, 70), 1.4))
    for name, pts in faces.items():
        if face == "iso":
            fill = {"top": QColor(150, 190, 235), "front": QColor(110, 160, 220), "side": QColor(80, 135, 205)}[name]
        else:
            fill = QColor(60, 130, 220) if name == face else QColor(238, 238, 238)
        p.setBrush(fill)
        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
    p.end()
    from qgis.PyQt.QtGui import QIcon
    return QIcon(pm)


def _layer_combo(filters):
    cb = QgsMapLayerComboBox()
    cb.setFilters(filters)
    cb.setAllowEmptyLayer(True)
    return cb


def _field_combo(layer_cb, guesses, optional=False):
    fc = QgsFieldComboBox()
    fc.setAllowEmptyFieldName(optional)

    def update(layer=None):
        layer = layer_cb.currentLayer()
        fc.setLayer(layer)
        if layer is None:
            return
        names = [f.name() for f in layer.fields()]
        for g in guesses:
            for n in names:
                if n.lower() == g:
                    fc.setField(n)
                    return

    layer_cb.layerChanged.connect(update)
    update()
    return fc


def make_style():
    """Terrain-style navigation: vertical axis stays up; right mouse button pans; wheel zooms (with limits)."""
    from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTerrain
    style = vtkInteractorStyleTerrain()
    style.SetMouseWheelMotionFactor(1.5)

    def press(obj, event):
        obj.StartPan()

    def release(obj, event):
        obj.EndPan()

    for evt, cb in (("RightButtonPressEvent", press), ("RightButtonReleaseEvent", release)):
        tag = style.AddObserver(evt, cb, 10.0)
        style.GetCommand(tag).SetAbortFlag(1)

    def wheel(factor):
        def cb(obj, event):
            iren = obj.GetInteractor()
            x, y = iren.GetEventPosition()
            ren = iren.FindPokedRenderer(x, y)
            if ren is None:
                return
            cam = ren.GetActiveCamera()
            b = ren.ComputeVisiblePropBounds()
            diag = ((b[1] - b[0]) ** 2 + (b[3] - b[2]) ** 2 + (b[5] - b[4]) ** 2) ** 0.5 or 1.0
            dist = cam.GetDistance()
            if dist != dist:  # camera got lost (NaN): bring the model back
                builders.reset_camera(ren)
                iren.Render()
                return
            new_dist = dist / factor
            if new_dist < 0.03 * diag or new_dist > 10.0 * diag:
                return
            cam.Dolly(factor)
            ren.ResetCameraClippingRange()
            iren.Render()
        return cb

    style.AddObserver("MouseWheelForwardEvent", wheel(1.25), 10.0)
    style.AddObserver("MouseWheelBackwardEvent", wheel(1 / 1.25), 10.0)
    return style


def _spin(lo, hi, val, dec=2, step=1.0):
    sp = QDoubleSpinBox()
    sp.setRange(lo, hi)
    sp.setDecimals(dec)
    sp.setValue(val)
    sp.setSingleStep(step)
    return sp


def _form():
    f = QFormLayout()
    f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
    f.setContentsMargins(0, 0, 0, 0)
    return f


def _section_of(name):
    if name.startswith("Fence"):
        return "fence"
    if name.startswith("Solid:"):
        return "model_solid"
    if name.startswith("Model contact") or name.startswith("Geology"):
        return "model_contact"
    if name == "Block model":
        return "model_block"
    if name == "Topography (DEM)":
        return "topo_dem"
    if name == "Groundwater surface":
        return "topo_gw"
    if name.startswith("Groundwater"):
        return "topo_gwc"
    if name in ("Plume samples", "Plume screen tubes", "Plume sample labels"):
        return "plume_samples"
    if name == "Plume scale bar":
        return "plume_bar"
    if name.startswith("Plume shell") or name == "Plume isosurface":
        return "plume_shells"
    if name.startswith("Plume"):
        return "plume"
    if name.startswith("Axes"):
        return "tools"
    return "drill"


PRESETS = {
    "Default": None,
    "Earth tones": ["#d9c8a0", "#a0724a", "#e0b94f", "#7d8f4e", "#5b7f8f", "#9a6b5d", "#c98f5d", "#6e6a59"],
    "Pastel": ["#f2d7d5", "#d6eaf8", "#d5f5e3", "#fcf3cf", "#e8daef", "#fadbd8", "#d0ece7", "#fdebd0"],
    "Bold": ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#ffd92f", "#a65628", "#f781bf"],
    "Colour-blind safe": ["#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2", "#D55E00", "#CC79A7", "#999999"],
    "Greys (light to dark)": "greys",
}


def _hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _default_lith_style():
    return {"colours": {}, "labels": {}, "show": {}, "order": [],
            "legend": {"show": True, "title": "", "corner": "bottom-left", "scale": 1.0, "background": True, "border": False,
                       "text_size": 12, "symbol_size": 11, "gap": 8, "spacing": 6, "padding": 8, "text_mode": "auto",
                       "text_colour": (0.0, 0.0, 0.0), "bold": False, "italic": False, "font": "arial",
                       "shape": "square"}}


class _StatusBox(QPlainTextEdit):
    """Message area at the bottom: shows all lines (with a scroll bar when there are many)."""

    def __init__(self, fit=True):
        super().__init__()
        self._autofit = fit
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        if fit:
            self.setFixedHeight(84)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

    def setText(self, text):
        self.setPlainText(text)
        self.verticalScrollBar().setValue(0)
        QTimer.singleShot(0, self._fit)

    def _fit(self):
        """Grows to show every line, up to a limit (then a scroll bar appears)."""
        if not self._autofit:
            return
        doc = self.document()
        lines = max(int(round(doc.documentLayout().documentSize().height())), 1)
        need = lines * self.fontMetrics().lineSpacing() + int(2 * doc.documentMargin()) + 2 * self.frameWidth() + 4
        self.setFixedHeight(min(max(need, 36), 150))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit)

    def text(self):
        return self.toPlainText()


class _InfoWindow(QDialog):
    """Floating window with the model information. Open it with the Model info button, close it when not needed."""

    def __init__(self, parent, box):
        super().__init__(parent)
        self.setWindowTitle("Model information")
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setModal(False)
        self._placed = False
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.addWidget(box, 1)
        h = QHBoxLayout()
        self.err_text = ""
        self.btn_mail = QPushButton("Report this problem by e-mail")
        self.btn_mail.setToolTip("Opens an e-mail to the author with the error and the program versions (your user name is removed). "
                                 "Nothing is sent until you press Send in your mail program. The full details are also copied.")
        self.btn_mail.clicked.connect(self._mail)
        self.btn_copy = QPushButton("Copy details")
        self.btn_copy.clicked.connect(self._copy)
        self.btn_mail.hide()
        self.btn_copy.hide()
        h.addWidget(self.btn_mail)
        h.addWidget(self.btn_copy)
        h.addStretch(1)
        b = QPushButton("Close")
        b.clicked.connect(self.hide)
        h.addWidget(b)
        v.addLayout(h)
        self.resize(680, 320)

    def set_error(self, text):
        """Shows the report buttons when the message is an error (it carries a traceback)."""
        on = bool(text) and "Traceback" in text
        self.err_text = text if on else ""
        self.btn_mail.setVisible(on)
        self.btn_copy.setVisible(on)

    def _mail(self):
        from . import report
        report.send_mail(self.err_text)

    def _copy(self):
        from . import report
        report.copy_details(self.err_text)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._placed and self.parent() is not None:
            self._placed = True
            p = self.parent().mapToGlobal(self.parent().rect().topLeft())
            self.move(p.x() + 60, p.y() + 90)


def _ramp_icon(stops):
    """Small gradient picture for a colour-ramp dropdown. stops None = the sample colour scale (blue to red)."""
    from qgis.PyQt.QtGui import QLinearGradient, QBrush, QIcon
    pm = QPixmap(110, 14)
    g = QLinearGradient(0, 0, 110, 0)
    if stops is None:
        for k in range(11):
            c = QColor()
            c.setHsvF((240.0 - 240.0 * k / 10) / 360.0, 1.0, 1.0)
            g.setColorAt(k / 10.0, c)
    else:
        for k, (r, gg, b) in enumerate(stops):
            g.setColorAt(k / max(len(stops) - 1, 1), QColor(int(r * 255), int(gg * 255), int(b * 255)))
    p = QPainter(pm)
    p.fillRect(0, 0, 110, 14, QBrush(g))
    p.setPen(QPen(QColor(120, 120, 120)))
    p.drawRect(0, 0, 109, 13)
    p.end()
    return QIcon(pm)


class _Accordion(QWidget):
    """Section headers on the left; one section open at a time."""

    def __init__(self):
        super().__init__()
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(3)
        self._items = []

    def add(self, title, icon, body):
        btn = QPushButton("  " + title)
        btn.setIcon(icon)
        btn.setCheckable(True)
        btn.setObjectName("avSection")
        self._plain_btn_style = "QPushButton { text-align: left; padding: 7px 8px; font-weight: bold; }"
        btn.setProperty("plainStyle", self._plain_btn_style)
        btn.setStyleSheet(self._plain_btn_style)
        idx = len(self._items)
        btn.clicked.connect(lambda checked=False, i=idx: self.toggle(i))
        body.setVisible(False)
        self._v.addWidget(btn)
        self._v.addWidget(body)
        self._items.append((btn, body))

    def toggle(self, i):
        for j, (btn, body) in enumerate(self._items):
            on = (j == i) and not body.isVisible()
            body.setVisible(on)
            btn.setChecked(on)


class SubsurfaceDock(QDockWidget):
    def __init__(self, iface):
        super().__init__("AV GeoTools 3D", iface.mainWindow())
        self.iface = iface
        self.setObjectName("Subsurface3DViewerDock")
        self.vtk_widget = None
        self.ren = None
        self._orient = None
        self._was_drawn = False
        self._cam = None
        self._cam_info = None
        self._last_info = None
        self._setup_path = None
        self.btn_save = None
        self._skip_tab_draw = False
        self._table_layers = []
        self.prep = None
        self.theme = "light"
        self.custom_bg = None
        self.layer_state = {}   # element name -> {"visible": bool, "transp": int}
        self.rows = {}
        self._registry = []
        self.export_dlg = None
        self.fence = None          # polylines (real coordinates) of the fence diagram, or None
        self._group_btn = {}
        self.lith_style = _default_lith_style()
        self._lith_units = []
        self._style_timer = QTimer(self)
        self._style_timer.setSingleShot(True)
        self._style_timer.setInterval(350)
        self._style_timer.timeout.connect(self._apply_style)
        self.cutter = None
        self._cut_layers = {}      # element name -> included in the cut (default yes)
        self._cut_ui = []
        self._holes = {}
        self.sec_canvas = None
        self.model = None
        self._model_text = ""
        self._model_stale = False
        self._block_entry = None
        self._block_timer = QTimer(self)
        self._block_timer.setSingleShot(True)
        self._block_timer.setInterval(150)
        self._block_timer.timeout.connect(self._update_block)

        root = QWidget()
        rv = QVBoxLayout(root)
        rv.setContentsMargins(4, 4, 4, 4)
        rv.setSpacing(4)
        rv.addWidget(self._build_toolbar())

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._build_left())
        self.tabs = QTabWidget()
        self.tabs.setObjectName("avTabs")
        self.view_holder = QWidget()
        self.view_layout = QVBoxLayout(self.view_holder)
        self.view_layout.setContentsMargins(0, 0, 0, 0)
        self.placeholder = QLabel("Choose your layers on the left, then click  Update model.")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view_layout.addWidget(self.placeholder)
        self.tabs.addTab(self.view_holder, "3D View")
        self.tabs.addTab(self._build_section_tab(), "2D View")
        self.tabs.addTab(self._build_tables_tab(), "Tables")
        self.tabs.currentChanged.connect(self._on_tab)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 900])
        rv.addWidget(split, 1)
        rv.addWidget(self._build_bottom())
        self.setWidget(root)
        self._update_title()
        self._tabs_vis_changed()                    # the 2D View and Tables tabs start closed (open them in Tools)
        self._style_panel()

    # ------------------------------------------------------------ toolbar
    def _build_toolbar(self):
        bar = QWidget()
        bar.setObjectName("avBar")
        self._toolbar = bar
        h = QHBoxLayout(bar)
        h.setContentsMargins(6, 6, 6, 6)
        h.setSpacing(4)

        def tb(icon, tip, slot, text=""):
            b = QPushButton(text)
            b.setIcon(icon)
            b.setIconSize(QSize(22, 22))
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        h.addWidget(tb(_ico("/mActionFileNew.svg"), "New: clear the scene and the chosen layers and start again", self.new_scene))
        self.btn_save = tb(_ico("/mActionFileSave.svg"), "Save this setup (layers, columns, settings). The first time it asks for a file name; "
                           "after that it saves to the same file.", self.save_setup)
        h.addWidget(self.btn_save)
        h.addWidget(tb(_ico("/mActionFileSaveAs.svg"), "Save this setup as ... (always asks for a file name)", self.save_setup_as))
        h.addWidget(tb(_ico("/mActionFileOpen.svg"), "Open a saved setup", self.open_setup))
        h.addSpacing(12)
        h.addWidget(tb(_ico("/mActionRefresh.svg"),
                       "Redraw the model with your current layers and settings. The camera stays where it is; "
                       "use the cube buttons to reset the view.", self.update_model, " Update model"))
        h.addSpacing(6)
        for face, mode, tip in (("iso", "3d", "3D view"), ("top", "top", "Top (plan) view"),
                                ("front", "south", "Front view (looking north)"), ("side", "west", "Side view (looking east)")):
            h.addWidget(tb(_cube_icon(face), tip, lambda checked=False, m=mode: self.set_view(m)))
        h.addSpacing(6)
        h.addWidget(tb(_ico("/mActionSaveMapAsImage.svg"), "Save a picture (PNG)", self.save_png))
        h.addWidget(tb(_ico("/mActionSharingExport.svg"), "Export the model as a web page (one HTML file)", self.open_export))
        h.addSpacing(6)
        h.addWidget(tb(_ico("/mActionPropertiesWidget.svg"), "Show or hide the model information window (what was drawn, notes and warnings)",
                       self.toggle_info, " Model info"))
        h.addStretch(1)
        h.addWidget(QLabel("Theme"))
        self.cmb_theme = QComboBox()
        self.cmb_theme.addItems(["Light", "Dark"])
        try:
            from qgis.core import QgsSettings
            if str(QgsSettings().value("AVGeoTools3D/theme", "light")) == "dark":
                self.theme = "dark"
                self.cmb_theme.setCurrentIndex(1)
        except Exception:
            _log.debug("could not read saved theme", exc_info=True)
        self.cmb_theme.currentIndexChanged.connect(self._theme_changed)
        h.addWidget(self.cmb_theme)
        h.addSpacing(8)
        h.addWidget(QLabel("Vertical exaggeration"))
        self.sp_ve = _spin(0.1, 100.0, 1.0, 1, 1.0)
        self.sp_ve.setFixedWidth(72)
        self.sp_ve.editingFinished.connect(self._ve_changed)
        self._last_ve = 1.0
        h.addWidget(self.sp_ve)
        return bar

    # ------------------------------------------------------------ left accordion
    def _rows(self, key):
        lay = QVBoxLayout()
        lay.setContentsMargins(0, 6, 0, 0)
        self.rows[key] = lay
        return lay

    def _gbox(self, v, title, collapsed, *parts):
        """A group box that opens and closes, holding forms / layouts / widgets, added to layout v."""
        gb = QgsCollapsibleGroupBox(title)
        gb.setCollapsed(collapsed)
        gb.setProperty("avCollapsed", bool(collapsed))

        def _mark(c, g=gb):                                # lets the panel style show a collapsed box as a plain title line
            g.setProperty("avCollapsed", bool(c))
            g.style().unpolish(g)
            g.style().polish(g)
        gb.collapsedStateChanged.connect(_mark)
        gl = QVBoxLayout(gb)
        for part in parts:
            if isinstance(part, QWidget):
                gl.addWidget(part)
            else:
                gl.addLayout(part)
        v.addWidget(gb)
        return gb

    def _build_left(self):
        acc = _Accordion()
        acc.add("Surfaces", _ico("/mActionAddRasterLayer.svg"), self._sec_topo())
        acc.add("Drillholes", _ico("/mActionAddOgrLayer.svg"), self._sec_drill())
        acc.add("Geological Models", _ico("/mActionShowAllLayers.svg"), self._sec_model())
        acc.add("Fence diagram", _ico("/mActionAddMeshLayer.svg"), self._sec_fence())
        cut_body = self._sec_cut()              # built first (as before), shown after the Plumes section
        acc.add("Plumes", _ico("/mActionAddPointCloudLayer.svg"), self._sec_plume())
        acc.add("Cut-away", _ico("/mActionEditCut.svg"), cut_body)
        acc.add("Tools", _ico("/mActionOptions.svg"), self._sec_tools())
        self.acc = acc
        self.cb_collar.layerChanged.connect(self._clear_model)
        self.cb_lith.layerChanged.connect(self._clear_model)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumWidth(290)
        scroll.setObjectName("avLeft")
        self._left_scroll = scroll
        host = QWidget()
        host.setObjectName("avHost")
        vl = QVBoxLayout(host)
        vl.setContentsMargins(0, 0, 4, 0)
        vl.addWidget(acc)
        vl.addStretch(1)
        scroll.setWidget(host)
        return scroll

    def _style_panel(self, *args):
        """The modern look of the left panel (soft cards, rounded boxes, blue accent); off = the plain QGIS look."""
        sc = getattr(self, "_left_scroll", None)
        if sc is None:
            return
        on = getattr(self, "chk_modern", None) is None or self.chk_modern.isChecked()
        for btn, _body in getattr(self.acc, "_items", []):                # the section buttons carry their own plain style when it is off
            btn.setStyleSheet("" if on else (btn.property("plainStyle") or ""))
        sc.setStyleSheet(panelstyle.panel_qss(self.theme == "dark") if on else "")
        bar = getattr(self, "_toolbar", None)
        if bar is not None:
            for b in bar.findChildren(QPushButton):
                if b.text().strip() == "Update model":
                    b.setObjectName("avPrimary")
            bar.setStyleSheet(panelstyle.toolbar_qss(self.theme == "dark") if on else "")
        tabs = getattr(self, "tabs", None)
        if tabs is not None:
            tabs.setStyleSheet(panelstyle.tabs_qss(self.theme == "dark") if on else "")

    def _body(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 4, 6, 8)
        return w, v

    def _sec_topo(self):
        w, v = self._body()
        f_dem, f_gw, f_gc = _form(), _form(), _form()
        self.cb_raster = _layer_combo(Qgis.LayerFilter.RasterLayer)
        f_dem.addRow("DEM raster", self.cb_raster)
        self.sp_demoff = _spin(-200.0, 200.0, 0.0, 2, 0.5)
        self.sp_demoff.setToolTip("Moves the whole DEM up (+) or down (-) in metres. The plume cut and the geology cut follow it. "
                                  "Press Update model after changing.")
        f_dem.addRow("DEM height adjustment (m)", self.sp_demoff)
        self.sp_modgap = _spin(0.0, 50.0, 0.3, 2, 0.1)
        self.sp_modgap.setToolTip("The geology model is kept at least this far below the DEM (metres), so no rock shows above the ground.")
        f_dem.addRow("Keep geology below the DEM by (m)", self.sp_modgap)
        self.cb_drape = _layer_combo(Qgis.LayerFilter.RasterLayer)
        self.cb_drape.setToolTip("A satellite or aerial picture to drape over the DEM: a georeferenced image, or a web map "
                                 "(XYZ tiles such as satellite imagery) from your QGIS project.")
        self.cb_bound = _layer_combo(Qgis.LayerFilter.PolygonLayer | Qgis.LayerFilter.LineLayer)
        self.cb_bound.setToolTip("A polygon or line layer (for example the site boundary) drawn on top of the DEM surface. "
                                 "Layers in another coordinate system are converted automatically.")
        self.btn_bcol = QgsColorButton()
        self.btn_bcol.setAllowOpacity(False)
        self.btn_bcol.setColor(QColor(225, 25, 50))
        self.sp_bwidth = _spin(1.0, 15.0, 3.0, 1, 0.5)
        self.sp_blift = _spin(0.0, 50.0, 0.3, 2, 0.1)
        self.sp_blift.setToolTip("Lifts the line this far above the surface (metres), so it does not sink into it.")
        for w_ in (self.btn_bcol.colorChanged,):
            w_.connect(self._boundary_changed)
        self.cb_bound.layerChanged.connect(self._boundary_changed)
        self.sp_bwidth.editingFinished.connect(self._boundary_changed)
        self.sp_blift.editingFinished.connect(self._boundary_changed)
        self.sp_drape = _spin(512.0, 4096.0, 2048.0, 0, 512.0)
        self.sp_drape.setToolTip("Picture size in pixels along its longer side. Bigger is sharper but slower to fetch and draw.")
        f_dem.addRow("Site boundary on the DEM (polygon or line layer)", self.cb_bound)
        f_dem.addRow("Boundary colour", self.btn_bcol)
        f_dem.addRow("Boundary line width (px)", self.sp_bwidth)
        f_dem.addRow("Boundary lift above the surface (m)", self.sp_blift)
        f_dem.addRow("Satellite / aerial image on the DEM", self.cb_drape)
        f_dem.addRow("Image detail (pixels, longer side)", self.sp_drape)
        self.cb_drape.layerChanged.connect(self._drape_changed)
        self.sp_drape.editingFinished.connect(self._drape_changed)
        self.cb_gw = _layer_combo(Qgis.LayerFilter.PointLayer)
        self.f_gwid = _field_combo(self.cb_gw, G_ID, optional=True)
        self.f_gwz = _field_combo(self.cb_gw, ("water_elev", "gw_elev", "water_level", "level_elev", "head", "gwl"), optional=True)
        self.f_gwd = _field_combo(self.cb_gw, ("depth_to_water", "dtw", "water_depth", "depth_water", "swl"), optional=True)
        self.cmb_gwmethod = QComboBox()
        for label, key in (("Kriging (ordinary)", "kriging"), ("Linear (triangles)", "linear"),
                           ("Smooth (thin-plate spline)", "smooth"), ("Inverse distance (IDW)", "idw")):
            self.cmb_gwmethod.addItem(label, key)
        self.sp_gwint = _spin(0.0, 1000.0, 0.0, 2, 0.5)
        self.sp_gwlab = _spin(6.0, 48.0, 13.0, 0, 1.0)
        self.sp_gwm = _spin(0.0, 100.0, 10.0, 0, 5.0)
        self.chk_gwflat = QCheckBox("Lay the contour labels flat on the surface")
        self.chk_gwflat.setChecked(True)
        self.chk_gwflat.setToolTip("On: the values are written on the groundwater surface, along the contour lines. "
                                   "Off: they always face the camera.")
        f_gw.addRow("Well layer (points with a water level)", self.cb_gw)
        f_gw.addRow("Hole ID (optional)", self.f_gwid)
        f_gw.addRow("Water level (elevation)", self.f_gwz)
        f_gw.addRow("OR depth to water (below ground)", self.f_gwd)
        f_gw.addRow("Interpolation between wells", self.cmb_gwmethod)
        f_gw.addRow("Extend beyond the wells (% of site)", self.sp_gwm)
        f_gc.addRow("Contour interval (m, 0 = auto)", self.sp_gwint)
        f_gc.addRow("Contour label size", self.sp_gwlab)
        f_gc.addRow(self.chk_gwflat)
        # each group: its show / hide tick boxes (they appear after you draw) and its settings
        self._gbox(v, "Topography (DEM)", True, self._rows("topo_dem"), f_dem)
        self._gbox(v, "Groundwater surface", True, self._rows("topo_gw"), f_gw)
        self._gbox(v, "Groundwater contours and labels", True, self._rows("topo_gwc"), f_gc)
        self.cmb_gwmethod.currentIndexChanged.connect(self._gw_changed)
        for w_ in (self.sp_gwint, self.sp_gwlab, self.sp_gwm):
            w_.editingFinished.connect(self._gw_changed)
        self.chk_gwflat.toggled.connect(self._gw_changed)
        return w

    def _drape_changed(self, *args):
        if self.vtk_widget is not None and self._was_drawn:
            self.draw(restore=True)

    def _make_drape(self, surface, dem_layer, notes):
        """Renders the chosen layer (image or web map) over the DEM area as a picture to wrap on the surface."""
        layer = self.cb_drape.currentLayer()
        if layer is None:
            return None
        xs, ys, _z = surface
        xmn, xmx, ymn, ymx = float(xs.min()), float(xs.max()), float(ys.min()), float(ys.max())
        px = int(self.sp_drape.value())
        crs = dem_layer.crs()
        key = (layer.id(), round(xmn, 3), round(xmx, 3), round(ymn, 3), round(ymx, 3), px, crs.authid())
        cached = getattr(self, "_drape_cache", None)
        if cached is not None and cached[0] == key:
            notes.append("Image draped on the DEM: %s (%d x %d px)." % (layer.name(), cached[1]["rgb"].shape[1], cached[1]["rgb"].shape[0]))
            return cached[1]
        from qgis.core import QgsMapSettings, QgsMapRendererParallelJob, QgsRectangle
        from qgis.PyQt.QtGui import QImage
        from qgis.PyQt.QtCore import QBuffer, QIODevice, QSize
        wd, ht = xmx - xmn, ymx - ymn
        if wd <= 0 or ht <= 0:
            return None
        w, h = (px, max(2, int(round(px * ht / wd)))) if wd >= ht else (max(2, int(round(px * wd / ht))), px)
        st = QgsMapSettings()
        st.setLayers([layer])
        st.setDestinationCrs(crs)
        st.setExtent(QgsRectangle(xmn, ymn, xmx, ymx))
        st.setOutputSize(QSize(w, h))
        st.setBackgroundColor(QColor(255, 255, 255))
        job = QgsMapRendererParallelJob(st)
        job.start()
        job.waitForFinished()
        img = job.renderedImage()
        if img is None or img.isNull():
            notes.append("The image could not be drawn (check the layer and your internet connection).")
            return None
        img = img.convertToFormat(QImage.Format.Format_RGB888)
        bpl = img.bytesPerLine()
        arr = np.frombuffer(img.constBits().asstring(bpl * h), dtype=np.uint8).reshape(h, bpl)[:, :w * 3].reshape(h, w, 3)
        if arr.std() < 1.0:
            notes.append("The image layer drew nothing over the DEM area (blank). Check that it covers the site and uses a known CRS.")
            return None
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "JPG", 88)
        drape = {"rgb": np.ascontiguousarray(arr[::-1]), "jpeg": bytes(buf.data()), "extent": (xmn, xmx, ymn, ymx)}
        self._drape_cache = (key, drape)
        notes.append("Image draped on the DEM: %s (%d x %d px)." % (layer.name(), w, h))
        return drape

    def _gw_changed(self, *args):
        if self.vtk_widget is not None and self._was_drawn and self.cb_gw.currentLayer() is not None:
            self.draw(restore=True)

    def _sec_drill(self):
        w, v = self._body()
        f_c, f_l, f_r = _form(), _form(), _form()
        self.cb_collar = _layer_combo(Qgis.LayerFilter.PointLayer)
        self.f_cid = _field_combo(self.cb_collar, G_ID)
        self.f_cz = _field_combo(self.cb_collar, G_Z)
        self.f_cd = _field_combo(self.cb_collar, G_DEPTH, optional=True)
        f_c.addRow("Layer", self.cb_collar)
        f_c.addRow("Hole ID", self.f_cid)
        f_c.addRow("Ground elevation", self.f_cz)
        f_c.addRow("Total depth (optional)", self.f_cd)
        self.cb_lith = _layer_combo(Qgis.LayerFilter.VectorLayer)
        self.f_lid = _field_combo(self.cb_lith, G_ID)
        self.f_lfrom = _field_combo(self.cb_lith, G_FROM)
        self.f_lto = _field_combo(self.cb_lith, G_TO)
        self.f_lcode = _field_combo(self.cb_lith, G_CODE, optional=True)
        f_l.addRow("Layer", self.cb_lith)
        f_l.addRow("Hole ID", self.f_lid)
        f_l.addRow("From (depth)", self.f_lfrom)
        f_l.addRow("To (depth)", self.f_lto)
        f_l.addRow("Lithology", self.f_lcode)
        self.sp_rad = _spin(0.1, 50.0, 1.0, 2, 0.5)
        self.sp_rad.editingFinished.connect(self._size_changed)
        f_r.addRow("Borehole radius (1 = auto)", self.sp_rad)
        # names written next to the boreholes
        self.sp_hlsize = _spin(6.0, 48.0, 13.0, 0, 1.0)
        self.cmb_hlcol = QComboBox()
        self.cmb_hlcol.addItem("Automatic (follows the theme)", "auto")
        self.cmb_hlcol.addItem("My colour", "custom")
        self.btn_hlcol = QgsColorButton()
        self.btn_hlcol.setAllowOpacity(False)
        self.btn_hlcol.setColor(QColor(0, 0, 0))
        self.btn_hlcol.setEnabled(False)
        self.cmb_hlfont = QComboBox()
        for label, key in (("Arial", "arial"), ("Times", "times"), ("Courier", "courier")):
            self.cmb_hlfont.addItem(label, key)
        self.chk_hlbold = QCheckBox("Bold")
        self.chk_hlbold.setChecked(True)
        self.chk_hlital = QCheckBox("Italic")
        self.chk_hlbg = QCheckBox("Background behind the text")
        self.chk_hlbg.setChecked(True)
        self.chk_hldecl = QCheckBox("Hide names that overlap")
        self.chk_hldecl.setChecked(True)
        self.sp_hlx = _spin(-300.0, 300.0, 6.0, 0, 2.0)
        self.sp_hlx.setToolTip("Moves each name sideways from its borehole, in pixels. Plus = right, minus = left.")
        self.sp_hly = _spin(-300.0, 300.0, 6.0, 0, 2.0)
        self.sp_hly.setToolTip("Moves each name up or down from its borehole, in pixels. Plus = up, minus = down.")
        hlh = QHBoxLayout()
        hlh.addWidget(self.chk_hlbold)
        hlh.addWidget(self.chk_hlital)
        hlh.addStretch(1)
        f_r.addRow("Name size (px)", self.sp_hlsize)
        f_r.addRow("Name font", self.cmb_hlfont)
        f_r.addRow("Name colour", self.cmb_hlcol)
        f_r.addRow("My colour", self.btn_hlcol)
        f_r.addRow(hlh)
        f_r.addRow(self.chk_hlbg)
        f_r.addRow(self.chk_hldecl)
        f_r.addRow("Name horizontal offset (px)", self.sp_hlx)
        f_r.addRow("Name vertical offset (px)", self.sp_hly)
        for sig in (self.cmb_hlcol.currentIndexChanged, self.btn_hlcol.colorChanged, self.sp_hlsize.valueChanged,
                    self.cmb_hlfont.currentIndexChanged, self.chk_hlbold.toggled, self.chk_hlital.toggled,
                    self.chk_hlbg.toggled, self.chk_hldecl.toggled, self.sp_hlx.valueChanged, self.sp_hly.valueChanged):
            sig.connect(self._hlabels_changed)
        self._gbox(v, "Collars", True, f_c)
        self._gbox(v, "Lithology log", True, f_l)
        self._gbox(v, "Boreholes on screen", True, self._rows("drill"), f_r)
        g = QgsCollapsibleGroupBox("Lithology colours and legend")
        g.setCollapsed(True)
        gl = QVBoxLayout(g)
        gl.addWidget(self._sec_lith())
        v.addWidget(g)
        return w

    def _sec_lith(self):
        w, v = self._body()
        note = QLabel("Choose the colour of each lithology unit and how the legend looks. The colours are used "
                      "everywhere: boreholes, solids, contacts, fence, block model, the 2D section and the web page.")
        note.setWordWrap(True)
        v.addWidget(note)
        h = QHBoxLayout()
        self.cmb_preset = QComboBox()
        self.cmb_preset.addItems(list(PRESETS))
        h.addWidget(self.cmb_preset, 1)
        b = QPushButton("Apply scheme")
        b.clicked.connect(self._apply_preset)
        h.addWidget(b)
        v.addLayout(h)
        self.lith_table = QTableWidget(0, 4)
        self.lith_table.setHorizontalHeaderLabels(["Legend", "Colour", "Unit", "Legend text"])
        self.lith_table.verticalHeader().setVisible(False)
        self.lith_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.lith_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hd = self.lith_table.horizontalHeader()
        hd.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hd.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hd.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hd.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.lith_table.setColumnWidth(1, 64)
        self.lith_table.setMinimumHeight(170)
        self.lith_table.itemChanged.connect(self._lith_label_edited)
        v.addWidget(self.lith_table)
        v.addWidget(QLabel("<span style='color:gray'>The table lists the units after you draw. Double-click the legend "
                           "text to rename a unit in the legend.</span>"))
        hb = QHBoxLayout()
        for text, slot in (("Up", lambda *a: self._lith_move(-1)), ("Down", lambda *a: self._lith_move(1)),
                           ("Reset", lambda *a: self._lith_reset())):
            bb = QPushButton(text)
            bb.clicked.connect(slot)
            hb.addWidget(bb)
        v.addLayout(hb)
        v.addWidget(QLabel("<b>Legend</b>"))
        f = _form()
        self.chk_lshow = QCheckBox("Show the legend")
        self.chk_lshow.setChecked(True)
        self.ed_ltitle = QLineEdit()
        self.ed_ltitle.setPlaceholderText("(no title)")
        self.cmb_lcorner = QComboBox()
        for label, key in (("Bottom left", "bottom-left"), ("Bottom right", "bottom-right"),
                           ("Top left", "top-left"), ("Top right", "top-right")):
            self.cmb_lcorner.addItem(label, key)
        self.sp_ltext = _spin(6.0, 80.0, 12.0, 0, 1.0)
        self.cmb_ltextcol = QComboBox()
        for label, key in (("Automatic (follows the theme)", "auto"), ("Same colour as the unit", "unit"), ("My colour", "custom")):
            self.cmb_ltextcol.addItem(label, key)
        self.btn_ltext = QgsColorButton()
        self.btn_ltext.setAllowOpacity(False)
        self.btn_ltext.setColor(QColor(0, 0, 0))
        self.cmb_lfont = QComboBox()
        for label, key in (("Arial", "arial"), ("Times", "times"), ("Courier", "courier")):
            self.cmb_lfont.addItem(label, key)
        self.chk_lbold = QCheckBox("Bold")
        self.chk_lital = QCheckBox("Italic")
        fh = QHBoxLayout()
        fh.addWidget(self.chk_lbold)
        fh.addWidget(self.chk_lital)
        fh.addStretch(1)
        self.sp_lsym = _spin(4.0, 80.0, 11.0, 0, 1.0)
        self.cmb_lshape = QComboBox()
        self.cmb_lshape.addItem("Square", "square")
        self.cmb_lshape.addItem("Circle", "circle")
        self.sp_lspace = _spin(0.0, 40.0, 6.0, 0, 1.0)
        self.sp_lgap = _spin(0.0, 40.0, 8.0, 0, 1.0)
        self.sp_lscale = _spin(0.5, 3.0, 1.0, 1, 0.1)
        self.chk_lbg = QCheckBox("Background box")
        self.chk_lbg.setChecked(True)
        self.chk_lborder = QCheckBox("Border")
        bh = QHBoxLayout()
        bh.addWidget(self.chk_lbg)
        bh.addWidget(self.chk_lborder)
        bh.addStretch(1)
        f.addRow(self.chk_lshow)
        f.addRow("Title", self.ed_ltitle)
        f.addRow("Position", self.cmb_lcorner)
        f.addRow("Text size (px)", self.sp_ltext)
        f.addRow("Text colour", self.cmb_ltextcol)
        f.addRow("My text colour", self.btn_ltext)
        f.addRow("Font", self.cmb_lfont)
        f.addRow("Style", fh)
        f.addRow("Symbol size (px)", self.sp_lsym)
        f.addRow("Symbol shape", self.cmb_lshape)
        f.addRow("Space between rows (px)", self.sp_lspace)
        f.addRow("Space between symbol and text (px)", self.sp_lgap)
        f.addRow("Overall scale", self.sp_lscale)
        f.addRow(bh)
        v.addLayout(f)
        for sig in (self.chk_lshow.toggled, self.ed_ltitle.textChanged, self.cmb_lcorner.currentIndexChanged,
                    self.sp_ltext.valueChanged, self.cmb_ltextcol.currentIndexChanged, self.btn_ltext.colorChanged,
                    self.cmb_lfont.currentIndexChanged, self.chk_lbold.toggled, self.chk_lital.toggled,
                    self.sp_lsym.valueChanged, self.cmb_lshape.currentIndexChanged, self.sp_lspace.valueChanged,
                    self.sp_lgap.valueChanged, self.sp_lscale.valueChanged, self.chk_lbg.toggled, self.chk_lborder.toggled):
            sig.connect(self._legend_changed)
        return w

    # ------------------------------------------------------------ lithology colours and legend
    def _push_style(self):
        st = dict(self.lith_style)
        if not st["order"] and self.model is not None:        # by default the legend follows the chronology
            st["order"] = list(self.model["units"])
        lg = dict(st["legend"])
        lg["dpr"] = self.vtk_widget.devicePixelRatioF() if self.vtk_widget is not None else 1.0
        st["legend"] = lg
        builders.set_style(**st)

    def _style_changed(self, *args):
        self._style_timer.start()

    def _apply_style(self):
        self._push_style()
        if self.vtk_widget is not None and self._was_drawn:
            self.draw(restore=True)

    def _legend_changed(self, *args):
        tc = self.btn_ltext.color()
        self.lith_style["legend"] = {
            "show": self.chk_lshow.isChecked(), "title": self.ed_ltitle.text(), "corner": self.cmb_lcorner.currentData(),
            "scale": self.sp_lscale.value(), "background": self.chk_lbg.isChecked(), "border": self.chk_lborder.isChecked(),
            "text_size": self.sp_ltext.value(), "symbol_size": self.sp_lsym.value(), "gap": self.sp_lgap.value(),
            "spacing": self.sp_lspace.value(), "padding": 8, "text_mode": self.cmb_ltextcol.currentData(),
            "text_colour": (tc.redF(), tc.greenF(), tc.blueF()), "bold": self.chk_lbold.isChecked(),
            "italic": self.chk_lital.isChecked(), "font": self.cmb_lfont.currentData(), "shape": self.cmb_lshape.currentData()}
        self.btn_ltext.setEnabled(self.cmb_ltextcol.currentData() == "custom")
        self._style_changed()

    def _lith_order(self, codes):
        base = self.lith_style["order"] or (list(self.model["units"]) if self.model is not None else [])
        order = [u for u in base if u in codes]
        return order + [u for u, i in sorted(codes.items(), key=lambda kv: kv[1]) if u not in order]

    def _fill_lith_table(self, codes, force=False):
        names = self._lith_order(codes)
        if not force and names == self._lith_units:
            return
        self._lith_units = names
        t = self.lith_table
        t.blockSignals(True)
        t.setRowCount(len(names))
        for r, u in enumerate(names):
            chk = QCheckBox()
            chk.setChecked(self.lith_style["show"].get(u, True))
            chk.toggled.connect(lambda on, u=u: self._set_unit_show(u, on))
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(8, 0, 0, 0)
            cl.addWidget(chk)
            t.setCellWidget(r, 0, cell)
            rgb = builders.unit_colour(u, codes)
            btn = QgsColorButton()
            btn.setAllowOpacity(False)
            btn.setColor(QColor.fromRgbF(rgb[0], rgb[1], rgb[2]))
            btn.colorChanged.connect(lambda c, u=u: self._set_unit_colour(u, c))
            t.setCellWidget(r, 1, btn)
            it = QTableWidgetItem(u)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, 2, it)
            t.setItem(r, 3, QTableWidgetItem(self.lith_style["labels"].get(u) or u))
        t.blockSignals(False)

    def _set_unit_colour(self, unit, color):
        c = QColor(color)
        self.lith_style["colours"][unit] = (c.redF(), c.greenF(), c.blueF())
        self._style_changed()

    def _set_unit_show(self, unit, on):
        self.lith_style["show"][unit] = bool(on)
        self._style_changed()

    def _lith_label_edited(self, item):
        if item.column() != 3 or item.row() >= len(self._lith_units):
            return
        unit = self._lith_units[item.row()]
        text = item.text().strip()
        if text and text != unit:
            self.lith_style["labels"][unit] = text
        else:
            self.lith_style["labels"].pop(unit, None)
        self._style_changed()

    def _lith_move(self, delta):
        r = self.lith_table.currentRow()
        nr = r + delta
        if r < 0 or not (0 <= nr < len(self._lith_units)):
            return
        names = list(self._lith_units)
        names[r], names[nr] = names[nr], names[r]
        self.lith_style["order"] = names
        codes = (self._last_info or {}).get("codes") or {u: i + 1 for i, u in enumerate(sorted(names))}
        self._fill_lith_table(codes, force=True)
        self.lith_table.selectRow(nr)
        self._style_changed()

    def _apply_preset(self, *args):
        name = self.cmb_preset.currentText()
        spec = PRESETS.get(name)
        names = list(self._lith_units)
        if not names:
            self.say("Draw the model first, so the units are known.", warn=True)
            return
        if spec is None:
            self.lith_style["colours"] = {}
        elif spec == "greys":
            n = len(names)
            self.lith_style["colours"] = {u: (0.92 - 0.72 * i / max(n - 1, 1),) * 3 for i, u in enumerate(names)}
        else:
            self.lith_style["colours"] = {u: _hex_rgb(spec[i % len(spec)]) for i, u in enumerate(names)}
        codes = (self._last_info or {}).get("codes") or {u: i + 1 for i, u in enumerate(sorted(names))}
        self._push_style()
        self._fill_lith_table(codes, force=True)
        self._style_changed()

    def _lith_reset(self):
        self.lith_style = _default_lith_style()
        self._push_style()
        self._sync_legend_widgets()
        codes = (self._last_info or {}).get("codes") or {u: i + 1 for i, u in enumerate(sorted(self._lith_units))}
        self._fill_lith_table(codes, force=True)
        self._style_changed()

    def _sync_legend_widgets(self):
        lg = {**_default_lith_style()["legend"], **self.lith_style["legend"]}
        ws = (self.chk_lshow, self.ed_ltitle, self.cmb_lcorner, self.sp_ltext, self.cmb_ltextcol, self.btn_ltext,
              self.cmb_lfont, self.chk_lbold, self.chk_lital, self.sp_lsym, self.cmb_lshape, self.sp_lspace, self.sp_lgap,
              self.sp_lscale, self.chk_lbg, self.chk_lborder)
        for w_ in ws:
            w_.blockSignals(True)
        self.chk_lshow.setChecked(lg["show"])
        self.ed_ltitle.setText(lg["title"])
        self.cmb_lcorner.setCurrentIndex(max(self.cmb_lcorner.findData(lg["corner"]), 0))
        self.sp_ltext.setValue(float(lg["text_size"]))
        self.cmb_ltextcol.setCurrentIndex(max(self.cmb_ltextcol.findData(lg["text_mode"]), 0))
        tc = lg["text_colour"]
        self.btn_ltext.setColor(QColor.fromRgbF(tc[0], tc[1], tc[2]))
        self.btn_ltext.setEnabled(lg["text_mode"] == "custom")
        self.cmb_lfont.setCurrentIndex(max(self.cmb_lfont.findData(lg["font"]), 0))
        self.chk_lbold.setChecked(bool(lg["bold"]))
        self.chk_lital.setChecked(bool(lg["italic"]))
        self.sp_lsym.setValue(float(lg["symbol_size"]))
        self.cmb_lshape.setCurrentIndex(max(self.cmb_lshape.findData(lg["shape"]), 0))
        self.sp_lspace.setValue(float(lg["spacing"]))
        self.sp_lgap.setValue(float(lg["gap"]))
        self.sp_lscale.setValue(float(lg["scale"]))
        self.chk_lbg.setChecked(bool(lg["background"]))
        self.chk_lborder.setChecked(bool(lg["border"]))
        for w_ in ws:
            w_.blockSignals(False)

    def _sec_model(self):
        w, v = self._body()
        gb_chron = QgsCollapsibleGroupBox("Chronology (youngest at the top)")
        gb_chron.setCollapsed(True)
        v.addWidget(gb_chron)
        cv = QVBoxLayout(gb_chron)
        self.chron = QListWidget()
        self.chron.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.chron.setMinimumHeight(120)
        self.chron.setMaximumHeight(190)
        cv.addWidget(self.chron)
        self.chron.model().rowsMoved.connect(self._mark_model_stale)
        h = QHBoxLayout()
        for text, slot in (("Load units", self._load_units), ("Up", lambda *a: self._move_unit(-1)),
                           ("Down", lambda *a: self._move_unit(1))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            h.addWidget(b)
        cv.addLayout(h)
        f = _form()
        self.cmb_mtype = QComboBox()
        self.cmb_mtype.addItem("Layered stratigraphy (fast)")
        self.cmb_interp = QComboBox()
        tips = {"linear": "Straight lines between neighbouring boreholes (triangles). Matches your logs exactly; "
                          "surfaces are faceted. Fast and safe.",
                "smooth": "A smooth surface through all boreholes (thin-plate spline). Matches the logs and gives "
                          "rounded surfaces; values are kept within a sensible range.",
                "idw": "Weighted average of the nearest boreholes (inverse distance). Very robust; surfaces tend "
                       "to flatten around each borehole.",
                "kriging": "Ordinary kriging with a spherical variogram fitted to your data. Needs at least 6 "
                           "boreholes per unit; falls back to linear where it cannot be fitted."}
        for key, label in strat.METHODS.items():
            self.cmb_interp.addItem(label, key)
            self.cmb_interp.setItemData(self.cmb_interp.count() - 1, tips[key], Qt.ItemDataRole.ToolTipRole)
        self.cmb_interp.setCurrentIndex(max(self.cmb_interp.findData("kriging"), 0))
        self.sp_mn = _spin(5.0, 120.0, 50.0, 0, 5.0)
        self.sp_mm = _spin(0.0, 100.0, 10.0, 0, 5.0)
        f.addRow("Model type", self.cmb_mtype)
        f.addRow("Interpolation between boreholes", self.cmb_interp)
        f.addRow("Grid cells along X and Y", self.sp_mn)
        f.addRow("Extend beyond boreholes (% of site)", self.sp_mm)
        self.chk_mhull = QCheckBox("Limit the model to the outline of the boreholes (convex hull)")
        self.chk_mhull.setChecked(True)
        self.chk_mhull.setToolTip("The geology only exists inside the convex hull of all boreholes with lithology; "
                                  "outside it nothing is drawn. Press Update model after changing.")
        self.sp_mhm = _spin(0.0, 1000.0, 0.0, 1, 5.0)
        self.sp_mhm.setToolTip("Pushes the outline outward by this many metres (0 = exactly through the outer boreholes).")
        f.addRow(self.chk_mhull)
        f.addRow("Outline margin (m)", self.sp_mhm)
        self.sp_msm = _spin(0.0, 4.0, 1.0, 1, 0.5)
        self.sp_msm.setToolTip("Rounds off the geology solids so they look smooth instead of faceted (in model cells; 0 = off). "
                               "Layer order is kept. Press Update model after changing.")
        f.addRow("Smooth geology solids", self.sp_msm)
        self.cmb_interp.currentIndexChanged.connect(self._mark_model_stale)
        self.sp_mn.valueChanged.connect(self._mark_model_stale)
        self.sp_mm.valueChanged.connect(self._mark_model_stale)
        self._gbox(v, "Model settings", True, f)
        b = QPushButton("Build Model")
        b.setStyleSheet("font-weight: bold; padding: 6px;")
        b.clicked.connect(self.build_model)
        v.addWidget(b)
        self._model_groups = {}
        self._group_btn = {}
        for key, title, hint in (("model_solid", "Solid model", "The solid units appear here after you press Build Model."),
                                 ("model_contact", "Model contact", "The contact surfaces appear here after you press Build Model."),
                                 ("model_block", "Block model", "The block model appears here after you press Build Model.")):
            if key in ("model_solid", "model_contact"):        # one click shows or hides the whole group
                nm = "solid units" if key == "model_solid" else "contact surfaces"
                gb = QPushButton("Hide all " + nm)
                gb.setEnabled(False)
                gb.clicked.connect(lambda checked=False, k=key: self._toggle_group(k))
                self._group_btn[key] = (gb, nm)
                v.addWidget(gb)
            g = QgsCollapsibleGroupBox(title)
            g.setCollapsed(True)
            gl = QVBoxLayout(g)
            ph = QLabel(hint)
            ph.setWordWrap(True)
            ph.setStyleSheet("color: gray;")
            gl.addWidget(ph)
            gl.addLayout(self._rows(key))
            if key == "model_block":
                f = _form()
                self.sp_nz = _spin(10.0, 120.0, 40.0, 0, 5.0)
                self.sp_nz.editingFinished.connect(self._update_block)
                f.addRow("Cells in depth", self.sp_nz)
                gl.addLayout(f)
                tip = QLabel("To cut the block model open, use the Cut-away section.")
                tip.setWordWrap(True)
                tip.setStyleSheet("color: gray;")
                gl.addWidget(tip)
            v.addWidget(g)
            self._model_groups[key] = ph
        self.lbl_model = QLabel("No model yet. Choose collars and lithology (Drillholes), check the chronology, "
                                "then press Build Model.")
        self.lbl_model.setWordWrap(True)
        v.addWidget(self.lbl_model)
        return w

    def _sec_fence(self):
        w, v = self._body()
        note = QLabel("A fence diagram shows vertical panels of the built model along a line, or through chosen "
                      "boreholes. Build the model first.")
        note.setWordWrap(True)
        v.addWidget(note)
        f = _form()
        self.cmb_fmode = QComboBox()
        self.cmb_fmode.addItem("Along the line(s) from the 2D View tab", "line")
        self.cmb_fmode.addItem("Through boreholes, in order", "holes")
        f.addRow("Fence", self.cmb_fmode)
        self.chk_fall = QCheckBox("Use all lines in the layer")
        f.addRow(self.chk_fall)
        self.chk_floop = QCheckBox("Close the loop (back to the first borehole)")
        f.addRow(self.chk_floop)
        gb_f = QgsCollapsibleGroupBox("Fence line or boreholes")
        gb_f.setCollapsed(True)
        v.addWidget(gb_f)
        fv = QVBoxLayout(gb_f)
        fv.addLayout(f)
        fv.addWidget(QLabel("<b>Boreholes</b> (tick the ones to pass through; drag to reorder)"))
        self._fence_list = QListWidget()
        self._fence_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._fence_list.setMinimumHeight(130)
        self._fence_list.setMaximumHeight(200)
        fv.addWidget(self._fence_list)
        hb = QHBoxLayout()
        for text, slot in (("Up", lambda *a: self._fence_move(-1)), ("Down", lambda *a: self._fence_move(1)),
                           ("Order along trend", lambda *a: self._fence_trend_order())):
            b = QPushButton(text)
            b.clicked.connect(slot)
            hb.addWidget(b)
        fv.addLayout(hb)
        b = QPushButton("Draw fence")
        b.setStyleSheet("font-weight: bold; padding: 6px;")
        b.clicked.connect(self.draw_fence)
        v.addWidget(b)
        b = QPushButton("Remove fence")
        b.clicked.connect(self.remove_fence)
        v.addWidget(b)
        self._gbox(v, "Fence panels (show / hide)", True, self._rows("fence"))
        return w

    def _fill_fence_holes(self):
        """After a draw: list the boreholes (keeps the order and ticks already chosen)."""
        old = [(self._fence_list.item(i).text(), self._fence_list.item(i).checkState() == Qt.CheckState.Checked)
               for i in range(self._fence_list.count())]
        state = dict(old)
        order = [h for h, _ in old if h in self._holes]
        new = [h for h in sorted(self._holes) if h not in order]
        self._fence_list.clear()
        for h in order + new:
            it = QListWidgetItem(h)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if state.get(h, False) else Qt.CheckState.Unchecked)
            self._fence_list.addItem(it)

    def _fence_move(self, delta):
        r = self._fence_list.currentRow()
        nr = r + delta
        if r < 0 or not (0 <= nr < self._fence_list.count()):
            return
        item = self._fence_list.takeItem(r)
        self._fence_list.insertItem(nr, item)
        self._fence_list.setCurrentRow(nr)

    def _fence_trend_order(self):
        """Orders the list along the main direction of the boreholes (a sensible start for a fence)."""
        ids = [self._fence_list.item(i).text() for i in range(self._fence_list.count())]
        state = {self._fence_list.item(i).text(): self._fence_list.item(i).checkState() for i in range(self._fence_list.count())}
        if len(ids) < 2:
            return
        P = np.array([self._holes[h] for h in ids], dtype=float)
        c = P - P.mean(axis=0)
        t = c @ np.linalg.svd(c, full_matrices=False)[2][0]
        self._fence_list.clear()
        for i in np.argsort(t):
            it = QListWidgetItem(ids[i])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(state[ids[i]])
            self._fence_list.addItem(it)

    def _line_points(self, layer, fid):
        feat = layer.getFeature(fid)
        g = feat.geometry()
        pts = g.asMultiPolyline()[0] if g.isMultipart() else g.asPolyline()
        lc = self.cb_collar.currentLayer()
        if lc is not None and layer.crs() != lc.crs():
            tr = QgsCoordinateTransform(layer.crs(), lc.crs(), QgsProject.instance())
            pts = [tr.transform(QgsPointXY(p.x(), p.y())) for p in pts]
        return [(p.x(), p.y()) for p in pts]

    def _fence_polylines(self):
        if self.cmb_fmode.currentData() == "holes":
            picked = [self._fence_list.item(i).text() for i in range(self._fence_list.count())
                      if self._fence_list.item(i).checkState() == Qt.CheckState.Checked]
            if len(picked) < 2:
                self.say("Tick at least two boreholes for the fence (Fence diagram section).", warn=True)
                return []
            pts = [self._holes[h] for h in picked]
            if self.chk_floop.isChecked():
                pts.append(pts[0])
            return [pts]
        layer = self.cb_line.currentLayer()
        if layer is None:
            self.say("Choose a line layer in the 2D View tab first (or open a line shapefile there).", warn=True)
            return []
        if self.chk_fall.isChecked():
            fids = [f.id() for f in layer.getFeatures()]
        else:
            fids = [self.cmb_line.currentData()] if self.cmb_line.currentIndex() >= 0 else []
        lines = [self._line_points(layer, fid) for fid in fids]
        lines = [ln for ln in lines if len(ln) >= 2]
        if not lines:
            self.say("The chosen line layer has no usable line.", warn=True)
        return lines

    def draw_fence(self, *args):
        try:
            if self.model is None:
                self.say("Build the model first (Geological Models section); the fence is cut from it.", warn=True)
                return
            lines = self._fence_polylines()
            if not lines:
                return
            self.fence = lines
            # the solid units fill the whole model box and would hide the fence, so they are switched off
            self._fence_hid = []
            for u in self.model["units"]:
                st = self.layer_state.setdefault("Solid: %s" % u, {"visible": True, "transp": 0})
                if st.get("visible", True):
                    st["visible"] = False
                    self._fence_hid.append("Solid: %s" % u)
            self.draw(restore=True)
            if self._fence_hid:
                self.say(self.status.text() + "\nThe solid units were hidden so the fence can be seen. Tick them again "
                         "under Geological Models > Solid model.")
        except Exception:
            self.say("Could not draw the fence:\n" + traceback.format_exc(limit=3), warn=True)

    def remove_fence(self, *args):
        self.fence = None
        for name in getattr(self, "_fence_hid", []):     # bring back the solid units that the fence had hidden
            self.layer_state.setdefault(name, {})["visible"] = True
        self._fence_hid = []
        if self.vtk_widget is not None and self._was_drawn:
            self.draw(restore=True)

    def _sec_cut(self):
        w, v = self._body()
        note = QLabel("Slice the whole 3D scene with up to three planes. Solid units are filled in where they are "
                      "cut. Draw the model first; the planes then start in the middle of it.")
        note.setWordWrap(True)
        v.addWidget(note)
        defs = (("Easting (X)", "Keep the east side", "Keep the west side"),
                ("Northing (Y)", "Keep the north side", "Keep the south side"),
                ("Elevation", "Keep above the plane", "Keep below the plane"))
        self._cut_ui = []
        gb_planes = QgsCollapsibleGroupBox("Cut planes (east-west, north-south, elevation)")
        gb_planes.setCollapsed(True)
        v.addWidget(gb_planes)
        pv = QVBoxLayout(gb_planes)
        for i, (title, k_pos, k_neg) in enumerate(defs):
            chk = QCheckBox("Cut at " + title)
            chk.setStyleSheet("font-weight: bold;")
            spin = QDoubleSpinBox()
            spin.setDecimals(1)
            spin.setRange(-1e9, 1e9)
            spin.setEnabled(False)
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(0, 1000)
            sl.setEnabled(False)
            keep = QComboBox()
            keep.addItem(k_pos, 1)
            keep.addItem(k_neg, -1)
            ui = {"chk": chk, "spin": spin, "slider": sl, "keep": keep, "lo": 0.0, "hi": 1.0, "busy": False, "init": False}
            chk.toggled.connect(self._apply_cuts)
            keep.currentIndexChanged.connect(self._apply_cuts)
            spin.valueChanged.connect(lambda val, i=i: self._cut_spin(i))
            sl.valueChanged.connect(lambda val, i=i: self._cut_slider(i))
            pv.addWidget(chk)
            row = QHBoxLayout()
            row.addWidget(sl, 1)
            row.addWidget(spin)
            pv.addLayout(row)
            pv.addWidget(keep)
            self._cut_ui.append(ui)
        gb_hole = QgsCollapsibleGroupBox("Place a plane through a borehole")
        gb_hole.setCollapsed(True)
        v.addWidget(gb_hole)
        hv = QVBoxLayout(gb_hole)
        self.cmb_cut_hole = QComboBox()
        hv.addWidget(self.cmb_cut_hole)
        h = QHBoxLayout()
        for text, axis in (("Easting plane here", 0), ("Northing plane here", 1)):
            b = QPushButton(text)
            b.clicked.connect(lambda checked=False, a=axis: self._snap_to_hole(a))
            h.addWidget(b)
        hv.addLayout(h)
        b = QPushButton("Remove all cuts")
        b.clicked.connect(self._reset_cuts)
        v.addWidget(b)
        g = QgsCollapsibleGroupBox("Layers included in the cut")
        g.setCollapsed(True)
        gl = QVBoxLayout(g)
        tip = QLabel("Tick the elements that the planes should cut. Unticked ones stay whole.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray;")
        gl.addWidget(tip)
        self._cut_list = QListWidget()
        self._cut_list.setMinimumHeight(170)
        self._cut_list.itemChanged.connect(self._cut_layer_changed)
        gl.addWidget(self._cut_list)
        hb = QHBoxLayout()
        for text, on in (("Select all", True), ("Select none", False)):
            bb = QPushButton(text)
            bb.clicked.connect(lambda checked=False, o=on: self._cut_layers_all(o))
            hb.addWidget(bb)
        gl.addLayout(hb)
        v.addWidget(g)
        return w

    def _fill_cut_layers(self, registry):
        """After a draw: list the elements that can be cut (state is remembered by name)."""
        names = [e["name"] for e in registry if (self.cutter is not None and (e["name"] in self.cutter.items or e["name"] in self.cutter.labels))
                 or e["name"] == "Block model"]
        self._cut_list.blockSignals(True)
        self._cut_list.clear()
        for n in names:
            item = QListWidgetItem(n)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if self._cut_layers.get(n, True) else Qt.CheckState.Unchecked)
            self._cut_list.addItem(item)
        self._cut_list.blockSignals(False)

    def _cut_layer_changed(self, item):
        self._cut_layers[item.text()] = item.checkState() == Qt.CheckState.Checked
        self._apply_cuts()

    def _cut_layers_all(self, on):
        self._cut_list.blockSignals(True)
        for i in range(self._cut_list.count()):
            it = self._cut_list.item(i)
            it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            self._cut_layers[it.text()] = bool(on)
        self._cut_list.blockSignals(False)
        self._apply_cuts()

    def _block_cuts(self):
        """Planes for the block model, unless the block model is left out of the cut."""
        return self._cuts_real() if self._cut_layers.get("Block model", True) else []

    # ------------------------------------------------------------ cut-away planes
    def _cut_spec_real(self):
        return [(ui["chk"].isChecked(), ui["spin"].value(), ui["keep"].currentData()) for ui in self._cut_ui]

    def _cuts_real(self):
        """Active planes as (axis, value, keep) in real coordinates (used by the block model)."""
        return [(i, val, keep) for i, (on, val, keep) in enumerate(self._cut_spec_real()) if on]

    def _cut_spin(self, i):
        ui = self._cut_ui[i]
        if ui["busy"]:
            return
        ui["busy"] = True
        span = ui["hi"] - ui["lo"]
        ui["slider"].setValue(int(round(1000.0 * (ui["spin"].value() - ui["lo"]) / span)) if span > 0 else 0)
        ui["busy"] = False
        self._apply_cuts()

    def _cut_slider(self, i):
        ui = self._cut_ui[i]
        if ui["busy"]:
            return
        ui["busy"] = True
        ui["spin"].setValue(ui["lo"] + (ui["hi"] - ui["lo"]) * ui["slider"].value() / 1000.0)
        ui["busy"] = False
        self._apply_cuts()

    def _update_cut_ranges(self, info):
        """After a draw: the planes can move over the extent of the scene (real coordinates)."""
        b = self.cutter.bounds0 if self.cutter is not None else None
        if not b or b[0] > b[1] or "origin" not in info:
            return
        x0, y0, z0 = info["origin"]
        ve = info["ve"]
        ranges = [(b[0] + x0, b[1] + x0), (b[2] + y0, b[3] + y0), (b[4] / ve + z0, b[5] / ve + z0)]
        for ui, (lo, hi) in zip(self._cut_ui, ranges):
            ui["busy"] = True
            ui["lo"], ui["hi"] = lo, hi
            ui["spin"].setRange(lo, hi)
            ui["spin"].setSingleStep(max((hi - lo) / 100.0, 0.1))
            cur = ui["spin"].value()
            if not ui["init"] or not (lo <= cur <= hi):
                ui["spin"].setValue((lo + hi) / 2.0)
                ui["init"] = True
            span = hi - lo
            ui["slider"].setValue(int(round(1000.0 * (ui["spin"].value() - lo) / span)) if span > 0 else 0)
            ui["spin"].setEnabled(True)
            ui["slider"].setEnabled(True)
            ui["busy"] = False

    def _apply_cuts(self, *args, block=True):
        if (self.vtk_widget is None or self.cutter is None or not self._last_info or "origin" not in self._last_info):
            return
        x0, y0, z0 = self._last_info["origin"]
        ve = self._last_info["ve"]
        spec = []
        for i, (on, val, keep) in enumerate(self._cut_spec_real()):
            spec.append((on, (val - x0, val - y0, (val - z0) * ve)[i], keep))
        self.cutter.set_planes(spec, {n for n in list(self.cutter.items) + list(self.cutter.labels) if self._cut_layers.get(n, True)})
        if block and self._block_entry is not None:
            self._block_timer.start()
        self.vtk_widget.GetRenderWindow().Render()

    def _snap_to_hole(self, axis):
        h = self.cmb_cut_hole.currentText()
        if h not in self._holes or not self._cut_ui[axis]["spin"].isEnabled():
            self.say("Draw the model first, then choose a borehole.", warn=True)
            return
        x, y = self._holes[h]
        ui = self._cut_ui[axis]
        ui["spin"].setValue(x if axis == 0 else y)
        ui["chk"].setChecked(True)
        self._apply_cuts()

    def _reset_cuts(self):
        for ui in self._cut_ui:
            ui["chk"].setChecked(False)

    def _sec_plume(self):
        w, v = self._body()
        self.cb_plume = _layer_combo(Qgis.LayerFilter.PointLayer)
        self.f_pz = _field_combo(self.cb_plume, G_Z)
        self.f_pc = _field_combo(self.cb_plume, G_CONC)
        self.cmb_ptype = QComboBox()
        self.cmb_ptype.addItem("Soil data (one depth per sample)", "soil")
        self.cmb_ptype.addItem("Groundwater data (screen interval: top to bottom)", "gw")
        self.cmb_ptype.setToolTip("Soil results are drawn as spheres. Groundwater results come from a screened interval and are drawn as tubes.")
        self.cb_gwr = _layer_combo(Qgis.LayerFilter.PointLayer)
        self.f_gri = _field_combo(self.cb_gwr, G_ID, optional=True)
        self.f_grt = _field_combo(self.cb_gwr, ("screen_top", "screen_from", "top_m", "top", "from_m", "depth_from"))
        self.f_grb = _field_combo(self.cb_gwr, ("screen_bottom", "screen_to", "bottom_m", "bottom", "to_m", "depth_to"))
        self.f_grc = _field_combo(self.cb_gwr, G_CONC)
        self.f_grc.fieldChanged.connect(self._sync_pname)
        self.chk_gbottom = QCheckBox("Cut the plume at the bottom of the wells")
        self.chk_gbottom.setChecked(True)
        self.chk_gbottom.setToolTip("The plume stops at an imaginary surface through the deepest point of every well "
                                    "(total depth of the hole). Needs at least 3 wells with a depth.")
        self.sp_gbuf = _spin(0.0, 1000.0, 0.0, 1, 0.5)
        self.sp_gbuf.setToolTip("How far the plume may go below that surface, in metres. 0 = exactly at the surface.")
        self.sp_iso = _spin(0.0, 1e9, 0.0, 4, 1.0)
        self.sp_infl = _spin(0.0, 1e6, 0.0, 1, 10.0)
        self.sp_prad = _spin(0.1, 50.0, 1.0, 2, 0.5)
        self.sp_prad.setToolTip("Size of the sample spheres (soil) or the thickness of the screen tubes (groundwater). 1 = automatic.")
        self.sp_prad.editingFinished.connect(self._size_changed)
        self.ed_pname = QLineEdit()
        self.ed_pname.setPlaceholderText("name of the concentration column")
        self.ed_pname.setToolTip("By default this is the heading of the Concentration column. Type another name to replace it; "
                                 "clear the box to go back to the column heading.")
        self._pname_auto = True
        self.ed_pname.textEdited.connect(self._pname_edited)
        self.ed_pname.editingFinished.connect(self._pname_finished)
        self.f_pc.fieldChanged.connect(self._sync_pname)
        self.ed_punit = QLineEdit("mg/kg")
        self.ed_punit.setToolTip("Unit of the results. It is only used for the legend; the values are not converted.")
        # results written next to the samples
        self.cmb_plcol = QComboBox()
        self.cmb_plcol.addItem("Automatic (follows the theme)", "auto")
        self.cmb_plcol.addItem("My colour", "custom")
        self.btn_plcol = QgsColorButton()
        self.btn_plcol.setAllowOpacity(False)
        self.btn_plcol.setColor(QColor(0, 0, 0))
        self.btn_plcol.setEnabled(False)
        self.sp_plsize = _spin(6.0, 48.0, 11.0, 0, 1.0)
        self.cmb_plfont = QComboBox()
        for label, key in (("Arial", "arial"), ("Times", "times"), ("Courier", "courier")):
            self.cmb_plfont.addItem(label, key)
        self.chk_plbold = QCheckBox("Bold")
        self.chk_plital = QCheckBox("Italic")
        self.chk_pldecl = QCheckBox("Hide labels that overlap (the higher result stays)")
        self.chk_pldecl.setChecked(True)
        self.chk_pldecl.setToolTip("When labels crowd each other, only the one with the higher result is shown. Zoom in to see the rest.")
        self.chk_plbg = QCheckBox("Background behind the text")
        self.chk_plbg.setChecked(True)
        plh = QHBoxLayout()
        plh.addWidget(self.chk_plbold)
        plh.addWidget(self.chk_plital)
        plh.addStretch(1)
        self.sp_pldec = _spin(0.0, 6.0, 2.0, 0, 1.0)
        self.sp_ploff = _spin(0.0, 10.0, 1.0, 1, 0.5)
        self.sp_ploff.setToolTip("How far each result is placed from its sample point, diagonally up and to the right. "
                                 "1 = default distance, 0 = on top of the point, 2 = twice as far.")
        for sig in (self.ed_pname.textChanged, self.ed_punit.textChanged, self.cmb_plcol.currentIndexChanged,
                    self.btn_plcol.colorChanged, self.sp_plsize.valueChanged, self.cmb_plfont.currentIndexChanged,
                    self.chk_plbold.toggled, self.chk_plital.toggled, self.sp_pldec.valueChanged, self.chk_plbg.toggled, self.chk_pldecl.toggled,
                    self.sp_ploff.valueChanged):
            sig.connect(self._plabels_changed)
        self.chk_shells = QCheckBox("Nested shells (several levels)")
        self.chk_shells.setChecked(True)
        self.chk_shells.setToolTip("Draws one see-through shell for each level, from pale (low) to red (high). "
                                   "Switch off to draw a single isosurface at the lowest value.")
        self.chk_fade = QCheckBox("Fade the shells away from the samples")
        self.chk_fade.setChecked(True)
        self.chk_fade.setToolTip("Shows how well the data supports each part of the plume: the shells are solid close to a sample "
                                 "and become more see-through the further they are from the nearest sample.")
        self.cmb_pdisp = QComboBox()
        self.cmb_pdisp.addItem("Shells (nested surfaces)", "shells")
        self.cmb_pdisp.addItem("Cloud (smooth volume)", "cloud")
        self.cmb_pdisp.setToolTip("Shells: see-through surfaces at chosen levels. Cloud: the plume as a smooth glowing cloud, "
                                  "faint at the lowest value and dense and red where the concentration is highest.")
        self.sp_cdens = QDoubleSpinBox()
        self.sp_cdens.setRange(5.0, 100.0)
        self.sp_cdens.setDecimals(0)
        self.sp_cdens.setValue(80.0)
        self.sp_cdens.setSuffix(" %")
        self.sp_cdens.setToolTip("How dense the cloud looks (higher = more solid).")
        self.chk_cap = QCheckBox("Closed shells (not hollow)")
        self.chk_cap.setChecked(True)
        self.chk_cap.setToolTip("Where a shell is cut (ground surface, water table, below the wells, around the wells), "
                                "the shell is closed with the cut face, so it is solid instead of hollow. Switch off for open shells.")
        self.chk_cutcol = QCheckBox("Colour the cut faces by concentration")
        self.chk_cutcol.setChecked(True)
        self.chk_cutcol.setToolTip("The cut face shows the concentration as a coloured cross-section (nested bands). "
                                   "Switch off to colour each cut face with its shell colour.")
        self.sp_fadedist = QDoubleSpinBox()
        self.sp_fadedist.setRange(0.0, 100000.0)
        self.sp_fadedist.setDecimals(1)
        self.sp_fadedist.setSuffix(" m")
        self.sp_fadedist.setToolTip("Distance from the nearest sample at which the fade is strongest. 0 = automatic "
                                    "(the variogram range for kriging, otherwise the influence radius).")
        self.sp_fadestr = QDoubleSpinBox()
        self.sp_fadestr.setRange(0.0, 95.0)
        self.sp_fadestr.setDecimals(0)
        self.sp_fadestr.setValue(60.0)
        self.sp_fadestr.setSuffix(" %")
        self.sp_fadestr.setToolTip("How much opacity is lost far from the samples (0 = no fade).")
        self._mult_texts = {"mult": "1, 2.5, 6, 15", "abs": "1, 5, 10, 50, 100"}
        self._shell_mode_prev = "mult"
        self.cmb_shellmode = QComboBox()
        self.cmb_shellmode.addItem("Multiples of the lowest shell value", "mult")
        self.cmb_shellmode.addItem("Concentrations (I type the levels)", "abs")
        self.cmb_shellmode.setToolTip("Multiples: each level is the lowest value times a number. Concentrations: the numbers "
                                      "you type are the levels themselves, in the same unit as your results.")
        self.lbl_mult = QLabel("Shell levels (multipliers of the lowest value)")
        self.ed_mult = QLineEdit("1, 2.5, 6, 15")
        self.ed_mult.setToolTip("The levels are the lowest value multiplied by each of these numbers. Levels higher "
                                "than the highest result are left out.")
        self.cmb_shellcol = QComboBox()
        self.cmb_shellcol.setIconSize(QSize(110, 14))
        self.cmb_shellcol.setToolTip("One colour scale for the sample spheres, the plume shells and the legend bar. "
                                     "A shell has the same colour as a sample with the same concentration.")
        for key, (label, stops) in builders.SHELL_RAMPS.items():
            self.cmb_shellcol.addItem(_ramp_icon([tuple(c) for c in stops]), label, key)
        self.chk_clip = QCheckBox("Keep the plume below the ground surface (cut at the DEM)")
        self.chk_clip.setChecked(True)
        self.chk_clip.setToolTip("Cuts the plume exactly where it would rise above the topography. Uses the DEM; "
                                 "without a DEM it uses the ground levels of the boreholes.")
        self.chk_clip.toggled.connect(self._shells_changed)
        self.cmb_pmethod = QComboBox()
        self.cmb_pmethod.addItem("Kriging (ordinary, on log values)", "kriging")
        self.cmb_pmethod.addItem("Inverse distance (IDW)", "idw")
        self.cmb_pmethod.setToolTip("How the plume is estimated between the samples.\n"
                                    "Kriging follows the spatial pattern of your data (needs at least 8 samples; "
                                    "falls back to inverse distance if it cannot be fitted).\n"
                                    "Inverse distance is simple and robust, but spreads values out more.")
        self.chk_water = QCheckBox("Keep the soil plume above the groundwater surface")
        self.chk_water.setChecked(True)
        self.chk_water.setToolTip("Soil results come from above the water table. This cuts the plume where it would go "
                                  "below the groundwater surface (needs the groundwater levels in the Surfaces section).")
        self.chk_water.toggled.connect(self._shells_changed)
        self.chk_hull = QCheckBox("Limit the plume sideways to the area of the wells (convex hull)")
        self.chk_hull.setToolTip("Cuts the plume at the outline of the smallest convex area that contains all the wells "
                                 "(collars and plume samples), plus the buffer below.")
        self.sp_hullbuf = _spin(0.0, 100000.0, 20.0, 0, 5.0)
        self.sp_hullbuf.setToolTip("How far the plume may extend beyond the outermost wells, in metres. 0 = exactly at the outline.")
        self.chk_hull.toggled.connect(self._shells_changed)
        self.sp_hullbuf.editingFinished.connect(self._hull_buf_changed)
        self.sp_pdetail = _spin(24.0, 100.0, 48.0, 0, 4.0)
        self.sp_pdetail.setToolTip("Number of grid cells along X and Y used to draw the plume. Higher gives a finer, "
                                   "smoother surface but is slower.")
        self.sp_psmooth = _spin(0.0, 10.0, 3.0, 0, 1.0)
        self.sp_psmooth.setToolTip("Smooths the shells without shrinking them. 0 leaves them as calculated.")
        self.sp_pz = _spin(1.0, 10.0, 3.0, 1, 0.5)
        self.sp_pz.setToolTip("How much further apart two samples at different depths are treated than samples at the "
                              "same depth. 1 = depth counts like distance sideways. Larger values let samples at the same "
                              "depth influence each other over longer horizontal distances, giving a flatter, more layered "
                              "plume.")
        for w_ in (self.sp_pdetail, self.sp_psmooth, self.sp_pz):
            w_.editingFinished.connect(self._shells_changed)
        self.cmb_pmethod.currentIndexChanged.connect(self._shells_changed)
        self.chk_shells.toggled.connect(self._shells_changed)
        self.chk_fade.toggled.connect(self._shells_changed)
        self.chk_cap.toggled.connect(self._shells_changed)
        self.chk_cutcol.toggled.connect(self._shells_changed)
        self.cmb_pdisp.currentIndexChanged.connect(self._shells_changed)
        self.sp_cdens.editingFinished.connect(self._shells_changed)
        self.sp_fadedist.editingFinished.connect(self._shells_changed)
        self.sp_fadestr.editingFinished.connect(self._shells_changed)
        self.chk_shells.toggled.connect(self._shell_mode_changed)
        self.cmb_shellmode.currentIndexChanged.connect(self._shell_mode_changed)
        self.cmb_shellmode.currentIndexChanged.connect(self._shells_changed)
        self.ed_mult.editingFinished.connect(self._shells_changed)
        self.cmb_shellcol.currentIndexChanged.connect(self._shells_changed)
        self.sp_iso.editingFinished.connect(self._shells_changed)
        # ---- organised in groups that open and close
        f_data, f_est, f_shell, f_lim, f_samp = _form(), _form(), _form(), _form(), _form()
        f_data.addRow("Data type", self.cmb_ptype)
        f_data.addRow("Plume sample layer", self.cb_plume)
        f_data.addRow("Elevation (same datum as collars)", self.f_pz)
        f_data.addRow("Concentration", self.f_pc)
        f_data.addRow("Groundwater result layer", self.cb_gwr)
        f_data.addRow("Hole ID", self.f_gri)
        f_data.addRow("Screen top (depth below ground)", self.f_grt)
        f_data.addRow("Screen bottom (depth below ground)", self.f_grb)
        f_data.addRow("Concentration", self.f_grc)
        f_est.addRow("Interpolation between samples", self.cmb_pmethod)
        f_est.addRow("Influence radius (0 = auto)", self.sp_infl)
        f_est.addRow("Detail (grid cells, higher = finer but slower)", self.sp_pdetail)
        f_est.addRow("Vertical distance weight (1 = like horizontal)", self.sp_pz)
        f_shell.addRow(self.chk_shells)
        f_shell.addRow("Plume display", self.cmb_pdisp)
        f_shell.addRow("Cloud density", self.sp_cdens)
        f_shell.addRow(self.chk_cap)
        f_shell.addRow(self.chk_cutcol)
        f_shell.addRow(self.chk_fade)
        f_shell.addRow("Fade distance (0 = automatic)", self.sp_fadedist)
        f_shell.addRow("Fade strength (opacity lost far from samples)", self.sp_fadestr)
        f_shell.addRow("Lowest shell value (0 = auto, 5% of the highest)", self.sp_iso)
        f_shell.addRow("Shell levels are", self.cmb_shellmode)
        f_shell.addRow(self.lbl_mult, self.ed_mult)
        f_shell.addRow("Smooth the shells (0 = none, 10 = very smooth)", self.sp_psmooth)
        f_shell.addRow("Plume colour ramp (samples, shells, legend)", self.cmb_shellcol)
        f_lim.addRow(self.chk_clip)
        f_lim.addRow(self.chk_water)
        f_lim.addRow(self.chk_gbottom)
        f_lim.addRow("Extra depth below the deepest well points (m)", self.sp_gbuf)
        f_lim.addRow(self.chk_hull)
        f_lim.addRow("Buffer around the wells (m)", self.sp_hullbuf)
        self._f_data, self._f_lim = f_data, f_lim
        self.chk_gbottom.toggled.connect(self._shells_changed)
        self.sp_gbuf.editingFinished.connect(self._shells_changed)
        self.cmb_ptype.currentIndexChanged.connect(self._ptype_changed)

        f_samp.addRow("Sample sphere radius / tube thickness (1 = auto)", self.sp_prad)
        lbl = QLabel("<b>Results written next to the samples</b> (tick 'Plume sample labels' in the list below to show or hide)")
        lbl.setWordWrap(True)
        f_samp.addRow(lbl)
        f_samp.addRow("Font", self.cmb_plfont)
        f_samp.addRow("Size (px)", self.sp_plsize)
        f_samp.addRow("Style", plh)
        f_samp.addRow("Colour", self.cmb_plcol)
        f_samp.addRow("My colour", self.btn_plcol)
        f_samp.addRow("Offset from the sample (1 = default)", self.sp_ploff)
        f_samp.addRow("Decimal places", self.sp_pldec)
        f_samp.addRow(self.chk_plbg)
        f_samp.addRow(self.chk_pldecl)
        for title, form, collapsed, rows_key, rows_first in (
                ("Data", f_data, True, None, False),
                ("Samples and their results", f_samp, True, "plume_samples", True),
                ("Estimation between samples", f_est, True, None, False),
                ("Shells", f_shell, True, "plume_shells", False),
                ("Limits: where the plume may extend", f_lim, True, None, False)):
            gb = QgsCollapsibleGroupBox(title)
            gb.setCollapsed(collapsed)
            gl = QVBoxLayout(gb)
            if rows_key and rows_first:
                gl.addLayout(self._rows(rows_key))          # the show / hide tick boxes of this group's elements
            gl.addLayout(form)
            if rows_key and not rows_first:
                gl.addLayout(self._rows(rows_key))
            v.addWidget(gb)
        # ---- volume above a guideline value
        self.sp_vguide = _spin(0.0, 1e9, 0.0, 4, 1.0)
        self.sp_vguide.setToolTip("The plume volume counts every part of the plume at or above this value, in the same unit as your results.")
        self.sp_vpor = _spin(0.01, 0.9, 0.30, 2, 0.05)
        self.sp_vpor.setToolTip("Share of the aquifer that is water (effective porosity). Used to turn the plume volume into a volume of groundwater.")
        self.btn_vol = QPushButton("Calculate the volume")
        self.btn_vol.clicked.connect(self.calc_volume)
        self.lbl_vol = QLabel("")
        self.lbl_vol.setWordWrap(True)
        self.lbl_vol.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_vol.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f_vol = _form()
        f_vol.addRow("Guideline value (same unit as the results)", self.sp_vguide)
        f_vol.addRow("Effective porosity (groundwater only)", self.sp_vpor)
        self._f_vol = f_vol
        lv = QLabel("Volume of the plume at or above the guideline, using the same estimate and the same limits as the plume "
                    "drawn above. Draw the plume first.")
        lv.setWordWrap(True)
        self._gbox(v, "Volume above a guideline value", True, lv, f_vol, self.btn_vol, self.lbl_vol)
        g = QgsCollapsibleGroupBox("Plume legend (colour bar)")
        g.setCollapsed(True)
        gl = QVBoxLayout(g)
        gl.addLayout(self._rows("plume_bar"))
        gl.addWidget(self._sec_blegend())
        v.addWidget(g)
        v.addLayout(self._rows("plume"))
        self._ptype_apply()
        return w

    def _ground_for(self, surface, collars, xy):
        """Ground surface for cutting a plume: the DEM, else the ground levels of the boreholes. Returns (grid, source)."""
        if surface is not None:
            return surface, "DEM"
        if len(collars) >= 3 and len(xy):
            from . import model as strat
            cp = np.array([(c[0], c[1], c[2]) for c in collars.values()], dtype=float)
            xy = np.asarray(xy, dtype=float)
            mg = 0.5 * max(np.ptp(xy[:, 0]), np.ptp(xy[:, 1]), 20.0)
            gxs = np.linspace(min(cp[:, 0].min(), xy[:, 0].min()) - mg, max(cp[:, 0].max(), xy[:, 0].max()) + mg, 60)
            gys = np.linspace(min(cp[:, 1].min(), xy[:, 1].min()) - mg, max(cp[:, 1].max(), xy[:, 1].max()) + mg, 60)
            GX, GY = np.meshgrid(gxs, gys)
            return (gxs, gys, strat.interp2(cp, GX, GY, "linear", set(), "Ground")), "borehole ground levels (no DEM chosen)"
        return None, ""

    def _fade_opts(self):
        return {"on": self.chk_fade.isChecked(), "dist": self.sp_fadedist.value(), "strength": self.sp_fadestr.value() / 100.0}

    def _gw_plume_opts(self, collars, intervals, rows, surface, gw_grid, notes):
        ground, src = (None, "")
        if self.chk_clip.isChecked():
            ground, src = self._ground_for(surface, collars, [(r[0], r[1]) for r in rows])
            if ground is None:
                notes.append("The groundwater plume could not be cut at the ground: choose a DEM or at least 3 boreholes.")
        water = None
        if self.chk_water.isChecked():
            if gw_grid is not None:
                water = (gw_grid[0], gw_grid[1], gw_grid[2])
            else:
                notes.append("The groundwater plume could not be kept below the groundwater surface: add groundwater levels for at least 3 wells (Surfaces section).")
        bottom = None
        if self.chk_gbottom.isChecked():
            # the deepest point of every well: total depth from the collar, else the lithology log, else the deepest screen
            deepest = {}
            for r in rows:
                deepest[r[5]] = min(deepest.get(r[5], r[3]), r[3])
            pts = []
            for h, (x, y, z, dep) in collars.items():
                if dep is None:
                    segs = intervals.get(h)
                    dep = max(s[1] for s in segs) if segs else None
                zb = (z - dep) if dep is not None else None
                if h in deepest:
                    zb = deepest[h] if zb is None else min(zb, deepest[h])
                if zb is not None:
                    pts.append((x, y, zb))
            if len(pts) >= 3:
                bx, by, bz, bn = builders.groundwater_grid(np.array(pts, dtype=float),
                                                          {"method": self.cmb_gwmethod.currentData(), "margin": 0.4, "grid": 80})
                bottom = (bx, by, bz - self.sp_gbuf.value())
            else:
                notes.append("The plume could not be cut at the bottom of the wells: at least 3 wells with a depth are needed.")
        hull = None
        if self.chk_hull.isChecked():
            xy = [(c[0], c[1]) for c in collars.values()] + [(r[0], r[1]) for r in rows]
            hull = {"xy": np.array(xy, dtype=float), "buffer": self.sp_hullbuf.value()} if xy else None
        return {"ramp": self.cmb_shellcol.currentData(), "title": self.plume_title(), "legend": self._blegend_opts(),
                "labels": self._plabel_opts(), "tube_scale": self.sp_prad.value(),
                "shells": {"on": self.chk_shells.isChecked(), "mult": self._shell_mult(), "absolute": self.cmb_shellmode.currentData() == "abs"},
                "base": self.sp_iso.value(), "method": self.cmb_pmethod.currentData(), "detail": int(self.sp_pdetail.value()),
                "smooth": self.sp_psmooth.value(), "zaniso": self.sp_pz.value(), "influence": self.sp_infl.value(),
                "ground": ground, "ground_source": src, "water": water, "bottom": bottom, "hull": hull,
                "fade": self._fade_opts(), "cap": self.chk_cap.isChecked(), "cutcol": self.chk_cutcol.isChecked(),
                "display": self.cmb_pdisp.currentData(), "density": self.sp_cdens.value() / 100.0}

    def _conc_field(self):
        return (self.f_grc if self.cmb_ptype.currentData() == "gw" else self.f_pc).currentField()

    def _ptype_changed(self, *args):
        self._ptype_apply()
        old, new = ("mg/kg", "ug/L") if self.cmb_ptype.currentData() == "gw" else ("ug/L", "mg/kg")
        if self.ed_punit.text().strip() == old:
            self.ed_punit.setText(new)
        self._sync_pname()
        self._shells_changed()

    def _ptype_apply(self):
        """Soil or groundwater data: shows the matching input boxes and limits."""
        gw = self.cmb_ptype.currentData() == "gw"
        for w_ in (self.cb_plume, self.f_pz, self.f_pc):
            self._f_data.setRowVisible(w_, not gw)
        for w_ in (self.cb_gwr, self.f_gri, self.f_grt, self.f_grb, self.f_grc):
            self._f_data.setRowVisible(w_, gw)
        self._f_lim.setRowVisible(self.chk_gbottom, gw)
        if getattr(self, "_f_vol", None) is not None:
            self._f_vol.setRowVisible(self.sp_vpor, gw)
            self.lbl_vol.setText("")
        self._f_lim.setRowVisible(self.sp_gbuf, gw)
        if gw:
            self.chk_water.setText("Keep the plume below the groundwater surface")
            self.chk_water.setToolTip("Groundwater results come from below the water table. Cuts the plume where it would rise above the "
                                      "groundwater surface (needs the groundwater levels in the Surfaces section).")
        else:
            self.chk_water.setText("Keep the soil plume above the groundwater surface")
            self.chk_water.setToolTip("Soil results come from above the water table. This cuts the plume where it would go "
                                      "below the groundwater surface (needs the groundwater levels in the Surfaces section).")

    def calc_volume(self):
        """Volume of the drawn plume at or above the guideline value, in total and per lithology unit."""
        from . import volume as volmod
        info = self._last_info or {}
        vol = info.get("vol")
        if not vol:
            self.lbl_vol.setText("<i>Draw the plume first (it needs the results and a Draw).</i>")
            return
        want = self.cmb_ptype.currentData()
        if vol["kind"] != want:
            self.lbl_vol.setText("<i>The plume drawn is the other data type. Draw again with the data type you want.</i>")
            return
        g = self.sp_vguide.value()
        if g <= 0:
            self.lbl_vol.setText("<i>Type a guideline value above 0.</i>")
            return
        gw = vol["kind"] == "gw"
        try:
            QgsApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            r = volmod.plume_volume(vol["arrays"], g, info.get("lith_ctx"), self.sp_vpor.value() if gw else None)
        except Exception as e:
            self.lbl_vol.setText("<i>Could not calculate the volume: %s</i>" % e)
            return
        finally:
            QgsApplication.restoreOverrideCursor()
        unit = self.ed_punit.text().strip()
        what = "Groundwater plume" if gw else "Soil plume"

        def fmt(x):
            return "{:,.0f}".format(x) if x >= 100 else "{:,.1f}".format(x)
        t = "<b>%s at or above %g %s</b><br>" % (what, g, unit)
        if r["cells"] == 0:
            t += "No part of the plume reaches this value (highest value in the plume: %.4g %s)." % (vol["cmax"], unit)
            self.lbl_vol.setText(t)
            return
        t += "<b>Volume: %s m&sup3;</b>" % fmt(r["total"])
        if gw:
            t += "<br>Groundwater in that volume (porosity %.2f): %s m&sup3; (%s litres)" % (
                self.sp_vpor.value(), fmt(r["pore"]), "{:,.0f}".format(r["pore"] * 1000.0))
        if r.get("footprint"):
            z0 = (info.get("origin") or (0, 0, 0))[2]
            zr = r["z_range"]
            t += "<br>Area in plan: %s m&sup2;; elevation from %.1f to %.1f m." % (fmt(r["footprint"]), zr[0] + z0, zr[1] + z0)
        if r["units"]:
            t += "<br><br><b>By lithology unit</b><table>"
            for nm, vv in sorted(r["units"], key=lambda a: -a[1]):
                t += "<tr><td>%s&nbsp;&nbsp;</td><td align='right'>%s m&sup3;&nbsp;&nbsp;</td><td align='right'>%.0f %%</td></tr>" % (
                    nm, fmt(vv), 100.0 * vv / r["total"])
            t += "</table>"
        if r["unit_note"]:
            t += "<br><i>%s</i>" % r["unit_note"]
        if r["touches_edge"]:
            t += ("<br><br><b>Warning:</b> the plume above this value reaches the edge of the calculation area, so the volume depends on "
                  "how far the estimate extends. Tick 'Limit the plume sideways to the area of the wells' to keep it to the "
                  "investigated area.")
        t += ("<br><br><i>Estimated volume of the interpolated plume (about %s m&sup3; per calculation cell). It is only as good as "
              "the samples behind it.</i>" % ("%.3g" % r["cell"]))
        self.lbl_vol.setText(t)

    def _sec_blegend(self):
        """Controls for the plume colour bar: position, orientation, size, shape and text."""
        w, v = self._body()
        v.setContentsMargins(0, 0, 0, 0)
        f = _form()
        self.cmb_blpos = QComboBox()
        for key, label in builders.LEGEND_POSITIONS:
            self.cmb_blpos.addItem(label, key)
        self.cmb_blor = QComboBox()
        self.cmb_blor.addItem("Vertical", "vertical")
        self.cmb_blor.addItem("Horizontal", "horizontal")
        self.sp_bllen = _spin(10.0, 100.0, 50.0, 0, 5.0)
        self.sp_blthk = _spin(1.0, 15.0, 3.0, 1, 0.5)
        self.sp_blsteps = _spin(0.0, 12.0, 0.0, 0, 1.0)
        self.sp_blsteps.setToolTip("0 = a smooth colour bar. 2 to 12 = a bar made of that many separate colour steps.")
        self.sp_bllabels = _spin(2.0, 12.0, 5.0, 0, 1.0)
        self.sp_bldec = _spin(-1.0, 6.0, -1.0, 0, 1.0)
        self.sp_bldec.setToolTip("Decimal places of the numbers on the bar. -1 = automatic (3 significant digits).")
        self.sp_bltsize = _spin(6.0, 48.0, 12.0, 0, 1.0)
        self.sp_blsize = _spin(6.0, 48.0, 11.0, 0, 1.0)
        self.cmb_blfont = QComboBox()
        for label, key in (("Arial", "arial"), ("Times", "times"), ("Courier", "courier")):
            self.cmb_blfont.addItem(label, key)
        self.chk_blbold = QCheckBox("Bold")
        self.chk_blital = QCheckBox("Italic")
        bh = QHBoxLayout()
        bh.addWidget(self.chk_blbold)
        bh.addWidget(self.chk_blital)
        bh.addStretch(1)
        self.cmb_blcol = QComboBox()
        self.cmb_blcol.addItem("Automatic (follows the theme)", "auto")
        self.cmb_blcol.addItem("My colour", "custom")
        self.btn_blcol = QgsColorButton()
        self.btn_blcol.setAllowOpacity(False)
        self.btn_blcol.setColor(QColor(0, 0, 0))
        self.btn_blcol.setEnabled(False)
        self.chk_blbg = QCheckBox("Background box")
        self.chk_blborder = QCheckBox("Border")
        gh = QHBoxLayout()
        gh.addWidget(self.chk_blbg)
        gh.addWidget(self.chk_blborder)
        gh.addStretch(1)
        f.addRow("Contaminant name (legend title)", self.ed_pname)
        f.addRow("Unit (legend)", self.ed_punit)
        f.addRow("Position", self.cmb_blpos)
        f.addRow("Orientation", self.cmb_blor)
        f.addRow("Length (% of the view)", self.sp_bllen)
        f.addRow("Thickness of the bar (% of the view)", self.sp_blthk)
        f.addRow("Colour steps (0 = smooth)", self.sp_blsteps)
        f.addRow("Number of labels", self.sp_bllabels)
        f.addRow("Decimal places (-1 = auto)", self.sp_bldec)
        f.addRow("Title size (px)", self.sp_bltsize)
        f.addRow("Number size (px)", self.sp_blsize)
        f.addRow("Font", self.cmb_blfont)
        f.addRow("Style", bh)
        f.addRow("Text colour", self.cmb_blcol)
        f.addRow("My text colour", self.btn_blcol)
        f.addRow(gh)
        v.addLayout(f)
        for sig in (self.cmb_blpos.currentIndexChanged, self.cmb_blor.currentIndexChanged, self.sp_bllen.valueChanged,
                    self.sp_blthk.valueChanged, self.sp_blsteps.valueChanged, self.sp_bllabels.valueChanged,
                    self.sp_bldec.valueChanged, self.sp_bltsize.valueChanged, self.sp_blsize.valueChanged,
                    self.cmb_blfont.currentIndexChanged, self.chk_blbold.toggled, self.chk_blital.toggled,
                    self.cmb_blcol.currentIndexChanged, self.btn_blcol.colorChanged, self.chk_blbg.toggled,
                    self.chk_blborder.toggled):
            sig.connect(self._blegend_changed)
        return w

    def _blegend_opts(self):
        c = self.btn_blcol.color()
        return {"position": self.cmb_blpos.currentData(), "orientation": self.cmb_blor.currentData(),
                "length": self.sp_bllen.value(), "thickness": self.sp_blthk.value(), "steps": int(self.sp_blsteps.value()),
                "labels": int(self.sp_bllabels.value()), "decimals": int(self.sp_bldec.value()),
                "title_size": self.sp_bltsize.value(), "label_size": self.sp_blsize.value(),
                "font": self.cmb_blfont.currentData(), "bold": self.chk_blbold.isChecked(), "italic": self.chk_blital.isChecked(),
                "mode": self.cmb_blcol.currentData(), "colour": (c.redF(), c.greenF(), c.blueF()),
                "background": self.chk_blbg.isChecked(), "border": self.chk_blborder.isChecked()}

    def _blegend_changed(self, *args):
        """Legend changes show on screen straight away, without rebuilding the plume."""
        self.btn_blcol.setEnabled(self.cmb_blcol.currentData() == "custom")
        if self.vtk_widget is None or not self._was_drawn:
            return
        for e in getattr(self, "_registry", []):
            if e["name"] == "Plume scale bar":
                builders.style_plume_bar(e["actors"][0], self._blegend_opts(), builders.theme_fg(self._bgtheme()),
                                         self.vtk_widget.devicePixelRatioF(), self.plume_title())

        self.vtk_widget.GetRenderWindow().Render()

    def _hull_buf_changed(self, *args):
        if self.chk_hull.isChecked():
            self._shells_changed()

    def _hull_spec(self, collars, plume):
        """Wells for the sideways limit: every collar and every plume sample point, with the buffer in metres."""
        if not self.chk_hull.isChecked():
            return None
        xy = [(c[0], c[1]) for c in collars.values()] + [(p[0], p[1]) for p in plume]
        return {"xy": np.array(xy, dtype=float), "buffer": self.sp_hullbuf.value()} if xy else None

    def _shell_mode_changed(self, *args):
        """Switches the shell-level box between multipliers and typed concentrations."""
        mode = self.cmb_shellmode.currentData()
        if mode != self._shell_mode_prev:
            self._mult_texts[self._shell_mode_prev] = self.ed_mult.text()
            self.ed_mult.setText(self._mult_texts[mode])
            self._shell_mode_prev = mode
        if mode == "abs":
            self.lbl_mult.setText("Shell levels (concentrations, e.g. 1, 5, 10, 50, 100)")
        else:
            self.lbl_mult.setText("Shell levels (multipliers of the lowest value)")
        self.sp_iso.setEnabled(not (mode == "abs" and self.chk_shells.isChecked()))

    def _shell_mult(self):
        import re as _re
        vals = []
        for tok in _re.split(r"[,; ]+", self.ed_mult.text().strip()):
            try:
                v = float(tok)
            except ValueError:
                _log.debug('Skipped a value that is not a number', exc_info=True)
                continue
            if v > 0:
                vals.append(v)
        if self.cmb_shellmode.currentData() == "abs":
            return vals
        return vals or [1.0]

    def _sync_pname(self, *args):
        """The contaminant name follows the heading of the Concentration column until it is typed over."""
        if getattr(self, "_pname_auto", False):
            self.ed_pname.blockSignals(True)
            self.ed_pname.setText(self._conc_field())
            self.ed_pname.blockSignals(False)
            self._plabels_changed()

    def _pname_edited(self, *args):
        self._pname_auto = False

    def _pname_finished(self):
        if not self.ed_pname.text().strip():
            self._pname_auto = True
            self._sync_pname()

    def plume_title(self):
        name = self.ed_pname.text().strip() or self._conc_field() or "Concentration"
        unit = self.ed_punit.text().strip()
        return "%s (%s)" % (name, unit) if unit else name

    def _plabel_opts(self):
        c = self.btn_plcol.color()
        return {"size": self.sp_plsize.value(), "mode": self.cmb_plcol.currentData(), "colour": (c.redF(), c.greenF(), c.blueF()),
                "font": self.cmb_plfont.currentData(), "bold": self.chk_plbold.isChecked(), "italic": self.chk_plital.isChecked(),
                "decimals": int(self.sp_pldec.value()), "background": self.chk_plbg.isChecked(),
                "offset": self.sp_ploff.value(), "declutter": self.chk_pldecl.isChecked()}

    def _hlabel_opts(self):
        c = self.btn_hlcol.color()
        return {"size": self.sp_hlsize.value(), "mode": self.cmb_hlcol.currentData(), "colour": (c.redF(), c.greenF(), c.blueF()),
                "font": self.cmb_hlfont.currentData(), "bold": self.chk_hlbold.isChecked(), "italic": self.chk_hlital.isChecked(),
                "background": self.chk_hlbg.isChecked(), "declutter": self.chk_hldecl.isChecked(), "dx": self.sp_hlx.value(), "dy": self.sp_hly.value()}

    def _hlabels_changed(self, *args):
        """Borehole names: font, size, colour and so on change on screen straight away, without a rebuild."""
        self.btn_hlcol.setEnabled(self.cmb_hlcol.currentData() == "custom")
        if self.vtk_widget is None or not self._was_drawn:
            return
        for e in getattr(self, "_registry", []):
            if e["name"] == "Borehole labels":
                builders.restyle_hole_labels(e, self._hlabel_opts(), builders.theme_fg(self._bgtheme()),
                                             self.vtk_widget.devicePixelRatioF())
        self.vtk_widget.GetRenderWindow().Render()

    def _plabels_changed(self, *args):
        """Font, size, colour, decimals, name and unit change the text on screen straight away, without a rebuild."""
        self.btn_plcol.setEnabled(self.cmb_plcol.currentData() == "custom")
        if self.vtk_widget is None or not self._was_drawn:
            return
        for e in getattr(self, "_registry", []):
            if e["name"] == "Plume sample labels":
                builders.restyle_sample_labels(e, self._plabel_opts(), builders.theme_fg(self._bgtheme()),
                                               self.vtk_widget.devicePixelRatioF())
            elif e["name"] == "Plume scale bar":
                e["actors"][0].SetTitle(builders._wrap_title(self.plume_title()))
        self.vtk_widget.GetRenderWindow().Render()

    def _boundary_changed(self, *args):
        if self.vtk_widget is not None and self._was_drawn:
            self.draw(restore=True)

    def _shells_changed(self, *args):
        if self.vtk_widget is not None and self._was_drawn:
            self.draw(restore=True)

    # ------------------------------------------------------------ text boxes on the screen
    _TB_ANCHORS = (("Top left", "top_left"), ("Top centre", "top"), ("Top right", "top_right"),
                   ("Middle left", "left"), ("Centre", "center"), ("Middle right", "right"),
                   ("Bottom left", "bottom_left"), ("Bottom centre", "bottom"), ("Bottom right", "bottom_right"))

    @staticmethod
    def _tb_default():
        return {"text": "Text", "visible": True, "font": "arial", "size": 18, "bold": False, "italic": False,
                "mode": "auto", "colour": [0.0, 0.0, 0.0], "background": False, "bg_opacity": 70, "border": False,
                "anchor": "top_left", "margin": 2.0, "nx": 0.0, "ny": 0.0}

    def _sec_text(self):
        w, v = self._body()
        self._tb = []
        self._tb_cur = -1
        self._tb_loading = False
        self._tb_actors = []
        note = QLabel("Text boxes stay in the same place on the screen while you rotate the model (titles, notes, captions). "
                      "Add one, select it in the list, then type its text and set how it looks. Tick to show, untick to hide.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        v.addWidget(note)
        self.tb_list = QListWidget()
        self.tb_list.setMinimumHeight(90)
        self.tb_list.setMaximumHeight(150)
        v.addWidget(self.tb_list)
        h = QHBoxLayout()
        b = QPushButton("Add text box")
        b.clicked.connect(self._tb_add)
        h.addWidget(b)
        b = QPushButton("Remove")
        b.clicked.connect(self._tb_remove)
        h.addWidget(b)
        v.addLayout(h)
        self.tb_text = QPlainTextEdit()
        self.tb_text.setFixedHeight(84)
        self.tb_text.setPlaceholderText("Type the text here (several lines are fine)")
        self.tb_font = QComboBox()
        for label, key in (("Arial", "arial"), ("Times", "times"), ("Courier", "courier")):
            self.tb_font.addItem(label, key)
        self.tb_size = _spin(6.0, 150.0, 18.0, 0, 1.0)
        self.tb_bold, self.tb_ital = QCheckBox("Bold"), QCheckBox("Italic")
        sh = QHBoxLayout()
        sh.addWidget(self.tb_bold)
        sh.addWidget(self.tb_ital)
        sh.addStretch(1)
        self.tb_mode = QComboBox()
        self.tb_mode.addItem("Automatic (follows the theme)", "auto")
        self.tb_mode.addItem("My colour", "custom")
        self.tb_col = QgsColorButton()
        self.tb_col.setAllowOpacity(False)
        self.tb_col.setColor(QColor(0, 0, 0))
        self.tb_bg = QCheckBox("Background box")
        self.tb_bgop = _spin(0.0, 100.0, 70.0, 0, 10.0)
        self.tb_border = QCheckBox("Border")
        bh = QHBoxLayout()
        bh.addWidget(self.tb_bg)
        bh.addWidget(self.tb_border)
        bh.addStretch(1)
        self.tb_anchor = QComboBox()
        for label, key in self._TB_ANCHORS:
            self.tb_anchor.addItem(label, key)
        self.tb_margin = _spin(0.0, 45.0, 2.0, 1, 0.5)
        self.tb_nx = _spin(-100.0, 100.0, 0.0, 1, 1.0)
        self.tb_ny = _spin(-100.0, 100.0, 0.0, 1, 1.0)
        self._tb_widgets = [self.tb_text, self.tb_font, self.tb_size, self.tb_bold, self.tb_ital, self.tb_mode, self.tb_col,
                            self.tb_bg, self.tb_bgop, self.tb_border, self.tb_anchor, self.tb_margin, self.tb_nx, self.tb_ny]
        f_t, f_s, f_p = _form(), _form(), _form()
        f_t.addRow(self.tb_text)
        f_s.addRow("Font", self.tb_font)
        f_s.addRow("Size (px)", self.tb_size)
        f_s.addRow("Style", sh)
        f_s.addRow("Text colour", self.tb_mode)
        f_s.addRow("My colour", self.tb_col)
        f_s.addRow(bh)
        f_s.addRow("Background opacity (%)", self.tb_bgop)
        f_p.addRow("Place at", self.tb_anchor)
        f_p.addRow("Distance from the edge (% of the view)", self.tb_margin)
        f_p.addRow("Move right (% of the view)", self.tb_nx)
        f_p.addRow("Move up (% of the view)", self.tb_ny)
        self._gbox(v, "Text", True, f_t)
        self._gbox(v, "Font, colour and box", True, f_s)
        self._gbox(v, "Position on the screen", True, f_p)
        for sig in (self.tb_text.textChanged, self.tb_font.currentIndexChanged, self.tb_size.valueChanged, self.tb_bold.toggled,
                    self.tb_ital.toggled, self.tb_mode.currentIndexChanged, self.tb_col.colorChanged, self.tb_bg.toggled,
                    self.tb_bgop.valueChanged, self.tb_border.toggled, self.tb_anchor.currentIndexChanged,
                    self.tb_margin.valueChanged, self.tb_nx.valueChanged, self.tb_ny.valueChanged):
            sig.connect(self._tb_edited)
        self.tb_list.currentRowChanged.connect(self._tb_select)
        self.tb_list.itemChanged.connect(self._tb_checked)
        self._tb_enable(False)
        return w

    def _tb_enable(self, on):
        for w_ in self._tb_widgets:
            w_.setEnabled(on)
        if on:
            self.tb_col.setEnabled(self.tb_mode.currentData() == "custom")
            self.tb_bgop.setEnabled(self.tb_bg.isChecked())

    @staticmethod
    def _tb_label(tb):
        first = (tb.get("text") or "").strip().splitlines()[0] if (tb.get("text") or "").strip() else "(empty)"
        return first if len(first) <= 32 else first[:31] + "..."

    def _tb_fill_list(self, select=-1):
        self.tb_list.blockSignals(True)
        self.tb_list.clear()
        for tb in self._tb:
            it = QListWidgetItem(self._tb_label(tb))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if tb.get("visible", True) else Qt.CheckState.Unchecked)
            self.tb_list.addItem(it)
        self.tb_list.blockSignals(False)
        if self._tb:
            self.tb_list.setCurrentRow(min(max(select, 0), len(self._tb) - 1))
        else:
            self._tb_select(-1)

    def _tb_add(self, *args):
        tb = self._tb_default()
        tb["text"] = "Text box %d" % (len(self._tb) + 1)
        self._tb.append(tb)
        self._tb_fill_list(len(self._tb) - 1)
        self._tb_sync()

    def _tb_remove(self, *args):
        r = self.tb_list.currentRow()
        if 0 <= r < len(self._tb):
            self._tb.pop(r)
            self._tb_fill_list(min(r, len(self._tb) - 1))
            self._tb_sync()

    def _tb_select(self, row):
        self._tb_cur = row if 0 <= row < len(self._tb) else -1
        self._tb_loading = True
        try:
            if self._tb_cur < 0:
                self.tb_text.setPlainText("")
                self._tb_enable(False)
                return
            tb = self._tb[self._tb_cur]
            self.tb_text.setPlainText(tb["text"])
            self.tb_font.setCurrentIndex(max(self.tb_font.findData(tb["font"]), 0))
            self.tb_size.setValue(float(tb["size"]))
            self.tb_bold.setChecked(bool(tb["bold"]))
            self.tb_ital.setChecked(bool(tb["italic"]))
            self.tb_mode.setCurrentIndex(max(self.tb_mode.findData(tb["mode"]), 0))
            c = tb["colour"]
            self.tb_col.setColor(QColor(int(c[0] * 255), int(c[1] * 255), int(c[2] * 255)))
            self.tb_bg.setChecked(bool(tb["background"]))
            self.tb_bgop.setValue(float(tb["bg_opacity"]))
            self.tb_border.setChecked(bool(tb["border"]))
            self.tb_anchor.setCurrentIndex(max(self.tb_anchor.findData(tb["anchor"]), 0))
            self.tb_margin.setValue(float(tb["margin"]))
            self.tb_nx.setValue(float(tb["nx"]))
            self.tb_ny.setValue(float(tb["ny"]))
            self._tb_enable(True)
        finally:
            self._tb_loading = False

    def _tb_edited(self, *args):
        if self._tb_loading or not (0 <= self._tb_cur < len(self._tb)):
            return
        c = self.tb_col.color()
        tb = self._tb[self._tb_cur]
        tb.update({"text": self.tb_text.toPlainText(), "font": self.tb_font.currentData(), "size": self.tb_size.value(),
                   "bold": self.tb_bold.isChecked(), "italic": self.tb_ital.isChecked(), "mode": self.tb_mode.currentData(),
                   "colour": [c.redF(), c.greenF(), c.blueF()], "background": self.tb_bg.isChecked(),
                   "bg_opacity": self.tb_bgop.value(), "border": self.tb_border.isChecked(),
                   "anchor": self.tb_anchor.currentData(), "margin": self.tb_margin.value(),
                   "nx": self.tb_nx.value(), "ny": self.tb_ny.value()})
        self.tb_col.setEnabled(tb["mode"] == "custom")
        self.tb_bgop.setEnabled(tb["background"])
        it = self.tb_list.item(self._tb_cur)
        if it is not None:
            self.tb_list.blockSignals(True)
            it.setText(self._tb_label(tb))
            self.tb_list.blockSignals(False)
        self._tb_sync()

    def _tb_checked(self, item):
        r = self.tb_list.row(item)
        if 0 <= r < len(self._tb):
            self._tb[r]["visible"] = item.checkState() == Qt.CheckState.Checked
            self._tb_sync()

    def _tb_sync(self, render=True):
        """(Re)creates the text actors on the screen from the list. Called after every edit and after every draw."""
        if self.ren is None:
            return
        for a in getattr(self, "_tb_actors", []):
            self.ren.RemoveViewProp(a)
        self._tb_actors = []
        from vtkmodules.vtkRenderingCore import vtkTextActor
        fg = builders.theme_fg(self._bgtheme())
        dpr = self.vtk_widget.devicePixelRatioF() if self.vtk_widget is not None else 1.0
        for tb in self._tb:
            if not tb.get("visible", True) or not (tb.get("text") or "").strip():
                continue
            ta = vtkTextActor()
            ta.SetInput(tb["text"])
            ta.SetTextScaleModeToNone()
            tp = ta.GetTextProperty()
            fam = tb.get("font", "arial")
            (tp.SetFontFamilyToTimes if fam == "times" else tp.SetFontFamilyToCourier if fam == "courier" else tp.SetFontFamilyToArial)()
            tp.SetFontSize(int(round(float(tb["size"]) * max(dpr, 0.5))))
            tp.SetBold(bool(tb["bold"]))
            tp.SetItalic(bool(tb["italic"]))
            tp.SetShadow(0)
            rgb = tuple(tb["colour"]) if tb["mode"] == "custom" else tuple(fg)
            tp.SetColor(*rgb)
            lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
            bg = (1.0, 1.0, 1.0) if lum < 0.5 else (0.05, 0.06, 0.08)
            tp.SetBackgroundColor(*bg)
            tp.SetBackgroundOpacity(float(tb["bg_opacity"]) / 100.0 if tb["background"] else 0.0)
            tp.SetFrame(1 if tb["border"] else 0)
            tp.SetFrameColor(*rgb)
            a = tb["anchor"]
            m = float(tb["margin"]) / 100.0
            x = m if a.endswith("left") else (1.0 - m) if a.endswith("right") else 0.5
            y = (1.0 - m) if a.startswith("top") else m if a.startswith("bottom") else 0.5
            (tp.SetJustificationToLeft if a.endswith("left") else tp.SetJustificationToRight if a.endswith("right")
             else tp.SetJustificationToCentered)()
            (tp.SetVerticalJustificationToTop if a.startswith("top") else tp.SetVerticalJustificationToBottom
             if a.startswith("bottom") else tp.SetVerticalJustificationToCentered)()
            ta.GetPositionCoordinate().SetCoordinateSystemToNormalizedViewport()
            ta.SetPosition(x + float(tb["nx"]) / 100.0, y + float(tb["ny"]) / 100.0)
            self.ren.AddViewProp(ta)
            self._tb_actors.append(ta)
        if render and self.vtk_widget is not None:
            self.vtk_widget.GetRenderWindow().Render()

    def _sec_tools(self):
        w, v = self._body()
        g1, g2 = QVBoxLayout(), QVBoxLayout()
        b = QPushButton("Prepare data (Excel / CSV)...")
        b.clicked.connect(self.open_prep)
        g1.addWidget(b)
        b = QPushButton("Export model as web page (HTML)...")
        b.clicked.connect(self.open_export)
        g1.addWidget(b)
        b = QPushButton("Add demo data to project (9 wells)")
        b.clicked.connect(self.make_demo)
        g2.addWidget(b)
        b = QPushButton("Add demo data: 20 wells at random places (plume)")
        b.clicked.connect(self.make_demo20)
        g2.addWidget(b)
        b = QPushButton("Save the 20-well demo as an Excel workbook...")
        b.clicked.connect(self.save_demo20_workbook)
        g2.addWidget(b)
        g3 = QVBoxLayout()
        self.chk_tab2d = QCheckBox("Show the 2D View tab")
        self.chk_tab2d.setChecked(False)
        self.chk_tabtab = QCheckBox("Show the Tables tab")
        self.chk_tabtab.setChecked(False)
        self.chk_tab2d.toggled.connect(self._tabs_vis_changed)
        self.chk_tabtab.toggled.connect(self._tabs_vis_changed)
        g3.addWidget(self.chk_tab2d)
        g3.addWidget(self.chk_tabtab)
        g4 = QVBoxLayout()
        self.chk_modern = QCheckBox("Modern panel style")
        self.chk_modern.setChecked(True)
        self.chk_modern.setToolTip("Soft cards, rounded boxes and a blue accent for this panel. Untick to go back to the plain QGIS look.")
        self.chk_modern.toggled.connect(self._style_panel)
        g4.addWidget(self.chk_modern)
        self._gbox(v, "Prepare and export", False, g1)
        self._gbox(v, "Tabs (open or close)", True, g3)
        self._gbox(v, "Look", True, g4)
        self._gbox(v, "Text boxes", True, self._sec_text())
        self._gbox(v, "Demo data", True, g2)
        self._gbox(v, "Axes and grid", True, self._rows("tools"))
        return w

    # ------------------------------------------------------------ tables tab + bottom bar
    def _build_tables_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)
        h = QHBoxLayout()
        h.addWidget(QLabel("Table:"))
        self.cmb_table = QComboBox()
        self.cmb_table.setMinimumWidth(260)
        self.cmb_table.currentIndexChanged.connect(self._show_table)
        h.addWidget(self.cmb_table)
        self.lbl_table = QLabel("")
        h.addWidget(self.lbl_table, 1)
        v.addLayout(h)
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        v.addWidget(self.table, 1)
        return w

    def _fill_tables(self):
        self.cmb_table.blockSignals(True)
        self.cmb_table.clear()
        self._table_layers = []
        for label, cb in (("Collars", self.cb_collar), ("Lithology", self.cb_lith), ("Plume samples", self.cb_plume)):
            lyr = cb.currentLayer()
            if lyr is not None:
                self._table_layers.append(lyr)
                self.cmb_table.addItem("%s  (%s)" % (label, lyr.name()))
        self.cmb_table.blockSignals(False)
        self._show_table()

    def _show_table(self, *args):
        i = self.cmb_table.currentIndex()
        self.table.clear()
        self.table.setRowCount(0)
        if i < 0 or i >= len(self._table_layers):
            self.table.setColumnCount(0)
            self.lbl_table.setText("No layers chosen yet.")
            return
        lyr = self._table_layers[i]
        names = [f.name() for f in lyr.fields()]
        self.table.setColumnCount(len(names))
        self.table.setHorizontalHeaderLabels(names)
        limit = 3000
        rows = []
        for n, feat in enumerate(lyr.getFeatures()):
            if n >= limit:
                break
            rows.append(feat.attributes())
        self.table.setRowCount(len(rows))
        for r, attrs in enumerate(rows):
            for c, val in enumerate(attrs):
                txt = "" if val is None or str(val) == "NULL" else str(val)
                self.table.setItem(r, c, QTableWidgetItem(txt))
        total = lyr.featureCount()
        self.lbl_table.setText("%d row(s)%s" % (total, " (first %d shown)" % limit if total > limit else ""))

    def _build_bottom(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 0, 2, 0)
        v.setSpacing(2)
        self.status = _StatusBox(fit=False)
        self.info_win = _InfoWindow(self, self.status)
        h = QHBoxLayout()
        hint = QLabel("Left-drag rotate | right/middle-drag or Shift+left-drag pan | wheel zoom")
        hint.setStyleSheet("font-size: 8pt; color: gray;")
        h.addWidget(hint)
        h.addStretch(1)
        v.addLayout(h)
        return w

    def _size_changed(self):
        """Redraw (keeping the view) when the borehole or plume-sample radius was edited."""
        now = (self.sp_rad.value(), self.sp_prad.value())
        if now != getattr(self, "_last_sizes", (1.0, 1.0)):
            self._last_sizes = now
            if self.vtk_widget is not None and self._was_drawn:
                self.draw(restore=True)

    def _ve_changed(self):
        if self.sp_ve.value() != self._last_ve:
            self._last_ve = self.sp_ve.value()
            if self.vtk_widget is not None and self._was_drawn:
                self.draw(restore=True)

    # ------------------------------------------------------------ theme / background
    def _bgtheme(self):
        """'light' when the background is light (so text must be dark), else 'dark'."""
        if self.custom_bg is not None:
            c = self.custom_bg
            return "light" if (0.299 * c.redF() + 0.587 * c.greenF() + 0.114 * c.blueF()) > 0.5 else "dark"
        return self.theme

    def _apply_background(self):
        if self.ren is None:
            return
        if self.custom_bg is not None:
            c = self.custom_bg
            self.ren.GradientBackgroundOff()
            self.ren.SetBackground(c.redF(), c.greenF(), c.blueF())
        elif self.theme == "light":
            self.ren.SetBackground(1.0, 1.0, 1.0)
            self.ren.SetBackground2(0.80, 0.89, 0.98)
            self.ren.GradientBackgroundOn()
        else:
            self.ren.SetBackground(0.10, 0.11, 0.14)
            self.ren.SetBackground2(0.28, 0.31, 0.38)
            self.ren.GradientBackgroundOn()
        if self._orient is not None:
            col = (0.1, 0.1, 0.1) if self._bgtheme() == "light" else (0.95, 0.95, 0.95)
            ax = self._orient.GetOrientationMarker()
            for cap in (ax.GetXAxisCaptionActor2D(), ax.GetYAxisCaptionActor2D(), ax.GetZAxisCaptionActor2D()):
                cap.GetCaptionTextProperty().SetColor(*col)

    def _theme_changed(self, idx):
        self.theme = "light" if idx == 0 else "dark"
        self.custom_bg = None
        try:
            from qgis.core import QgsSettings
            QgsSettings().setValue("AVGeoTools3D/theme", self.theme)
        except Exception:
            _log.debug("could not save theme", exc_info=True)
        self._style_panel()
        if self.vtk_widget is not None and self._was_drawn:
            self.draw(restore=True)
        else:
            self._apply_background()
            if self.vtk_widget is not None:
                self.vtk_widget.GetRenderWindow().Render()

    def _bg_changed(self, color):
        self.custom_bg = QColor(color)
        if self.vtk_widget is not None and self._was_drawn:
            self.draw(restore=True)
        else:
            self._apply_background()
            if self.vtk_widget is not None:
                self.vtk_widget.GetRenderWindow().Render()

    # ------------------------------------------------------------ 3D view
    def _ensure_view(self):
        if self.vtk_widget is not None:
            return
        from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
        from vtkmodules.vtkRenderingCore import vtkRenderer
        import vtkmodules.vtkInteractionWidgets  # noqa: F401
        from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget
        from vtkmodules.vtkRenderingAnnotation import vtkAxesActor
        self.placeholder.hide()
        self.vtk_widget = QVTKRenderWindowInteractor(self.view_holder)
        self.view_layout.addWidget(self.vtk_widget, 1)
        self.ren = vtkRenderer()
        try:
            self.ren.UseFXAAOn()                       # smoother edges
        except AttributeError:
            _log.debug("FXAA is not available", exc_info=True)
        rw = self.vtk_widget.GetRenderWindow()
        rw.AddRenderer(self.ren)
        iren = rw.GetInteractor()
        iren.SetInteractorStyle(make_style())
        iren.Initialize()
        self._orient = vtkOrientationMarkerWidget()
        self._orient.SetOrientationMarker(vtkAxesActor())
        self._orient.SetInteractor(iren)
        self._orient.SetViewport(0.84, 0.78, 1.0, 1.0)
        self._orient.SetEnabled(1)
        self._orient.InteractiveOff()
        self._apply_background()

    def _store_camera(self):
        if self.ren is not None and self._was_drawn:
            c = self.ren.GetActiveCamera()
            self._cam = (c.GetPosition(), c.GetFocalPoint(), c.GetViewUp())
            self._cam_info = self._last_info

    def _apply_camera(self, info=None):
        """Puts the camera back. Positions are converted through real-world coordinates, so the view stays
        on the same spot even if the data or the vertical exaggeration changed."""
        if self._cam is None or self.ren is None:
            return
        pos, foc, up = self._cam
        old = self._cam_info
        if old and info and "origin" in info and "origin" in old:
            ox, oy, oz = old["origin"]
            nx, ny, nz = info["origin"]
            ove, nve = old["ve"], info["ve"]

            def conv(p):
                return (p[0] + ox - nx, p[1] + oy - ny, ((p[2] / ove + oz) - nz) * nve)
            pos, foc = conv(pos), conv(foc)
        c = self.ren.GetActiveCamera()
        c.SetPosition(*pos)
        c.SetFocalPoint(*foc)
        c.SetViewUp(*up)
        self.ren.ResetCameraClippingRange()
        self._cam = None

    def cleanup(self):
        if self.vtk_widget is not None:
            try:
                if self._orient is not None:
                    self._orient.SetEnabled(0)
                self.vtk_widget.Finalize()
            except Exception:
                _log.debug('Could not finalise the 3D widget', exc_info=True)
            self._orient = None
            self.view_layout.removeWidget(self.vtk_widget)
            self.vtk_widget.setParent(None)
            self.vtk_widget.deleteLater()
            self.vtk_widget = None
            self.ren = None
            self.cutter = None
            self._clear_layers()

    def ensure_on_screen(self):
        """Keeps the floating window fully visible (it can be restored off-screen or taller than the screen)."""
        if not self.isFloating():
            return
        from qgis.PyQt.QtWidgets import QApplication
        avail = QApplication.primaryScreen().availableGeometry()
        w = min(self.width(), avail.width() - 60)
        h = min(self.height(), avail.height() - 90)
        if (w, h) != (self.width(), self.height()):
            self.resize(w, h)
        x = max(avail.left() + 10, min(self.x(), avail.right() - w - 10))
        y = max(avail.top() + 10, min(self.y(), avail.bottom() - h - 40))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    # A hidden OpenGL view comes back blank (white). So the view is thrown away when the window is
    # closed or another tab is shown, and rebuilt from the chosen layers when it is needed again.
    def hideEvent(self, event):
        if not event.spontaneous() and self.vtk_widget is not None:
            self._store_camera()
            self.cleanup()
            self.placeholder.show()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(50, self.ensure_on_screen)
        if self._was_drawn and self.vtk_widget is None and self.tabs.currentIndex() == 0:
            QTimer.singleShot(150, lambda: self.draw(restore=True))

    def _on_tab(self, i):
        if i == 0:
            if self._skip_tab_draw:
                self._skip_tab_draw = False
                return
            if self._was_drawn and self.vtk_widget is None:
                QTimer.singleShot(100, lambda: self.draw(restore=True))
        else:
            self._store_camera()
            self.cleanup()
            self.placeholder.show()
            if self.tabs.tabText(i) == "Tables":
                self._fill_tables()

    # ------------------------------------------------------------ left-hand layer rows
    def _group_boxes(self, key):
        lay = self.rows.get(key)
        if lay is None:
            return []
        out = []
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            cb = w.findChild(QCheckBox) if w is not None else None
            if cb is not None:
                out.append(cb)
        return out

    def _toggle_group(self, key):
        boxes = self._group_boxes(key)
        if not boxes:
            self.say("Build the model first (Geological Models section).", warn=True)
            return
        target = not all(cb.isChecked() for cb in boxes)      # all shown -> hide them all; otherwise show them all
        for cb in boxes:
            cb.setChecked(target)
        self._update_group_buttons()

    def _update_group_buttons(self):
        for key, (btn, nm) in getattr(self, "_group_btn", {}).items():
            boxes = self._group_boxes(key)
            btn.setEnabled(bool(boxes))
            btn.setText(("Hide all " if boxes and all(cb.isChecked() for cb in boxes) else "Show all ") + nm)

    def _update_group_hints(self):
        for key, ph in getattr(self, "_model_groups", {}).items():
            lay = self.rows.get(key)
            ph.setVisible(lay is None or lay.count() == 0)
        self._update_group_buttons()

    def _clear_layers(self):
        for lay in self.rows.values():
            while lay.count():
                item = lay.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
        self._update_group_hints()

    def _refresh_layers(self, registry):
        """Fills each section with show/hide + transparency rows for the elements just drawn."""
        self._clear_layers()
        self._block_entry = None
        for entry in registry:
            name = entry["name"]
            actors = entry["actors"]
            if name == "Block model":
                self._block_entry = entry
            st = self.layer_state.get(name)
            if st is None:
                transp = 0
                if entry["fade"]:
                    transp = int(round((1.0 - actors[0].GetProperty().GetOpacity()) * 100))
                st = self.layer_state[name] = {"visible": entry.get("default_visible", True), "transp": transp}
            entry["user_visible"] = bool(st["visible"])
            for a in actors:
                a.SetVisibility(1 if st["visible"] else 0)
                if entry["fade"]:
                    a.GetProperty().SetOpacity(1.0 - st["transp"] / 100.0)
            row = QWidget()
            rv = QVBoxLayout(row)
            rv.setContentsMargins(0, 2, 0, 4)
            cb = QCheckBox(name)
            cb.setChecked(st["visible"])
            rv.addWidget(cb)
            cb.toggled.connect(lambda on, e=entry: self._set_visible(e, on))
            if entry["fade"]:
                h = QHBoxLayout()
                h.addWidget(QLabel("Transparency"))
                sl = QSlider(Qt.Orientation.Horizontal)
                sl.setRange(0, 100)
                sl.setValue(st["transp"])
                lab = QLabel("%d%%" % st["transp"])
                lab.setMinimumWidth(34)
                h.addWidget(sl, 1)
                h.addWidget(lab)
                rv.addLayout(h)
                sl.valueChanged.connect(lambda val, e=entry, lb=lab: self._set_transparency(e, val, lb))
            self.rows[_section_of(name)].addWidget(row)
        self._update_group_hints()

    def _set_visible(self, entry, on):
        self.layer_state.setdefault(entry["name"], {})["visible"] = bool(on)
        entry["user_visible"] = bool(on)
        for a in entry["actors"]:
            a.SetVisibility(1 if on else 0)
        if self.vtk_widget is not None:
            self.vtk_widget.GetRenderWindow().Render()
        self._update_group_buttons()

    def _set_transparency(self, entry, val, label):
        self.layer_state.setdefault(entry["name"], {})["transp"] = int(val)
        label.setText("%d%%" % val)
        for a in entry["actors"]:
            a.GetProperty().SetOpacity(1.0 - val / 100.0)
        if self.vtk_widget is not None:
            self.vtk_widget.GetRenderWindow().Render()

    # ------------------------------------------------------------ actions
    def say(self, text, warn=False):
        self.status.setText(text)
        self.status.setStyleSheet("color: #b00020;" if warn else "")
        if getattr(self, "info_win", None) is not None:
            self.info_win.set_error(text if warn else "")
        if warn and getattr(self, "info_win", None) is not None and not self.info_win.isVisible():       # a warning should not go unnoticed
            self.toggle_info()

    def _tabs_vis_changed(self, *args):
        """Tools: show or hide the 2D View and Tables tabs (the 3D View always stays)."""
        for chk, name in ((self.chk_tab2d, "2D View"), (self.chk_tabtab, "Tables")):
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == name:
                    if not chk.isChecked() and self.tabs.currentIndex() == i:
                        self.tabs.setCurrentIndex(0)
                    self.tabs.setTabVisible(i, chk.isChecked())

    def toggle_info(self, *args):
        if self.info_win.isVisible():
            self.info_win.hide()
        else:
            self.info_win.show()
            self.info_win.raise_()

    def update_model(self, *args):
        """Toolbar button: redraw but keep the current view (the first draw frames the model).
        If the model settings were changed since the model was built, the model is rebuilt first."""
        if self.model is not None and self._model_stale:
            self.build_model()
            return
        self.draw(restore=True)

    def draw(self, *args, restore=False):
        try:
            self._draw(restore)
        except Exception:
            self.say("Something went wrong:\n" + traceback.format_exc(limit=3), warn=True)

    def _read_drill(self):
        """(collars, intervals) from the Drillholes section, or None (with a message) if a column is missing."""
        lc, ll = self.cb_collar.currentLayer(), self.cb_lith.currentLayer()
        collars, intervals = {}, {}
        if lc is not None:
            if not (self.f_cid.currentField() and self.f_cz.currentField()):
                self.say("Choose the Hole ID and Ground elevation fields for the collars (Drillholes section).", warn=True)
                return None
            collars = data.read_collars(lc, self.f_cid.currentField(), self.f_cz.currentField(),
                                        self.f_cd.currentField())
            if ll is not None:
                if not (self.f_lid.currentField() and self.f_lfrom.currentField() and self.f_lto.currentField()):
                    self.say("Choose Hole ID, From and To fields for the lithology (Drillholes section).", warn=True)
                    return None
                intervals = data.read_intervals(ll, self.f_lid.currentField(), self.f_lfrom.currentField(),
                                                self.f_lto.currentField(), self.f_lcode.currentField())
        return collars, intervals

    def _draw(self, restore):
        if restore and self.ren is not None and self._cam is None:
            self._store_camera()
        if self.tabs.currentIndex() != 0:
            self._skip_tab_draw = True
            self.tabs.setCurrentIndex(0)
        lc = self.cb_collar.currentLayer()
        lr, lp = self.cb_raster.currentLayer(), self.cb_plume.currentLayer()
        gw_mode = self.cmb_ptype.currentData() == "gw"
        if not any([lc, lr, lp]):
            self.say("Pick at least a collar layer, a DEM or a plume layer.", warn=True)
            return
        notes = []
        crs = {l.crs().authid() for l in (lc, lr, lp) if l is not None}
        if len(crs) > 1:
            notes.append("Warning: layers use different CRS (%s). Reproject them to one projected CRS." % ", ".join(sorted(crs)))
        drill = self._read_drill()
        if drill is None:
            return
        collars, intervals = drill
        self._holes = {h: (c[0], c[1]) for h, c in collars.items()}
        self.cmb_cut_hole.blockSignals(True)
        cur_hole = self.cmb_cut_hole.currentText()
        self.cmb_cut_hole.clear()
        self.cmb_cut_hole.addItems(sorted(self._holes))
        if cur_hole in self._holes:
            self.cmb_cut_hole.setCurrentText(cur_hole)
        self.cmb_cut_hole.blockSignals(False)
        self._fill_fence_holes()
        plume = np.zeros((0, 4))
        if lp is not None and not gw_mode:
            if not (self.f_pz.currentField() and self.f_pc.currentField()):
                self.say("Choose the Elevation and Concentration fields for the plume (Plumes section).", warn=True)
                return
            plume = data.read_plume(lp, self.f_pz.currentField(), self.f_pc.currentField())

        surface = None
        if lr is not None:
            xy = [(c[0], c[1]) for c in collars.values()] + [(p[0], p[1]) for p in plume]
            bbox = None
            if xy:
                a = np.array(xy)
                mx = max(20.0, 0.25 * max(np.ptp(a[:, 0]), np.ptp(a[:, 1])))
                bbox = (a[:, 0].min() - mx, a[:, 1].min() - mx, a[:, 0].max() + mx, a[:, 1].max() + mx)
            surface = data.read_raster(lr, bbox)
            if surface is not None and abs(self.sp_demoff.value()) > 1e-9:
                surface = (surface[0], surface[1], np.asarray(surface[2], float) + self.sp_demoff.value()) + tuple(surface[3:])
            if surface is None:
                notes.append("The DEM does not overlap your points (check the CRS).")

        drape = None
        if surface is not None and self.cb_drape.currentLayer() is not None:
            drape = self._make_drape(surface, lr, notes)
        elif surface is None and self.cb_drape.currentLayer() is not None:
            notes.append("An image is chosen but there is no DEM: choose the DEM raster first (Surfaces section).")
        lg = self.cb_gw.currentLayer()
        gw_pts = gw_grid = None
        gw_opts = {"method": self.cmb_gwmethod.currentData(), "interval": self.sp_gwint.value(),
                   "label_size": self.sp_gwlab.value(), "margin": self.sp_gwm.value() / 100.0,
                   "flat": self.chk_gwflat.isChecked()}
        if lg is not None and (self.f_gwz.currentField() or self.f_gwd.currentField()):
            ground_at = None
            if lr is not None:
                def ground_at(x, y, _p=lr.dataProvider()):
                    val, ok = _p.sample(QgsPointXY(x, y), 1)
                    return val if ok else None
            gw_pts = data.read_groundwater(lg, self.f_gwz.currentField(), self.f_gwd.currentField(),
                                           self.f_gwid.currentField(), collars, ground_at)
            if len(gw_pts) >= 3:
                gw_grid = builders.groundwater_grid(gw_pts, gw_opts)
        water = None
        if len(plume) and self.chk_water.isChecked():
            if gw_grid is not None:
                water = (gw_grid[0], gw_grid[1], gw_grid[2])
            else:
                notes.append("The plume could not be kept above the groundwater: add groundwater levels for at least 3 wells (Surfaces section).")
        plume_nd = data.read_plume_nd(lp, self.f_pz.currentField(), self.f_pc.currentField()) if len(plume) else None
        ground, ground_src = None, ""
        if len(plume) and self.chk_clip.isChecked():
            if surface is not None:
                ground, ground_src = surface, "DEM"
            elif len(collars) >= 3:
                from . import model as strat
                cp = np.array([(c[0], c[1], c[2]) for c in collars.values()], dtype=float)
                mg = 0.5 * max(np.ptp(plume[:, 0]), np.ptp(plume[:, 1]), 20.0)
                gxs = np.linspace(min(cp[:, 0].min(), plume[:, 0].min()) - mg, max(cp[:, 0].max(), plume[:, 0].max()) + mg, 60)
                gys = np.linspace(min(cp[:, 1].min(), plume[:, 1].min()) - mg, max(cp[:, 1].max(), plume[:, 1].max()) + mg, 60)
                GX, GY = np.meshgrid(gxs, gys)
                ground = (gxs, gys, strat.interp2(cp, GX, GY, "linear", set(), "Ground"))
                ground_src = "borehole ground levels (no DEM chosen)"
            else:
                notes.append("The plume could not be cut at the ground: choose a DEM or at least 3 boreholes.")
        self._ensure_view()
        self._apply_background()
        self._push_style()
        bgt = self._bgtheme()
        registry, info = [], {}
        use_model = self.model is not None and bool(collars)
        self._model_draw = _model_mod.clip_to_surface(self.model, surface, self.sp_modgap.value()) if use_model else self.model
        if use_model and self.chk_mhull.isChecked():
            hxy = [(c[0], c[1]) for h, c in collars.items() if intervals.get(h)]
            self._model_draw = _model_mod.limit_to_hull(self._model_draw, hxy, self.sp_mhm.value())
        notes += builders.build_scene(self.ren, collars, intervals, surface, plume,
                                      ve=self.sp_ve.value(), radius=self.sp_rad.value(), sample_radius=self.sp_prad.value(),
                                      iso=self.sp_iso.value(), influence=self.sp_infl.value(),
                                      registry=registry, theme=bgt, info=info,
                                      plume_method=self.cmb_pmethod.currentData(),
                                      plume_opts={"detail": int(self.sp_pdetail.value()), "smooth": self.sp_psmooth.value(),
                                                  "zaniso": self.sp_pz.value(), "ground": ground, "ground_source": ground_src,
                                                  "water": water, "labels": self._plabel_opts(), "nd": plume_nd,
                                                  "hull": self._hull_spec(collars, plume), "fade": self._fade_opts(), "cap": self.chk_cap.isChecked(), "cutcol": self.chk_cutcol.isChecked(),
                                                   "display": self.cmb_pdisp.currentData(), "density": self.sp_cdens.value() / 100.0,
                                                  "dpr": self.vtk_widget.devicePixelRatioF() if self.vtk_widget is not None else 1.0},
                                      plume_title=self.plume_title(), plume_legend=self._blegend_opts(), drape=drape,
                                      shells={"on": self.chk_shells.isChecked(), "mult": self._shell_mult(), "absolute": self.cmb_shellmode.currentData() == "abs",
                                              "colours": self.cmb_shellcol.currentData()},
                                      geology={"on": False})
        self._fill_lith_table(info.get("codes") or {})
        lb = self.cb_bound.currentLayer()
        if lb is not None:
            if surface is None:
                notes.append("A site boundary is chosen but there is no DEM: choose the DEM raster first (Surfaces section).")
            else:
                try:
                    cb_ = self.btn_bcol.color()
                    notes += builders.add_boundary(self.ren, data.read_boundary(lb, lr.crs()), surface, info, self.sp_ve.value(),
                                                   {"colour": (cb_.redF(), cb_.greenF(), cb_.blueF()),
                                                    "width": self.sp_bwidth.value(), "lift": self.sp_blift.value()}, registry)
                except Exception:
                    notes.append("The site boundary could not be drawn:\n" + traceback.format_exc(limit=2))
        self._fill_lith_table(info.get("codes") or {})
        if use_model:
            notes += builders.add_model(self.ren, self._model_draw, info, self.sp_ve.value(), nz=int(self.sp_nz.value()),
                                        cuts=self._block_cuts(), registry=registry, smooth=self.sp_msm.value())
            if self.fence:
                notes += builders.add_fence(self.ren, self._model_draw, info, self.sp_ve.value(), self.fence, registry=registry)
        if lg is not None:
            if not (self.f_gwz.currentField() or self.f_gwd.currentField()):
                notes.append("Groundwater: choose the water-level or the depth-to-water column (Surfaces section).")
            else:
                notes += builders.add_groundwater(self.ren, gw_pts, info, self.sp_ve.value(), dict(gw_opts, grid_data=gw_grid),
                                                  registry=registry, dpr=self.vtk_widget.devicePixelRatioF())
        lgr = self.cb_gwr.currentLayer() if gw_mode else None
        if gw_mode and lgr is None:
            notes.append("Groundwater plume: choose the groundwater result layer (Plumes section, Data).")
        if lgr is not None:
            if not (self.f_grt.currentField() and self.f_grb.currentField() and self.f_grc.currentField()):
                notes.append("Groundwater plume: choose the screen top, screen bottom and concentration fields (Plumes section, Data).")
            else:
                gwrows, gwnd, skipped = data.read_gw_results(lgr, self.f_gri.currentField(), self.f_grt.currentField(),
                                                             self.f_grb.currentField(), self.f_grc.currentField(), collars)
                if skipped:
                    notes.append("Groundwater plume: %d result(s) skipped (no matching collar or missing value)." % skipped)
                gopts = self._gw_plume_opts(collars, intervals, gwrows, surface, gw_grid, notes)
                notes += builders.add_gw_plume(self.ren, gwrows, gwnd, info, self.sp_ve.value(), gopts, registry,
                                               builders.theme_fg(bgt), self.vtk_widget.devicePixelRatioF())
        builders.add_axes(self.ren, info, bgt, registry)
        self._refresh_layers(registry)
        self._registry = registry
        for e_ in registry:
            if e_["name"] == "Borehole labels":
                builders.restyle_hole_labels(e_, self._hlabel_opts(), builders.theme_fg(bgt), self.vtk_widget.devicePixelRatioF())
        builders.reset_camera(self.ren)
        if restore:
            self._apply_camera(info)
        else:
            self._cam = None
        self._last_info = dict(info)
        self.cutter = cutmod.CutController(self.ren)
        self.cutter.attach(registry)
        self._fill_cut_layers(registry)
        self._tb_actors = []                       # build_scene cleared the scene: put the text boxes back
        self._tb_sync(render=False)
        self._update_cut_ranges(info)
        self._apply_cuts(block=False)
        self.vtk_widget.GetRenderWindow().Render()
        self._was_drawn = True
        self.lbl_vol.setText("")
        self.say("\n".join(notes))

    # ------------------------------------------------------------ stratigraphic model
    def _load_units(self, *args):
        drill = self._read_drill()
        if drill is None:
            return
        collars, intervals = drill
        if not collars or not intervals:
            self.say("Choose collars and lithology first (Drillholes section).", warn=True)
            return
        default = strat.default_order(collars, intervals)
        keep = [self.chron.item(i).text() for i in range(self.chron.count())]
        order = [u for u in keep if u in default] + [u for u in default if u not in keep]
        self.chron.clear()
        self.chron.addItems(order)
        self.say("%d unit(s) loaded. Drag them, or use Up / Down, so the youngest is at the top." % len(order))

    def _move_unit(self, delta):
        r = self.chron.currentRow()
        nr = r + delta
        if r < 0 or not (0 <= nr < self.chron.count()):
            return
        item = self.chron.takeItem(r)
        self.chron.insertItem(nr, item)
        self.chron.setCurrentRow(nr)
        self._mark_model_stale()

    def _mark_model_stale(self, *args):
        """The model settings changed after the model was built: say so, so the old model is not mistaken for new."""
        if self.model is not None:
            self._model_stale = True
            self.lbl_model.setText(self._model_text + "\n\nSettings changed since this model was built. "
                                   "Press Build Model (or Update model) to apply them.")

    def _clear_model(self, *args):
        if self.model is not None:
            self.model = None
            self._model_stale = False
            self._block_entry = None
            self.lbl_model.setText("The boreholes changed, so the model was cleared. Press Build Model again.")

    def build_model(self, *args):
        try:
            drill = self._read_drill()
            if drill is None:
                return
            collars, intervals = drill
            if not collars or not intervals:
                self.say("A model needs collars and lithology (Drillholes section).", warn=True)
                return
            names = {c for h in collars for a, b, c in intervals.get(h, [])}
            have = [self.chron.item(i).text() for i in range(self.chron.count())]
            if not have or not names.issubset(set(have)):
                self._load_units()
                have = [self.chron.item(i).text() for i in range(self.chron.count())]
            n = int(self.sp_mn.value())
            mdl, notes = strat.build_model(collars, intervals, have, nx=n, ny=n, margin=self.sp_mm.value() / 100.0,
                                      method=self.cmb_interp.currentData())
            if mdl is None:
                self.model = None
                self.say("\n".join(notes), warn=True)
                return
            self.model = mdl
            lines = ["Built from %d borehole(s). Units from top to bottom:" % mdl["n_holes"]]
            for u, vol, mt in zip(mdl["units"], mdl["vol"], mdl["mean_t"]):
                lines.append("%s: %s m3, mean thickness %.1f m" % (u, format(vol, ",.0f"), mt))
            self._model_text = "\n".join(lines + notes)
            self._model_stale = False
            self.lbl_model.setText(self._model_text)
            self.draw(restore=self._was_drawn)
        except Exception:
            self.say("Could not build the model:\n" + traceback.format_exc(limit=3), warn=True)

    def _mdl_view(self):
        """The model as drawn (unit tops clipped to the DEM); the plain model if nothing was drawn yet."""
        md = getattr(self, "_model_draw", None)
        return md if (self.model is not None and md is not None) else self.model

    def _update_block(self):
        """Rebuilds only the block model (after a cut slider or the depth cells changed)."""
        if (self.vtk_widget is None or self.model is None or self._block_entry is None
                or not self._last_info or "origin" not in self._last_info):
            return
        info = self._last_info
        new = builders.make_block_actor(self._mdl_view(), info, info["ve"], int(self.sp_nz.value()), self._block_cuts(), info.get("codes"))
        st = self.layer_state.get("Block model", {})
        new.SetVisibility(1 if st.get("visible", False) else 0)
        new.GetProperty().SetOpacity(1.0 - st.get("transp", 0) / 100.0)
        old = self._block_entry["actors"][0]
        self.ren.RemoveActor(old)
        self.ren.AddActor(new)
        self._block_entry["actors"] = [new]
        self.vtk_widget.GetRenderWindow().Render()

    def set_view(self, mode):
        if self.vtk_widget is not None:
            builders.set_view(self.ren, mode)
            self.vtk_widget.GetRenderWindow().Render()

    def save_png(self):
        if self.vtk_widget is None:
            self.say("Draw something first.", warn=True)
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save picture", "subsurface3d.png", "PNG (*.png)")
        if not path:
            return
        from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter
        from vtkmodules.vtkIOImage import vtkPNGWriter
        w2i = vtkWindowToImageFilter()
        w2i.SetInput(self.vtk_widget.GetRenderWindow())
        w2i.SetScale(2)
        w2i.Update()
        wr = vtkPNGWriter()
        wr.SetFileName(path)
        wr.SetInputConnection(w2i.GetOutputPort())
        wr.Write()
        self.say("Saved " + path)

    # ------------------------------------------------------------ save / open a setup
    def _setup_items(self):
        return {
            "layers": {"collar": self.cb_collar, "lith": self.cb_lith, "raster": self.cb_raster, "plume": self.cb_plume, "gw": self.cb_gw, "gwr": self.cb_gwr, "drape": self.cb_drape, "bound": self.cb_bound},
            "fields": {"c_id": self.f_cid, "c_z": self.f_cz, "c_d": self.f_cd, "l_id": self.f_lid,
                       "l_from": self.f_lfrom, "l_to": self.f_lto, "l_code": self.f_lcode,
                       "p_z": self.f_pz, "p_c": self.f_pc, "gw_id": self.f_gwid, "gw_z": self.f_gwz, "gw_d": self.f_gwd,
                       "gr_id": self.f_gri, "gr_top": self.f_grt, "gr_bot": self.f_grb, "gr_c": self.f_grc},
            "values": {"ve": self.sp_ve, "rad": self.sp_rad, "prad": self.sp_prad, "iso": self.sp_iso, "infl": self.sp_infl,
                       "mn": self.sp_mn, "mm": self.sp_mm, "nz": self.sp_nz, "gwint": self.sp_gwint, "gwlab": self.sp_gwlab, "gwm": self.sp_gwm, "drape_px": self.sp_drape, "demoff": self.sp_demoff, "bwidth": self.sp_bwidth, "blift": self.sp_blift, "msm": self.sp_msm, "mhm": self.sp_mhm, "modgap": self.sp_modgap},
        }

    def save_setup(self):
        """Save: writes to the setup file already in use (opened or saved before); asks for a file only the first time."""
        if self._setup_path:
            self._write_setup(self._setup_path)
        else:
            self.save_setup_as()

    def save_setup_as(self):
        """Save as: always asks for a file name."""
        start = self._setup_path or "subsurface_setup.json"
        path, _ = QFileDialog.getSaveFileName(self, "Save setup as", start, "Setup (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        self._write_setup(path)

    def _write_setup(self, path):
        items = self._setup_items()
        out = {
            "layers": {k: (cb.currentLayer().name() if cb.currentLayer() else None) for k, cb in items["layers"].items()},
            "fields": {k: fc.currentField() for k, fc in items["fields"].items()},
            "values": {k: sp.value() for k, sp in items["values"].items()},
            "theme": self.theme,
            "background": self.custom_bg.name() if self.custom_bg is not None else None,
            "layer_state": self.layer_state,
            "chronology": [self.chron.item(i).text() for i in range(self.chron.count())],
            "interpolation": self.cmb_interp.currentData(),
            "lithology_style": self.lith_style,
            "shells": {"on": self.chk_shells.isChecked(), "mult": self.ed_mult.text(), "colours": self.cmb_shellcol.currentData(),
                       "mode": self.cmb_shellmode.currentData(),
                       "texts": dict(self._mult_texts, **{self.cmb_shellmode.currentData(): self.ed_mult.text()})},
            "cap_faces": self.chk_cap.isChecked(), "cut_colour": self.chk_cutcol.isChecked(),
            "plume_display": {"mode": self.cmb_pdisp.currentData(), "density": self.sp_cdens.value()},
            "fade": {"on": self.chk_fade.isChecked(), "dist": self.sp_fadedist.value(), "strength": self.sp_fadestr.value()},
            "plume_method": self.cmb_pmethod.currentData(),
            "gw_method": self.cmb_gwmethod.currentData(),
            "plume_opts": {"detail": self.sp_pdetail.value(), "smooth": self.sp_psmooth.value(), "zaniso": self.sp_pz.value(),
                           "clip": self.chk_clip.isChecked(), "model_hull": self.chk_mhull.isChecked(), "above_water": self.chk_water.isChecked(),
                           "hull": self.chk_hull.isChecked(), "hull_buffer": self.sp_hullbuf.value()},
            "plume_legend": {"name": self.ed_pname.text(), "unit": self.ed_punit.text(), "name_auto": self._pname_auto},
            "text_boxes": self._tb,
            "tabs": {"2d": self.chk_tab2d.isChecked(), "tables": self.chk_tabtab.isChecked()},
            "plume_type": self.cmb_ptype.currentData(),
            "gw_bottom": {"on": self.chk_gbottom.isChecked(), "extra": self.sp_gbuf.value()},
            "volume": {"guideline": self.sp_vguide.value(), "porosity": self.sp_vpor.value()},
            "plume_labels": self._plabel_opts(),
            "hole_labels": self._hlabel_opts(),
            "boundary_colour": self.btn_bcol.color().name(),
            "plume_legend_style": self._blegend_opts(),
        }
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=2)
            self._set_setup_path(path)
            self.say("Setup saved to " + path)
        except Exception as e:
            self.say("Could not save the setup: %s" % e, warn=True)

    def new_scene(self, *args, ask=True):
        """New: empties the 3D scene and the layer choices so a new model can be started. Layers in the QGIS project are not touched."""
        if ask:
            from qgis.PyQt.QtWidgets import QMessageBox
            r = QMessageBox.question(self, "New scene",
                                     "Clear the 3D scene, the model, the text boxes and the layers chosen here, and start a new one?\n\n"
                                     "Your layers in the QGIS project are not removed, and your settings stay as they are. "
                                     "Save the setup first if you want to come back to this one.")
            if r != QMessageBox.StandardButton.Yes:
                return
        try:
            for cb in (self.cb_collar, self.cb_lith, self.cb_raster, self.cb_plume, self.cb_line, self.cb_gw, self.cb_gwr, self.cb_drape):
                cb.setLayer(None)
            self.model = None
            self._model_stale = False
            self._block_entry = None
            self.fence = None
            self.chron.clear()
            self.lbl_model.setText("")
            self.layer_state = {}
            self._drape_cache = None
            self._clear_layers()
            self._tb = []
            self._tb_fill_list(-1)
            self._tb_actors = []
            if self.ren is not None:
                self.ren.RemoveAllViewProps()
                builders._drop_legend(self.ren)
                self._registry = []
                self.cutter = cutmod.CutController(self.ren)
                self.cutter.attach([])
                self._fill_cut_layers([])
                if self.vtk_widget is not None:
                    self.vtk_widget.GetRenderWindow().Render()
            self._last_info = None
            self._was_drawn = False
            self._cam = None
            self._setup_path = None
            self._update_title()
            if self.btn_save is not None:
                self.btn_save.setToolTip("Save this setup (layers, columns, settings). The first time it asks for a file name; "
                                         "after that it saves to the same file.")
            self.say("New empty scene. Choose your layers on the left, then click Update model.")
        except Exception:
            self.say("Could not clear the scene:\n" + traceback.format_exc(limit=3), warn=True)

    def _update_title(self):
        """The title bar shows the setup file in use, or says that none is saved yet."""
        name = os.path.basename(self._setup_path) if self._setup_path else "no setup saved yet"
        self.setWindowTitle("AV GeoTools 3D  -  %s" % name)
        if self._setup_path:
            self.setToolTip("Setup file: %s" % self._setup_path)

    def _set_setup_path(self, path):
        self._setup_path = path
        self._update_title()
        if getattr(self, "btn_save", None) is not None:
            import os
            self.btn_save.setToolTip("Save the setup to %s" % os.path.basename(path))

    def open_setup(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open setup", "", "Setup (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
            self._set_setup_path(path)
            items = self._setup_items()
            missing = []
            for k, cb in items["layers"].items():
                name = cfg.get("layers", {}).get(k)
                if name:
                    found = QgsProject.instance().mapLayersByName(name)
                    if found:
                        cb.setLayer(found[0])
                    else:
                        missing.append(name)
            for k, fc in items["fields"].items():
                val = cfg.get("fields", {}).get(k)
                if val:
                    fc.setField(val)
            for k, sp in items["values"].items():
                if k == "rad" and "prad" not in cfg.get("values", {}):
                    continue   # older setups used a different meaning (0 = auto)
                if k in cfg.get("values", {}):
                    sp.setValue(float(cfg["values"][k]))
            self._last_ve = self.sp_ve.value()
            self.layer_state = cfg.get("layer_state", {}) or {}
            if cfg.get("lithology_style"):
                st = _default_lith_style()
                st.update({k: v for k, v in cfg["lithology_style"].items() if k in st})
                st["legend"] = {**_default_lith_style()["legend"], **st.get("legend", {})}
                self.lith_style = st
                self._push_style()
                self._sync_legend_widgets()
                self._lith_units = []
            self.cmb_pmethod.setCurrentIndex(max(self.cmb_pmethod.findData(cfg.get("plume_method", "kriging")), 0))
            self.cmb_gwmethod.setCurrentIndex(max(self.cmb_gwmethod.findData(cfg.get("gw_method", "kriging")), 0))
            po = cfg.get("plume_opts") or {}
            self.sp_pdetail.setValue(float(po.get("detail", 48)))
            self.sp_psmooth.setValue(float(po.get("smooth", 3)))
            self.sp_pz.setValue(float(po.get("zaniso", 3)))
            self.chk_clip.setChecked(bool(po.get("clip", True)))
            self.chk_mhull.setChecked(bool(po.get("model_hull", True)))
            self.chk_water.setChecked(bool(po.get("above_water", True)))
            self.chk_hull.setChecked(bool(po.get("hull", False)))
            self.sp_hullbuf.setValue(float(po.get("hull_buffer", 20)))
            pg = cfg.get("plume_legend") or {}
            self._pname_auto = bool(pg.get("name_auto", not pg.get("name")))
            self.ed_pname.setText(pg.get("name", ""))
            if self._pname_auto:
                self._sync_pname()
            self.ed_punit.setText(pg.get("unit", "mg/kg"))
            pb = cfg.get("plume_legend_style") or {}
            if pb:
                self.cmb_blpos.setCurrentIndex(max(self.cmb_blpos.findData(pb.get("position", "right")), 0))
                self.cmb_blor.setCurrentIndex(max(self.cmb_blor.findData(pb.get("orientation", "vertical")), 0))
                self.sp_bllen.setValue(float(pb.get("length", 50)))
                self.sp_blthk.setValue(float(pb.get("thickness", 3)))
                self.sp_blsteps.setValue(float(pb.get("steps", 0)))
                self.sp_bllabels.setValue(float(pb.get("labels", 5)))
                self.sp_bldec.setValue(float(pb.get("decimals", -1)))
                self.sp_bltsize.setValue(float(pb.get("title_size", 14)))
                self.sp_blsize.setValue(float(pb.get("label_size", 12)))
                self.cmb_blfont.setCurrentIndex(max(self.cmb_blfont.findData(pb.get("font", "arial")), 0))
                self.chk_blbold.setChecked(bool(pb.get("bold", False)))
                self.chk_blital.setChecked(bool(pb.get("italic", False)))
                self.cmb_blcol.setCurrentIndex(max(self.cmb_blcol.findData(pb.get("mode", "auto")), 0))
                c = pb.get("colour", (0, 0, 0))
                self.btn_blcol.setColor(QColor(int(c[0] * 255), int(c[1] * 255), int(c[2] * 255)))
                self.chk_blbg.setChecked(bool(pb.get("background", False)))
                self.chk_blborder.setChecked(bool(pb.get("border", False)))
            if cfg.get("boundary_colour"):
                self.btn_bcol.setColor(QColor(cfg["boundary_colour"]))
            hf = cfg.get("hole_labels") or {}
            if hf:
                self.sp_hlsize.setValue(float(hf.get("size", 13)))
                self.cmb_hlcol.setCurrentIndex(max(self.cmb_hlcol.findData(hf.get("mode", "auto")), 0))
                c = hf.get("colour", (0, 0, 0))
                self.btn_hlcol.setColor(QColor(int(c[0] * 255), int(c[1] * 255), int(c[2] * 255)))
                self.cmb_hlfont.setCurrentIndex(max(self.cmb_hlfont.findData(hf.get("font", "arial")), 0))
                self.chk_hlbold.setChecked(bool(hf.get("bold", True)))
                self.chk_hlital.setChecked(bool(hf.get("italic", False)))
                self.chk_hlbg.setChecked(bool(hf.get("background", True)))
                self.chk_hldecl.setChecked(bool(hf.get("declutter", True)))
                k_ = float(hf.get("offset", 1.0))          # older setups stored one distance
                self.sp_hlx.setValue(float(hf.get("dx", 6.0 * k_)))
                self.sp_hly.setValue(float(hf.get("dy", 6.0 * k_)))
            pf = cfg.get("plume_labels") or {}
            if pf:
                self.sp_plsize.setValue(float(pf.get("size", 13)))
                self.cmb_plcol.setCurrentIndex(max(self.cmb_plcol.findData(pf.get("mode", "auto")), 0))
                c = pf.get("colour", (0, 0, 0))
                self.btn_plcol.setColor(QColor(int(c[0] * 255), int(c[1] * 255), int(c[2] * 255)))
                self.cmb_plfont.setCurrentIndex(max(self.cmb_plfont.findData(pf.get("font", "arial")), 0))
                self.chk_plbold.setChecked(bool(pf.get("bold", False)))
                self.chk_plital.setChecked(bool(pf.get("italic", False)))
                self.sp_pldec.setValue(float(pf.get("decimals", 2)))
                self.chk_plbg.setChecked(bool(pf.get("background", True)))
                self.sp_ploff.setValue(float(pf.get("offset", 1.0)))
                self.chk_pldecl.setChecked(bool(pf.get("declutter", True)))
            self.chk_cap.setChecked(bool(cfg.get("cap_faces", True)))
            self.chk_cutcol.setChecked(bool(cfg.get("cut_colour", True)))
            pdc = cfg.get("plume_display") or {}
            self.cmb_pdisp.setCurrentIndex(max(self.cmb_pdisp.findData(pdc.get("mode", "shells")), 0))
            self.sp_cdens.setValue(float(pdc.get("density", 80.0)))
            fdc = cfg.get("fade")
            if fdc:
                self.chk_fade.setChecked(bool(fdc.get("on", True)))
                self.sp_fadedist.setValue(float(fdc.get("dist", 0.0)))
                self.sp_fadestr.setValue(float(fdc.get("strength", 60.0)))
            sh = cfg.get("shells")
            if sh:
                self.chk_shells.setChecked(bool(sh.get("on", True)))
                self._mult_texts.update(sh.get("texts") or {})
                mode = sh.get("mode", "mult")
                self._mult_texts[mode] = str(sh.get("mult", self._mult_texts.get(mode, "1, 2.5, 6, 15")))
                self._shell_mode_prev = mode
                self.cmb_shellmode.blockSignals(True)
                self.cmb_shellmode.setCurrentIndex(max(self.cmb_shellmode.findData(mode), 0))
                self.cmb_shellmode.blockSignals(False)
                self.ed_mult.setText(self._mult_texts[mode])
                self._shell_mode_changed()
                self.cmb_shellcol.setCurrentIndex(max(self.cmb_shellcol.findData(builders.norm_ramp(sh.get("colours", "rainbow"))), 0))
            if "tabs" in cfg:
                self.chk_tab2d.setChecked(bool(cfg["tabs"].get("2d", False)))
                self.chk_tabtab.setChecked(bool(cfg["tabs"].get("tables", False)))
            if "text_boxes" in cfg:
                self._tb = [dict(self._tb_default(), **tb) for tb in (cfg.get("text_boxes") or [])]
                self._tb_fill_list(0)
                self._tb_sync(render=False)
            self.cmb_ptype.blockSignals(True)
            self.cmb_ptype.setCurrentIndex(max(self.cmb_ptype.findData(cfg.get("plume_type", "soil")), 0))
            self.cmb_ptype.blockSignals(False)
            self._ptype_apply()
            gb = cfg.get("gw_bottom") or {}
            self.chk_gbottom.setChecked(bool(gb.get("on", True)))
            self.sp_gbuf.setValue(float(gb.get("extra", 0.0)))
            vo = cfg.get("volume") or {}
            self.sp_vguide.setValue(float(vo.get("guideline", 0.0)))
            self.sp_vpor.setValue(float(vo.get("porosity", 0.30)))
            ix = self.cmb_interp.findData(cfg.get("interpolation", "kriging"))
            if ix >= 0:
                self.cmb_interp.setCurrentIndex(ix)
            if cfg.get("chronology"):
                self.chron.clear()
                self.chron.addItems(cfg["chronology"])
            if cfg.get("theme") == "dark":
                self.cmb_theme.setCurrentIndex(1)
            elif cfg.get("theme") == "light":
                self.cmb_theme.setCurrentIndex(0)
            if cfg.get("background"):
                self.custom_bg = QColor(cfg["background"])
            msg = "Setup loaded. Click Update model."
            if missing:
                msg += "\nThese layers are not in the project: " + ", ".join(missing)
            self.say(msg, warn=bool(missing))
        except Exception as e:
            self.say("Could not open the setup: %s" % e, warn=True)

    # ------------------------------------------------------------ 2D cross-section tab
    def _build_section_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Line layer"))
        self.cb_line = _layer_combo(Qgis.LayerFilter.LineLayer)
        self.cb_line.layerChanged.connect(self._fill_lines)
        r1.addWidget(self.cb_line, 2)
        b = QPushButton("Open line shapefile...")
        b.clicked.connect(self.open_line_file)
        r1.addWidget(b)
        r1.addWidget(QLabel("Line"))
        self.cmb_line = QComboBox()
        r1.addWidget(self.cmb_line, 1)
        v.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Show boreholes within (m)"))
        self.sp_buf = _spin(0.0, 100000.0, 25.0, 0, 5.0)
        r2.addWidget(self.sp_buf)
        r2.addWidget(QLabel("Vertical exaggeration"))
        self.sp_sve = _spin(0.1, 100.0, 1.0, 1, 1.0)
        r2.addWidget(self.sp_sve)
        self.chk_sfit = QCheckBox("Fit to window")
        self.chk_sfit.setToolTip("Stretch the section to fill the window (ignores the exaggeration).")
        r2.addWidget(self.chk_sfit)
        self.chk_smodel = QCheckBox("Model")
        self.chk_sholes = QCheckBox("Boreholes")
        self.chk_splume = QCheckBox("Plume")
        for c_ in (self.chk_smodel, self.chk_sholes, self.chk_splume):
            c_.setChecked(True)
            r2.addWidget(c_)
        r2.addStretch(1)
        b = QPushButton("Create section")
        b.setStyleSheet("font-weight: bold; padding: 5px 12px;")
        b.clicked.connect(self.create_section)
        r2.addWidget(b)
        v.addLayout(r2)
        self.sec_layout = QVBoxLayout()
        self.sec_placeholder = QLabel("Choose a line layer (or open a line shapefile), pick the line, then press "
                                      "Create section.\nThe section uses the boreholes, the built model and the plume "
                                      "chosen on the left.")
        self.sec_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sec_layout.addWidget(self.sec_placeholder, 1)
        v.addLayout(self.sec_layout, 1)
        return w

    def _fill_lines(self, *args):
        self.cmb_line.clear()
        layer = self.cb_line.currentLayer()
        if layer is None:
            return
        name_field = None
        for f in layer.fields():
            if f.typeName().lower() in ("string", "text", "varchar") or "char" in f.typeName().lower():
                name_field = f.name()
                break
        for n, feat in enumerate(layer.getFeatures()):
            if n >= 500:
                break
            label = ""
            if name_field:
                val = feat[name_field]
                label = "" if val is None or str(val) == "NULL" else str(val)
            self.cmb_line.addItem(label or "Line %d" % (n + 1), feat.id())

    def open_line_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open a line shapefile", "",
                                              "Line data (*.shp *.gpkg *.geojson *.json *.dxf);;All files (*.*)")
        if not path:
            return
        import os
        layer = QgsVectorLayer(path, os.path.splitext(os.path.basename(path))[0], "ogr")
        if not layer.isValid():
            self.say("Could not read " + path, warn=True)
            return
        if layer.geometryType() != Qgis.GeometryType.Line:
            self.say("That file does not contain lines.", warn=True)
            return
        QgsProject.instance().addMapLayer(layer)
        self.cb_line.setLayer(layer)
        self.say("Line layer added to the project and selected.")

    def _ensure_section_canvas(self):
        if self.sec_canvas is not None:
            return
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
        from matplotlib.figure import Figure
        self.sec_fig = Figure(layout="constrained", facecolor="white")
        self.sec_canvas = FigureCanvasQTAgg(self.sec_fig)
        self.sec_toolbar = NavigationToolbar2QT(self.sec_canvas, self)
        self.sec_placeholder.hide()
        self.sec_layout.addWidget(self.sec_toolbar)
        self.sec_layout.addWidget(self.sec_canvas, 1)

    def create_section(self, *args):
        try:
            from . import section
            self._push_style()
            layer = self.cb_line.currentLayer()
            if layer is None or self.cmb_line.currentIndex() < 0:
                self.say("Choose a line layer and a line first (2D View tab).", warn=True)
                return
            feat = layer.getFeature(self.cmb_line.currentData())
            g = feat.geometry()
            pts = g.asMultiPolyline()[0] if g.isMultipart() else g.asPolyline()
            if len(pts) < 2:
                self.say("The chosen line has fewer than two points.", warn=True)
                return
            lc, lp = self.cb_collar.currentLayer(), self.cb_plume.currentLayer()
            if lc is not None and layer.crs() != lc.crs():
                tr = QgsCoordinateTransform(layer.crs(), lc.crs(), QgsProject.instance())
                pts = [tr.transform(QgsPointXY(p.x(), p.y())) for p in pts]
            pts = [(p.x(), p.y()) for p in pts]
            drill = self._read_drill()
            if drill is None:
                return
            collars, intervals = drill
            plume = np.zeros((0, 4))
            if lp is not None and self.f_pz.currentField() and self.f_pc.currentField():
                plume = data.read_plume(lp, self.f_pz.currentField(), self.f_pc.currentField())
            if not collars and not len(plume) and self.model is None:
                self.say("Nothing to show: choose collars (Drillholes), a plume layer, or build a model.", warn=True)
                return
            codes = (self._last_info or {}).get("codes") or {
                nm: i + 1 for i, nm in enumerate(sorted({c for segs in intervals.values() for a, b, c in segs}))}
            opts = {"buffer": self.sp_buf.value(), "ve": self.sp_sve.value(), "fit": self.chk_sfit.isChecked(),
                    "show_model": self.chk_smodel.isChecked(), "show_holes": self.chk_sholes.isChecked(),
                    "show_plume": self.chk_splume.isChecked(), "iso": self.sp_iso.value(),
                    "influence": self.sp_infl.value(), "title": self.cmb_line.currentText(),
                    "plume_method": self.cmb_pmethod.currentData(), "plume_zaniso": self.sp_pz.value(),
                    "plume_ramp": self.cmb_shellcol.currentData(),
                    "plume_title": self.plume_title(), "plume_hull": self._hull_spec(collars, plume)}
            self._ensure_section_canvas()
            notes = section.make_section(self.sec_fig, pts, collars, intervals, plume, self._mdl_view(), codes, opts)
            self.sec_canvas.draw()
            self.say("\n".join(notes))
        except Exception:
            self.say("Could not create the section:\n" + traceback.format_exc(limit=3), warn=True)

    # ------------------------------------------------------------ data tools
    def open_export(self, *args):
        try:
            from .export_ui import ExportDialog
            if self.export_dlg is None:
                self.export_dlg = ExportDialog(self.iface.mainWindow(), self)
            self.export_dlg.show()
            self.export_dlg.raise_()
            self.export_dlg.activateWindow()
        except Exception:
            self.say("Could not open the export window:\n" + traceback.format_exc(limit=3), warn=True)

    def open_prep(self):
        try:
            from .prep_ui import PrepareDialog
            if self.prep is None:
                self.prep = PrepareDialog(self.iface.mainWindow(), self.select_layers)
            self.prep.show()
            self.prep.raise_()
            self.prep.activateWindow()
        except Exception:
            self.say("Could not open the data preparation window:\n" + traceback.format_exc(limit=3), warn=True)

    def select_layers(self, layers):
        c, l, p = layers.get("SS_Collars"), layers.get("SS_Lithology"), layers.get("SS_Plume")
        if c is not None:
            self.cb_collar.setLayer(c)
        if l is not None:
            self.cb_lith.setLayer(l)
        if p is not None:
            self.cb_plume.setLayer(p)
        self.say("Prepared layers selected. Click Update model.")

    def _demo_look(self):
        """The demo opens with vertical exaggeration 3 and a 'Demo Model' title in the corner."""
        self.sp_ve.setValue(3.0)
        if not any((tb.get("text") or "").strip() == "Demo Model" for tb in self._tb):
            tb = self._tb_default()
            tb.update({"text": "Demo Model", "size": 18.0, "anchor": "top_left", "ny": -12.0})
            self._tb.append(tb)
            self._tb_fill_list(len(self._tb) - 1)
            self._tb_sync()

    def make_demo20(self):
        try:
            proj = QgsProject.instance()
            old_ids = [l.id() for l in proj.mapLayers().values() if l.name().startswith(("Demo_", "Demo20_"))]
            if old_ids:
                for cb in (self.cb_collar, self.cb_lith, self.cb_raster, self.cb_plume, self.cb_line, self.cb_gw, self.cb_gwr, self.cb_drape):
                    cb.setLayer(None)
                proj.removeMapLayers(old_ids)
            collars, lith, plume, dem, lines, gwl, gwr, sat = demo.create_demo20_layers()
            for l in (gwr, gwl, lines, sat, dem, plume, lith, collars):
                proj.addMapLayer(l)
            self.cb_collar.setLayer(collars)
            self.cb_lith.setLayer(lith)
            self.cb_raster.setLayer(dem)
            self.cb_plume.setLayer(plume)
            self.cb_line.setLayer(lines)
            self.cb_gw.setLayer(gwl)
            self.cb_gwr.setLayer(gwr)
            self.cb_drape.setLayer(sat)
            self._demo_look()
            self.say("Demo with 20 wells at random places added. Click Update model, then Load units and Build Model.")
        except Exception:
            self.say("Could not create the 20-well demo:\n" + traceback.format_exc(limit=3), warn=True)

    def save_demo20_workbook(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save the demo workbook", "demo_20_wells.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            demo.write_demo20_workbook(path)
            self.say("Saved %s. Open it with Prepare data (Tools) to try the whole preparation workflow." % path)
        except Exception as e:
            self.say("Could not save the workbook: %s" % e, warn=True)

    def make_demo(self):
        try:
            proj = QgsProject.instance()
            old_ids = [l.id() for l in proj.mapLayers().values() if l.name().startswith(("Demo_", "Demo20_"))]
            if old_ids:
                for cb in (self.cb_collar, self.cb_lith, self.cb_raster, self.cb_plume, self.cb_line, self.cb_gw, self.cb_gwr, self.cb_drape):
                    cb.setLayer(None)
                proj.removeMapLayers(old_ids)
            collars, lith, plume, dem = demo.create_demo_layers()
            lines = demo.create_demo_line()
            for l in (lines, dem, plume, lith, collars):
                proj.addMapLayer(l)
            self.cb_line.setLayer(lines)
            self.cb_collar.setLayer(collars)
            self.cb_lith.setLayer(lith)
            self.cb_raster.setLayer(dem)
            self.cb_plume.setLayer(plume)
            self._demo_look()
            self.say("Demo layers added. Click Update model.")
        except Exception:
            self.say("Could not create demo data:\n" + traceback.format_exc(limit=3), warn=True)
