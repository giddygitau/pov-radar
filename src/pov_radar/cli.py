"""The `pov-radar` command line."""

from collections.abc import Callable
from functools import cache
from pathlib import Path
from statistics import fmean
from typing import Annotated

import typer
from pydantic import BaseModel

from pov_radar import maps
from pov_radar.chapter import ChapterError, read_ride
from pov_radar.recording import DiscoveryError, Recording, discover
from pov_radar.ride import KMH_PER_MS, MAX_GAP, MAX_SPEED, Ride, join
from pov_radar.overview import Overview, write_png
from pov_radar.roads import MINIMAP_REACH, MapData, RoadClass, RoadNetwork

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


class Telemetry(Ride):
    """What `telemetry.json` holds: the Ride, and the map data its roads came from."""

    map: MapRecord | None
    """None when there was no map data."""


maps_app = typer.Typer(no_args_is_help=True, help="The OpenStreetMap extract the Road network comes from.")
app.add_typer(maps_app, name="maps")


@app.callback()
def main() -> None:
    """Game-style navigation Overlays from GoPro GPS telemetry."""


@app.command()
def inspect(
    sources: Annotated[
        list[Path],
        typer.Argument(
            help="A GoPro Chapter (its Recording's other Chapters are found), a folder (one Ride per Recording), "
            "or several Chapters (grouped by Recording; other names are joined in the order given)"
        ),
    ],
    out: Annotated[
        Path | None,
        typer.Option(
            help="Output folder; with several Rides, each goes in a subfolder "
            "[default: pov-radar/<recording>/ beside the source]"
        ),
    ] = None,
    max_speed: Annotated[
        float,
        typer.Option(help="Km/h faster than which the motorcycle can't have moved between Samples", min=0),
    ] = MAX_SPEED * KMH_PER_MS,
    max_gap: Annotated[
        float,
        typer.Option(help="Seconds without a valid Sample longer than which there is a Fix gap", min=0),
    ] = MAX_GAP,
) -> None:
    """Summarise each Ride's GPS telemetry and write its telemetry.json, route.geojson and an Overview preview.png."""
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
        failed |= not _inspect(recording, folder, map_data, max_speed / KMH_PER_MS, max_gap)
    if failed:
        raise typer.Exit(1)


def _inspect(
    recording: Recording, out: Path, map_data: Callable[[], MapData | None], max_speed: float, max_gap: float
) -> bool:
    """Report one Ride and write its telemetry and Route, `max_speed` in metres per second and `max_gap` in
    seconds; False if it couldn't be."""
    typer.echo(f"{recording.name}: {', '.join(path.name for path in recording.chapters)}")
    if recording.missing:
        plural = "s" if len(recording.missing) > 1 else ""
        typer.echo(f"Warning: {recording.name} is missing chapter{plural} {_ranges(recording.missing)}", err=True)
    try:
        ride = join([read_ride(path) for path in recording.chapters], max_speed)
    except ChapterError as error:
        typer.echo(f"Error: {error}", err=True)
        return False

    typer.echo(_summary(ride, max_gap))
    if not ride.valid_samples:
        typer.echo(f"Error: {recording.name} has no valid Samples; there is nothing to map", err=True)
        return False

    overview = Overview(ride, PREVIEW_FRAME_HEIGHT)
    record = None
    roads = RoadNetwork([])
    if loaded := map_data():
        record = MapRecord(extract=loaded.extract.name, date=loaded.date, roads=loaded.roads_around(ride).counts())
        roads = loaded.roads_around(ride, margin=max(MINIMAP_REACH, overview.reach))
        counts = ", ".join(f"{count} {road_class}" for road_class, count in record.roads.items())
        typer.echo(f"Roads:    {counts} (OpenStreetMap, {_date(loaded)})")
    else:
        typer.echo("Roads:    no map data; run `pov-radar maps update`")

    out.mkdir(parents=True, exist_ok=True)
    telemetry = out / "telemetry.json"
    telemetry.write_text(Telemetry(**ride.model_dump(), map=record).model_dump_json(indent=2))
    typer.echo(f"Wrote {telemetry}")
    route = out / "route.geojson"
    route.write_text(ride.route_geojson(max_gap).model_dump_json())
    typer.echo(f"Wrote {route}")
    preview = out / "preview.png"
    write_png(overview.render(roads), preview)
    typer.echo(f"Wrote {preview}")
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
