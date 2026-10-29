"""Primer (GitHub-Designsystem, https://primer.style) für Qt: Farb-Tokens als QPalette + Stylesheet.

Tokens in ``tokens`` (Qt-frei, auch für die iOS-App).
Hell/Dunkel folgt dem System und wechselt live mit. Fusion als Grundstil, weil er die Palette vollständig nutzt.
Komponenten: Button (default, primary = Default-Knopf), TextInput, ActionList (Listen), Checkbox, Tooltip,
ProgressBar; Toolbar-Knöpfe als „invisible“-Buttons. Gedämpfte Texte: ``widget.setProperty("muted", True)``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QStandardPaths, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from .tokens import PAD_BLOCK, PAD_INLINE, RADIUS_MEDIUM, RADIUS_SMALL, TOKENS

STYLE = """
QToolTip {{
    background: {bgColor-emphasis}; color: {fgColor-onEmphasis}; border: none;
    border-radius: {r_medium}px; padding: 4px 8px;
}}
QLabel[muted="true"] {{ color: {fgColor-muted}; }}
QStatusBar {{ color: {fgColor-muted}; border-top: 1px solid {borderColor-default}; }}
QStatusBar::item {{ border: none; }}

QPushButton {{
    background: {control-bgColor-rest}; color: {control-fgColor-rest};
    border: 1px solid {borderColor-default}; border-radius: {r_medium}px;
    padding: {pad_block}px {pad_inline}px; font-weight: 500;
}}
QPushButton:hover {{ background: {control-bgColor-hover}; }}
QPushButton:pressed {{ background: {control-bgColor-active}; }}
QPushButton:disabled {{
    background: {bgColor-disabled}; color: {fgColor-disabled}; border-color: {borderColor-disabled};
}}
QPushButton:focus {{ border: 1px solid {bgColor-accent-emphasis}; }}
QPushButton:default {{
    background: {button-primary-bgColor-rest}; color: {fgColor-onEmphasis};
    border: 1px solid {borderColor-translucent}; font-weight: 600;
}}
QPushButton:default:hover {{ background: {button-primary-bgColor-hover}; }}
QPushButton:default:pressed {{ background: {button-primary-bgColor-active}; }}
QPushButton:default:disabled {{
    background: {button-primary-bgColor-disabled}; color: {button-primary-fgColor-disabled};
}}

QLineEdit {{
    background: {bgColor-default}; color: {fgColor-default};
    border: 1px solid {borderColor-default}; border-radius: {r_medium}px;
    padding: {pad_block}px {pad_inline}px;
    selection-background-color: {bgColor-accent-emphasis}; selection-color: {fgColor-onEmphasis};
}}
QLineEdit:focus {{ border: 1px solid {bgColor-accent-emphasis}; }}
QLineEdit:disabled {{ background: {bgColor-disabled}; color: {fgColor-disabled}; }}

QListWidget {{
    background: {bgColor-default}; color: {fgColor-default};
    border: 1px solid {borderColor-default}; border-radius: {r_medium}px; padding: 4px; outline: none;
}}
QListWidget::item {{ border: 1px solid transparent; border-radius: {r_medium}px; padding: 4px; }}
QListWidget::item:hover {{ background: {control-transparent-bgColor-hover}; }}
QListWidget::item:selected {{
    background: {bgColor-accent-muted}; color: {fgColor-default}; border-color: {bgColor-accent-emphasis};
}}

QCheckBox {{ spacing: 8px; color: {fgColor-default}; }}
QCheckBox::indicator {{
    width: 14px; height: 14px; border: 1px solid {borderColor-emphasis};
    border-radius: {r_small}px; background: {bgColor-default};
}}
QCheckBox::indicator:hover {{ border-color: {fgColor-muted}; }}
QCheckBox::indicator:checked {{
    background: {bgColor-accent-emphasis}; border-color: {bgColor-accent-emphasis}; image: url("{check}");
}}
QCheckBox::indicator:checked:hover {{
    background: {control-checked-bgColor-hover}; border-color: {control-checked-bgColor-hover};
}}
QCheckBox::indicator:disabled {{ background: {bgColor-disabled}; border-color: {fgColor-disabled}; }}

QToolBar {{
    background: {bgColor-default}; border: none; border-bottom: 1px solid {borderColor-default};
    padding: 4px 8px; spacing: 2px;
}}
QToolBar::separator {{ background: {borderColor-default}; width: 1px; margin: 6px 6px; }}
QToolButton {{
    background: transparent; color: {control-fgColor-rest}; border: none;
    border-radius: {r_medium}px; padding: 4px 8px; font-weight: 500;
}}
QToolButton:hover {{ background: {control-transparent-bgColor-hover}; }}
QToolButton:pressed {{ background: {control-transparent-bgColor-active}; }}
QToolButton:disabled {{ color: {fgColor-disabled}; }}

QProgressBar {{ background: {controlTrack-bgColor-rest}; border: none; border-radius: {r_small}px; }}
QProgressBar::chunk {{ background: {bgColor-accent-emphasis}; border-radius: {r_small}px; }}
"""

_connected = False


def scheme() -> str:
    return "dark" if QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"


def color(token: str, mode: str | None = None) -> QColor:
    """Token als QColor; CSS-Hex mit Alpha (#RRGGBBAA) wird umgerechnet."""
    value = TOKENS[mode or scheme()][token]
    c = QColor(value[:7])
    if len(value) == 9:
        c.setAlpha(int(value[7:], 16))
    return c


def _qss(value: str) -> str:
    if len(value) != 9:
        return value
    r, g, b, a = (int(value[i : i + 2], 16) for i in (1, 3, 5, 7))
    return f"rgba({r}, {g}, {b}, {a})"


def palette(mode: str) -> QPalette:
    def t(name: str) -> QColor:
        return color(name, mode)

    pal = QPalette()
    for role, name in (
        (QPalette.ColorRole.Window, "bgColor-muted"),
        (QPalette.ColorRole.WindowText, "fgColor-default"),
        (QPalette.ColorRole.Base, "bgColor-default"),
        (QPalette.ColorRole.AlternateBase, "bgColor-muted"),
        (QPalette.ColorRole.Text, "fgColor-default"),
        (QPalette.ColorRole.PlaceholderText, "fgColor-muted"),
        (QPalette.ColorRole.Button, "control-bgColor-rest"),
        (QPalette.ColorRole.ButtonText, "control-fgColor-rest"),
        (QPalette.ColorRole.BrightText, "fgColor-onEmphasis"),
        (QPalette.ColorRole.ToolTipBase, "bgColor-emphasis"),
        (QPalette.ColorRole.ToolTipText, "fgColor-onEmphasis"),
        (QPalette.ColorRole.Highlight, "bgColor-accent-emphasis"),
        (QPalette.ColorRole.HighlightedText, "fgColor-onEmphasis"),
        (QPalette.ColorRole.Link, "fgColor-accent"),
        (QPalette.ColorRole.LinkVisited, "fgColor-accent"),
        (QPalette.ColorRole.Accent, "bgColor-accent-emphasis"),
        (QPalette.ColorRole.Light, "bgColor-default"),
        (QPalette.ColorRole.Midlight, "control-bgColor-hover"),
        (QPalette.ColorRole.Mid, "borderColor-default"),
        (QPalette.ColorRole.Dark, "borderColor-emphasis"),
        (QPalette.ColorRole.Shadow, "borderColor-emphasis"),
    ):
        pal.setColor(role, t(name))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, t("fgColor-disabled"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, t("bgColor-disabled"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, t("bgColor-disabled"))
    return pal


def _check_image() -> str:
    """Weißes Häkchen (Checkbox) als PNG, mit @2x für Retina; Qt-Stylesheets brauchen dafür eine Datei."""
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "primer-check.png"
    for scale, name in ((1, path), (2, path.with_name("primer-check@2x.png"))):
        if name.exists():
            continue
        pix = QPixmap(14 * scale, 14 * scale)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(
            QPen(
                QColor("#ffffff"),
                1.8 * scale,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        stroke = QPainterPath(QPointF(3.5 * scale, 7.2 * scale))
        stroke.lineTo(6 * scale, 9.7 * scale)
        stroke.lineTo(10.5 * scale, 4.5 * scale)
        p.drawPath(stroke)
        p.end()
        pix.save(str(name))
    return path.as_posix()


def apply(*_args: object) -> None:
    """Primer auf die laufende App anwenden; folgt danach dem Wechsel zwischen Hell und Dunkel."""
    global _connected
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        raise RuntimeError("primer.apply braucht eine laufende QApplication")
    mode = scheme()
    app.setStyle("Fusion")
    app.setPalette(palette(mode))
    app.setStyleSheet(
        STYLE.format(
            **{k: _qss(v) for k, v in TOKENS[mode].items()},
            r_small=RADIUS_SMALL,
            r_medium=RADIUS_MEDIUM,
            pad_block=PAD_BLOCK,
            pad_inline=PAD_INLINE,
            check=_check_image(),
        )
    )
    if not _connected:
        QApplication.styleHints().colorSchemeChanged.connect(apply)
        _connected = True
