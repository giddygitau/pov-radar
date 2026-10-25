"""The settings of a run: built-in defaults, overridden by a YAML config file, overridden by command-line options."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pov_radar.placement import Corner
from pov_radar.ride import KMH_PER_MS, MAX_GAP, MAX_INTERPOLATED_GAP, MAX_SPEED, HeadingSmoothing
from pov_radar.roads import MINIMAP_REACH
from pov_radar.style import Style
from pov_radar.video import Encoding

DEFAULT_PATH = Path("config.yaml")
"""The config file read, from the working directory, when none is named."""


class ConfigError(Exception):
    """The config can't be read or has a bad value; the message names where and which."""


class _Section(BaseModel):
    """A part of the config, read-only, refusing settings it doesn't have so a misspelt one isn't ignored."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class TelemetryConfig(_Section):
    """How the GPS telemetry is validated and lined up with the footage."""

    max_speed: float = Field(default=MAX_SPEED * KMH_PER_MS, gt=0)
    """Km/h faster than which the motorcycle can't have moved between Samples."""
    max_gap: float = Field(default=MAX_GAP, ge=0)
    """Seconds without a valid Sample longer than which there is a Fix gap."""
    max_interpolated_gap: float = Field(default=MAX_INTERPOLATED_GAP, ge=0)
    """Seconds of Fix gap across which the Current position is interpolated rather than held."""
    time_offset: float = 0.0
    """Seconds added to every Sample's time, to line the GPS up with the footage when it runs early."""


class HeadingConfig(_Section):
    """How the Heading is derived from movement; set separately from how the Current position is."""

    window: float = Field(default=HeadingSmoothing.window, gt=0)
    """Seconds of travel, centred on each frame, whose overall direction is the Heading."""
    min_speed: float = Field(default=HeadingSmoothing.min_speed * KMH_PER_MS, ge=0)
    """Km/h below which the Heading holds at the last moment the motorcycle was faster."""

    def smoothing(self) -> HeadingSmoothing:
        """As the Ride takes it, in metres per second."""
        return HeadingSmoothing(window=self.window, min_speed=self.min_speed / KMH_PER_MS)


class Placed(_Section):
    """Where an Overlay sits on the frame."""

    corner: Corner
    margin: float = Field(default=0.03, ge=0)
    """Gap between the Overlay and the frame's edges beside its corner, as a fraction of the frame's height."""


class MinimapConfig(Placed):
    """Where the Minimap sits on the frame, and how far it sees."""

    corner: Corner = "bottom-right"
    reach: float = Field(default=MINIMAP_REACH, gt=0)
    """Metres from the Current position to the Minimap's top edge: its zoom."""


class OverviewConfig(Placed):
    """Where the Overview sits on the frame."""

    corner: Corner = "top-left"


class CompositeConfig(_Section):
    """How the Composite is encoded."""

    encoder: str = "libx265"
    """The ffmpeg video encoder."""
    preset: str = "slow"
    """The encoder's trade of speed against size; `--fast` overrides it for previews."""
    crf: float = Field(default=16, ge=0, le=51)
    """The encoder's constant quality: lower is better, and bigger."""
    pixel_format: str = "yuv420p10le"
    """10-bit, so the Overlays' gradients don't band."""
    threads: int | None = Field(default=None, ge=1)
    """How many threads decode, draw and encode the Composite, to leave the machine some room; None for every
    core."""

    def encoding(self) -> Encoding:
        """As the encoder takes it."""
        return Encoding(**self.model_dump())


class Config(_Section):
    """Every setting of a run."""

    telemetry: TelemetryConfig = TelemetryConfig()
    heading: HeadingConfig = HeadingConfig()
    minimap: MinimapConfig = MinimapConfig()
    overview: OverviewConfig = OverviewConfig()
    style: Style = Style()
    composite: CompositeConfig = CompositeConfig()
    attribution: Literal["file", "burn_in"] = "file"
    """`file` writes the OpenStreetMap credit to ATTRIBUTION.txt beside the Overlays; `burn_in` also draws it on the
    Overview."""
    full_frame: bool = False
    """Whether each Overlay is drawn in place on a transparent frame the size of the footage's, rather than cropped
    to its map."""

    def dumped(self) -> dict[str, Any]:
        """As a config file would give it: plain values, colours in hex."""
        return self.model_dump(mode="json")


def load_config(path: Path | None, options: Mapping[str, Any] | None = None) -> Config:
    """The built-in defaults, overridden by the YAML file at `path` (or `config.yaml` in the working directory, if
    there is one), overridden by `options`, nested as in the file; options set to None are left out."""
    settings = Config().dumped()
    path = config_file(path)
    if path is not None:
        settings = _validated(_merged(settings, _read(path)), str(path)).dumped()
    return _validated(_merged(settings, given(options or {})), "options")


def config_file(path: Path | None) -> Path | None:
    """The config file to read: `path` if named, otherwise `config.yaml` in the working directory if there is one."""
    return path if path is not None or not DEFAULT_PATH.exists() else DEFAULT_PATH


def _read(path: Path) -> dict[str, Any]:
    try:
        loaded: object = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"{path}: no such file") from None
    except yaml.YAMLError as error:
        raise ConfigError(f"{path}: not valid YAML: {error}") from None
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path}: should be a mapping of settings, not a {type(loaded).__name__}")
    return {str(key): value for key, value in cast(dict[object, Any], loaded).items()}


def _validated(settings: dict[str, Any], source: str) -> Config:
    try:
        return Config.model_validate(settings)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}" for problem in error.errors()
        )
        raise ConfigError(f"{source}: {problems}") from None


def _merged(base: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """`base` with `overrides` laid over it, section by section."""
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _merged(merged[key], value)  # pyright: ignore[reportUnknownArgumentType]
        else:
            merged[key] = value
    return merged


def given(options: Mapping[str, Any]) -> dict[str, Any]:
    """`options` without those left unset."""
    kept: dict[str, Any] = {}
    for key, value in options.items():
        if isinstance(value, Mapping):
            if inner := given(value):  # pyright: ignore[reportUnknownArgumentType]
                kept[key] = inner
        elif value is not None:
            kept[key] = value
    return kept
