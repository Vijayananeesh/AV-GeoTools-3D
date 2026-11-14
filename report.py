import importlib
import logging
_log = logging.getLogger(__name__)
"""Problem reports: builds a short, privacy-friendly report from an error text and opens the user's mail program."""
import os
import platform
import re
import sys
from urllib.parse import quote

ADDRESS = "krishnanvijayananeesh@gmail.com"
MAX_BODY = 1400          # characters of the error text put into the e-mail (a mailto link cannot carry much)


def _plugin_version():
    try:
        with open(os.path.join(os.path.dirname(__file__), "metadata.txt"), encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("version="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        _log.debug("could not read metadata.txt", exc_info=True)
    return "?"


def clean(text):
    """Removes the Windows / macOS / Linux user name from file paths."""
    text = re.sub(r"([A-Za-z]:[\\/]+Users[\\/]+)[^\\/\r\n]+", r"\1<user>", text)
    text = re.sub(r"(/(?:Users|home)/)[^/\r\n]+", r"\1<user>", text)
    return text


def versions():
    out = ["AV GeoTools 3D: %s" % _plugin_version()]
    try:
        from qgis.core import Qgis
        out.append("QGIS: %s" % Qgis.version())
    except Exception:
        _log.debug("QGIS version unavailable", exc_info=True)
    out.append("Python: %s" % sys.version.split()[0])
    for mod, label in (("vtkmodules.vtkCommonCore", "VTK"), ("numpy", "numpy"), ("scipy", "scipy")):
        try:
            if label == "VTK":
                from vtkmodules.vtkCommonCore import vtkVersion
                out.append("VTK: %s" % vtkVersion.GetVTKVersion())
            else:
                out.append("%s: %s" % (label, importlib.import_module(mod).__version__))
        except Exception:
            out.append("%s: not available" % label)
    out.append("System: %s" % platform.platform())
    return out


def full_report(error_text):
    return "\n".join(versions()) + "\n\nWhat I was doing: (please describe)\n\nError:\n" + clean(error_text or "").strip() + "\n"


def copy_details(error_text):
    from qgis.PyQt.QtWidgets import QApplication
    QApplication.clipboard().setText(full_report(error_text))


def send_mail(error_text):
    """Copies the full report to the clipboard and opens a ready-made e-mail (the end of long errors is kept)."""
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtGui import QDesktopServices
    copy_details(error_text)
    err = clean(error_text or "").strip()
    if len(err) > MAX_BODY:
        err = "...\n" + err[-MAX_BODY:]
    body = "\n".join(versions()) + "\n\nWhat I was doing: (please describe)\n\nError:\n" + err + "\n"
    url = "mailto:%s?subject=%s&body=%s" % (ADDRESS, quote("AV GeoTools 3D problem report (%s)" % _plugin_version()),
                                           quote(body))
    return QDesktopServices.openUrl(QUrl(url))
