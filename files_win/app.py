"""Application-wide state: windows, clipboard, theme."""

import os
import struct

from PySide6.QtCore import QMimeData, QObject, QPoint, QUrl, Signal
from PySide6.QtGui import QColor, QPalette

from . import icons, theme

VERSION = "1.0"
DROPEFFECT = 'application/x-qt-windows-mime;value="Preferred DropEffect"'


class AppState(QObject):
    themeChanged = Signal()
    cutChanged = Signal()

    def __init__(self, qapp):
        super().__init__()
        self.qapp = qapp
        self.windows = []
        self.cut_paths = set()
        qapp.clipboard().dataChanged.connect(self._clipboard_changed)
        hints = qapp.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda *_: self.apply_theme())

    # ------------------------------------------------------------- theme
    def apply_theme(self):
        t = theme.reload_theme()
        icons.clear_cache()
        pal = QPalette()
        pal.setColor(QPalette.Window, t.window_bg)
        pal.setColor(QPalette.Base, t.view_bg)
        pal.setColor(QPalette.AlternateBase, t.view_bg)
        pal.setColor(QPalette.Text, t.fg)
        pal.setColor(QPalette.WindowText, t.fg)
        pal.setColor(QPalette.ButtonText, t.fg)
        pal.setColor(QPalette.Button, t.window_bg)
        pal.setColor(QPalette.Highlight, t.accent_alpha(0.35))
        pal.setColor(QPalette.HighlightedText, t.fg)
        pal.setColor(QPalette.PlaceholderText, t.dim_fg)
        pal.setColor(QPalette.ToolTipBase, t.toast_bg)
        pal.setColor(QPalette.ToolTipText, QColor("white"))
        pal.setColor(QPalette.Link, t.accent)
        self.qapp.setPalette(pal)
        self.qapp.setStyleSheet(theme.stylesheet(t))
        self.themeChanged.emit()

    # ------------------------------------------------------------- clipboard
    def set_clipboard(self, paths, cut):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        mime.setText("\n".join(paths))
        mime.setData(DROPEFFECT, struct.pack("<I", 2 if cut else 5))
        self.qapp.clipboard().setMimeData(mime)
        self.cut_paths = {os.path.normcase(p) for p in paths} if cut else set()
        self.cutChanged.emit()

    def read_clipboard(self):
        mime = self.qapp.clipboard().mimeData()
        if mime is None or not mime.hasUrls():
            return [], False
        paths = [os.path.normpath(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]
        cut = False
        if mime.hasFormat(DROPEFFECT):
            data = bytes(mime.data(DROPEFFECT))
            if len(data) >= 4:
                effect = struct.unpack("<I", data[:4])[0]
                cut = bool(effect & 2) and not effect & 1
        return paths, cut

    def clipboard_has_files(self):
        mime = self.qapp.clipboard().mimeData()
        return bool(mime and mime.hasUrls() and any(u.isLocalFile() for u in mime.urls()))

    def _clipboard_changed(self):
        paths, cut = self.read_clipboard()
        new = {os.path.normcase(p) for p in paths} if cut else set()
        if new != self.cut_paths:
            self.cut_paths = new
            self.cutChanged.emit()

    # ------------------------------------------------------------- windows
    def new_window(self, location=None, geometry_from=None):
        from .window import FilesWindow
        w = FilesWindow(self, location)
        self.windows.append(w)
        if geometry_from is not None:
            g = geometry_from.geometry()
            w.resize(g.size())
            w.move(g.topLeft() + QPoint(32, 32))
        w.show()
        w.raise_()
        w.activateWindow()
        return w

    def window_closed(self, w):
        if w in self.windows:
            self.windows.remove(w)

    def all_slots(self):
        for w in self.windows:
            yield from w.slots()


state = None
