"""How the Overlays look: the defaults the rendering prototype settled (issue #15).

Sizes are fractions of the video frame's height, so one Style suits 1080p, 2.7K and 4K.
"""

from dataclasses import dataclass, field

from pov_radar.roads import RoadClass

Colour = tuple[int, int, int, int]
"""Red, green, blue and alpha, 0–255."""


@dataclass(frozen=True)
class RoadStyle:
    """How one class of road is drawn."""

    colour: Colour
    width: float
    """On the Minimap; the Overview scales it by `Style.overview_line_scale`."""


@dataclass(frozen=True)
class Style:
    """Colours, sizes and proportions shared by both Overlays."""

    surface: Colour = (10, 12, 16, 212)
    """The near-black surface the map is drawn on; opaque enough that busy footage doesn't show through."""
    ring: Colour = (255, 255, 255, 70)
    """The faint outline around each map."""
    ring_width: float = 0.0016
    roads: dict[RoadClass, RoadStyle] = field(
        default_factory=lambda: {
            "minor": RoadStyle((84, 89, 98, 255), 0.0018),
            "mid": RoadStyle((118, 123, 132, 255), 0.0026),
            "major": RoadStyle((150, 155, 164, 255), 0.0034),
        }
    )
    """Drawn in this order, faintest first."""
    upcoming: Colour = (0x00, 0xE5, 0xFF, 255)
    """The accent colour of the Upcoming route."""
    completed: Colour = (128, 128, 128, 255)
    """The muted grey of the Completed route."""
    upcoming_glow_alpha: int = 110
    """Opacity, 0–255, of the soft glow in the accent colour beneath the Upcoming route."""
    upcoming_glow_scale: float = 2.2
    """Width of the glow beneath the Upcoming route, relative to the route's."""
    marker: Colour = (255, 255, 255, 255)
    """The Current position's marker."""
    marker_held: Colour = (255, 255, 255, 110)
    """The marker held at the last valid position during a Fix gap too long to interpolate across: dimmed, so it
    reads as a lost Fix rather than as the motorcycle standing still."""
    marker_halo: Colour = (0, 0, 0, 160)
    """The dark ring around the Overview's dot, and the shadow beneath the Minimap's arrow, that keep the marker
    visible over the bright Upcoming route."""
    route_width: float = 0.0040
    """On the Minimap; the Overview scales it by `Style.overview_line_scale` and `Style.overview_route_scale`."""

    minimap_diameter: float = 0.24
    """The Minimap's diameter."""
    minimap_bias: float = 0.22
    """How far below the Minimap's centre the marker sits, as a fraction of its radius, so more of the map shows
    ahead than behind; the top edge is `MINIMAP_REACH` from the marker."""
    minimap_fade: float = 0.78
    """Fraction of the Minimap's radius beyond which the map fades out towards the edge."""
    minimap_glow_blur: float = 0.004
    """How far the Upcoming route's glow spreads on the Minimap."""
    minimap_arrow: float = 0.022
    """How far the tip of the Minimap's arrow is above the Current position."""
    minimap_arrow_shadow: float = 0.004
    """How far the dark shadow beneath the Minimap's arrow spreads."""

    overview_height: float = 0.28
    """The Overview's height; its width follows the Route's shape."""
    overview_aspect: tuple[float, float] = (0.7, 1.8)
    """Narrowest and widest width-to-height ratio, within which the Overview takes the Route's shape."""
    overview_padding: float = 0.08
    """Margin around the Route, as a fraction of its longer side."""
    overview_corner: float = 0.07
    """Corner radius, as a fraction of the Overview's height."""
    overview_line_scale: float = 0.55
    """Overview road and route widths relative to the Minimap's."""
    overview_route_scale: float = 1.2
    """Extra width for the Route on the Overview, so it stands out from the thinner roads."""
    overview_glow_blur: float = 0.003
    """How far the Upcoming route's glow spreads on the Overview."""
    overview_dot: float = 0.0075
    """Radius of the Overview's dot at the Current position."""
    overview_dot_halo: float = 0.012
    """Radius of the dark halo around the Overview's dot."""
    overview_minor_ink: float = 0.2
    """Most of each small patch of the Overview that minor roads may cover; denser ones are thinned."""
    overview_patch: float = 0.02
    """Side of the patches whose minor-road density is limited."""
