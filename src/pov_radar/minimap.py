"""The Minimap Renderer: the Road network and Route around the Current position, Heading-up, in a circle."""

from collections.abc import Callable
from typing import Any

import numpy as np
from numpy.typing import NDArray

from pov_radar.drawing import draw_route, fill, path, pixels, skia, skia_colour, stroke
from pov_radar.ride import FrameState, Ride
from pov_radar.roads import MINIMAP_REACH, RoadClass, RoadNetwork
from pov_radar.style import Style


class Minimap:
    """The Minimap's size and scale for a Ride: the marker below centre, `MINIMAP_REACH` metres from the top edge."""

    def __init__(self, ride: Ride, frame_height: int, style: Style | None = None) -> None:
        del ride  # Every Ride's Minimap is the same size; its frame states give the Heading.
        self.style = style or Style()
        self.frame_height = frame_height
        self.width = self.height = round(self.style.minimap_diameter * frame_height)
        self._radius = self.width / 2
        self._marker = (self._radius, self._radius * (1 + self.style.minimap_bias))
        """Where the Current position is drawn, in pixels."""
        self._scale = (self._radius + self._radius * self.style.minimap_bias) / MINIMAP_REACH
        """Pixels per metre."""

    def frames(self, roads: RoadNetwork) -> Callable[[FrameState], NDArray[np.uint8]]:
        """A function drawing the Minimap at a frame state, turned so its Heading is up: the roads and the Route,
        Completed up to the Split point and Upcoming after it, under an arrow there pointing up, dimmed while held in
        a Fix gap; nothing at all while hidden. Each road's path is built once, here."""
        s, h, r = self.style, self.frame_height, self._radius
        surface = skia.Surface(self.width, self.height)
        circle = skia.Path().addCircle(r, r, r)
        fade = skia.Paint(
            Shader=skia.GradientShader.MakeRadial(
                skia.Point(r, r), r, [skia.ColorBLACK, skia.ColorBLACK, skia.ColorTRANSPARENT], [0.0, s.minimap_fade, 1.0]
            ),
            BlendMode=skia.BlendMode.kDstIn,
        )
        classes = [
            (_Roads(roads, road_class), stroke(road_style.colour, road_style.width * h / self._scale))
            for road_class, road_style in s.roads.items()
        ]

        def draw(state: FrameState) -> NDArray[np.uint8]:
            canvas = surface.getCanvas()
            canvas.clear(skia.ColorTRANSPARENT)
            if state.visibility == "hidden":
                return pixels(surface)
            here = np.array(state.metres)
            canvas.save()
            canvas.clipPath(circle, doAntiAlias=True)
            canvas.drawColor(skia_colour(s.surface))
            canvas.saveLayer()
            canvas.save()
            canvas.translate(*self._marker)
            canvas.scale(self._scale, -self._scale)  # North up,
            canvas.rotate(state.heading)  # then turned anticlockwise by the Heading, so it is up.
            canvas.translate(-here[0], -here[1])
            for near, paint in classes:
                for road in near.around(here, MINIMAP_REACH):
                    canvas.drawPath(road, paint)
            draw_route(
                canvas,
                s,
                _near(state.completed, here, MINIMAP_REACH),
                _near(state.upcoming, here, MINIMAP_REACH),
                s.route_width * h / self._scale,
                s.minimap_glow_blur * h / self._scale,
            )
            canvas.restore()
            canvas.drawPaint(fade)
            canvas.restore()
            canvas.restore()
            canvas.drawCircle(r, r, r - s.ring_width * h / 2, stroke(s.ring, s.ring_width * h))
            canvas.drawPath(arrow, shadow)
            canvas.drawPath(arrow, held if state.visibility == "held_dimmed" else marker)
            return pixels(surface)

        arrow = _arrow(*self._marker, s.minimap_arrow * h)
        shadow = fill(s.marker_halo)
        shadow.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, s.minimap_arrow_shadow * h))
        marker, held = fill(s.marker), fill(s.marker_held)
        return draw


def _arrow(x: float, y: float, size: float) -> Any:
    """The marker: an arrowhead pointing up, centred on (x, y), its tip `size` above it."""
    arrow = skia.Path()
    arrow.moveTo(x, y - size)
    arrow.lineTo(x + 0.72 * size, y + 0.75 * size)
    arrow.lineTo(x, y + 0.38 * size)
    arrow.lineTo(x - 0.72 * size, y + 0.75 * size)
    arrow.close()
    return arrow


class _Roads:
    """One class of road, each as a skia path in metres with its bounding box, to draw only those in view."""

    def __init__(self, roads: RoadNetwork, road_class: RoadClass) -> None:
        points = [road.points for road in roads.roads if road.road_class == road_class]
        self._paths: list[Any] = [path([line]) for line in points]
        self._boxes = np.array([[*line.min(axis=0), *line.max(axis=0)] for line in points]).reshape(-1, 4)
        """West, south, east and north of each road, in metres."""

    def around(self, centre: NDArray[np.float64], reach: float) -> list[Any]:
        """The paths of the roads whose bounding boxes come within `reach` metres of `centre` east or north."""
        (west, south), (east, north) = centre - reach, centre + reach
        b = self._boxes
        near = (b[:, 0] <= east) & (b[:, 2] >= west) & (b[:, 1] <= north) & (b[:, 3] >= south)
        return [self._paths[i] for i in np.flatnonzero(near)]


def _near(line: NDArray[np.float64], centre: NDArray[np.float64], reach: float) -> list[NDArray[np.float64]]:
    """The runs of a line's segments that come within `reach` of `centre`, so a long Route isn't drawn whole."""
    if len(line) < 2:
        return []
    start, step = line[:-1], np.diff(line, axis=0)
    along = np.clip(((centre - start) * step).sum(axis=1) / np.maximum((step**2).sum(axis=1), 1e-12), 0, 1)
    near = np.flatnonzero(np.linalg.norm(start + step * along[:, None] - centre, axis=1) <= reach)
    runs = np.split(near, np.flatnonzero(np.diff(near) != 1) + 1)
    return [line[run[0] : run[-1] + 2] for run in runs if len(run)]
