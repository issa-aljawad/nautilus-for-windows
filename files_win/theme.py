"""Color themes (Adwaita and Catppuccin) and the Qt stylesheet built from them."""

from PySide6.QtGui import QColor, QFont, QFontDatabase

from . import platform_win
from .settings import settings

# libadwaita 1.6 accent colors (the "accent_bg" value)
ADWAITA_ACCENTS = {
    "blue": "#3584e4", "teal": "#2190a4", "green": "#3a944a", "yellow": "#c88800", "orange": "#ed5b00",
    "red": "#e62d42", "pink": "#d56199", "purple": "#9141ac", "slate": "#6f8396",
}

# https://catppuccin.com/palette
CATPPUCCIN = {
    "latte": dict(
        rosewater="#dc8a78", flamingo="#dd7878", pink="#ea76cb", mauve="#8839ef", red="#d20f39", maroon="#e64553",
        peach="#fe640b", yellow="#df8e1d", green="#40a02b", teal="#179299", sky="#04a5e5", sapphire="#209fb5",
        blue="#1e66f5", lavender="#7287fd", text="#4c4f69", subtext1="#5c5f77", subtext0="#6c6f85",
        overlay2="#7c7f93", overlay1="#8c8fa1", overlay0="#9ca0b0", surface2="#acb0be", surface1="#bcc0cc",
        surface0="#ccd0da", base="#eff1f5", mantle="#e6e9ef", crust="#dce0e8"),
    "frappe": dict(
        rosewater="#f2d5cf", flamingo="#eebebe", pink="#f4b8e4", mauve="#ca9ee6", red="#e78284", maroon="#ea999c",
        peach="#ef9f76", yellow="#e5c890", green="#a6d189", teal="#81c8be", sky="#99d1db", sapphire="#85c1dc",
        blue="#8caaee", lavender="#babbf1", text="#c6d0f5", subtext1="#b5bfe2", subtext0="#a5adce",
        overlay2="#949cbb", overlay1="#838ba7", overlay0="#737994", surface2="#626880", surface1="#51576d",
        surface0="#414559", base="#303446", mantle="#292c3c", crust="#232634"),
    "macchiato": dict(
        rosewater="#f4dbd6", flamingo="#f0c6c6", pink="#f5bde6", mauve="#c6a0f6", red="#ed8796", maroon="#ee99a0",
        peach="#f5a97f", yellow="#eed49f", green="#a6da95", teal="#8bd5ca", sky="#91d7e3", sapphire="#7dc4e4",
        blue="#8aadf4", lavender="#b7bdf8", text="#cad3f5", subtext1="#b8c0e0", subtext0="#a5adcb",
        overlay2="#939ab7", overlay1="#8087a2", overlay0="#6e738d", surface2="#5b6078", surface1="#494d64",
        surface0="#363a4f", base="#24273a", mantle="#1e2030", crust="#181926"),
    "mocha": dict(
        rosewater="#f5e0dc", flamingo="#f2cdcd", pink="#f5c2e7", mauve="#cba6f7", red="#f38ba8", maroon="#eba0ac",
        peach="#fab387", yellow="#f9e2af", green="#a6e3a1", teal="#94e2d5", sky="#89dceb", sapphire="#74c7ec",
        blue="#89b4fa", lavender="#b4befe", text="#cdd6f4", subtext1="#bac2de", subtext0="#a6adc8",
        overlay2="#9399b2", overlay1="#7f849c", overlay0="#6c7086", surface2="#585b70", surface1="#45475a",
        surface0="#313244", base="#1e1e2e", mantle="#181825", crust="#11111b"),
}
CATPPUCCIN_ACCENTS = ("rosewater", "flamingo", "pink", "mauve", "red", "maroon", "peach", "yellow", "green",
                      "teal", "sky", "sapphire", "blue", "lavender")
FLAVOR_NAMES = {"latte": "Latte", "frappe": "Frappé", "macchiato": "Macchiato", "mocha": "Mocha"}
DARK_FLAVORS = ("mocha", "macchiato", "frappe")
THEME_KEYS = ("theme", "color_theme", "accent", "catppuccin_flavor", "catppuccin_accent")


def _c(text, alpha=None):
    c = QColor(text)
    if alpha is not None:
        c.setAlphaF(alpha)
    return c


def _mix(a, b, amount):
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() + (b.red() - a.red()) * amount), round(a.green() + (b.green() - a.green()) * amount),
                  round(a.blue() + (b.blue() - a.blue()) * amount))


class Theme:
    """Adwaita by default; catppuccin() fills the same roles from a Catppuccin flavor."""

    def __init__(self, dark, accent="#3584e4"):
        self.dark = dark
        self.name = "adwaita"
        if dark:
            self.window_bg = _c("#222226")
            self.view_bg = _c("#1d1d20")
            self.headerbar_bg = _c("#1d1d20")
            self.sidebar_bg = _c("#2e2e32")
            self.popover_bg = _c("#36363a")
            self.dialog_bg = _c("#2e2e32")
            self.card_bg = _c("#ffffff", 0.08)
            self.fg = _c("#ffffff")
            self.destructive = _c("#ff7b63")
            self.destructive_bg = _c("#c01c28")
            self.shade = _c("#000006", 0.36)
            self.border = _c("#ffffff", 0.15)
            self.toast_bg = _c("#4a4a4f")
            self.thumb_bg = _c("#2e2e32")
        else:
            self.window_bg = _c("#fafafb")
            self.view_bg = _c("#ffffff")
            self.headerbar_bg = _c("#ffffff")
            self.sidebar_bg = _c("#ebebed")
            self.popover_bg = _c("#ffffff")
            self.dialog_bg = _c("#fafafb")
            self.card_bg = _c("#ffffff")
            self.fg = _c("#000006", 0.8)
            self.destructive = _c("#c30000")
            self.destructive_bg = _c("#e01b24")
            self.shade = _c("#000006", 0.12)
            self.border = _c("#000006", 0.15)
            self.toast_bg = _c("#363639")
            self.thumb_bg = _c("#ffffff")
        self.accent_bg = _c(accent)
        self.accent_fg = _c("#ffffff")
        # libadwaita derives the accent text color: lighter on dark, darker on light
        self.accent = _mix(accent, "#ffffff", 0.35) if dark else _mix(accent, "#000000", 0.15)
        self.success = _c("#26a269") if not dark else _c("#8ff0a4")
        self._icon_fg = QColor(self.fg) if dark else _c("#2e2e33")

    @classmethod
    def catppuccin(cls, flavor, accent):
        p = CATPPUCCIN[flavor]
        dark = flavor != "latte"
        t = cls(dark)
        t.name = "catppuccin"
        t.window_bg = t.view_bg = t.headerbar_bg = t.thumb_bg = _c(p["base"])
        t.sidebar_bg = _c(p["mantle"])
        t.popover_bg = _c(p["surface0"] if dark else p["base"])
        t.dialog_bg = _c(p["base"] if dark else p["mantle"])
        t.card_bg = _c(p["surface0"]) if dark else _c(p["base"])
        t.fg = _c(p["text"])
        t._icon_fg = _c(p["text"])
        t.accent = t.accent_bg = _c(p.get(accent, p["mauve"]))
        t.accent_fg = _c(p["base"])
        t.destructive = t.destructive_bg = _c(p["red"])
        t.success = _c(p["green"])
        t.shade = _c(p["crust"], 0.5 if dark else 0.6)
        t.border = _c(p["overlay0"], 0.35)
        t.toast_bg = _c(p["surface1"] if dark else p["text"])
        return t

    # helpers ------------------------------------------------------------
    def fg_alpha(self, a):
        c = QColor(self.fg)
        c.setAlphaF(self.fg.alphaF() * a if not self.dark else a)
        return c

    def accent_alpha(self, a):
        c = QColor(self.accent_bg)
        c.setAlphaF(a)
        return c

    @property
    def dim_fg(self):
        return self.fg_alpha(0.55)

    def accent_hover(self, pressed=False):
        """Suggested-button hover/pressed shades of the accent."""
        if pressed:
            return _mix(self.accent_bg, "#000000", 0.15)
        return _mix(self.accent_bg, "#ffffff", 0.12)

    def destructive_hover(self):
        return _mix(self.destructive_bg, "#ffffff", 0.12)

    @property
    def icon_fg(self):
        """Opaque-ish color for symbolic icons."""
        return QColor(self._icon_fg)


def rgba(c):
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.3f})"


_current = None


def current():
    global _current
    if _current is None:
        _current = make_theme()
    return _current


def make_theme():
    mode = settings["theme"]
    dark = platform_win.system_prefers_dark() if mode == "system" else mode == "dark"
    if settings["color_theme"] == "catppuccin":
        flavor = settings["catppuccin_flavor"] if dark else "latte"
        return Theme.catppuccin(flavor if flavor in CATPPUCCIN else "mocha", settings["catppuccin_accent"])
    return Theme(dark, ADWAITA_ACCENTS.get(settings["accent"], ADWAITA_ACCENTS["blue"]))


def reload_theme():
    global _current
    _current = make_theme()
    return _current


def ui_font(point_size=10.5, weight=None):
    families = set(QFontDatabase.families())
    family = next((f for f in ("Adwaita Sans", "Inter", "Cantarell", "Segoe UI Variable Text", "Segoe UI")
                   if f in families), "Segoe UI")
    f = QFont(family)
    f.setPointSizeF(point_size)
    f.setHintingPreference(QFont.PreferNoHinting)
    if weight is not None:
        f.setWeight(weight)
    return f


def stylesheet(t):
    fg = rgba(t.fg)
    dim = rgba(t.dim_fg)
    b10 = rgba(t.fg_alpha(0.10))
    b07 = rgba(t.fg_alpha(0.07))
    b15 = rgba(t.fg_alpha(0.15))
    b22 = rgba(t.fg_alpha(0.22))
    b30 = rgba(t.fg_alpha(0.30))
    accent_bg = rgba(t.accent_bg)
    accent = rgba(t.accent)
    border = rgba(t.border)
    pop = rgba(t.popover_bg)
    sel = rgba(t.accent_alpha(0.25))
    return f"""
* {{ color: {fg}; outline: none; }}
QWidget#Window {{ background: {rgba(t.view_bg)}; }}
QWidget#Sidebar, QWidget#SidebarHeader {{ background: {rgba(t.sidebar_bg)}; }}
QWidget#Content, QWidget#ContentHeader, QWidget#BottomBar {{ background: {rgba(t.view_bg)}; }}
QWidget#ViewArea, QAbstractItemView, QAbstractItemView > QWidget {{ background: {rgba(t.view_bg)}; border: none; }}
QLabel {{ background: transparent; }}
QLabel[dim="true"] {{ color: {dim}; }}
QLabel[heading="true"] {{ font-weight: 700; }}
QLabel[title1="true"] {{ font-size: 20pt; font-weight: 800; }}
QLabel[title2="true"] {{ font-size: 15pt; font-weight: 800; }}
QLabel[caption="true"] {{ font-size: 9pt; color: {dim}; }}
QLabel[error="true"] {{ color: {rgba(t.destructive)}; }}

QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 5px; }}
QToolButton:hover {{ background: {b07}; }}
QToolButton:pressed, QToolButton:checked {{ background: {b15}; }}
QToolButton:checked:hover {{ background: {b22}; }}
QToolButton:disabled {{ background: transparent; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton[circular="true"] {{ background: {b10}; border-radius: 12px; padding: 4px; }}
QToolButton[circular="true"]:hover {{ background: {b15}; }}
QToolButton[circular="true"]:pressed {{ background: {b30}; }}
QToolButton[closeButton="true"]:hover {{ background: {rgba(t.destructive_bg)}; }}
QToolButton[osd="true"] {{ background: rgba(0,0,0,0.65); border-radius: 18px; padding: 8px; }}
QToolButton[osd="true"]:hover {{ background: rgba(0,0,0,0.8); }}

QPushButton {{ background: {b10}; border: none; border-radius: 6px; padding: 7px 16px; font-weight: 700; min-height: 20px; }}
QPushButton:hover {{ background: {b15}; }}
QPushButton:pressed {{ background: {b30}; }}
QPushButton:disabled {{ color: {dim}; background: {b07}; }}
QPushButton[flat="true"] {{ background: transparent; }}
QPushButton[flat="true"]:hover {{ background: {b07}; }}
QPushButton[suggested="true"] {{ background: {accent_bg}; color: {rgba(t.accent_fg)}; }}
QPushButton[suggested="true"]:hover {{ background: {rgba(t.accent_hover())}; }}
QPushButton[suggested="true"]:pressed {{ background: {rgba(t.accent_hover(pressed=True))}; }}
QPushButton[suggested="true"]:disabled {{ background: {rgba(t.accent_alpha(0.5))}; color: {rgba(_c(t.accent_fg.name(), 0.6))}; }}
QPushButton[destructive="true"] {{ background: {rgba(t.destructive_bg)}; color: {rgba(t.accent_fg)}; }}
QPushButton[destructive="true"]:hover {{ background: {rgba(t.destructive_hover())}; }}
QPushButton[pill="true"] {{ border-radius: 17px; padding: 8px 28px; }}
QPushButton[link="true"] {{ background: transparent; color: {accent}; font-weight: 400; padding: 2px 4px; }}
QPushButton[link="true"]:hover {{ text-decoration: underline; }}
QPushButton[responseButton="true"] {{ background: transparent; border-radius: 0; padding: 12px; }}
QPushButton[responseButton="true"]:hover {{ background: {b07}; }}
QPushButton[responseButton="true"][destructive="true"] {{ color: {rgba(t.destructive)}; background: transparent; }}
QPushButton[responseButton="true"][destructive="true"]:hover {{ background: {b07}; }}
QPushButton[responseButton="true"][suggested="true"] {{ color: {accent}; background: transparent; }}
QPushButton[responseButton="true"][suggested="true"]:hover {{ background: {b07}; }}

QLineEdit {{ background: {b10}; border: 2px solid transparent; border-radius: 8px; padding: 5px 7px;
             selection-background-color: {rgba(t.accent_alpha(0.35))}; selection-color: {fg}; }}
QLineEdit:focus {{ border: 2px solid {rgba(t.accent_alpha(0.5))}; }}
QLineEdit[error="true"] {{ border: 2px solid {rgba(t.destructive_bg)}; }}
QLineEdit[flatEntry="true"] {{ background: transparent; border: none; padding: 4px 2px; }}

QMenu {{ background: {pop}; border: 1px solid {border}; border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 6px; min-width: 150px; background: transparent; }}
QMenu::item:selected {{ background: {b07}; }}
QMenu::item:disabled {{ color: {dim}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 6px 0px; }}
QMenu::indicator {{ width: 16px; height: 16px; left: 8px; }}
QMenu::item:checked, QMenu::item:unchecked {{ padding-left: 32px; }}
QMenu::right-arrow {{ width: 16px; height: 16px; }}

QToolTip {{ background: {rgba(t.toast_bg)}; color: white; border: none; border-radius: 6px; padding: 5px 8px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {b22}; border-radius: 3px; min-height: 40px; min-width: 40px; margin: 1px; }}
QScrollBar::handle:hover {{ background: {b30}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QHeaderView {{ background: {rgba(t.view_bg)}; border: none; }}
QHeaderView::section {{ background: {rgba(t.view_bg)}; color: {dim}; border: none; padding: 6px 8px;
                         font-size: 9pt; font-weight: 700; }}
QHeaderView::section:hover {{ color: {fg}; }}
QHeaderView::down-arrow, QHeaderView::up-arrow {{ width: 0; height: 0; }}

QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; border: none; border-radius: 9px; padding: 6px 12px; margin: 3px 3px;
                min-width: 120px; color: {dim}; }}
QTabBar::tab:hover {{ background: {b07}; color: {fg}; }}
QTabBar::tab:selected {{ background: {b10}; color: {fg}; }}

QComboBox {{ background: {b10}; border: none; border-radius: 6px; padding: 6px 10px; min-height: 22px; }}
QComboBox:hover {{ background: {b15}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{ background: {pop}; border: 1px solid {border}; border-radius: 8px; padding: 4px;
                               selection-background-color: {b10}; selection-color: {fg}; outline: none; }}
QCheckBox {{ spacing: 10px; background: transparent; }}
QRadioButton {{ spacing: 10px; background: transparent; }}

QProgressBar {{ background: {b15}; border: none; border-radius: 3px; height: 6px; max-height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {accent_bg}; border-radius: 3px; }}

QFrame#Card {{ background: {rgba(t.card_bg)}; border-radius: 12px; border: 1px solid {rgba(t.fg_alpha(0.08))}; }}
QFrame#Separator {{ background: {rgba(t.fg_alpha(0.1))}; }}
QFrame#HSep {{ background: {rgba(t.border)}; max-height: 1px; min-height: 1px; }}
QFrame#PathBar {{ background: {b10}; border-radius: 9px; }}
QWidget#RowCard {{ background: transparent; }}
"""
