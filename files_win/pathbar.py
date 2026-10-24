"""The header's center: path bar, location entry and search entry."""

import os

from PySide6.QtCore import QDir, QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCompleter, QFileSystemModel, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QScrollArea, QSizePolicy, QStackedWidget, QToolButton, QWidget)

from . import icons, platform_win, theme
from .model import OTHER, RECENT, STARRED, TRASH
from .widgets import header_button, make_menu, set_symbolic

VIRTUAL_INFO = {
    RECENT: ("Recent", "document-open-recent"),
    STARRED: ("Starred", "starred"),
    TRASH: ("Trash", "user-trash"),
    OTHER: ("Other Locations", "computer"),
}


def segments(location):
    """[(label, path, icon_name)] for the breadcrumbs of a location."""
    if location in VIRTUAL_INFO:
        name, icon = VIRTUAL_INFO[location]
        return [(name, location, icon)]
    path = os.path.normpath(location)
    home = os.path.normpath(platform_win.HOME)
    if os.path.normcase(path + os.sep).startswith(os.path.normcase(home + os.sep)):
        out = [("Home", home, "user-home")]
        rest = path[len(home):].strip("\\/")
        cur = home
    else:
        drive, rest = os.path.splitdrive(path)
        root = drive + os.sep if drive else os.sep
        icon = "network" if drive.startswith("\\\\") else "drive-harddisk"
        out = [(drive or os.sep, root, icon)]
        rest = rest.strip("\\/")
        cur = root
    for part in [p for p in rest.replace("/", "\\").split("\\") if p]:
        cur = os.path.join(cur, part)
        out.append((part, cur, None))
    return out


class PathButton(QToolButton):
    dropped = Signal(list, str, object)

    def __init__(self, text, path, icon_name, current):
        super().__init__()
        self.path = path
        self.setText(text)
        self.setCursor(Qt.ArrowCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(28)
        self.setAcceptDrops(path not in VIRTUAL_INFO or path == TRASH)
        if icon_name:
            set_symbolic(self, icon_name)
            self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if text else Qt.ToolButtonIconOnly)
        else:
            self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        f = self.font()
        f.setBold(current)
        self.setFont(f)
        # Nautilus: the current folder's button never shows a hover background
        self.setStyleSheet("QToolButton { padding: 2px 8px; border-radius: 7px; }"
                           + ("QToolButton:hover, QToolButton:pressed { background: transparent; }" if current else ""))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            action = Qt.CopyAction if e.modifiers() & Qt.ControlModifier else Qt.MoveAction
            e.acceptProposedAction()
            self.dropped.emit(paths, self.path, action)


class PathBar(QFrame):
    navigate = Signal(str, int)            # path, how
    menuRequested = Signal(QPoint)         # current location menu
    crumbMenuRequested = Signal(str, QPoint)
    dropped = Signal(list, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PathBar")
        self.setFixedHeight(34)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 0, 0, 0)
        lay.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("background: transparent;")
        self.inner = QWidget()
        self.row = QHBoxLayout(self.inner)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(0)
        self.row.addStretch(1)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        self.more = header_button("view-more", "Current Folder Menu")
        self.more.setFixedSize(34, 34)
        self.more.setStyleSheet("QToolButton { border-radius: 9px; }")
        self.more.clicked.connect(lambda: self.menuRequested.emit(self.more.mapToGlobal(QPoint(17, 34))))
        lay.addWidget(self.more)
        self.location = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_location(self, location):
        self.location = location
        while self.row.count() > 1:
            w = self.row.takeAt(0).widget()
            if w:
                w.deleteLater()
        segs = segments(location)
        t = theme.current()
        for i, (text, path, icon) in enumerate(segs):
            current = i == len(segs) - 1
            if i:
                sep = QLabel("/")
                sep.setStyleSheet(f"color: {theme.rgba(t.fg_alpha(0.35))}; padding: 0 1px;")
                self.row.insertWidget(self.row.count() - 1, sep)
            b = PathButton(text, path, icon, current)
            if current:
                b.clicked.connect(lambda _=False, b=b: self.menuRequested.emit(b.mapToGlobal(QPoint(b.width() // 2, 30))))
            else:
                b.clicked.connect(lambda _=False, p=path: self.navigate.emit(p, 0))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, b=b: self.crumbMenuRequested.emit(b.path, b.mapToGlobal(pos)))
            b.dropped.connect(self.dropped)
            b.mousePressEvent = self._middle(b, b.mousePressEvent)
            self.row.insertWidget(self.row.count() - 1, b)
        QTimer.singleShot(0, self._scroll_end)

    def _middle(self, b, orig):
        def handler(e):
            if e.button() == Qt.MiddleButton:
                self.navigate.emit(b.path, 1)
                return
            orig(e)
        return handler

    def _scroll_end(self):
        bar = self.scroll.horizontalScrollBar()
        bar.setValue(bar.maximum())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._scroll_end)


class LocationEntry(QLineEdit):
    entered = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Enter a location")
        fs = QFileSystemModel(self)
        fs.setRootPath("")
        fs.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Drives)
        comp = QCompleter(fs, self)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        self.setCompleter(comp)
        comp.popup().setStyleSheet("QListView { padding: 4px; border-radius: 8px; }")
        self.returnPressed.connect(lambda: self.entered.emit(self.text()))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and not (self.completer() and self.completer().popup().isVisible()):
            self.cancelled.emit()
            return
        super().keyPressEvent(e)


class SearchEntry(QWidget):
    changed = Signal(str, str)       # query, type filter
    closed = Signal()
    focusView = Signal()

    TYPES = [("", "Any"), ("documents", "Documents"), ("folders", "Folders"), ("images", "Pictures"),
             ("music", "Music"), ("videos", "Videos"), ("archives", "Archives"), ("text", "Text & Code")]

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.frame = QFrame()
        self.frame.setObjectName("PathBar")
        self.frame.setFixedHeight(34)
        fl = QHBoxLayout(self.frame)
        fl.setContentsMargins(10, 0, 3, 0)
        fl.setSpacing(6)
        self.icon = QLabel()
        fl.addWidget(self.icon)
        self.edit = QLineEdit()
        self.edit.setProperty("flatEntry", True)
        self.edit.setClearButtonEnabled(False)
        fl.addWidget(self.edit, 1)
        self.clear = header_button("edit-clear", "Clear")
        self.clear.setFixedSize(28, 28)
        self.clear.clicked.connect(lambda: self.edit.clear())
        fl.addWidget(self.clear)
        self.filter = header_button("pan-down", "Filter Results")
        self.filter.setFixedSize(28, 28)
        self.filter.clicked.connect(self._filter_menu)
        fl.addWidget(self.filter)
        lay.addWidget(self.frame)
        self.type_filter = ""
        self._timer = QTimer(self, singleShot=True, interval=250, timeout=self._emit)
        self.edit.textChanged.connect(lambda _t: (self._timer.start(), self._sync_clear()))
        self.edit.installEventFilter(self)
        self._sync_clear()
        self.refresh()

    def refresh(self):
        self.icon.setPixmap(icons.symbolic_pixmap("edit-find", theme.current().dim_fg, 16))

    def set_scope(self, text):
        self.edit.setPlaceholderText(text)

    def _sync_clear(self):
        self.clear.setVisible(bool(self.edit.text()))

    def _emit(self):
        self.changed.emit(self.edit.text(), self.type_filter)

    def _filter_menu(self):
        m = make_menu(self)
        m.addSection("What")
        for key, text in self.TYPES:
            a = m.addAction(text)
            a.setCheckable(True)
            a.setChecked(key == self.type_filter)
            a.triggered.connect(lambda _=False, k=key: self._set_type(k))
        m.exec(self.filter.mapToGlobal(QPoint(0, self.filter.height() + 4)))

    def _set_type(self, key):
        self.type_filter = key
        set_symbolic(self.filter, "pan-down")
        self.filter.setChecked(bool(key))
        self._emit()

    def eventFilter(self, obj, e):
        if obj is self.edit and e.type() == e.Type.KeyPress:
            if e.key() == Qt.Key_Escape:
                self.closed.emit()
                return True
            if e.key() in (Qt.Key_Down, Qt.Key_Return, Qt.Key_Enter):
                self._timer.stop()
                self._emit()
                self.focusView.emit()
                return True
        return False


class LocationBar(QStackedWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pathbar = PathBar()
        self.entry_box = QWidget()
        eb = QHBoxLayout(self.entry_box)
        eb.setContentsMargins(0, 0, 0, 0)
        eb.setSpacing(4)
        self.entry = LocationEntry()
        self.entry.setFixedHeight(34)
        self.entry_cancel = header_button("window-close", "Cancel")
        eb.addWidget(self.entry, 1)
        eb.addWidget(self.entry_cancel)
        self.search = SearchEntry()
        self.addWidget(self.pathbar)
        self.addWidget(self.entry_box)
        self.addWidget(self.search)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(34)
        self.setMinimumWidth(120)

    def show_pathbar(self):
        self.setCurrentWidget(self.pathbar)

    def show_entry(self, text):
        self.setCurrentWidget(self.entry_box)
        self.entry.setText(text)
        self.entry.setFocus()
        self.entry.selectAll()

    def show_search(self, text=None):
        self.setCurrentWidget(self.search)
        if text is not None:
            self.search.edit.setText(text)
        self.search.edit.setFocus()
        if text:
            self.search.edit.end(False)

    def mode(self):
        w = self.currentWidget()
        return "path" if w is self.pathbar else "entry" if w is self.entry_box else "search"
