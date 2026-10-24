"""Build a Ride from a Chapter's raw exiftool telemetry text."""

import math
import re
from bisect import bisect_left
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


IssueKind = Literal["no_fix", "invalid_coordinates", "duplicate_time", "out_of_order_time", "implausible_jump"]


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
    """Runs of invalid Samples, in time order."""

    @property
    def valid_samples(self) -> list[Sample]:
        return [s for s in self.samples if s.valid]


KMH_PER_MS = 3.6
"""Kilometres per hour in one metre per second; exiftool reports GPSSpeed in km/h."""

MAX_SPEED = 250 / KMH_PER_MS
"""Metres per second faster than which, by default, a motorcycle can't have travelled between two Samples."""

_GPS_COLUMNS = ("GPSLatitude", "GPSLongitude", "GPSAltitude", "GPSSpeed")
_PAYLOAD_TAGS = ("SampleTime", "SampleDuration", "GPSMeasureMode", "GPSHPositioningError")

EXIFTOOL_ARGS = ["-a", "-ee", "-G3", "-n", "-s", "-api", "LargeFileSupport=1"] + [
    f"-{tag}" for tag in _PAYLOAD_TAGS + _GPS_COLUMNS
]
"""The exiftool arguments, before the file path, whose output `parse_ride` reads."""

_LINE = re.compile(r"^\[Doc(\d+)(?:-\d+)?\]\s+(\w+)\s*:\s*(.*)$")
"""One tag value from payload N, labelled `[DocN]` or, from exiftool 13.55, `[DocN-row]` per GPS row."""


def parse_ride(text: str, duration: float, max_speed: float = MAX_SPEED) -> Ride:
    """Parse exiftool output from `EXIFTOOL_ARGS`, which is grouped by tag rather than by payload.

    Samples only reachable faster than `max_speed`, in metres per second, are invalid.
    """
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
    return Ride(duration=duration, samples=samples, issues=_validate(samples, max_speed))


def join(chapters: list[Ride], max_speed: float = MAX_SPEED) -> Ride:
    """Join a Recording's Chapter Rides end to end, each offset by the durations of those before it.

    The joined Samples are validated afresh, as in `parse_ride`.
    """
    samples: list[Sample] = []
    offset = 0.0
    for chapter in chapters:
        samples += [s.model_copy(update={"time": s.time + offset}) for s in chapter.samples]
        offset += chapter.duration
    return Ride(duration=offset, samples=samples, issues=_validate(samples, max_speed))


MIN_FIX = 2
"""The lowest GPSMeasureMode with a trustworthy position: a two-dimensional Fix."""


def _validate(samples: list[Sample], max_speed: float) -> list[Issue]:
    """Mark untrustworthy Samples invalid, reporting each run of one rejection kind as an Issue."""
    rejections: dict[int, IssueKind] = {}
    for i, sample in enumerate(samples):
        if kind := _rejection(sample):
            rejections[i] = kind

    def candidates() -> list[tuple[int, Sample]]:
        return [(i, s) for i, s in enumerate(samples) if i not in rejections]

    rejections |= _out_of_order_times(candidates())
    rejections |= _implausible_jumps(candidates(), max_speed)

    def rejection(item: tuple[int, Sample]) -> IssueKind | None:
        return rejections.get(item[0])

    issues: list[Issue] = []
    for kind, group in groupby(enumerate(samples), rejection):
        run = [sample for _, sample in group]
        for sample in run:
            sample.valid = kind is None
        if kind is not None:
            # A run of out-of-order Samples needn't run forwards.
            times = [sample.time for sample in run]
            issues.append(Issue(kind=kind, start=min(times), end=max(times)))
    return sorted(issues, key=lambda issue: issue.start)


def _out_of_order_times(candidates: list[tuple[int, Sample]]) -> dict[int, IssueKind]:
    """Reject Samples out of time order, keeping the most Samples whose times strictly increase.

    A single Sample stamped far in the future or past is rejected alone, not everything after it.
    """
    # The longest strictly increasing subsequence of times, built from the end so ties keep earlier Samples:
    # `ends` holds each length's best first time (negated, so ascending) and `heads` its candidate.
    ends: list[float] = []
    heads: list[int] = []
    following: list[int | None] = []
    for k in reversed(range(len(candidates))):
        length = bisect_left(ends, -candidates[k][1].time)
        following.append(heads[length - 1] if length else None)
        if length == len(ends):
            ends.append(-candidates[k][1].time)
            heads.append(k)
        else:
            ends[length] = -candidates[k][1].time
            heads[length] = k
    following.reverse()
    kept: set[int] = set()
    k = heads[-1] if heads else None
    while k is not None:
        kept.add(k)
        k = following[k]

    kept_times = {candidates[k][1].time for k in kept}
    return {
        index: "duplicate_time" if sample.time in kept_times else "out_of_order_time"
        for k, (index, sample) in enumerate(candidates)
        if k not in kept
    }


def _implausible_jumps(candidates: list[tuple[int, Sample]], max_speed: float) -> dict[int, IssueKind]:
    """Reject Samples only reachable at an implausible speed, trusting the bulk of the Track.

    The candidates split into runs wherever consecutive Samples are too far apart; the largest run is
    trusted, and Samples beyond it are accepted outward only while reachable from the last one accepted.
    """
    if not candidates:
        return {}
    runs: list[list[int]] = [[0]]
    for k in range(1, len(candidates)):
        if _plausible(candidates[k - 1][1], candidates[k][1], max_speed):
            runs[-1].append(k)
        else:
            runs.append([k])
    anchor = max(runs, key=len)

    rejected: dict[int, IssueKind] = {}
    forwards = (anchor[-1], range(anchor[-1] + 1, len(candidates)))
    backwards = (anchor[0], range(anchor[0] - 1, -1, -1))
    for edge, outward in (forwards, backwards):
        last = candidates[edge][1]
        for k in outward:
            index, sample = candidates[k]
            if _plausible(last, sample, max_speed):
                last = sample
            else:
                rejected[index] = "implausible_jump"
    return rejected


EARTH_RADIUS = 6_371_008.8
"""Mean radius in metres."""


def _plausible(a: Sample, b: Sample, max_speed: float) -> bool:
    """Whether the motorcycle could have travelled between two Samples, in either time order."""
    return _distance(a, b) <= max_speed * abs(b.time - a.time)


def _distance(a: Sample, b: Sample) -> float:
    """Great-circle metres between two Samples."""
    lat_a, lat_b = math.radians(a.latitude), math.radians(b.latitude)
    half_chord = (
        math.sin((lat_b - lat_a) / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(math.radians(b.longitude - a.longitude) / 2) ** 2
    )
    return 2 * EARTH_RADIUS * math.asin(math.sqrt(half_chord))


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
