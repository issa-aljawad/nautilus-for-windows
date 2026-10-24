"""The places sidebar (Nautilus' NautilusSidebar) and the operations indicator."""

import os

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QListWidget, QListWidgetItem,
                               QProgressBar, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from . import icons, platform_win, theme
from .model import OTHER, RECENT, STARRED, TRASH
from .settings import bookmarks
from .widgets import header_button, label

KindRole = Qt.UserRole + 10
PathRole = Qt.UserRole + 11
IconRole = Qt.UserRole + 12
DriveRole = Qt.UserRole + 13


def places():
    kf = platform_win.known_folder
    out = [("Recent", RECENT, "document-open-recent"),
           ("Starred", STARRED, "starred"),
           ("Home", platform_win.HOME, "user-home")]
    for name, key, icon in (("Desktop", "desktop", "user-desktop"), ("Documents", "documents", "folder-documents"),
                            ("Downloads", "downloads", "folder-download"), ("Music", "music", "folder-music"),
                            ("Pictures", "pictures", "folder-pictures"), ("Videos", "videos", "folder-videos")):
        path = kf(key)
        if os.path.isdir(path):
            out.append((name, path, icon))
    out.append(("Trash", TRASH, "user-trash"))
    return out


def special_folder_map():
    kf = platform_win.known_folder
    m = {os.path.normcase(os.path.normpath(platform_win.HOME)): "user-home"}
    for key in ("documents", "downloads", "music", "pictures", "videos", "desktop"):
        m[os.path.normcase(os.path.normpath(kf(key)))] = key
    return m


class SidebarDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index):
        return QSize(100, 13 if index.data(KindRole) == "separator" else 38)

    def paint(self, p, option, index):
        t = theme.current()
        kind = index.data(KindRole)
        r = option.rect
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if kind == "separator":
            p.fillRect(QRect(r.x() + 12, r.center().y(), r.width() - 24, 1), t.fg_alpha(0.1))
            p.restore()
            return
        row = QRect(r.x() + 6, r.y() + 1, r.width() - 12, r.height() - 2)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        if self.view.drop_row == index.row():
            p.setBrush(t.accent_alpha(0.25))
            p.setPen(QPen(t.accent_bg, 1.5))
            p.drawRoundedRect(QRectF(row).adjusted(0.75, 0.75, -0.75, -0.75), 6, 6)
        elif selected or hovered:
            p.setPen(Qt.NoPen)
            p.setBrush(t.fg_alpha(0.10 if selected else 0.06))
            p.drawRoundedRect(QRectF(row), 6, 6)
        icon = index.data(IconRole)
        if index.data(PathRole) == TRASH and not self.view.trash_empty:
            icon = "user-trash-full"
        p.drawPixmap(row.x() + 10, row.center().y() - 8, icons.symbolic_pixmap(icon, t.icon_fg, 16))
        text_rect = QRect(row.x() + 38, row.y(), row.width() - 46, row.height())
        drive = index.data(DriveRole)
        if drive is not None and drive.kind in (platform_win.DRIVE_REMOVABLE, platform_win.DRIVE_CDROM):
            text_rect.setRight(row.right() - 34)
            p.drawPixmap(row.right() - 26, row.center().y() - 8, icons.symbolic_pixmap("media-eject", t.dim_fg, 16))
        p.setPen(t.fg)
        f = option.font
        p.setFont(f)
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                   option.fontMetrics.elidedText(index.data(Qt.DisplayRole), Qt.ElideRight, text_rect.width()))
        p.restore()


class PlacesList(QListWidget):
    activatedPath = Signal(str, int)
    menuRequested = Signal(str, str, QPoint)     # kind, path, global pos
    dropped = Signal(list, str, object)
    bookmarkDropped = Signal(list)
    ejectRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WA_Hover)
        self.setItemDelegate(SidebarDelegate(self))
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.setStyleSheet("QListWidget { background: transparent; padding-top: 4px; }")
        self.setFocusPolicy(Qt.NoFocus)
        self.drop_row = -1
        self.trash_empty = True
        self.location = None
        self.rebuild()

    def _add(self, text, kind, path=None, icon=None, drive=None):
        it = QListWidgetItem(text)
        it.setData(KindRole, kind)
        it.setData(PathRole, path)
        it.setData(IconRole, icon)
        it.setData(DriveRole, drive)
        if kind == "separator":
            it.setFlags(Qt.NoItemFlags)
        else:
            it.setToolTip(path if path and "://" not in path else text)
        self.addItem(it)

    def rebuild(self):
        self.clear()
        for name, path, icon in places():
            self._add(name, "place", path, icon)
        marks = [b for b in bookmarks if os.path.isdir(b["path"])]
        if marks:
            self._add("", "separator")
            for b in marks:
                self._add(b["label"] or os.path.basename(b["path"].rstrip("\\/")) or b["path"],
                          "bookmark", b["path"], "folder")
        self._add("", "separator")
        for d in platform_win.list_drives():
            icon = {platform_win.DRIVE_REMOVABLE: "drive-removable", platform_win.DRIVE_CDROM: "media-optical",
                    platform_win.DRIVE_REMOTE: "network"}.get(d.kind, "drive-harddisk")
            self._add(d.display_name, "drive", d.root, icon, d)
        self._add("Other Locations", "other", OTHER, "list-add")
        self.trash_empty = platform_win.trash_is_empty()
        self.set_location(self.location)

    def set_location(self, location):
        self.location = location
        self.clearSelection()
        if not location:
            return
        key = os.path.normcase(os.path.normpath(location)) if "://" not in location else location
        for i in range(self.count()):
            p = self.item(i).data(PathRole)
            if not p:
                continue
            pk = os.path.normcase(os.path.normpath(p)) if "://" not in p else p
            if pk == key:
                self.item(i).setSelected(True)
                return

    def mousePressEvent(self, e):
        it = self.itemAt(e.position().toPoint())
        if it is None or it.data(KindRole) == "separator":
            return
        if e.button() == Qt.LeftButton:
            drive = it.data(DriveRole)
            rect = self.visualItemRect(it)
            if (drive is not None and drive.kind in (platform_win.DRIVE_REMOVABLE, platform_win.DRIVE_CDROM)
                    and e.position().x() > rect.right() - 36):
                self.ejectRequested.emit(drive)
                return
            how = 1 if e.modifiers() & Qt.ControlModifier else 0
            self.activatedPath.emit(it.data(PathRole), how)
        elif e.button() == Qt.MiddleButton:
            self.activatedPath.emit(it.data(PathRole), 1)
        elif e.button() == Qt.RightButton:
            super().mousePressEvent(e)

    def _menu(self, pos):
        it = self.itemAt(pos)
        if it is not None and it.data(KindRole) != "separator":
            self.menuRequested.emit(it.data(KindRole), it.data(PathRole), self.viewport().mapToGlobal(pos))

    # --- drops
    def _target(self, pos):
        it = self.itemAt(pos)
        if it is None:
            return None, -1
        kind, path = it.data(KindRole), it.data(PathRole)
        if kind in ("place", "bookmark", "drive") and path not in (RECENT, STARRED):
            return path, self.row(it)
        return None, -1

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        target, row = self._target(e.position().toPoint())
        if row != self.drop_row:
            self.drop_row = row
            self.viewport().update()
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if target or (paths and all(os.path.isdir(p) for p in paths)):
            e.setDropAction(Qt.CopyAction if e.modifiers() & Qt.ControlModifier else Qt.MoveAction)
            e.accept()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self.drop_row = -1
        self.viewport().update()

    def dropEvent(self, e):
        target, _ = self._target(e.position().toPoint())
        self.drop_row = -1
        self.viewport().update()
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if not paths:
            return
        e.accept()
        if target:
            from .operations import same_volume
            if e.modifiers() & Qt.ControlModifier:
                action = Qt.CopyAction
            elif target == TRASH:
                action = Qt.MoveAction
            else:
                action = Qt.MoveAction if same_volume(paths[0], target) else Qt.CopyAction
            self.dropped.emit(paths, target, action)
        elif all(os.path.isdir(p) for p in paths):
            self.bookmarkDropped.emit(paths)


class ProgressPanel(QFrame):
    """Shows running file operations at the bottom of the sidebar."""

    def __init__(self, ops, parent=None):
        super().__init__(parent)
        self.ops = ops
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(12, 8, 12, 10)
        self.lay.setSpacing(10)
        ops.changed.connect(self.refresh)
        self.rows = {}
        self.hide()

    def refresh(self):
        running = list(self.ops.running)
        for op in list(self.rows):
            if op not in running:
                self.rows.pop(op).deleteLater()
        for op in running:
            row = self.rows.get(op)
            if row is None:
                row = self._make_row(op)
                self.rows[op] = row
                self.lay.addWidget(row)
            row.title.setText(row.title.fontMetrics().elidedText(op.title, Qt.ElideMiddle, max(60, self.width() - 60)))
            row.detail.setText(op.detail)
            if op.fraction < 0:
                row.bar.setRange(0, 0)
            else:
                row.bar.setRange(0, 1000)
                row.bar.setValue(int(op.fraction * 1000))
        self.setVisible(bool(running))

    def _make_row(self, op):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        top = QHBoxLayout()
        w.title = label("", heading=False)
        top.addWidget(w.title, 1)
        cancel = header_button("window-close", "Cancel")
        cancel.setProperty("circular", True)
        cancel.setFixedSize(24, 24)
        cancel.clicked.connect(op.cancel)
        top.addWidget(cancel)
        v.addLayout(top)
        w.bar = QProgressBar()
        w.bar.setTextVisible(False)
        w.bar.setFixedHeight(6)
        v.addWidget(w.bar)
        w.detail = label("", caption=True)
        v.addWidget(w.detail)
        return w


class Sidebar(QWidget):
    def __init__(self, ops, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WA_StyledBackground)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.header = QWidget()
        self.header.setObjectName("SidebarHeader")
        self.header.setAttribute(Qt.WA_StyledBackground)
        self.header.setProperty("dragArea", True)
        self.header.setFixedHeight(47)
        h = QHBoxLayout(self.header)
        h.setContentsMargins(7, 6, 7, 6)
        h.setSpacing(6)
        self.search_button = header_button("edit-find", "Search Everywhere", checkable=True)
        h.addWidget(self.search_button)
        self.title = label("Files", heading=True, center=True)
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents)
        h.addWidget(self.title, 1)
        self.menu_button = header_button("open-menu", "Main Menu")
        h.addWidget(self.menu_button)
        v.addWidget(self.header)
        self.places = PlacesList()
        v.addWidget(self.places, 1)
        self.progress = ProgressPanel(ops)
        v.addWidget(self.progress)
