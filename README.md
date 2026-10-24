# Nautilus for Windows

**Files** — a recreation of GNOME Files (Nautilus) for Windows, written in Python and Qt
(PySide6). The layout, menus, keyboard shortcuts, spacing and colors follow the current
Nautilus and libadwaita, and it adds what Windows users expect: the Recycle Bin, drives,
Windows' Properties tabs, and previews for Photoshop, Illustrator, HEIC and Office files.

![Files in dark mode](docs/screenshots/02-pictures-thumbnails.png)

*Not affiliated with or endorsed by the GNOME Project.*

## Get it

**Download:** grab `Files.exe` from the [latest release](../../releases/latest) — a single
file, nothing to install. Settings live in `%APPDATA%\FilesForWindows`.

**Run from source** (Python 3.11+ on Windows 10/11):

```
pip install -r requirements.txt
python -m files_win                 # opens your home folder
python -m files_win C:\Some\Folder
```

Or double-click `Files.pyw` to start without a console window. `--native-frame` uses the
standard Windows title bar instead of the Adwaita header bar.

**Build the .exe:** `pip install pyinstaller`, then run `build_exe.bat` → `Files.exe`.

## Features

### Browsing
- Grid and list views with an **Icon Size slider** (48–256 px grid, 16–64 px list);
  Ctrl+plus/minus, Ctrl+wheel and Ctrl+0 step through presets.
- **Thumbnails** for images, Photoshop `.psd`/`.psb` (read straight from the flattened image,
  fast even on multi-gigabyte files), Illustrator `.ai`, and HEIC/HEIF/AVIF and camera RAW
  through Windows' installed codecs.
- **Tabs**: the New Tab button, the **+** on the tab strip and Ctrl+T open a tab on Other
  Locations; drop files on a tab to move them there, or rest on it while dragging to switch.
- Sidebar with places, bookmarks (Ctrl+D) and drives; pill path bar with breadcrumbs that
  accept drops; Ctrl+L location entry with completion; recursive type-to-search.

| | |
|---|---|
| ![Home with colored folders](docs/screenshots/01-home.png) | ![List view, light](docs/screenshots/03-documents-list-light.png) |
| ![Tabs and Other Locations](docs/screenshots/18-tabs-other-locations.png) | ![Icon size slider](docs/screenshots/19-icon-size-slider.png) |

### Quick Look
Press **Space** on a file — or just hover it and press Space — to preview it. ←/→ step
through the folder, Return opens, Space/Esc closes; it zooms out of the file's icon and back.

Images and animated GIFs · video and audio · PDFs · Word `.docx` · Excel `.xlsx` with sheet
tabs, dates and number formats · CSV · Markdown · text and code · Photoshop · Illustrator · HEIC.

| | |
|---|---|
| ![Quick Look: image](docs/screenshots/04-quicklook-jpg.png) | ![Quick Look: Word](docs/screenshots/05-quicklook-docx.png) |
| ![Quick Look: Excel](docs/screenshots/06-quicklook-xlsx.png) | ![Quick Look: PDF](docs/screenshots/07-quicklook-pdf.png) |
| ![Quick Look: Photoshop](docs/screenshots/08-quicklook-psd.png) | ![Quick Look: Illustrator](docs/screenshots/09-quicklook-ai.png) |

### Properties, with Windows' tabs built in
- **General** — size, contents, dates.
- **Sharing** — status and network path; share or stop sharing (one UAC prompt).
- **Security** — owner, users and groups, the Allow/Deny grid and every entry with inheritance.
- **Location** — move Desktop, Documents, Downloads, Music, Pictures or Videos, or restore the default.
- **Previous Versions** — from File History and restore points: open, copy or restore.
- **Digital Signatures** — every signature verified, with signer, timestamp, countersigners and certificate.
- **Appearance** — folder colors from a palette, saved custom colors, a color code or a screen eyedropper.

| | |
|---|---|
| ![Security tab](docs/screenshots/12-properties-security.png) | ![Digital Signatures tab](docs/screenshots/14-properties-signatures.png) |
| ![Appearance tab](docs/screenshots/11-properties-appearance.png) | ![Location tab](docs/screenshots/13-properties-location.png) |

### Appearance
Light or dark (or follow Windows), the **Adwaita** theme with GNOME's nine accent colors,
or **Catppuccin** — Latte in light style, Mocha, Macchiato or Frappé in dark — with its
fourteen accents. Changes apply instantly.

| | |
|---|---|
| ![Catppuccin Mocha](docs/screenshots/15-catppuccin-mocha.png) | ![Preferences](docs/screenshots/16-preferences-appearance.png) |

### File operations
Copy, move, rename, trash (the Windows Recycle Bin, with restore), delete, compress and
extract, all on background threads with progress and cancel. Drag and drop everywhere
(Ctrl copies, Shift moves, Alt links), a clipboard shared with File Explorer, Nautilus'
Replace / Merge / Keep Both conflict dialog, and undo/redo (Ctrl+Z / Ctrl+Shift+Z).

## Differences from Nautilus

- Search matches file names only (no full-text index).
- Not included: network locations and Connect to Server, batch rename, scripts and extensions.
- Office previews are rendered by Files itself, so layout is approximate; legacy `.doc`/`.xls`
  and PowerPoint show an info card.
- Editing permissions opens Windows' own permission editor.

## Project layout

```
files_win/
  app.py          application state, theme and clipboard
  window.py       the window: header bar, tabs, menus, actions
  slot.py         one tab: location, history, loading, search
  views.py        grid and list views, thumbnails, drag and drop
  model.py        file items, background loaders, the Qt model
  sidebar.py      places, bookmarks, drives
  pathbar.py      breadcrumbs and the location entry
  quicklook.py    the Space-bar previewer
  adobe.py        Photoshop and Illustrator readers
  propertytabs.py / winprops.py   Properties tabs and the Windows data behind them
  dialogs.py      Properties, Preferences, rename, conflicts, folder colors…
  widgets.py      libadwaita look-alikes and dialog animations
  theme.py        Adwaita and Catppuccin palettes and the stylesheet
  icons.py        icons rendered from inline SVG
  operations.py   file operations with progress and undo
  platform_win.py known folders, drives, Recycle Bin, shell thumbnails
  settings.py     preferences, bookmarks, starred, folder colors
```
