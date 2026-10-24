"""The Overview Renderer: the whole Route north-up over the Road network, in a rounded rectangle."""

from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from pov_radar.projection import LocalProjection
from pov_radar.ride import Ride
from pov_radar.roads import RoadNetwork
from pov_radar.style import Colour, Style

skia: Any = import_module("skia")
"""skia-python ships no type information."""

MIN_EXTENT = 100.0
"""Metres the Overview shows at least, so a Ride that barely moves isn't magnified to nothing."""


class Overview:
    """The Overview's fixed framing for a Ride: fitted to its valid Samples, so rejected ones never move it."""

    def __init__(self, ride: Ride, frame_height: int, style: Style | None = None) -> None:
        self.style = style or Style()
        self.frame_height = frame_height
        valid = ride.valid_samples
        projection = LocalProjection.around(ride)
        self._route = projection.to_metres([s.longitude for s in valid], [s.latitude for s in valid])

        (west, south), (east, north) = self._route.min(axis=0), self._route.max(axis=0)
        padding = self.style.overview_padding * max(east - west, north - south, MIN_EXTENT)
        span_x, span_y = east - west + 2 * padding, north - south + 2 * padding
        narrowest, widest = self.style.overview_aspect
        self.height = round(self.style.overview_height * frame_height)
        self.width = round(self.height * min(max(span_x / span_y, narrowest), widest))
        self._scale = min(self.width / span_x, self.height / span_y)
        """Pixels per metre."""
        self._centre = np.array([(west + east) / 2, (south + north) / 2])
        self.reach = max(self.width / self._scale - (east - west), self.height / self._scale - (north - south)) / 2
        """Metres the Overview shows beyond the box around the Route, where it needs roads."""

    def render(self, roads: RoadNetwork) -> NDArray[np.uint8]:
        """The Overview as (height, width, 4) RGBA with straight alpha, the whole Route Upcoming."""
        s, h = self.style, self.frame_height
        surface = skia.Surface(self.width, self.height)
        canvas = surface.getCanvas()
        canvas.clear(skia.ColorTRANSPARENT)
        corner = s.overview_corner * self.height
        shape = skia.Path().addRRect(skia.RRect.MakeRectXY(skia.Rect(self.width, self.height), corner, corner))
        canvas.save()
        canvas.clipPath(shape, doAntiAlias=True)
        canvas.drawColor(_colour(s.surface))
        for road_class, road_style in s.roads.items():
            width = road_style.width * s.overview_line_scale * h
            lines = [self._pixels(road.points) for road in roads.roads if road.road_class == road_class]
            if road_class == "minor":
                lines = self._thinned(lines, width)
            canvas.drawPath(_path(lines), _stroke(road_style.colour, width))

        route = _path([self._pixels(self._route)])
        width = s.route_width * s.overview_line_scale * s.overview_route_scale * h
        glow = (*s.upcoming[:3], s.upcoming_glow_alpha)
        canvas.drawPath(route, _stroke(glow, width * s.upcoming_glow_scale, blur=s.overview_glow_blur * h))
        canvas.drawPath(route, _stroke(s.upcoming, width))
        canvas.restore()
        canvas.drawPath(shape, _stroke(s.ring, s.ring_width * h))
        return surface.toarray(colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kUnpremul_AlphaType)

    def _thinned(self, lines: list[NDArray[np.float64]], width: float) -> list[NDArray[np.float64]]:
        """The longest of `lines`, in pixels `width` wide, kept until each patch of the Overview is as covered as
        the Style allows; a line is dropped if half its length lies in patches already full."""
        patch = self.style.overview_patch * self.frame_height
        columns = int(np.ceil(self.width / patch))
        budget = self.style.overview_minor_ink * patch**2 / width
        """Pixels of line each patch may hold."""
        spent = np.zeros(columns * int(np.ceil(self.height / patch)))

        def lengths_by_patch(line: NDArray[np.float64]) -> NDArray[np.float64]:
            # Each segment is cut into pieces under half a patch long, counted in the patch its middle lies in.
            steps = np.diff(line, axis=0)
            lengths = np.linalg.norm(steps, axis=1)
            pieces = np.maximum(np.ceil(lengths / (patch / 2)), 1).astype(np.int64)
            segment = np.repeat(np.arange(len(steps)), pieces)
            nth: NDArray[np.int64] = np.arange(len(segment)) - np.repeat(np.cumsum(pieces) - pieces, pieces)
            fraction: NDArray[np.float64] = (nth + 0.5) / pieces[segment]
            middles: NDArray[np.float64] = line[segment] + steps[segment] * fraction[:, None]
            x, y = middles[:, 0], middles[:, 1]
            inside = (x >= 0) & (x < self.width) & (y >= 0) & (y < self.height)
            cells = (y[inside] // patch).astype(np.int64) * columns + (x[inside] // patch).astype(np.int64)
            return np.bincount(cells, (lengths / pieces)[segment][inside], minlength=len(spent))

        measured = [(lengths_by_patch(line), line) for line in lines]
        kept: list[NDArray[np.float64]] = []
        for lengths, line in sorted(measured, key=lambda item: -item[0].sum()):
            if lengths.sum() and lengths[spent + lengths > budget].sum() < lengths.sum() / 2:
                kept.append(line)
                spent += lengths
        return kept

    def _pixels(self, metres: NDArray[np.float64]) -> NDArray[np.float64]:
        """Projected metres as Overview pixels: x right, y down, north up."""
        offset = (metres - self._centre) * self._scale
        return np.column_stack([self.width / 2 + offset[:, 0], self.height / 2 - offset[:, 1]])


def write_png(image: NDArray[np.uint8], path: Path) -> None:
    """Save (height, width, 4) straight-alpha RGBA as a PNG."""
    skia.Image.fromarray(image, colorType=skia.kRGBA_8888_ColorType).save(str(path), skia.kPNG)


def read_png(path: Path) -> NDArray[np.uint8]:
    """A PNG as (height, width, 4) straight-alpha RGBA."""
    return skia.Image.open(str(path)).toarray(colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kUnpremul_AlphaType)


def _path(lines: list[NDArray[np.float64]]) -> Any:
    """One skia path through every line of pixel points."""
    path = skia.Path()
    for line in lines:
        path.addPoly([skia.Point(float(x), float(y)) for x, y in line], False)
    return path


def _stroke(colour: Colour, width: float, blur: float = 0.0) -> Any:
    """A round-capped line paint, `width` pixels wide, softened by a `blur` of that many pixels if given."""
    paint = skia.Paint(
        Color=_colour(colour),
        AntiAlias=True,
        Style=skia.Paint.kStroke_Style,
        StrokeWidth=width,
        StrokeCap=skia.Paint.kRound_Cap,
        StrokeJoin=skia.Paint.kRound_Join,
    )
    if blur:
        paint.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur))
    return paint


def _colour(colour: Colour) -> int:
    """As skia's packed ARGB."""
    red, green, blue, alpha = colour
    return skia.ColorSetARGB(alpha, red, green, blue)
