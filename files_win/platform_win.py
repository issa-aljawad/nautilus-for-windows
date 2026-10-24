"""Windows integration: known folders, drives, the Recycle Bin, shell verbs.

Everything here uses ctypes so the app has no dependency beyond PySide6.
"""

import ctypes
import os
import shutil
import string
import struct
import subprocess
import sys
import uuid
from ctypes import wintypes
from datetime import datetime, timedelta, timezone

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    ole32 = ctypes.windll.ole32


# ---------------------------------------------------------------- known folders

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

    def __init__(self, text):
        super().__init__()
        u = uuid.UUID(text)
        self.Data1, self.Data2, self.Data3 = u.fields[0], u.fields[1], u.fields[2]
        for i, b in enumerate(u.bytes[8:]):
            self.Data4[i] = b


_KNOWN = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
    "recent": "AE50C081-EBD2-438A-8655-8A092E34987A",
}

_FALLBACK = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads",
             "music": "Music", "pictures": "Pictures", "videos": "Videos"}


def known_folder(name):
    if IS_WINDOWS and name in _KNOWN:
        out = ctypes.c_wchar_p()
        guid = _GUID(_KNOWN[name])
        try:
            if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
                path = out.value
                ole32.CoTaskMemFree(out)
                if path:
                    return path
        except OSError:
            pass
    return os.path.join(os.path.expanduser("~"), _FALLBACK.get(name, name))


HOME = os.path.expanduser("~")


# ---------------------------------------------------------------- theme

def system_prefers_dark():
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def animations_enabled():
    """Windows' "Animation effects" setting (Accessibility > Visual effects)."""
    if not IS_WINDOWS:
        return True
    on = wintypes.BOOL(True)
    user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)    # SPI_GETCLIENTAREAANIMATION
    return bool(on.value)


# ---------------------------------------------------------------- drives

DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_REMOTE, DRIVE_CDROM = 2, 3, 4, 5


class Drive:
    def __init__(self, root, label, kind):
        self.root = root            # "C:\\"
        self.label = label          # "Local Disk"
        self.kind = kind            # DRIVE_* constant
        self.letter = root[0]

    @property
    def display_name(self):
        return f"{self.label} ({self.letter}:)"

    def usage(self):
        try:
            return shutil.disk_usage(self.root)
        except OSError:
            return None


def list_drives():
    drives = []
    if not IS_WINDOWS:
        return [Drive("/", "Root", DRIVE_FIXED)]
    mask = kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        kind = kernel32.GetDriveTypeW(root)
        if kind not in (DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_REMOTE, DRIVE_CDROM):
            continue
        buf = ctypes.create_unicode_buffer(261)
        old_mode = kernel32.SetErrorMode(1)  # no "insert a disk" popups
        ok = kernel32.GetVolumeInformationW(root, buf, 261, None, None, None, None, 0)
        kernel32.SetErrorMode(old_mode)
        if not ok and kind in (DRIVE_REMOVABLE, DRIVE_CDROM):
            continue  # empty card reader / optical drive
        label = buf.value or {DRIVE_FIXED: "Local Disk", DRIVE_REMOVABLE: "Removable Disk",
                              DRIVE_REMOTE: "Network Drive", DRIVE_CDROM: "CD Drive"}[kind]
        drives.append(Drive(root, label, kind))
    return drives


def eject_drive(drive):
    script = (f"(New-Object -ComObject Shell.Application).Namespace(17)"
              f".ParseName('{drive.letter}:').InvokeVerb('Eject')")
    _spawn(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script])


# ---------------------------------------------------------------- hidden files

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4


def is_hidden_attrs(name, attrs):
    return name.startswith(".") or bool(attrs & FILE_ATTRIBUTE_HIDDEN)


# ---------------------------------------------------------------- recycle bin

class TrashEntry:
    def __init__(self, trash_path, info_path, original, size, deleted):
        self.trash_path = trash_path    # ...\$Rxxxxxx.ext — the actual data
        self.info_path = info_path      # ...\$Ixxxxxx.ext — metadata
        self.original = original        # where it came from
        self.size = size
        self.deleted = deleted          # datetime

    @property
    def name(self):
        return os.path.basename(self.original)


def _filetime_to_datetime(ft):
    if ft <= 0:
        return None
    return datetime(1601, 1, 1) + timedelta(microseconds=ft // 10)


def _parse_info(path):
    with open(path, "rb") as f:
        data = f.read()
    version, size, ft = struct.unpack_from("<qqq", data, 0)
    if version == 2:
        n = struct.unpack_from("<i", data, 24)[0]
        original = data[28:28 + n * 2].decode("utf-16-le").rstrip("\0")
    else:
        original = data[24:24 + 520].decode("utf-16-le").split("\0")[0]
    deleted = _filetime_to_datetime(ft)
    if deleted:  # FILETIME is UTC; show local time
        deleted = deleted.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    return original, size, deleted


def _bin_dirs():
    if not IS_WINDOWS:
        return
    for drive in list_drives():
        if drive.kind not in (DRIVE_FIXED, DRIVE_REMOVABLE):
            continue
        root = os.path.join(drive.root, "$Recycle.Bin")
        try:
            sids = os.listdir(root)
        except OSError:
            continue
        for sid in sids:
            d = os.path.join(root, sid)
            if os.access(d, os.R_OK):
                try:
                    os.listdir(d)
                except OSError:
                    continue
                yield d


def list_trash():
    entries = []
    for d in _bin_dirs():
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if not n.startswith("$I"):
                continue
            info = os.path.join(d, n)
            data = os.path.join(d, "$R" + n[2:])
            if not os.path.lexists(data):
                continue
            try:
                original, size, deleted = _parse_info(info)
            except (OSError, struct.error, UnicodeDecodeError):
                continue
            entries.append(TrashEntry(data, info, original, size, deleted))
    return entries


def trash_is_empty():
    for d in _bin_dirs():
        try:
            if any(n.startswith("$I") for n in os.listdir(d)):
                return False
        except OSError:
            pass
    return True


def info_path_for(trash_path):
    d, n = os.path.split(trash_path)
    return os.path.join(d, "$I" + n[2:])


def restore_from_trash(trash_path, original):
    """Move a $R item back to its original location. Returns the final path."""
    os.makedirs(os.path.dirname(original), exist_ok=True)
    target = original
    if os.path.lexists(target):
        target = unique_name(os.path.dirname(original), os.path.basename(original))
    shutil.move(trash_path, target)
    try:
        os.remove(info_path_for(trash_path))
    except OSError:
        pass
    return target


def delete_from_trash(trash_path):
    if os.path.isdir(trash_path) and not os.path.islink(trash_path):
        shutil.rmtree(trash_path)
    else:
        os.remove(trash_path)
    try:
        os.remove(info_path_for(trash_path))
    except OSError:
        pass


def empty_trash():
    if IS_WINDOWS:
        # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)


def unique_name(folder, name):
    base, ext = os.path.splitext(name)
    if os.path.isdir(os.path.join(folder, name)):
        base, ext = name, ""
    i = 2
    while True:
        candidate = f"{base} ({i}){ext}"
        if not os.path.lexists(os.path.join(folder, candidate)):
            return os.path.join(folder, candidate)
        i += 1


# ---------------------------------------------------------------- shell verbs

def _spawn(args, cwd=None):
    flags = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW
    try:
        subprocess.Popen(args, cwd=cwd, creationflags=flags, close_fds=True)
        return True
    except OSError:
        return False


def open_default(path):
    if IS_WINDOWS:
        os.startfile(path)
    else:
        _spawn(["xdg-open", path])


def open_with_dialog(path):
    _spawn(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", path])


def open_terminal(folder):
    if shutil.which("wt"):
        return _spawn(["wt.exe", "-d", folder])
    exe = shutil.which("pwsh") or shutil.which("powershell") or "cmd.exe"
    flags = 0x00000010  # CREATE_NEW_CONSOLE
    try:
        subprocess.Popen([exe], cwd=folder, creationflags=flags)
        return True
    except OSError:
        return False


def open_in_explorer(path, select=False):
    if select:
        _spawn(["explorer.exe", "/select,", os.path.normpath(path)])
    else:
        _spawn(["explorer.exe", os.path.normpath(path)])


def set_wallpaper(path):
    if IS_WINDOWS:
        # SPI_SETDESKWALLPAPER, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
        return bool(user32.SystemParametersInfoW(20, 0, os.path.normpath(path), 3))
    return False


def recent_shortcuts():
    """Targets of the .lnk files in the Windows Recent folder, newest first."""
    folder = known_folder("recent")
    try:
        entries = [e for e in os.scandir(folder) if e.name.lower().endswith(".lnk")]
    except OSError:
        return []
    entries.sort(key=lambda e: e.stat().st_mtime, reverse=True)
    return [e.path for e in entries[:150]]


# ---------------------------------------------------------------- shell thumbnails

class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                ("bmWidthBytes", ctypes.c_long), ("bmPlanes", ctypes.c_ushort), ("bmBitsPixel", ctypes.c_ushort),
                ("bmBits", ctypes.c_void_p)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


_GetImage = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, _SIZE, ctypes.c_int, ctypes.POINTER(wintypes.HBITMAP))
_Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


def shell_thumbnail(path, size):
    """Windows' own preview of a file at up to size×size pixels, as a QImage (None if Windows has none).

    Goes through the installed codecs, so it covers HEIC/HEIF (with Microsoft's HEIF extension),
    AVIF, camera RAW and anything else Explorer can preview. Safe on worker threads."""
    if not IS_WINDOWS:
        return None
    from PySide6.QtGui import QImage
    ole32.CoInitializeEx(None, 0x2)     # apartment threaded, as the shell expects (no-op if already)
    iid = _GUID("BCC18B79-BA16-442F-80C4-8A59C30C463B")     # IShellItemImageFactory
    factory = ctypes.c_void_p()
    create = shell32.SHCreateItemFromParsingName
    create.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_long
    if create(path, None, ctypes.byref(iid), ctypes.byref(factory)) < 0 or not factory:
        return None
    vtable = ctypes.cast(ctypes.cast(factory, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    hbm = wintypes.HBITMAP()
    try:
        hr = _GetImage(vtable[3])(factory, _SIZE(size, size), 0x8, ctypes.byref(hbm))   # SIIGBF_THUMBNAILONLY
    finally:
        _Release(vtable[2])(factory)
    if hr < 0 or not hbm:
        return None
    gdi32 = ctypes.windll.gdi32
    gdi32.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_uint]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    try:
        bm = _BITMAP()
        gdi32.GetObjectW(hbm, ctypes.sizeof(bm), ctypes.byref(bm))
        w, h = bm.bmWidth, abs(bm.bmHeight)
        if w <= 0 or h <= 0:
            return None
        info = _BITMAPINFOHEADER(biSize=ctypes.sizeof(_BITMAPINFOHEADER), biWidth=w, biHeight=-h, biPlanes=1,
                                 biBitCount=32)
        buf = ctypes.create_string_buffer(w * h * 4)
        dc = user32.GetDC(None)
        try:
            rows = gdi32.GetDIBits(dc, hbm, 0, h, buf, ctypes.byref(info), 0)
        finally:
            user32.ReleaseDC(None, dc)
        if rows != h:
            return None
        raw = buf.raw
        alpha = raw[3::4]
        # photos come back with an empty alpha channel; only trust alpha when it is actually used
        fmt = QImage.Format_RGB32 if not alpha.strip(b"\0") or not alpha.strip(b"\xff") else QImage.Format_ARGB32_Premultiplied
        return QImage(raw, w, h, w * 4, fmt).copy()
    finally:
        gdi32.DeleteObject(hbm)
