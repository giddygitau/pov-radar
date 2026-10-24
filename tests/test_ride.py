import math
from dataclasses import dataclass, field
from typing import Literal

import pytest

from pov_radar.ride import TelemetryError, parse_ride

GPS_COLUMNS = ["GPSLatitude", "GPSLongitude", "GPSAltitude", "GPSSpeed"]


@dataclass
class Payload:
    sample_time: float | None = 0.0
    sample_duration: float | None = 1.0
    fix: int | None = 3
    precision: float | None = 2.01
    # One (lat, lon, altitude m, speed km/h) row per Sample.
    gps: list[tuple[float, float, float, float]] = field(default_factory=list[tuple[float, float, float, float]])
    # Overrides a GPS column's values, to build unequal columns.
    columns: dict[str, list[float]] = field(default_factory=dict[str, list[float]])


RowLabels = Literal["shared", "numbered"]


def exiftool_text(*payloads: Payload, row_labels: RowLabels = "numbered") -> str:
    """Mimics `exiftool -a -ee -G3 -n -s <tags>`: grouped by tag, then by payload.

    exiftool 13.30 labels every GPS row of payload 1 `[Doc1]` ("shared"); 13.55 labels them
    `[Doc1]`, `[Doc1-1]`, `[Doc1-2]`, … ("numbered").
    """
    lines: list[str] = []

    def emit(name: str, values: list[tuple[int, object]]) -> None:
        row = 0
        for i, (doc, value) in enumerate(values):
            row = row + 1 if i and values[i - 1][0] == doc else 0
            label = f"Doc{doc}-{row}" if row and row_labels == "numbered" else f"Doc{doc}"
            lines.append(f"[{label}]{'':<10}{name:<32}: {value}")

    def scalar(name: str, attr: str) -> None:
        emit(name, [(d, getattr(p, attr)) for d, p in enumerate(payloads, 1) if getattr(p, attr) is not None])

    scalar("SampleTime", "sample_time")
    scalar("SampleDuration", "sample_duration")
    scalar("GPSMeasureMode", "fix")
    scalar("GPSHPositioningError", "precision")
    for column, name in enumerate(GPS_COLUMNS):
        emit(
            name,
            [
                (d, value)
                for d, p in enumerate(payloads, 1)
                for value in p.columns.get(name, [row[column] for row in p.gps])
            ],
        )
    return "\n".join(lines) + "\n"


ROW = (-0.7, 36.9, 1746.0, 36.0)


@pytest.mark.parametrize("row_labels", ["shared", "numbered"])
def test_samples_are_spread_evenly_across_their_payload(row_labels: RowLabels) -> None:
    text = exiftool_text(
        Payload(sample_time=0.0, sample_duration=1.0, gps=[ROW] * 4),
        Payload(sample_time=1.0, sample_duration=0.5, gps=[ROW] * 2),
        row_labels=row_labels,
    )

    ride = parse_ride(text, duration=1.5)

    assert [s.time for s in ride.samples] == [0.0, 0.25, 0.5, 0.75, 1.0, 1.25]
    assert ride.duration == 1.5


def test_samples_carry_position_altitude_speed_in_metres_per_second_fix_and_precision() -> None:
    text = exiftool_text(
        Payload(fix=2, precision=1.5, gps=[(-0.7092127, 36.973307, 1746.79, 72.0)]),
    )

    (sample,) = parse_ride(text, duration=1.0).samples

    assert sample.latitude == -0.7092127
    assert sample.longitude == 36.973307
    assert sample.altitude == 1746.79
    assert sample.speed == pytest.approx(20.0)
    assert sample.fix == 2
    assert sample.precision == 1.5


def test_payloads_without_gps_are_skipped() -> None:
    text = exiftool_text(
        Payload(sample_time=0.0, fix=None, precision=None, gps=[]),
        Payload(sample_time=1.0, gps=[ROW] * 2),
    )

    ride = parse_ride(text, duration=2.0)

    assert [s.time for s in ride.samples] == [1.0, 1.5]


@pytest.mark.parametrize("missing", ["sample_time", "sample_duration"])
def test_a_payload_with_gps_but_no_timing_is_an_error(missing: str) -> None:
    broken = Payload(sample_time=1.0, gps=[ROW] * 2)
    setattr(broken, missing, None)
    text = exiftool_text(Payload(gps=[ROW] * 2), broken)

    with pytest.raises(TelemetryError, match=r"payload 2 has GPS but no (SampleTime|SampleDuration)"):
        parse_ride(text, duration=2.0)


@pytest.mark.parametrize("short_column", GPS_COLUMNS)
def test_a_payload_with_unequal_gps_columns_is_an_error(short_column: str) -> None:
    broken = Payload(gps=[ROW] * 3, columns={short_column: [0.0]})

    with pytest.raises(TelemetryError, match="payload 1 has unequal GPS columns"):
        parse_ride(exiftool_text(broken), duration=1.0)


def test_an_unrecognised_payload_label_is_an_error_not_skipped() -> None:
    text = exiftool_text(Payload(gps=[ROW] * 2)) + "[Doc1.2]          GPSLatitude                     : -0.7\n"

    with pytest.raises(TelemetryError, match=r"Unrecognised exiftool line: \[Doc1\.2\]"):
        parse_ride(text, duration=1.0)


def test_a_clean_track_has_every_sample_valid_and_no_issues() -> None:
    text = exiftool_text(Payload(gps=[ROW] * 4), Payload(sample_time=1.0, gps=[ROW] * 4))

    ride = parse_ride(text, duration=2.0)

    assert all(s.valid for s in ride.samples)
    assert ride.issues == []


@pytest.mark.parametrize("fix", [0, 1])
def test_a_run_of_samples_below_a_2d_fix_is_one_issue_spanning_their_times(fix: int) -> None:
    text = exiftool_text(
        Payload(sample_time=0.0, fix=fix, gps=[ROW] * 4),
        Payload(sample_time=1.0, fix=fix, gps=[ROW] * 2),
        Payload(sample_time=2.0, fix=2, gps=[ROW] * 2),
    )

    ride = parse_ride(text, duration=3.0)

    assert [s.valid for s in ride.samples] == [False] * 6 + [True] * 2
    assert [(i.kind, i.start, i.end) for i in ride.issues] == [("no_fix", 0.0, 1.5)]


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(0.0, 0.0), (90.5, 36.9), (-91.0, 36.9), (-0.7, 180.5), (-0.7, -181.0), (math.nan, 36.9), (-0.7, math.nan)],
    ids=["null-island", "lat-north", "lat-south", "lon-east", "lon-west", "lat-nan", "lon-nan"],
)
def test_a_sample_with_impossible_coordinates_is_rejected(latitude: float, longitude: float) -> None:
    text = exiftool_text(Payload(gps=[ROW, (latitude, longitude, 1746.0, 36.0), ROW, ROW]))

    ride = parse_ride(text, duration=1.0)

    assert [s.valid for s in ride.samples] == [True, False, True, True]
    assert [(i.kind, i.start, i.end) for i in ride.issues] == [("invalid_coordinates", 0.25, 0.25)]


def test_coordinates_on_the_equator_or_prime_meridian_alone_are_valid() -> None:
    text = exiftool_text(Payload(gps=[(0.0, 36.9, 1746.0, 36.0), (-0.7, 0.0, 1746.0, 36.0)]))

    ride = parse_ride(text, duration=1.0)

    assert all(s.valid for s in ride.samples)


def test_samples_with_no_fix_information_count_as_valid() -> None:
    text = exiftool_text(Payload(fix=None, gps=[ROW] * 2))

    ride = parse_ride(text, duration=1.0)

    assert [(s.fix, s.valid) for s in ride.samples] == [(None, True), (None, True)]
    assert ride.issues == []
