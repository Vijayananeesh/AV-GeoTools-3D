"""Modern look for the left panel of AV GeoTools 3D: soft cards, rounded boxes, blue accent (light and dark).
Only the plugin's own panel is styled; nothing else in QGIS changes."""
import os

_UI = os.path.join(os.path.dirname(__file__), "ui").replace("\\", "/")

LIGHT = {"bg": "#ffffff", "card": "#f1f4f9", "card2": "#e7ecf5", "line": "#dbe1ec", "text": "#1f2a3d", "muted": "#6b7790",
         "input": "#ffffff", "accent": "#2f6fed", "hover": "#e9eef7", "groove": "#d9e0ee", "chev": "chevron-down.svg"}
DARK = {"bg": "#1b2030", "card": "#242b3d", "card2": "#2d3550", "line": "#38425c", "text": "#e6eaf3", "muted": "#9aa6c0",
        "input": "#1a1f2e", "accent": "#5b8cff", "hover": "#2a3248", "groove": "#3a4560", "chev": "chevron-down-dark.svg"}


def panel_qss(dark=False):
    c = DARK if dark else LIGHT
    chev = "%s/%s" % (_UI, c["chev"])
    tick = "%s/check.svg" % _UI
    return """
QScrollArea#avLeft { background: %(bg)s; border: none; }
QWidget#avHost { background: %(bg)s; }
QWidget#avHost QWidget { color: %(text)s; }
QPushButton#avSection { background: %(card)s; border: 1px solid %(line)s; border-radius: 10px; padding: 9px 12px;
                        text-align: left; font-weight: 600; color: %(text)s; }
QPushButton#avSection:hover { background: %(hover)s; }
QPushButton#avSection:checked { background: %(card2)s; border-color: %(accent)s; }
QWidget#avHost QGroupBox { background: %(card)s; border: 1px solid %(line)s; border-radius: 10px; margin-top: 12px; padding-top: 8px; font-weight: 600; }
QWidget#avHost QGroupBox[avCollapsed="true"] { background: transparent; border: none; margin-top: 0px; padding-top: 0px; }
QWidget#avHost QLineEdit, QWidget#avHost QAbstractSpinBox, QWidget#avHost QComboBox, QWidget#avHost QPlainTextEdit {
    background: %(input)s; border: 1px solid %(line)s; border-radius: 8px; padding: 3px 8px; min-height: 20px; color: %(text)s;
    selection-background-color: %(accent)s; }
QWidget#avHost QLineEdit:focus, QWidget#avHost QAbstractSpinBox:focus, QWidget#avHost QComboBox:focus { border: 1px solid %(accent)s; }
QWidget#avHost QComboBox::drop-down { border: none; width: 22px; }
QWidget#avHost QComboBox::down-arrow { image: url(%(chev)s); width: 12px; height: 12px; }
QWidget#avHost QComboBox QAbstractItemView { background: %(input)s; color: %(text)s; border: 1px solid %(line)s; selection-background-color: %(accent)s;
    selection-color: #ffffff; }
QWidget#avHost QPushButton { background: %(input)s; border: 1px solid %(line)s; border-radius: 8px; padding: 6px 12px; color: %(text)s; }
QWidget#avHost QPushButton:hover { background: %(hover)s; }
QWidget#avHost QPushButton:pressed { background: %(card2)s; }
QWidget#avHost QCheckBox, QWidget#avHost QRadioButton { spacing: 8px; background: transparent; }
QWidget#avHost QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid %(muted)s; border-radius: 4px; background: %(input)s; }
QWidget#avHost QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; image: url(%(tick)s); }
QWidget#avHost QSlider::groove:horizontal { height: 6px; background: %(groove)s; border-radius: 3px; }
QWidget#avHost QSlider::sub-page:horizontal { background: %(accent)s; border-radius: 3px; }
QWidget#avHost QSlider::handle:horizontal { background: %(accent)s; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; }
QWidget#avHost QLabel { background: transparent; }
QWidget#avHost QListWidget, QWidget#avHost QListView, QWidget#avHost QTreeView, QWidget#avHost QTextEdit {
    background: %(input)s; color: %(text)s; border: 1px solid %(line)s; border-radius: 8px;
    selection-background-color: %(accent)s; selection-color: #ffffff; }
QWidget#avHost QListWidget::item { padding: 3px 6px; color: %(text)s; }
QWidget#avHost QListWidget::item:selected { background: %(accent)s; color: #ffffff; }
QWidget#avHost QTableWidget { background: %(input)s; border: 1px solid %(line)s; border-radius: 8px; gridline-color: %(line)s; }
QWidget#avHost QHeaderView::section { background: %(card2)s; border: none; padding: 4px 6px; font-weight: 600; }
QScrollBar:vertical { width: 8px; background: transparent; margin: 0; }
QScrollBar::handle:vertical { background: %(groove)s; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
""" % dict(c, chev=chev, tick=tick)


def toolbar_qss(dark=False):
    """Rounded buttons, a blue main button (Update model) and matching boxes for the toolbar at the top."""
    c = DARK if dark else LIGHT
    chev = "%s/%s" % (_UI, c["chev"])
    return """
QWidget#avBar { background: %(bg)s; border-radius: 12px; }
QWidget#avBar QLabel { background: transparent; color: %(text)s; }
QWidget#avBar QPushButton { background: %(card)s; border: 1px solid %(line)s; border-radius: 9px; padding: 5px 10px; color: %(text)s; }
QWidget#avBar QPushButton:hover { background: %(hover)s; border-color: %(accent)s; }
QWidget#avBar QPushButton:pressed { background: %(card2)s; }
QWidget#avBar QPushButton#avPrimary { background: %(accent)s; border-color: %(accent)s; color: #ffffff; font-weight: 600; }
QWidget#avBar QPushButton#avPrimary:hover { background: %(accent)s; border-color: %(text)s; }
QWidget#avBar QAbstractSpinBox, QWidget#avBar QComboBox { background: %(input)s; border: 1px solid %(line)s; border-radius: 8px;
    padding: 3px 8px; min-height: 22px; color: %(text)s; }
QWidget#avBar QAbstractSpinBox:focus, QWidget#avBar QComboBox:focus { border: 1px solid %(accent)s; }
QWidget#avBar QComboBox::drop-down { border: none; width: 22px; }
QWidget#avBar QComboBox::down-arrow { image: url(%(chev)s); width: 12px; height: 12px; }
QWidget#avBar QComboBox QAbstractItemView { background: %(input)s; color: %(text)s; border: 1px solid %(line)s;
    selection-background-color: %(accent)s; selection-color: #ffffff; }
""" % dict(c, chev=chev)


def tabs_qss(dark=False):
    """Tabs (3D View, 2D View, Tables): flat rounded tabs with a blue underline on the open one."""
    c = DARK if dark else LIGHT
    return """
QTabWidget#avTabs::pane { border: 1px solid %(line)s; border-radius: 10px; top: -1px; }
QTabWidget#avTabs QTabBar::tab { background: %(card)s; color: %(muted)s; border: 1px solid %(line)s; border-bottom: none;
    border-top-left-radius: 9px; border-top-right-radius: 9px; padding: 7px 16px; margin-right: 3px; font-weight: 600; }
QTabWidget#avTabs QTabBar::tab:selected { background: %(bg)s; color: %(text)s; border-bottom: 3px solid %(accent)s; }
QTabWidget#avTabs QTabBar::tab:hover:!selected { background: %(hover)s; color: %(text)s; }
""" % c
