import traceback
from qgis.PyQt.QtCore import Qt
try:
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication, Qgis


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
        except Exception:
            self.iface.messageBar().pushMessage(
                "AV GeoTools 3D", "Could not open the viewer: " + traceback.format_exc(),
                level=Qgis.MessageLevel.Critical, duration=0)

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
                pass
            self.dock.deleteLater()
            self.dock = None
