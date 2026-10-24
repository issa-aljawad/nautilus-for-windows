"""The extra Properties tabs: Sharing, Security, Location, Previous Versions and
Digital Signatures. Each page loads its data on a worker thread."""

import os
import shutil
import tempfile

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QScrollArea, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import icons, model, operations, platform_win, theme, winprops
from .widgets import AlertDialog, BoxedList, Sheet, Spinner, Switch, action_row, alert, button, label, property_row


class _Loader(QObject):
    done = Signal(object, object)   # result, error


def load_async(owner, fn, on_done):
    """Runs fn() on a worker thread and hands (result, error) to on_done on the GUI thread."""
    loader = _Loader(owner)
    loader.done.connect(on_done)

    def work():
        try:
            result, error = fn(), None
        except Exception as e:  # shown on the page, never raised into the pool
            result, error = None, e
        try:
            loader.done.emit(result, error)
        except RuntimeError:    # the dialog closed meanwhile
            pass

    model.run_async(work)


def _when(dt):
    return model.format_date(dt.timestamp(), True) if dt else "Not available"


def _table(headers, stretch=0):
    """A QTreeWidget styled like the app's boxed lists."""
    t = theme.current()
    view = QTreeWidget()
    view.setRootIsDecorated(False)
    view.setHeaderLabels(headers)
    view.setUniformRowHeights(True)
    view.setStyleSheet(
        f"QTreeWidget {{ background: {theme.rgba(t.card_bg)}; border: 1px solid {theme.rgba(t.fg_alpha(0.08))};"
        f" border-radius: 10px; padding: 2px 4px; }}"
        f"QTreeWidget::item {{ padding: 5px 2px; border: none; }}"
        f"QTreeWidget::item:selected {{ background: {theme.rgba(t.accent_alpha(0.25))}; color: {theme.rgba(t.fg)}; }}"
        f"QHeaderView, QHeaderView::section {{ background: transparent; }}")
    h = view.header()
    for i in range(len(headers)):
        h.setSectionResizeMode(i, QHeaderView.Stretch if i == stretch else QHeaderView.ResizeToContents)
    return view


def _scrolled(inner):
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setWidget(inner)
    return s


def _status_row(ok, text):
    w = QWidget()
    w.setObjectName("RowCard")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(14, 10, 12, 10)
    t = theme.current()
    icon = QLabel()
    icon.setPixmap(icons.symbolic_pixmap("security-high" if ok else "dialog-warning",
                                         t.success if ok else t.destructive, 20))
    lay.addWidget(icon, 0, Qt.AlignTop)
    lay.addWidget(label(text, wrap=True), 1)
    return w


class Page(QWidget):
    """A tab page: a spinner until load() reports, then build(result)."""

    def __init__(self, item, window):
        super().__init__()
        self.item, self.window_ = item, window
        self.path = item.open_path if item.opens_as_folder else item.path
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 4, 0, 0)
        self.lay.setSpacing(10)
        self._loaded = False

    def activate(self):
        if not self._loaded:
            self._loaded = True
            self.reload()

    def reload(self):
        self._clear()
        spin = QHBoxLayout()
        spin.addStretch(1)
        spin.addWidget(Spinner(24))
        spin.addStretch(1)
        self.lay.addStretch(1)
        self.lay.addLayout(spin)
        self.lay.addStretch(1)
        load_async(self, self.load, self._loaded_cb)

    def _clear(self):
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                _drop_layout(it.layout())

    def _loaded_cb(self, result, error):
        self._clear()
        if error is not None:
            self.lay.addWidget(label(f"This information isn't available.\n{error}", dim=True, center=True, wrap=True))
            self.lay.addStretch(1)
        else:
            self.build(result)

    def load(self):
        return None

    def build(self, result):
        pass


def _drop_layout(layout):
    while layout.count():
        it = layout.takeAt(0)
        if it.widget():
            it.widget().deleteLater()
        elif it.layout():
            _drop_layout(it.layout())


# ------------------------------------------------------------------ sharing

class ShareDialog(Sheet):
    PERMISSIONS = [("READ", "Read"), ("CHANGE", "Read & write"), ("FULL", "Full control")]

    def __init__(self, parent, folder_name):
        super().__init__(parent, "Share Folder", 420)
        self.body.addWidget(label("Share Name", dim=True))
        self.name = QLineEdit(folder_name[:80])
        self.name.setMinimumHeight(36)
        self.body.addWidget(self.name)
        self.body.addWidget(label("Everyone on the network can", dim=True))
        self.perm = QComboBox()
        self.perm.addItems([t for _k, t in self.PERMISSIONS])
        self.body.addWidget(self.perm)
        self.body.addWidget(label("Windows will ask for administrator permission. People still need an account "
                                  "on this PC unless password-protected sharing is turned off.", caption=True, wrap=True))
        ok = button("_Share", suggested=True)
        ok.setMinimumHeight(36)
        ok.clicked.connect(self.accept)
        self.name.returnPressed.connect(self.accept)
        self.body.addWidget(ok)

    def value(self):
        if not self.exec():
            return None
        name = "".join(c for c in self.name.text().strip() if c not in '"/\\[]:|<>+=;,?*')
        return (name, self.PERMISSIONS[self.perm.currentIndex()][0]) if name else None


class SharingPage(Page):
    def load(self):
        return winprops.shares_for(self.path)

    def build(self, shares):
        box = BoxedList()
        box.add_row(property_row("Network File and Folder Sharing", "Shared" if shares else "Not Shared"))
        for name, remark in shares:
            row = property_row("Network Path", f"\\\\{winprops.COMPUTER}\\{name}" + (f"  — {remark}" if remark else ""))
            box.add_row(row)
        self.lay.addWidget(box)
        buttons = QHBoxLayout()
        share = button("Share…" if not shares else "Share Again…")
        share.clicked.connect(self._share)
        buttons.addWidget(share)
        if shares:
            copy = button("Copy Network Path")
            copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(f"\\\\{winprops.COMPUTER}\\{shares[0][0]}"))
            buttons.addWidget(copy)
            stop = button("Stop Sharing", destructive=True)
            stop.clicked.connect(lambda: self._run(lambda: [winprops.unshare_folder(n) for n, _r in shares]))
            buttons.addWidget(stop)
        buttons.addStretch(1)
        self.lay.addLayout(buttons)
        self.lay.addWidget(label("Shared folders can be opened by other computers on your network. Changing "
                                 "sharing needs administrator permission.", caption=True, wrap=True))
        self.lay.addStretch(1)

    def _share(self):
        result = ShareDialog(self.window(), os.path.basename(self.path.rstrip("\\")) or "Share").value()
        if result:
            name, perm = result
            self._run(lambda: winprops.share_folder(self.path, name, perm))

    def _run(self, fn):
        def done(codes, error):
            codes = codes if isinstance(codes, list) else [codes]
            if error is not None:
                if not isinstance(error, PermissionError):
                    alert(self.window(), "Sharing Couldn't Be Changed", str(error))
            elif any(c for c in codes):
                alert(self.window(), "Sharing Couldn't Be Changed",
                      "Windows reported an error. The share name may already be in use.")
            self.reload()
        load_async(self, fn, done)


# ------------------------------------------------------------------ security

class SecurityPage(Page):
    def load(self):
        return winprops.security(self.path)

    def build(self, sec):
        is_dir = self.item.opens_as_folder
        self.rows = winprops.summarize(sec["entries"], is_dir)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 6, 0)
        v.setSpacing(10)
        top = BoxedList()
        top.add_row(property_row("Object Name", self.path))
        o_name, o_detail, _g = sec["owner"]
        top.add_row(property_row("Owner", f"{o_name} ({o_detail})" if o_detail else o_name))
        v.addWidget(top)
        if sec["no_dacl"]:
            v.addWidget(label("This item has no permissions set, so everyone has full access.", wrap=True))
        v.addWidget(label("Group or User Names", heading=True))
        self.people = QListWidget()
        self.people.setFixedHeight(min(150, 34 * max(1, len(self.rows)) + 8))
        self.people.setStyleSheet(f"QListWidget {{ background: {theme.rgba(theme.current().card_bg)}; border-radius: 10px;"
                                  f" border: 1px solid {theme.rgba(theme.current().fg_alpha(0.08))}; padding: 4px; }}"
                                  "QListWidget::item { padding: 6px; border-radius: 6px; }"
                                  f"QListWidget::item:selected {{ background: {theme.rgba(theme.current().accent_alpha(0.25))};"
                                  f" color: {theme.rgba(theme.current().fg)}; }}")
        for name, detail, group, *_ in self.rows:
            it = QListWidgetItem(icons.symbolic("system-users" if group else "avatar-default",
                                                theme.current().icon_fg), f"{name} ({detail})" if detail else name)
            self.people.addItem(it)
        self.people.currentRowChanged.connect(self._show_person)
        v.addWidget(self.people)
        self.perm_title = label("", heading=True)
        v.addWidget(self.perm_title)
        self.grid_holder = QFrame()
        self.grid_holder.setObjectName("Card")
        self.grid = QGridLayout(self.grid_holder)
        self.grid.setContentsMargins(14, 8, 14, 8)
        self.grid.setVerticalSpacing(8)
        v.addWidget(self.grid_holder)

        adv = Switch(False)
        v.addWidget(action_row("Show All Entries", "Every permission entry, with where it comes from", adv))
        self.table = _table(["Type", "Principal", "Access", "Inherited", "Applies To"], stretch=1)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        for e in sec["entries"]:
            QTreeWidgetItem(self.table, [e["kind"], e["name"], winprops.access_name(e["mask"], is_dir),
                                         "Parent object" if e["flags"] & winprops.INHERITED else "None",
                                         winprops.applies_to(e["flags"], is_dir)])
        self.table.setMinimumHeight(180)
        self.table.hide()
        adv.toggled.connect(self.table.setVisible)
        v.addWidget(self.table)
        edit_row = QHBoxLayout()
        edit = button("Edit Permissions…")
        edit.clicked.connect(lambda: winprops.open_windows_page(self.path, "Security"))
        edit_row.addWidget(edit)
        edit_row.addWidget(label("Changes are made in Windows' permission editor, which can ask for administrator "
                                 "permission.", caption=True, wrap=True), 1)
        v.addLayout(edit_row)
        v.addStretch(1)
        self.lay.addWidget(_scrolled(inner))
        if self.rows:
            self.people.setCurrentRow(0)

    def _show_person(self, index):
        _drop_layout(self.grid)
        if index < 0:
            return
        name, _detail, _group, allowed, denied, special_allow, special_deny = self.rows[index]
        self.perm_title.setText(f"Permissions for {name}")
        t = theme.current()
        for col, text in ((1, "Allow"), (2, "Deny")):
            self.grid.addWidget(label(text, caption=True), 0, col, Qt.AlignCenter)
        titles = [title for title, _r in winprops.basic_permissions(self.item.opens_as_folder)] + ["Special permissions"]
        for r, title in enumerate(titles, start=1):
            self.grid.addWidget(label(title), r, 0)
            for col, on in ((1, title in allowed if title != "Special permissions" else special_allow),
                            (2, title in denied if title != "Special permissions" else special_deny)):
                mark = QLabel()
                if on:
                    mark.setPixmap(icons.symbolic_pixmap("object-select", t.accent if col == 1 else t.destructive, 16))
                self.grid.addWidget(mark, r, col, Qt.AlignCenter)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnMinimumWidth(1, 70)
        self.grid.setColumnMinimumWidth(2, 70)


# ------------------------------------------------------------------ location

class LocationPage(Page):
    def __init__(self, item, window, key):
        super().__init__(item, window)
        self.key = key

    def load(self):
        return platform_win.known_folder(self.key)

    def build(self, current):
        title = winprops.KNOWN_FOLDER_NAMES[self.key]
        self.lay.addWidget(label(f"Files in the {title} folder are stored in the location below. You can store "
                                 "them somewhere else on this PC, another drive or another computer on your network.",
                                 wrap=True))
        box = BoxedList()
        box.add_row(property_row("Location", current))
        default = winprops.default_location(self.key)
        if os.path.normcase(default) != os.path.normcase(current):
            box.add_row(property_row("Default Location", default))
        self.lay.addWidget(box)
        row = QHBoxLayout()
        find = button("Find Target")
        find.clicked.connect(lambda: self.window_.open_location(current, 1))
        row.addWidget(find)
        move = button("Move…")
        move.clicked.connect(lambda: self._move(current, None))
        row.addWidget(move)
        restore = button("Restore Default")
        restore.setEnabled(os.path.normcase(default) != os.path.normcase(current))
        restore.clicked.connect(lambda: self._move(current, default))
        row.addWidget(restore)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.lay.addStretch(1)

    def _move(self, old, new):
        title = winprops.KNOWN_FOLDER_NAMES[self.key]
        if new is None:
            new = QFileDialog.getExistingDirectory(self.window(), f"Choose a New Location for {title}",
                                                   os.path.dirname(old))
            if not new:
                return
            new = os.path.normpath(new)
        n_old, n_new = os.path.normcase(old), os.path.normcase(new)
        if n_new == n_old:
            return
        if n_new.startswith(n_old + os.sep):
            return alert(self.window(), "Can't Move Here", f"The new location is inside the {title} folder itself.")
        if not os.path.splitdrive(new)[1].strip("\\/"):
            return alert(self.window(), "Choose a Folder", "Pick a folder rather than a whole drive.")
        choice = AlertDialog(self.window(), f"Move {title} to “{new}”?",
                             "Do you want to move all of the files from the old location to the new one?",
                             (("cancel", "_Cancel", None), ("keep", "_Don't Move Files", None),
                              ("move", "_Move Files", "suggested")), default="move", width=440).run()
        if choice not in ("keep", "move"):
            return
        try:
            os.makedirs(new, exist_ok=True)
            winprops.set_known_folder(self.key, new)
        except OSError as e:
            return alert(self.window(), f"Couldn't Move {title}", str(e))
        if choice == "move":
            try:
                pairs = [(os.path.join(old, n), os.path.join(new, n)) for n in os.listdir(old)]
            except OSError:
                pairs = []
            if pairs:
                self.window_.ops.run(operations.move_job(pairs))
        from .sidebar import special_folder_map
        from .views import set_special_folders
        set_special_folders(special_folder_map())
        self.window_.refresh_sidebars()
        self.reload()


# ------------------------------------------------------------------ previous versions

class PreviousVersionsPage(Page):
    def load(self):
        return winprops.previous_versions(self.path)

    def build(self, result):
        versions, needs_admin = result
        self.versions = versions
        self.lay.addWidget(label("Previous versions come from File History or from restore points.", wrap=True))
        self.list = _table(["Name", "Date Modified", "Location"])
        for v in versions:
            QTreeWidgetItem(self.list, [self.item.name, _when(v["when"]), v["source"]])
        self.list.setVisible(bool(versions))
        self.list.itemSelectionChanged.connect(self._selection)
        self.list.itemDoubleClicked.connect(lambda *_: self._open())
        self.lay.addWidget(self.list, 1)
        if not versions:
            self.lay.addStretch(1)
            self.lay.addWidget(label("There are no previous versions available.", dim=True, center=True))
            self.lay.addStretch(1)
        if needs_admin:
            row = QHBoxLayout()
            search = button("Search Restore Points…")
            search.clicked.connect(self._elevate)
            row.addWidget(search)
            row.addWidget(label("Listing restore points needs administrator permission once per session.",
                                caption=True, wrap=True), 1)
            self.lay.addLayout(row)
        buttons = QHBoxLayout()
        self.open_btn, self.copy_btn = button("Open"), button("Copy To…")
        self.restore_btn = button("Restore…", suggested=True)
        for b, fn in ((self.open_btn, self._open), (self.copy_btn, self._copy), (self.restore_btn, self._restore)):
            b.setEnabled(False)
            b.clicked.connect(fn)
            buttons.addWidget(b)
        buttons.addStretch(1)
        self.lay.addLayout(buttons)

    def _selection(self):
        on = bool(self.list.selectedItems())
        for b in (self.open_btn, self.copy_btn, self.restore_btn):
            b.setEnabled(on)

    def _current(self):
        items = self.list.selectedItems()
        return self.versions[self.list.indexOfTopLevelItem(items[0])] if items else None

    def _elevate(self):
        def done(_r, error):
            if error is not None and not isinstance(error, PermissionError):
                alert(self.window(), "Couldn't List Restore Points", str(error))
            self.reload()
        load_async(self, winprops.list_shadows_elevated, done)

    def _open(self):
        v = self._current()
        if v is None:
            return
        if os.path.isdir(v["path"]):
            self.window_.open_location(v["path"], 1)
            return
        stamp = v["when"].astimezone().strftime("%Y-%m-%d %H-%M")
        base, ext = os.path.splitext(self.item.name)
        tmp = os.path.join(tempfile.gettempdir(), "Files previous versions", f"{base} ({stamp}){ext}")
        try:
            os.makedirs(os.path.dirname(tmp), exist_ok=True)
            shutil.copy2(v["path"], tmp)
        except OSError as e:
            return alert(self.window(), "Couldn't Open This Version", str(e))
        platform_win.open_default(tmp)

    def _copy(self):
        v = self._current()
        dest = v and QFileDialog.getExistingDirectory(self.window(), "Copy Previous Version To")
        if dest:
            self.window_.ops.run(operations.copy_job([(v["path"], os.path.join(dest, self.item.name))]))

    def _restore(self):
        v = self._current()
        if v is None:
            return
        r = AlertDialog(self.window(), f"Restore “{self.item.name}”?",
                        f"The version from {_when(v['when'])} will replace the current one. You can undo this.",
                        (("cancel", "_Cancel", None), ("restore", "_Restore", "suggested")), default="restore").run()
        if r == "restore":
            self.window_.ops.run(operations.copy_job([(v["path"], self.path)]))


# ------------------------------------------------------------------ digital signatures

class SignaturesPage(Page):
    def load(self):
        return winprops.signatures(self.path)

    def build(self, sigs):
        self.sigs = sigs
        self.lay.addWidget(label("Signature List", heading=True))
        self.list = _table(["Name of Signer", "Digest Algorithm", "Timestamp"])
        for s in sigs:
            QTreeWidgetItem(self.list, [s["signer"], s["digest"], _when(s["timestamp"])])
        self.list.setFixedHeight(min(150, 32 * len(sigs) + 40))
        self.list.currentItemChanged.connect(lambda *_: self._details())
        self.lay.addWidget(self.list)
        self.detail = QWidget()
        self.detail_lay = QVBoxLayout(self.detail)
        self.detail_lay.setContentsMargins(0, 0, 6, 0)
        self.detail_lay.setSpacing(10)
        self.lay.addWidget(_scrolled(self.detail), 1)
        if sigs:
            self.list.setCurrentItem(self.list.topLevelItem(0))

    def _details(self):
        _drop_layout(self.detail_lay)
        item = self.list.currentItem()
        if item is None:
            return
        s = self.sigs[self.list.indexOfTopLevelItem(item)]
        status = BoxedList()
        status.add_row(_status_row(s["ok"], s["status"]))
        self.detail_lay.addWidget(status)
        self.detail_lay.addWidget(label("Signer", heading=True))
        signer = BoxedList()
        signer.add_row(property_row("Name", s["signer"]))
        signer.add_row(property_row("E-mail", s["email"] or "Not available"))
        signer.add_row(property_row("Signing Time", _when(s["timestamp"])))
        self.detail_lay.addWidget(signer)
        if s["countersigners"]:
            self.detail_lay.addWidget(label("Countersignatures", heading=True))
            counters = BoxedList()
            for c in s["countersigners"]:
                counters.add_row(property_row(c["name"], f"{_when(c['timestamp'])}  ·  {c['digest']}"))
            self.detail_lay.addWidget(counters)
        if s["cert"]:
            c = s["cert"]
            self.detail_lay.addWidget(label("Certificate", heading=True))
            cert = BoxedList()
            cert.add_row(property_row("Issued To", c["subject"]))
            cert.add_row(property_row("Issued By", c["issuer"]))
            cert.add_row(property_row("Valid From", _when(c["valid_from"])))
            cert.add_row(property_row("Valid To", _when(c["valid_to"])))
            cert.add_row(property_row("Serial Number", c["serial"].hex(" ").upper()))
            cert.add_row(property_row("Thumbprint", c["thumbprint"]))
            self.detail_lay.addWidget(cert)
        self.detail_lay.addStretch(1)


# ------------------------------------------------------------------ appearance

class AppearancePage(QWidget):
    """Folder color, applied as soon as it's picked."""

    def __init__(self, items, window):
        super().__init__()
        self.items, self.window_ = items, window
        self.paths = [i.open_path for i in items]
        self._built = False
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)

    def activate(self):
        if self._built:
            return
        self._built = True
        from .dialogs import FolderColorPicker
        from .settings import folder_colors
        shared = {folder_colors.get(p) for p in self.paths}
        picker = FolderColorPicker(self.items, shared.pop() if len(shared) == 1 else None,
                                   lambda color: self.window_.apply_folder_color(self.paths, color))
        self.lay.addWidget(_scrolled(picker))


def _colorable(item):
    """A real folder that isn't a drive root, the trash or a virtual location."""
    path = item.open_path
    return (item.opens_as_folder and not item.drive and not item.trash and path not in model.VIRTUAL
            and bool(os.path.splitdrive(path)[1].strip("\\/")) and os.path.isdir(path))


def tabs_for_items(items, window):
    """Tabs for a selection: Windows' tabs for one item, Appearance alone for several folders."""
    if len(items) == 1:
        return tabs_for(items[0], window)
    if items and all(_colorable(i) for i in items):
        return [("Appearance", AppearancePage(items, window))]
    return []


def tabs_for(item, window):
    """[(title, page)] Windows would show for this single item, in Windows' order, plus Appearance."""
    if not platform_win.IS_WINDOWS or item.drive or item.trash or not os.path.exists(item.path):
        return []
    tabs = []
    is_dir = item.opens_as_folder
    path = item.open_path if is_dir else item.path
    if is_dir and os.path.splitdrive(path)[1].strip("\\/"):
        tabs.append(("Sharing", SharingPage(item, window)))
    if not is_dir and winprops.has_signature(path):
        tabs.append(("Digital Signatures", SignaturesPage(item, window)))
    tabs.append(("Security", SecurityPage(item, window)))
    key = winprops.known_folder_of(path) if is_dir else None
    if key:
        tabs.append(("Location", LocationPage(item, window, key)))
    tabs.append(("Previous Versions", PreviousVersionsPage(item, window)))
    if _colorable(item):
        tabs.append(("Appearance", AppearancePage([item], window)))
    return tabs
