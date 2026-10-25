"""Build a Ride from a Chapter's raw exiftool telemetry text."""

import math
import re
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property
from itertools import groupby
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel

from pov_radar.projection import LocalProjection


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


class FixGap(BaseModel):
    """A stretch of the Video timeline with no valid Samples."""

    start: float
    """Video timeline seconds of the last valid Sample before it, or 0 at the start of the Ride."""
    end: float
    """Video timeline seconds of the first valid Sample after it, or the Ride's duration at its end."""


class MultiLineString(BaseModel):
    """A GeoJSON MultiLineString geometry."""

    type: Literal["MultiLineString"] = "MultiLineString"
    coordinates: list[list[tuple[float, float]]]
    """Lines of (longitude, latitude) positions."""


Visibility = Literal["hidden", "live", "held_dimmed", "held_final"]
"""How the Overlays show the Current position (ADR-0004): `hidden` before the first Fix, fully transparent; `live`
at or between valid Samples; `held_dimmed` at the last valid Sample during a Fix gap too long to interpolate across;
`held_final` at the last valid Sample once it has passed."""


@dataclass(frozen=True)
class FrameState:
    """The Ride at one instant on the Video timeline."""

    time: float
    """Seconds on the Video timeline."""
    visibility: Visibility
    """How the Overlays show the Current position; while hidden, nothing should be drawn from it."""
    latitude: float
    longitude: float
    """The Current position."""
    metres: tuple[float, float]
    """The Current position in the Ride's projection: metres east and north of its centre."""
    passed: int
    """How many of the Ride's valid Samples are at or before `time`, and so on the Completed route."""
    route: NDArray[np.float64]
    """The whole Route, (N, 2) projected metres of the valid Samples in time order; shared, never copied."""

    @property
    def completed(self) -> NDArray[np.float64]:
        """The Completed route: the valid Samples passed, then the Split point."""
        return np.vstack([self.route[: self.passed], [self.metres]])

    @property
    def upcoming(self) -> NDArray[np.float64]:
        """The Upcoming route: the Split point, then the valid Samples still to come."""
        return np.vstack([[self.metres], self.route[self.passed :]])


MAX_GAP = 0.5
"""Seconds without a valid Sample longer than which, by default, there is a Fix gap."""

MAX_INTERPOLATED_GAP = 3.0
"""Seconds between valid Samples, by default, across which the Current position is interpolated rather than held."""


class Ride(BaseModel):
    duration: float
    """Seconds of footage on the Video timeline."""
    samples: list[Sample]
    issues: list[Issue] = []
    """Runs of invalid Samples, in time order."""

    @property
    def valid_samples(self) -> list[Sample]:
        return [s for s in self.samples if s.valid]

    def fix_gaps(self, max_gap: float = MAX_GAP) -> list[FixGap]:
        """Stretches longer than `max_gap` seconds without a valid Sample, from video start to video end."""
        times = [0.0, *(s.time for s in self.valid_samples), self.duration]
        return [FixGap(start=a, end=b) for a, b in zip(times, times[1:]) if b - a > max_gap]

    def route_geojson(self, max_gap: float = MAX_GAP) -> MultiLineString:
        """The valid Samples as lines broken at every Fix gap, so none is drawn across unknown ground.

        A Sample alone between Fix gaps makes no line and is left out.
        """
        lines: list[list[Sample]] = []
        for sample in self.valid_samples:
            if lines and sample.time - lines[-1][-1].time <= max_gap:
                lines[-1].append(sample)
            else:
                lines.append([sample])
        return MultiLineString(
            coordinates=[[(s.longitude, s.latitude) for s in line] for line in lines if len(line) > 1]
        )

    @cached_property
    def projection(self) -> LocalProjection:
        """Local metres centred on the valid Samples, shared by everything drawn for the Ride."""
        return LocalProjection.around(self)

    def at(self, t: float, max_interpolated_gap: float = MAX_INTERPOLATED_GAP) -> FrameState:
        """The Ride `t` seconds into the Video timeline, interpolated in metres between the valid Samples either
        side if they're at most `max_interpolated_gap` seconds apart; never extrapolated (ADR-0004).

        Before the first valid Sample it is hidden at it; across a longer Fix gap, and after the last valid Sample,
        it holds at the last one passed.
        """
        times, metres = self._timeline
        if not len(times):
            raise ValueError("A Ride with no valid Samples has no Current position")
        # The Split point follows every Sample at or before `t`, so the Route is split by time, not by place.
        passed = int(np.searchsorted(times, t, side="right"))
        if not passed:
            visibility: Visibility = "hidden"
        elif t == times[passed - 1]:
            visibility = "live"
        elif passed == len(times):
            visibility = "held_final"
        elif times[passed] - times[passed - 1] > max_interpolated_gap:
            visibility = "held_dimmed"
        else:
            visibility = "live"
        if visibility == "live":
            x, y = (float(np.interp(t, times, column)) for column in (metres[:, 0], metres[:, 1]))
        else:
            x, y = (float(value) for value in metres[max(passed - 1, 0)])
        longitude, latitude = self.projection.to_degrees(x, y)
        return FrameState(
            time=t,
            visibility=visibility,
            latitude=latitude,
            longitude=longitude,
            metres=(x, y),
            passed=passed,
            route=metres,
        )

    @cached_property
    def _timeline(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """The valid Samples' times and projected metres, drawn up once for `at`."""
        valid = self.valid_samples
        times = np.array([s.time for s in valid], dtype=np.float64)
        if not valid:
            return times, np.empty((0, 2))
        return times, self.projection.to_metres([s.longitude for s in valid], [s.latitude for s in valid])


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
