"""A window slot: one tab's location, history, model and views."""

import itertools
import os
import threading

from PySide6.QtCore import QFileSystemWatcher, QItemSelection, QItemSelectionModel, QTimer, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

from . import model, platform_win, theme
from .model import OTHER, RECENT, STARRED, TRASH, VIRTUAL, FileModel
from .pathbar import VIRTUAL_INFO
from .settings import settings
from .views import GridView, ListView
from .widgets import StatusPage

_generation = itertools.count(1)


def location_title(location):
    if location in VIRTUAL_INFO:
        return VIRTUAL_INFO[location][0]
    norm = os.path.normcase(os.path.normpath(location))
    if norm == os.path.normcase(os.path.normpath(platform_win.HOME)):
        return "Home"
    name = os.path.basename(location.rstrip("\\/"))
    if not name:  # drive root
        for d in platform_win.list_drives():
            if os.path.normcase(d.root) == os.path.normcase(location):
                return d.display_name
        return location
    return name


class Slot(QWidget):
    locationChanged = Signal(str)
    selectionChanged = Signal()
    loadingChanged = Signal(bool)
    searchChanged = Signal()

    def __init__(self, location, parent=None):
        super().__init__(parent)
        self.location = None
        self.back_list, self.forward_list = [], []
        self.loading = False
        self.error = None
        self.query = ""
        self.type_filter = ""
        self.search_global = False
        self._cancel = None
        self._gen = 0
        self._pending_select = None

        self.model = FileModel(self)
        self.grid = GridView()
        self.list = ListView()
        self.grid.setModel(self.model)
        self.list.setModel(self.model)
        self.list.setSelectionModel(self.grid.selectionModel())
        self.list.sortRequested = self._header_sort
        self.selection = self.grid.selectionModel()
        self.selection.selectionChanged.connect(lambda *_: self._selection_changed())
        # a model reset or removed rows drop the selection without selectionChanged
        self.model.modelReset.connect(self._selection_changed)
        self.model.rowsRemoved.connect(lambda *_: self._selection_changed())

        self.status = StatusPage()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.list)
        self.stack.addWidget(self.status)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)

        self.floating = QLabel(self)
        self.floating.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.floating.hide()

        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _p: self._watch_timer.start())
        self._watch_timer = QTimer(self, singleShot=True, interval=350, timeout=lambda: self.reload(soft=True))
        self._spinner_timer = QTimer(self, singleShot=True, interval=300, timeout=self._show_spinner)
        model.bus.loaded.connect(self._loaded)
        model.thumbnails.listeners.append(self._thumb_ready)

        self.list.setup_columns()
        self.apply_view_mode()
        self.apply_zoom()
        self.restyle()
        self.open_location(location, record=False)

    # ------------------------------------------------------------- views
    @property
    def view(self):
        return self.list if settings["view_mode"] == "list" else self.grid

    def views(self):
        return (self.grid, self.list)

    def apply_view_mode(self):
        if self.stack.currentWidget() is not self.status:
            self.stack.setCurrentWidget(self.view)
        self.view.scrollTo(self.view.currentIndex()) if self.view.currentIndex().isValid() else None

    def apply_zoom(self):
        from .views import icon_size
        self.grid.set_icon_size(icon_size("grid"))
        self.list.set_icon_size(icon_size("list"))

    def apply_click_policy(self):
        for v in self.views():
            v.single_click = settings["click_policy"] == "single"

    def restyle(self):
        t = theme.current()
        bg = t.view_bg
        mix = t.fg_alpha(0.10)
        a = mix.alphaF()
        blended = QColor(int(bg.red() * (1 - a) + mix.red() * a), int(bg.green() * (1 - a) + mix.green() * a),
                         int(bg.blue() * (1 - a) + mix.blue() * a))
        self.floating.setStyleSheet(f"background: {blended.name()}; border-radius: 12px; padding: 7px 12px;")
        self.status.refresh()
        self.apply_click_policy()
        for v in self.views():
            v.viewport().update()

    def focus_view(self):
        if self.stack.currentWidget() is not self.status:
            self.view.setFocus()
            if not self.view.currentIndex().isValid() and self.model.rowCount():
                self.view.selectionModel().setCurrentIndex(self.model.index(0, 0), QItemSelectionModel.NoUpdate)

    # ------------------------------------------------------------- navigation
    def open_location(self, location, record=True, select=None):
        location = self._normalize(location)
        if record and self.location and location != self.location:
            self.back_list.append(self.location)
            self.forward_list.clear()
        self._set_location(location, select)

    def _normalize(self, location):
        if location in VIRTUAL:
            return location
        location = os.path.normpath(location)
        if len(location) == 2 and location[1] == ":":
            location += os.sep
        return location

    def _set_location(self, location, select=None):
        self.cancel_search(clear=False)
        self.query = ""
        old = self.location
        self.location = location
        for v in self.views():
            v.location = location
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        if location not in VIRTUAL:
            self.watcher.addPath(location)
        self.model.cut_paths = self.model.cut_paths  # keep clipboard dimming
        self._configure_columns()
        self._pending_select = select
        self.model.set_items([])
        self.reload()
        self.locationChanged.emit(location)

    def go_back(self):
        if self.back_list:
            came_from = self.location
            self.forward_list.append(self.location)
            self._set_location(self.back_list.pop(), select=self._child_of(came_from))

    def go_forward(self):
        if self.forward_list:
            came_from = self.location
            self.back_list.append(self.location)
            self._set_location(self.forward_list.pop(), select=self._child_of(came_from))

    def go_up(self):
        if self.location in VIRTUAL:
            return
        parent = os.path.dirname(self.location.rstrip("\\/"))
        if parent and os.path.normcase(parent) != os.path.normcase(self.location):
            self.open_location(parent, select=self.location)

    def _child_of(self, path):
        return path if path and path not in VIRTUAL else None

    def title(self):
        if self.query:
            return f"Search for “{self.query}”"
        return location_title(self.location)

    # ------------------------------------------------------------- loading
    def reload(self, soft=False):
        gen = next(_generation)
        self._gen = gen
        self.loading = True
        self.error = None
        self.loadingChanged.emit(True)
        if not soft:
            self._spinner_timer.start()
        location = self.location

        def work():
            try:
                items = model.load_location(location)
                model.bus.loaded.emit(gen, items, True)
            except OSError as e:
                model.bus.loaded.emit(gen, e, True)

        model.run_async(work)

    def _loaded(self, gen, items, final):
        if gen != self._gen:
            return
        if isinstance(items, Exception):
            self.error = items
            self.model.set_items([])
        elif self.query and not final:
            self.model.add_items(items)
        elif self.query and final:
            if items:
                self.model.add_items(items)
            self.model.resort()
        elif self.model.rowCount() or self.model.all_items:
            self.model.update_items(items)
        else:
            self.model.set_items(items)
        if final:
            self.loading = False
            self._spinner_timer.stop()
            self.loadingChanged.emit(False)
            if self._pending_select:
                self.select_paths([self._pending_select], scroll=True)
                self._pending_select = None
        self._update_status()

    def _show_spinner(self):
        if self.loading and not self.model.rowCount():
            self.status.show_status(None, "Searching…" if self.query else "Loading…", spinner=True)
            self.stack.setCurrentWidget(self.status)

    def _update_status(self):
        if self.model.rowCount():
            if self.stack.currentWidget() is self.status:
                self.stack.setCurrentWidget(self.view)
            return
        if self.loading:
            return
        if self.error is not None:
            e = self.error
            msg = ("You do not have the permissions necessary to view the contents of this folder."
                   if isinstance(e, PermissionError) else
                   "The folder could not be found. Perhaps it was deleted or renamed." if isinstance(e, FileNotFoundError)
                   else str(e))
            self.status.show_status("dialog-warning", "Unable to Display Folder", msg)
        elif self.query:
            self.status.show_status("edit-find", "No Results Found", "Try a different search.")
        elif self.location == TRASH:
            self.status.show_status("user-trash", "Trash is Empty")
        elif self.location == STARRED:
            self.status.show_status("starred", "No Starred Files",
                                    "Use the “Star” action in the context menu to mark files as important.")
        elif self.location == RECENT:
            self.status.show_status("document-open-recent", "No Recent Files")
        elif self.model.all_items and not self.model.show_hidden:
            self.status.show_status("folder", "Folder is Empty", "This folder only contains hidden files.")
        else:
            self.status.show_status("folder", "Folder is Empty")
        self.stack.setCurrentWidget(self.status)

    def _thumb_ready(self, path):
        if self.location not in VIRTUAL and os.path.normcase(os.path.dirname(path)) != os.path.normcase(self.location):
            if not self.query:
                return
        self.model.refresh_row(path)

    # ------------------------------------------------------------- columns & sort
    def _configure_columns(self):
        if self.query:
            cols = ["name", "size", "location", "modified", "starred"]
            sort = ("rank", False)
        elif self.location == TRASH:
            cols = ["name", "original", "size", "trashed"]
            sort = ("trashed", True)
        elif self.location == RECENT:
            cols = ["name", "size", "location", "starred"]
            sort = ("rank", False)
        elif self.location == STARRED:
            cols = ["name", "location", "size", "modified", "starred"]
            sort = ("name", False)
        elif self.location == OTHER:
            cols = ["name", "size"]
            sort = ("name", False)
        else:
            cols = ["name"] + list(settings.data.get("list_columns", ["size", "modified", "starred"]))
            sort = self.folder_sort()
        if self.model.columns != cols:
            self.model.beginResetModel()
            self.model.columns = cols
            self.model.endResetModel()
            self.list.setup_columns()
        self.model.sort_key, self.model.sort_reverse = sort

    def folder_sort(self):
        return settings["sort"], settings["sort_reverse"]

    def set_sort(self, key, reverse):
        if self.location not in VIRTUAL and not self.query:
            settings["sort"], settings["sort_reverse"] = key, reverse
        self.model.set_sort(key, reverse)

    def _header_sort(self, column):
        key = {"name": "name", "size": "size", "modified": "modified", "type": "type",
               "trashed": "trashed", "location": "location", "original": "location"}.get(column)
        if not key:
            return
        if self.model.sort_key == key:
            reverse = not self.model.sort_reverse
        else:
            reverse = key in ("modified", "size", "trashed")
        self.set_sort(key, reverse)

    def refresh_columns(self):
        self._configure_columns()

    # ------------------------------------------------------------- search
    def start_search(self, query, type_filter="", global_=False):
        self.cancel_search(clear=False)
        self.query = query.strip()
        self.type_filter = type_filter
        self.search_global = global_
        self._configure_columns()
        self.searchChanged.emit()
        if not self.query:
            self.model.set_items([])
            self.reload()
            return
        gen = next(_generation)
        self._gen = gen
        self.model.set_items([])
        self.loading = True
        self.loadingChanged.emit(True)
        self._spinner_timer.start()
        if self.location in VIRTUAL and not global_:
            words = self.query.casefold().split()
            loc = self.location

            def filter_virtual():
                items = [i for i in model.load_location(loc) if all(w in i.name.casefold() for w in words)]
                model.bus.loaded.emit(gen, items, True)

            model.run_async(filter_virtual)
            return
        root = platform_win.HOME if global_ or self.location in VIRTUAL else self.location
        cancel = threading.Event()
        self._cancel = cancel
        show_hidden = settings["show_hidden"]
        q, tf = self.query, type_filter

        def work():
            model.search(root, q, tf, show_hidden, cancel, lambda batch: model.bus.loaded.emit(gen, batch, False))
            if not cancel.is_set():
                model.bus.loaded.emit(gen, [], True)

        model.run_async(work)

    def cancel_search(self, clear=True):
        if self._cancel is not None:
            self._cancel.set()
            self._cancel = None
        if clear and self.query:
            self.query = ""
            self._configure_columns()
            self.model.set_items([])
            self.reload()
            self.searchChanged.emit()

    # ------------------------------------------------------------- selection
    def selected_items(self):
        # any selected cell counts: the grid view selects items, the list view whole rows
        rows = sorted({i.row() for i in self.selection.selectedIndexes()})
        return [self.model.item(r) for r in rows if self.model.item(r)]

    def select_paths(self, paths, scroll=True):
        sel = QItemSelection()
        first = None
        for p in paths:
            r = self.model.row_for_path(p)
            if r >= 0:
                sel.select(self.model.index(r, 0), self.model.index(r, self.model.columnCount() - 1))
                if first is None:
                    first = self.model.index(r, 0)
        if first is not None:
            self.selection.select(sel, QItemSelectionModel.ClearAndSelect)
            self.selection.setCurrentIndex(first, QItemSelectionModel.NoUpdate)
            if scroll:
                self.view.scrollTo(first)

    def select_all(self):
        self.view.selectAll()

    def invert_selection(self):
        sel = QItemSelection()
        sel.select(self.model.index(0, 0), self.model.index(self.model.rowCount() - 1, self.model.columnCount() - 1))
        self.selection.select(sel, QItemSelectionModel.Toggle)

    def _selection_changed(self):
        self._update_floating()
        self.selectionChanged.emit()

    def _update_floating(self):
        items = self.selected_items()
        if not items:
            self.floating.hide()
            return
        if len(items) == 1:
            it = items[0]
            if it.opens_as_folder:
                extra = f" (containing {model.format_count(it.count)})" if it.count is not None and it.count >= 0 else ""
                text = f"“{it.name}” selected{extra}"
            else:
                text = f"“{it.name}” selected ({model.format_size(it.size)})"
        else:
            folders = [i for i in items if i.opens_as_folder]
            files = [i for i in items if not i.opens_as_folder]
            parts = []
            if folders:
                parts.append(f"{len(folders)} folder{'s' if len(folders) != 1 else ''} selected")
            if files:
                size = model.format_size(sum(i.size for i in files))
                word = "other item" if folders else "item"
                parts.append(f"{len(files)} {word}{'s' if len(files) != 1 else ''} selected ({size})")
            text = ", ".join(parts)
        fm = self.floating.fontMetrics()
        self.floating.setText(fm.elidedText(text, Qt.ElideMiddle, max(120, self.width() // 2)))
        self.floating.adjustSize()
        self._place_floating()
        self.floating.show()
        self.floating.raise_()

    def _place_floating(self):
        self.floating.move(self.width() - self.floating.width() - 8, self.height() - self.floating.height() - 8)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.floating.isVisible():
            self._update_floating()
