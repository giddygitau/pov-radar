"""How the Overlays look: the defaults the rendering prototype settled (issue #15).

Sizes are fractions of the video frame's height, so one Style suits 1080p, 2.7K and 4K.
"""

import re
from typing import Annotated, Any, Literal, Self, cast


from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer, model_validator

from pov_radar.roads import RoadClass


def _from_hex(value: object) -> object:
    """`#RRGGBB` or `#RRGGBBAA` as red, green, blue and alpha; anything else is left to validate as it is."""
    if not isinstance(value, str):
        return value
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", value):
        raise ValueError("a colour is #RRGGBB or #RRGGBBAA in hex, or [red, green, blue, alpha] from 0 to 255")
    digits = value[1:] + ("" if len(value) == 9 else "FF")
    return tuple(int(digits[i : i + 2], 16) for i in range(0, 8, 2))


def _to_hex(colour: tuple[int, int, int, int]) -> str:
    return "#" + "".join(f"{channel:02X}" for channel in colour)


Byte = Annotated[int, Field(ge=0, le=255)]
"""One channel of a colour, or an opacity."""

Colour = Annotated[tuple[Byte, Byte, Byte, Byte], BeforeValidator(_from_hex), PlainSerializer(_to_hex)]
"""Red, green, blue and alpha, 0–255; written `#RRGGBB` or `#RRGGBBAA` in a config file."""

Size = Annotated[float, Field(gt=0)]
"""A positive fraction of the frame's height, or a ratio."""

Proportion = Annotated[float, Field(ge=0, le=1)]
"""A fraction from 0 to 1 of something named beside it."""

Look = Literal["route", "map"]
"""How the Overview is drawn: `route`, just the Route and its dot over the footage; `map`, over the Road network on
a dark surface in a rounded rectangle."""


ThemeName = Literal["cyan", "amber", "magenta", "lime", "violet"]
"""A Theme: a named Accent with a Completed route colour to suit it."""

THEMES: dict[str, dict[str, str]] = {
    "cyan": {"accent": "#00E5FF", "completed": "#808080"},
    "amber": {"accent": "#FFB000", "completed": "#8C8274"},
    "magenta": {"accent": "#FF2D95", "completed": "#8C7A84"},
    "lime": {"accent": "#A6FF00", "completed": "#828C78"},
    "violet": {"accent": "#9D6BFF", "completed": "#827E8E"},
}
"""Each Theme's colours, filling those of the Style not set explicitly."""

THEMED = set(THEMES["cyan"])
"""The settings of the Style a Theme fills."""


class RoadStyle(BaseModel, frozen=True, extra="forbid"):
    """How one class of road is drawn."""

    colour: Colour
    width: Size
    """On the Minimap; the Overview scales it by `Style.overview_line_scale`."""


class Style(BaseModel):
    """Colours, sizes and proportions shared by both Overlays."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    theme: ThemeName = "cyan"
    """Sets `accent` and `completed`, unless they are set themselves."""
    surface: Colour = (10, 12, 16, 212)
    """The near-black surface the map is drawn on; opaque enough that busy footage doesn't show through."""
    ring: Colour = (255, 255, 255, 70)
    """The faint outline around each map."""
    ring_width: Size = 0.0016
    roads: dict[RoadClass, RoadStyle] = Field(
        default_factory=lambda: {
            "minor": RoadStyle(colour=(84, 89, 98, 255), width=0.0018),
            "mid": RoadStyle(colour=(118, 123, 132, 255), width=0.0026),
            "major": RoadStyle(colour=(150, 155, 164, 255), width=0.0034),
        }
    )
    """Drawn in this order, faintest first."""
    accent: Colour = (0x00, 0xE5, 0xFF, 255)
    """The Accent: the colour of the Upcoming route, and of its glow."""
    completed: Colour = (128, 128, 128, 255)
    """The muted grey of the Completed route."""
    upcoming_glow_alpha: Byte = 110
    """Opacity, 0–255, of the soft glow in the Accent beneath the Upcoming route."""
    upcoming_glow_scale: Size = 2.2
    """Width of the glow beneath the Upcoming route, relative to the route's."""
    marker: Colour = (255, 255, 255, 255)
    """The Current position's marker."""
    marker_held: Colour = (255, 255, 255, 110)
    """The marker held at the last valid position during a Fix gap too long to interpolate across: dimmed, so it
    reads as a lost Fix rather than as the motorcycle standing still."""
    marker_halo: Colour = (0, 0, 0, 160)
    """The dark ring around the Overview's dot, and the shadow beneath the Minimap's arrow, that keep the marker
    visible over the bright Upcoming route."""
    route_width: Size = 0.0040
    """On the Minimap; the Overview scales it by `Style.overview_line_scale` and `Style.overview_route_scale`."""

    minimap_diameter: Size = 0.24
    """The Minimap's diameter."""
    minimap_bias: Proportion = 0.22
    """How far below the Minimap's centre the marker sits, as a fraction of its radius, so more of the map shows
    ahead than behind; the top edge is the Minimap's reach from the marker."""
    minimap_fade: Proportion = 0.78
    """Fraction of the Minimap's radius beyond which the map fades out towards the edge."""
    minimap_glow_blur: Size = 0.004
    """How far the Upcoming route's glow spreads on the Minimap."""
    minimap_arrow: Size = 0.022
    """How far the tip of the Minimap's arrow is above the Current position."""
    minimap_arrow_shadow: Size = 0.004
    """How far the dark shadow beneath the Minimap's arrow spreads."""

    overview_height: Size = 0.28
    """The Overview's height in its `map` look; its width follows the Route's shape."""
    overview_route_height: Size = 0.20
    """The Overview's height in its `route` look: smaller, as the Route floats on its own without a surface to hold
    it."""
    overview_aspect: tuple[Size, Size] = (0.7, 1.8)
    """Narrowest and widest width-to-height ratio, within which the Overview takes the Route's shape."""
    overview_padding: Proportion = 0.08
    """Margin around the Route, as a fraction of its longer side."""
    overview_corner: Proportion = 0.07
    """Corner radius, as a fraction of the Overview's height."""
    overview_line_scale: Size = 0.55
    """Overview road and route widths relative to the Minimap's."""
    overview_route_scale: Size = 1.2
    """Extra width for the Route on the Overview, so it stands out from the thinner roads."""
    overview_casing_scale: Size = 2.2
    """Width of the dark casing, in `marker_halo`'s colour, beneath the Route in the Overview's `route` look,
    relative to the Route's; it keeps both the Completed and Upcoming routes visible over bright footage."""
    overview_glow_blur: Size = 0.003
    """How far the Upcoming route's glow spreads on the Overview."""
    overview_dot: Size = 0.0075
    """Radius of the Overview's dot at the Current position."""
    overview_dot_halo: Size = 0.012
    """Radius of the dark halo around the Overview's dot."""
    overview_minor_ink: Proportion = 0.2
    """Most of each small patch of the Overview that minor roads may cover; denser ones are thinned."""
    overview_patch: Size = 0.02
    """Side of the patches whose minor-road density is limited."""
    overview_credit: Colour = (255, 255, 255, 150)
    """The OpenStreetMap credit, when burnt into the Overview."""
    overview_credit_size: Size = 0.0095
    """Height of the credit's capitals, and its gap from the Overview's edges; smaller if the Overview is too narrow
    for it."""
    overview_credit_typeface: str = "Helvetica"

    @model_validator(mode="before")
    @classmethod
    def _themed(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        settings = cast(dict[str, Any], data)
        colours = THEMES.get(settings.get("theme", "cyan"), {})
        return {**colours, **settings}

    @model_validator(mode="after")
    def _narrowest_first(self) -> Self:
        if self.overview_aspect[0] > self.overview_aspect[1]:
            raise ValueError("overview_aspect is the narrowest width-to-height ratio, then the widest")
        return self
