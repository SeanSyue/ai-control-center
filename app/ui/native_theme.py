"""Day and night palettes for the native Qt chrome.

Night mode used to stop at the window's edge. The six redesigned panels are
HTML and repaint themselves from `aicc.css`, but the window around them - tab
bar, toolbar, tables, the live log, every dialog - was a single hard-coded light
stylesheet. Switching to night gave you dark panels mounted in a white frame.

Rather than keep a second copy of 165 lines of QSS, the stylesheet is written
once with `$role` placeholders and filled from one of two palettes. A rule can
therefore never exist in one mode and be forgotten in the other, which is the
failure a duplicated stylesheet invites.

Two things are worth knowing before editing:

* **`string.Template`, not `str.format`.** QSS is full of `{ }`, so `format()`
  would treat every rule block as a field. `$name` appears nowhere in QSS.

* **The day palette reproduces the previous stylesheet exactly.** That was the
  point of the split: adding night must not restyle day. `test_native_theme.py`
  holds the two properties that keep it honest - the two modes stay structurally
  identical, and no colour may be written inline past this file.

Night values come from `aicc.css`'s `html[data-mode="night"]` tokens so the
frame and the panels are the same colours, not merely both dark. Where the
native chrome needs a surface the web design has no token for (the tinted
状態 cards, for one), the value is picked to sit between two real tokens.
"""
from __future__ import annotations

from string import Template

# Roles, not colours. A name says what the colour is for, so the night column
# can answer differently without the rule below having to know.
#
# Several roles share a value in day and split in night - `surface` and `field`
# are both white on a light window but a card and a text field are different
# depths on a dark one. That is why they are separate roles even though the day
# column repeats itself.
DAY: dict[str, str] = {
    # surfaces
    "bg":              "#F4F7FB",   # window
    "surface":         "#FFFFFF",   # cards, buttons
    "surface_alt":     "#F9FBFE",   # alternating table rows
    "field":           "#FFFFFF",   # text inputs, combo boxes, tables
    "inset":           "#EDF2F8",   # recessed: path label, read-only fields
    "disabled_bg":     "#F4F7FB",
    "header_bg":       "#F4F7FB",   # table header, scrollbar track
    # text
    "text":            "#171B2C",
    "text_muted":      "#667085",
    "text_disabled":   "#98A2B3",
    "on_accent":       "#FFFFFF",   # text on the primary button
    # lines
    "line":            "#D6DEEA",
    "line_soft":       "#E7ECF4",
    "line_hover":      "#AEBAD0",
    # accent
    "accent":          "#252B68",
    "accent_hover":    "#343C8F",
    "accent_pressed":  "#1A1F4F",
    "primary_bg":      "#252B68",   # may be a gradient, hence its own role
    "primary_hover":   "#343C8F",
    "primary_pressed": "#1A1F4F",
    "accent_soft":     "#E9EDFA",   # checked buttons, selected table rows
    "accent_soft_line": "#C4CDEC",
    "focus":           "#252B68",
    "tab_active":      "#252B68",
    "selection_bg":    "#E9EDFA",
    "selection_fg":    "#252B68",
    "hover":           "#E7EDF7",
    "pressed":         "#DCE5F1",
    # run states
    "idle_bg":         "#F1F4F9",
    "running_bg":      "#E9EDFA",
    "running_line":    "#C4CDEC",
    "pass_fg":         "#2D7A64",
    "pass_bg":         "#EAF4F1",
    "pass_line":       "#BFDCD3",
    "ask_fg":          "#7C6125",
    "ask_bg":          "#F8F2E4",
    "ask_line":        "#E3D3AC",
    "fix_fg":          "#96553A",
    "fix_bg":          "#F8EFEA",
    "fix_line":        "#E7CDBE",
    # status dots
    "dot_running":      "#63C9F4",
    "dot_running_ring": "#252B68",
    "dot_ask":          "#C4A15A",
    "dot_fix":          "#B66A47",
    # destructive actions
    "danger_fg":         "#A43C54",
    "danger_line":       "#E4C2CB",
    "danger_hover":      "#FBEFF2",
    "danger_pressed":    "#F5DCE2",
    "danger_pressed_fg": "#83293F",
}

NIGHT: dict[str, str] = {
    # --bg / --panel / --surface / --surface2 from aicc.css
    "bg":              "#0A0E24",
    "surface":         "#161C3E",
    "surface_alt":     "#161C3E",
    "field":           "#11162F",
    "inset":           "#0A0E24",   # recessed = darker than the card it sits on
    "disabled_bg":     "#11162F",
    "header_bg":       "#11162F",
    # --text / --text-2 / --text-3
    "text":            "#F0F3FF",
    "text_muted":      "#B6C0E8",
    "text_disabled":   "#7B85BC",
    "on_accent":       "#FFFFFF",
    # --line / --line-soft
    "line":            "#2C3468",
    "line_soft":       "#222950",
    "line_hover":      "#4FD1FF",   # --aqua: on dark, hover lights up
    # --aqua, and --grad-btn for the primary button
    "accent":          "#4FD1FF",
    "accent_hover":    "#9FE9FF",
    "accent_pressed":  "#2FA9D8",
    "primary_bg":      "qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                       " stop:0 #4FD1FF, stop:1 #8A7BF0)",
    "primary_hover":   "qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                       " stop:0 #9FE9FF, stop:1 #B5AAFF)",
    "primary_pressed": "qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                       " stop:0 #2FA9D8, stop:1 #6B5CD0)",
    "accent_soft":     "#1C2350",
    "accent_soft_line": "#2C3468",
    "focus":           "#4FD1FF",
    "tab_active":      "#4FD1FF",
    "selection_bg":    "#1E6E9E",   # .fabric-aqua's base, the design's own pick
    "selection_fg":    "#F0F3FF",
    "hover":           "#1C2350",
    "pressed":         "#222950",
    # Tinted state cards: between --surface and the state's hue, dark enough
    # that white text still carries.
    "idle_bg":         "#161C3E",
    "running_bg":      "#152A4E",
    "running_line":    "#1E6E9E",
    "pass_fg":         "#5FF0C8",   # --mint
    "pass_bg":         "#122E2A",
    "pass_line":       "#2C6E5E",
    "ask_fg":          "#FFD98E",   # --gold
    "ask_bg":          "#2C2718",
    "ask_line":        "#7A6330",   # .fabric-gold's base
    "fix_fg":          "#FF7BA6",   # --rose
    "fix_bg":          "#2E1B28",
    "fix_line":        "#7A3A54",
    "dot_running":      "#4FD1FF",
    "dot_running_ring": "#1E6E9E",
    "dot_ask":          "#FFD98E",
    "dot_fix":          "#FF7BA6",
    "danger_fg":         "#FF7BA6",
    "danger_line":       "#7A3A54",
    "danger_hover":      "#2E1B28",
    "danger_pressed":    "#3A2030",
    "danger_pressed_fg": "#FFA7C4",
}

PALETTES = {"day": DAY, "night": NIGHT}


_TEMPLATE = Template(
    """
/* Deliberately NOT "QMainWindow, QWidget": the QWidget type
   selector also matches QCheckBox, and a matched background makes
   QStyleSheetStyle take over CE_CheckBox drawing. With no
   ::indicator rule to draw with, the checked state lost its box
   entirely and rendered as a bare tick - and the disabled-checked
   state became indistinguishable from the enabled one. QDialog is
   listed because dialogs are top-level widgets that the
   QMainWindow selector does not reach; without it they fall back
   to the system font (9pt) instead of the designed 14px.
   Container widgets inside the window need no rule of their own -
   they are transparent and show the window's background through. */
QMainWindow, QDialog { background: $bg; color: $text;
    font-family: "Microsoft JhengHei"; font-size: 14px; }

/* The page inside the scroll area. QMainWindow's background above never
   reaches it - the scroll area's content widget covers the window, and its
   24px layout margins are the only gap you can see through. Unstyled, that gap
   was Qt's default #EFEFEF: slightly off in day mode, and a pale border around
   a dark window at night. An id selector, not "QWidget", for the CE_CheckBox
   reason above; build_ui() also sets WA_StyledBackground, without which a bare
   QWidget ignores this rule. */
QWidget#pageContent { background: $bg; }

QLabel { background: transparent; color: $text; font-size: 14px; }
QLabel:disabled { color: $text_disabled; }

QLabel#titleLabel    { font-size: 24px; font-weight: bold; color: $text; }
QLabel#subtitleLabel { font-size: 13px; color: $text_muted; }
QLabel#sectionLabel  { font-size: 12px; font-weight: bold; color: $text_muted; }
QLabel#projectName   { font-size: 16px; font-weight: bold; color: $text; }
QLabel#pathLabel     { font-size: 13px; color: $text_muted; background: $inset;
                       border: 1px solid $line_soft; border-radius: 4px; padding: 2px 6px;
                       font-family: "Consolas"; }
QLabel#stageLabel    { font-size: 14px; font-weight: bold; color: $text; }
QLabel#elapsedLabel  { font-size: 14px; font-weight: bold; color: $text_muted;
                       font-family: "Consolas"; }

QFrame#card { background: $surface; border: 1px solid $line;
    border-radius: 10px; padding: 16px; }

/* 狀態標籤：共用底規格，各狀態只換三個顏色 */
QLabel#statusIdle, QLabel#statusRunning, QLabel#statusPass,
QLabel#statusAsk,  QLabel#statusFix {
    font-size: 13px; font-weight: bold; border-radius: 4px;
    border: 1px solid; padding: 3px 10px; }
QLabel#statusIdle    { color:$text_muted; background:$idle_bg; border-color:$line; }
QLabel#statusRunning { color:$selection_fg; background:$running_bg; border-color:$running_line; }
QLabel#statusPass    { color:$pass_fg; background:$pass_bg; border-color:$pass_line; }
QLabel#statusAsk     { color:$ask_fg; background:$ask_bg; border-color:$ask_line; }
QLabel#statusFix     { color:$fix_fg; background:$fix_bg; border-color:$fix_line; }

/* 狀態圓點：固定 14x14 的 QLabel，執行中用雙色 */
QLabel#dotIdle, QLabel#dotRunning, QLabel#dotPass,
QLabel#dotAsk,  QLabel#dotFix {
    min-width: 10px; max-width: 10px; min-height: 10px; max-height: 10px;
    border-radius: 5px; }
QLabel#dotIdle    { background:$text_disabled; }
QLabel#dotRunning { background:$dot_running; border: 2px solid $dot_running_ring;
                    border-radius: 7px; }   /* 10px + 兩側 2px 邊框 = 14px，半徑要跟著加 */
QLabel#dotPass    { background:$pass_fg; }
QLabel#dotAsk     { background:$dot_ask; }
QLabel#dotFix     { background:$dot_fix; }

QPushButton { background:$surface; color:$text; border:1px solid $line;
    border-radius:6px; padding:8px 14px; font-size:14px; }
QPushButton:hover    { background:$hover; border-color:$line_hover; }
QPushButton:pressed  { background:$pressed; border-color:$line_hover; }
QPushButton:checked  { background:$accent_soft; color:$selection_fg; border-color:$accent_soft_line; }
QPushButton:disabled { background:$disabled_bg; color:$text_disabled; border-color:$line_soft; }

QPushButton#primaryButton { background:$primary_bg; color:$on_accent;
    border:1px solid $accent; border-radius:6px; padding:11px 26px;
    font-size:15px; font-weight:bold; }
QPushButton#primaryButton:hover    { background:$primary_hover; border-color:$accent_hover; }
QPushButton#primaryButton:pressed  { background:$primary_pressed; border-color:$accent_pressed; }
QPushButton#primaryButton:disabled { background:$pressed; color:$text_disabled;
    border-color:$pressed; }

QPushButton#dangerButton { background:$surface; color:$danger_fg;
    border:1px solid $danger_line; }
QPushButton#dangerButton:hover    { background:$danger_hover; border-color:$danger_fg; }
QPushButton#dangerButton:pressed  { background:$danger_pressed; color:$danger_pressed_fg;
    border-color:$danger_pressed_fg; }
QPushButton#dangerButton:disabled { background:$disabled_bg; color:$text_disabled;
    border-color:$line_soft; }

/* 右側「設定與同步」次要按鈕：無底無框，只有 hover 才浮出來 */
QPushButton[flatTool="true"] { background: transparent; border: 1px solid transparent;
    color:$text_muted; font-size:13px; padding:7px 12px; }
QPushButton[flatTool="true"]:hover   { background:$hover; border-color:$line;
    color:$text; }
QPushButton[flatTool="true"]:pressed { background:$pressed; }
/* Qt gives an attribute selector the same specificity as a
   pseudo-class (both 0x10), so the flatTool rules above tie with
   QPushButton:disabled and win on source order alone - which left
   these buttons looking fully enabled while disabled, with clicks
   silently doing nothing. Restating :disabled here is what puts
   the disabled look back. */
QPushButton[flatTool="true"]:disabled { background: transparent;
    border-color: transparent; color:$text_disabled; }

/* 分隔執行區與工具區的細線 */
QFrame#toolSeparator { background:$line; border: none; }

/* 執行中／等待決策時，整張狀態卡換底色，遠看就分得出來 */
QFrame#card[runState="statusRunning"] { background:$running_bg;
    border-color:$running_line; }
QFrame#card[runState="statusAsk"]     { background:$ask_bg;
    border-color:$ask_line; }
QFrame#card[runState="statusFix"]     { background:$fix_bg;
    border-color:$fix_line; }

QComboBox { background:$field; color:$text; border:1px solid $line;
    border-radius:6px; padding:7px 12px; font-size:14px; }
QComboBox:hover     { border-color:$line_hover; }
QComboBox:focus, QComboBox:on { border-color:$focus; }
QComboBox:disabled  { background:$disabled_bg; color:$text_disabled; border-color:$line_soft; }
/* The design asked for QComboBox::drop-down { border:none; width:22px }.
   Dropped deliberately: styling ::drop-down makes Qt stop drawing
   its native arrow, and QSS cannot replace it without an image
   asset - the web trick of a 0x0 box with borders renders as a
   solid dash in Qt, not a triangle (verified on screen). Leaving
   the sub-control unstyled keeps a real arrow, which matters more
   than shaving the drop-down's bevel. */
QComboBox QAbstractItemView { background:$field; border:1px solid $line;
    selection-background-color:$selection_bg; selection-color:$selection_fg; outline: none; }

/* QLineEdit and QSpinBox were never in the original sheet at all, so they kept
   Qt's default white field. That was invisible in day mode - white on a white
   card - and is a lit rectangle on a dark one. The conversation search box is
   the visible case; the other line edits are the internal ones inside spin
   boxes and combo boxes, which is why the spin box's own field is left
   transparent rather than painted twice. The up/down buttons are deliberately
   unstyled: as with QComboBox::drop-down, styling the sub-control makes Qt stop
   drawing its arrows and QSS has no way to draw replacements. */
QLineEdit { background:$field; color:$text; border:1px solid $line;
    border-radius:6px; padding:7px 10px; font-size:14px;
    selection-background-color:$selection_bg; selection-color:$selection_fg; }
QLineEdit:focus     { border-color:$focus; }
QLineEdit:disabled  { background:$disabled_bg; color:$text_disabled; border-color:$line_soft; }

QSpinBox { background:$field; color:$text; border:1px solid $line;
    border-radius:6px; padding:6px 8px; font-size:14px;
    selection-background-color:$selection_bg; selection-color:$selection_fg; }
QSpinBox:focus    { border-color:$focus; }
QSpinBox:disabled { background:$disabled_bg; color:$text_disabled; border-color:$line_soft; }
QSpinBox QLineEdit { background: transparent; border: none; border-radius: 0;
    padding: 0; }

/* The tab bar's overflow arrows. They appear only when the window is too
   narrow for every tab, which is exactly when nobody is looking for a styling
   bug - so they sat at default light grey. */
QTabBar QToolButton { background:$surface; color:$text; border:1px solid $line;
    border-radius:4px; }
QTabBar QToolButton:hover    { background:$hover; border-color:$line_hover; }
QTabBar QToolButton:disabled { background:$disabled_bg; color:$text_disabled;
    border-color:$line_soft; }

/* Checkbox and radio labels take their colour from the palette, not from the
   QLabel rule above, so without this they stayed near-black on a dark panel.
   Colour and font only - nothing that touches the box. Adding
   "background: transparent" here is enough to hand CE_CheckBox to
   QStyleSheetStyle, which then draws a bare tick with no indicator box at all;
   test_quiet_celestial_styles.py caught exactly that when this rule first went
   in, so the omission is load-bearing, not an oversight. */
QCheckBox, QRadioButton { color:$text; font-size:14px; }
QCheckBox:disabled, QRadioButton:disabled { color:$text_disabled; }

/* The scroll area itself: frameless already, but it paints a Window-coloured
   backdrop behind #pageContent while the layout settles. */
QScrollArea { background:$bg; border: none; }

QPlainTextEdit { background:$field; color:$text; border:1px solid $line;
    border-radius:6px; padding:12px 14px;
    font-family:"Consolas"; font-size:13px;
    selection-background-color:$selection_bg; selection-color:$selection_fg; }
QPlainTextEdit[readOnly="true"] { background:$inset; color:$text_muted; }
/* The readOnly inset above is meant for short read-only fields.
   The live execution log is also read-only but is the thing you
   actually stare at while a workflow runs - muted text on a
   recessed panel costs real legibility there, so it keeps the
   full-contrast field surface instead. */
QPlainTextEdit#liveLog { background:$field; color:$text; }

QTabBar::tab { background: transparent; color:$text_muted; font-size:14px;
    padding:11px 18px; border: none; border-bottom: 2px solid transparent; }
QTabBar::tab:hover    { background:$hover; color:$text; }
QTabBar::tab:selected { color:$tab_active; font-weight: bold;
    border-bottom: 2px solid $tab_active; }
QTabWidget::pane { background:$field; border:1px solid $line;
    border-top: none; border-bottom-left-radius:10px; border-bottom-right-radius:10px; }

QHeaderView::section { background:$header_bg; color:$text_muted; font-size:12px;
    font-weight:bold; padding:10px 12px; border: none;
    border-top:1px solid $line; border-bottom:1px solid $line; }
QTableWidget, QTableView { background:$field; alternate-background-color:$surface_alt;
    gridline-color:$line_soft; border:none; font-size:14px; }
QTableView::item { padding: 12px; border-bottom: 1px solid $line_soft; }
QTableView::item:selected { background:$accent_soft; color:$selection_fg; }

/* Splitter handles. These used to be two local setStyleSheet calls inside
   session_panel.py, which meant they stayed light grey when everything around
   them went dark - a widget's own stylesheet beats the window's. The
   declarations are unchanged; only the colours became roles. */
QSplitter#previewSplitter::handle:horizontal { background:$line; width: 8px;
    margin: 22px 2px 2px 2px; border-radius: 3px; }
QSplitter#previewSplitter::handle:horizontal:hover { background:$line_hover; }
QSplitter#sessionSplitter::handle:vertical { background:$line; height: 10px;
    margin: 2px 18px; border-radius: 4px; }
QSplitter#sessionSplitter::handle:vertical:hover { background:$line_hover; }

/* Both orientations must be styled. Qt hands the whole scrollbar to
   QStyleSheetStyle as soon as any part of it is styled, so defining
   ::handle only for :vertical left horizontal scrollbars with no
   slider at all - a flat line you cannot grab. Confirmed by eye:
   the vertical bar had its rounded handle, the horizontal one was
   empty. */
QScrollBar:vertical { background:$header_bg; width:12px; border:none; margin:0; }
QScrollBar::handle:vertical { background:$line; border-radius:6px; min-height:32px; }
QScrollBar::handle:vertical:hover { background:$line_hover; }
QScrollBar:horizontal { background:$header_bg; height:12px; border:none; margin:0; }
QScrollBar::handle:horizontal { background:$line; border-radius:6px; min-width:32px; }
QScrollBar::handle:horizontal:hover { background:$line_hover; }
/* Unqualified on purpose: it removes the stepper buttons on both
   orientations, which is the intent. */
QScrollBar::add-line, QScrollBar::sub-line { height:0; width:0; }
QScrollBar::add-page, QScrollBar::sub-page { background:transparent; }
"""
)


def stylesheet(mode: str) -> str:
    """The whole native chrome's QSS for `mode`, which is "day" or "night"."""
    palette = PALETTES.get(mode)
    if palette is None:
        raise ValueError(f"不認識的模式 {mode!r}，只有 day 與 night")
    # substitute(), not safe_substitute(): a typo'd role should fail loudly at
    # startup rather than ship a stylesheet with "$fied" printed in it.
    return _TEMPLATE.substitute(palette)
