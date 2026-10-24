"""The `pov-radar` command line."""

from pathlib import Path
from statistics import fmean
from typing import Annotated

import typer

from pov_radar.chapter import ChapterError, read_ride
from pov_radar.recording import DiscoveryError, Recording, discover
from pov_radar.ride import KMH_PER_MS, Ride, join

app = typer.Typer(no_args_is_help=True, add_completion=False)


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
) -> None:
    """Summarise each Ride's GPS telemetry and write its telemetry.json."""
    try:
        recordings = discover(sources)
    except DiscoveryError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from error
    if not recordings:
        typer.echo(f"Error: no Chapters found in {sources[0]}", err=True)
        raise typer.Exit(1)
    failed = False
    for i, recording in enumerate(recordings):
        if i:
            typer.echo()
        if out is None:
            folder = recording.chapters[0].parent / "pov-radar" / recording.name
        else:
            folder = out / recording.name if len(recordings) > 1 else out
        failed |= not _inspect(recording, folder)
    if failed:
        raise typer.Exit(1)


def _inspect(recording: Recording, out: Path) -> bool:
    """Report one Ride and write its telemetry; False if it couldn't be."""
    typer.echo(f"{recording.name}: {', '.join(path.name for path in recording.chapters)}")
    if recording.missing:
        plural = "s" if len(recording.missing) > 1 else ""
        typer.echo(f"Warning: {recording.name} is missing chapter{plural} {_ranges(recording.missing)}", err=True)
    try:
        ride = join([read_ride(path) for path in recording.chapters])
    except ChapterError as error:
        typer.echo(f"Error: {error}", err=True)
        return False

    typer.echo(_summary(ride))
    if not any(s.valid for s in ride.samples):
        typer.echo(f"Error: {recording.name} has no valid Samples; there is nothing to map", err=True)
        return False

    out.mkdir(parents=True, exist_ok=True)
    telemetry = out / "telemetry.json"
    telemetry.write_text(ride.model_dump_json(indent=2))
    typer.echo(f"Wrote {telemetry}")
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


def _summary(ride: Ride) -> str:
    valid = [s for s in ride.samples if s.valid]
    lines = [
        f"Duration: {ride.duration:.1f} s",
        f"Samples:  {len(ride.samples)} at {len(ride.samples) / ride.duration:.1f} Hz",
        f"Valid:    {len(valid)} of {len(ride.samples)}",
    ]
    if valid:
        speeds = [s.speed * KMH_PER_MS for s in valid]
        lines.append(f"Speed:    {min(speeds):.0f}–{max(speeds):.0f} km/h, mean {fmean(speeds):.0f} km/h")
    issues = [f"{i.kind} {i.start:.1f}–{i.end:.1f} s" for i in ride.issues] or ["none"]
    lines += [f"Issues:   {issues[0]}", *(f"          {issue}" for issue in issues[1:])]
    return "\n".join(lines)
