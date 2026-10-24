"""The `pov-radar` command line."""

import re
from pathlib import Path
from statistics import fmean
from typing import Annotated

import typer

from pov_radar.chapter import ChapterError, read_ride
from pov_radar.ride import KMH_PER_MS, Ride

app = typer.Typer(no_args_is_help=True, add_completion=False)

_CHAPTER_NAME = re.compile(r"^GX\d{2}(\d{4})$", re.IGNORECASE)


@app.callback()
def main() -> None:
    """Game-style navigation Overlays from GoPro GPS telemetry."""


@app.command()
def inspect(
    source: Annotated[Path, typer.Argument(help="A GoPro Chapter file, e.g. GX040710.MP4")],
    out: Annotated[
        Path | None, typer.Option(help="Output folder [default: pov-radar/GX<recording>/ beside the source]")
    ] = None,
) -> None:
    """Summarise a Chapter's GPS telemetry and write telemetry.json."""
    try:
        ride = read_ride(source)
    except ChapterError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from error

    typer.echo(_summary(ride))
    if not any(s.usable for s in ride.samples):
        typer.echo(f"Error: {source.name} has no usable Samples; there is nothing to map", err=True)
        raise typer.Exit(1)

    out = out or _default_out(source)
    out.mkdir(parents=True, exist_ok=True)
    telemetry = out / "telemetry.json"
    telemetry.write_text(ride.model_dump_json(indent=2))
    typer.echo(f"Wrote {telemetry}")


def _default_out(source: Path) -> Path:
    match = _CHAPTER_NAME.match(source.stem)
    return source.parent / "pov-radar" / (f"GX{match[1]}" if match else source.stem)


def _summary(ride: Ride) -> str:
    usable = [s for s in ride.samples if s.usable]
    lines = [
        f"Duration: {ride.duration:.1f} s",
        f"Samples:  {len(ride.samples)} at {len(ride.samples) / ride.duration:.1f} Hz",
        f"Usable:   {len(usable)} of {len(ride.samples)}",
    ]
    if usable:
        speeds = [s.speed * KMH_PER_MS for s in usable]
        lines.append(f"Speed:    {min(speeds):.0f}–{max(speeds):.0f} km/h, mean {fmean(speeds):.0f} km/h")
    issues = [f"{i.kind} {i.start:.1f}–{i.end:.1f} s" for i in ride.issues] or ["none"]
    lines += [f"Issues:   {issues[0]}", *(f"          {issue}" for issue in issues[1:])]
    return "\n".join(lines)
