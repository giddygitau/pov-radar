"""The `pov-radar` command line."""

from collections.abc import Callable
from functools import cache
from importlib.metadata import version
from pathlib import Path
from statistics import fmean
from typing import Annotated, Any, Literal

import typer
import yaml
from pydantic import BaseModel

from pov_radar import maps
from pov_radar.chapter import ChapterError, read_ride
from pov_radar.config import DEFAULT_PATH, Config, ConfigError, Placed, config_file, given, load_config
from pov_radar.recording import DiscoveryError, Recording, discover
from pov_radar.ride import KMH_PER_MS, Ride, join
from pov_radar.drawing import write_png
from pov_radar.minimap import Minimap
from pov_radar.overview import Overview
from pov_radar.placement import Placement, PlacementError, in_frame, place
from pov_radar.maps import ATTRIBUTION
from pov_radar.roads import MapData, RoadClass, RoadNetwork
from pov_radar.video import OverlayWriter, VideoError, read_frames

app = typer.Typer(no_args_is_help=True, add_completion=False)


PREVIEW_FRAME_HEIGHT = 2160
"""The preview is drawn as the Overview would be on 4K footage."""


class MapRecord(BaseModel):
    """Which map data a Ride's roads came from."""

    extract: str
    date: str | None
    """When the extract's data was current, as an ISO timestamp, if known."""
    roads: dict[RoadClass, int]
    """Roads around the Ride, by class."""


class OverlayRecord(BaseModel):
    """An Overlay as rendered."""

    file: str
    """Its name, in the folder beside this record."""
    full_frame: bool
    """Whether the file is the footage's size with the map in place, or cropped to the map."""
    placement: Placement
    """Where the map goes on the footage."""


class FrameRecord(BaseModel):
    """The footage's frames, which the Overlays are placed on."""

    width: int
    height: int
    """Pixels."""
    rate: str
    """Frames per second, exactly, e.g. `30000/1001`."""


class RenderRecord(BaseModel):
    """What `render.json` holds: how to place each Overlay on the footage, and how the run made them."""

    version: str
    """pov-radar's version."""
    frame: FrameRecord
    """The footage's frames."""
    overlays: dict[str, OverlayRecord]
    """Each Overlay rendered, by name."""
    time_offset: float
    """Seconds the telemetry was moved later on the Video timeline."""
    map: MapRecord | None
    """None when there was no map data."""
    config: dict[str, Any]
    """The effective config, as a config file would give it."""


class Telemetry(Ride):
    """What `telemetry.json` holds: the Ride, and the map data its roads came from."""

    time_offset: float
    """Seconds every Sample and Issue was moved later on the Video timeline from where the camera put it."""
    map: MapRecord | None
    """None when there was no map data."""


maps_app = typer.Typer(no_args_is_help=True, help="The OpenStreetMap extract the Road network comes from.")
app.add_typer(maps_app, name="maps")


@app.callback()
def main() -> None:
    """Game-style navigation Overlays from GoPro GPS telemetry."""


Sources = Annotated[
    list[Path],
    typer.Argument(
        help="A GoPro Chapter (its Recording's other Chapters are found), a folder (one Ride per Recording), "
        "or several Chapters (grouped by Recording; other names are joined in the order given)"
    ),
]
Out = Annotated[
    Path | None,
    typer.Option(
        help="Output folder; with several Rides, each goes in a subfolder "
        "[default: pov-radar/<recording>/ beside the source]"
    ),
]
ConfigFile = Annotated[
    Path | None,
    typer.Option(
        "--config",
        help=f"YAML config file, overriding the built-in defaults; options override it [default: {DEFAULT_PATH} "
        "in the working directory, if there is one]",
    ),
]
_FROM_CONFIG = "[default: from the config]"
"""Each option overrides the config, whose limits it shares, so it shows no default or limits of its own."""
MaxSpeed = Annotated[
    float | None,
    typer.Option(help=f"Km/h faster than which the motorcycle can't have moved between Samples {_FROM_CONFIG}"),
]
MaxGap = Annotated[
    float | None,
    typer.Option(help=f"Seconds without a valid Sample longer than which there is a Fix gap {_FROM_CONFIG}"),
]
TimeOffset = Annotated[
    float | None,
    typer.Option(
        help=f"Seconds to move the telemetry later on the Video timeline, when the map runs ahead of the footage; "
        f"negative when it lags {_FROM_CONFIG}"
    ),
]
MaxInterpolatedGap = Annotated[
    float | None,
    typer.Option(
        help="Seconds of Fix gap across which the Current position is interpolated; "
        f"across longer ones it holds, dimmed, at the last valid Sample {_FROM_CONFIG}",
    ),
]
HeadingWindow = Annotated[
    float | None,
    typer.Option(
        help=f"Seconds of travel, centred on each frame, whose overall direction is the Heading {_FROM_CONFIG}",
    ),
]
HeadingMinSpeed = Annotated[
    float | None,
    typer.Option(
        help=f"Km/h below which the Heading holds at the last moment the motorcycle was faster {_FROM_CONFIG}"
    ),
]
FullFrame = Annotated[
    bool | None,
    typer.Option(
        "--full-frame/--cropped",
        help=f"Draw each Overlay in place on a transparent frame the footage's size, or crop it to its map "
        f"{_FROM_CONFIG}",
        show_default=False,
    ),
]
Attribution = Annotated[
    Literal["file", "burn_in"] | None,
    typer.Option(
        help=f"Credit OpenStreetMap in ATTRIBUTION.txt only, or burn it into the Overview too {_FROM_CONFIG}",
    ),
]


@app.command()
def inspect(
    sources: Sources,
    out: Out = None,
    config: ConfigFile = None,
    max_speed: MaxSpeed = None,
    max_gap: MaxGap = None,
    time_offset: TimeOffset = None,
) -> None:
    """Summarise each Ride's GPS telemetry and write its telemetry.json, route.geojson and an Overview preview.png."""
    telemetry = {"max_speed": max_speed, "max_gap": max_gap, "time_offset": time_offset}
    settings = _config(config, {"telemetry": telemetry})
    _each_ride(sources, out, settings, lambda recording, folder, inspected: True)


@app.command()
def render(
    sources: Sources,
    out: Out = None,
    config: ConfigFile = None,
    max_speed: MaxSpeed = None,
    max_gap: MaxGap = None,
    time_offset: TimeOffset = None,
    max_interpolated_gap: MaxInterpolatedGap = None,
    heading_window: HeadingWindow = None,
    heading_min_speed: HeadingMinSpeed = None,
    full_frame: FullFrame = None,
    attribution: Attribution = None,
    overview: Annotated[bool, typer.Option(help="Render overview.mov")] = True,
    minimap: Annotated[bool, typer.Option(help="Render minimap.mov")] = True,
) -> None:
    """Inspect each Ride, as `inspect` does, then render its Overlays, overview.mov and minimap.mov, as transparent
    videos, with render.json saying where they go on the footage and ATTRIBUTION.txt crediting the map data."""
    chosen = [name for name, wanted in (("overview", overview), ("minimap", minimap)) if wanted]
    if not chosen:
        typer.echo("Error: --no-overview and --no-minimap leave nothing to render", err=True)
        raise typer.Exit(1)
    options = {
        "telemetry": {
            "max_speed": max_speed,
            "max_gap": max_gap,
            "time_offset": time_offset,
            "max_interpolated_gap": max_interpolated_gap,
        },
        "heading": {"window": heading_window, "min_speed": heading_min_speed},
        "full_frame": full_frame,
        "attribution": attribution,
    }
    settings = _config(config, options, log=True)
    _each_ride(
        sources,
        out,
        settings,
        lambda recording, folder, inspected: _render(recording, folder, inspected, chosen, settings),
    )


def _config(path: Path | None, options: dict[str, Any], log: bool = False) -> Config:
    """The effective config, from the defaults, the config file and the options given, echoed first if `log`;
    exiting 1 if it has a bad value."""
    path = config_file(path)
    try:
        config = load_config(path, options)
    except ConfigError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from error
    if log:
        sources = ["built-in defaults", *([str(path)] if path else []), *(["options"] if given(options) else [])]
        typer.echo(f"Config: {', '.join(sources)}")
        dumped = yaml.safe_dump(config.dumped(), sort_keys=False, allow_unicode=True)
        typer.echo("".join(f"  {line}\n" for line in dumped.splitlines()))
    return config




Renderer = Callable[[Ride, int, Config], tuple[Overview | Minimap, Placed]]
"""An Overlay's Renderer, made for a Ride, the height of its footage's frames and the config, with the config's
placement for it."""

RENDERERS: dict[str, Renderer] = {
    "overview": lambda ride, height, config: (
        Overview(ride, height, config.style, credit=config.attribution == "burn_in"),
        config.overview,
    ),
    "minimap": lambda ride, height, config: (Minimap(ride, height, config.style, config.minimap.reach), config.minimap),
}


class Inspected(BaseModel, arbitrary_types_allowed=True):
    """A Ride that passed inspection, the roads around it, and the map data they came from."""

    ride: Ride
    roads: RoadNetwork
    map: MapRecord | None


def _each_ride(
    sources: list[Path],
    out: Path | None,
    config: Config,
    then: Callable[[Recording, Path, Inspected], bool],
) -> None:
    """Inspect the Ride of each Recording found in `sources`, then do `then` with it, exiting 1 if any failed."""
    try:
        recordings = discover(sources)
    except DiscoveryError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from error
    if not recordings:
        typer.echo(f"Error: no Chapters found in {sources[0]}", err=True)
        raise typer.Exit(1)
    map_data = cache(_map_data)
    failed = False
    for i, recording in enumerate(recordings):
        if i:
            typer.echo()
        if out is None:
            folder = recording.chapters[0].parent / "pov-radar" / recording.name
        else:
            folder = out / recording.name if len(recordings) > 1 else out
        inspected = _inspect(recording, folder, map_data, config)
        failed |= inspected is None or not then(recording, folder, inspected)
    if failed:
        raise typer.Exit(1)


def _inspect(
    recording: Recording, out: Path, map_data: Callable[[], MapData | None], config: Config
) -> Inspected | None:
    """Report one Ride, its telemetry moved by the config's time offset, and write its telemetry and Route; None if
    it couldn't be."""
    max_speed, max_gap = config.telemetry.max_speed / KMH_PER_MS, config.telemetry.max_gap
    typer.echo(f"{recording.name}: {', '.join(path.name for path in recording.chapters)}")
    if recording.missing:
        plural = "s" if len(recording.missing) > 1 else ""
        typer.echo(f"Warning: {recording.name} is missing chapter{plural} {_ranges(recording.missing)}", err=True)
    try:
        ride = join([read_ride(path) for path in recording.chapters], max_speed).shifted(config.telemetry.time_offset)
    except ChapterError as error:
        typer.echo(f"Error: {error}", err=True)
        return None

    typer.echo(_summary(ride, max_gap))
    if not ride.valid_samples:
        typer.echo(f"Error: {recording.name} has no valid Samples; there is nothing to map", err=True)
        return None

    overview = Overview(ride, PREVIEW_FRAME_HEIGHT, config.style)
    record = None
    roads = RoadNetwork([])
    if loaded := map_data():
        record = MapRecord(extract=loaded.extract.name, date=loaded.date, roads=loaded.roads_around(ride).counts())
        roads = loaded.roads_around(ride, margin=max(config.minimap.reach, overview.reach))
        counts = ", ".join(f"{count} {road_class}" for road_class, count in record.roads.items())
        typer.echo(f"Roads:    {counts} (OpenStreetMap, {_date(loaded)})")
    else:
        typer.echo("Roads:    no map data; run `pov-radar maps update`")

    out.mkdir(parents=True, exist_ok=True)
    telemetry = out / "telemetry.json"
    written = Telemetry(**ride.model_dump(), time_offset=config.telemetry.time_offset, map=record)
    telemetry.write_text(written.model_dump_json(indent=2))
    typer.echo(f"Wrote {telemetry}")
    route = out / "route.geojson"
    route.write_text(ride.route_geojson(max_gap).model_dump_json())
    typer.echo(f"Wrote {route}")
    preview = out / "preview.png"
    write_png(overview.render(roads), preview)
    typer.echo(f"Wrote {preview}")
    return Inspected(ride=ride, roads=roads, map=record)


def _render(recording: Recording, out: Path, inspected: Inspected, overlays: list[str], config: Config) -> bool:
    """Render each named Overlay, as `<name>.mov`, frame for frame with the Ride's footage, then render.json
    recording where each goes and ATTRIBUTION.txt; False if any couldn't be."""
    ride, telemetry = inspected.ride, config.telemetry
    heading = config.heading.smoothing()
    records: dict[str, OverlayRecord] = {}
    try:
        frames = read_frames(recording.chapters)
        for name in overlays:
            path = out / f"{name}.mov"
            overlay, placed = RENDERERS[name](ride, frames.height, config)
            placement = place(placed.corner, placed.margin, overlay.width, overlay.height, frames.size)
            draw = overlay.frames(inspected.roads)
            size = frames.size if config.full_frame else (overlay.width, overlay.height)
            with (
                OverlayWriter(path, *size, frames.rate) as writer,
                typer.progressbar(range(frames.count), label=f"Rendering {path.name}", show_percent=True) as bar,
            ):
                for frame in bar:
                    image = draw(ride.at(float(frame / frames.rate), telemetry.max_interpolated_gap, heading))
                    writer.write(in_frame(image, placement, frames.size) if config.full_frame else image)
            typer.echo(f"Wrote {path}")
            records[name] = OverlayRecord(file=path.name, full_frame=config.full_frame, placement=placement)
    except (VideoError, PlacementError) as error:
        typer.echo(f"Error: {error}", err=True)
        return False

    record = RenderRecord(
        version=version("pov-radar"),
        frame=FrameRecord(width=frames.width, height=frames.height, rate=str(frames.rate)),
        overlays=records,
        time_offset=telemetry.time_offset,
        map=inspected.map,
        config=config.dumped(),
    )
    sidecar = out / "render.json"
    sidecar.write_text(record.model_dump_json(indent=2))
    typer.echo(f"Wrote {sidecar}")
    attribution = out / "ATTRIBUTION.txt"
    attribution.write_text(ATTRIBUTION)
    typer.echo(f"Wrote {attribution}; credit OpenStreetMap wherever the Overlays are shown")
    return True


def _ranges(chapters: list[int]) -> str:
    """Chapter numbers as runs, e.g. `01–03, 05`."""
    runs: list[list[int]] = []
    for chapter in chapters:
        if runs and chapter == runs[-1][-1] + 1:
            runs[-1].append(chapter)
        else:
            runs.append([chapter])
    return ", ".join(f"{run[0]:02}" if len(run) == 1 else f"{run[0]:02}–{run[-1]:02}" for run in runs)


def _summary(ride: Ride, max_gap: float) -> str:
    valid = ride.valid_samples
    lines = [
        f"Duration: {ride.duration:.1f} s",
        f"Samples:  {len(ride.samples)} at {len(ride.samples) / ride.duration:.1f} Hz",
        f"Valid:    {len(valid)} of {len(ride.samples)}",
    ]
    if valid:
        speeds = [s.speed * KMH_PER_MS for s in valid]
        lines.append(f"Speed:    {min(speeds):.0f}–{max(speeds):.0f} km/h, mean {fmean(speeds):.0f} km/h")
    lines += _listed("Issues:", [f"{i.kind} {i.start:.1f}–{i.end:.1f} s" for i in ride.issues])
    lines += _listed("Fix gaps:", [f"{g.start:.1f}–{g.end:.1f} s" for g in ride.fix_gaps(max_gap)])
    return "\n".join(lines)


def _listed(label: str, entries: list[str]) -> list[str]:
    """Summary lines for `entries`, the first beside `label` and the rest aligned under it; `none` if empty."""
    first, *rest = entries or ["none"]
    return [f"{label:<10}{first}", *(f"{'':<10}{entry}" for entry in rest)]


@maps_app.command("update")
def maps_update() -> None:
    """Download the Kenya OpenStreetMap extract and index its roads; it is never refreshed otherwise."""
    try:
        _update_maps(maps.EXTRACT_URL)
    except maps.DownloadError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from error


def _update_maps(url: str) -> MapData:
    """Fetch the extract at `url`, with progress, and index its roads."""
    with maps.Download(url) as download, typer.progressbar(length=download.total or 0, label=f"Downloading {url}") as bar:
        for size in download.chunks():
            bar.update(size)
    map_data = _index(download.path)
    typer.echo(f"Map data dated {_date(map_data)}, in {download.path.parent}")
    return map_data


def _map_data() -> MapData | None:
    """The default extract's map data, offering to download it if it's missing; None if the rider declines."""
    extract = maps.extract_path(maps.EXTRACT_URL)
    if not extract.exists():
        question = f"No OpenStreetMap extract in {extract.parent}. Download it now? ({maps.EXTRACT_URL})"
        try:
            if not typer.confirm(question, default=False):
                return None
        except typer.Abort:
            typer.echo()  # No one to answer, as when run from a script: carry on without roads.
            return None
        try:
            return _update_maps(maps.EXTRACT_URL)
        except maps.DownloadError as error:
            typer.echo(f"Error: {error}", err=True)
            return None
    return MapData.open(extract) or _index(extract)


def _index(extract: Path) -> MapData:
    """Index the extract's roads, which takes a while for a whole country."""
    typer.echo(f"Indexing the roads in {extract.name}; a whole country takes a minute or two")
    return MapData.build(extract)


def _date(map_data: MapData) -> str:
    """The extract's date, as YYYY-MM-DD."""
    return map_data.date[:10] if map_data.date else "unknown"
