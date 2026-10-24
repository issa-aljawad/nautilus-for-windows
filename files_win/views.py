"""Grid and list views with Nautilus styling and shared behavior."""

import os

from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap, QTextLayout, QTextOption
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QListView, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QTreeView)

from . import icons, theme
from .model import ItemRole, VIRTUAL
from . import model as model_mod
from .settings import folder_colors, settings

GRID_SIZES = [48, 64, 96, 128, 192, 256]     # presets for Ctrl+plus/minus; the slider goes anywhere between
LIST_SIZES = [16, 32, 48, 64]
DEFAULT_SIZE = {"grid": 96, "list": 32}
_RENDER_SIZES = (16, 24, 32, 48, 64, 96, 128, 192, 256)


def drop_action(mods, paths, target):
    """What dropping `paths` on `target` does: Ctrl copies, Shift moves, Alt links; otherwise
    move on the same drive and copy across drives (into the trash is always a move)."""
    if target == model_mod.TRASH:
        return Qt.MoveAction
    if mods & Qt.ControlModifier:
        return Qt.CopyAction
    if mods & Qt.ShiftModifier:
        return Qt.MoveAction
    if mods & Qt.AltModifier:
        return Qt.LinkAction
    from .operations import same_volume
    return Qt.MoveAction if paths and same_volume(paths[0], target) else Qt.CopyAction


def drop_paths(mime):
    return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]


def preset_sizes(mode):
    return GRID_SIZES if mode == "grid" else LIST_SIZES


def icon_size(mode):
    """The icon size in pixels for 'grid' or 'list', from the slider or the older preset index."""
    sizes = preset_sizes(mode)
    px = settings[f"{mode}_icon_size"]
    if not isinstance(px, int):
        level = settings[f"{mode}_zoom"]
        px = sizes[max(0, min(len(sizes) - 1, level if isinstance(level, int) else 0))]
    return max(sizes[0], min(sizes[-1], px))

_SPECIAL = {}


def set_special_folders(mapping):
    """mapping: normcase(path) -> special key ('documents', 'user-home', ...)."""
    _SPECIAL.clear()
    _SPECIAL.update(mapping)


_shell_icons = {}


def _shell_icon(path, size):
    """Windows' own icon for executables and shortcuts (they carry app icons)."""
    from PySide6.QtCore import QFileInfo
    from PySide6.QtWidgets import QFileIconProvider
    key = (os.path.normcase(path), size)
    pm = _shell_icons.get(key)
    if pm is None:
        prov = QFileIconProvider()
        icon = prov.icon(QFileInfo(path))
        pm = icon.pixmap(QSize(size, size)) if not icon.isNull() else QPixmap()
        _shell_icons[key] = pm
    return pm if not pm.isNull() else None


def item_pixmap(item, size):
    """(pixmap, is_thumbnail) for an item at the given logical size (or a little larger: drawing
    scales icons down, and rendering only fixed sizes keeps the slider from filling the icon cache)."""
    size = next((s for s in _RENDER_SIZES if s >= size), size)
    if item.drive:
        return icons.color_pixmap("drive", size), False
    if item.opens_as_folder:
        special = _SPECIAL.get(os.path.normcase(os.path.normpath(item.open_path)))
        return icons.color_pixmap("folder", size, special, folder_colors.get(item.open_path)), False
    if model_mod.thumbnails is not None and size >= 32:
        img = model_mod.thumbnails.get(item)
        if img is not None:
            return img, True
    ext = icons.extension(item.name)
    if ext in ("exe", "lnk", "ico", "msi", "url") and not item.trash:
        pm = _shell_icon(item.path, min(size, 256))
        if pm is not None:
            return pm, False
    kind = "archive" if item.category == "archive" else item.category
    return icons.color_pixmap(kind, size), False


def draw_item_icon(p, item, rect, size, dimmed=False):
    """Draw icon/thumbnail centered in rect."""
    pm, is_thumb = item_pixmap(item, size)
    if is_thumb:
        img = pm
        scaled = QSize(img.width(), img.height()).scaled(QSize(size, size), Qt.KeepAspectRatio)
        if img.width() <= size and img.height() <= size:
            scaled = QSize(img.width(), img.height())
        target = QRect(0, 0, scaled.width(), scaled.height())
        target.moveCenter(rect.center())
        t = theme.current()
        p.save()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawRoundedRect(QRectF(target).adjusted(-1, -1, 1, 2), 3, 3)
        p.setBrush(t.thumb_bg)
        p.drawRoundedRect(QRectF(target), 2, 2)
        path = QPainterPath()
        path.addRoundedRect(QRectF(target), 2, 2)
        p.setClipPath(path)
        p.drawImage(target, img)
        p.restore()
    else:
        dpr = pm.devicePixelRatio() or 1
        w, h = pm.width() / dpr, pm.height() / dpr
        if w > size or h > size:
            pm = pm.scaled(int(size * dpr), int(size * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            w, h = pm.width() / dpr, pm.height() / dpr
        x = rect.x() + (rect.width() - w) / 2
        y = rect.y() + (rect.height() - h) / 2
        p.drawPixmap(QRectF(x, y, w, h), pm, QRectF(pm.rect()))
    if item.is_link and not item.drive:
        es = max(12, size // 4)
        er = QRectF(rect.center().x() + size / 2 - es + 2, rect.center().y() + size / 2 - es + 2, es, es)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(er)
        p.drawPixmap(er.adjusted(es * .15, es * .15, -es * .15, -es * .15).toRect(),
                     icons.symbolic_pixmap("emblem-link", QColor("#2e2e33"), max(8, int(es * .7))))
        p.restore()


# ------------------------------------------------------------------ shared behavior

class ViewMixin:
    """Behavior shared by GridView and ListView (signals live on each class)."""

    def _setup_common(self):
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(False)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WA_Hover)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context)
        self.setFrameShape(QListView.NoFrame)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.drop_row = -1
        self.location = None
        self.single_click = False
        self._press_pos = None

    def item_at(self, pos):
        idx = self.indexAt(pos)
        return self.model().item(idx.row()) if idx.isValid() else None

    def item_icon_rect(self, item):
        """Global rect of the item's icon, or None when it isn't scrolled into view."""
        row = self.model().row_for_path(item.path)
        if row < 0:
            return None
        cell = self.visualRect(self.model().index(row, 0))
        if not cell.isValid() or not self.viewport().rect().intersects(cell):
            return None
        r = self._icon_rect(cell)
        return QRect(self.viewport().mapToGlobal(r.topLeft()), r.size())

    def hovered_item(self):
        """The item under the mouse pointer, if the pointer is over this view."""
        vp = self.viewport()
        return self.item_at(vp.mapFromGlobal(QCursor.pos())) if vp.underMouse() else None

    def _context(self, pos):
        idx = self.indexAt(pos)
        if idx.isValid() and not self.selectionModel().isSelected(idx):
            flags = QItemSelectionModel.SelectionFlag
            self.selectionModel().select(idx, flags.ClearAndSelect | flags.Rows)
            self.setCurrentIndex(idx)
        self.contextRequested.emit(self.viewport().mapToGlobal(pos), idx.isValid())

    def mouseDoubleClickEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        if e.button() == Qt.LeftButton and idx.isValid() and not self.single_click:
            how = 1 if e.modifiers() & Qt.ControlModifier else 2 if e.modifiers() & Qt.ShiftModifier else 0
            self.activateRequested.emit(self.model().item(idx.row()), how)
            return
        super().mouseDoubleClickEvent(e)

    def mousePressEvent(self, e):
        self._press_pos = e.position().toPoint()
        idx = self.indexAt(self._press_pos)
        if e.button() == Qt.MiddleButton and idx.isValid():
            item = self.model().item(idx.row())
            if item.opens_as_folder:
                self.activateRequested.emit(item, 1)
            return
        if e.button() == Qt.BackButton or e.button() == Qt.ForwardButton:
            e.ignore()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if (self.single_click and e.button() == Qt.LeftButton and self._press_pos is not None
                and not e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)
                and (e.position().toPoint() - self._press_pos).manhattanLength() < 4):
            idx = self.indexAt(e.position().toPoint())
            if idx.isValid():
                self.activateRequested.emit(self.model().item(idx.row()), 0)

    def keyPressEvent(self, e):
        key, mods = e.key(), e.modifiers()
        if key in (Qt.Key_Return, Qt.Key_Enter) and not mods & Qt.AltModifier:
            idx = self.currentIndex()
            items = self.selected_items()
            if items:
                how = 1 if mods & Qt.ControlModifier else 2 if mods & Qt.ShiftModifier else 0
                for it in items[:10]:
                    self.activateRequested.emit(it, how)
                return
        if key == Qt.Key_Space and not mods & ~Qt.KeypadModifier and (self.selected_items() or self.hovered_item()):
            self.previewRequested.emit()
            return
        if key == Qt.Key_Backspace and not mods:
            self.backRequested.emit()
            return
        text = e.text()
        if (text and text.isprintable() and not text.isspace()
                and not mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            self.typeAhead.emit(text)
            return
        super().keyPressEvent(e)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoomRequested.emit(1 if e.angleDelta().y() > 0 else -1)
            return
        super().wheelEvent(e)

    def selected_items(self):
        rows = sorted({i.row() for i in self.selectionModel().selectedIndexes()})
        return [self.model().item(r) for r in rows]

    # --- drag & drop
    def _drop_target(self, pos, paths):
        idx = self.indexAt(pos)
        if idx.isValid():
            item = self.model().item(idx.row())
            norm = {os.path.normcase(p) for p in paths}
            if item.opens_as_folder and os.path.normcase(item.path) not in norm and not item.trash:
                return item.open_path, idx.row()
        if self.location and self.location not in VIRTUAL:
            return self.location, -1
        if self.location == model_mod.TRASH:
            return model_mod.TRASH, -1
        return None, -1

    def _paths(self, mime):
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]

    def _action(self, e, paths, target):
        return drop_action(e.modifiers(), paths, target)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        paths = self._paths(e.mimeData())
        target, row = self._drop_target(e.position().toPoint(), paths)
        if self.drop_row != row:
            self.drop_row = row
            self.viewport().update()
        if not paths or target is None:
            e.ignore()
            return
        if row < 0 and target != model_mod.TRASH and all(os.path.normcase(os.path.dirname(p)) == os.path.normcase(target) for p in paths):
            e.ignore()   # dropping into the folder they already live in
            return
        e.setDropAction(self._action(e, paths, target))
        e.accept()

    def dragLeaveEvent(self, e):
        self.drop_row = -1
        self.viewport().update()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        paths = self._paths(e.mimeData())
        target, _row = self._drop_target(e.position().toPoint(), paths)
        self.drop_row = -1
        self.viewport().update()
        if paths and target:
            action = self._action(e, paths, target)
            e.setDropAction(action)
            e.accept()
            self.dropRequested.emit(paths, target, action)
        else:
            e.ignore()

    def startDrag(self, actions):
        super().startDrag(Qt.CopyAction | Qt.MoveAction | Qt.LinkAction)


def _cell_bg(p, rect, radius, hovered, selected, drop):
    t = theme.current()
    if drop:
        p.setBrush(t.accent_alpha(0.25))
        p.setPen(QPen(t.accent_bg, 2))
        p.drawRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), radius, radius)
        return
    if selected:
        c = t.accent_alpha(0.32 if hovered else 0.25)
    elif hovered:
        c = t.fg_alpha(0.06)
    else:
        return
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawRoundedRect(QRectF(rect), radius, radius)


# ------------------------------------------------------------------ grid

class GridDelegate(QStyledItemDelegate):
    PAD = 6
    GAP = 6

    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def label_width(self):
        return max(self.view.icon_px + 24, 104)

    def cell_size(self):
        fm = QFontMetrics(self.view.font())
        return QSize(self.label_width() + self.PAD * 2,
                     self.PAD + self.view.icon_px + 6 + fm.lineSpacing() * 3 + self.PAD)

    def sizeHint(self, option, index):
        return self.cell_size()

    def paint(self, p, option, index):
        item = index.data(ItemRole)
        if item is None:
            return
        v = self.view
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        cell = option.rect.adjusted(self.GAP // 2, self.GAP // 2, -self.GAP // 2, -self.GAP // 2)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        fm = QFontMetrics(option.font)
        size = v.icon_px
        icon_rect = QRect(cell.x(), cell.y() + self.PAD, cell.width(), size)
        lines = self._lines(item.name, option.font, fm, cell.width() - self.PAD * 2, 3)
        text_h = fm.lineSpacing() * len(lines)
        bg = QRect(cell.x(), cell.y(), cell.width(), self.PAD + size + 6 + text_h + self.PAD)
        _cell_bg(p, bg, 12, hovered, selected, v.drop_row == index.row())
        if item.hidden or os.path.normcase(item.path) in v.model().cut_paths:
            p.setOpacity(0.55)
        draw_item_icon(p, item, icon_rect, size)
        t = theme.current()
        p.setPen(t.fg)
        y = icon_rect.bottom() + 1 + 6
        for line in lines:
            r = QRect(cell.x() + self.PAD, y, cell.width() - self.PAD * 2, fm.lineSpacing())
            p.drawText(r, Qt.AlignHCenter | Qt.AlignTop, line)
            y += fm.lineSpacing()
        p.restore()

    @staticmethod
    def _lines(text, font, fm, width, max_lines):
        layout = QTextLayout(text, font)
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(opt)
        lines = []
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            lines.append((line.textStart(), line.textLength()))
        layout.endLayout()
        out = [text[s:s + n] for s, n in lines]
        if len(out) > max_lines:
            rest = text[lines[max_lines - 1][0]:]
            out = out[:max_lines - 1] + [fm.elidedText(rest, Qt.ElideMiddle, width)]
        return out or [""]


class GridView(ViewMixin, QListView):
    activateRequested = Signal(object, int)       # item, how (0 same, 1 tab, 2 window)
    contextRequested = Signal(QPoint, bool)       # global pos, on_item
    typeAhead = Signal(str)
    zoomRequested = Signal(int)
    dropRequested = Signal(list, str, object)     # paths, target folder, Qt.DropAction
    starToggled = Signal(object)
    previewRequested = Signal()
    backRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_common()
        self.setViewMode(QListView.IconMode)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)   # same selections as the list view
        self.setMovement(QListView.Static)
        # setMovement(Static) also turns off dragging and the viewport's drops; files still drag and drop
        self.setDragEnabled(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setResizeMode(QListView.Adjust)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSelectionRectVisible(True)
        self.setLayoutMode(QListView.Batched)
        self.setBatchSize(400)
        self.setSpacing(0)
        self.setViewportMargins(12, 12, 12, 12)
        self.icon_px = GRID_SIZES[2]
        self.delegate = GridDelegate(self)
        self.setItemDelegate(self.delegate)
        self._relayout()

    def set_icon_size(self, px):
        if px != self.icon_px:
            self.icon_px = px
            self._relayout()
            self.viewport().update()

    def _icon_rect(self, cell):
        d = GridDelegate
        return QRect(cell.center().x() - self.icon_px // 2, cell.y() + d.GAP // 2 + d.PAD, self.icon_px, self.icon_px)

    def _relayout(self):
        """Stretch cells so the columns fill the width, like GtkGridView."""
        cell = self.delegate.cell_size()
        # Measure from the view itself, always leaving room for the scrollbar: sizing cells by the
        # viewport made the scrollbar's appearing and disappearing resize them back and forth forever.
        m = self.viewportMargins()
        width = self.width() - 2 * self.frameWidth() - m.left() - m.right()
        avail = max(1, width - self.verticalScrollBar().sizeHint().width() - 4)   # QListView wraps a little early
        cols = max(1, avail // cell.width())
        w = avail // cols if cols > 1 else cell.width()
        size = QSize(w, cell.height())
        if size != self.gridSize():
            self.setGridSize(size)
            self.doItemsLayout()

    def viewportEvent(self, e):
        if e.type() == QEvent.Resize:
            QTimer.singleShot(0, self._relayout)
        return super().viewportEvent(e)


# ------------------------------------------------------------------ list

class ListDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index):
        return QSize(100, self.view.row_height())

    def paint(self, p, option, index):
        item = index.data(ItemRole)
        if item is None:
            return
        v = self.view
        col = v.model().columns[index.column()]
        t = theme.current()
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if item.hidden or os.path.normcase(item.path) in v.model().cut_paths:
            p.setOpacity(0.55)
        r = option.rect
        if col == "name":
            size = v.icon_px
            x = r.x() + 18
            draw_item_icon(p, item, QRect(x, r.y(), size, r.height()), size)
            tr = QRect(x + size + 12, r.y(), r.right() - (x + size + 12), r.height())
            p.setPen(t.fg)
            p.drawText(tr, Qt.AlignVCenter | Qt.AlignLeft,
                       option.fontMetrics.elidedText(item.name, Qt.ElideMiddle, tr.width()))
        elif col == "starred":
            if not item.trash and not item.drive:
                hovered_row = v.hover_row == index.row()
                on = item.starred
                if on or hovered_row:
                    color = t.fg if on else t.fg_alpha(0.45)
                    pm = icons.symbolic_pixmap("starred" if on else "non-starred", color, 16)
                    p.drawPixmap(r.center().x() - 8, r.center().y() - 8, pm)
        else:
            text = v.model().text(item, col)
            p.setPen(t.dim_fg if col != "name" else t.fg)
            align = (Qt.AlignRight if col == "size" else Qt.AlignLeft) | Qt.AlignVCenter
            tr = r.adjusted(8, 0, -12 if col == "size" else -8, 0)
            p.drawText(tr, align, option.fontMetrics.elidedText(text, Qt.ElideRight, tr.width()))
        p.restore()


class ListView(ViewMixin, QTreeView):
    activateRequested = Signal(object, int)       # item, how (0 same, 1 tab, 2 window)
    contextRequested = Signal(QPoint, bool)       # global pos, on_item
    typeAhead = Signal(str)
    zoomRequested = Signal(int)
    dropRequested = Signal(list, str, object)     # paths, target folder, Qt.DropAction
    starToggled = Signal(object)
    previewRequested = Signal()
    backRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_common()
        self.setRootIsDecorated(False)
        self.setItemsExpandable(False)
        self.setUniformRowHeights(True)
        self.setIndentation(0)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setAllColumnsShowFocus(False)
        self.setSortingEnabled(False)
        self.icon_px = LIST_SIZES[1]
        self.hover_row = -1
        self.delegate = ListDelegate(self)
        self.setItemDelegate(self.delegate)
        h = self.header()
        h.setSectionsClickable(True)
        h.setHighlightSections(False)
        h.setStretchLastSection(False)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        h.setMinimumHeight(34)
        h.sectionClicked.connect(self._header_clicked)
        self.sortRequested = None
        self.setViewportMargins(0, 0, 0, 0)

    def row_height(self):
        return self.icon_px + (16 if self.icon_px <= 16 else 18)

    def _icon_rect(self, cell):
        return QRect(cell.x() + 18, cell.center().y() - self.icon_px // 2, self.icon_px, self.icon_px)

    def set_icon_size(self, px):
        if px != self.icon_px:
            self.icon_px = px
            self.scheduleDelayedItemsLayout()
            self.viewport().update()

    def setup_columns(self):
        cols = self.model().columns
        h = self.header()
        for i, c in enumerate(cols):
            if c == "name":
                h.setSectionResizeMode(i, QHeaderView.Stretch)
            elif c == "starred":
                h.setSectionResizeMode(i, QHeaderView.Fixed)
                h.resizeSection(i, 52)
            else:
                h.setSectionResizeMode(i, QHeaderView.Interactive)
                h.resizeSection(i, {"size": 100, "modified": 150, "type": 150, "location": 220,
                                    "original": 220, "trashed": 150}.get(c, 120))

    def _header_clicked(self, section):
        if self.sortRequested:
            self.sortRequested(self.model().columns[section])

    def drawRow(self, p, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRect(8, option.rect.y() + 2, self.viewport().width() - 16, option.rect.height() - 4)
        selected = self.selectionModel().isSelected(index)
        _cell_bg(p, r, 9, self.hover_row == index.row(), selected, self.drop_row == index.row())
        p.restore()
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.State_Selected
        opt.state &= ~QStyle.State_HasFocus
        super().drawRow(p, opt, index)

    def drawBranches(self, p, rect, index):
        pass

    def mouseMoveEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        row = idx.row() if idx.isValid() else -1
        if row != self.hover_row:
            self.hover_row = row
            self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self.hover_row = -1
        self.viewport().update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        if (e.button() == Qt.LeftButton and idx.isValid()
                and self.model().columns[idx.column()] == "starred"):
            item = self.model().item(idx.row())
            if not item.trash and not item.drive:
                self.starToggled.emit(item)
                return
        super().mousePressEvent(e)
