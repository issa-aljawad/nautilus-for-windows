"""The Files window: sidebar, header bar, tabs, slots, menus and actions."""

import ctypes
import os
import sys
from ctypes import wintypes

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QApplication, QFileDialog,
                               QGraphicsDropShadowEffect, QHBoxLayout, QLineEdit, QPlainTextEdit, QScrollBar,
                               QStackedWidget, QTabBar, QTextEdit, QToolButton, QVBoxLayout, QWidget,
                               QWidgetAction, QLabel)

from . import dialogs, model, operations, platform_win, quicklook, theme
from .model import OTHER, RECENT, STARRED, TRASH, VIRTUAL, SORTS
from .operations import copy_name
from .pathbar import LocationBar
from .settings import bookmarks, folder_colors, recent, settings, starred
from .sidebar import Sidebar, special_folder_map
from .slot import Slot, location_title
from .views import DEFAULT_SIZE, drop_action, drop_paths, icon_size, preset_sizes, set_special_folders
from .widgets import (Toast, add_action, add_submenu, alert, header_button, make_menu, refresh_symbolic,
                      set_symbolic, slider)

BREAKPOINT = 682
HELP_URL = "https://help.gnome.org/users/gnome-help/stable/files.html"

# --------------------------------------------------------------------- win32 frame

if platform_win.IS_WINDOWS:
    _user32 = ctypes.windll.user32
    _dwm = ctypes.windll.dwmapi
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _user32.MonitorFromWindow.restype = wintypes.HMONITOR
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]

    class _MARGINS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]

    class _NCCALCSIZE_PARAMS(ctypes.Structure):
        _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

WM_NCCALCSIZE, WM_NCHITTEST = 0x0083, 0x0084
HTCLIENT, HTCAPTION = 1, 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 10, 11, 12, 13, 14, 15, 16, 17


class TabBar(QTabBar):
    """Tabs take dropped files into that tab's folder, and switch to a tab when a drag rests on it
    (so the files can be dropped into a folder inside it)."""

    dropped = Signal(list, str, object)     # paths, target folder, Qt.DropAction

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self._hover = -1
        self._spring = QTimer(self, singleShot=True, interval=650, timeout=self._switch)

    def _target(self, index, paths):
        s = self.tabData(index) if index >= 0 else None
        loc = s.location if s is not None else None
        if loc is None or (loc in VIRTUAL and loc != TRASH):
            return None
        n = os.path.normcase(os.path.normpath(loc))
        if all(os.path.normcase(os.path.dirname(os.path.normpath(p))) == n for p in paths):
            return None     # already in that folder
        return loc

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        i = self.tabAt(e.position().toPoint())
        if i != self._hover:
            self._hover = i
            if i >= 0 and i != self.currentIndex():
                self._spring.start()
            else:
                self._spring.stop()
        paths = drop_paths(e.mimeData())
        target = self._target(i, paths) if paths else None
        if target is None:
            e.ignore()
            return
        e.setDropAction(drop_action(e.modifiers(), paths, target))
        e.accept()

    def dragLeaveEvent(self, e):
        self._spring.stop()
        self._hover = -1
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self._spring.stop()
        self._hover = -1
        paths = drop_paths(e.mimeData())
        target = self._target(self.tabAt(e.position().toPoint()), paths) if paths else None
        if target is None:
            e.ignore()
            return
        action = drop_action(e.modifiers(), paths, target)
        e.setDropAction(action)
        e.accept()
        self.dropped.emit(paths, target, action)

    def _switch(self):
        if 0 <= self._hover < self.count():
            self.setCurrentIndex(self._hover)


class FilesWindow(QWidget):
    def __init__(self, app, location=None):
        super().__init__()
        self.app = app
        self.ops = operations.operations
        self.setObjectName("Window")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Files")
        self.setMinimumSize(360, 294)
        self.native_frame = "--native-frame" in sys.argv or not platform_win.IS_WINDOWS
        if not self.native_frame:
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self._frame_ready = False
        self.closed_tabs = []
        self.collapsed = None   # decided on first resize
        self.overlay_sidebar = False
        self._build()
        self._shortcuts()
        w, h = settings["window_size"]
        self.resize(max(360, w), max(294, h))
        set_special_folders(special_folder_map())
        self.add_tab(location or platform_win.HOME)
        app.themeChanged.connect(self._theme_changed)
        app.cutChanged.connect(self._cut_changed)
        self.ops.history_changed.connect(self.update_header)
        self.ops.job_finished.connect(self._job_finished)
        QApplication.instance().installEventFilter(self)    # Space-over-a-file Quick Look
        if settings["maximized"]:
            QTimer.singleShot(0, self.showMaximized)

    # ================================================================= building
    def _build(self):
        self.sidebar = Sidebar(self.ops, self)
        self.sidebar.search_button.clicked.connect(lambda on: self.toggle_search(on, global_=True))
        self.sidebar.menu_button.clicked.connect(self.show_main_menu)
        pl = self.sidebar.places
        pl.activatedPath.connect(self._sidebar_activate)
        pl.menuRequested.connect(self._sidebar_menu)
        pl.dropped.connect(self.handle_drop)
        pl.bookmarkDropped.connect(lambda paths: [self.add_bookmark(p) for p in paths])
        pl.ejectRequested.connect(lambda d: (platform_win.eject_drive(d), QTimer.singleShot(2500, self.refresh_sidebars)))
        fx = QGraphicsDropShadowEffect(self.sidebar)
        fx.setBlurRadius(24)
        fx.setOffset(0, 0)
        fx.setColor(Qt.black)
        fx.setEnabled(False)
        self.sidebar.setGraphicsEffect(fx)

        self.content = QWidget(self)
        self.content.setObjectName("Content")
        self.content.setAttribute(Qt.WA_StyledBackground)
        cv = QVBoxLayout(self.content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        # header bar
        self.header = QWidget()
        self.header.setObjectName("ContentHeader")
        self.header.setAttribute(Qt.WA_StyledBackground)
        self.header.setProperty("dragArea", True)
        self.header.setFixedHeight(47)
        h = QHBoxLayout(self.header)
        h.setContentsMargins(7, 6, 7, 6)
        h.setSpacing(6)
        self.sidebar_btn = header_button("sidebar-show", "Show Sidebar", checkable=True)
        self.sidebar_btn.clicked.connect(self.toggle_sidebar)
        self.back_btn = header_button("go-previous", "Back")
        self.fwd_btn = header_button("go-next", "Forward")
        self.back_btn.clicked.connect(lambda: self.slot.go_back())
        self.fwd_btn.clicked.connect(lambda: self.slot.go_forward())
        for b in (self.back_btn, self.fwd_btn):
            b.setContextMenuPolicy(Qt.CustomContextMenu)
        self.back_btn.customContextMenuRequested.connect(lambda _p: self._history_menu(self.back_btn, True))
        self.fwd_btn.customContextMenuRequested.connect(lambda _p: self._history_menu(self.fwd_btn, False))
        h.addWidget(self.sidebar_btn)
        h.addWidget(self.back_btn)
        h.addWidget(self.fwd_btn)
        h.addSpacing(2)
        self.locbar = LocationBar()
        h.addWidget(self.locbar, 1)
        self.newtab_btn = header_button("tab-new", "New Tab (Ctrl+T)")
        self.newtab_btn.clicked.connect(self.new_tab)
        h.addWidget(self.newtab_btn)
        self.search_btn = header_button("folder-search", "Search Current Folder", checkable=True)
        self.search_btn.clicked.connect(lambda on: self.toggle_search(on, global_=False))
        h.addWidget(self.search_btn)
        h.addSpacing(2)
        self.view_toggle = header_button("view-list", "List View")
        self.view_toggle.clicked.connect(self.toggle_view_mode)
        self.view_menu_btn = header_button("pan-down", "View Options")
        self.view_menu_btn.setFixedWidth(22)
        self.view_menu_btn.clicked.connect(lambda: self.show_view_menu(self.view_menu_btn))
        split = QHBoxLayout()
        split.setSpacing(0)
        split.addWidget(self.view_toggle)
        split.addWidget(self.view_menu_btn)
        h.addLayout(split)
        self.controls = QWidget()
        ch = QHBoxLayout(self.controls)
        ch.setContentsMargins(8, 0, 2, 0)
        ch.setSpacing(10)
        self.min_btn = header_button("window-minimize", "Minimize")
        self.max_btn = header_button("window-maximize", "Maximize")
        self.close_btn = header_button("window-close", "Close")
        for b in (self.min_btn, self.max_btn, self.close_btn):
            b.setProperty("circular", True)
            b.setFixedSize(24, 24)
            ch.addWidget(b)
        self.min_btn.clicked.connect(self.showMinimized)
        self.max_btn.clicked.connect(lambda: self.showNormal() if self.isMaximized() else self.showMaximized())
        self.close_btn.clicked.connect(self.close)
        self.controls.setVisible(not self.native_frame)
        h.addWidget(self.controls)
        cv.addWidget(self.header)
        self.header_line = QWidget()
        self.header_line.setFixedHeight(1)
        cv.addWidget(self.header_line)

        pb = self.locbar.pathbar
        pb.navigate.connect(lambda p, how: self.open_location(p, how))
        pb.menuRequested.connect(self.show_location_menu)
        pb.crumbMenuRequested.connect(self._crumb_menu)
        pb.dropped.connect(self.handle_drop)
        self.locbar.entry.entered.connect(self._location_entered)
        self.locbar.entry.cancelled.connect(self._entry_cancelled)
        self.locbar.entry_cancel.clicked.connect(self._entry_cancelled)
        self.locbar.search.changed.connect(lambda q, t: self.slot.start_search(q, t, self.slot.search_global))
        self.locbar.search.closed.connect(lambda: self.toggle_search(False))
        self.locbar.search.focusView.connect(lambda: self.slot.focus_view())

        # tabs
        self.tabbar = TabBar()
        self.tabbar.dropped.connect(self.handle_drop)
        self.tabbar.setExpanding(True)
        self.tabbar.setMovable(True)
        self.tabbar.setDocumentMode(True)
        self.tabbar.setElideMode(Qt.ElideMiddle)
        self.tabbar.setDrawBase(False)
        self.tabbar.setFocusPolicy(Qt.NoFocus)
        self.tabbar.currentChanged.connect(self._tab_changed)
        self.tabbar.tabBarClicked.connect(lambda i: None)
        self.tabbar.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabbar.customContextMenuRequested.connect(self._tab_menu)
        self.tab_wrap = QWidget()
        tw = QHBoxLayout(self.tab_wrap)
        tw.setContentsMargins(6, 0, 6, 3)
        tw.setSpacing(4)
        tw.addWidget(self.tabbar, 1)
        self.tab_add = header_button("list-add", "New Tab (Ctrl+T)")
        self.tab_add.clicked.connect(self.new_tab)
        tw.addWidget(self.tab_add)
        self.tab_wrap.hide()
        cv.addWidget(self.tab_wrap)

        self.slot_stack = QStackedWidget()
        cv.addWidget(self.slot_stack, 1)

        # bottom bar for narrow windows (Nautilus' action bar)
        self.bottom = QWidget()
        self.bottom.setObjectName("BottomBar")
        self.bottom.setAttribute(Qt.WA_StyledBackground)
        bh = QHBoxLayout(self.bottom)
        bh.setContentsMargins(7, 5, 7, 5)
        self.b_back = header_button("go-previous", "Back")
        self.b_fwd = header_button("go-next", "Forward")
        self.b_back.clicked.connect(lambda: self.slot.go_back())
        self.b_fwd.clicked.connect(lambda: self.slot.go_forward())
        self.b_view = header_button("view-list", "List View")
        self.b_view.clicked.connect(self.toggle_view_mode)
        self.b_menu = header_button("pan-down", "View Options")
        self.b_menu.clicked.connect(lambda: self.show_view_menu(self.b_menu))
        bh.addWidget(self.b_back)
        bh.addWidget(self.b_fwd)
        bh.addStretch(1)
        bh.addWidget(self.b_view)
        bh.addWidget(self.b_menu)
        self.bottom.hide()
        cv.addWidget(self.bottom)

        self.toast = Toast(self.content)
        self.dim = QWidget(self)
        self.dim.hide()
        self.dim.mousePressEvent = lambda e: self._hide_overlay_sidebar()
        self._restyle_lines()

    def _restyle_lines(self):
        t = theme.current()
        self.header_line.setStyleSheet(f"background: {theme.rgba(t.shade if not t.dark else t.border)};")
        self.dim.setStyleSheet("background: rgba(0,0,0,0.25);")

    # ================================================================= layout
    def sidebar_width(self):
        return int(max(180, min(280, self.width() * 0.2)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        collapsed = self.width() <= BREAKPOINT
        if collapsed != self.collapsed:
            self.collapsed = collapsed
            self.overlay_sidebar = False
            self.sidebar_btn.setVisible(collapsed or not settings["sidebar_visible"])
            for wdg in (self.back_btn, self.fwd_btn, self.view_toggle, self.view_menu_btn):
                wdg.setVisible(not collapsed)
            self.bottom.setVisible(collapsed)
            self._update_tabbar()
        self._layout()
        if self.toast.isVisible():
            self.toast.reposition()

    def _layout(self):
        sw = self.sidebar_width()
        r = self.rect()
        docked = not self.collapsed and settings["sidebar_visible"]
        fx = self.sidebar.graphicsEffect()
        if docked:
            self.sidebar.setGeometry(0, 0, sw, r.height())
            self.sidebar.show()
            self.content.setGeometry(sw, 0, r.width() - sw, r.height())
            self.dim.hide()
            fx.setEnabled(False)
        else:
            self.content.setGeometry(r)
            if self.overlay_sidebar:
                w = min(280, int(r.width() * 0.8))
                self.dim.setGeometry(r)
                self.dim.show()
                self.dim.raise_()
                self.sidebar.setGeometry(0, 0, w, r.height())
                self.sidebar.show()
                self.sidebar.raise_()
                fx.setEnabled(True)
            else:
                self.sidebar.hide()
                self.dim.hide()
        self.sidebar_btn.setChecked(docked or self.overlay_sidebar)

    def toggle_sidebar(self):
        if self.collapsed:
            self.overlay_sidebar = not self.overlay_sidebar
        else:
            settings["sidebar_visible"] = not settings["sidebar_visible"]
            self.sidebar_btn.setVisible(not settings["sidebar_visible"])
        self._layout()

    def _hide_overlay_sidebar(self):
        if self.overlay_sidebar:
            self.overlay_sidebar = False
            self._layout()

    # ================================================================= frameless window (Windows)
    def showEvent(self, e):
        super().showEvent(e)
        if not self.native_frame and not self._frame_ready:
            self._frame_ready = True
            hwnd = int(self.winId())
            style = _user32.GetWindowLongPtrW(hwnd, -16)
            # WS_THICKFRAME | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX: snap, animations, shadow
            _user32.SetWindowLongPtrW(hwnd, -16, style | 0x00040000 | 0x00C00000 | 0x00080000 | 0x00020000 | 0x00010000)
            m = _MARGINS(0, 0, 1, 0)
            _dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            _dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
            dark = ctypes.c_int(1 if theme.current().dark else 0)
            _dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), 4)
            _user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)

    def nativeEvent(self, event_type, message):
        if self.native_frame or bytes(event_type) != b"windows_generic_MSG":
            return super().nativeEvent(event_type, message)
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_NCCALCSIZE and msg.wParam:
            if _user32.IsZoomed(msg.hWnd):
                params = _NCCALCSIZE_PARAMS.from_address(msg.lParam)
                mon = _user32.MonitorFromWindow(msg.hWnd, 2)
                info = _MONITORINFO()
                info.cbSize = ctypes.sizeof(info)
                if _user32.GetMonitorInfoW(mon, ctypes.byref(info)):
                    params.rgrc[0] = info.rcWork
            return True, 0
        if msg.message == WM_NCHITTEST:
            hit = self._hit_test(self.mapFromGlobal(QCursor.pos()))
            if hit is not None:
                return True, hit
        return super().nativeEvent(event_type, message)

    def _hit_test(self, pos):
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        if not self.isMaximized():
            m = 6
            left, right, top, bottom = x < m, x >= w - m, y < m, y >= h - m
            if top and left:
                return HTTOPLEFT
            if top and right:
                return HTTOPRIGHT
            if bottom and left:
                return HTBOTTOMLEFT
            if bottom and right:
                return HTBOTTOMRIGHT
            if left:
                return HTLEFT
            if right:
                return HTRIGHT
            if top:
                return HTTOP
            if bottom:
                return HTBOTTOM
        child = self.childAt(pos)
        wdg = child
        while wdg is not None and wdg is not self:
            if isinstance(wdg, (QAbstractButton, QLineEdit, QTabBar, QScrollBar, QAbstractItemView)):
                return HTCLIENT
            if wdg.property("dragArea"):
                return HTCAPTION
            if wdg.objectName() == "PathBar":
                return HTCLIENT
            wdg = wdg.parentWidget()
        return None

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.WindowStateChange:
            set_symbolic(self.max_btn, "window-restore" if self.isMaximized() else "window-maximize")
            self.max_btn.setToolTip("Restore" if self.isMaximized() else "Maximize")

    def mousePressEvent(self, e):
        # Fallback move for non-Windows frameless mode
        if self.native_frame or platform_win.IS_WINDOWS:
            return super().mousePressEvent(e)

    def closeEvent(self, e):
        settings["maximized"] = self.isMaximized()
        if not self.isMaximized():
            settings["window_size"] = [self.width(), self.height()]
        for s in self.slots():
            s.cancel_search(clear=False)
        QApplication.instance().removeEventFilter(self)
        self.app.window_closed(self)
        super().closeEvent(e)

    # ================================================================= tabs & slots
    def slots(self):
        return [self.tabbar.tabData(i) for i in range(self.tabbar.count())]

    @property
    def slot(self):
        return self.tabbar.tabData(self.tabbar.currentIndex())

    def new_tab(self):
        """A fresh tab opens on Other Locations, to pick a drive or place from there."""
        return self.add_tab(OTHER)

    def add_tab(self, location, select=True):
        s = Slot(location)
        self.slot_stack.addWidget(s)
        s.locationChanged.connect(lambda _l, s=s: self._slot_location_changed(s))
        s.selectionChanged.connect(lambda s=s: s is self.slot and self.update_header())
        s.searchChanged.connect(lambda s=s: s is self.slot and self._sync_tab(s))
        for v in s.views():
            v.activateRequested.connect(self.activate)
            v.contextRequested.connect(self.show_context_menu)
            v.typeAhead.connect(self.type_ahead)
            v.zoomRequested.connect(self.zoom)
            v.dropRequested.connect(self.handle_drop)
            v.starToggled.connect(lambda item: self.toggle_star([item]))
            v.previewRequested.connect(self.preview)
            v.backRequested.connect(lambda: self.slot.go_back())
            v.verticalScrollBar().valueChanged.connect(lambda val, v=v: self._scrolled(v, val))
        s.model.cut_paths = self.app.cut_paths
        index = self.tabbar.currentIndex() + 1 if self.tabbar.count() else 0
        index = self.tabbar.insertTab(index, location_title(s.location))
        self.tabbar.setTabData(index, s)
        close = QToolButton()
        close.setFixedSize(22, 22)
        close.setFocusPolicy(Qt.NoFocus)
        close.setStyleSheet("QToolButton { border-radius: 11px; padding: 3px; }")
        set_symbolic(close, "window-close", 14)
        close.clicked.connect(lambda _=False, s=s: self.close_tab(self._index_of(s)))
        self.tabbar.setTabButton(index, QTabBar.RightSide, close)
        if select or self.tabbar.count() == 1:
            if self.tabbar.currentIndex() == index:
                self._tab_changed(index)   # insertTab already switched before the data was set
            else:
                self.tabbar.setCurrentIndex(index)
        self._update_tabbar()
        return s

    def _index_of(self, slot):
        for i in range(self.tabbar.count()):
            if self.tabbar.tabData(i) is slot:
                return i
        return -1

    def close_tab(self, index=None):
        index = self.tabbar.currentIndex() if index is None else index
        if index < 0:
            return
        if self.tabbar.count() == 1:
            self.close()
            return
        s = self.tabbar.tabData(index)
        self.closed_tabs.append((s.location, list(s.back_list), list(s.forward_list)))
        s.cancel_search(clear=False)
        self.tabbar.removeTab(index)
        self.slot_stack.removeWidget(s)
        s.deleteLater()
        self._update_tabbar()

    def restore_tab(self):
        if self.closed_tabs:
            loc, back, fwd = self.closed_tabs.pop()
            s = self.add_tab(loc if loc in VIRTUAL or os.path.isdir(loc) else platform_win.HOME)
            s.back_list, s.forward_list = back, fwd
            self.update_header()

    def _update_tabbar(self):
        self.tab_wrap.setVisible(self.tabbar.count() > 1 and not self.collapsed)

    def _tab_changed(self, index):
        s = self.tabbar.tabData(index)
        if s is None:
            return
        self.slot_stack.setCurrentWidget(s)
        s.apply_view_mode()
        self._sync_tab(s)
        s.focus_view()

    def _sync_tab(self, s):
        """Bring header + sidebar in line with the active slot."""
        if s.query or self.locbar.mode() == "search" and s is self.slot and s.query:
            self.locbar.show_search(s.query)
            self.locbar.search.set_scope("Search everywhere" if s.search_global else "Search current folder")
        elif self.locbar.mode() != "entry":
            self.locbar.show_pathbar()
        self.locbar.pathbar.set_location(s.location)
        self.sidebar.places.set_location(s.location)
        self.update_header()

    def _slot_location_changed(self, s):
        i = self._index_of(s)
        if i >= 0:
            self.tabbar.setTabText(i, location_title(s.location))
            self.tabbar.setTabToolTip(i, s.location if s.location not in VIRTUAL else location_title(s.location))
        if s is self.slot:
            self.locbar.show_pathbar()
            self._sync_tab(s)

    def _tab_menu(self, pos):
        i = self.tabbar.tabAt(pos)
        if i < 0:
            return
        m = make_menu(self)
        add_action(m, "_New Tab", self.new_tab, "Ctrl+T")
        add_action(m, "Re_store Closed Tab", self.restore_tab, "Ctrl+Shift+T", enabled=bool(self.closed_tabs))
        m.addSeparator()
        add_action(m, "Move Tab to New _Window", lambda: self._tab_to_window(i))
        add_action(m, "Move Tab _Left", lambda: self.tabbar.moveTab(i, i - 1), enabled=i > 0)
        add_action(m, "Move Tab _Right", lambda: self.tabbar.moveTab(i, i + 1), enabled=i < self.tabbar.count() - 1)
        m.addSeparator()
        add_action(m, "_Close Tab", lambda: self.close_tab(i), "Ctrl+W")
        add_action(m, "Close _Other Tabs", lambda: self._close_others(i), enabled=self.tabbar.count() > 1)
        m.exec(self.tabbar.mapToGlobal(pos))

    def _tab_to_window(self, i):
        loc = self.tabbar.tabData(i).location
        self.close_tab(i)
        self.app.new_window(loc, self)

    def _close_others(self, keep):
        keep_slot = self.tabbar.tabData(keep)
        for s in [s for s in self.slots() if s is not keep_slot]:
            self.close_tab(self._index_of(s))

    def _cycle_tab(self, d):
        n = self.tabbar.count()
        if n > 1:
            self.tabbar.setCurrentIndex((self.tabbar.currentIndex() + d) % n)

    def _move_tab(self, d):
        i = self.tabbar.currentIndex()
        j = i + d
        if 0 <= j < self.tabbar.count():
            self.tabbar.moveTab(i, j)

    def _scrolled(self, view, value):
        if view is self.slot.view:
            t = theme.current()
            self.header_line.setStyleSheet(
                f"background: {theme.rgba(t.shade if not t.dark else t.border) if value > 0 else 'transparent'};")

    # ================================================================= header state
    def update_header(self):
        s = self.slot
        if s is None:
            return
        for b in (self.back_btn, self.b_back):
            b.setEnabled(bool(s.back_list))
        for b in (self.fwd_btn, self.b_fwd):
            b.setEnabled(bool(s.forward_list))
        grid = settings["view_mode"] == "grid"
        for b in (self.view_toggle, self.b_view):
            set_symbolic(b, "view-list" if grid else "view-grid")
            b.setToolTip("View items as a list" if grid else "View items as a grid of icons")
        self.search_btn.setChecked(bool(s.query) and not s.search_global or
                                   (self.locbar.mode() == "search" and not s.search_global))
        self.sidebar.search_button.setChecked(self.locbar.mode() == "search" and s.search_global)
        title = s.title()
        self.setWindowTitle(title)

    def _theme_changed(self):
        refresh_symbolic(self)
        self.toast.restyle()
        self.locbar.search.refresh()
        self._restyle_lines()
        for s in self.slots():
            s.restyle()
        self.locbar.pathbar.set_location(self.slot.location)
        if self._frame_ready:
            dark = ctypes.c_int(1 if theme.current().dark else 0)
            _dwm.DwmSetWindowAttribute(int(self.winId()), 20, ctypes.byref(dark), 4)
        self.update()

    def _cut_changed(self):
        for s in self.slots():
            s.model.cut_paths = self.app.cut_paths
            s.model.refresh_all()

    def refresh_sidebars(self):
        for w in self.app.windows:
            w.sidebar.places.rebuild()

    # ================================================================= shortcuts
    def _shortcuts(self):
        def sc(keys, fn):
            for k in keys if isinstance(keys, (list, tuple)) else [keys]:
                s = QShortcut(QKeySequence(k), self)
                s.setContext(Qt.WindowShortcut)
                s.activated.connect(fn)

        sc("Ctrl+N", lambda: self.app.new_window(self.slot.location, self))
        sc("Ctrl+T", self.new_tab)
        sc("Ctrl+W", self.close_tab)
        sc("Ctrl+Q", lambda: [w.close() for w in list(self.app.windows)])
        sc("Ctrl+Shift+T", self.restore_tab)
        sc(["Ctrl+PgUp", "Ctrl+Shift+Tab"], lambda: self._cycle_tab(-1))
        sc(["Ctrl+PgDown", "Ctrl+Tab"], lambda: self._cycle_tab(1))
        sc("Ctrl+Shift+PgUp", lambda: self._move_tab(-1))
        sc("Ctrl+Shift+PgDown", lambda: self._move_tab(1))
        for i in range(9):
            sc(f"Alt+{i + 1}", lambda i=i: self.tabbar.setCurrentIndex(i) if i < self.tabbar.count() else None)
        sc("Ctrl+Z", self.ops.undo)
        sc(["Ctrl+Shift+Z", "Ctrl+Y"], self.ops.redo)
        sc("F9", self.toggle_sidebar)
        sc("F10", lambda: self.show_location_menu(self.locbar.pathbar.more.mapToGlobal(QPoint(17, 34))))
        sc(["Alt+Left", "Back"], lambda: self.slot.go_back())
        sc(["Alt+Right", "Forward"], lambda: self.slot.go_forward())
        sc("Alt+Up", lambda: self.slot.go_up())
        sc("Alt+Down", self._go_down)
        sc(["Alt+Home", "HomePage"], lambda: self.open_location(platform_win.HOME))
        sc("Ctrl+L", lambda: self.show_location_entry())
        sc(["Ctrl+F", "Search"], lambda: self.toggle_search(True))
        sc("Ctrl+Shift+F", lambda: self.toggle_search(True, global_=True))
        sc(["F5", "Ctrl+R", "Refresh"], lambda: self.slot.reload())
        sc("Ctrl+D", lambda: self.toggle_bookmark(self.slot.location))
        sc("Ctrl+1", lambda: self.set_view_mode("list"))
        sc("Ctrl+2", lambda: self.set_view_mode("grid"))
        sc(["Ctrl+=", "Ctrl++", "ZoomIn"], lambda: self.zoom(1))
        sc(["Ctrl+-", "ZoomOut"], lambda: self.zoom(-1))
        sc("Ctrl+0", lambda: self.zoom(0, reset=True))
        sc("Ctrl+H", self.toggle_hidden)
        sc("Ctrl+Shift+N", self.new_folder)
        sc("Ctrl+.", lambda: platform_win.open_terminal(self._current_folder() or platform_win.HOME))
        sc("Ctrl+,", self.show_preferences)
        sc(["Ctrl+?", "Ctrl+Shift+/", "Ctrl+F1"], self.show_shortcuts)
        sc("F1", lambda: QDesktopServices.openUrl(QUrl(HELP_URL)))
        # file actions (QLineEdit consumes these first when it has focus)
        sc("Delete", lambda: self.trash_or_delete(False))
        sc("Shift+Delete", lambda: self.trash_or_delete(True))
        sc("F2", self.rename_selection)
        sc(["Ctrl+C", "Ctrl+Insert"], lambda: self.copy_selection(cut=False))
        sc("Ctrl+X", lambda: self.copy_selection(cut=True))
        sc(["Ctrl+V", "Shift+Insert"], lambda: self.paste())
        sc("Ctrl+A", lambda: self.slot.select_all())
        sc("Ctrl+Shift+I", lambda: self.slot.invert_selection())
        sc(["Ctrl+I", "Alt+Return"], self.show_properties)
        sc("Ctrl+M", self.create_links)
        sc("Ctrl+S", self.select_pattern)
        sc("Ctrl+O", lambda: [self.activate(i, 0) for i in self.slot.selected_items()[:10]])
        sc("Ctrl+Alt+O", self.open_item_location)
        sc("Escape", self._escape)

    def _escape(self):
        if self.overlay_sidebar:
            self._hide_overlay_sidebar()
        elif self.locbar.mode() == "search":
            self.toggle_search(False)
        elif self.locbar.mode() == "entry":
            self._entry_cancelled()
        else:
            self.slot.selection.clearSelection()

    def _go_down(self):
        items = self.slot.selected_items()
        if len(items) == 1 and items[0].opens_as_folder:
            self.activate(items[0], 0)

    # ================================================================= navigation
    def open_location(self, location, how=0, select=None):
        if how == 1:
            s = self.add_tab(location, select=False)
            return s
        if how == 2:
            return self.app.new_window(location, self)
        self.slot.open_location(location, select=select)
        self.slot.focus_view()
        return self.slot

    def _sidebar_activate(self, path, how):
        if self.overlay_sidebar and how == 0:
            self._hide_overlay_sidebar()
        if path not in VIRTUAL and not os.path.isdir(path):
            alert(self, "Unable to Access Location", f"“{path}” is not available.")
            self.refresh_sidebars()
            return
        self.open_location(path, how)

    def activate(self, item, how=0):
        if item is None:
            return
        if item.trash:
            alert(self, f"“{item.name}” is in the Trash",
                  "Restore it from the trash to open it.", (("close", "_Close", None),))
            return
        if item.drive:
            self.open_location(item.drive.root, how)
            return
        if item.opens_as_folder:
            self.open_location(item.open_path, how)
            return
        try:
            platform_win.open_default(item.open_path)
            recent.add(item.open_path, front=True)
        except OSError as e:
            alert(self, f"Could Not Open “{item.name}”", str(e))

    def show_location_entry(self, text=None):
        loc = self.slot.location
        if text is None:
            text = loc if loc not in VIRTUAL else ""
        self.locbar.show_entry(text)
        if text in ("/", "\\", "~"):
            self.locbar.entry.deselect()
            self.locbar.entry.end(False)

    def _location_entered(self, text):
        text = text.strip().strip('"')
        if not text:
            return self._entry_cancelled()
        lowered = text.lower()
        aliases = {"recent": RECENT, "recent:///": RECENT, "starred": STARRED, "starred:///": STARRED,
                   "trash": TRASH, "trash:///": TRASH, "trash:": TRASH, "shell:recyclebinfolder": TRASH,
                   "other-locations:///": OTHER, "computer": OTHER, "this pc": OTHER}
        if lowered in aliases:
            self.locbar.show_pathbar()
            return self.open_location(aliases[lowered])
        if text.startswith("file:///"):
            text = QUrl(text).toLocalFile()
        path = os.path.expandvars(os.path.expanduser(text))
        if text in ("/", "\\"):
            path = os.path.splitdrive(self._current_folder() or platform_win.HOME)[0] + "\\"
        elif not os.path.isabs(path) and self._current_folder():
            path = os.path.join(self._current_folder(), path)
        path = os.path.normpath(path)
        self.locbar.show_pathbar()
        if os.path.isdir(path):
            self.open_location(path)
        elif os.path.exists(path):
            self.open_location(os.path.dirname(path), select=path)
        else:
            alert(self, "Unable to Find the Requested Location", f"“{text}” does not exist.")
            self.show_location_entry(text)

    def _entry_cancelled(self):
        self.locbar.show_pathbar()
        self.slot.focus_view()

    def type_ahead(self, text):
        if text in ("/", "\\", "~"):
            self.show_location_entry(text)
            return
        self.toggle_search(True, initial=text)

    def toggle_search(self, on, global_=None, initial=None):
        s = self.slot
        if on:
            if global_ is not None:
                s.search_global = global_
            elif self.locbar.mode() != "search":
                s.search_global = False
            self.locbar.search.set_scope("Search everywhere" if s.search_global else "Search current folder")
            self.locbar.show_search(initial if initial is not None else s.query)
            if global_ is not None and s.query:
                s.start_search(s.query, self.locbar.search.type_filter, s.search_global)
        else:
            self.locbar.search.edit.blockSignals(True)
            self.locbar.search.edit.clear()
            self.locbar.search.edit.blockSignals(False)
            s.cancel_search()
            self.locbar.show_pathbar()
            s.focus_view()
        self.update_header()

    def _history_menu(self, button, back):
        s = self.slot
        entries = list(reversed(s.back_list)) if back else list(reversed(s.forward_list))
        if not entries:
            return
        m = make_menu(self)
        for depth, loc in enumerate(entries[:12]):
            def go(depth=depth):
                for _ in range(depth + 1):
                    s.go_back() if back else s.go_forward()
            add_action(m, location_title(loc).replace("&", "&&").replace("_", "__"), go)
        m.exec(button.mapToGlobal(QPoint(0, button.height())))

    # ================================================================= view controls
    def toggle_view_mode(self):
        self.set_view_mode("list" if settings["view_mode"] == "grid" else "grid")

    def set_view_mode(self, mode):
        sel = [i.path for i in self.slot.selected_items()]
        settings["view_mode"] = mode
        for w in self.app.windows:
            for s in w.slots():
                s.apply_view_mode()
            w.update_header()
        if sel:
            self.slot.select_paths(sel)
        self.slot.focus_view()

    def zoom(self, delta, reset=False):
        """Ctrl+plus/minus and Ctrl+wheel: step to the next preset size."""
        mode = settings["view_mode"]
        px = icon_size(mode)
        if reset:
            px = DEFAULT_SIZE[mode]
        elif delta > 0:
            px = next((s for s in preset_sizes(mode) if s > px), px)
        elif delta < 0:
            px = next((s for s in reversed(preset_sizes(mode)) if s < px), px)
        self.set_icon_size(px)

    def set_icon_size(self, px, save=True):
        """Resizes icons in every window; save=False while the slider is still being dragged."""
        mode = settings["view_mode"]
        if save:
            settings[f"{mode}_icon_size"] = px
        for w in self.app.windows:
            for s in w.slots():
                (s.list if mode == "list" else s.grid).set_icon_size(px)

    def toggle_hidden(self):
        settings["show_hidden"] = not settings["show_hidden"]
        for w in self.app.windows:
            for s in w.slots():
                s.model.set_show_hidden(settings["show_hidden"])
                s._update_status()

    def _icon_size_row(self, menu):
        """'Icon Size' with a slider between zoom-out and zoom-in buttons, for the view menu."""
        mode = settings["view_mode"]
        sizes = preset_sizes(mode)
        row = QWidget()
        v = QVBoxLayout(row)
        v.setContentsMargins(10, 6, 10, 6)
        v.setSpacing(2)
        top = QHBoxLayout()
        top.addWidget(QLabel("Icon Size"))
        top.addStretch(1)
        value = QLabel()
        value.setProperty("dim", True)
        top.addWidget(value)
        v.addLayout(top)
        line = QHBoxLayout()
        line.setSpacing(6)
        out = header_button("zoom-out", "Smaller")
        inn = header_button("zoom-in", "Larger")
        bar = slider(sizes[0], sizes[-1], icon_size(mode))
        bar.setMinimumWidth(170)
        bar.setPageStep(max(4, (sizes[-1] - sizes[0]) // 8))
        line.addWidget(out)
        line.addWidget(bar, 1)
        line.addWidget(inn)
        v.addLayout(line)

        live = QTimer(row, singleShot=True, interval=30)      # coalesce slider moves into one relayout
        live.timeout.connect(lambda: self.set_icon_size(bar.value(), save=False))

        def changed(px):
            value.setText(f"{px} px")
            out.setEnabled(px > sizes[0])
            inn.setEnabled(px < sizes[-1])
            live.start()

        def step(delta):
            self.zoom(delta)
            bar.blockSignals(True)
            bar.setValue(icon_size(mode))
            bar.blockSignals(False)
            changed(bar.value())

        bar.valueChanged.connect(changed)
        bar.sliderReleased.connect(lambda: self.set_icon_size(bar.value()))
        bar.actionTriggered.connect(lambda _a: QTimer.singleShot(0, lambda: self.set_icon_size(bar.value())))
        out.clicked.connect(lambda: step(-1))
        inn.clicked.connect(lambda: step(1))
        changed(bar.value())
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(row)
        return wa

    def show_view_menu(self, anchor):
        s = self.slot
        m = make_menu(self)
        m.addAction(self._icon_size_row(m))
        m.addSeparator()
        if s.query:
            ids = ["relevance", "az", "za", "last-modified", "first-modified", "size", "type"]
        elif s.location == TRASH:
            ids = ["az", "za", "last-modified", "first-modified", "size", "type", "trashed"]
        elif s.location == RECENT:
            ids = ["recency", "az", "za", "last-modified", "first-modified", "size", "type"]
        else:
            ids = ["az", "za", "last-modified", "first-modified", "size", "type"]
        cur = (s.model.sort_key, s.model.sort_reverse)
        for sid, text, key_, rev in SORTS:
            if sid in ids:
                a = add_action(m, text, lambda k=key_, r=rev: s.set_sort(k, r), checkable=True,
                               checked=(key_, rev) == cur or (key_ == "rank" and cur[0] == "rank"))
        m.addSeparator()
        add_action(m, "Show _Hidden Files", self.toggle_hidden, "Ctrl+H", checkable=True,
                   checked=settings["show_hidden"])
        if settings["view_mode"] == "list" and s.location not in VIRTUAL and not s.query:
            m.addSeparator()
            add_action(m, "_Visible Columns…", self.show_columns)
        m.exec(anchor.mapToGlobal(QPoint(anchor.width() - m.sizeHint().width(), anchor.height() + 4)))

    def show_columns(self):
        current = settings.data.get("list_columns", ["size", "modified", "starred"])

        def changed(cols):
            settings["list_columns"] = cols
            for w in self.app.windows:
                for s in w.slots():
                    s.refresh_columns()

        dialogs.ColumnsDialog(self, current, changed).exec()

    # ================================================================= menus
    def show_main_menu(self):
        m = make_menu(self)
        add_action(m, "New _Window", lambda: self.app.new_window(self.slot.location, self), "Ctrl+N")
        add_action(m, "New _Tab", self.new_tab, "Ctrl+T")
        m.addSeparator()
        u, r = self.ops.undo_label(), self.ops.redo_label()
        add_action(m, f"_Undo {u}" if u else "_Undo", self.ops.undo, "Ctrl+Z", enabled=bool(u))
        add_action(m, f"_Redo {r}" if r else "_Redo", self.ops.redo, "Ctrl+Shift+Z", enabled=bool(r))
        m.addSeparator()
        add_action(m, "_Preferences", self.show_preferences, "Ctrl+,")
        add_action(m, "_Keyboard Shortcuts", self.show_shortcuts, "Ctrl+?")
        add_action(m, "_Help", lambda: QDesktopServices.openUrl(QUrl(HELP_URL)), "F1")
        add_action(m, "_About Files", lambda: dialogs.AboutDialog(self, f"Version {self.app_version()}").exec())
        b = self.sidebar.menu_button
        m.exec(b.mapToGlobal(QPoint(max(-b.x() + 6, b.width() - m.sizeHint().width()), b.height() + 4)))

    @staticmethod
    def app_version():
        from .app import VERSION
        return VERSION

    def _current_folder(self):
        loc = self.slot.location
        return None if loc in VIRTUAL else loc

    def _new_menu_section(self, m):
        folder = self._current_folder()
        add_action(m, "New _Folder…", self.new_folder, "Ctrl+Shift+N", enabled=bool(folder))
        doc = add_submenu(m, "New _Document")
        doc.setEnabled(bool(folder))
        add_action(doc, "Empty Text File", lambda: self.new_document(".txt"))
        add_action(doc, "Empty File", lambda: self.new_document(""))
        add_action(m, "Open in Consol_e", lambda: platform_win.open_terminal(folder), "Ctrl+.", enabled=bool(folder))

    def show_background_menu(self, pos):
        s = self.slot
        m = make_menu(self)
        if s.location not in VIRTUAL:
            self._new_menu_section(m)
            m.addSeparator()
            add_action(m, "_Paste", self.paste, "Ctrl+V", enabled=self.app.clipboard_has_files())
        add_action(m, "Select _All", s.select_all, "Ctrl+A")
        if s.location == TRASH:
            m.addSeparator()
            add_action(m, "Empty _Trash…", self.empty_trash, enabled=s.model.rowCount() > 0)
        m.addSeparator()
        add_action(m, "P_roperties", lambda: self.show_properties(background=True), "Ctrl+I",
                   enabled=s.location not in (RECENT, STARRED))
        m.exec(pos)

    def show_location_menu(self, pos):
        s = self.slot
        loc = s.location
        m = make_menu(self)
        if loc not in VIRTUAL:
            self._new_menu_section(m)
            m.addSeparator()
        add_action(m, "R_eload", s.reload, "F5")
        if loc not in VIRTUAL:
            if loc in bookmarks:
                add_action(m, "Remove from _Bookmarks", lambda: self.toggle_bookmark(loc), "Ctrl+D")
            else:
                add_action(m, "Add to _Bookmarks", lambda: self.toggle_bookmark(loc), "Ctrl+D")
            if loc in starred:
                add_action(m, "_Unstar Folder", lambda: self._star_paths([loc], False))
            else:
                add_action(m, "_Star Folder", lambda: self._star_paths([loc], True))
            add_action(m, "_Copy Location", lambda: QGuiApplication.clipboard().setText(loc))
            add_action(m, "_Open in File Explorer", lambda: platform_win.open_in_explorer(loc))
            m.addSeparator()
            add_action(m, "_Paste", self.paste, "Ctrl+V", enabled=self.app.clipboard_has_files())
        add_action(m, "Select _All", s.select_all, "Ctrl+A")
        if loc == TRASH:
            m.addSeparator()
            add_action(m, "Empty _Trash…", self.empty_trash, enabled=s.model.rowCount() > 0)
        if loc not in (RECENT, STARRED, TRASH):
            m.addSeparator()
            add_action(m, "P_roperties", lambda: self.show_properties(background=True))
        m.exec(pos)

    def _crumb_menu(self, path, pos):
        if path == self.slot.location:
            return self.show_location_menu(pos)
        m = make_menu(self)
        add_action(m, "Open in New _Window", lambda: self.open_location(path, 2))
        add_action(m, "Open in New _Tab", lambda: self.open_location(path, 1))
        if path not in VIRTUAL:
            m.addSeparator()
            add_action(m, "_Properties", lambda: self._properties_for([model.make_item(path)]))
        m.exec(pos)

    def show_context_menu(self, pos, on_item):
        s = self.slot
        items = s.selected_items()
        if not on_item or not items:
            return self.show_background_menu(pos)
        m = make_menu(self)
        one = items[0] if len(items) == 1 else None
        loc = s.location
        if loc == TRASH:
            add_action(m, "Restore From Tras_h", self.restore_selection)
            add_action(m, "_Delete From Trash…", lambda: self.trash_or_delete(True), "Delete")
            m.addSeparator()
            add_action(m, "P_roperties", self.show_properties, "Ctrl+I")
            return m.exec(pos)
        if loc == OTHER:
            add_action(m, "_Open", lambda: self.activate(one, 0), "Return", enabled=bool(one))
            add_action(m, "Open in New _Tab", lambda: self.activate(one, 1), "Ctrl+Return", enabled=bool(one))
            add_action(m, "Open in New _Window", lambda: self.activate(one, 2), "Shift+Return", enabled=bool(one))
            if one and one.drive and one.drive.kind in (platform_win.DRIVE_REMOVABLE, platform_win.DRIVE_CDROM):
                add_action(m, "_Eject", lambda: platform_win.eject_drive(one.drive))
            m.addSeparator()
            add_action(m, "P_roperties", self.show_properties, "Ctrl+I")
            return m.exec(pos)

        all_folders = all(i.opens_as_folder for i in items)
        if all_folders:
            add_action(m, "_Open", lambda: [self.activate(i, 0) for i in items[:1]], "Return")
            add_action(m, "Open in New _Tab", lambda: [self.activate(i, 1) for i in items], "Ctrl+Return")
            add_action(m, "Open in New _Window", lambda: [self.activate(i, 2) for i in items], "Shift+Return")
        else:
            if one and operations.is_extractable(one.path):
                add_action(m, "_Extract", lambda: self.extract(one, here=True))
                add_action(m, "E_xtract to…", lambda: self.extract(one, here=False))
                m.addSeparator()
            add_action(m, "_Open", lambda: [self.activate(i, 0) for i in items[:10]], "Return")
        m.addSeparator()
        if not all_folders and one:
            add_action(m, "Open _With…", lambda: platform_win.open_with_dialog(one.open_path))
        if all_folders and one:
            add_action(m, "Open in Consol_e", lambda: platform_win.open_terminal(one.open_path))
        if loc in (RECENT, STARRED) or s.query:
            add_action(m, "_Open Item Location", self.open_item_location, "Ctrl+Alt+O", enabled=bool(one))
        m.addSeparator()
        add_action(m, "Cu_t", lambda: self.copy_selection(True), "Ctrl+X")
        add_action(m, "_Copy", lambda: self.copy_selection(False), "Ctrl+C")
        add_action(m, "Move to…", lambda: self.transfer_to(move=True))
        add_action(m, "Copy to…", lambda: self.transfer_to(move=False))
        m.addSeparator()
        add_action(m, "Rena_me…", self.rename_selection, "F2", enabled=bool(one))
        if one and one.opens_as_folder and self.app.clipboard_has_files():
            add_action(m, "_Paste Into Folder", lambda: self.paste(one.open_path))
        if settings["show_create_link"]:
            add_action(m, "Create _Link", self.create_links, "Ctrl+M")
        add_action(m, "C_ompress…", self.compress)
        if one and not one.is_dir and one.category == "image":
            add_action(m, "Set as Background…", lambda: self.set_background(one))
        if one and not one.is_dir:
            add_action(m, "_Copy Address", lambda: QGuiApplication.clipboard().setText(one.path))
        m.addSeparator()
        add_action(m, "Mo_ve to Trash", lambda: self.trash_or_delete(False), "Delete")
        if settings["show_delete_permanently"]:
            add_action(m, "_Delete Permanently…", lambda: self.trash_or_delete(True), "Shift+Delete")
        if loc == RECENT:
            add_action(m, "_Remove From Recent", self.remove_from_recent)
        if all(i.starred for i in items):
            add_action(m, "Unstar", lambda: self.toggle_star(items))
        else:
            add_action(m, "Star", lambda: self.toggle_star(items))
        m.addSeparator()
        add_action(m, "P_roperties", self.show_properties, "Ctrl+I")
        m.exec(pos)

    def _sidebar_menu(self, kind, path, pos):
        m = make_menu(self)
        add_action(m, "_Open", lambda: self._sidebar_activate(path, 0))
        add_action(m, "Open in New _Tab", lambda: self._sidebar_activate(path, 1))
        add_action(m, "Open in New _Window", lambda: self._sidebar_activate(path, 2))
        if path == TRASH:
            m.addSeparator()
            add_action(m, "Empty _Trash…", self.empty_trash, enabled=not platform_win.trash_is_empty())
        if kind == "bookmark":
            m.addSeparator()
            add_action(m, "Rename…", lambda: self._rename_bookmark(path))
            add_action(m, "_Remove from Bookmarks", lambda: self.toggle_bookmark(path))
        if kind == "drive":
            for d in platform_win.list_drives():
                if d.root == path and d.kind in (platform_win.DRIVE_REMOVABLE, platform_win.DRIVE_CDROM):
                    m.addSeparator()
                    add_action(m, "_Eject", lambda d=d: platform_win.eject_drive(d))
        if path not in VIRTUAL:
            m.addSeparator()
            add_action(m, "P_roperties", lambda: self._properties_for([self._item_for_path(path)]))
        m.exec(pos)

    def _item_for_path(self, path):
        it = model.make_item(path)
        for d in platform_win.list_drives():
            if os.path.normcase(d.root) == os.path.normcase(path):
                it.drive = d
                it.name = d.display_name
        return it

    # ================================================================= file actions
    def _run(self, op):
        op.origin = self
        return self.ops.run(op)

    def _toast(self, text, undo=False):
        self.toast.show_toast(text, "Undo" if undo else None, self.ops.undo if undo else None)

    def new_folder(self):
        folder = self._current_folder()
        if not folder:
            return
        name = dialogs.NameDialog(self, folder, "New Folder", "Folder Name", "C_reate", "", True).value()
        if not name:
            return
        path = os.path.join(folder, name)
        try:
            os.mkdir(path)
        except OSError as e:
            return alert(self, "Unable to Create Folder", str(e))
        self.ops.push({"kind": "create", "path": path})
        self._select_when_loaded(path)

    def new_document(self, ext):
        folder = self._current_folder()
        if not folder:
            return
        default = f"New Document{ext}"
        n = 2
        while os.path.exists(os.path.join(folder, default)):
            default = f"New Document {n}{ext}"
            n += 1
        name = dialogs.NameDialog(self, folder, "New File", "File Name", "C_reate", default, False).value()
        if not name:
            return
        path = os.path.join(folder, name)
        try:
            open(path, "x").close()
        except OSError as e:
            return alert(self, "Unable to Create File", str(e))
        self.ops.push({"kind": "create", "path": path})
        self._select_when_loaded(path)

    def _select_when_loaded(self, path, attempts=12):
        s = self.slot
        if s.model.row_for_path(path) >= 0:
            s.select_paths([path])
            s.focus_view()
        elif attempts:
            if attempts == 12:
                s.reload(soft=True)
            QTimer.singleShot(120, lambda: self._select_when_loaded(path, attempts - 1))

    def rename_selection(self):
        s = self.slot
        items = s.selected_items()
        if len(items) != 1 or s.location in (TRASH, OTHER) or items[0].drive:
            return
        item = items[0]
        v = s.view
        idx = s.model.index(s.model.row_for_path(item.path), 0)
        rect = v.visualRect(idx)
        anchor = v.viewport().mapToGlobal(QPoint(rect.center().x(), rect.bottom()))
        if s.view is s.list:
            anchor = v.viewport().mapToGlobal(QPoint(min(rect.left() + 180, v.viewport().width() // 2), rect.bottom()))
        dialogs.RenamePopover(self, item, self._do_rename).popup_at(anchor)

    def _do_rename(self, item, new_name):
        new = os.path.join(os.path.dirname(item.path), new_name)
        try:
            if os.path.normcase(new) == os.path.normcase(item.path):
                tmp = item.path + ".~rename"
                os.rename(item.path, tmp)
                os.rename(tmp, new)
            else:
                os.rename(item.path, new)
        except OSError as e:
            return alert(self, f"Could Not Rename “{item.name}”", str(e))
        for lst in (starred, recent):
            if item.path in lst:
                lst.rename(item.path, new)
        folder_colors.rename(item.path, new)
        if item.path in bookmarks:
            bookmarks.remove(item.path)
            bookmarks.add(new)
            self.refresh_sidebars()
        self.ops.push({"kind": "rename", "old": item.path, "new": new})
        self._select_when_loaded(new)

    def copy_selection(self, cut):
        items = self.slot.selected_items()
        if not items or self.slot.location in (TRASH, OTHER):
            return
        self.app.set_clipboard([i.path for i in items], cut)

    def paste(self, target=None):
        target = target or self._current_folder()
        if not target:
            if self.slot.location in VIRTUAL:
                alert(self, "Could Not Paste Files",
                      f"Cannot paste files into {location_title(self.slot.location)}.")
            return
        paths, cut = self.app.read_clipboard()
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            return
        self.transfer(paths, target, move=cut)
        if cut:
            QGuiApplication.clipboard().clear()

    def transfer_to(self, move):
        items = self.slot.selected_items()
        if not items:
            return
        title = "Select Move Destination" if move else "Select Copy Destination"
        start = self._current_folder() or platform_win.HOME
        dest = QFileDialog.getExistingDirectory(self, title, start)
        if dest:
            self.transfer([i.path for i in items], os.path.normpath(dest), move)

    def transfer(self, paths, dest, move):
        pairs = self.resolve_conflicts(paths, dest, copy=not move)
        if pairs:
            op = self._run(operations.move_job(pairs) if move else operations.copy_job(pairs))
            op.select_after = [d for _, d in pairs]

    def resolve_conflicts(self, paths, dest, copy):
        pairs = []
        policy = None
        nd = os.path.normcase(os.path.normpath(dest))
        for i, src in enumerate(paths):
            ns = os.path.normcase(os.path.normpath(src))
            if nd == ns or nd.startswith(ns + os.sep):
                alert(self, "You Cannot Copy a Folder Into Itself" if copy else "You Cannot Move a Folder Into Itself",
                      "The destination folder is inside the source folder.")
                return []
            name = os.path.basename(src.rstrip("\\/"))
            dst = os.path.join(dest, name)
            if os.path.normcase(os.path.dirname(src)) == nd:
                if copy:
                    pairs.append((src, copy_name(dest, name)))
                continue  # moving into the same folder does nothing
            if os.path.lexists(dst):
                choice = policy
                if choice is None:
                    choice, apply_all = dialogs.ask_conflict(self, src, dst, len(paths) - i)
                    if choice == "cancel":
                        return []
                    if apply_all:
                        policy = choice
                if choice == "skip":
                    continue
                if choice == "rename":
                    dst = platform_win.unique_name(dest, name)
            pairs.append((src, dst))
        return pairs

    def handle_drop(self, paths, target, action):
        paths = [os.path.normpath(p) for p in paths]
        if target == TRASH:
            return self._trash(paths)
        if target in VIRTUAL:
            return
        if action == Qt.LinkAction:
            return self._make_links(paths, target)
        self.transfer(paths, target, move=action == Qt.MoveAction)

    def trash_or_delete(self, permanently):
        s = self.slot
        items = s.selected_items()
        if not items or s.location == OTHER:
            return
        if s.location == TRASH:
            names = [i.name for i in items]
            heading = (f"Permanently Delete “{names[0]}”?" if len(items) == 1
                       else f"Permanently Delete {len(items)} Selected Items?")
            r = alert(self, heading, "If you delete an item, it will be permanently lost.",
                      (("cancel", "_Cancel", None), ("delete", "_Delete", "destructive")), default="cancel")
            if r == "delete":
                self._run(operations.delete_from_trash_job([i.path for i in items], names))
            return
        paths = [i.path for i in items]
        if permanently:
            heading = (f"Permanently Delete “{items[0].name}”?" if len(items) == 1
                       else f"Permanently Delete {len(items)} Selected Items?")
            r = alert(self, heading, "If you delete an item, it will be permanently lost.",
                      (("cancel", "_Cancel", None), ("delete", "_Delete", "destructive")), default="cancel")
            if r == "delete":
                op = self._run(operations.delete_job(paths))
                op.toast = (f"“{items[0].name}” deleted" if len(items) == 1 else f"{len(items)} files deleted", False)
            return
        self._trash(paths)

    def _trash(self, paths):
        op = self._run(operations.trash_job(paths))
        name = os.path.basename(paths[0])
        op.toast = (f"“{name}” moved to trash" if len(paths) == 1 else f"{len(paths)} files moved to trash", True)

    def restore_selection(self):
        items = self.slot.selected_items()
        entries = [(i.trash.trash_path, i.trash.original) for i in items if i.trash]
        if entries:
            op = self._run(operations.restore_job(entries))
            op.toast = (f"“{items[0].name}” restored" if len(items) == 1 else f"{len(items)} files restored", True)

    def empty_trash(self):
        r = alert(self, "Empty all Items From Trash?", "All items in the Trash will be permanently deleted.",
                  (("cancel", "_Cancel", None), ("empty", "_Empty Trash", "destructive")), default="cancel")
        if r == "empty":
            self._run(operations.empty_trash_job())

    def _job_finished(self, op):
        # refresh views showing affected places; the watcher covers most folders
        for s in self.slots():
            if s.location in (TRASH, RECENT, STARRED) or op.kind in ("restore",):
                s.reload(soft=True)
            elif op.kind in ("trash", "delete", "move", "copy", "compress", "extract"):
                QTimer.singleShot(150, lambda s=s: s.reload(soft=True) if not s.query else None)
        self.sidebar.places.trash_empty = platform_win.trash_is_empty()
        self.sidebar.places.viewport().update()
        if getattr(op, "origin", None) is not self or op.error is not None:
            return
        toast = getattr(op, "toast", None)
        if toast:
            self._toast(*toast)
        pending = getattr(op, "select_after", None)
        if pending:
            QTimer.singleShot(250, lambda: self.slot.select_paths(pending))
        if op.kind in ("compress", "extract") and op.result:
            self._select_when_loaded(op.result["path"])

    def create_links(self):
        items = self.slot.selected_items()
        folder = self._current_folder()
        if items and folder:
            self._make_links([i.path for i in items], folder)

    def _make_links(self, paths, folder):
        from PySide6.QtCore import QFile
        made = None
        for p in paths:
            name = os.path.basename(p.rstrip("\\/"))
            link = os.path.join(folder, f"Link to {name}.lnk")
            n = 2
            while os.path.lexists(link):
                link = os.path.join(folder, f"Link to {name} ({n}).lnk")
                n += 1
            if QFile.link(p, link):
                self.ops.push({"kind": "create", "path": link})
                made = link
        if made:
            self._select_when_loaded(made)

    def compress(self):
        items = self.slot.selected_items()
        folder = self._current_folder()
        if not items:
            return
        if not folder:
            folder = os.path.dirname(items[0].path)
        default = os.path.splitext(items[0].name)[0] if len(items) == 1 and not items[0].is_dir else (
            items[0].name if len(items) == 1 else "Archive")
        archive = dialogs.CompressDialog(self, folder, default).value()
        if archive:
            self._run(operations.compress_job([i.path for i in items], archive))

    def extract(self, item, here=True):
        dest = os.path.dirname(item.path)
        if not here:
            dest = QFileDialog.getExistingDirectory(self, "Select Extract Destination", dest)
            if not dest:
                return
        self._run(operations.extract_job(item.path, os.path.normpath(dest)))

    def set_background(self, item):
        if not platform_win.set_wallpaper(item.path):
            alert(self, "Could Not Set Background", "The image could not be set as the desktop background.")
        else:
            self._toast("Background changed")

    def toggle_star(self, items):
        star = not all(i.starred for i in items)
        self._star_paths([i.path for i in items], star)

    def _star_paths(self, paths, star):
        for p in paths:
            starred.add(p) if star else starred.remove(p)
        for w in self.app.windows:
            for s in w.slots():
                if s.location == STARRED:
                    s.reload(soft=True)
                else:
                    s.model.refresh_all()

    def remove_from_recent(self):
        for i in self.slot.selected_items():
            recent.remove(i.path)
        self.slot.reload(soft=True)

    def open_item_location(self):
        items = self.slot.selected_items()
        if len(items) == 1:
            it = items[0]
            self.open_location(it.parent, select=it.path)

    def select_pattern(self):
        pat = dialogs.SelectPatternDialog(self).value()
        if pat:
            s = self.slot
            s.select_paths([i.path for i in s.model.items if dialogs.matches(pat, i.name)])

    def toggle_bookmark(self, path):
        if path in VIRTUAL:
            return
        if path in bookmarks:
            bookmarks.remove(path)
        else:
            bookmarks.add(path)
        self.refresh_sidebars()

    def add_bookmark(self, path):
        if os.path.isdir(path) and path not in bookmarks:
            bookmarks.add(path)
            self.refresh_sidebars()

    def _rename_bookmark(self, path):
        name = dialogs.NameDialog(self, "", "Rename Bookmark", "Name", "_Rename",
                                  os.path.basename(path.rstrip("\\/")), True)
        name.entry.textChanged.disconnect()
        name.ok.setEnabled(True)
        value = name.value()
        if value is not None:
            bookmarks.set_label(path, value)
            self.refresh_sidebars()

    def show_properties(self, background=False):
        s = self.slot
        items = [] if background else s.selected_items()
        if not items:
            if s.location in (RECENT, STARRED):
                return
            if s.location == TRASH:
                n = s.model.rowCount()
                return alert(self, "Trash", f"{n} item{'s' if n != 1 else ''} in the trash.")
            if s.location == OTHER:
                return
            items = [self._item_for_path(s.location)]
        self._properties_for(items)

    def apply_folder_color(self, paths, color):
        """color: "#rrggbb", or "" for the default (Properties → Appearance)."""
        for p in paths:
            folder_colors.set(p, color)
        for s in self.app.all_slots():
            s.model.refresh_all()

    def _properties_for(self, items):
        dialogs.PropertiesDialog(self, items).exec()

    def preview(self):
        """Quick Look the item under the pointer, or else the selection."""
        s = self.slot
        selected = s.selected_items()
        start = s.view.hovered_item()
        if len(selected) > 1 and (start is None or any(it is start for it in selected)):
            index = next((i for i, it in enumerate(selected) if it is start), 0)
            # walk the selection without disturbing it
            quicklook.QuickLook(self, selected, index, source=s.view.item_icon_rect).exec()
            return
        start = start or (selected[0] if selected else None)
        if start is None:
            return
        items = list(s.model.items)
        index = next((i for i, it in enumerate(items) if it is start), None)
        if index is None:
            items, index = [start], 0
        quicklook.QuickLook(self, items, index, lambda it: s.select_paths([it.path]),
                            source=s.view.item_icon_rect).exec()

    def eventFilter(self, obj, e):
        # Space over a file previews it wherever keyboard focus is, unless typing into a field.
        if (e.type() == QEvent.KeyPress and e.key() == Qt.Key_Space and not e.modifiers() & ~Qt.KeypadModifier
                and isinstance(obj, QWidget) and obj.window() is self
                and not isinstance(QApplication.focusWidget(), (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox))):
            s = self.slot
            if s is not None and s.view.hovered_item() is not None:
                if not e.isAutoRepeat():
                    self.preview()
                return True
        return super().eventFilter(obj, e)

    def show_preferences(self):
        def changed(key):
            if key in theme.THEME_KEYS:
                self.app.apply_theme()
            for w in self.app.windows:
                for s in w.slots():
                    if key == "folders_first":
                        s.model.resort()
                    elif key in ("thumbnails", "count_folder_items"):
                        s.reload(soft=True)
                        s.model.refresh_all()
                    elif key == "click_policy":
                        s.apply_click_policy()

        dialogs.PreferencesDialog(self, changed).exec()

    def show_shortcuts(self):
        dialogs.ShortcutsDialog(self).exec()
