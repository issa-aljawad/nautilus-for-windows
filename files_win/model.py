"""File items, background loaders and the Qt item model shared by both views."""

import os
import re
import struct
import time
from collections import OrderedDict
from datetime import datetime

from PySide6.QtCore import (QAbstractTableModel, QMimeData, QModelIndex, QObject, QRunnable,
                            QSize, Qt, QThreadPool, QTimer, QUrl, Signal, QFileInfo)
from PySide6.QtGui import QImage, QImageReader

from . import icons, platform_win
from .settings import recent, settings, starred

RECENT = "recent:///"
STARRED = "starred:///"
TRASH = "trash:///"
OTHER = "other-locations:///"
VIRTUAL = (RECENT, STARRED, TRASH, OTHER)

ItemRole = Qt.UserRole + 1


class FileItem:
    __slots__ = ("path", "name", "is_dir", "size", "mtime", "ctime", "atime", "hidden",
                 "is_link", "target", "category", "trash", "drive", "count", "rank")

    def __init__(self, path, name=None):
        self.path = path
        self.name = name or os.path.basename(path.rstrip("\\/")) or path
        self.is_dir = False
        self.size = 0
        self.mtime = 0.0
        self.ctime = 0.0
        self.atime = 0.0
        self.hidden = False
        self.is_link = False
        self.target = None      # resolved target of a .lnk / symlink
        self.category = "generic"
        self.trash = None       # platform_win.TrashEntry for trash items
        self.drive = None       # platform_win.Drive for "Other Locations"
        self.count = None       # number of children (folders, computed lazily)
        self.rank = 0           # search relevance / recency order

    @property
    def open_path(self):
        """Path to navigate to / launch (follows shortcuts)."""
        return self.target or self.path

    @property
    def opens_as_folder(self):
        return self.is_dir or (self.target is not None and os.path.isdir(self.target))

    @property
    def parent(self):
        if self.trash:
            return os.path.dirname(self.trash.original)
        return os.path.dirname(self.path)

    @property
    def starred(self):
        return self.path in starred


def make_item(path, entry=None):
    item = FileItem(path, entry.name if entry is not None else None)
    try:
        if entry is not None:
            st = entry.stat(follow_symlinks=True)
            lst = entry.stat(follow_symlinks=False)
            item.is_dir = entry.is_dir()
            item.is_link = entry.is_symlink()
        else:
            st = os.stat(path)
            lst = os.lstat(path)
            item.is_dir = os.path.isdir(path)
            item.is_link = os.path.islink(path)
        item.size = st.st_size
        item.mtime = st.st_mtime
        item.ctime = getattr(st, "st_birthtime", st.st_ctime)
        item.atime = st.st_atime
        item.hidden = platform_win.is_hidden_attrs(item.name, getattr(lst, "st_file_attributes", 0))
    except OSError:
        item.hidden = item.name.startswith(".")
    if not item.is_dir:
        if item.name.lower().endswith(".lnk"):
            target = QFileInfo(path).symLinkTarget()
            if target:
                item.is_link = True
                item.target = os.path.normpath(target)
        item.category = icons.categorize(item.name)
    elif item.is_link:
        item.target = os.path.realpath(path)
    return item


# ------------------------------------------------------------------ sorting

_num = re.compile(r"(\d+)")


def natural_key(text):
    return [(0, int(t)) if t.isdigit() else (1, t) for t in _num.split(text.casefold()) if t]


SORTS = [  # (id, label, key, reverse)
    ("az", "_A-Z", "name", False),
    ("za", "_Z-A", "name", True),
    ("last-modified", "Last _Modified", "modified", True),
    ("first-modified", "_First Modified", "modified", False),
    ("size", "_Size", "size", True),
    ("type", "_Type", "type", False),
    ("trashed", "Last _Trashed", "trashed", True),
    ("recency", "Recency", "rank", False),
    ("relevance", "Relevance", "rank", False),
]


def sort_key_func(key):
    if key == "name":
        return lambda it: natural_key(it.name)
    if key == "modified":
        return lambda it: it.mtime
    if key == "size":
        return lambda it: it.count if it.is_dir and it.count is not None else it.size
    if key == "type":
        return lambda it: (icons.type_description(it.name, it.is_dir).casefold(), natural_key(it.name))
    if key == "trashed":
        return lambda it: it.trash.deleted.timestamp() if it.trash and it.trash.deleted else 0
    if key == "rank":
        return lambda it: it.rank
    if key == "location":
        return lambda it: natural_key(it.parent)
    return lambda it: natural_key(it.name)


# ------------------------------------------------------------------ formatting

def format_size(n):
    if n < 1000:
        return "1 byte" if n == 1 else f"{n} bytes"
    for unit in ("kB", "MB", "GB", "TB", "PB"):
        n /= 1000.0
        if n < 1000 or unit == "PB":
            return f"{n:.1f} {unit}"


def format_count(n):
    return "1 item" if n == 1 else f"{n} items"


def format_date(ts, full=False):
    if not ts:
        return "—"
    dt = datetime.fromtimestamp(ts) if not isinstance(ts, datetime) else ts
    now = datetime.now()
    clock = dt.strftime("%H:%M")
    if full:
        return dt.strftime("%A, %d %B %Y, %H:%M")
    days = (now.date() - dt.date()).days
    if days == 0:
        return clock
    if days == 1:
        return f"Yesterday {clock}"
    if 0 < days < 7:
        return dt.strftime("%A")
    if dt.year == now.year:
        return dt.strftime("%d %b").lstrip("0")
    return dt.strftime("%d %b %Y").lstrip("0")


def display_path(path):
    home = platform_win.HOME
    if os.path.normcase(path).startswith(os.path.normcase(home)):
        rest = path[len(home):].lstrip("\\/")
        return "Home" + (" / " + rest.replace("\\", " / ") if rest else "")
    return path.replace("\\", " / ").replace(":", ":", 1)


# ------------------------------------------------------------------ background loading

class _Bus(QObject):
    loaded = Signal(int, object, bool)    # generation, items, final
    thumb = Signal(str, object)           # path, QImage
    counted = Signal(str, int)            # folder path, child count


bus = None
pool = None


def init_workers():
    global bus, pool
    bus = _Bus()
    pool = QThreadPool()
    pool.setMaxThreadCount(max(4, QThreadPool.globalInstance().maxThreadCount()))


class _Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.setAutoDelete(True)

    def run(self):
        try:
            self.fn()
        except Exception:  # never let a worker kill the pool
            import traceback
            traceback.print_exc()


def run_async(fn):
    pool.start(_Task(fn))


def load_location(location):
    """Blocking; returns list[FileItem] for a folder path or virtual location."""
    if location == RECENT:
        return _load_recent()
    if location == STARRED:
        items = [make_item(p) for p in starred if os.path.lexists(p)]
        return items
    if location == TRASH:
        return _load_trash()
    if location == OTHER:
        out = []
        for d in platform_win.list_drives():
            it = FileItem(d.root, d.display_name)
            it.is_dir = True
            it.drive = d
            out.append(it)
        return out
    items = []
    with os.scandir(location) as it:
        for entry in it:
            items.append(make_item(entry.path, entry))
    return items


def _load_recent():
    seen = set()
    out = []
    candidates = list(recent) + platform_win.recent_shortcuts()
    for p in candidates:
        if p.lower().endswith(".lnk") and os.path.dirname(p) == platform_win.known_folder("recent"):
            target = QFileInfo(p).symLinkTarget()
            if not target:
                continue
            p = os.path.normpath(target)
        key = os.path.normcase(p)
        if key in seen or not os.path.exists(p) or os.path.isdir(p):
            continue
        seen.add(key)
        item = make_item(p)
        item.rank = len(out)
        out.append(item)
    return out


def _load_trash():
    out = []
    for e in platform_win.list_trash():
        it = FileItem(e.trash_path, e.name)
        it.trash = e
        try:
            it.is_dir = os.path.isdir(e.trash_path)
            st = os.stat(e.trash_path)
            it.mtime = st.st_mtime
        except OSError:
            pass
        it.size = e.size
        if not it.is_dir:
            it.category = icons.categorize(it.name)
        out.append(it)
    return out


def search(root, query, type_filter, show_hidden, cancel, emit):
    """Breadth-first name search. Calls emit(items) in batches."""
    words = query.casefold().split()
    if not words:
        return
    batch, last = [], time.monotonic()
    queue = [root]
    found = 0
    while queue and not cancel.is_set():
        folder = queue.pop(0)
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if cancel.is_set():
                return
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                attrs = entry.stat(follow_symlinks=False).st_file_attributes if hasattr(os.stat_result, "st_file_attributes") else 0
            except OSError:
                continue
            hidden = platform_win.is_hidden_attrs(entry.name, attrs)
            if hidden and not show_hidden:
                continue
            if is_dir and not (attrs & 0x400):  # skip reparse points (junction loops)
                queue.append(entry.path)
            name = entry.name.casefold()
            if all(w in name for w in words):
                if type_filter and icons.type_group(entry.name, is_dir) != type_filter:
                    continue
                item = make_item(entry.path, entry)
                item.rank = (0 if name.startswith(words[0]) else 1, len(name), found)
                found += 1
                batch.append(item)
                if found >= 5000:
                    emit(batch)
                    return
        if batch and time.monotonic() - last > 0.15:
            emit(batch)
            batch, last = [], time.monotonic()
    if batch:
        emit(batch)


# ------------------------------------------------------------------ thumbnails

class Thumbnails:
    MAX = 256

    def __init__(self):
        self.cache = OrderedDict()
        self.pending = set()
        bus.thumb.connect(self._done)
        self.listeners = []
        # QtPdf deadlocks when two Python threads drive it at once (its lock vs. the GIL), so
        # Illustrator thumbnails render on the GUI thread, one per event-loop turn.
        self._gui_jobs = []
        self._gui_timer = QTimer(interval=0, timeout=self._gui_step)

    def _gui_step(self):
        if not self._gui_jobs:
            self._gui_timer.stop()
            return
        path, mtime = self._gui_jobs.pop(0)
        from . import adobe
        try:
            img = adobe.ai_thumbnail(path, self.MAX)
        except (OSError, ValueError):
            img = None
        self._done(f"{mtime}|{path}", img if img is not None else QImage())

    def get(self, item):
        if not settings["thumbnails"] or item.is_dir or item.trash:
            return None
        ext = icons.extension(item.name)
        if ext not in icons.PREVIEW_EXTS:
            return None
        if item.size > 80_000_000 and ext not in ("psd", "psb", "ai"):   # those are read sparsely
            return None
        key = (item.path, item.mtime)
        img = self.cache.get(key)
        if img is not None:
            self.cache.move_to_end(key)
            return img if not img.isNull() else None
        if key not in self.pending:
            self.pending.add(key)
            path, mtime = item.path, item.mtime
            if ext == "ai":
                self._gui_jobs.append((path, mtime))
                if not self._gui_timer.isActive():
                    self._gui_timer.start()
                return None

            def work():
                img = None
                try:
                    if ext in ("psd", "psb"):
                        from . import adobe
                        img, _info = adobe.read_psd(path, self.MAX)
                    elif ext in icons.SHELL_PREVIEW_EXTS:
                        img = platform_win.shell_thumbnail(path, self.MAX)
                    else:
                        reader = QImageReader(path)
                        reader.setAutoTransform(True)
                        size = reader.size()
                        if size.isValid() and (size.width() > self.MAX or size.height() > self.MAX):
                            reader.setScaledSize(size.scaled(QSize(self.MAX, self.MAX), Qt.KeepAspectRatio))
                        img = reader.read()
                except (OSError, ValueError, struct.error):
                    img = None    # unreadable file: show its icon
                bus.thumb.emit(f"{mtime}|{path}", img if img is not None else QImage())

            run_async(work)
        return None

    def _done(self, key, img):
        mtime, path = key.split("|", 1)
        k = (path, float(mtime))
        self.pending.discard(k)
        self.cache[k] = img if img is not None else QImage()
        while len(self.cache) > 800:
            self.cache.popitem(last=False)
        for cb in self.listeners:
            cb(path)


class FolderCounter:
    def __init__(self):
        self.pending = set()
        self.listeners = []
        bus.counted.connect(self._done)

    def request(self, path):
        if path in self.pending:
            return
        self.pending.add(path)

        def work():
            try:
                n = sum(1 for name in os.listdir(path) if not name.startswith("."))
            except OSError:
                n = -1
            bus.counted.emit(path, n)

        run_async(work)

    def _done(self, path, n):
        self.pending.discard(path)
        for cb in self.listeners:
            cb(path, n)


thumbnails = None
counter = None


def init_services():
    global thumbnails, counter
    init_workers()
    thumbnails = Thumbnails()
    counter = FolderCounter()


# ------------------------------------------------------------------ the model

COLUMN_TITLES = {"name": "Name", "size": "Size", "type": "Type", "modified": "Modified",
                 "location": "Location", "original": "Original Location", "trashed": "Trashed On",
                 "starred": ""}


class FileModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.all_items = []       # unfiltered (hidden files included)
        self.columns = ["name", "size", "modified", "starred"]
        self.sort_key = "name"
        self.sort_reverse = False
        self.show_hidden = settings["show_hidden"]
        self.cut_paths = set()
        self._row_of = {}
        counter.listeners.append(self._on_count)

    # --- structure
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and 0 <= section < len(self.columns):
            col = self.columns[section]
            if role == Qt.DisplayRole:
                return COLUMN_TITLES.get(col, col)
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignRight | Qt.AlignVCenter) if col == "size" else int(Qt.AlignLeft | Qt.AlignVCenter)
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.items):
            return None
        item = self.items[index.row()]
        if role == ItemRole:
            return item
        col = self.columns[index.column()]
        if role == Qt.DisplayRole:
            return self.text(item, col)
        if role == Qt.ToolTipRole and col == "name":
            return item.name
        return None

    def text(self, item, col):
        if col == "name":
            return item.name
        if col == "size":
            if item.drive:
                u = item.drive.usage()
                return f"{format_size(u.free)} free" if u else ""
            if item.is_dir:
                if item.count is None and settings["count_folder_items"] and not item.trash:
                    counter.request(item.path)
                    return ""
                return format_count(item.count) if item.count is not None and item.count >= 0 else ""
            return format_size(item.size)
        if col == "modified":
            return format_date(item.mtime)
        if col == "type":
            return icons.type_description(item.name, item.is_dir)
        if col == "location":
            return display_path(item.parent)
        if col == "original":
            return display_path(item.parent)
        if col == "trashed":
            return format_date(item.trash.deleted) if item.trash and item.trash.deleted else ""
        return ""

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsDropEnabled
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
        if self.items[index.row()].opens_as_folder:
            f |= Qt.ItemIsDropEnabled
        return f

    # --- drag and drop
    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, indexes):
        rows = sorted({i.row() for i in indexes})
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self.items[r].path) for r in rows])
        return mime

    def supportedDragActions(self):
        return Qt.CopyAction | Qt.MoveAction | Qt.LinkAction

    def supportedDropActions(self):
        return Qt.CopyAction | Qt.MoveAction | Qt.LinkAction

    # --- content
    def set_items(self, items):
        self.beginResetModel()
        self.all_items = list(items)
        self.items = self._visible(self.all_items)
        self._sort_list(self.items)
        self._reindex()
        self.endResetModel()

    def add_items(self, items):
        """Append (search results arrive in batches)."""
        self.all_items.extend(items)
        new = self._visible(items)
        if not new:
            return
        start = len(self.items)
        self.beginInsertRows(QModelIndex(), start, start + len(new) - 1)
        self.items.extend(new)
        self._reindex()
        self.endInsertRows()

    def update_items(self, items):
        """Replace contents, keeping rows (and therefore selection) that still exist."""
        new_by_path = {os.path.normcase(i.path): i for i in items}
        self.all_items = list(items)
        visible = {os.path.normcase(i.path): i for i in self._visible(items)}
        for r in range(len(self.items) - 1, -1, -1):
            key = os.path.normcase(self.items[r].path)
            if key not in visible:
                self.beginRemoveRows(QModelIndex(), r, r)
                del self.items[r]
                self.endRemoveRows()
        present = set()
        for r, it in enumerate(self.items):
            key = os.path.normcase(it.path)
            present.add(key)
            fresh = new_by_path[key]
            if fresh.mtime != it.mtime or fresh.size != it.size or fresh.is_dir != it.is_dir:
                fresh.count = None if fresh.is_dir and fresh.mtime != it.mtime else it.count
                self.items[r] = fresh
                self.dataChanged.emit(self.index(r, 0), self.index(r, len(self.columns) - 1))
            else:
                # keep the object, but refresh fields that the views read
                it.hidden = fresh.hidden
        added = [i for k, i in visible.items() if k not in present]
        if added:
            start = len(self.items)
            self.beginInsertRows(QModelIndex(), start, start + len(added) - 1)
            self.items.extend(added)
            self.endInsertRows()
        self.resort()

    def _visible(self, items):
        if self.show_hidden:
            return list(items)
        return [i for i in items if not i.hidden]

    def set_show_hidden(self, show):
        self.show_hidden = show
        self.update_items(self.all_items)

    def _sort_list(self, lst):
        lst.sort(key=sort_key_func(self.sort_key), reverse=self.sort_reverse)
        if settings["folders_first"] and self.sort_key not in ("rank",):
            lst.sort(key=lambda it: not it.opens_as_folder)

    def set_sort(self, key, reverse):
        self.sort_key, self.sort_reverse = key, reverse
        self.resort()

    def resort(self):
        self.layoutAboutToBeChanged.emit()
        old = self.persistentIndexList()
        old_items = [self.items[i.row()] if i.isValid() and i.row() < len(self.items) else None for i in old]
        self._sort_list(self.items)
        self._reindex()
        new = []
        for idx, it in zip(old, old_items):
            r = self._row_of.get(id(it)) if it is not None else None
            new.append(self.index(r, idx.column()) if r is not None else QModelIndex())
        self.changePersistentIndexList(old, new)
        self.layoutChanged.emit()

    def _reindex(self):
        self._row_of = {id(it): r for r, it in enumerate(self.items)}

    def row_for_path(self, path):
        key = os.path.normcase(os.path.normpath(path))
        for r, it in enumerate(self.items):
            if os.path.normcase(os.path.normpath(it.path)) == key:
                return r
        return -1

    def item(self, row):
        return self.items[row] if 0 <= row < len(self.items) else None

    def refresh_row(self, path):
        r = self.row_for_path(path)
        if r >= 0:
            self.dataChanged.emit(self.index(r, 0), self.index(r, len(self.columns) - 1))

    def refresh_all(self):
        if self.items:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.items) - 1, len(self.columns) - 1))

    def _on_count(self, path, n):
        key = os.path.normcase(path)
        for r, it in enumerate(self.items):
            if it.is_dir and os.path.normcase(it.path) == key:
                it.count = n
                self.dataChanged.emit(self.index(r, 0), self.index(r, len(self.columns) - 1))
                break
