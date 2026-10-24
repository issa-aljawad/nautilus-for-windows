"""What Windows shows on its Properties tabs, read straight from the system:
sharing, NTFS permissions, known-folder locations, previous versions (restore
points and File History) and Authenticode digital signatures.

Everything here is plain data and ctypes, safe to call on worker threads."""

import base64
import ctypes
import glob
import hashlib
import json
import os
import re
import tempfile
from ctypes import wintypes
from datetime import datetime, timezone

from . import platform_win

IS_WINDOWS = platform_win.IS_WINDOWS
if IS_WINDOWS:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.LookupAccountSidW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPWSTR,
                                           ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR,
                                           ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    crypt32.CryptMsgClose.argtypes = [ctypes.c_void_p]
    crypt32.CertCloseStore.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    shell32.SHObjectProperties.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR]

COMPUTER = os.environ.get("COMPUTERNAME", "localhost")


# ---------------------------------------------------------------- elevation

class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


def run_elevated(exe, params):
    """Runs a hidden program through the UAC prompt and waits; returns its exit code.
    Raises PermissionError if the person declines the prompt."""
    info = _SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(_SHELLEXECUTEINFOW), fMask=0x40,  # SEE_MASK_NOCLOSEPROCESS
                              lpVerb="runas", lpFile=exe, lpParameters=params, nShow=0)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == 1223:     # ERROR_CANCELLED
            raise PermissionError("Administrator permission was not given.")
        raise OSError(err, ctypes.FormatError(err))
    try:
        kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel32.CloseHandle(info.hProcess)


def _powershell_elevated(script):
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    return run_elevated("powershell.exe", f"-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand {encoded}")


# ---------------------------------------------------------------- sharing

def shares_for(path):
    """[(share name, remark)] for SMB shares of exactly this folder."""
    import winreg
    out = []
    target = os.path.normcase(os.path.normpath(path))
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\LanmanServer\Shares") as k:
            i = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(k, i)
                except OSError:
                    break
                i += 1
                fields = dict(v.split("=", 1) for v in (value if isinstance(value, list) else []) if "=" in v)
                if os.path.normcase(os.path.normpath(fields.get("Path", ""))) == target:
                    out.append((name, fields.get("Remark", "")))
    except OSError:
        pass
    return out


def share_folder(path, name, permission):
    """permission: 'READ' | 'CHANGE' | 'FULL' for Everyone. Needs the UAC prompt."""
    return run_elevated(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "net.exe"),
                        f'share "{name}={path}" /GRANT:Everyone,{permission} /CACHE:Manual')


def unshare_folder(name):
    return run_elevated(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "net.exe"),
                        f'share "{name}" /DELETE /Y')


# ---------------------------------------------------------------- security

FULL, MODIFY, READ_EXECUTE, READ, WRITE = 0x1F01FF, 0x1301BF, 0x1200A9, 0x120089, 0x100116
_GENERIC = {0x10000000: FULL, 0x80000000: READ, 0x40000000: 0x120116, 0x20000000: 0x1200A0}
INHERITED, INHERIT_ONLY, CONTAINER_INHERIT, OBJECT_INHERIT = 0x10, 0x08, 0x02, 0x01


def _map_generic(mask):
    for bit, rights in _GENERIC.items():
        if mask & bit:
            mask |= rights
    return mask & 0x0FFFFFFF


def basic_permissions(is_dir):
    rows = [("Full control", FULL), ("Modify", MODIFY), ("Read & execute", READ_EXECUTE)]
    if is_dir:
        rows.append(("List folder contents", READ_EXECUTE))
    return rows + [("Read", READ), ("Write", WRITE)]


def access_name(mask, is_dir):
    for title, rights in basic_permissions(is_dir):
        if mask == rights:
            return title
    if mask == READ_EXECUTE | WRITE:
        return "Read, write & execute"
    return "Special"


def applies_to(flags, is_dir):
    if not is_dir:
        return "This file only"
    oi, ci, io = flags & OBJECT_INHERIT, flags & CONTAINER_INHERIT, flags & INHERIT_ONLY
    if io:
        return {(1, 1): "Subfolders and files only", (0, 1): "Subfolders only", (1, 0): "Files only"}.get(
            (bool(oi), bool(ci)), "This folder only")
    return {(1, 1): "This folder, subfolders and files", (0, 1): "This folder and subfolders",
            (1, 0): "This folder and files"}.get((bool(oi), bool(ci)), "This folder only")


def _sid_name(sid):
    """(display name, 'DOMAIN\\name' detail, is_group)"""
    name, domain = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
    n, d, use = wintypes.DWORD(256), wintypes.DWORD(256), wintypes.DWORD()
    if advapi32.LookupAccountSidW(None, sid, name, ctypes.byref(n), domain, ctypes.byref(d), ctypes.byref(use)):
        dom = COMPUTER if domain.value == "BUILTIN" else domain.value
        detail = f"{dom}\\{name.value}" if dom and dom not in ("NT AUTHORITY", "NT SERVICE", "APPLICATION PACKAGE AUTHORITY") else ""
        return name.value, detail, use.value != 1    # SidTypeUser = 1
    text = wintypes.LPWSTR()
    if advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        s = text.value
        kernel32.LocalFree(text)
        return "Account Unknown", s, False
    return "Account Unknown", "", False


class _ACE_HEADER(ctypes.Structure):
    _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", ctypes.c_ushort)]


class _ACL(ctypes.Structure):
    _fields_ = [("AclRevision", ctypes.c_ubyte), ("Sbz1", ctypes.c_ubyte), ("AclSize", ctypes.c_ushort),
                ("AceCount", ctypes.c_ushort), ("Sbz2", ctypes.c_ushort)]


def security(path):
    """{'owner': (name, detail), 'entries': [ {kind, name, detail, group, mask, flags} ], 'no_dacl': bool}"""
    owner, dacl, sd = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    gnsi = advapi32.GetNamedSecurityInfoW
    gnsi.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD] + [ctypes.POINTER(ctypes.c_void_p)] * 5
    err = gnsi(path, 1, 0x1 | 0x4, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(sd))
    if err:
        raise OSError(err, ctypes.FormatError(err))
    try:
        out = {"owner": _sid_name(owner) if owner else ("", "", False), "entries": [], "no_dacl": not dacl}
        if dacl:
            count = ctypes.cast(dacl, ctypes.POINTER(_ACL)).contents.AceCount
            for i in range(count):
                ace = ctypes.c_void_p()
                if not advapi32.GetAce(dacl, i, ctypes.byref(ace)):
                    continue
                head = ctypes.cast(ace, ctypes.POINTER(_ACE_HEADER)).contents
                if head.AceType not in (0, 1):      # only plain allow/deny ACEs appear on files
                    continue
                mask = ctypes.cast(ace.value + 4, ctypes.POINTER(wintypes.DWORD)).contents.value
                name, detail, group = _sid_name(ctypes.c_void_p(ace.value + 8))
                out["entries"].append({"kind": "Allow" if head.AceType == 0 else "Deny", "name": name,
                                       "detail": detail, "group": group, "mask": _map_generic(mask),
                                       "flags": head.AceFlags})
        return out
    finally:
        kernel32.LocalFree(sd)


def summarize(entries, is_dir):
    """Per principal, in ACL order: (name, detail, group, allowed {title}, denied {title}, special_allow, special_deny)"""
    people = {}
    for e in entries:
        p = people.setdefault((e["name"], e["detail"]), {"group": e["group"], "Allow": 0, "Deny": 0,
                                                         "odd_Allow": False, "odd_Deny": False})
        if e["flags"] & INHERIT_ONLY:
            p["odd_" + e["kind"]] = True    # only applies to things inside: Windows calls it special
        else:
            p[e["kind"]] |= e["mask"]
    rows = []
    for (name, detail), p in people.items():
        result = [name, detail, p["group"]]
        for kind in ("Allow", "Deny"):
            mask = p[kind]
            titles = {t for t, rights in basic_permissions(is_dir) if mask & rights == rights}
            covered = 0
            for t, rights in basic_permissions(is_dir):
                if t in titles:
                    covered |= rights
            result.append(titles)
            result.append(p["odd_" + kind] or bool(mask & ~covered & 0x1F01FF))
        rows.append(tuple(result[:3] + [result[3], result[5], result[4], result[6]]))
    return rows


def open_windows_page(path, page):
    """Opens Windows' own Properties dialog on a page ('Security', 'Sharing', …) for editing."""
    shell32.SHObjectProperties(None, 0x2, path, page)    # SHOP_FILEPATH


# ---------------------------------------------------------------- location

KNOWN_FOLDER_NAMES = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads",
                      "music": "Music", "pictures": "Pictures", "videos": "Videos"}


def known_folder_of(path):
    """The known-folder key ('documents', …) whose current location is this folder."""
    n = os.path.normcase(os.path.normpath(path))
    for key in KNOWN_FOLDER_NAMES:
        if os.path.normcase(os.path.normpath(platform_win.known_folder(key))) == n:
            return key
    return None


def default_location(key):
    return os.path.join(platform_win.HOME, KNOWN_FOLDER_NAMES[key])


def set_known_folder(key, new_path):
    """Points Documents, Downloads, … at another folder (moving the files is the caller's job)."""
    guid = platform_win._GUID(platform_win._KNOWN[key])
    fn = shell32.SHSetKnownFolderPath
    fn.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, wintypes.LPCWSTR]
    fn.restype = ctypes.c_long
    hr = fn(ctypes.byref(guid), 0, None, new_path)
    if hr < 0:
        raise OSError(hr & 0xFFFFFFFF, ctypes.FormatError(hr & 0xFFFF) or f"Error 0x{hr & 0xFFFFFFFF:08X}")
    shell32.SHChangeNotify(0x08000000, 0, None, None)     # SHCNE_ASSOCCHANGED: refresh Explorer


# ---------------------------------------------------------------- previous versions

_shadow_cache = None    # [(device object, volume name, datetime utc)] once listed this session


def _volume_of(path):
    mount, vol = ctypes.create_unicode_buffer(300), ctypes.create_unicode_buffer(100)
    if not kernel32.GetVolumePathNameW(path, mount, 300):
        return None, None
    if not kernel32.GetVolumeNameForVolumeMountPointW(mount.value, vol, 100):
        return mount.value, None
    return mount.value, vol.value


def _shadows_direct():
    """Snapshot names straight from the volume driver: works when Files itself runs as administrator."""
    out = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if not os.path.exists(root):
            continue
        h = kernel32.CreateFileW(f"\\\\.\\{letter}:", 0, 3, None, 3, 0, None)
        if h in (None, ctypes.c_void_p(-1).value):
            continue
        try:
            buf, n = ctypes.create_string_buffer(65536), wintypes.DWORD()
            if not kernel32.DeviceIoControl(ctypes.c_void_p(h), 0x530018, None, 0, buf, 65536, ctypes.byref(n), None):
                return None     # not elevated: needs the helper
            size = int.from_bytes(buf.raw[:4], "little")
            _mount, vol = _volume_of(root)
            for dev in buf.raw[4:4 + size].decode("utf-16-le").split("\0"):
                if dev:
                    dev = "\\\\?\\GLOBALROOT" + dev
                    try:
                        when = datetime.fromtimestamp(os.stat(dev + "\\").st_ctime, timezone.utc)
                    except OSError:
                        continue
                    out.append((dev, vol, when))
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(h))
    return out


def list_shadows_elevated():
    """Asks for administrator permission once to list the restore point snapshots."""
    global _shadow_cache
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    script = ("$r = @(Get-CimInstance Win32_ShadowCopy | ForEach-Object { [pscustomobject]@{ d = $_.DeviceObject; "
              "v = $_.VolumeName; t = $_.InstallDate.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss') } }); "
              f"ConvertTo-Json -InputObject $r -Compress | Set-Content -Encoding UTF8 -Path '{tmp}'")
    try:
        _powershell_elevated(script)
        with open(tmp, encoding="utf-8-sig") as f:
            text = f.read().strip()
        rows = json.loads(text) if text else []
        if isinstance(rows, dict):
            rows = [rows]
        _shadow_cache = [(r["d"], r["v"], datetime.strptime(r["t"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc))
                         for r in rows if r.get("d")]
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return _shadow_cache


def _file_history(path):
    out = []
    configs = glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\FileHistory\Configuration\Config*.xml"))
    targets = set()
    for c in configs:
        try:
            text = open(c, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        targets.update(m.strip() for m in re.findall(r"<TargetUrl>(.*?)</TargetUrl>", text, re.S))
    drive, rest = os.path.splitdrive(path)
    folder, name = os.path.split(rest.strip("\\"))
    stem, ext = os.path.splitext(name)
    pattern = re.compile(rf"^{re.escape(stem)} \((\d{{4}})_(\d\d)_(\d\d) (\d\d)_(\d\d)_(\d\d) UTC\){re.escape(ext)}$", re.I)
    for t in targets:
        where = os.path.join(t, "Data", drive.rstrip(":"), folder)
        try:
            names = os.listdir(where)
        except OSError:
            continue
        for n in names:
            m = pattern.match(n)
            if m:
                when = datetime(*map(int, m.groups()), tzinfo=timezone.utc)
                out.append({"path": os.path.join(where, n), "when": when, "source": "File History"})
    return out


def previous_versions(path):
    """(versions, needs_admin). versions: [{'path', 'when' (utc), 'source'}], newest first."""
    versions = _file_history(path) if not os.path.isdir(path) else []
    shadows = _shadow_cache if _shadow_cache is not None else _shadows_direct()
    needs_admin = shadows is None
    try:
        current = os.stat(path).st_mtime
    except OSError:
        current = None
    seen = set()
    mount, vol = _volume_of(path)
    for dev, svol, when in shadows or []:
        if vol and svol and svol.lower() != vol.lower():
            continue
        candidate = dev.rstrip("\\") + "\\" + path[len(mount):] if mount else None
        try:
            st = os.stat(candidate)
        except (OSError, TypeError):
            continue
        if not os.path.isdir(path) and (st.st_mtime == current or st.st_mtime in seen):
            continue    # unchanged since then: Windows doesn't list it either
        seen.add(st.st_mtime)
        versions.append({"path": candidate, "when": datetime.fromtimestamp(st.st_mtime, timezone.utc)
                         if not os.path.isdir(path) else when, "source": "Restore point"})
    versions.sort(key=lambda v: v["when"], reverse=True)
    return versions, needs_admin


# ---------------------------------------------------------------- digital signatures

SIGNABLE = {"exe", "dll", "sys", "ocx", "cpl", "scr", "drv", "efi", "mui", "msi", "msp", "msix", "appx",
            "msixbundle", "appxbundle", "cab", "cat", "ps1", "psm1", "psd1", "ps1xml", "vbs", "js", "wsf", "ax"}
_DIGESTS = {"1.3.14.3.2.26": "sha1", "2.16.840.1.101.3.4.2.1": "sha256", "2.16.840.1.101.3.4.2.2": "sha384",
            "2.16.840.1.101.3.4.2.3": "sha512", "1.2.840.113549.2.5": "md5"}


class _Der:
    """A minimal DER reader: enough of PKCS#7 / X.509 for Authenticode."""

    __slots__ = ("tag", "value", "raw")

    def __init__(self, data, pos=0):
        tag, length = data[pos], data[pos + 1]
        start = pos + 2
        if length & 0x80:
            n = length & 0x7F
            length = int.from_bytes(data[start:start + n], "big")
            start += n
        self.tag, self.value, self.raw = tag, data[start:start + length], data[pos:start + length]

    def children(self):
        out, pos = [], 0
        while pos < len(self.value):
            child = _Der(self.value, pos)
            out.append(child)
            pos += len(child.raw)
        return out

    def oid(self):
        v = self.value
        parts = [str(v[0] // 40), str(v[0] % 40)]
        n = 0
        for b in v[1:]:
            n = (n << 7) | (b & 0x7F)
            if not b & 0x80:
                parts.append(str(n))
                n = 0
        return ".".join(parts)

    def text(self):
        if self.tag == 0x1E:
            return self.value.decode("utf-16-be", "replace")
        return self.value.decode("utf-8" if self.tag == 0x0C else "latin-1", "replace")

    def time(self):
        s = self.value.decode("ascii", "replace").rstrip("Z").split(".")[0]
        fmt = "%y%m%d%H%M%S" if self.tag == 0x17 else "%Y%m%d%H%M%S"
        return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)


def _name_part(name, oid):
    for rdn in name.children():
        for atv in rdn.children():
            kids = atv.children()
            if kids and kids[0].oid() == oid:
                return kids[1].text()
    return ""


def _display_name(name):
    return _name_part(name, "2.5.4.3") or _name_part(name, "2.5.4.10") or _name_part(name, "2.5.4.11")


def _cert_info(cert):
    tbs = cert.children()[0].children()
    if tbs[0].tag == 0xA0:      # explicit version
        tbs = tbs[1:]
    serial, _alg, issuer, validity, subject = tbs[:5]
    start, end = validity.children()
    return {"serial": serial.value, "issuer_raw": issuer.raw, "subject": _display_name(subject),
            "issuer": _display_name(issuer), "email": _name_part(subject, "1.2.840.113549.1.9.1"),
            "valid_from": start.time(), "valid_to": end.time(),
            "thumbprint": hashlib.sha1(cert.raw).hexdigest().upper()}


def _attributes(node):
    out = {}
    for attr in node.children():
        kids = attr.children()
        out.setdefault(kids[0].oid(), []).extend(kids[1].children())
    return out


def _signed_data(content_info):
    """(certificates [_Der], signer infos [_Der], encapsulated content _Der) of a PKCS#7 ContentInfo."""
    signed = content_info.children()[1].children()[0].children()
    certs, signers, encap = [], [], signed[2]
    for part in signed[3:]:
        if part.tag == 0xA0:
            certs = part.children()
        elif part.tag == 0x31:
            signers = part.children()
    return certs, signers, encap


def _signer(signer, certs):
    """{'cert': cert info, 'digest', 'auth', 'unauth'} for a SignerInfo."""
    kids = signer.children()
    sid, digest = kids[1], kids[2].children()[0].oid()
    issuer_raw, serial = (sid.children()[0].raw, sid.children()[1].value) if sid.tag == 0x30 else (None, None)
    cert = None
    for c in certs:
        info = _cert_info(c)
        if info["serial"] == serial and info["issuer_raw"] == issuer_raw:
            cert = info
            break
    auth = unauth = {}
    for part in kids[3:]:
        if part.tag == 0xA0:
            auth = _attributes(part)
        elif part.tag == 0xA1:
            unauth = _attributes(part)
    return {"cert": cert, "digest": _DIGESTS.get(digest, digest), "auth": auth, "unauth": unauth}


def _parse_signatures(pkcs7):
    """[{signer, email, digest, timestamp, countersigners: [{name, timestamp, digest}], cert}] incl. nested."""
    out = []
    certs, signers, _encap = _signed_data(_Der(pkcs7))
    for s in signers:
        info = _signer(s, certs)
        counters = []
        for cs in info["unauth"].get("1.2.840.113549.1.9.6", []):        # legacy countersignature
            c = _signer(cs, certs)
            times = c["auth"].get("1.2.840.113549.1.9.5", [])
            counters.append({"name": c["cert"]["subject"] if c["cert"] else "Unknown", "digest": c["digest"],
                             "timestamp": times[0].time() if times else None, "cert": c["cert"]})
        for ts in info["unauth"].get("1.3.6.1.4.1.311.3.3.1", []):       # RFC 3161 timestamp
            try:
                tcerts, tsigners, tencap = _signed_data(ts)
                tst = _Der(tencap.children()[1].children()[0].value).children()
                gen = next(x for x in tst if x.tag == 0x18).time()
                c = _signer(tsigners[0], tcerts) if tsigners else {"cert": None, "digest": ""}
                counters.append({"name": c["cert"]["subject"] if c["cert"] else "Unknown", "digest": c["digest"],
                                 "timestamp": gen, "cert": c["cert"]})
            except (IndexError, ValueError, StopIteration):
                pass
        cert = info["cert"] or {}
        out.append({"signer": cert.get("subject", "Unknown"), "email": cert.get("email", ""), "digest": info["digest"],
                    "timestamp": counters[0]["timestamp"] if counters else None, "countersigners": counters,
                    "cert": info["cert"]})
        for nested in info["unauth"].get("1.3.6.1.4.1.311.2.4.1", []):  # dual signing (sha1 + sha256 …)
            out.extend(_parse_signatures(nested.raw))
    return out


def _embedded_pkcs7(path):
    enc, content, fmt = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    store, msg = ctypes.c_void_p(), ctypes.c_void_p()
    q = crypt32.CryptQueryObject
    q.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                  ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                  ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    if not q(1, path, 0x400, 0x2, 0, ctypes.byref(enc), ctypes.byref(content), ctypes.byref(fmt),
             ctypes.byref(store), ctypes.byref(msg), None):     # file, PKCS7_SIGNED_EMBED, binary
        return None
    try:
        get = crypt32.CryptMsgGetParam
        get.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        size = wintypes.DWORD()
        if not get(msg, 29, 0, None, ctypes.byref(size)):       # CMSG_ENCODED_MESSAGE
            return None
        buf = ctypes.create_string_buffer(size.value)
        if not get(msg, 29, 0, buf, ctypes.byref(size)):
            return None
        return buf.raw[:size.value]
    finally:
        crypt32.CryptMsgClose(msg)
        crypt32.CertCloseStore(store, 0)


def has_signature(path):
    return IS_WINDOWS and os.path.splitext(path)[1][1:].lower() in SIGNABLE and _embedded_pkcs7(path) is not None


class _WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", wintypes.DWORD), ("pcwszFilePath", wintypes.LPCWSTR), ("hFile", wintypes.HANDLE),
                ("pgKnownSubject", ctypes.c_void_p)]


class _WINTRUST_SIGNATURE_SETTINGS(ctypes.Structure):
    _fields_ = [("cbStruct", wintypes.DWORD), ("dwIndex", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("cSecondarySigs", wintypes.DWORD), ("dwVerifiedSigIndex", wintypes.DWORD),
                ("pCryptoPolicy", ctypes.c_void_p)]


class _WINTRUST_DATA(ctypes.Structure):
    _fields_ = [("cbStruct", wintypes.DWORD), ("pPolicyCallbackData", ctypes.c_void_p),
                ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", wintypes.DWORD),
                ("fdwRevocationChecks", wintypes.DWORD), ("dwUnionChoice", wintypes.DWORD),
                ("pFile", ctypes.POINTER(_WINTRUST_FILE_INFO)), ("dwStateAction", wintypes.DWORD),
                ("hWVTStateData", wintypes.HANDLE), ("pwszURLReference", wintypes.LPCWSTR),
                ("dwProvFlags", wintypes.DWORD), ("dwUIContext", wintypes.DWORD),
                ("pSignatureSettings", ctypes.POINTER(_WINTRUST_SIGNATURE_SETTINGS))]


_TRUST_MESSAGES = {
    0: "This digital signature is OK.",
    0x800B0100: "This file isn't signed.",
    0x80096010: "This digital signature is not valid. The file may have been changed after it was signed.",
    0x800B0109: "The certificate chain ends in a root certificate that isn't trusted.",
    0x800B0101: "A required certificate is not within its validity period.",
    0x800B010C: "The signer's certificate was revoked.",
    0x800B0111: "The signer's certificate is explicitly distrusted.",
    0x800B010A: "The certificate chain couldn't be built to a trusted root.",
}


def verify(path, index=0):
    """(ok, message) for one signature, without contacting revocation servers."""
    file_info = _WINTRUST_FILE_INFO(cbStruct=ctypes.sizeof(_WINTRUST_FILE_INFO), pcwszFilePath=path)
    settings = _WINTRUST_SIGNATURE_SETTINGS(cbStruct=ctypes.sizeof(_WINTRUST_SIGNATURE_SETTINGS), dwIndex=index,
                                            dwFlags=0x1)       # WSS_VERIFY_SPECIFIC
    data = _WINTRUST_DATA(cbStruct=ctypes.sizeof(_WINTRUST_DATA), dwUIChoice=2, fdwRevocationChecks=0,
                          dwUnionChoice=1, pFile=ctypes.pointer(file_info), dwStateAction=1,
                          dwProvFlags=0x1000, pSignatureSettings=ctypes.pointer(settings))  # cache-only URLs
    guid = platform_win._GUID("00AAC56B-CD44-11D0-8CC2-00C04FC295EE")       # WINTRUST_ACTION_GENERIC_VERIFY_V2
    fn = wintrust.WinVerifyTrust
    fn.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p]
    fn.restype = ctypes.c_long
    code = fn(None, ctypes.byref(guid), ctypes.byref(data)) & 0xFFFFFFFF
    data.dwStateAction = 2      # WTD_STATEACTION_CLOSE
    fn(None, ctypes.byref(guid), ctypes.byref(data))
    return code == 0, _TRUST_MESSAGES.get(code, f"This digital signature couldn't be verified (0x{code:08X}).")


def signatures(path):
    """Every embedded signature with its verification result."""
    pkcs7 = _embedded_pkcs7(path)
    if pkcs7 is None:
        return []
    sigs = _parse_signatures(pkcs7)
    for i, s in enumerate(sigs):
        s["ok"], s["status"] = verify(path, i)
    return sigs
