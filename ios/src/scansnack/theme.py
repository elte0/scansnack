"""Primer (as in the desktop app) for Toga on iOS: colors from ``scansnap.adapters.qt.tokens``, the rest via rubicon.

Toga knows neither corner radius nor borders; the helpers here set them directly on the UIView layer. Light/dark:
:func:`mode` reads the system state, the app rebuilds the UI on a switch.
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
        """Token as a Toga color (Travertino does not understand hex with alpha, hence a color object)."""
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
    """TextInput like Primer: white, gray border, padding."""
    native = widget._impl.native
    native.borderStyle = 0  # UITextBorderStyleNone
    native.backgroundColor = theme.ui("bgColor-default")
    native.textColor = theme.ui("fgColor-default")
    pad = ObjCClass("UIView").alloc().initWithFrame(CGRectMake(0, 0, 12, 1))
    native.leftView = pad
    native.leftViewMode = 3  # UITextFieldViewModeAlways
    rounded(widget, theme)


def tint_window(window, theme: Theme) -> None:
    """Accent color for the navigation bar, tab bar and system buttons."""
    window._impl.native.tintColor = theme.ui("fgColor-accent")


def tab_symbols(tabs, names: list[str]) -> None:
    """SF Symbols for the tab bar (Toga otherwise expects image files)."""
    controllers = tabs._impl.native_controller.viewControllers
    for i, name in enumerate(names):
        controllers.objectAtIndex(i).tabBarItem.image = UIImage.systemImageNamed(name)


class _TapTarget(NSObject):
    @objc_method
    def tapped_(self, recognizer) -> None:
        self.callback()


_targets: list = []  # gestures do not retain their target


def _pass_touches(widget) -> None:
    """Subviews (texts, images) do not accept touches, otherwise the tap does not reach the row."""
    for child in widget.children:
        child._impl.native.userInteractionEnabled = False
        _pass_touches(child)


def on_tap(widget, callback: Callable[[], None]) -> None:
    """Tapping a box (list row) – Toga boxes have no handler of their own."""
    target = _TapTarget.alloc().init()
    target.callback = callback
    _targets.append(target)
    native = widget._impl.native
    native.userInteractionEnabled = True
    _pass_touches(widget)
    native.addGestureRecognizer(UITapGestureRecognizer.alloc().initWithTarget(target, action=SEL("tapped:")))
