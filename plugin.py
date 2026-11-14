import logging
import traceback
from qgis.PyQt.QtCore import Qt
try:
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication, Qgis


_log = logging.getLogger("av_geotools_3d")


class Subsurface3DPlugin:
    MENU = "&AV GeoTools 3D"

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.action_prep = None
        self.dock = None

    def initGui(self):
        import os
        from qgis.PyQt.QtGui import QIcon
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        if icon.isNull():
            icon = QgsApplication.getThemeIcon("/3d.svg")
        self.action = QAction(icon, "AV GeoTools 3D", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.pluginMenu().addAction(self.action)
        self.iface.addToolBarIcon(self.action)

    def _push_error(self, text):
        """Error in the message bar with a button that opens a ready-made e-mail to the author."""
        from qgis.PyQt.QtWidgets import QPushButton
        bar = self.iface.messageBar()
        item = bar.createMessage("AV GeoTools 3D", text)
        btn = QPushButton("Report by e-mail")

        def _send(*a):
            from . import report
            report.send_mail(text)
        btn.clicked.connect(_send)
        item.layout().addWidget(btn)
        bar.pushWidget(item, Qgis.MessageLevel.Critical, 0)

    def run(self, *args):
        try:
            if self.dock is None:
                from .viewer import SubsurfaceDock
                self.dock = SubsurfaceDock(self.iface)
                self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
                self.dock.setFloating(True)
                self.dock.resize(1150, 720)
            self.dock.show()
            self.dock.ensure_on_screen()
            self.dock.raise_()
        except ModuleNotFoundError as exc:
            self.dock = None
            name = (getattr(exc, "name", "") or "").split(".")[0]
            if name in ("vtkmodules", "vtk", "scipy", "numpy"):
                pkg = "vtk" if name in ("vtkmodules", "vtk") else name
                self.iface.messageBar().pushMessage(
                    "AV GeoTools 3D",
                    "The Python package '%s' is missing in this QGIS, and the plugin needs it. Close QGIS, open the "
                    "'OSGeo4W Shell' (Start menu > QGIS) and run:  python -m pip install %s   then start QGIS again. "
                    "See the plugin page for details." % (pkg, pkg),
                    level=Qgis.MessageLevel.Critical, duration=0)
            else:
                self._push_error("Could not open the viewer: " + traceback.format_exc())
        except Exception:
            self._push_error("Could not open the viewer: " + traceback.format_exc())

    def run_prep(self, *args):
        self.run()
        if self.dock is not None:
            self.dock.open_prep()

    def unload(self):
        if self.action is not None:
            self.iface.pluginMenu().removeAction(self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None
        if self.dock is not None:
            try:
                if getattr(self.dock, "prep", None) is not None:
                    self.dock.prep.close()
                    self.dock.prep.deleteLater()
                if getattr(self.dock, "export_dlg", None) is not None:
                    self.dock.export_dlg.close()
                    self.dock.export_dlg.deleteLater()
                self.dock.cleanup()
                self.iface.removeDockWidget(self.dock)
            except Exception:
                _log.debug('Could not remove the viewer dock while unloading', exc_info=True)
            self.dock.deleteLater()
            self.dock = None
