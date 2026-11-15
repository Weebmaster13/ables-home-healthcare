"""Contrast regression tests (WCAG 1.4.3 text / 1.4.11 UI), computed locally."""
import re
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "static" / "css" / "style.css"


def luminance(rgb):
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    red, green, blue = (channel(v) for v in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def ratio(hex_a, hex_b):
    def to_rgb(value):
        value = value.lstrip("#")
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))

    first, second = sorted(
        [luminance(to_rgb(hex_a)), luminance(to_rgb(hex_b))], reverse=True)
    return (first + 0.05) / (second + 0.05)


def form_control_border():
    text = CSS.read_text(encoding="utf-8")
    match = re.search(
        r"\.form-control,\s*\.form-select\s*\{[^}]*border:\s*1px solid (#[0-9a-fA-F]{6})",
        text)
    assert match, "form-control border color not found"
    return match.group(1)


def test_input_borders_meet_ui_contrast():
    assert ratio(form_control_border(), "#ffffff") >= 3.0


def test_key_text_colors_meet_aa():
    text = CSS.read_text(encoding="utf-8")
    for name, expected in [("--ink", "#232f3e"), ("--pine-ink", "#10294a"),
                           ("--pine", "#0f5dab"), ("--danger", "#a4261c")]:
        assert expected in text.lower(), name
    assert ratio("#232f3e", "#faf6ee") >= 4.5
    assert ratio("#10294a", "#faf6ee") >= 4.5
    assert ratio("#0f5dab", "#ffffff") >= 4.5
    assert ratio("#a4261c", "#fffdf8") >= 4.5


def test_focus_indicator_present_and_visible():
    text = CSS.read_text(encoding="utf-8")
    assert ":focus-visible" in text
    assert ratio("#a8842c", "#faf6ee") >= 3.0
