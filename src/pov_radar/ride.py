"""Build a Ride from a Chapter's raw exiftool telemetry text."""

import re
from collections import defaultdict
from itertools import groupby
from typing import Literal

from pydantic import BaseModel


class TelemetryError(Exception):
    """The telemetry text cannot be turned into Samples."""


class Sample(BaseModel):
    time: float
    """Seconds on the Video timeline."""
    latitude: float
    longitude: float
    altitude: float
    """Metres above mean sea level."""
    speed: float
    """Metres per second (2D ground speed)."""
    fix: int | None
    """GPS fix state: 0 none, 2 two-dimensional, 3 three-dimensional; None if the payload doesn't say."""
    precision: float
    """Horizontal dilution of precision; under 5 is good."""
    valid: bool = True
    """Whether validation trusts this Sample's position."""


IssueKind = Literal["no_fix", "invalid_coordinates"]


class Issue(BaseModel):
    """A run of consecutive Samples rejected for the same reason."""

    kind: IssueKind
    """Why the Samples were rejected."""
    start: float
    """Video timeline seconds of the first rejected Sample."""
    end: float
    """Video timeline seconds of the last rejected Sample."""


class Ride(BaseModel):
    duration: float
    """Seconds of footage on the Video timeline."""
    samples: list[Sample]
    issues: list[Issue] = []
    """Runs of invalid Samples, in Track order."""


KMH_PER_MS = 3.6
"""Kilometres per hour in one metre per second; exiftool reports GPSSpeed in km/h."""

_GPS_COLUMNS = ("GPSLatitude", "GPSLongitude", "GPSAltitude", "GPSSpeed")
_PAYLOAD_TAGS = ("SampleTime", "SampleDuration", "GPSMeasureMode", "GPSHPositioningError")

EXIFTOOL_ARGS = ["-a", "-ee", "-G3", "-n", "-s", "-api", "LargeFileSupport=1"] + [
    f"-{tag}" for tag in _PAYLOAD_TAGS + _GPS_COLUMNS
]
"""The exiftool arguments, before the file path, whose output `parse_ride` reads."""

_LINE = re.compile(r"^\[Doc(\d+)(?:-\d+)?\]\s+(\w+)\s*:\s*(.*)$")
"""One tag value from payload N, labelled `[DocN]` or, from exiftool 13.55, `[DocN-row]` per GPS row."""


def parse_ride(text: str, duration: float) -> Ride:
    """Parse exiftool output from `EXIFTOOL_ARGS`, which is grouped by tag rather than by payload."""
    payloads: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for line in text.splitlines():
        if match := _LINE.match(line):
            doc, tag, value = match.groups()
            payloads[int(doc)][tag].append(value.strip())
        elif line.startswith("[Doc"):
            raise TelemetryError(f"Unrecognised exiftool line: {line.strip()}")

    samples: list[Sample] = []
    for doc in sorted(payloads):
        tags = payloads[doc]
        columns = [tags.get(name, []) for name in _GPS_COLUMNS]
        lengths = {len(column) for column in columns}
        if lengths == {0}:
            continue
        if len(lengths) > 1:
            raise TelemetryError(f"Telemetry payload {doc} has unequal GPS columns {sorted(lengths)}")
        start = float(_single(tags, "SampleTime", doc))
        step = float(_single(tags, "SampleDuration", doc)) / len(columns[0])
        fix = int(tags["GPSMeasureMode"][0]) if tags.get("GPSMeasureMode") else None
        precision = float(_single(tags, "GPSHPositioningError", doc))
        samples += [
            Sample(
                time=start + i * step,
                latitude=float(latitude),
                longitude=float(longitude),
                altitude=float(altitude),
                speed=float(speed) / KMH_PER_MS,
                fix=fix,
                precision=precision,
            )
            for i, (latitude, longitude, altitude, speed) in enumerate(zip(*columns))
        ]
    return Ride(duration=duration, samples=samples, issues=_validate(samples))


def join(chapters: list[Ride]) -> Ride:
    """Join a Recording's Chapter Rides end to end, each offset by the durations of those before it."""
    samples: list[Sample] = []
    offset = 0.0
    for chapter in chapters:
        samples += [s.model_copy(update={"time": s.time + offset}) for s in chapter.samples]
        offset += chapter.duration
    return Ride(duration=offset, samples=samples, issues=_validate(samples))


MIN_FIX = 2
"""The lowest GPSMeasureMode with a trustworthy position: a two-dimensional Fix."""


def _validate(samples: list[Sample]) -> list[Issue]:
    """Mark untrustworthy Samples invalid, reporting each run of one rejection kind as an Issue."""
    issues: list[Issue] = []
    for kind, group in groupby(samples, _rejection):
        run = list(group)
        for sample in run:
            sample.valid = kind is None
        if kind is not None:
            issues.append(Issue(kind=kind, start=run[0].time, end=run[-1].time))
    return issues


def _rejection(sample: Sample) -> IssueKind | None:
    if sample.fix is not None and sample.fix < MIN_FIX:
        return "no_fix"
    # NaN fails both range checks.
    in_range = -90 <= sample.latitude <= 90 and -180 <= sample.longitude <= 180
    if not in_range or sample.latitude == sample.longitude == 0:
        return "invalid_coordinates"
    return None


def _single(tags: dict[str, list[str]], name: str, doc: int) -> str:
    values = tags.get(name)
    if not values:
        raise TelemetryError(f"Telemetry payload {doc} has GPS but no {name}")
    return values[0]
