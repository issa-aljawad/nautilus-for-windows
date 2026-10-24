"""File operations on worker threads, with progress reporting and undo/redo."""

import os
import shutil
import tarfile
import threading
import time
import zipfile

from PySide6.QtCore import QFile, QObject, Signal

from . import platform_win


class Cancelled(Exception):
    pass


def _norm(p):
    return os.path.normcase(os.path.normpath(p))


def same_volume(a, b):
    return os.path.splitdrive(os.path.abspath(a))[0].lower() == os.path.splitdrive(os.path.abspath(b))[0].lower()


def copy_name(folder, name):
    """Nautilus-style duplicate names: 'Name (Copy).ext', 'Name (Copy 2).ext'."""
    is_dir = os.path.isdir(os.path.join(folder, name))
    base, ext = (name, "") if is_dir else os.path.splitext(name)
    candidate = f"{base} (Copy){ext}"
    i = 2
    while os.path.lexists(os.path.join(folder, candidate)):
        candidate = f"{base} (Copy {i}){ext}"
        i += 1
    return os.path.join(folder, candidate)


def tree_size(paths):
    total, files = 0, 0
    for p in paths:
        if os.path.isdir(p) and not os.path.islink(p):
            for root, _dirs, names in os.walk(p):
                for n in names:
                    try:
                        total += os.path.getsize(os.path.join(root, n))
                    except OSError:
                        pass
                    files += 1
        else:
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
            files += 1
    return total, files


class Operation(QObject):
    """One background job. Signals are delivered on the GUI thread."""

    progress = Signal(object)    # self
    finished = Signal(object)    # self

    def __init__(self, kind, title, fn):
        super().__init__()
        self.kind = kind
        self.title = title           # "Copying “foo” to “Downloads”"
        self.detail = "Preparing…"
        self.fraction = -1.0         # < 0 means indeterminate
        self.error = None
        self.result = None           # data needed for undo
        self.cancel_event = threading.Event()
        self._fn = fn
        self._done_bytes = 0
        self._total_bytes = 0
        self._start = 0.0
        self._last_emit = 0.0

    def start(self):
        self._start = time.monotonic()
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        self.cancel_event.set()

    def check(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def _run(self):
        try:
            self.result = self._fn(self)
        except Cancelled:
            self.error = "cancelled"
        except Exception as e:  # report to the UI instead of crashing the thread
            self.error = str(e) or e.__class__.__name__
        self.finished.emit(self)

    # progress helpers ---------------------------------------------------
    def set_total(self, nbytes):
        self._total_bytes = max(1, nbytes)

    def add_bytes(self, n):
        self._done_bytes += n
        now = time.monotonic()
        if now - self._last_emit > 0.1:
            self._last_emit = now
            self.fraction = min(1.0, self._done_bytes / self._total_bytes)
            elapsed = now - self._start
            rate = self._done_bytes / elapsed if elapsed > 0 else 0
            from .model import format_size
            left = ""
            if rate > 0 and elapsed > 1:
                secs = int((self._total_bytes - self._done_bytes) / rate)
                left = f" — {secs} second{'s' if secs != 1 else ''} left" if secs < 60 else f" — {secs // 60} minute{'s' if secs >= 120 else ''} left"
            self.detail = f"{format_size(self._done_bytes)} of {format_size(self._total_bytes)}{left}"
            self.progress.emit(self)

    def set_step(self, done, total, what):
        self.fraction = done / total if total else -1
        self.detail = what
        self.progress.emit(self)


# ------------------------------------------------------------------ primitives

def _copy_file(op, src, dst):
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            op.check()
            chunk = fi.read(1024 * 1024)
            if not chunk:
                break
            fo.write(chunk)
            op.add_bytes(len(chunk))
    shutil.copystat(src, dst)


def _copy_tree(op, src, dst):
    if os.path.isdir(src) and not os.path.islink(src):
        os.makedirs(dst, exist_ok=True)
        for entry in os.scandir(src):
            _copy_tree(op, entry.path, os.path.join(dst, entry.name))
        try:
            shutil.copystat(src, dst)
        except OSError:
            pass
    else:
        _copy_file(op, src, dst)


def _remove(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def _move(op, src, dst):
    if os.path.lexists(dst) and os.path.isdir(dst) and os.path.isdir(src):
        _copy_tree(op, src, dst)   # merge folders
        _remove(src)
        return
    if os.path.lexists(dst):
        _remove(dst)
    if same_volume(src, dst):
        os.rename(src, dst)
    else:
        _copy_tree(op, src, dst)
        _remove(src)


# ------------------------------------------------------------------ jobs

def _quote(path):
    return f"“{os.path.basename(path.rstrip(os.sep)) or path}”"


def _many(paths, verb_one, verb_many):
    return verb_one.format(_quote(paths[0])) if len(paths) == 1 else verb_many.format(len(paths))


def copy_job(pairs):
    """pairs: [(src, dst)] with conflicts already resolved."""
    srcs = [s for s, _ in pairs]
    dest = os.path.dirname(pairs[0][1])

    def fn(op):
        op.set_total(tree_size(srcs)[0])
        done = []
        for src, dst in pairs:
            op.check()
            if os.path.lexists(dst) and not (os.path.isdir(src) and os.path.isdir(dst)):
                _remove(dst)
            _copy_tree(op, src, dst)
            done.append((src, dst))
        return {"kind": "copy", "pairs": done}

    return Operation("copy", _many(srcs, "Copying {} to " + _quote(dest), "Copying {} files to " + _quote(dest)), fn)


def move_job(pairs):
    srcs = [s for s, _ in pairs]
    dest = os.path.dirname(pairs[0][1])

    def fn(op):
        if not all(same_volume(s, d) for s, d in pairs):
            op.set_total(tree_size(srcs)[0])
        done = []
        for src, dst in pairs:
            op.check()
            _move(op, src, dst)
            done.append((src, dst))
        return {"kind": "move", "pairs": done}

    return Operation("move", _many(srcs, "Moving {} to " + _quote(dest), "Moving {} files to " + _quote(dest)), fn)


def trash_job(paths):
    def fn(op):
        done = []
        for i, p in enumerate(paths):
            op.check()
            f = QFile(p)
            if not f.moveToTrash():
                raise OSError(f"Could not move {_quote(p)} to the trash")
            done.append((p, os.path.normpath(f.fileName())))
            op.set_step(i + 1, len(paths), f"{i + 1} of {len(paths)}")
        return {"kind": "trash", "pairs": done}

    return Operation("trash", _many(paths, "Trashing {}", "Trashing {} files"), fn)


def delete_job(paths):
    def fn(op):
        for i, p in enumerate(paths):
            op.check()
            _remove(p)
            op.set_step(i + 1, len(paths), f"{i + 1} of {len(paths)}")
        return {"kind": "delete", "paths": list(paths)}

    return Operation("delete", _many(paths, "Deleting {}", "Deleting {} files"), fn)


def restore_job(entries):
    """entries: [(trash_path, original_path)]"""
    def fn(op):
        done = []
        for i, (tp, orig) in enumerate(entries):
            op.check()
            final = platform_win.restore_from_trash(tp, orig)
            done.append((orig, final))
            op.set_step(i + 1, len(entries), f"{i + 1} of {len(entries)}")
        return {"kind": "restore", "pairs": done}

    return Operation("restore", _many([o for _, o in entries], "Restoring {}", "Restoring {} files"), fn)


def delete_from_trash_job(trash_paths, names):
    def fn(op):
        for i, tp in enumerate(trash_paths):
            op.check()
            platform_win.delete_from_trash(tp)
            op.set_step(i + 1, len(trash_paths), f"{i + 1} of {len(trash_paths)}")
        return {"kind": "delete", "paths": []}

    return Operation("delete", _many(names, "Deleting {}", "Deleting {} files"), fn)


def empty_trash_job():
    def fn(op):
        platform_win.empty_trash()
        return {"kind": "empty"}

    return Operation("delete", "Emptying Trash", fn)


def compress_job(sources, archive):
    def fn(op):
        total, _ = tree_size(sources)
        op.set_total(total)
        base = os.path.dirname(sources[0])
        tmp = archive + ".part"
        try:
            if archive.lower().endswith(".zip"):
                with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                    for path, arc in _walk_members(sources, base):
                        op.check()
                        z.write(path, arc)
                        if os.path.isfile(path):
                            op.add_bytes(os.path.getsize(path))
            else:
                mode = "w:xz" if archive.lower().endswith(".tar.xz") else "w:gz"
                with tarfile.open(tmp, mode) as t:
                    for path, arc in _walk_members(sources, base):
                        op.check()
                        t.add(path, arc, recursive=False)
                        if os.path.isfile(path):
                            op.add_bytes(os.path.getsize(path))
            os.replace(tmp, archive)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return {"kind": "create", "path": archive}

    return Operation("compress", f"Compressing {_many(sources, '{}', '{} files')}", fn)


def _walk_members(sources, base):
    for src in sources:
        yield src, os.path.relpath(src, base)
        if os.path.isdir(src):
            for root, dirs, files in os.walk(src):
                for d in dirs:
                    p = os.path.join(root, d)
                    yield p, os.path.relpath(p, base)
                for f in files:
                    p = os.path.join(root, f)
                    yield p, os.path.relpath(p, base)


def _safe_member(dest, name):
    target = os.path.realpath(os.path.join(dest, name))
    if not (target == os.path.realpath(dest) or target.startswith(os.path.realpath(dest) + os.sep)):
        raise OSError(f"Refusing to extract “{name}” outside the destination folder")
    return target


def extract_job(archive, dest_parent):
    name = os.path.basename(archive)
    for ext in (".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".zip", ".tar"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    dest = os.path.join(dest_parent, name)
    if os.path.lexists(dest):
        dest = platform_win.unique_name(dest_parent, name)

    def fn(op):
        os.makedirs(dest)
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as z:
                members = z.infolist()
                op.set_total(sum(m.file_size for m in members))
                for m in members:
                    op.check()
                    _safe_member(dest, m.filename)
                    z.extract(m, dest)
                    op.add_bytes(m.file_size)
        elif tarfile.is_tarfile(archive):
            with tarfile.open(archive) as t:
                members = t.getmembers()
                op.set_total(sum(m.size for m in members))
                for m in members:
                    op.check()
                    _safe_member(dest, m.name)
                    if m.issym() or m.islnk():
                        continue
                    t.extract(m, dest, filter="data")
                    op.add_bytes(m.size)
        else:
            os.rmdir(dest)
            raise OSError("This archive format is not supported")
        return {"kind": "create", "path": dest}

    return Operation("extract", f"Extracting {_quote(archive)}", fn)


EXTRACTABLE = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".jar", ".whl", ".cbz", ".epub")


def is_extractable(path):
    return path.lower().endswith(EXTRACTABLE)


# ------------------------------------------------------------------ manager & undo

class Operations(QObject):
    """Tracks running jobs and the undo/redo history."""

    changed = Signal()             # list of running jobs changed / progressed
    history_changed = Signal()
    job_finished = Signal(object)  # Operation
    error = Signal(str, str)       # title, message

    def __init__(self):
        super().__init__()
        self.running = []
        self.undo_stack = []
        self.redo_stack = []
        self._replaying = None   # "undo" | "redo" while replaying

    def run(self, op, record=True):
        op.record = record
        op.progress.connect(lambda _op: self.changed.emit())
        op.finished.connect(self._finished)
        self.running.append(op)
        self.changed.emit()
        op.start()
        return op

    def _finished(self, op):
        if op in self.running:
            self.running.remove(op)
        self.changed.emit()
        if op.error and op.error != "cancelled":
            self.error.emit(f"Error while {op.title[0].lower()}{op.title[1:]}", op.error)
        if op.result and op.result.get("kind") == "move":
            from .settings import folder_colors
            for src, dst in op.result["pairs"]:
                folder_colors.rename(src, dst)
        if op.result and getattr(op, "record", True):
            self.push(op.result)
        self.job_finished.emit(op)

    def push(self, record):
        if record.get("kind") in ("delete", "empty"):
            return
        if self._replaying == "undo":
            self.redo_stack.append(record)
        else:
            self.undo_stack.append(record)
            if self._replaying is None:
                self.redo_stack.clear()
        self.history_changed.emit()

    def undo_label(self):
        return _describe(self.undo_stack[-1]) if self.undo_stack else None

    def redo_label(self):
        return _describe(self.redo_stack[-1]) if self.redo_stack else None

    def undo(self):
        if self.undo_stack:
            self._replay(self.undo_stack.pop(), "undo")

    def redo(self):
        if self.redo_stack:
            self._replay(self.redo_stack.pop(), "redo")

    def _replay(self, rec, direction):
        self.history_changed.emit()
        kind = rec["kind"]
        inverse = direction == "undo"
        try:
            if kind == "rename":
                old, new = rec["old"], rec["new"]
                src, dst = (new, old) if inverse else (old, new)
                os.rename(src, dst)
                from .settings import folder_colors
                folder_colors.rename(src, dst)
                self._store(direction, {"kind": "rename", "old": old, "new": new})
            elif kind in ("move", "copy", "trash", "restore", "create"):
                op = self._inverse_op(rec) if inverse else self._forward_op(rec)
                if op is None:
                    return
                op.finished.connect(lambda o, r=rec, d=direction: self._replayed(o, r, d))
                self.run(op, record=False)
        except OSError as e:
            self.error.emit("Could not undo" if inverse else "Could not redo", str(e))

    def _replayed(self, op, rec, direction):
        if op.error:
            return
        # Store a record that can be replayed in the opposite direction.
        if rec["kind"] == "trash" and direction == "redo":
            rec = op.result                      # fresh trash paths
        elif rec["kind"] == "restore":
            if direction == "undo":              # files went back to the trash
                rec = {"kind": "restore", "trash_pairs": op.result["pairs"]}
            else:
                rec = op.result
        self._store(direction, rec)

    def _store(self, direction, rec):
        (self.redo_stack if direction == "undo" else self.undo_stack).append(rec)
        self.history_changed.emit()

    def _inverse_op(self, rec):
        kind = rec["kind"]
        if kind == "move":
            return move_job([(d, s) for s, d in rec["pairs"]])
        if kind in ("copy",):
            return trash_job([d for _, d in rec["pairs"] if os.path.lexists(d)])
        if kind == "create":
            return trash_job([rec["path"]]) if os.path.lexists(rec["path"]) else None
        if kind == "trash":
            return restore_job([(t, o) for o, t in rec["pairs"]])
        if kind == "restore":
            return trash_job([f for _, f in rec["pairs"] if os.path.lexists(f)])
        return None

    def _forward_op(self, rec):
        kind = rec["kind"]
        if kind == "move":
            return move_job(rec["pairs"])
        if kind == "copy":
            return copy_job(rec["pairs"])
        if kind == "trash":
            return trash_job([o for o, _ in rec["pairs"]])
        if kind == "restore" and "trash_pairs" in rec:
            return restore_job([(t, o) for o, t in rec["trash_pairs"]])
        if kind == "create":
            os.makedirs(rec["path"], exist_ok=True)
            self._store("redo", rec)
            return None
        return None


def _describe(rec):
    kind = rec["kind"]
    n = len(rec.get("pairs", [])) or 1
    return {"rename": "Rename", "move": "Move", "copy": "Copy", "trash": "Trash",
            "restore": "Restore From Trash", "create": "Create"}.get(kind, "Action") + (f" {n} Items" if n > 1 else "")


operations = None


def init():
    global operations
    operations = Operations()
    return operations
