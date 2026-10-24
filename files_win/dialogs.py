"""Dialogs: new folder, rename, compress, conflicts, properties, preferences,
keyboard shortcuts, about, select pattern and visible columns."""

import fnmatch
import os

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QCheckBox, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QScrollArea, QVBoxLayout, QWidget)

from . import icons, model, theme
from .settings import settings
from .widgets import AlertDialog, BoxedList, Popover, Sheet, Switch, action_row, button, label, property_row

INVALID = set('<>:"/\\|?*')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def validate_name(name, folder, is_dir, original=None):
    """Returns (ok, message) like Nautilus' file name widget."""
    kind = "Folder" if is_dir else "File"
    stripped = name.strip()
    if not stripped:
        return False, ""
    bad = [c for c in name if c in INVALID]
    if bad:
        return False, f"{kind} names cannot contain “{bad[0]}”."
    if stripped in (".", ".."):
        return False, f"A {kind.lower()} cannot be called “{stripped}”."
    if os.path.splitext(stripped)[0].upper() in RESERVED:
        return False, f"“{stripped}” is reserved by Windows."
    if name.endswith((" ", ".")):
        return False, f"{kind} names cannot end with a space or a dot."
    if len(name) > 255:
        return False, f"{kind} name is too long."
    if original is None or os.path.normcase(name) != os.path.normcase(original):
        target = os.path.join(folder, name)
        if os.path.lexists(target) and (original is None or name.lower() != original.lower()):
            return False, ("A folder with that name already exists." if os.path.isdir(target)
                           else "A file with that name already exists.")
    if name.startswith("."):
        return True, f"{kind}s with “.” at the beginning of their name are hidden."
    return True, ""


class NameDialog(Sheet):
    """'New Folder' / 'New File' sheet with live validation."""

    def __init__(self, parent, folder, title, label_text, action, initial="", is_dir=True):
        super().__init__(parent, title, 400)
        self.folder, self.is_dir = folder, is_dir
        self.body.addWidget(label(label_text, dim=True))
        self.entry = QLineEdit(initial)
        self.entry.setMinimumHeight(38)
        self.body.addWidget(self.entry)
        self.msg = label("", caption=True, wrap=True)
        self.body.addWidget(self.msg)
        self.ok = button(action, suggested=True)
        self.ok.setMinimumHeight(36)
        self.ok.clicked.connect(self._accept)
        self.header_start.addSpacing(26)
        self.body.addWidget(self.ok)
        self.entry.textChanged.connect(self._check)
        self.entry.returnPressed.connect(self._accept)
        self._check()
        base, _ext = os.path.splitext(initial)
        QTimer.singleShot(0, lambda: (self.entry.setFocus(), self.entry.setSelection(0, len(base) if not is_dir else len(initial))))

    def _check(self):
        ok, msg = validate_name(self.entry.text(), self.folder, self.is_dir)
        self.ok.setEnabled(ok)
        self.msg.setText(msg)
        self.msg.setProperty("error", not ok and bool(msg))
        self.msg.style().unpolish(self.msg)
        self.msg.style().polish(self.msg)
        return ok

    def _accept(self):
        if self._check():
            self.accept()

    def value(self):
        return self.entry.text().strip() if self.exec() else None


class RenamePopover(Popover):
    def __init__(self, parent, item, on_rename):
        super().__init__(parent)
        self.item, self.on_rename = item, on_rename
        self.card.setFixedWidth(300)
        self.body.addWidget(label("Rename Folder" if item.is_dir else "Rename File", heading=True, center=True))
        row = QHBoxLayout()
        self.entry = QLineEdit(item.name)
        self.entry.setMinimumHeight(34)
        row.addWidget(self.entry, 1)
        self.ok = button("_Rename", suggested=True)
        self.ok.setMinimumHeight(34)
        row.addWidget(self.ok)
        self.body.addLayout(row)
        self.msg = label("", caption=True, wrap=True)
        self.msg.hide()
        self.body.addWidget(self.msg)
        self.entry.textChanged.connect(self._check)
        self.entry.returnPressed.connect(self._go)
        self.ok.clicked.connect(self._go)
        base = item.name if item.is_dir else os.path.splitext(item.name)[0]
        QTimer.singleShot(0, lambda: (self.entry.setFocus(), self.entry.setSelection(0, len(base or item.name))))

    def _check(self):
        ok, msg = validate_name(self.entry.text(), os.path.dirname(self.item.path), self.item.is_dir, self.item.name)
        self.ok.setEnabled(ok)
        self.msg.setText(msg)
        self.msg.setVisible(bool(msg))
        self.msg.setProperty("error", not ok)
        self.msg.style().polish(self.msg)
        self.adjustSize()
        return ok

    def _go(self):
        if self._check():
            new = self.entry.text().strip()
            self.close()
            if new != self.item.name:
                self.on_rename(self.item, new)


class CompressDialog(Sheet):
    FORMATS = [(".zip", "Compatible with all operating systems."),
               (".tar.xz", "Smaller archives but Linux and Mac only."),
               (".tar.gz", "Widely supported on Linux and Mac.")]

    def __init__(self, parent, folder, default_name):
        super().__init__(parent, "Create Archive", 420)
        self.folder = folder
        self.body.addWidget(label("Archive Name", dim=True))
        self.entry = QLineEdit(default_name)
        self.entry.setMinimumHeight(38)
        self.body.addWidget(self.entry)
        self.msg = label("", caption=True)
        self.body.addWidget(self.msg)
        box = BoxedList()
        self.group = QButtonGroup(self)
        from PySide6.QtWidgets import QRadioButton
        for i, (ext, desc) in enumerate(self.FORMATS):
            rb = QRadioButton()
            rb.setChecked(i == 0)
            self.group.addButton(rb, i)
            box.add_row(action_row(ext, desc, rb))
        self.body.addWidget(box)
        self.ok = button("C_reate", suggested=True)
        self.ok.setMinimumHeight(36)
        self.ok.clicked.connect(self._accept)
        self.body.addWidget(self.ok)
        self.entry.textChanged.connect(self._check)
        self.group.idToggled.connect(lambda *_: self._check())
        self.entry.returnPressed.connect(self._accept)
        self._check()
        QTimer.singleShot(0, lambda: (self.entry.setFocus(), self.entry.selectAll()))

    def filename(self):
        return self.entry.text().strip() + self.FORMATS[self.group.checkedId()][0]

    def _check(self):
        ok, msg = validate_name(self.filename(), self.folder, False)
        self.ok.setEnabled(ok and bool(self.entry.text().strip()))
        self.msg.setText(msg)
        return ok

    def _accept(self):
        if self._check():
            self.accept()

    def value(self):
        return os.path.join(self.folder, self.filename()) if self.exec() else None


def ask_conflict(parent, src, dst, remaining):
    """Returns (choice, apply_all) with choice in {'replace','skip','rename','cancel'}."""
    is_dir = os.path.isdir(dst) and os.path.isdir(src)
    name = os.path.basename(dst)
    folder = os.path.basename(os.path.dirname(dst)) or os.path.dirname(dst)
    if is_dir:
        heading = f"Merge Folder “{name}”?"
        body = (f"A folder with the same name already exists in “{folder}”. "
                "Merging will ask for confirmation before replacing any files in the folder that conflict.")
    else:
        heading = f"Replace File “{name}”?"
        body = f"Another file with the same name already exists in “{folder}”. Replacing it will overwrite its content."
    try:
        a, b = os.stat(src), os.stat(dst)
        body += (f"\n\nOriginal: {model.format_size(b.st_size)}, {model.format_date(b.st_mtime)}"
                 f"\nReplace with: {model.format_size(a.st_size)}, {model.format_date(a.st_mtime)}") if not is_dir else ""
    except OSError:
        pass
    check = QCheckBox("Apply this action to all files and folders")
    check.setVisible(remaining > 1)
    d = AlertDialog(parent, heading, body,
                    (("skip", "_Skip", None), ("rename", "_Keep Both", None),
                     ("replace", "_Merge" if is_dir else "_Replace", "destructive")),
                    default="skip", extra=check, width=420)
    r = d.run()
    return (r or "cancel"), check.isChecked()


# ------------------------------------------------------------------ properties

class PropertiesDialog(Sheet):
    def __init__(self, parent, items, location=None):
        title = "Properties" if len(items) != 1 else items[0].name
        super().__init__(parent, "Properties", 400)
        self.items = items
        self.body.setSpacing(10)
        general = QWidget()
        g = QVBoxLayout(general)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(14)
        top = QVBoxLayout()
        top.setSpacing(6)
        icon = QLabel()
        icon.setAlignment(Qt.AlignCenter)
        from .views import item_pixmap
        if len(items) == 1:
            pm, is_thumb = item_pixmap(items[0], 96)
            if is_thumb:
                pm = QPixmap.fromImage(pm).scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            icon.setPixmap(pm)
        else:
            icon.setPixmap(icons.color_pixmap("folder" if all(i.is_dir for i in items) else "generic", 96))
        top.addWidget(icon)
        name = label(title if len(items) == 1 else f"{len(items)} items", title2=True, center=True, wrap=True,
                     selectable=True)
        top.addWidget(name)
        it = items[0]
        if len(items) == 1:
            sub = icons.type_description(it.name, it.is_dir) if not it.drive else "Drive"
        else:
            sub = ""
        self.size_label = label(sub, dim=True, center=True)
        top.addWidget(self.size_label)
        g.addLayout(top)

        box = BoxedList()
        self.contents_row = None
        if len(items) == 1 and it.drive:
            u = it.drive.usage()
            if u:
                box.add_row(property_row("Capacity", model.format_size(u.total)))
                box.add_row(property_row("Used", f"{model.format_size(u.used)} ({u.used * 100 // max(1, u.total)}%)"))
                box.add_row(property_row("Free", model.format_size(u.free)))
            box.add_row(property_row("Drive", it.drive.root))
        elif len(items) == 1:
            if it.is_dir:
                self.contents_row = box.add_row(property_row("Contents", "Calculating…"))
            else:
                box.add_row(property_row("Size", f"{model.format_size(it.size)} ({it.size:,} bytes)"))
            if it.trash:
                box.add_row(property_row("Original Location", it.trash.original))
                if it.trash.deleted:
                    box.add_row(property_row("Trashed On", model.format_date(it.trash.deleted, True)))
            else:
                box.add_row(property_row("Parent Folder", os.path.dirname(it.path)))
            if it.target:
                box.add_row(property_row("Link Target", it.target))
        else:
            self.contents_row = box.add_row(property_row("Total Size", "Calculating…"))
            box.add_row(property_row("Parent Folder", os.path.dirname(it.path)))
        g.addWidget(box)

        if len(items) == 1 and not it.drive and not it.trash:
            times = BoxedList()
            times.add_row(property_row("Modified", model.format_date(it.mtime, True)))
            times.add_row(property_row("Accessed", model.format_date(it.atime, True)))
            times.add_row(property_row("Created", model.format_date(it.ctime, True)))
            g.addWidget(times)

        g.addStretch(1)
        from .propertytabs import tabs_for_items
        self.tabs = tabs_for_items(items, parent)
        if self.tabs:
            self._build_tabs(general)
        else:
            self.body.addWidget(general)

        if self.contents_row is not None:
            paths = [i.path for i in items]
            self._result = None
            model.run_async(lambda: self._count(paths))
            self._poll = QTimer(self, interval=150, timeout=self._update)
            self._poll.start()

    def _build_tabs(self, general):
        from PySide6.QtWidgets import QStackedWidget, QTabBar
        self.card.setFixedWidth(max(600, min(700, 600 + 40 * (len(self.tabs) - 4))))
        bar = QTabBar()
        bar.setExpanding(False)
        bar.setDrawBase(False)
        bar.setFocusPolicy(Qt.NoFocus)
        bar.setStyleSheet("QTabBar::tab { min-width: 0; padding: 6px 10px; margin: 2px 1px; }")
        self.stack = QStackedWidget()
        bar.addTab("General")
        self.stack.addWidget(general)
        for title, page in self.tabs:
            bar.addTab(title)
            self.stack.addWidget(page)
        self.stack.setFixedHeight(520)
        bar.currentChanged.connect(self._tab_changed)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(bar)
        row.addStretch(1)
        self.body.addLayout(row)
        self.body.addWidget(self.stack)

    def _tab_changed(self, index):
        self.stack.setCurrentIndex(index)
        page = self.stack.currentWidget()
        if hasattr(page, "activate"):
            page.activate()

    def _count(self, paths):
        files = folders = 0
        total = 0
        for p in paths:
            if os.path.isdir(p):
                for root, dirs, names in os.walk(p):
                    folders += len(dirs)
                    for n in names:
                        files += 1
                        try:
                            total += os.path.getsize(os.path.join(root, n))
                        except OSError:
                            pass
            else:
                files += 1
                total += os.path.getsize(p)
        self._result = (files, folders, total)

    def _update(self):
        if self._result is None:
            return
        self._poll.stop()
        files, folders, total = self._result
        parts = []
        if folders:
            parts.append(f"{folders} folder{'s' if folders != 1 else ''}")
        parts.append(f"{files} file{'s' if files != 1 else ''}")
        self.contents_row.value_label.setText(f"{', '.join(parts)} — {model.format_size(total)}")
        if self.items[0].is_dir and len(self.items) == 1:
            self.size_label.setText(f"Folder — {model.format_size(total)}")


# ------------------------------------------------------------------ preferences

class _Swatch(QAbstractButton):
    """A round accent color button, ringed and ticked when chosen (GNOME Settings style)."""

    def __init__(self, name, color):
        super().__init__()
        self.name, self.color = name, QColor(color)
        self.setCheckable(True)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(name.capitalize())

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.isChecked():
            p.setPen(QPen(self.color, 2))
            p.drawEllipse(QRectF(1.5, 1.5, 29, 29))
        elif self.underMouse():
            p.setPen(QPen(theme.current().fg_alpha(0.25), 2))
            p.drawEllipse(QRectF(1.5, 1.5, 29, 29))
        p.setPen(QPen(theme.current().fg_alpha(0.18), 1))     # keeps colors close to the background visible
        p.setBrush(self.color)
        p.drawEllipse(QRectF(5, 5, 22, 22))
        if self.isChecked():
            tick = QColor("white") if self.color.lightnessF() < 0.62 else QColor("#1e1e2e")
            p.drawPixmap(9, 9, icons.symbolic_pixmap("object-select", tick, 14))

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


class AccentPicker(QWidget):
    """A grid of color swatches; on_remove adds a right-click "Remove" to each one."""

    def __init__(self, on_pick, on_remove=None):
        super().__init__()
        self.on_pick, self.on_remove = on_pick, on_remove
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(6)
        self.grid.setVerticalSpacing(4)
        self.group = QButtonGroup(self)

    def set_colors(self, colors, current):
        """colors: [(name, color)]; up to 9 fit on a row, more split into two even rows."""
        for b in self.group.buttons():
            self.group.removeButton(b)
            self.grid.removeWidget(b)
            b.hide()
            b.deleteLater()
        cols = len(colors) if len(colors) <= 9 else (len(colors) + 1) // 2
        for i, (name, color) in enumerate(colors):
            b = _Swatch(name, color)
            b.setChecked(name == current)
            b.clicked.connect(lambda _=False, n=name: self.on_pick(n))
            if self.on_remove:
                b.setContextMenuPolicy(Qt.CustomContextMenu)
                b.customContextMenuRequested.connect(lambda pos, b=b: self._menu(b, pos))
            self.group.addButton(b)
            self.grid.addWidget(b, i // cols, i % cols)
            b.show()
        for c in range(self.grid.columnCount()):
            self.grid.setColumnStretch(c, 0)
        self.grid.setColumnStretch(cols, 1)    # keep the swatches packed to the left

    def set_current(self, name):
        self.group.setExclusive(False)
        for b in self.group.buttons():
            b.setChecked(b.name == name)
        self.group.setExclusive(True)

    def _menu(self, swatch, pos):
        from .widgets import make_menu, add_action
        m = make_menu(self)
        add_action(m, "_Remove", lambda: self.on_remove(swatch.name))
        m.exec(swatch.mapToGlobal(pos))


class PreferencesDialog(Sheet):
    STYLES = [("system", "Follow System"), ("light", "Light"), ("dark", "Dark")]
    THEMES = [("adwaita", "Adwaita"), ("catppuccin", "Catppuccin")]

    def __init__(self, parent, on_change):
        super().__init__(parent, "Preferences", 520)
        self.on_change = on_change
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        def group(title, rows):
            v.addWidget(label(title, heading=True))
            box = BoxedList()
            for r in rows:
                box.add_row(r)
            v.addWidget(box)
            v.addSpacing(10)
            return box

        def switch(key, title, subtitle=""):
            s = Switch(bool(settings[key]))
            s.toggled.connect(lambda on, k=key: self._set(k, on))
            return action_row(title, subtitle, s)

        from PySide6.QtWidgets import QComboBox

        def combo(key, options):
            box = QComboBox()
            box.addItems([text for _k, text in options])
            keys = [k for k, _t in options]
            box.setCurrentIndex(keys.index(settings[key]) if settings[key] in keys else 0)
            box.currentIndexChanged.connect(lambda i: self._set(key, keys[i]))
            box.setMinimumWidth(150)
            return box

        accent_row = QWidget()
        accent_row.setObjectName("RowCard")
        al = QVBoxLayout(accent_row)
        al.setContentsMargins(14, 10, 12, 12)
        al.setSpacing(8)
        al.addWidget(label("Accent Color"))
        self.accents = AccentPicker(self._pick_accent)
        al.addWidget(self.accents)
        self.flavor_row = action_row("Dark Flavor", "Light style always uses Latte",
                                     combo("catppuccin_flavor", [(f, theme.FLAVOR_NAMES[f]) for f in theme.DARK_FLAVORS]))
        appearance = group("Appearance", [
            action_row("Style", "Light or dark appearance", combo("theme", self.STYLES)),
            action_row("Theme", "Colors used throughout the app", combo("color_theme", self.THEMES)),
            self.flavor_row,
            accent_row,
        ])
        lay = appearance.lay
        self.flavor_sep = lay.itemAt(lay.indexOf(self.flavor_row) - 1).widget()
        self._refresh_appearance()

        click = Switch(settings["click_policy"] == "single")
        click.toggled.connect(lambda on: self._set("click_policy", "single" if on else "double"))

        group("General", [
            switch("folders_first", "Sort Folders Before Files"),
            action_row("Open Items With a Single Click", "Instead of double-clicking", click),
        ])
        group("Optional Context Menu Actions", [
            switch("show_create_link", "Create Link", "Show an action to create shortcuts"),
            switch("show_delete_permanently", "Delete Permanently", "Show an action to bypass the trash"),
        ])
        group("Performance", [
            switch("thumbnails", "Show Thumbnails", "Previews of images in folders"),
            switch("count_folder_items", "Count Number of Files in Folders", "Shown in the list view's size column"),
        ])
        v.addStretch(1)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(min(560, inner.sizeHint().height() + 8))
        self.body.addWidget(scroll)

    def _set(self, key, value):
        settings[key] = value
        self.on_change(key)
        if key in theme.THEME_KEYS:
            self._refresh_appearance()

    def _pick_accent(self, name):
        self._set("catppuccin_accent" if settings["color_theme"] == "catppuccin" else "accent", name)

    def _refresh_appearance(self):
        t = theme.current()
        catppuccin = t.name == "catppuccin"
        self.flavor_row.setVisible(catppuccin)
        self.flavor_sep.setVisible(catppuccin)
        if catppuccin:
            flavor = theme.CATPPUCCIN[settings["catppuccin_flavor"] if t.dark else "latte"]
            colors = [(n, flavor[n]) for n in theme.CATPPUCCIN_ACCENTS]
            current = settings["catppuccin_accent"]
        else:
            colors = list(theme.ADWAITA_ACCENTS.items())
            current = settings["accent"]
        self.accents.set_colors(colors, current)
        self.update()


SHORTCUTS = [
    ("General", [("New window", "Ctrl+N"), ("Close window or tab", "Ctrl+W"), ("Quit", "Ctrl+Q"),
                 ("Search", "Ctrl+F"), ("Search everywhere", "Ctrl+Shift+F"), ("Bookmark current location", "Ctrl+D"),
                 ("Show help", "F1"), ("Shortcuts", "Ctrl+?"), ("Preferences", "Ctrl+,"),
                 ("Undo", "Ctrl+Z"), ("Redo", "Ctrl+Shift+Z")]),
    ("Opening", [("Open", "Return"), ("Open in new tab", "Ctrl+Return"), ("Open in new window", "Shift+Return"),
                 ("Open item location (search and recent only)", "Ctrl+Alt+O"), ("Quick preview", "Space"),
                 ("Open in console", "Ctrl+.")]),
    ("Tabs", [("New tab", "Ctrl+T"), ("Restore closed tab", "Ctrl+Shift+T"), ("Go to previous tab", "Ctrl+Page Up"),
              ("Go to next tab", "Ctrl+Page Down"), ("Move tab left", "Ctrl+Shift+Page Up"),
              ("Move tab right", "Ctrl+Shift+Page Down"), ("Switch to tab 1…9", "Alt+1…9")]),
    ("Navigation", [("Go back", "Alt+Left"), ("Go forward", "Alt+Right"), ("Go up", "Alt+Up"),
                    ("Go down", "Alt+Down"), ("Go to home folder", "Alt+Home"), ("Enter location", "Ctrl+L"),
                    ("Location bar with root location", "/"), ("Location bar with home location", "~"),
                    ("Reload", "F5"), ("Toggle sidebar", "F9"), ("Current folder menu", "F10")]),
    ("View", [("Zoom in", "Ctrl++"), ("Zoom out", "Ctrl+-"), ("Reset zoom", "Ctrl+0"),
              ("Show/hide hidden files", "Ctrl+H"), ("List view", "Ctrl+1"), ("Grid view", "Ctrl+2")]),
    ("Editing", [("Create folder", "Ctrl+Shift+N"), ("Rename", "F2"), ("Move to trash", "Delete"),
                 ("Delete permanently", "Shift+Delete"), ("Create link", "Ctrl+M"),
                 ("Cut", "Ctrl+X"), ("Copy", "Ctrl+C"), ("Paste", "Ctrl+V"), ("Select all", "Ctrl+A"),
                 ("Invert selection", "Ctrl+Shift+I"), ("Select items matching", "Ctrl+S"),
                 ("Show item properties", "Ctrl+I")]),
]


class ShortcutsDialog(Sheet):
    def __init__(self, parent):
        super().__init__(parent, "Keyboard Shortcuts", 520)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 8, 0)
        for title, rows in SHORTCUTS:
            v.addWidget(label(title, heading=True))
            box = BoxedList()
            for desc, keys in rows:
                k = QLabel(keys)
                t = theme.current()
                k.setStyleSheet(f"background: {theme.rgba(t.fg_alpha(0.1))}; border-radius: 6px; padding: 3px 8px;"
                                f" font-weight: 700; font-size: 9pt;")
                box.add_row(action_row(desc, "", k))
            v.addWidget(box)
            v.addSpacing(10)
        scroll.setWidget(inner)
        scroll.setFixedHeight(560)
        self.body.addWidget(scroll)


class AboutDialog(Sheet):
    def __init__(self, parent, version):
        super().__init__(parent, "", 360)
        self.body.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(icons.app_icon().pixmap(128, 128))
        icon.setAlignment(Qt.AlignCenter)
        self.body.addWidget(icon)
        self.body.addWidget(label("Files", title1=True, center=True))
        self.body.addWidget(label("A Nautilus-inspired file manager for Windows", dim=True, center=True, wrap=True))
        ver = QLabel(version)
        ver.setAlignment(Qt.AlignCenter)
        t = theme.current()
        ver.setStyleSheet(f"color: {theme.rgba(t.accent)}; background: {theme.rgba(t.accent_alpha(0.12))};"
                          " border-radius: 12px; padding: 4px 12px;")
        self.body.addWidget(ver, 0, Qt.AlignCenter)
        self.body.addSpacing(12)
        self.body.addWidget(label("An independent recreation of the look and behavior of GNOME Files "
                                  "(Nautilus), written with Python and Qt. Not affiliated with "
                                  "or endorsed by the GNOME Project.", caption=True, center=True, wrap=True))


class SelectPatternDialog(Sheet):
    def __init__(self, parent):
        super().__init__(parent, "Select Items Matching", 380)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Pattern")
        self.entry.setMinimumHeight(38)
        self.body.addWidget(self.entry)
        self.body.addWidget(label("Examples: *.txt, file?.png, *report*", caption=True))
        ok = button("_Select", suggested=True)
        ok.clicked.connect(self.accept)
        self.entry.returnPressed.connect(self.accept)
        self.body.addWidget(ok)
        QTimer.singleShot(0, self.entry.setFocus)

    def value(self):
        return self.entry.text().strip() if self.exec() else None


def matches(pattern, name):
    return fnmatch.fnmatch(name.casefold(), pattern.casefold())


class ColumnsDialog(Sheet):
    OPTIONAL = [("size", "Size", "The amount of space taken"), ("type", "Type", "The kind of file"),
                ("modified", "Modified", "When the item was last changed"),
                ("starred", "Star", "Shows whether the item is starred")]

    def __init__(self, parent, current, on_change):
        super().__init__(parent, "Visible Columns", 400)
        self.cols = list(current)
        box = BoxedList()
        self.body.addWidget(label("Choose the order of information to appear in this folder.", dim=True, wrap=True))
        for key, title, desc in self.OPTIONAL:
            s = Switch(key in self.cols)
            s.toggled.connect(lambda on, k=key: self._toggle(k, on, on_change))
            box.add_row(action_row(title, desc, s))
        self.body.addWidget(box)

    def _toggle(self, key, on, on_change):
        if on and key not in self.cols:
            self.cols.append(key)
        elif not on and key in self.cols:
            self.cols.remove(key)
        order = ["size", "type", "modified", "starred"]
        self.cols = [c for c in order if c in self.cols]
        on_change(self.cols)


# ------------------------------------------------------------------ folder color (Properties → Appearance)

def parse_color(text):
    """'#rgb', '#rrggbb', 'rrggbb' or a CSS color name -> '#rrggbb', else None."""
    text = text.strip()
    if len(text) in (3, 6) and all(ch in "0123456789abcdefABCDEF" for ch in text):
        text = "#" + text
    c = QColor(text) if QColor.isValidColorName(text) else QColor()
    return c.name() if c.isValid() else None


class FolderColorPicker(QWidget):
    """Folder color from the palette, saved custom colors or a color code, applied as you pick.

    on_change(color) gets "#rrggbb", or "" for the default color."""

    PRESETS = [("blue", "#62a0ea"), ("teal", "#33b2a4"), ("green", "#57e389"), ("yellow", "#f8e45c"),
               ("orange", "#ffa348"), ("red", "#ed333b"), ("pink", "#f17eb8"), ("purple", "#c061cb"),
               ("brown", "#b5835a"), ("slate", "#8b9bb4"), ("gray", "#9a9996"), ("charcoal", "#3d3846")]

    def __init__(self, items, current, on_change):
        super().__init__()
        self.color, self.on_change = current, on_change
        self.special = None
        if len(items) == 1:
            from .views import _SPECIAL
            self.special = _SPECIAL.get(os.path.normcase(os.path.normpath(items[0].open_path)))
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 4, 0, 0)
        body.setSpacing(8)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        body.addWidget(self.preview)
        body.addWidget(label(items[0].name if len(items) == 1 else f"{len(items)} folders", dim=True, center=True))
        body.addSpacing(4)

        body.addWidget(label("Folder Color", heading=True))
        body.addWidget(label("Palette", caption=True))
        self.palette = AccentPicker(lambda n: self._choose(dict(self.PRESETS)[n]))
        self.palette.set_colors(self.PRESETS, None)
        body.addWidget(self.palette)
        body.addSpacing(6)

        row = QHBoxLayout()
        row.addWidget(label("Custom Colors", caption=True))
        row.addStretch(1)
        self.save_btn = button("Save Color", flat=True)
        self.save_btn.setIcon(icons.symbolic("list-add", theme.current().icon_fg))
        self.save_btn.setToolTip("Save the current color for later")
        self.save_btn.clicked.connect(self._save_custom)
        row.addWidget(self.save_btn)
        body.addLayout(row)
        self.customs = AccentPicker(self._choose, on_remove=self._remove_custom)
        body.addWidget(self.customs)
        self.customs_note = label("Colors you save appear here. Right-click one to remove it.",
                                  caption=True, wrap=True)
        body.addWidget(self.customs_note)
        body.addSpacing(6)

        body.addWidget(label("Color Code", caption=True))
        code_row = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText(icons.DEFAULT_FOLDER_COLOR)
        self.code.setMinimumHeight(36)
        self.code.textEdited.connect(self._typed)
        code_row.addWidget(self.code, 1)
        pick = button("Pick…")
        pick.setMinimumHeight(36)
        pick.setToolTip("Choose any color, or pick one from the screen")
        pick.clicked.connect(self._pick_any)
        code_row.addWidget(pick)
        self.reset_btn = button("_Reset to Default")
        self.reset_btn.setMinimumHeight(36)
        self.reset_btn.clicked.connect(lambda: self._choose(None))
        code_row.addWidget(self.reset_btn)
        body.addLayout(code_row)
        self.msg = label("", caption=True)
        self.msg.setProperty("error", True)
        body.addWidget(self.msg)
        body.addStretch(1)

        self._refresh_customs()
        self._show(current)

    def _show(self, color, from_typing=False):
        self.color = color
        self.preview.setPixmap(icons.color_pixmap("folder", 96, self.special, color))
        if not from_typing:
            self.code.setText(color or "")
            self.msg.setText("")
        self.palette.set_current(next((n for n, c in self.PRESETS if c == color), None))
        self.customs.set_current(color)
        self.save_btn.setEnabled(bool(color) and color not in settings["custom_colors"])
        self.reset_btn.setEnabled(bool(color))

    def _choose(self, color, from_typing=False):
        self._show(color, from_typing)
        self.on_change(color or "")

    def _typed(self, text):
        if not text.strip():
            self.msg.setText("")
            return self._choose(None, from_typing=True)
        color = parse_color(text)
        self.msg.setText("" if color else "Use a code like #3584e4 or a color name like teal.")
        if color:
            self._choose(color, from_typing=True)

    def _pick_any(self):
        from PySide6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(QColor(self.color or icons.DEFAULT_FOLDER_COLOR), self.window(), "Folder Color")
        if c.isValid():
            self._choose(c.name())

    def _refresh_customs(self):
        saved = settings["custom_colors"]
        self.customs.set_colors([(c, c) for c in saved], self.color)
        self.customs.setVisible(bool(saved))
        self.customs_note.setVisible(not saved)

    def _save_custom(self):
        if self.color and self.color not in settings["custom_colors"]:
            settings["custom_colors"] = (settings["custom_colors"] + [self.color])[-14:]
            self._refresh_customs()
            self._show(self.color)

    def _remove_custom(self, color):
        settings["custom_colors"] = [c for c in settings["custom_colors"] if c != color]
        self._refresh_customs()
        self._show(self.color)
