"""Read a Chapter file through the external tools exiftool and ffprobe."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pov_radar.ride import EXIFTOOL_ARGS, Ride, TelemetryError, parse_ride


class ChapterError(Exception):
    """The Chapter cannot be read; the message says what to do about it."""


_TOOLS = {"exiftool": "brew install exiftool", "ffprobe": "brew install ffmpeg"}


def read_ride(path: Path) -> Ride:
    """Build the Ride for a single Chapter; joining a Recording's Chapters comes later."""
    tools = {name: _require(name, install) for name, install in _TOOLS.items()}
    duration = _probe_video(tools["ffprobe"], path)
    try:
        ride = parse_ride(_read_telemetry(tools["exiftool"], path), duration)
    except TelemetryError as error:
        raise ChapterError(f"{path.name}: {error}") from error
    if not ride.samples:
        raise ChapterError(f"{path.name} has a telemetry track but no GPS Samples")
    return ride


def _read_telemetry(exiftool: str, path: Path) -> str:
    result = subprocess.run(
        [exiftool, *EXIFTOOL_ARGS, str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ChapterError(f"exiftool could not read {path.name}: {result.stderr.strip()}")
    return result.stdout


def _probe_video(ffprobe: str, path: Path) -> float:
    """Check the Chapter is a video with a telemetry track, and return its duration in seconds."""
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_tag_string,duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    streams: list[dict[str, Any]] = json.loads(result.stdout or "{}").get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if result.returncode != 0 or video is None:
        reason = result.stderr.strip().splitlines()[-1:] or ["no video stream"]
        raise ChapterError(f"{path} is not a readable video ({reason[0]})")
    if not any(s.get("codec_tag_string") == "gpmd" for s in streams):
        raise ChapterError(
            f"{path.name} has no GoPro telemetry track (gpmd): GPS may have been off, "
            "or this is an exported copy rather than the camera's original file"
        )
    return float(video["duration"])


def _require(name: str, install: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise ChapterError(f"{name} not found on PATH; install it with `{install}`")
    return found
