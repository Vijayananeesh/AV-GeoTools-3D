import os
import traceback
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                                 QCheckBox, QLineEdit, QComboBox, QFormLayout)
from qgis.core import QgsProject
from qgis.gui import QgsFileWidget
from . import htmlexport


class ExportDialog(QDialog):
    def __init__(self, parent, dock):
        super().__init__(parent)
        self.dock = dock
        self.setWindowTitle("Export the model as a web page")
        self.resize(560, 660)
        v = QVBoxLayout(self)
        tip = QLabel("Saves ONE HTML file with an embedded 3D viewer. Send it to anyone: it opens in any modern "
                     "browser, with no internet and no QGIS. In the page you can rotate, show or hide each element, "
                     "change its transparency, cut the model with planes and change the vertical exaggeration.")
        tip.setWordWrap(True)
        v.addWidget(tip)
        f = QFormLayout()
        self.title = QLineEdit("Subsurface model")
        f.addRow("Page title", self.title)
        self.theme = QComboBox()
        self.theme.addItem("Light", "light")
        self.theme.addItem("Dark", "dark")
        f.addRow("Starts with the", self.theme)
        v.addLayout(f)
        v.addWidget(QLabel("<b>Elements to include</b> (hidden ones can be included too; the page lets viewers "
                           "switch them on):"))
        self.lst = QListWidget()
        self.lst.setMinimumHeight(240)
        v.addWidget(self.lst)
        hb = QHBoxLayout()
        for text, on in (("Select all", True), ("Select none", False)):
            b = QPushButton(text)
            b.clicked.connect(lambda checked=False, o=on: self._tick_all(o))
            hb.addWidget(b)
        v.addLayout(hb)
        self.chk_labels = QCheckBox("Include borehole labels")
        self.chk_labels.setChecked(True)
        v.addWidget(self.chk_labels)
        v.addWidget(QLabel("Save as:"))
        self.path = QgsFileWidget()
        self.path.setStorageMode(QgsFileWidget.StorageMode.SaveFile)
        self.path.setFilter("Web page (*.html)")
        v.addWidget(self.path)
        self.chk_open = QCheckBox("Open it in my browser afterwards")
        self.chk_open.setChecked(True)
        v.addWidget(self.chk_open)
        b = QPushButton("Export web page")
        b.setStyleSheet("font-weight: bold; padding: 7px;")
        b.clicked.connect(self.export_page)
        v.addWidget(b)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)

    def showEvent(self, event):
        super().showEvent(event)
        self._fill()

    def _fill(self):
        self.lst.clear()
        els = htmlexport.exportable_elements(self.dock._registry)
        for name, a, visible, fade in els:
            it = QListWidgetItem(name + ("" if visible else "   (hidden now)"))
            it.setData(Qt.ItemDataRole.UserRole, name)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked if name == "Block model" else Qt.CheckState.Checked)
            self.lst.addItem(it)
        if not els:
            self.say("Draw the model in the 3D view first, then export.")

    def _tick_all(self, on):
        for i in range(self.lst.count()):
            self.lst.item(i).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def say(self, text, warn=False):
        self.msg.setText(text)
        self.msg.setStyleSheet("color: #b00020;" if warn else "")

    def _overlay(self, dk):
        """Legends and text boxes as they are set in the plugin, drawn over the web page's 3D view."""
        def shown(name):
            return bool(dk.layer_state.get(name, {}).get("visible", True))
        lg = dict(dk.lith_style.get("legend", {}))
        lg["show"] = bool(lg.get("show", True)) and shown("Lithology legend")
        pl = dict(dk._blegend_opts())
        pl["show"] = shown("Plume scale bar")
        texts = [dict(tb) for tb in getattr(dk, "_tb", []) if tb.get("visible", True) and (tb.get("text") or "").strip()]
        return {"lith": lg, "plume": pl, "texts": texts}

    def export_page(self):
        dk = self.dock
        ctl = dk.cutter
        spec = incl = None
        try:
            path = self.path.filePath().strip()
            if not path:
                self.say("Choose where to save the file.", warn=True)
                return
            if not path.lower().endswith((".html", ".htm")):
                path += ".html"
            names = {self.lst.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.lst.count())
                     if self.lst.item(i).checkState() == Qt.CheckState.Checked}
            if not names:
                self.say("Tick at least one element.", warn=True)
                return
            els = [e for e in htmlexport.exportable_elements(dk._registry) if e[0] in names]
            info = dict(dk._last_info or {})
            if "origin" not in info:
                self.say("Draw the model in the 3D view first.", warn=True)
                return
            if ctl is not None and ctl.spec is not None:       # the page gets the whole model: cuts are made in the page
                spec, incl = ctl.spec, ctl.included
                ctl.set_planes([(False, 0.0, 1)] * 3, None)
            lc = dk.cb_collar.currentLayer()
            crs_text = lc.crs().authid() if lc is not None else QgsProject.instance().crs().authid()
            holes = {}
            if self.chk_labels.isChecked():
                drill = dk._read_drill()
                if drill:
                    holes = {h: (c[0], c[1], c[2]) for h, c in drill[0].items()}
            plume_range = None
            for n, a, v, f in els:
                if n in ("Plume samples", "Plume screen tubes"):
                    plume_range = a.GetMapper().GetScalarRange()
            order = dk.model["units"] if dk.model is not None else None
            labels = [ls for ls in htmlexport.label_sets(dk._registry)
                      if ("Groundwater contours" in names if ls["name"].startswith("Groundwater") else
                                  ("Plume samples" in names or "Plume screen tubes" in names))]
            data = htmlexport.build_data(els, info, self.title.text().strip() or "Subsurface model", crs_text, holes,
                                         self.theme.currentData(), plume_range, order, labels, dk.plume_title(), dk.cmb_shellcol.currentData(),
                                         overlay=self._overlay(dk))
            size = htmlexport.write_page(path, data)
            self.say("Saved %d element(s), %.1f MB:\n%s" % (len(data["elements"]), size / 1e6, path))
            if self.chk_open.isChecked():
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception:
            self.say("Could not export:\n" + traceback.format_exc(limit=3), warn=True)
        finally:
            if ctl is not None and spec is not None:
                ctl.set_planes(spec, incl)
                if dk.vtk_widget is not None:
                    dk.vtk_widget.GetRenderWindow().Render()
