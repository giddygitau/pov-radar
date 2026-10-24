"""Build a Ride from a Chapter's raw exiftool telemetry text."""

import re
from collections import defaultdict

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
    fix: int
    """GPS fix state: 0 none, 2 two-dimensional, 3 three-dimensional."""
    precision: float
    """Horizontal dilution of precision; under 5 is good."""


class Ride(BaseModel):
    duration: float
    """Seconds of footage on the Video timeline."""
    samples: list[Sample]


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
        fix = int(_single(tags, "GPSMeasureMode", doc))
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
    return Ride(duration=duration, samples=samples)


def _single(tags: dict[str, list[str]], name: str, doc: int) -> str:
    values = tags.get(name)
    if not values:
        raise TelemetryError(f"Telemetry payload {doc} has GPS but no {name}")
    return values[0]
