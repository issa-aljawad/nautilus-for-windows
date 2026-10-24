"""Quick Look: the Space-bar previewer (GNOME Sushi in Nautilus).

Renders images (animated too), Photoshop and Illustrator files, video and
audio, PDF, Word (.docx), Excel (.xlsx), CSV, Markdown and plain text. Left/Right step through the folder
(or the selection), Return opens the file, Space or Escape closes."""

import csv
import html
import io
import os
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFont, QFontDatabase, QImage, QImageReader, QMovie, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLayout, QPlainTextEdit, QTabBar,
                               QTableView, QTextBrowser, QVBoxLayout, QWidget)

from . import adobe, icons, model, platform_win, theme
from .widgets import Sheet, header_button, label, set_symbolic, slider

TEXT_EXTS = {"txt", "md", "log", "py", "js", "ts", "json", "xml", "yaml", "yml", "toml", "ini", "cfg", "c",
             "h", "cpp", "cs", "java", "go", "rs", "css", "html", "htm", "sh", "ps1", "bat", "sql", "srt", "vtt"}
TEXT_LIMIT = 512_000
MAX_ROWS, MAX_COLS = 1000, 100
SEP = "  ·  "


def _decode(raw):
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if raw.startswith(bom):
            return raw.decode(enc, errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        if e.start > len(raw) - 4:      # a character cut in half by the read limit
            return raw[:e.start].decode("utf-8", errors="replace")
        return raw.decode("cp1252", errors="replace")


def _read_text(path, limit=TEXT_LIMIT):
    with open(path, "rb") as f:
        raw = f.read(limit + 1)
    return _decode(raw[:limit]), len(raw) > limit


def _looks_text(path):
    try:
        if os.path.getsize(path) > 5_000_000:
            return False
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    return bool(head) and b"\0" not in head


def _duration(ms):
    s = max(0, ms // 1000)
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def _col_name(i):
    name = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        name = chr(65 + r) + name
    return name


def _col_index(ref):
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


def _cell_ref(ref):
    letters = re.match(r"[A-Za-z]+", ref)
    return int(ref[letters.end():]) - 1, _col_index(ref)


# ------------------------------------------------------------------ Office Open XML

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _rels(z, part):
    """Relationship id -> part name inside the zip, for internal targets of `part`."""
    folder, name = posixpath.split(part)
    try:
        root = ET.fromstring(z.read(posixpath.join(folder, "_rels", name + ".rels")))
    except KeyError:
        return {}
    out = {}
    for r in root.iter(PKG + "Relationship"):
        target = r.get("Target", "")
        if r.get("TargetMode") == "External" or not target:
            continue
        out[r.get("Id")] = target[1:] if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
    return out


def _on(props, tag):
    el = props.find(W + tag)
    return el is not None and el.get(W + "val", "true") not in ("0", "false", "none")


def _roman(n):
    out = ""
    for value, digits in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                          (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= value:
            out += digits
            n -= value
    return out


def _list_number(n, fmt):
    if fmt in ("lowerLetter", "upperLetter"):
        s = _col_name(n - 1).lower()
        return s.upper() if fmt == "upperLetter" else s
    if fmt in ("lowerRoman", "upperRoman"):
        s = _roman(n)
        return s.upper() if fmt == "upperRoman" else s
    return str(n)


class _Docx:
    """Converts word/document.xml to the HTML subset QTextDocument understands."""

    def __init__(self, z, images):
        self.z, self.images = z, images
        self.rels = _rels(z, "word/document.xml")
        self.styles = self._styles()
        self.numbering = self._numbering()
        self.counters = {}

    def _xml(self, part):
        try:
            return ET.fromstring(self.z.read(part))
        except KeyError:
            return None

    def _styles(self):
        root = self._xml("word/styles.xml")
        if root is None:
            return {}
        out = {}
        for s in root.iter(W + "style"):
            n = s.find(W + "name")
            if n is not None:
                out[s.get(W + "styleId")] = n.get(W + "val", "").lower()
        return out

    def _numbering(self):
        root = self._xml("word/numbering.xml")
        if root is None:
            return {}
        abstract = {}
        for a in root.iter(W + "abstractNum"):
            for lvl in a.iter(W + "lvl"):
                f = lvl.find(W + "numFmt")
                abstract[(a.get(W + "abstractNumId"), int(lvl.get(W + "ilvl", 0)))] = (
                    f.get(W + "val") if f is not None else "bullet")
        out = {}
        for n in root.iter(W + "num"):
            a = n.find(W + "abstractNumId")
            if a is not None:
                for (aid, lvl), fmt in abstract.items():
                    if aid == a.get(W + "val"):
                        out[(n.get(W + "numId"), lvl)] = fmt
        return out

    def html(self):
        body = self._xml("word/document.xml").find(W + "body")
        return "".join(self._block(el) for el in body)

    def _block(self, el):
        if el.tag == W + "p":
            return self._para(el)
        if el.tag == W + "tbl":
            return self._table(el)
        if el.tag == W + "sdt":
            content = el.find(W + "sdtContent")
            return "".join(self._block(c) for c in content) if content is not None else ""
        return ""

    def _para(self, p):
        style, align, marker, indent = "", "", "", 0
        ppr = p.find(W + "pPr")
        if ppr is not None:
            ps = ppr.find(W + "pStyle")
            style = self.styles.get(ps.get(W + "val"), "") if ps is not None else ""
            jc = ppr.find(W + "jc")
            if jc is not None:
                align = {"center": "center", "right": "right", "end": "right", "both": "justify"}.get(jc.get(W + "val"), "")
            num = ppr.find(W + "numPr")
            if num is not None:
                marker, indent = self._marker(num)
        text = "".join(self._run(r) for r in p.iter(W + "r")) or "&nbsp;"
        css = f"margin:0 0 8px {indent}px;" + (f" text-align:{align};" if align else "")
        if style == "title":
            return f'<h1 style="{css} font-size:24pt; font-weight:400;">{text}</h1>'
        if style == "subtitle":
            return f'<p style="{css} font-size:14pt; color:#5e5c64;">{text}</p>'
        if style.startswith("heading ") and style[8:].isdigit():
            level = min(6, int(style[8:]))
            return f'<h{level} style="{css} margin-top:12px; color:#1a5fb4;">{text}</h{level}>'
        return f'<p style="{css}">{marker}{text}</p>'

    def _marker(self, numpr):
        nid, lvl = numpr.find(W + "numId"), numpr.find(W + "ilvl")
        nid = nid.get(W + "val") if nid is not None else None
        lvl = int(lvl.get(W + "val", 0)) if lvl is not None else 0
        if nid in (None, "0"):
            return "", 0
        key = (nid, lvl)
        self.counters[key] = self.counters.get(key, 0) + 1
        for k in [k for k in self.counters if k[0] == nid and k[1] > lvl]:
            del self.counters[k]
        fmt = self.numbering.get(key, "bullet")
        mark = "•" if fmt in ("bullet", "none") else _list_number(self.counters[key], fmt) + "."
        return mark + " ", 24 * (lvl + 1)

    def _run(self, r):
        parts = []
        for ch in r:
            if ch.tag == W + "t":
                parts.append(html.escape(ch.text or ""))
            elif ch.tag == W + "tab":
                parts.append("&emsp;")
            elif ch.tag in (W + "br", W + "cr") and ch.get(W + "type") != "page":
                parts.append("<br>")
            elif ch.tag == W + "noBreakHyphen":
                parts.append("‑")
            elif ch.tag == W + "drawing":
                parts.append(self._image(ch))
        s = "".join(parts)
        rpr = r.find(W + "rPr")
        if not s or rpr is None:
            return s
        if _on(rpr, "b"):
            s = f"<b>{s}</b>"
        if _on(rpr, "i"):
            s = f"<i>{s}</i>"
        u = rpr.find(W + "u")
        if u is not None and u.get(W + "val", "single") != "none":
            s = f"<u>{s}</u>"
        if _on(rpr, "strike") or _on(rpr, "dstrike"):
            s = f"<s>{s}</s>"
        va = rpr.find(W + "vertAlign")
        if va is not None and va.get(W + "val") in ("superscript", "subscript"):
            tag = "sup" if va.get(W + "val") == "superscript" else "sub"
            s = f"<{tag}>{s}</{tag}>"
        css = []
        color = rpr.find(W + "color")
        if color is not None and re.fullmatch(r"[0-9A-Fa-f]{6}", color.get(W + "val", "")):
            css.append(f"color:#{color.get(W + 'val')}")
        sz = rpr.find(W + "sz")
        if sz is not None and sz.get(W + "val", "").isdigit():
            css.append(f"font-size:{int(sz.get(W + 'val')) / 2:g}pt")
        hl = rpr.find(W + "highlight")
        if hl is not None and hl.get(W + "val", "none").isalpha() and hl.get(W + "val") != "none":
            css.append(f"background-color:{hl.get(W + 'val')}")
        return f'<span style="{";".join(css)}">{s}</span>' if css else s

    def _image(self, drawing):
        blip = next(drawing.iter(A + "blip"), None)
        target = self.rels.get(blip.get(R_ID + "embed")) if blip is not None else None
        if not target:
            return ""
        try:
            img = QImage.fromData(self.z.read(target))
        except KeyError:
            return ""
        if img.isNull():
            return ""
        extent = next(drawing.iter(WP + "extent"), None)
        w = int(extent.get("cx", 0)) // 9525 if extent is not None else img.width()   # EMU -> px
        w = max(1, min(w or img.width(), 640))
        name = f"docx-image-{len(self.images)}"
        self.images[name] = img
        return f'<img src="{name}" width="{w}" height="{max(1, round(w * img.height() / img.width()))}">'

    def _table(self, tbl):
        rows = []
        for tr in tbl.iter(W + "tr"):
            cells = []
            for tc in tr.findall(W + "tc"):
                span = tc.find(f"{W}tcPr/{W}gridSpan")
                colspan = f' colspan="{span.get(W + "val")}"' if span is not None else ""
                cells.append(f"<td{colspan}>{''.join(self._block(el) for el in tc)}</td>")
            rows.append(f"<tr>{''.join(cells)}</tr>")
        return ('<table border="1" cellspacing="0" cellpadding="5" width="100%"'
                f' style="border-collapse:collapse; border-color:#c0bfbc; margin:4px 0 10px 0;">{"".join(rows)}</table>')


_BUILTIN_NUMBERS = {1: ("number", 0, False), 2: ("number", 2, False), 3: ("number", 0, True),
                    4: ("number", 2, True), 9: ("percent", 0), 10: ("percent", 2)}


def _format_kind(fid, code):
    """Classifies an Excel number format: "date" / "time" / "datetime", ("percent", decimals),
    ("number", decimals, thousands separator) or None (general)."""
    if code is None:
        if fid == 22:
            return "datetime"
        if 14 <= fid <= 17 or 27 <= fid <= 36 or 50 <= fid <= 58:
            return "date"
        if 18 <= fid <= 21 or 45 <= fid <= 47:
            return "time"
        return _BUILTIN_NUMBERS.get(fid)
    c = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.|_.|\*.', "", code).lower().split(";")[0]
    decimals = len(m.group(1)) if (m := re.search(r"[0#]\.([0#]+)", c)) else 0
    if "%" in c:
        return ("percent", decimals)
    has_date, has_time = "d" in c or "y" in c, "h" in c or "s" in c
    if has_date:
        return "datetime" if has_time else "date"
    if has_time:
        return "time"
    if re.search(r"[0#]", c) and "e" not in c:
        return ("number", decimals, "," in c)
    return None


class _Xlsx:
    def __init__(self, z):
        self.z = z
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = _rels(z, "xl/workbook.xml")
        pr = wb.find(S + "workbookPr")
        self.epoch = datetime(1904, 1, 1) if pr is not None and pr.get("date1904") in ("1", "true") else datetime(1899, 12, 30)
        self.sheets = [(s.get("name"), rels.get(s.get(R_ID + "id"))) for s in wb.iter(S + "sheet")
                       if s.get("state") not in ("hidden", "veryHidden")]
        self.sheets = [(n, t) for n, t in self.sheets if t]
        self.strings = self._strings()
        self.formats = self._formats()

    def _strings(self):
        try:
            f = self.z.open("xl/sharedStrings.xml")
        except KeyError:
            return []
        out = []
        for _, el in ET.iterparse(f):
            if el.tag == S + "si":
                t = el.find(S + "t")
                out.append((t.text or "") if t is not None else "".join(x.text or "" for x in el.iterfind(f"{S}r/{S}t")))
                el.clear()
        return out

    def _formats(self):
        try:
            root = ET.fromstring(self.z.read("xl/styles.xml"))
        except KeyError:
            return []
        custom = {n.get("numFmtId"): n.get("formatCode", "") for n in root.iterfind(f"{S}numFmts/{S}numFmt")}
        out = []
        for xf in root.iterfind(f"{S}cellXfs/{S}xf"):
            fid = xf.get("numFmtId", "0")
            out.append(_format_kind(int(fid) if fid.isdigit() else 0, custom.get(fid)))
        return out

    def _number(self, v, kind):
        x = float(v)
        if kind in ("date", "datetime", "time"):
            try:
                d = self.epoch + timedelta(days=x)
            except OverflowError:
                return v
            return d.strftime({"date": "%Y-%m-%d", "time": "%H:%M:%S", "datetime": "%Y-%m-%d %H:%M"}[kind])
        if isinstance(kind, tuple) and kind[0] == "percent":
            return f"{x * 100:.{kind[1]}f}%"
        if isinstance(kind, tuple):
            return f"{x:,.{kind[1]}f}" if kind[2] else f"{x:.{kind[1]}f}"
        return str(int(x)) if x.is_integer() and abs(x) < 1e15 else f"{x:.10g}"

    def _cell(self, c):
        t = c.get("t", "n")
        if t == "inlineStr":
            return "".join(x.text or "" for x in c.iter(S + "t")), False
        v = c.find(S + "v")
        if v is None or v.text is None:
            return "", False
        v = v.text
        if t == "s":
            i = int(v)
            return (self.strings[i] if 0 <= i < len(self.strings) else ""), False
        if t == "b":
            return ("TRUE" if v == "1" else "FALSE"), False
        if t in ("e", "str"):
            return v, False
        s = c.get("s", "")
        kind = self.formats[int(s)] if s.isdigit() and int(s) < len(self.formats) else None
        try:
            return self._number(v, kind), True
        except ValueError:
            return v, False

    def sheet(self, index):
        """(cells {(row, col): (text, numeric)}, rows, cols, spans, truncated)"""
        cells, spans, truncated = {}, [], False
        rows = cols = 0
        row = -1
        for _, el in ET.iterparse(self.z.open(self.sheets[index][1])):
            if el.tag == S + "row":
                row = int(el.get("r")) - 1 if el.get("r", "").isdigit() else row + 1
                if row >= MAX_ROWS:
                    truncated = True
                    break
                col = -1
                for c in el.iterfind(S + "c"):
                    col = _col_index(c.get("r")) if c.get("r") else col + 1
                    if col >= MAX_COLS:
                        truncated = True
                        continue
                    text, numeric = self._cell(c)
                    if text:
                        cells[(row, col)] = (text.replace("\n", " "), numeric)
                        rows, cols = max(rows, row + 1), max(cols, col + 1)
                el.clear()
            elif el.tag == S + "mergeCell" and ":" in el.get("ref", ""):
                (r0, c0), (r1, c1) = (_cell_ref(x) for x in el.get("ref").split(":"))
                spans.append((r0, c0, r1 - r0 + 1, c1 - c0 + 1))
        spans = [s for s in spans if s[0] < rows and s[1] < cols]
        return cells, rows, cols, spans, truncated


# ------------------------------------------------------------------ content widgets

class _ImageView(QWidget):
    """Draws an image or animation scaled down to fit, never up."""

    def __init__(self, image=None, movie=None):
        super().__init__()
        self.image, self.movie = image, movie
        self._scaled = None
        if movie is not None:
            movie.setParent(self)
            movie.frameChanged.connect(self.update)
            movie.start()

    def _target(self, size):
        return size.scaled(self.size(), Qt.KeepAspectRatio) if size.width() > self.width() or size.height() > self.height() else size

    def resizeEvent(self, e):
        self._scaled = None
        super().resizeEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        if self.movie is not None:
            pm = self.movie.currentPixmap()
            if pm.isNull():
                return
            size = self._target(pm.size())
            p.setRenderHint(QPainter.SmoothPixmapTransform)
        else:
            size = self._target(self.image.deviceIndependentSize().toSize())
            if self._scaled is None:
                dpr = self.devicePixelRatioF()
                pm = QPixmap.fromImage(self.image.scaled(size * dpr, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                pm.setDevicePixelRatio(dpr)
                self._scaled = pm
            pm = self._scaled
        p.drawPixmap(QRect((self.width() - size.width()) // 2, (self.height() - size.height()) // 2,
                           size.width(), size.height()), pm)


class _MediaView(QWidget):
    """QMediaPlayer with a play button, seek bar and times."""

    details = Signal(str)

    def __init__(self, path, is_video, base_info):
        super().__init__()
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        class Video(QVideoWidget):
            clicked = Signal()

            def mousePressEvent(self, e):
                if e.button() == Qt.LeftButton:
                    self.clicked.emit()

        self.base_info = base_info
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        if is_video:
            self.video = Video()
            self.video.setStyleSheet("background: black; border-radius: 8px;")
            self.video.clicked.connect(self.toggle)
            self.player.setVideoOutput(self.video)
            v.addWidget(self.video, 1)
        else:
            self.video = None
            v.addStretch(1)
            self.art = QLabel()
            self.art.setAlignment(Qt.AlignCenter)
            self.art.setPixmap(icons.color_pixmap("audio", 128))
            v.addWidget(self.art)
            self.song = label("", title2=True, center=True, wrap=True)
            self.artist = label("", dim=True, center=True)
            v.addWidget(self.song)
            v.addWidget(self.artist)
            v.addStretch(1)
        self.error = label("", dim=True, center=True, wrap=True)
        self.error.hide()
        v.addWidget(self.error)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.play = header_button("media-playback-pause", "Pause")
        self.play.setProperty("circular", True)
        self.play.clicked.connect(self.toggle)
        bar.addWidget(self.play)
        self.elapsed = label("0:00", caption=True)
        bar.addWidget(self.elapsed)
        self.seek = slider(0, 0, 0)
        self.seek.sliderMoved.connect(self.player.setPosition)
        bar.addWidget(self.seek, 1)
        self.total = label("0:00", caption=True)
        bar.addWidget(self.total)
        v.addLayout(bar)

        self.player.durationChanged.connect(self._duration)
        self.player.positionChanged.connect(self._position)
        self.player.playbackStateChanged.connect(self._state)
        self.player.errorOccurred.connect(self._error)
        self.player.metaDataChanged.connect(self._meta)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def toggle(self):
        from PySide6.QtMultimedia import QMediaPlayer
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self):
        self.player.stop()
        self.player.setSource(QUrl())

    def _state(self, state):
        from PySide6.QtMultimedia import QMediaPlayer
        playing = state == QMediaPlayer.PlayingState
        set_symbolic(self.play, "media-playback-pause" if playing else "media-playback-start")
        self.play.setToolTip("Pause" if playing else "Play")

    def _duration(self, ms):
        self.seek.setRange(0, ms)
        self.total.setText(_duration(ms))

    def _position(self, ms):
        if not self.seek.isSliderDown():
            self.seek.setValue(ms)
        self.elapsed.setText(_duration(ms))

    def _error(self, _err, message):
        self.error.setText(f"This file can't be played. {message}".strip())
        self.error.show()

    def _meta(self):
        from PySide6.QtMultimedia import QMediaMetaData
        md = self.player.metaData()
        parts = [_duration(self.player.duration())] if self.player.duration() > 0 else []
        res = md.value(QMediaMetaData.Key.Resolution)
        if isinstance(res, QSize) and res.isValid():
            parts.append(f"{res.width()} × {res.height()}")
        self.details.emit(SEP.join(parts + [self.base_info]))
        if self.video is None:
            self.song.setText(md.stringValue(QMediaMetaData.Key.Title))
            self.artist.setText(md.stringValue(QMediaMetaData.Key.ContributingArtist)
                                or md.stringValue(QMediaMetaData.Key.AlbumArtist))
            for key in (QMediaMetaData.Key.CoverArtImage, QMediaMetaData.Key.ThumbnailImage):
                art = md.value(key)
                if isinstance(art, QImage) and not art.isNull():
                    pm = QPixmap.fromImage(art.scaled(QSize(220, 220) * self.devicePixelRatioF(), Qt.KeepAspectRatio,
                                                      Qt.SmoothTransformation))
                    pm.setDevicePixelRatio(self.devicePixelRatioF())
                    self.art.setPixmap(pm)
                    break


class _Page(QTextBrowser):
    """A white sheet of paper that serves the document's embedded images."""

    scrolls = True

    def __init__(self):
        super().__init__()
        self.images = {}
        self.setOpenLinks(False)
        families = set(QFontDatabase.families())
        font = QFont(next((f for f in ("Calibri", "Aptos", "Segoe UI") if f in families), "Segoe UI"))
        font.setPointSizeF(11)
        self.document().setDefaultFont(font)
        self.document().setDocumentMargin(44)
        self.setStyleSheet(f"QTextBrowser {{ background: #ffffff; color: #1e1e1e; border-radius: 8px;"
                           f" border: 1px solid {theme.rgba(theme.current().border)}; }}")

    def loadResource(self, kind, url):
        img = self.images.get(url.toString())
        return img if img is not None else super().loadResource(kind, url)


class _GridModel(QAbstractTableModel):
    def __init__(self, cells, rows, cols):
        super().__init__()
        self.cells, self.rows, self.cols = cells, rows, cols

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.rows

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.cols

    def data(self, index, role=Qt.DisplayRole):
        cell = self.cells.get((index.row(), index.column()))
        if cell is None:
            return None
        if role == Qt.DisplayRole:
            return cell[0]
        if role == Qt.TextAlignmentRole:
            return (Qt.AlignRight if cell[1] else Qt.AlignLeft) | Qt.AlignVCenter
        if role == Qt.ToolTipRole and len(cell[0]) > 30:
            return cell[0]
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        return _col_name(section) if orientation == Qt.Horizontal else str(section + 1)


class _SheetView(QWidget):
    """Spreadsheet grid with Excel-style sheet tabs underneath."""

    scrolls = True

    def __init__(self, names, load, closer=None):
        super().__init__()
        self.load, self.closer = load, closer
        t = theme.current()
        grid = theme.rgba(t.fg_alpha(0.12))
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.table = QTableView()
        self.table.setEditTriggers(QTableView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setCornerButtonEnabled(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.horizontalHeader().setResizeContentsPrecision(200)
        self.table.setStyleSheet(
            f"QTableView {{ background: {theme.rgba(t.view_bg)}; color: {theme.rgba(t.fg)}; gridline-color: {grid};"
            f" border: 1px solid {theme.rgba(t.border)}; border-radius: 8px;"
            f" selection-background-color: {theme.rgba(t.accent_alpha(0.25))}; selection-color: {theme.rgba(t.fg)}; }}"
            f"QHeaderView::section {{ background: {theme.rgba(t.fg_alpha(0.05))}; color: {theme.rgba(t.dim_fg)};"
            f" border: none; border-right: 1px solid {grid}; border-bottom: 1px solid {grid}; padding: 2px 6px; }}")
        v.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setShape(QTabBar.RoundedSouth)
        self.tabs.setExpanding(False)
        self.tabs.setFocusPolicy(Qt.NoFocus)
        for n in names:
            self.tabs.addTab(n)
        self.tabs.setVisible(len(names) > 1)
        row.addWidget(self.tabs)
        row.addStretch(1)
        self.note = label("", caption=True, dim=True)
        row.addWidget(self.note)
        v.addLayout(row)
        self.tabs.currentChanged.connect(self._show)
        self._show(0)

    def _show(self, index):
        cells, rows, cols, spans, truncated = self.load(index)
        self.table.setModel(_GridModel(cells, rows, cols))
        self.table.clearSpans()
        for r, c, h, w in spans:
            self.table.setSpan(r, c, h, w)
        self.table.resizeColumnsToContents()
        for c in range(cols):
            self.table.setColumnWidth(c, max(48, min(320, self.table.columnWidth(c))))
        self.note.setText(f"Showing the first {MAX_ROWS:,} rows and {MAX_COLS} columns" if truncated else
                          "Empty sheet" if not cells else "")

    def stop(self):
        if self.closer:
            self.closer()


_retired_docs = []


def _pdf_view(doc, whole_pages=False):
    from PySide6.QtPdfWidgets import QPdfView
    view = QPdfView()
    doc.setParent(view)
    view.setDocument(doc)

    def stop():
        # Deleting a QPdfView together with its document crashes Qt; detach it and let it go later.
        view.setDocument(None)
        doc.setParent(None)
        _retired_docs.append(doc)
        QTimer.singleShot(1000, lambda: (_retired_docs.remove(doc), doc.deleteLater()))

    view.stop = stop
    view.setPageMode(QPdfView.PageMode.MultiPage)
    view.setZoomMode(QPdfView.ZoomMode.FitInView if whole_pages else QPdfView.ZoomMode.FitToWidth)
    view.setPageSpacing(12)
    view.setStyleSheet(f"QPdfView {{ background: {theme.rgba(theme.current().fg_alpha(0.06))};"
                       " border: none; border-radius: 8px; }")
    view.scrolls = True
    return view


def _text_edit(text, wrap):
    edit = QPlainTextEdit(text)
    edit.setReadOnly(True)
    edit.setLineWrapMode(QPlainTextEdit.WidgetWidth if wrap else QPlainTextEdit.NoWrap)
    t = theme.current()
    edit.setStyleSheet(f"QPlainTextEdit {{ background: {theme.rgba(t.view_bg)}; border-radius: 8px; padding: 8px;"
                       f" border: 1px solid {theme.rgba(t.border)}; font-family: Consolas, 'Cascadia Mono', monospace; }}")
    edit.scrolls = True
    return edit


# ------------------------------------------------------------------ the dialog

class QuickLook(Sheet):
    """`items` is the list Left/Right walk through; `on_step(item)` is told when it moves;
    `source(item)` gives the item's icon rect on screen to zoom out of and back into."""

    def __init__(self, parent, items, index=0, on_step=None, source=None):
        super().__init__(parent, "", 400)
        self.items, self.index, self.on_step, self.source = items, index, on_step, source
        self.content = None
        self.layout().setSizeConstraint(QLayout.SetFixedSize)
        self.body.setContentsMargins(14, 4, 14, 12)
        self.body.setSpacing(8)
        self.prev = header_button("go-previous", "Previous")
        self.next = header_button("go-next", "Next")
        self.prev.clicked.connect(lambda: self._step(-1))
        self.next.clicked.connect(lambda: self._step(1))
        self.header_start.addWidget(self.prev)
        self.header_start.addWidget(self.next)
        self.open = header_button("external-link", "Open With Default App")
        self.open.clicked.connect(self._open)
        self.header_end.insertWidget(0, self.open)
        self.info = label("", caption=True, dim=True, center=True)
        self.body.addWidget(self.info)
        multi = len(items) > 1
        self.prev.setVisible(multi)
        self.next.setVisible(multi)
        if not multi:
            self.header_start.addSpacing(34)
        self._load()

    # --- keyboard: content widgets would otherwise swallow arrows and Space
    def showEvent(self, e):
        QApplication.instance().installEventFilter(self)
        super().showEvent(e)

    def closing(self):
        QApplication.instance().removeEventFilter(self)
        self._clear()

    def zoom_rect(self):
        return self.source(self.item) if self.source else None

    def eventFilter(self, obj, e):
        if (e.type() == QEvent.KeyPress and isinstance(obj, QWidget) and obj.window() is self
                and not e.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            key = e.key()
            if key == Qt.Key_Space and e.isAutoRepeat():    # the Space that opened us, still held down
                return True
            if key in (Qt.Key_Space, Qt.Key_Escape):
                self.reject()
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._open()
                return True
            if key in (Qt.Key_Left, Qt.Key_Right) or (
                    key in (Qt.Key_Up, Qt.Key_Down) and not getattr(self.content, "scrolls", False)):
                self._step(-1 if key in (Qt.Key_Left, Qt.Key_Up) else 1)
                return True
        return super().eventFilter(obj, e)

    # --- navigation
    @property
    def item(self):
        return self.items[self.index]

    def _step(self, delta):
        i = max(0, min(len(self.items) - 1, self.index + delta))
        if i == self.index:
            return
        self._finish_animation()
        before, rect = self.grab(), self.geometry()
        self.index = i
        self._load()
        self._center()
        if self.on_step:
            self.on_step(self.item)
        self.morph_from(before, rect)

    def _open(self):
        platform_win.open_default(self.item.open_path)
        self.accept()

    def _center(self):
        self.adjustSize()
        top = self.parentWidget().window() if self.parentWidget() else None
        if top is not None:
            g = top.frameGeometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)

    def _bounds(self):
        top = self.parentWidget().window() if self.parentWidget() else self
        geo = (top.screen() or QApplication.primaryScreen()).availableGeometry()
        return min(1200, geo.width() - 140), min(860, geo.height() - 140)

    # --- content
    def _clear(self):
        if self.content is not None:
            if hasattr(self.content, "stop"):
                self.content.stop()
            self.body.removeWidget(self.content)
            self.content.deleteLater()
            self.content = None

    def _load(self):
        self._clear()
        item = self.item
        self.prev.setEnabled(self.index > 0)
        self.next.setEnabled(self.index < len(self.items) - 1)
        self.open.setVisible(not item.drive)
        try:
            widget, details, size = self._build(item)
        except Exception:   # a malformed file must never take the window down with it
            widget, details, size = self._fallback(item, "This file couldn't be previewed.")
        self.content = widget
        self.body.insertWidget(0, widget, 1)
        if size is None:
            self.card.setMinimumSize(0, 0)
            self.card.setMaximumSize(16777215, 16777215)
            self.card.setFixedWidth(400)
        else:
            self.card.setFixedSize(*size)
        self._set_info(details)
        if isinstance(widget, _MediaView):
            widget.details.connect(self._set_info)
        width = size[0] if size else 400
        fm = self.title_label.fontMetrics()
        self.title_label.setText(fm.elidedText(item.name, Qt.ElideMiddle, width - 190))
        self.title_label.setToolTip(item.name)

    def _set_info(self, details):
        pos = f"{self.index + 1} of {len(self.items)}" if len(self.items) > 1 else ""
        self.info.setText(SEP.join(p for p in (pos, details) if p))

    def _build(self, item):
        if item.drive or item.opens_as_folder:
            return self._fallback(item)
        path = item.open_path
        name = os.path.basename(path) if item.target else item.name
        ext, cat = icons.extension(name), icons.categorize(name)
        size = model.format_size(item.size)
        max_w, max_h = self._bounds()
        doc_size = (min(max_w, 900), max_h)

        box = QSize(max_w - 28, max_h - 90)
        dpr = self.devicePixelRatioF()

        def picture(view, shown, details):
            fit = shown.scaled(box, Qt.KeepAspectRatio) if shown.width() > box.width() or shown.height() > box.height() else shown
            return view, details, (max(380, fit.width() + 28), max(260, fit.height() + 90))

        if ext == "pdf":
            from PySide6.QtPdf import QPdfDocument
            doc = QPdfDocument()
            err = doc.load(path)
            if err == QPdfDocument.Error.IncorrectPassword:
                doc.deleteLater()
                return self._fallback(item, "This PDF is password protected.")
            if doc.status() != QPdfDocument.Status.Ready:
                doc.deleteLater()
                return self._fallback(item, "This PDF couldn't be opened.")
            n = doc.pageCount()
            return _pdf_view(doc), SEP.join((f"{n} page{'s' if n != 1 else ''}", size)), doc_size

        if ext in ("psd", "psb"):
            img, info = adobe.read_psd(path, int(max(box.width(), box.height()) * dpr))
            if img is None:
                return self._fallback(item, "This document has no flattened preview to show.")
            details = SEP.join((f"{info.width} × {info.height}", info.describe(), size))
            if info.thumbnail_only:
                details += SEP + "thumbnail only (saved without Maximize Compatibility)"
            img.setDevicePixelRatio(dpr)
            return picture(_ImageView(image=img), img.deviceIndependentSize().toSize(), details)

        if ext == "ai":
            doc, thumb = adobe.open_ai(path)
            if doc is None:
                if thumb is None:
                    return self._fallback(item, "This file was saved without PDF content.")
                return picture(_ImageView(image=thumb), thumb.size(), SEP.join(("thumbnail only", size)))
            pt = doc.pagePointSize(0)
            dims = f"{pt.width():.0f} × {pt.height():.0f}"
            n = doc.pageCount()
            if n > 1:
                return _pdf_view(doc, whole_pages=True), SEP.join((f"{n} artboards", dims, size)), doc_size
            shown = pt.toSize().scaled(box, Qt.KeepAspectRatio)    # vector art: fill the window
            art = doc.render(0, shown * dpr)
            doc.deleteLater()
            img = QImage(art.size(), QImage.Format_RGB32)          # artboards are white in Illustrator
            img.fill(Qt.white)
            p = QPainter(img)
            p.drawImage(0, 0, art)
            p.end()
            img.setDevicePixelRatio(dpr)
            return picture(_ImageView(image=img), shown, SEP.join((dims, size)))

        if cat == "image" or ext.encode() in QImageReader.supportedImageFormats():
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            natural = reader.size()
            dims = f"{natural.width()} × {natural.height()}" if natural.isValid() else ""
            if reader.supportsAnimation() and reader.imageCount() > 1:
                view, shown = _ImageView(movie=QMovie(path)), natural
            else:
                if natural.isValid() and (natural.width() > box.width() * 2 or natural.height() > box.height() * 2):
                    reader.setScaledSize(natural.scaled(box * 2, Qt.KeepAspectRatio))
                img = reader.read()
                if img.isNull():    # HEIC, AVIF, camera RAW…: decode with Windows' installed codecs
                    img = platform_win.shell_thumbnail(path, int(max(box.width(), box.height()) * dpr))
                    if img is None:
                        return self._fallback(item, "Windows has no decoder for this image format installed.")
                    img.setDevicePixelRatio(dpr)
                    view = _ImageView(image=img)
                    return picture(view, img.deviceIndependentSize().toSize(),
                                   SEP.join((f"{ext.upper()} image", size)))
                if not dims:
                    dims = f"{img.width()} × {img.height()}"
                view, shown = _ImageView(image=img), img.size()
            return picture(view, shown, SEP.join(p for p in (dims, size) if p))

        if cat in ("video", "audio"):
            is_video = cat == "video"
            view = _MediaView(path, is_video, size)
            w = min(max_w, 960)
            return view, size, (w, min(max_h, w * 9 // 16 + 140)) if is_video else (420, 440)

        if ext in ("docx", "docm", "dotx"):
            page = _Page()
            with zipfile.ZipFile(path) as z:
                page.setHtml(_Docx(z, page.images).html())
            return page, SEP.join(("Word document", size)), doc_size

        if ext in ("xlsx", "xlsm", "xltx"):
            z = zipfile.ZipFile(path)
            try:
                book = _Xlsx(z)
            except Exception:
                z.close()
                raise
            n = len(book.sheets)
            view = _SheetView([s[0] for s in book.sheets], book.sheet, z.close)
            return view, SEP.join((f"{n} sheet{'s' if n != 1 else ''}", size)), (min(max_w, 1000), max_h)

        if ext in ("csv", "tsv"):
            text, _ = _read_text(path, 4_000_000)
            try:
                delim = "\t" if ext == "tsv" else csv.Sniffer().sniff(text[:8192], ",;\t|").delimiter
            except csv.Error:
                delim = ","
            cells, rows, cols, truncated = {}, 0, 0, False
            for r, record in enumerate(csv.reader(io.StringIO(text), delimiter=delim)):
                if r >= MAX_ROWS:
                    truncated = True
                    break
                for c, value in enumerate(record[:MAX_COLS]):
                    if value:
                        try:
                            float(value)
                            numeric = True
                        except ValueError:
                            numeric = False
                        cells[(r, c)] = (value.replace("\n", " "), numeric)
                        rows, cols = max(rows, r + 1), max(cols, c + 1)
            view = _SheetView([], lambda _i: (cells, rows, cols, [], truncated))
            return view, SEP.join((f"{rows:,} row{'s' if rows != 1 else ''}", size)), (min(max_w, 1000), max_h)

        if ext in ("md", "markdown"):
            text, cut = _read_text(path)
            view = QTextBrowser()
            view.setOpenExternalLinks(True)
            view.setMarkdown(text)
            view.document().setDocumentMargin(24)
            t = theme.current()
            view.setStyleSheet(f"QTextBrowser {{ background: {theme.rgba(t.view_bg)}; border-radius: 8px;"
                               f" border: 1px solid {theme.rgba(t.border)}; }}")
            view.scrolls = True
            return view, SEP.join(p for p in ("Markdown", size, "beginning only" if cut else "") if p), doc_size

        if cat in ("text", "code", "web") or ext in TEXT_EXTS or (not ext and _looks_text(path)):
            text, cut = _read_text(path)
            view = _text_edit(text, wrap=cat == "text" or not ext)
            lines = text.count("\n") + 1
            info = f"{lines:,}+ lines" if cut else f"{lines:,} line{'s' if lines != 1 else ''}"
            return view, SEP.join((info, size)), doc_size

        return self._fallback(item)

    def _fallback(self, item, note=""):
        from .views import item_pixmap
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 4)
        v.setSpacing(6)
        pm, _ = item_pixmap(item, 128)
        icon = QLabel()
        icon.setPixmap(pm if isinstance(pm, QPixmap) else QPixmap.fromImage(pm).scaled(
            128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        icon.setAlignment(Qt.AlignCenter)
        v.addWidget(icon)
        v.addWidget(label(item.name, title2=True, center=True, wrap=True))
        kind = "Drive" if item.drive else icons.type_description(item.name, item.opens_as_folder)
        v.addWidget(label(kind if item.opens_as_folder or item.drive else f"{kind} — {model.format_size(item.size)}",
                          dim=True, center=True))
        if not item.drive:
            v.addWidget(label(f"Modified {model.format_date(item.mtime, True)}", caption=True, center=True))
        if note:
            v.addWidget(label(note, caption=True, dim=True, center=True, wrap=True))
        return w, "", None
