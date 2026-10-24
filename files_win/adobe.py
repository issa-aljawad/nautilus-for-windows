"""Photoshop (.psd/.psb) and Illustrator (.ai) previews without Adobe software.

Photoshop files end with a flattened copy of the document (the "composite"
saved by Maximize Compatibility). Its rows are PackBits-compressed exactly like
TIFF strips, so the rows we want are wrapped in an in-memory TIFF and decoded by
Qt's TIFF reader; only every Nth row is read, which keeps multi-gigabyte PSBs
fast. Illustrator files are PDF-compatible and render through QtPdf.
Everything here only touches QImage, so it is safe on worker threads too."""

import base64
import math
import re
import struct
import sys
from array import array
from itertools import accumulate

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage

MODE_NAMES = {0: "Bitmap", 1: "Grayscale", 2: "Indexed", 3: "RGB", 4: "CMYK", 7: "Multichannel",
              8: "Duotone", 9: "Lab"}
# Photoshop color mode -> (TIFF photometric interpretation, color channels)
_PHOTOMETRIC = {0: (0, 1), 1: (1, 1), 2: (3, 1), 3: (2, 3), 4: (5, 4), 7: (1, 1), 8: (1, 1), 9: (8, 3)}


class PsdInfo:
    def __init__(self, width, height, depth, mode, version):
        self.width, self.height, self.depth, self.mode, self.version = width, height, depth, mode, version
        self.thumbnail_only = False

    def describe(self):
        return f"{MODE_NAMES.get(self.mode, 'Unknown')}, {self.depth}-bit"


def _tiff(width, height, bits, photometric, samples, strips, compression, colormap=None, sample_format=1):
    """A big-endian, planar TIFF around ready-made strips (one row of one channel each)."""
    tags = [(256, 4, [width]), (257, 4, [height]), (258, 3, [bits] * samples), (259, 3, [compression]),
            (262, 3, [photometric]), (273, 4, [0] * len(strips)), (277, 3, [samples]), (278, 4, [1]),
            (279, 4, [len(s) for s in strips]), (284, 3, [2])]
    if colormap:
        tags.append((320, 3, colormap))
    if sample_format != 1:
        tags.append((339, 3, [sample_format] * samples))
    tags.sort()

    def packed(kind, values):
        return struct.pack(f">{len(values)}{'H' if kind == 3 else 'I'}", *values)

    ifd_end = 8 + 2 + 12 * len(tags) + 4
    data_start = ifd_end + sum(len(packed(k, v)) for _, k, v in tags if len(packed(k, v)) > 4)
    tags = [(t, k, list(accumulate((len(s) for s in strips[:-1]), initial=data_start)) if t == 273 else v)
            for t, k, v in tags]
    out = bytearray(b"MM\0*" + struct.pack(">IH", 8, len(tags)))
    extra = bytearray()
    for tag, kind, values in tags:
        data = packed(kind, values)
        if len(data) <= 4:
            out += struct.pack(">HHI", tag, kind, len(values)) + data.ljust(4, b"\0")
        else:
            out += struct.pack(">HHII", tag, kind, len(values), ifd_end + len(extra))
            extra += data
    out += b"\0\0\0\0" + extra
    return bytes(out) + b"".join(strips)


def _flat(img):
    small = img.scaled(8, 8)
    return len({small.pixel(x, y) for x in range(8) for y in range(8)}) == 1


def _composite(f, info, channels, palette, max_side):
    photometric, used = _PHOTOMETRIC.get(info.mode, (None, 0))
    if photometric is None or channels < used:
        return None
    width, height, depth = info.width, info.height, info.depth
    compression = struct.unpack(">H", f.read(2))[0]
    step = max(1, math.ceil(max(width, height) / max_side))
    rows = range(0, height, step)
    strips = []
    if compression == 1:        # PackBits, preceded by every row's compressed length
        table = array("I" if info.version == 2 else "H")
        table.frombytes(f.read(table.itemsize * channels * height))
        if sys.byteorder == "little":
            table.byteswap()
        offsets = list(accumulate(table[:used * height], initial=f.tell()))
        if step == 1:
            block = f.read(offsets[-1] - offsets[0])
            base = offsets[0]
            strips = [block[offsets[i] - base:offsets[i + 1] - base] for i in range(used * height)]
        else:
            for c in range(used):
                for r in rows:
                    i = c * height + r
                    f.seek(offsets[i])
                    strips.append(f.read(table[i]))
        tiff_compression = 32773
    elif compression == 0:      # raw
        row_bytes = (width * depth + 7) // 8
        start = f.tell()
        for c in range(used):
            for r in rows:
                f.seek(start + (c * height + r) * row_bytes)
                strips.append(f.read(row_bytes))
        tiff_compression = 1
    else:                       # ZIP composites aren't written by Photoshop
        return None
    target = QSize(width, height)
    if max(width, height) > max_side:
        target = target.scaled(QSize(max_side, max_side), Qt.KeepAspectRatio)

    def decode(photometric, samples, parts, colormap=None):
        img = QImage.fromData(_tiff(width, len(rows), depth, photometric, samples, parts, tiff_compression,
                                    colormap, 3 if depth == 32 else 1))
        return None if img.isNull() else img.convertToFormat(
            QImage.Format_Grayscale8 if samples == 1 and photometric == 1 else QImage.Format_RGB32
        ).scaled(target, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    if used == 1 or (info.mode == 3 and depth == 8):
        colormap = [v * 257 for v in palette] if info.mode == 2 and len(palette) == 768 else None
        img = decode(photometric, used, strips, colormap)
        return None if img is None else img.convertToFormat(QImage.Format_RGB32)
    if info.mode not in (3, 4):
        return None
    # Qt's TIFF reader only handles interleaved samples above 8 bits (and CMYK needs
    # inverting), so decode each channel as grayscale and interleave them here.
    n = len(rows)
    w, h = target.width(), target.height()
    channels_8 = []
    for c in range(used):
        plane = decode(1, 1, strips[c * n:(c + 1) * n])
        if plane is None:
            return None
        bpl, raw = plane.bytesPerLine(), bytes(plane.constBits())
        channels_8.append(raw[:w * h] if bpl == w else b"".join(raw[y * bpl:y * bpl + w] for y in range(h)))
    out = bytearray(w * h * 4)
    if info.mode == 4:          # Photoshop stores CMYK inverted (255 = no ink)
        invert = bytes(range(255, -1, -1))
        for i, ch in enumerate(channels_8):
            out[i::4] = ch.translate(invert)
        return QImage(out, w, h, 4 * w, QImage.Format_CMYK8888).convertToFormat(QImage.Format_RGB32)
    out[0::4], out[1::4], out[2::4] = channels_8[2], channels_8[1], channels_8[0]
    out[3::4] = b"\xff" * (w * h)
    return QImage(out, w, h, 4 * w, QImage.Format_RGB32).copy()


def _thumbnail(f, start, length):
    """The JPEG thumbnail in image resource 1036 (about 160 px)."""
    end = start + length
    pos = start
    while pos + 12 <= end:
        f.seek(pos)
        head = f.read(7)
        if head[:4] != b"8BIM":
            return None
        rid, name_len = struct.unpack(">HB", head[4:7])
        pos += 6 + ((name_len + 2) & ~1)
        f.seek(pos)
        size = struct.unpack(">I", f.read(4))[0]
        pos += 4
        if rid == 1036 and size > 28:
            f.seek(pos + 28)
            img = QImage.fromData(f.read(size - 28))
            return None if img.isNull() else img.convertToFormat(QImage.Format_RGB32)
        pos += size + (size & 1)
    return None


def read_psd(path, max_side=2048):
    """(QImage or None, PsdInfo). Raises ValueError for files that aren't Photoshop documents."""
    with open(path, "rb") as f:
        sig, version, channels, height, width, depth, mode = struct.unpack(">4sH6xHIIHH", f.read(26))
        if sig != b"8BPS" or version not in (1, 2):
            raise ValueError("not a Photoshop document")
        info = PsdInfo(width, height, depth, mode, version)
        palette = f.read(struct.unpack(">I", f.read(4))[0])
        resources_len = struct.unpack(">I", f.read(4))[0]
        resources_at = f.tell()
        f.seek(resources_len, 1)
        size_fmt = ">Q" if version == 2 else ">I"
        f.seek(struct.unpack(size_fmt, f.read(struct.calcsize(size_fmt)))[0], 1)
        img = _composite(f, info, channels, palette, max_side)
        if img is None or _flat(img):   # no usable composite: saved without Maximize Compatibility
            thumb = _thumbnail(f, resources_at, resources_len)
            if thumb is not None:
                img, info.thumbnail_only = thumb, True
    return img, info


def _xmp_thumbnail(path):
    with open(path, "rb") as f:
        head = f.read(8_000_000)
    m = re.search(rb"<xmpGImg:image>(.*?)</xmpGImg:image>", head, re.S)
    if not m:
        return None
    img = QImage.fromData(base64.b64decode(m.group(1).replace(b"&#xA;", b"").replace(b"\n", b"")))
    return None if img.isNull() else img


def ai_thumbnail(path, size):
    """The first artboard on white, fitted in size×size (or Illustrator's own thumbnail)."""
    doc, thumb = open_ai(path)
    if doc is None:
        return thumb
    try:
        target = doc.pagePointSize(0).toSize().scaled(QSize(size, size), Qt.KeepAspectRatio)
        art = doc.render(0, target)
    finally:
        doc.close()
    if art.isNull():
        return None
    img = QImage(art.size(), QImage.Format_RGB32)
    img.fill(Qt.white)
    from PySide6.QtGui import QPainter
    p = QPainter(img)
    p.drawImage(0, 0, art)
    p.end()
    return img


def open_ai(path):
    """(QPdfDocument or None, fallback QImage or None) for an Illustrator file."""
    from PySide6.QtPdf import QPdfDocument
    doc = QPdfDocument()
    doc.load(path)
    if doc.status() == QPdfDocument.Status.Ready and doc.pageCount():
        if "saved without PDF Content" not in doc.getAllText(0).text():
            return doc, None
    doc.deleteLater()
    return None, _xmp_thumbnail(path)
