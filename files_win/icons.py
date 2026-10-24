"""Adwaita-style icons rendered from inline SVG.

Symbolic icons are 16x16 single-color glyphs (recolored per theme); full-color
icons are 64x64 folder/file artwork in the spirit of the Adwaita icon theme.
"""

import math
import os

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# --------------------------------------------------------------- symbolic

_SYM = {
    "go-previous": '<path d="M10 3.5 5.5 8l4.5 4.5" stroke-width="2"/>',
    "go-next": '<path d="m6 3.5 4.5 4.5L6 12.5" stroke-width="2"/>',
    "go-up": '<path d="M3.5 10 8 5.5l4.5 4.5" stroke-width="2"/>',
    "pan-down": '<path d="m4 6 4 4 4-4" stroke-width="1.75"/>',
    "pan-end": '<path d="m6 4 4 4-4 4" stroke-width="1.75"/>',
    "edit-find": '<circle cx="6.75" cy="6.75" r="4.5"/><path d="m10.25 10.25 3.75 3.75" stroke-width="2"/>',
    "folder-search": '<path d="M7 13.25H3.25a1.5 1.5 0 0 1-1.5-1.5v-7.5a1.5 1.5 0 0 1 1.5-1.5h2.75l1.5 1.5h5.25a1.5 1.5 0 0 1 1.5 1.5v1.5"/>'
                     '<circle cx="11" cy="10.25" r="2.5"/><path d="m12.9 12.15 1.6 1.6" stroke-width="1.75"/>',
    "open-menu": '<path d="M2.5 4h11M2.5 8h11M2.5 12h11"/>',
    "view-grid": '<g fill="$c" stroke="none"><rect x="1.5" y="1.5" width="5.5" height="5.5" rx="1.5"/>'
                 '<rect x="9" y="1.5" width="5.5" height="5.5" rx="1.5"/><rect x="1.5" y="9" width="5.5" height="5.5" rx="1.5"/>'
                 '<rect x="9" y="9" width="5.5" height="5.5" rx="1.5"/></g>',
    "view-list": '<g fill="$c" stroke="none"><rect x="1.25" y="2" width="2.75" height="2.75" rx="0.8"/>'
                 '<rect x="1.25" y="6.6" width="2.75" height="2.75" rx="0.8"/><rect x="1.25" y="11.25" width="2.75" height="2.75" rx="0.8"/></g>'
                 '<path d="M6.5 3.4h8M6.5 8h8M6.5 12.6h8"/>',
    "sidebar-show": '<rect x="1.75" y="2.25" width="12.5" height="11.5" rx="2"/><path d="M6.25 2.5v11"/>'
                    '<path d="M3.5 5h1M3.5 7.5h1" stroke-width="1.25"/>',
    "folder-new": '<path d="M7.5 13.25H3.25a1.5 1.5 0 0 1-1.5-1.5v-7.5a1.5 1.5 0 0 1 1.5-1.5h2.75l1.5 1.5h5.25a1.5 1.5 0 0 1 1.5 1.5v2"/>'
                  '<path d="M12 9v5M9.5 11.5h5"/>',
    "document-new": '<path d="M8 14.25H4.75a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1h4.5l3 3V8"/><path d="M12 9.5v5M9.5 12h5"/>',
    "window-close": '<path d="m4.75 4.75 6.5 6.5m0-6.5-6.5 6.5" stroke-width="1.6"/>',
    "window-minimize": '<path d="M4.5 11h7" stroke-width="1.6"/>',
    "window-maximize": '<rect x="4.25" y="4.25" width="7.5" height="7.5" rx="1.5" stroke-width="1.4"/>',
    "window-restore": '<rect x="3.5" y="5.75" width="6.75" height="6.75" rx="1.4" stroke-width="1.4"/>'
                      '<path d="M6 3.5h5a1.5 1.5 0 0 1 1.5 1.5v5" stroke-width="1.4"/>',
    "user-home": '<path d="M2.25 7.25 8 2.5l5.75 4.75"/><path d="M3.75 6.25v7.25h3V10h2.5v3.5h3V6.25"/>',
    "folder": '<path d="M1.75 4.25a1.5 1.5 0 0 1 1.5-1.5h2.75l1.5 1.5h5.25a1.5 1.5 0 0 1 1.5 1.5v6a1.5 1.5 0 0 1-1.5 1.5h-9.5a1.5 1.5 0 0 1-1.5-1.5z"/>',
    "folder-documents": '<path d="M3.75 2.25a1 1 0 0 1 1-1h4.5l3 3v9.5a1 1 0 0 1-1 1h-6.5a1 1 0 0 1-1-1z"/><path d="M6 8h4M6 10.75h4"/>',
    "folder-download": '<path d="M8 1.75v8M4.75 6.75 8 10l3.25-3.25"/><path d="M2 11v1.75a1.5 1.5 0 0 0 1.5 1.5h9a1.5 1.5 0 0 0 1.5-1.5V11"/>',
    "folder-music": '<path d="M6 12V3.5l7-1.5v8.5"/><circle cx="4.25" cy="12" r="1.75" fill="$c"/><circle cx="11.25" cy="10.5" r="1.75" fill="$c"/>',
    "folder-pictures": '<rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2"/><path d="m2.5 11.5 3.5-3.5 3 3 1.5-1.5 3 3"/>'
                       '<circle cx="10.5" cy="6" r="1.25" fill="$c" stroke="none"/>',
    "folder-videos": '<rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2"/><path d="M6.5 5.75v4.5L10.5 8z" fill="$c"/>',
    "user-desktop": '<rect x="1.75" y="2.25" width="12.5" height="8.5" rx="1.5"/><path d="M5.5 13.75h5M8 10.75v3"/>',
    "user-trash": '<path d="M2.5 4h11M6 4V2.25h4V4"/><path d="m3.75 4 .75 9.25a1 1 0 0 0 1 .95h5a1 1 0 0 0 1-.95L12.25 4"/>',
    "user-trash-full": '<path d="M2.5 4h11M6 4V2.25h4V4"/><path d="m3.75 4 .75 9.25a1 1 0 0 0 1 .95h5a1 1 0 0 0 1-.95L12.25 4"/>'
                       '<path d="M6.5 7v4.5M9.5 7v4.5"/>',
    "document-open-recent": '<circle cx="8" cy="8" r="6.25"/><path d="M8 4.5V8l2.5 1.75"/>',
    "starred": '<path d="M8 1.75 9.9 5.6l4.25.62-3.08 3 .73 4.23L8 11.45l-3.8 2 .73-4.23-3.08-3 4.25-.62z" fill="$c"/>',
    "non-starred": '<path d="M8 1.75 9.9 5.6l4.25.62-3.08 3 .73 4.23L8 11.45l-3.8 2 .73-4.23-3.08-3 4.25-.62z"/>',
    "drive-harddisk": '<rect x="1.75" y="3.75" width="12.5" height="8.5" rx="2"/><path d="M1.75 9h12.5"/>'
                      '<circle cx="11.5" cy="10.6" r=".6" fill="$c"/>',
    "drive-removable": '<path d="M5 1.75h6v4.5H5z"/><rect x="3.25" y="6.25" width="9.5" height="8" rx="1.5"/>',
    "media-optical": '<circle cx="8" cy="8" r="6.25"/><circle cx="8" cy="8" r="1.75"/>',
    "network": '<circle cx="8" cy="8" r="6.25"/><path d="M1.75 8h12.5M8 1.75c-3 3.5-3 9 0 12.5M8 1.75c3 3.5 3 9 0 12.5"/>',
    "media-eject": '<path d="M8 3.5 12.5 9h-9z" fill="$c"/><path d="M3.5 12.5h9"/>',
    "list-add": '<path d="M8 3v10M3 8h10"/>',
    "view-more": '<g fill="$c" stroke="none"><circle cx="8" cy="3" r="1.5"/><circle cx="8" cy="8" r="1.5"/><circle cx="8" cy="13" r="1.5"/></g>',
    "tab-new": '<rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2"/><path d="M8 5.5v5M5.5 8h5"/>',
    "zoom-in": '<circle cx="6.75" cy="6.75" r="4.5"/><path d="m10.25 10.25 3.75 3.75" stroke-width="2"/><path d="M6.75 4.75v4M4.75 6.75h4"/>',
    "zoom-out": '<circle cx="6.75" cy="6.75" r="4.5"/><path d="m10.25 10.25 3.75 3.75" stroke-width="2"/><path d="M4.75 6.75h4"/>',
    "object-select": '<path d="m3 8.5 3.25 3.25L13 5" stroke-width="2"/>',
    "edit-clear": '<circle cx="8" cy="8" r="6.25"/><path d="m5.75 5.75 4.5 4.5m0-4.5-4.5 4.5"/>',
    "text-x-generic": '<path d="M3.75 2.25a1 1 0 0 1 1-1h4.5l3 3v9.5a1 1 0 0 1-1 1h-6.5a1 1 0 0 1-1-1z"/>',
    "emblem-link": '<path d="M5 11 11 5M6.5 5H11v4.5"/>',
    "edit-undo": '<path d="M5.5 3 2.5 6l3 3"/><path d="M2.75 6h6.75a3.75 3.75 0 0 1 0 7.5H7"/>',
    "dialog-warning": '<path d="M8 2 14.5 13.5h-13z"/><path d="M8 6.5v3"/><circle cx="8" cy="11.6" r=".5" fill="$c"/>',
    "process-stop": '<rect x="3.5" y="3.5" width="9" height="9" rx="2"/>',
    "external-link": '<path d="M9 2.5h4.5V7M13.5 2.5 7.5 8.5"/><path d="M12 9.5v3a1.5 1.5 0 0 1-1.5 1.5h-7A1.5 1.5 0 0 1 2 12.5v-7A1.5 1.5 0 0 1 3.5 4h3"/>',
    "computer": '<rect x="1.75" y="2.25" width="12.5" height="8.5" rx="1.5"/><path d="M5.5 13.75h5M8 10.75v3"/>',
    "terminal": '<rect x="1.75" y="2.25" width="12.5" height="11.5" rx="2"/><path d="m4.5 6 2 2-2 2M8 10.5h3.5"/>',
    "image-x-generic": '<rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2"/><path d="m2.5 11.5 3.5-3.5 3 3 1.5-1.5 3 3"/>',
    "folder-open": '<path d="M1.75 11.75v-7.5a1.5 1.5 0 0 1 1.5-1.5h2.75l1.5 1.5h4.25a1.5 1.5 0 0 1 1.5 1.5v1"/><path d="M1.75 12.75 4 7.25h10.75l-2.25 5.5z"/>',
    "package": '<path d="M2.25 5 8 2l5.75 3v6.5L8 14.5l-5.75-3z"/><path d="M2.25 5 8 8l5.75-3M8 8v6.5"/>',
    "avatar-default": '<circle cx="8" cy="5" r="3"/><path d="M2.5 14a5.5 5.5 0 0 1 11 0"/>',
    "system-users": '<circle cx="6" cy="5.5" r="2.5"/><path d="M1.5 13.5a4.5 4.5 0 0 1 9 0"/>'
                    '<path d="M10.5 3.2a2.5 2.5 0 0 1 0 4.6M12 9.3a4.5 4.5 0 0 1 2.5 4.2"/>',
    "security-high": '<path d="M8 1.75 13.25 3.75v4c0 3.2-2.2 5.6-5.25 6.5-3.05-.9-5.25-3.3-5.25-6.5v-4z"/>'
                     '<path d="m5.5 8 1.75 1.75L10.75 6.25"/>',
    "media-playback-start": '<path d="M5 3.25v9.5L12.75 8z" fill="$c"/>',
    "media-playback-pause": '<path d="M5.25 3.5v9M10.75 3.5v9" stroke-width="2.5"/>',
}

_SVG_SYM = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="$c" '
            'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">{}</svg>')

_cache = {}


def _dpr():
    app = QGuiApplication.instance()
    return app.devicePixelRatio() if app else 1.0


def _render(svg, w, h, dpr=None):
    dpr = dpr or _dpr()
    pm = QPixmap(max(1, math.ceil(w * dpr)), max(1, math.ceil(h * dpr)))
    pm.fill(Qt.transparent)
    r = QSvgRenderer(QByteArray(svg.encode()))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def _hex(color):
    return QColor(color).name(QColor.HexRgb)


def symbolic_pixmap(name, color, size=16):
    color = QColor(color)
    key = ("s", name, color.rgba(), size, _dpr())
    pm = _cache.get(key)
    if pm is None:
        body = _SYM.get(name, _SYM["text-x-generic"])
        svg = _SVG_SYM.format(body).replace("$c", _hex(color))
        if color.alphaF() < 1:
            svg = svg.replace("<svg ", f'<svg opacity="{color.alphaF():.3f}" ', 1)
        pm = _render(svg, size, size)
        _cache[key] = pm
    return pm


def symbolic(name, color, size=16):
    return QIcon(symbolic_pixmap(name, color, size))


# --------------------------------------------------------------- full color

_FOLDER = (
    '<path d="M10 9h14.2a4 4 0 0 1 2.9 1.2L31 14h23a4 4 0 0 1 4 4v32a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4V13a4 4 0 0 1 4-4z" fill="#438de6"/>'
    '<path d="M10 19.5h44a4 4 0 0 1 4 4V50a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4V23.5a4 4 0 0 1 4-4z" fill="#62a0ea"/>'
    '<path d="M10 19.5h44a4 4 0 0 1 4 4v1.2a4 4 0 0 0-4-4H10a4 4 0 0 0-4 4v-1.2a4 4 0 0 1 4-4z" fill="#c0dbf6" opacity=".7"/>'
    '<path d="M6 49a4 4 0 0 0 4 4h44a4 4 0 0 0 4-4v1a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4z" fill="#1a5fb4" opacity=".55"/>'
)

_PAPER = (
    '<path d="M15.5 4.5H39L52.5 18v39a3 3 0 0 1-3 3h-34a3 3 0 0 1-3-3v-49.5a3 3 0 0 1 3-3z" fill="#fcfcfc" stroke="#b9b8b5" stroke-width="1"/>'
    '<path d="M39 4.5V15a3 3 0 0 0 3 3h10.5z" fill="#deddda" stroke="#b9b8b5" stroke-width="1" stroke-linejoin="round"/>'
)

_LINES = '<path d="M20 26h24M20 32h24M20 38h24M20 44h24M20 50h14" stroke="{c}" stroke-width="2.4" stroke-linecap="round"/>'

_GLYPHS = {
    "text": _LINES.format(c="#9a9996"),
    "document": _LINES.format(c="#3584e4"),
    "code": ('<path d="M20 26h14M24 32h18M24 38h12M20 44h20M20 50h10" stroke="#9a9996" stroke-width="2.4" stroke-linecap="round"/>'
             '<path d="M20 26h6" stroke="#e66100" stroke-width="2.4" stroke-linecap="round"/>'
             '<path d="M24 32h7M24 38h5" stroke="#3584e4" stroke-width="2.4" stroke-linecap="round"/>'),
    "image": ('<rect x="18" y="25" width="28" height="24" rx="3" fill="#62a0ea"/>'
              '<path d="m18 43 8-8 6.5 6.5 4-4L46 46.5V46a3 3 0 0 1-3 3H21a3 3 0 0 1-3-3z" fill="#33d17a"/>'
              '<circle cx="38.5" cy="31.5" r="3.2" fill="#f6d32d"/>'),
    "audio": ('<path d="M27.5 46V30.5L42 27v15" fill="none" stroke="#e66100" stroke-width="3" stroke-linejoin="round"/>'
              '<circle cx="24" cy="46" r="4.5" fill="#e66100"/><circle cx="38.5" cy="42.5" r="4.5" fill="#e66100"/>'),
    "video": ('<rect x="17" y="27" width="30" height="22" rx="3.5" fill="#613583"/>'
              '<path d="M28.5 32.5v11l9-5.5z" fill="#ffffff"/>'),
    "pdf": (_LINES.format(c="#c0bfbc").replace("M20 44h24M20 50h14", "") +
            '<rect x="16" y="39" width="32" height="13" rx="3" fill="#e01b24"/>'
            '<path d="M21 46h6M30 46h3M36 46h7" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/>'),
    "spreadsheet": ('<rect x="18" y="25" width="28" height="25" rx="2.5" fill="#26a269"/>'
                    '<path d="M18 33.3h28M18 41.6h28M27.3 25v25M36.6 25v25" stroke="#ffffff" stroke-width="1.6"/>'),
    "presentation": ('<rect x="17" y="27" width="30" height="20" rx="2.5" fill="#e66100"/>'
                     '<path d="m22 42 6-6 4.5 4.5L41 32" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'),
    "executable": ('<circle cx="32" cy="38" r="9" fill="none" stroke="#5e5c64" stroke-width="5" stroke-dasharray="4.7 2.37"/>'
                   '<circle cx="32" cy="38" r="5" fill="none" stroke="#5e5c64" stroke-width="3"/>'),
    "web": ('<circle cx="32" cy="38" r="12" fill="#3584e4"/>'
            '<path d="M20 38h24M32 26c-6 7-6 17 0 24M32 26c6 7 6 17 0 24" stroke="#fff" stroke-width="1.8" fill="none"/>'),
    "font": '<path d="m21 49 8-22h2l8 22M24 41h12" fill="none" stroke="#5e5c64" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>',
    "disk": ('<circle cx="32" cy="38" r="12" fill="#c0bfbc"/><circle cx="32" cy="38" r="12" fill="none" stroke="#9a9996"/>'
             '<circle cx="32" cy="38" r="3.5" fill="#fcfcfc" stroke="#9a9996"/>'),
    "generic": "",
}

_ARCHIVE = (
    '<path d="M12 18 18 8h28l6 10z" fill="#b5835a"/>'
    '<rect x="8" y="17" width="48" height="39" rx="3.5" fill="#cdab8f"/>'
    '<path d="M8 20.5a3.5 3.5 0 0 1 3.5-3.5h41a3.5 3.5 0 0 1 3.5 3.5V24H8z" fill="#b5835a"/>'
    '<rect x="26" y="17" width="12" height="17" fill="#f6f5f4" opacity=".85"/>'
    '<path d="M8 53a3.5 3.5 0 0 0 3.5 3.5h41A3.5 3.5 0 0 0 56 53v-1H8z" fill="#865e3c" opacity=".6"/>'
)

_DRIVE = (
    '<rect x="5" y="17" width="54" height="32" rx="6" fill="#77767b"/>'
    '<rect x="5" y="15" width="54" height="28" rx="6" fill="#deddda"/>'
    '<rect x="5" y="15" width="54" height="28" rx="6" fill="none" stroke="#c0bfbc"/>'
    '<rect x="12" y="35" width="18" height="2.5" rx="1.25" fill="#9a9996"/>'
    '<circle cx="50" cy="36" r="2.2" fill="#33d17a"/>'
)

_FOLDER_GLYPH = {
    "user-home": "user-home", "documents": "folder-documents", "downloads": "folder-download",
    "music": "folder-music", "pictures": "folder-pictures", "videos": "folder-videos",
    "desktop": "user-desktop", "remote": "network", "trash": "user-trash",
}


def _wrap(body, size=64):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">{body}</svg>'


def _glyph_group(name, color, x, y, scale, width=1.5):
    body = _SYM[name].replace("$c", color)
    return (f'<g transform="translate({x} {y}) scale({scale})" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{body}</g>')


DEFAULT_FOLDER_COLOR = "#62a0ea"


def _mix(a, b, amount):
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() + (b.red() - a.red()) * amount), round(a.green() + (b.green() - a.green()) * amount),
                  round(a.blue() + (b.blue() - a.blue()) * amount)).name()


def _folder(tint):
    """The folder artwork recolored from one front color: (svg body, glyph color)."""
    if not tint:
        return _FOLDER, "#1c5fb0"
    c = QColor(tint)
    glyph = _mix(c, "#ffffff", 0.6) if c.lightnessF() < 0.35 else _mix(c, "#000000", 0.5)
    body = (_FOLDER.replace("#438de6", _mix(c, "#000000", 0.13)).replace("#62a0ea", c.name())
            .replace("#c0dbf6", _mix(c, "#ffffff", 0.6)).replace("#1a5fb4", _mix(c, "#000000", 0.45)))
    return body, glyph


def color_pixmap(kind, size, special=None, tint=None):
    """kind: 'folder', 'archive', 'drive', or a file category from categorize().
    tint recolors a folder (any QColor name, e.g. '#e01b24')."""
    key = ("c", kind, special, size, _dpr(), tint)
    pm = _cache.get(key)
    if pm is not None:
        return pm
    if kind == "folder":
        body, glyph = _folder(tint)
        if special in _FOLDER_GLYPH:
            body += _glyph_group(_FOLDER_GLYPH[special], glyph, 20.8, 25.8, 1.4, 1.5)
    elif kind == "archive":
        body = _ARCHIVE
    elif kind == "drive":
        body = _DRIVE
    else:
        body = _PAPER + _GLYPHS.get(kind, "")
    pm = _render(_wrap(body), size, size)
    _cache[key] = pm
    return pm


def app_icon():
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_render(_wrap(_FOLDER + _glyph_group("edit-find", "#1c5fb0", 22.5, 27.5, 1.2, 1.8)), s, s, 1.0))
    return icon


def clear_cache():
    _cache.clear()


# --------------------------------------------------------------- categories

_CATEGORIES = {
    "image": "png jpg jpeg jfif gif bmp webp tif tiff ico svg heic heif hif avif raw cr2 cr3 nef dng arw raf orf rw2 "
             "psd psb ai xcf",
    "audio": "mp3 wav flac ogg oga opus m4a aac wma aiff mid midi",
    "video": "mp4 mkv webm avi mov wmv flv m4v mpg mpeg 3gp ts",
    "archive": "zip 7z rar tar gz tgz bz2 xz zst lz cab jar apk deb rpm whl",
    "pdf": "pdf",
    "document": "doc docx odt rtf pages epub md markdown tex",
    "spreadsheet": "xls xlsx ods csv tsv numbers",
    "presentation": "ppt pptx odp key",
    "code": "py pyw js mjs cjs ts tsx jsx java c h cpp hpp cc cs go rs rb php swift kt sh ps1 bat cmd lua "
            "pl r scala sql json yaml yml toml xml ini cfg conf css scss less vue dart zig nim ex exs hs",
    "web": "html htm url",
    "text": "txt log nfo me readme",
    "executable": "exe msi com scr appx msix",
    "font": "ttf otf woff woff2 fon",
    "disk": "iso img vhd vhdx dmg",
}

_EXT_TO_CAT = {ext: cat for cat, exts in _CATEGORIES.items() for ext in exts.split()}

THUMBNAIL_EXTS = {"png", "jpg", "jpeg", "jfif", "gif", "bmp", "webp", "ico", "tif", "tiff", "svg"}
ADOBE_EXTS = {"psd", "psb", "ai"}
SHELL_PREVIEW_EXTS = {"heic", "heif", "hif", "avif", "cr2", "cr3", "nef", "dng", "arw", "raf", "orf", "rw2"}
PREVIEW_EXTS = THUMBNAIL_EXTS | ADOBE_EXTS | SHELL_PREVIEW_EXTS    # files that get a picture instead of an icon

_TYPE_NAMES = {
    "image": "image", "audio": "audio", "video": "video", "archive": "archive", "pdf": "PDF document",
    "document": "document", "spreadsheet": "spreadsheet", "presentation": "presentation",
    "code": "source code", "web": "web page", "text": "plain text document", "executable": "program",
    "font": "font", "disk": "disk image",
}


def extension(name):
    _, ext = os.path.splitext(name)
    return ext[1:].lower()


def categorize(name):
    return _EXT_TO_CAT.get(extension(name), "generic")


def type_description(name, is_dir=False):
    if is_dir:
        return "Folder"
    ext = extension(name)
    cat = _EXT_TO_CAT.get(ext)
    if ext == "lnk":
        return "Shortcut"
    if not cat:
        return f"{ext.upper()} file" if ext else "File"
    if cat in ("pdf", "text"):
        return _TYPE_NAMES[cat][0].upper() + _TYPE_NAMES[cat][1:]
    return f"{ext.upper()} {_TYPE_NAMES[cat]}"


def type_group(name, is_dir=False):
    """Coarse group used by the search type filter."""
    if is_dir:
        return "folders"
    cat = categorize(name)
    return {"image": "images", "audio": "music", "video": "videos", "archive": "archives",
            "pdf": "documents", "document": "documents", "spreadsheet": "documents",
            "presentation": "documents", "text": "documents", "code": "text"}.get(cat, "other")
