"""Persistent preferences, bookmarks, starred files and recent files."""

import json
import os

APP_DIR = os.environ.get("FILES_WIN_CONFIG") or os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "FilesForWindows")

DEFAULTS = {
    "theme": "system",            # system | light | dark
    "color_theme": "adwaita",     # adwaita | catppuccin
    "accent": "blue",             # theme.ADWAITA_ACCENTS
    "catppuccin_flavor": "mocha", # dark flavor: mocha | macchiato | frappe (light is always latte)
    "catppuccin_accent": "mauve", # theme.CATPPUCCIN_ACCENTS
    "custom_colors": [],          # saved folder colors, "#rrggbb"
    "view_mode": "grid",          # grid | list
    "grid_zoom": 2,               # older preset index, used until an icon size is set
    "list_zoom": 1,
    "grid_icon_size": None,       # pixels, from the icon size slider
    "list_icon_size": None,
    "show_hidden": False,
    "sort": "name",               # name | modified | size | type
    "sort_reverse": False,
    "folders_first": True,
    "click_policy": "double",     # double | single
    "show_delete_permanently": False,
    "show_create_link": False,
    "thumbnails": True,
    "count_folder_items": True,
    "sidebar_visible": True,
    "window_size": [890, 550],
    "maximized": False,
}


class _JsonFile:
    def __init__(self, name, default):
        self.path = os.path.join(APP_DIR, name)
        self.data = default
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, type(default)):
                self.data = loaded
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass


class Settings(_JsonFile):
    def __init__(self):
        super().__init__("settings.json", {})
        for key, value in DEFAULTS.items():
            self.data.setdefault(key, value)

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()


def _norm(path):
    return os.path.normcase(os.path.normpath(path))


class PathList(_JsonFile):
    """An ordered list of paths stored on disk (starred, bookmarks, recent)."""

    def __init__(self, name, limit=None):
        super().__init__(name, [])
        self.limit = limit
        self.data = [p for p in self.data if isinstance(p, str)]

    def __contains__(self, path):
        n = _norm(path)
        return any(_norm(p) == n for p in self.data)

    def __iter__(self):
        return iter(list(self.data))

    def add(self, path, front=False):
        self.remove(path, save=False)
        if front:
            self.data.insert(0, path)
        else:
            self.data.append(path)
        if self.limit:
            del self.data[self.limit:]
        self.save()

    def remove(self, path, save=True):
        n = _norm(path)
        self.data = [p for p in self.data if _norm(p) != n]
        if save:
            self.save()

    def rename(self, old, new):
        n = _norm(old)
        self.data = [new if _norm(p) == n else p for p in self.data]
        self.save()


class Bookmarks(_JsonFile):
    """Bookmarks keep an optional custom label: [{"path":..., "label":...}]."""

    def __init__(self):
        super().__init__("bookmarks.json", [])
        self.data = [b for b in self.data if isinstance(b, dict) and "path" in b]

    def __iter__(self):
        return iter(list(self.data))

    def __contains__(self, path):
        n = _norm(path)
        return any(_norm(b["path"]) == n for b in self.data)

    def add(self, path):
        if path not in self:
            self.data.append({"path": path, "label": ""})
            self.save()

    def remove(self, path):
        n = _norm(path)
        self.data = [b for b in self.data if _norm(b["path"]) != n]
        self.save()

    def set_label(self, path, label):
        n = _norm(path)
        for b in self.data:
            if _norm(b["path"]) == n:
                b["label"] = label
        self.save()

    def move(self, path, new_index):
        n = _norm(path)
        items = [b for b in self.data if _norm(b["path"]) == n]
        if not items:
            return
        self.data = [b for b in self.data if _norm(b["path"]) != n]
        self.data.insert(max(0, min(new_index, len(self.data))), items[0])
        self.save()


class FolderColors(_JsonFile):
    """Custom folder icon colors: {normcase(path): "#rrggbb"}."""

    def __init__(self):
        super().__init__("folder_colors.json", {})
        self.data = {k: v for k, v in self.data.items() if isinstance(k, str) and isinstance(v, str)}

    def get(self, path):
        return self.data.get(_norm(path)) if self.data else None

    def set(self, path, color):
        if color:
            self.data[_norm(path)] = color
        else:
            self.data.pop(_norm(path), None)
        self.save()

    def rename(self, old, new):
        """Carries colors along when a folder is renamed or moved, including folders inside it."""
        old, new = _norm(old), _norm(new)
        moved = {k: v for k, v in self.data.items() if k == old or k.startswith(old + os.sep)}
        if moved:
            for k, v in moved.items():
                del self.data[k]
                self.data[new + k[len(old):]] = v
            self.save()


settings = Settings()
folder_colors = FolderColors()
starred = PathList("starred.json")
recent = PathList("recent.json", limit=200)
bookmarks = Bookmarks()
