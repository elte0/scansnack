"""Primer (wie die Desktop-App) für Toga auf iOS: Farben aus ``scansnap.adapters.qt.tokens``, Rest nativ per rubicon.

Toga kennt weder Eckenradius noch Rahmen; das setzen die Helfer hier direkt am UIView-Layer. Hell/Dunkel:
:func:`mode` liest den Systemzustand, die App baut die Oberfläche bei einem Wechsel neu auf.
"""

from __future__ import annotations

from collections.abc import Callable

from rubicon.objc import SEL, CGRectMake, NSObject, ObjCClass, objc_method
from toga.colors import rgb

from scansnap.adapters.qt.tokens import RADIUS_MEDIUM, TOKENS

UIColor = ObjCClass("UIColor")
UIScreen = ObjCClass("UIScreen")
UITapGestureRecognizer = ObjCClass("UITapGestureRecognizer")
UIImage = ObjCClass("UIImage")


def mode() -> str:
    # UIUserInterfaceStyleDark = 2
    return "dark" if UIScreen.mainScreen.traitCollection.userInterfaceStyle == 2 else "light"


class Theme:
    def __init__(self, scheme: str):
        self.scheme = scheme
        self.tokens = TOKENS[scheme]

    def __getitem__(self, token: str) -> rgb:
        """Token als Toga-Farbe (Hex mit Alpha versteht Travertino nicht, daher als Farbobjekt)."""
        return rgb(*self.rgba(token))

    def rgba(self, token: str) -> tuple[int, int, int, float]:
        v = self.tokens[token]
        r, g, b = (int(v[i : i + 2], 16) for i in (1, 3, 5))
        return r, g, b, (int(v[7:9], 16) / 255 if len(v) == 9 else 1.0)

    def ui(self, token: str):
        r, g, b, a = self.rgba(token)
        return UIColor.colorWithRed(r / 255, green=g / 255, blue=b / 255, alpha=a)


def rounded(
    widget, theme: Theme, border: str | None = "borderColor-default", radius: float = RADIUS_MEDIUM, width: float = 1.0
) -> None:
    layer = widget._impl.native.layer
    layer.cornerRadius = radius
    layer.masksToBounds = True
    if border:
        layer.borderWidth = width
        layer.borderColor = theme.ui(border).CGColor
    else:
        layer.borderWidth = 0


def switch_tint(switch, theme: Theme) -> None:
    switch._impl.native_switch.onTintColor = theme.ui("bgColor-accent-emphasis")


def text_field(widget, theme: Theme) -> None:
    """TextInput wie Primer: weiß, grauer Rahmen, Innenabstand."""
    native = widget._impl.native
    native.borderStyle = 0  # UITextBorderStyleNone
    native.backgroundColor = theme.ui("bgColor-default")
    native.textColor = theme.ui("fgColor-default")
    pad = ObjCClass("UIView").alloc().initWithFrame(CGRectMake(0, 0, 12, 1))
    native.leftView = pad
    native.leftViewMode = 3  # UITextFieldViewModeAlways
    rounded(widget, theme)


def tint_window(window, theme: Theme) -> None:
    """Akzentfarbe für Navigationsleiste, Tab-Leiste und System-Knöpfe."""
    window._impl.native.tintColor = theme.ui("fgColor-accent")


def tab_symbols(tabs, names: list[str]) -> None:
    """SF-Symbole für die Tab-Leiste (Toga erwartet sonst Bilddateien)."""
    controllers = tabs._impl.native_controller.viewControllers
    for i, name in enumerate(names):
        controllers.objectAtIndex(i).tabBarItem.image = UIImage.systemImageNamed(name)


class _TapTarget(NSObject):
    @objc_method
    def tapped_(self, recognizer) -> None:
        self.callback()


_targets: list = []  # Gesten halten ihr Ziel nicht fest


def _pass_touches(widget) -> None:
    """Unteransichten (Texte, Bilder) nehmen keine Berührungen an, sonst kommt der Tipp nicht bei der Zeile an."""
    for child in widget.children:
        child._impl.native.userInteractionEnabled = False
        _pass_touches(child)


def on_tap(widget, callback: Callable[[], None]) -> None:
    """Antippen einer Box (Listenzeile) – Toga-Boxen haben keinen eigenen Handler."""
    target = _TapTarget.alloc().init()
    target.callback = callback
    _targets.append(target)
    native = widget._impl.native
    native.userInteractionEnabled = True
    _pass_touches(widget)
    native.addGestureRecognizer(UITapGestureRecognizer.alloc().initWithTarget(target, action=SEL("tapped:")))
