"""What both Overlay Renderers draw with: skia paints and paths, the Route, the marker dot, and PNGs."""

from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from pov_radar.ride import Visibility
from pov_radar.style import Colour, Style

skia: Any = import_module("skia")
"""skia-python ships no type information."""


def draw_route(
    canvas: Any,
    style: Style,
    completed: list[NDArray[np.float64]],
    upcoming: list[NDArray[np.float64]],
    width: float,
    glow_blur: float,
) -> None:
    """The Completed route muted, beneath the Upcoming route in the accent colour with its glow; `width` and
    `glow_blur` in the canvas's units."""
    glow = (*style.upcoming[:3], style.upcoming_glow_alpha)
    ahead = path(upcoming)
    canvas.drawPath(path(completed), stroke(style.completed, width))
    canvas.drawPath(ahead, stroke(glow, width * style.upcoming_glow_scale, blur=glow_blur))
    canvas.drawPath(ahead, stroke(style.upcoming, width))


def draw_dot(canvas: Any, style: Style, x: float, y: float, radius: float, halo: float, visibility: Visibility) -> None:
    """The marker dot at (x, y) in its dark halo, dimmed while held in a Fix gap."""
    canvas.drawCircle(x, y, halo, fill(style.marker_halo))
    canvas.drawCircle(x, y, radius, fill(style.marker_held if visibility == "held_dimmed" else style.marker))


def write_png(image: NDArray[np.uint8], path: Path) -> None:
    """Save (height, width, 4) straight-alpha RGBA as a PNG."""
    skia.Image.fromarray(image, colorType=skia.kRGBA_8888_ColorType).save(str(path), skia.kPNG)


def read_png(path: Path) -> NDArray[np.uint8]:
    """A PNG as (height, width, 4) straight-alpha RGBA."""
    return skia.Image.open(str(path)).toarray(colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kUnpremul_AlphaType)


def pixels(surface: Any) -> NDArray[np.uint8]:
    """A surface's contents as (height, width, 4) straight-alpha RGBA."""
    return surface.toarray(colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kUnpremul_AlphaType)


def path(lines: list[NDArray[np.float64]]) -> Any:
    """One skia path through every line of points."""
    built = skia.Path()
    for line in lines:
        built.addPoly([skia.Point(float(x), float(y)) for x, y in line], False)
    return built


def stroke(colour: Colour, width: float, blur: float = 0.0) -> Any:
    """A round-capped line paint, `width` wide, softened by a `blur` of that much if given."""
    paint = skia.Paint(
        Color=skia_colour(colour),
        AntiAlias=True,
        Style=skia.Paint.kStroke_Style,
        StrokeWidth=width,
        StrokeCap=skia.Paint.kRound_Cap,
        StrokeJoin=skia.Paint.kRound_Join,
    )
    if blur:
        paint.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur))
    return paint


def fill(colour: Colour) -> Any:
    """An antialiased fill paint."""
    return skia.Paint(Color=skia_colour(colour), AntiAlias=True)


def skia_colour(colour: Colour) -> int:
    """As skia's packed ARGB."""
    red, green, blue, alpha = colour
    return skia.ColorSetARGB(alpha, red, green, blue)
