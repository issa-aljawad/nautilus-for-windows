"""Small libadwaita look-alikes: header buttons, switch, spinner, status page,
toast, popover, floating dialogs and rounded menus."""

from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer,
                            QVariantAnimation, Signal, Property)
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QApplication, QDialog, QFrame, QGraphicsDropShadowEffect,
                               QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from . import icons, platform_win, theme


def mnemonic(label):
    """GTK '_' mnemonics -> Qt '&' mnemonics."""
    return label.replace("&", "&&").replace("_", "&")


def plain(label):
    return label.replace("_", "")


def set_symbolic(button, name, size=16):
    button.setProperty("symbolic", name)
    button.setProperty("symbolicSize", size)
    button.setIcon(icons.symbolic(name, theme.current().icon_fg, size))
    button.setIconSize(QSize(size, size))


def refresh_symbolic(root):
    t = theme.current()
    for b in root.findChildren(QAbstractButton):
        name = b.property("symbolic")
        if name:
            size = b.property("symbolicSize") or 16
            color = QColor("white") if b.property("osd") else t.accent_fg if b.property("onAccent") else t.icon_fg
            b.setIcon(icons.symbolic(name, color, size))


def header_button(name, tooltip="", checkable=False, parent=None):
    b = QToolButton(parent)
    set_symbolic(b, name)
    b.setToolTip(tooltip)
    b.setCheckable(checkable)
    b.setFixedSize(34, 34)
    b.setFocusPolicy(Qt.NoFocus)
    b.setCursor(Qt.ArrowCursor)
    return b


def label(text="", *, dim=False, heading=False, title1=False, title2=False, caption=False,
          wrap=False, center=False, selectable=False):
    l = QLabel(text)
    for prop, on in (("dim", dim), ("heading", heading), ("title1", title1), ("title2", title2),
                     ("caption", caption)):
        if on:
            l.setProperty(prop, True)
    l.setWordWrap(wrap)
    if center:
        l.setAlignment(Qt.AlignCenter)
    if selectable:
        l.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return l


def button(text, *, suggested=False, destructive=False, flat=False, pill=False):
    b = QPushButton(mnemonic(text) if "_" in text else text)
    for prop, on in (("suggested", suggested), ("destructive", destructive), ("flat", flat), ("pill", pill)):
        if on:
            b.setProperty(prop, True)
    b.setCursor(Qt.PointingHandCursor)
    return b


def slider(minimum, maximum, value):
    """An Adwaita-style horizontal slider: accent fill and a round white knob."""
    from PySide6.QtWidgets import QSlider
    s = QSlider(Qt.Horizontal)
    s.setRange(minimum, maximum)
    s.setValue(value)
    s.setFocusPolicy(Qt.NoFocus)
    t = theme.current()
    s.setStyleSheet(
        f"QSlider::groove:horizontal {{ height: 4px; background: {theme.rgba(t.fg_alpha(0.15))}; border-radius: 2px; }}"
        f"QSlider::sub-page:horizontal {{ background: {theme.rgba(t.accent_bg)}; border-radius: 2px; }}"
        "QSlider::handle:horizontal { background: white; width: 14px; margin: -5px 0; border-radius: 7px;"
        " border: 1px solid rgba(0,0,0,0.18); }")
    return s


def hsep():
    f = QFrame()
    f.setObjectName("HSep")
    f.setFixedHeight(1)
    return f


# ------------------------------------------------------------------ menus

def make_menu(parent=None, title=""):
    m = QMenu(title, parent)
    m.setWindowFlags(m.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    m.setAttribute(Qt.WA_TranslucentBackground)
    m.setSeparatorsCollapsible(True)
    return m


def add_action(menu, text, slot=None, shortcut=None, *, checkable=False, checked=False, enabled=True):
    a = QAction(mnemonic(text), menu)
    if shortcut:
        a.setShortcut(QKeySequence(shortcut))
        a.setShortcutVisibleInContextMenu(True)
    a.setCheckable(checkable)
    if checkable:
        a.setChecked(checked)
    a.setEnabled(enabled)
    if slot:
        a.triggered.connect(lambda _=False: slot())
    menu.addAction(a)
    return a


def add_submenu(menu, text):
    sub = make_menu(menu, mnemonic(text))
    menu.addMenu(sub)
    return sub


# ------------------------------------------------------------------ switch

class Switch(QAbstractButton):
    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(48, 26)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get(self):
        return self._pos

    def _set(self, v):
        self._pos = v
        self.update()

    knob = Property(float, _get, _set)

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        off = t.fg_alpha(0.15)
        on = t.accent_bg
        c = QColor(int(off.red() + (on.red() - off.red()) * self._pos),
                   int(off.green() + (on.green() - off.green()) * self._pos),
                   int(off.blue() + (on.blue() - off.blue()) * self._pos),
                   int(off.alpha() + (255 - off.alpha()) * self._pos))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(0, 0, 48, 26), 13, 13)
        x = 3 + self._pos * 22
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(QRectF(x, 4, 20, 20))
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x, 3, 20, 20))


# ------------------------------------------------------------------ spinner

class Spinner(QWidget):
    def __init__(self, size=16, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def _tick(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = max(2.0, self.width() / 8)
        r = QRectF(w / 2, w / 2, self.width() - w, self.height() - w)
        pen = QPen(theme.current().fg_alpha(0.15), w)
        p.setPen(pen)
        p.drawEllipse(r)
        pen.setColor(theme.current().fg_alpha(0.8))
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(r, -self._angle * 16, 100 * 16)


# ------------------------------------------------------------------ status page

class StatusPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(12)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignCenter)
        self.spinner = Spinner(32)
        self.title = label("", title1=True, center=True)
        self.desc = label("", center=True, wrap=True)
        self.desc.setMaximumWidth(420)
        lay.addWidget(self.icon, 0, Qt.AlignCenter)
        lay.addWidget(self.spinner, 0, Qt.AlignCenter)
        lay.addSpacing(12)
        lay.addWidget(self.title, 0, Qt.AlignCenter)
        lay.addWidget(self.desc, 0, Qt.AlignCenter)
        self._icon_name = None

    def show_status(self, icon_name, title, description="", spinner=False):
        self._icon_name = icon_name
        self.spinner.setVisible(spinner)
        self.icon.setVisible(not spinner and bool(icon_name))
        self.refresh()
        self.title.setText(title)
        self.desc.setText(description)
        self.desc.setVisible(bool(description))
        self.title.setProperty("title1", not spinner)
        self.title.setProperty("title2", spinner)
        self.title.style().unpolish(self.title)
        self.title.style().polish(self.title)

    def refresh(self):
        if self._icon_name:
            self.icon.setPixmap(icons.symbolic_pixmap(self._icon_name, theme.current().fg_alpha(0.5), 128))


# ------------------------------------------------------------------ toast

class Toast(QFrame):
    """Adw.Toast: a pill at the bottom of the view with an optional action."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 6, 6, 6)
        lay.setSpacing(6)
        self.text = QLabel()
        self.text.setStyleSheet("color: white;")
        self.action = QPushButton()
        self.action.setCursor(Qt.PointingHandCursor)
        self.close_btn = QToolButton()
        self.close_btn.setProperty("osd", True)
        self.close_btn.setStyleSheet("QToolButton { background: transparent; border-radius: 15px; padding: 6px; }"
                                     "QToolButton:hover { background: rgba(255,255,255,0.15); }")
        set_symbolic(self.close_btn, "window-close")
        self.close_btn.setIcon(icons.symbolic("window-close", QColor("white")))
        lay.addWidget(self.text)
        lay.addSpacing(6)
        lay.addWidget(self.action)
        lay.addWidget(self.close_btn)
        self.close_btn.clicked.connect(self.dismiss)
        self.action.clicked.connect(self._act)
        self._cb = None
        self._timer = QTimer(self, singleShot=True, timeout=self.dismiss)
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(180)
        self._anim.finished.connect(self._anim_done)
        self.hide()
        self.restyle()

    def restyle(self):
        t = theme.current()
        self.setStyleSheet(f"Toast {{ background: {theme.rgba(t.toast_bg)}; border-radius: 22px; }}")
        self.action.setStyleSheet("QPushButton { background: rgba(255,255,255,0.12); color: white; border-radius: 15px;"
                                  " padding: 5px 14px; font-weight: 700; }"
                                  "QPushButton:hover { background: rgba(255,255,255,0.2); }")

    def show_toast(self, text, action_label=None, callback=None, timeout=5000):
        self.text.setText(text)
        self._cb = callback
        self.action.setVisible(bool(action_label))
        self.action.setText(action_label or "")
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity() if self.isVisible() else 0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(timeout)

    def reposition(self):
        p = self.parentWidget()
        self.adjustSize()
        w = min(self.sizeHint().width(), p.width() - 24)
        self.setGeometry((p.width() - w) // 2, p.height() - self.sizeHint().height() - 24, w, self.sizeHint().height())

    def _act(self):
        cb = self._cb
        self.dismiss()
        if cb:
            cb()

    def dismiss(self):
        self._timer.stop()
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _anim_done(self):
        if self._fx.opacity() < 0.01:
            self.hide()


# ------------------------------------------------------------------ floating cards

class _Card(QFrame):
    def __init__(self, parent, radius=14, bg=None):
        super().__init__(parent)
        self.radius = radius
        self.bg = bg
        fx = QGraphicsDropShadowEffect(self)
        fx.setBlurRadius(32)
        fx.setOffset(0, 4)
        fx.setColor(QColor(0, 0, 0, 90))
        self.setGraphicsEffect(fx)

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(t.border if t.dark else QColor(0, 0, 6, 30), 1))
        p.setBrush(self.bg() if callable(self.bg) else (self.bg or t.dialog_bg))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.radius)


class Popover(QWidget):
    """Frameless popup with a rounded card, closes on outside click."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 20)
        self.card = _Card(self, 12, lambda: theme.current().popover_bg)
        outer.addWidget(self.card)
        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(12, 12, 12, 12)
        self.body.setSpacing(8)

    def popup_at(self, global_point, center=True):
        self.adjustSize()
        x = global_point.x() - (self.width() // 2 if center else 16)
        y = global_point.y() - 12
        screen = QApplication.screenAt(global_point) or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = max(geo.left(), min(x, geo.right() - self.width()))
        if y + self.height() > geo.bottom():
            y = global_point.y() - self.height() - 24
        self.move(x, y)
        self.show()


class _Ghost(QWidget):
    """Click-through top-level that paints window snapshots while they fly and fade.

    Animating a snapshot instead of the window itself keeps heavy content (PDF
    pages, tables, video) from being laid out again on every frame. One instance
    is reused because creating a native window takes long enough to miss frames."""

    painted = Signal()
    _shared = None

    @classmethod
    def play(cls, layers):
        """layers: [(pixmap, global rect from, global rect to, opacity from, opacity to)]"""
        if cls._shared is None:
            cls._shared = cls()
        g = cls._shared
        g.layers, g.t, g._fresh = layers, 0.0, True
        area = QRect()
        for _pm, a, b, _o1, _o2 in layers:
            area = area.united(a).united(b)
        g.setGeometry(area)
        g.show()
        g.repaint()     # paint now, so the window can hide behind it without a blank frame
        return g

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.NoDropShadowWindowHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.layers, self.t, self._fresh = [], 0.0, False

    def paintEvent(self, _e):
        self._paint()
        if self._fresh:     # on screen now: let the clock start
            self._fresh = False
            QTimer.singleShot(0, self.painted.emit)

    def _paint(self):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        t, off = self.t, self.pos()

        def lerp(a, b):
            return a + (b - a) * t

        for pm, a, b, o1, o2 in self.layers:
            p.setOpacity(lerp(o1, o2))
            p.drawPixmap(QRectF(lerp(a.x(), b.x()) - off.x(), lerp(a.y(), b.y()) - off.y(),
                                lerp(a.width(), b.width()), lerp(a.height(), b.height())), pm, QRectF(pm.rect()))


def _animate(owner, duration, easing, step, finished):
    """A 0 -> 1 animation; the caller starts it."""
    anim = QVariantAnimation(owner)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(duration)
    anim.setEasingCurve(easing)
    anim.valueChanged.connect(step)
    anim.finished.connect(finished)
    return anim


class Sheet(QDialog):
    """AdwDialog look-alike: a floating rounded card over a dimmed window.

    It scales and fades in and out like AdwDialog, or zooms out of and back into
    zoom_rect() when a subclass provides one."""

    def __init__(self, parent, title="", width=420, header=True):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self._width = width
        self._anim = None
        self._anim_done = None
        self._closing = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 32)
        self.card = _Card(self, 14)
        outer.addWidget(self.card)
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = QWidget()
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(10, 8, 10, 4)
        self.header_start = QHBoxLayout()
        hl.addLayout(self.header_start)
        self.title_label = label(title, heading=True, center=True)
        hl.addWidget(self.title_label, 1)
        self.header_end = QHBoxLayout()
        hl.addLayout(self.header_end)
        self.close_button = header_button("window-close", "Close")
        self.close_button.setProperty("circular", True)
        self.close_button.setFixedSize(26, 26)
        self.close_button.clicked.connect(self.reject)
        self.header_end.addWidget(self.close_button)
        self.header.setVisible(header)
        self._drag = None
        lay.addWidget(self.header)
        self.body_widget = QWidget()
        self.body = QVBoxLayout(self.body_widget)
        self.body.setContentsMargins(24, 8, 24, 24)
        self.body.setSpacing(12)
        lay.addWidget(self.body_widget, 1)
        self.card.setFixedWidth(width)
        self._dim = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.header.geometry().contains(self.card.mapFrom(self, e.position().toPoint())):
            self.windowHandle().startSystemMove()

    def showEvent(self, e):
        super().showEvent(e)
        top = self.parentWidget().window() if self.parentWidget() else None
        if top is not None and self._dim is None:
            self._dim = QWidget(top)
            self._dim.setStyleSheet("background: rgba(0,0,0,0.18);")
            self._dim.setGraphicsEffect(QGraphicsOpacityEffect(self._dim))
            self._dim.setGeometry(top.rect())
            self._dim.show()
            self._dim.raise_()
        self.adjustSize()
        if top is not None:
            g = top.frameGeometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)
        if self._anim is None and not self._closing and platform_win.animations_enabled():
            self._fly(opening=True)

    # --- animation
    def zoom_rect(self):
        """Global rect the card grows out of and shrinks back into; None scales around its center."""
        return None

    def _rest_rect(self, target):
        """Where the whole window snapshot goes when its card lands on `target`."""
        d = self.geometry()
        c = QRect(self.mapToGlobal(self.card.pos()), self.card.size())
        if target is None or target.isEmpty():
            target = QRect(0, 0, round(c.width() * 0.9), round(c.height() * 0.9))
            target.moveCenter(c.center())
        sx, sy = target.width() / c.width(), target.height() / c.height()
        return QRect(round(target.x() + (d.x() - c.x()) * sx), round(target.y() + (d.y() - c.y()) * sy),
                     round(d.width() * sx), round(d.height() * sy))

    def _run(self, layers, duration, easing, dim=None, then=None):
        """Hides the window behind a ghost playing `layers`, then shows it again (or calls `then`)."""
        fx = self._dim.graphicsEffect() if self._dim is not None else None
        if fx is not None and dim is not None:
            fx.setOpacity(dim[0])
        ghost = _Ghost.play(layers)
        self.setWindowOpacity(0)

        def step(t):
            ghost.t = t
            ghost.update()
            if fx is not None and dim is not None:
                fx.setOpacity(dim[0] + (dim[1] - dim[0]) * t)

        def start():
            if anim is self._anim and anim.state() == QVariantAnimation.Stopped:
                anim.start()

        def finished():
            ghost.painted.disconnect(start)
            self._anim = self._anim_done = None
            ghost.hide()
            if then is not None:
                then()
            else:
                self.setWindowOpacity(1)

        anim = _animate(self, duration, easing, step, finished)
        self._anim, self._anim_done = anim, finished
        ghost.painted.connect(start)
        QTimer.singleShot(120, start)     # in case the first paint never comes

    def _finish_animation(self):
        if self._anim is not None:
            self._anim.stop()
            self._anim_done()

    def _fly(self, opening, then=None):
        snapshot, home = self.grab(), self.geometry()
        zoom = self.zoom_rect()
        away = self._rest_rect(zoom)
        faded = 0.35 if zoom is not None else 0.0
        if opening:
            self._run([(snapshot, away, home, faded, 1.0)], 240, QEasingCurve.OutCubic, (0.0, 1.0))
        else:
            self._run([(snapshot, home, away, 1.0, faded if zoom is not None else 0.0)], 170,
                      QEasingCurve.InCubic, (1.0, 0.0), then)

    def morph_from(self, snapshot, rect):
        """Animates from an earlier snapshot of this window (taken at `rect`) to how it looks now."""
        if not (self.isVisible() and platform_win.animations_enabled()):
            return
        self._run([(snapshot, rect, self.geometry(), 1.0, 1.0), (self.grab(), rect, self.geometry(), 0.0, 1.0)],
                  180, QEasingCurve.OutCubic)

    def closing(self):
        """Called once when the sheet starts to close, before the close animation plays."""

    def closeEvent(self, e):
        # The ghost plays the close animation, so the window can go right away
        # (QDialog would refuse while the animation keeps it visible, blocking app quit).
        self.reject()
        e.accept()

    def done(self, r):
        if self._closing:
            return
        self._closing = True
        self._finish_animation()

        def finish():
            if self._dim is not None:
                self._dim.deleteLater()
                self._dim = None
            QDialog.done(self, r)

        if self.isVisible() and platform_win.animations_enabled():
            self._fly(opening=False, then=finish)
            self.closing()
        else:
            self.closing()
            finish()


class AlertDialog(Sheet):
    """AdwAlertDialog: heading, body and a row of response buttons."""

    def __init__(self, parent, heading, body="", responses=(("cancel", "_Cancel", None),), default=None,
                 extra=None, width=360):
        super().__init__(parent, "", width, header=False)
        self.body.setContentsMargins(24, 28, 24, 24)
        self.body.setSpacing(10)
        h = label(heading, title2=True, center=True, wrap=True)
        self.body.addWidget(h)
        if body:
            self.body.addWidget(label(body, center=True, wrap=True))
        if extra is not None:
            self.body.addSpacing(6)
            self.body.addWidget(extra)
        self.body.addSpacing(14)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.response = None
        for rid, text, style in responses:
            b = button(text, suggested=style == "suggested", destructive=style == "destructive")
            b.setMinimumHeight(38)
            b.clicked.connect(lambda _=False, r=rid: self._respond(r))
            row.addWidget(b)
            if rid == default:
                b.setDefault(True)
                b.setFocus()
        self.body.addLayout(row)

    def _respond(self, rid):
        self.response = rid
        self.accept()

    def run(self):
        self.exec()
        return self.response


def alert(parent, heading, body="", responses=(("close", "_Close", None),), default=None, extra=None):
    return AlertDialog(parent, heading, body, responses, default, extra).run()


# ------------------------------------------------------------------ preferences rows

class BoxedList(QFrame):
    """A card containing rows separated by hairlines (Adw boxed-list)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self._rows = 0

    def add_row(self, widget):
        if self._rows:
            self.lay.addWidget(hsep())
        self.lay.addWidget(widget)
        self._rows += 1
        return widget


def action_row(title, subtitle="", suffix=None):
    w = QWidget()
    w.setObjectName("RowCard")
    w.setMinimumHeight(54)
    lay = QHBoxLayout(w)
    lay.setContentsMargins(14, 8, 12, 8)
    text = QVBoxLayout()
    text.setSpacing(1)
    text.addWidget(label(title))
    if subtitle:
        s = label(subtitle, caption=True, wrap=True)
        text.addWidget(s)
    lay.addLayout(text, 1)
    if suffix is not None:
        lay.addWidget(suffix, 0, Qt.AlignVCenter)
        if isinstance(suffix, QAbstractButton):
            w.mousePressEvent = lambda e: suffix.click()
    return w


def property_row(title, value):
    """Title above a selectable value (properties dialog)."""
    w = QWidget()
    w.setObjectName("RowCard")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(14, 9, 14, 9)
    lay.setSpacing(1)
    lay.addWidget(label(title, caption=True))
    v = ElidedLabel(value)
    lay.addWidget(v)
    w.value_label = v
    return w


class ElidedLabel(QLabel):
    """Single-line label that elides in the middle and shows the full text as tooltip."""

    def __init__(self, text=""):
        super().__init__()
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setText(text)

    def setText(self, text):
        self._full = text
        self.setToolTip(text)
        self._elide()

    def text(self):
        return self._full

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideMiddle, max(40, self.width())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def sizeHint(self):
        return QSize(200, self.fontMetrics().height() + 2)
