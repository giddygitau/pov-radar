import math
from dataclasses import dataclass, field
from typing import Literal

import pytest

from pov_radar.ride import KMH_PER_MS, TelemetryError, join, parse_ride

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


@pytest.mark.parametrize(("latitude", "longitude"), [(0.0, 36.9), (-0.7, 0.0)], ids=["equator", "prime-meridian"])
def test_coordinates_on_the_equator_or_prime_meridian_alone_are_valid(latitude: float, longitude: float) -> None:
    text = exiftool_text(Payload(gps=[(latitude, longitude, 1746.0, 36.0)]))

    ride = parse_ride(text, duration=1.0)

    assert all(s.valid for s in ride.samples)


def test_samples_with_no_fix_information_count_as_valid() -> None:
    text = exiftool_text(Payload(fix=None, gps=[ROW] * 2))

    ride = parse_ride(text, duration=1.0)

    assert [(s.fix, s.valid) for s in ride.samples] == [(None, True), (None, True)]
    assert ride.issues == []


def test_chapters_join_onto_one_timeline_each_offset_by_the_durations_before_it() -> None:
    first = parse_ride(exiftool_text(Payload(sample_time=0.0, gps=[ROW] * 2)), duration=1.5)
    second = parse_ride(exiftool_text(Payload(sample_time=0.0, gps=[ROW] * 2)), duration=2.0)
    third = parse_ride(exiftool_text(Payload(sample_time=0.5, gps=[ROW] * 2)), duration=1.0)

    ride = join([first, second, third])

    assert [s.time for s in ride.samples] == [0.0, 0.5, 1.5, 2.0, 4.0, 4.5]
    assert ride.duration == 4.5


def test_a_run_of_invalid_samples_across_a_chapter_boundary_is_one_issue() -> None:
    first = parse_ride(exiftool_text(Payload(gps=[ROW, ROW]), Payload(sample_time=1.0, fix=0, gps=[ROW] * 2)), 2.0)
    second = parse_ride(exiftool_text(Payload(fix=0, gps=[ROW] * 2), Payload(sample_time=1.0, gps=[ROW] * 2)), 2.0)

    ride = join([first, second])

    assert [(i.kind, i.start, i.end) for i in ride.issues] == [("no_fix", 1.0, 2.5)]


METRES_PER_DEGREE = 111_320.0
"""Roughly, of latitude."""


def riding_north(seconds: int, kmh: float = 60.0, rate: int = 10, start: float = 0.0) -> list[Payload]:
    """One payload per second of a Track due north from ROW at a steady speed, `rate` Samples a second."""
    step = kmh / KMH_PER_MS / rate / METRES_PER_DEGREE
    return [
        Payload(
            sample_time=start + second,
            gps=[(ROW[0] + (second * rate + i) * step, ROW[1], ROW[2], kmh) for i in range(rate)],
        )
        for second in range(seconds)
    ]


def with_row(payloads: list[Payload], payload: int, row: int, **changes: float) -> list[Payload]:
    """`payloads` with one GPS row moved, e.g. `latitude=1.0`."""
    gps = list(payloads[payload].gps)
    latitude, longitude, altitude, speed = gps[row]
    gps[row] = (
        changes.get("latitude", latitude),
        changes.get("longitude", longitude),
        changes.get("altitude", altitude),
        changes.get("speed", speed),
    )
    return payloads[:payload] + [Payload(sample_time=payloads[payload].sample_time, gps=gps)] + payloads[payload + 1 :]


def test_a_single_far_off_spike_is_rejected_and_its_neighbours_kept() -> None:
    payloads = with_row(riding_north(5), payload=2, row=3, latitude=ROW[0] + 0.1)

    ride = parse_ride(exiftool_text(*payloads), duration=5.0)

    assert [i for i, s in enumerate(ride.samples) if not s.valid] == [23]
    assert [(i.kind, i.start, i.end) for i in ride.issues] == [("implausible_jump", pytest.approx(2.3), pytest.approx(2.3))]


def test_bad_samples_at_the_very_start_are_rejected_and_the_rest_kept() -> None:
    payloads = riding_north(5)
    for row in range(4):
        payloads = with_row(payloads, payload=0, row=row, latitude=ROW[0] + 0.5, longitude=ROW[1] + 0.5)

    ride = parse_ride(exiftool_text(*payloads), duration=5.0)

    assert [i for i, s in enumerate(ride.samples) if not s.valid] == [0, 1, 2, 3]
    assert [(i.kind, i.start, i.end) for i in ride.issues] == [("implausible_jump", 0.0, pytest.approx(0.3))]


def test_a_sample_repeating_the_previous_ones_timestamp_is_rejected() -> None:
    # One Sample a second, the third payload sent twice.
    payloads = riding_north(5, rate=1)
    payloads.insert(3, payloads[2])

    ride = parse_ride(exiftool_text(*payloads), duration=5.0)

    assert [s.valid for s in ride.samples] == [True, True, True, False, True, True]
    assert [(i.kind, i.start, i.end) for i in ride.issues] == [("duplicate_time", 2.0, 2.0)]


@pytest.mark.parametrize("glitch", [1, 7], ids=["early", "late"])
@pytest.mark.parametrize("time", [9000.0, -100.0], ids=["future", "past"])
def test_a_single_sample_stamped_out_of_order_is_rejected_alone(glitch: int, time: float) -> None:
    payloads = riding_north(10, rate=1)
    payloads[glitch] = Payload(sample_time=time, gps=payloads[glitch].gps)

    ride = parse_ride(exiftool_text(*payloads), duration=10.0)

    assert [i for i, s in enumerate(ride.samples) if not s.valid] == [glitch]
    assert [(i.kind, i.start, i.end) for i in ride.issues] == [("out_of_order_time", time, time)]


def test_issues_are_reported_in_time_order_not_track_order() -> None:
    payloads = with_row(riding_north(10, rate=1), payload=6, row=0, latitude=ROW[0] + 0.1)
    payloads[2] = Payload(sample_time=9000.0, gps=payloads[2].gps)

    ride = parse_ride(exiftool_text(*payloads), duration=10.0)

    assert [(i.kind, i.start) for i in ride.issues] == [("implausible_jump", 6.0), ("out_of_order_time", 9000.0)]


def test_the_speed_limit_is_configurable() -> None:
    text = exiftool_text(*riding_north(3, kmh=200.0))

    assert parse_ride(text, duration=3.0).issues == []
    assert parse_ride(text, duration=3.0, max_speed=150 / KMH_PER_MS).issues != []
    assert join([parse_ride(text, duration=3.0)], max_speed=150 / KMH_PER_MS).issues != []


def test_a_two_hour_two_hundred_kilometre_ride_has_no_issues() -> None:
    text = exiftool_text(*riding_north(2 * 3600, kmh=100.0))

    ride = parse_ride(text, duration=2 * 3600.0)

    assert ride.issues == []
    assert len(ride.valid_samples) == 72_000


def test_a_payload_sent_twice_is_one_duplicate_time_issue() -> None:
    payloads = riding_north(5)
    payloads.insert(3, payloads[2])

    ride = parse_ride(exiftool_text(*payloads), duration=5.0)

    assert [i for i, s in enumerate(ride.samples) if not s.valid] == list(range(30, 40))
    assert [(i.kind, i.start, i.end) for i in ride.issues] == [("duplicate_time", 2.0, pytest.approx(2.9))]


def test_fix_gaps_are_reported_from_video_start_between_fixes_and_to_video_end() -> None:
    # No Fix for the first 2 s and from 5 s to 7 s; the last Sample is at 9.9 s of 12.
    payloads = riding_north(10)
    for second in (0, 1, 5, 6):
        payloads[second] = Payload(sample_time=float(second), fix=0, gps=payloads[second].gps)

    ride = parse_ride(exiftool_text(*payloads), duration=12.0)

    assert [(g.start, g.end) for g in ride.fix_gaps()] == [
        (0.0, 2.0),
        (pytest.approx(4.9), 7.0),
        (pytest.approx(9.9), 12.0),
    ]


def test_pauses_between_samples_under_the_threshold_are_not_fix_gaps() -> None:
    # A Sample every 0.1 s, the last at 4.9 s of 5.
    ride = parse_ride(exiftool_text(*riding_north(5)), duration=5.0)

    assert ride.fix_gaps() == []


def test_the_fix_gap_threshold_is_configurable() -> None:
    # A Sample every second, the last at 4 s of 5.
    ride = parse_ride(exiftool_text(*riding_north(5, rate=1)), duration=5.0)

    assert len(ride.fix_gaps()) == 5
    assert ride.fix_gaps(max_gap=1.0) == [], "a pause exactly at the threshold isn't longer than it"


def test_a_ride_with_no_valid_samples_is_one_fix_gap() -> None:
    text = exiftool_text(Payload(fix=0, gps=[ROW] * 4))

    ride = parse_ride(text, duration=3.0)

    assert [(g.start, g.end) for g in ride.fix_gaps()] == [(0.0, 3.0)]


def test_the_route_geojson_leaves_out_invalid_samples_and_breaks_at_each_fix_gap() -> None:
    # A Sample a second, ~11 m apart, without a Fix at 2 s, 5 s and 7 s: 6 s and 8 s are alone after gaps.
    latitudes = [-0.7, -0.6999, -0.6998, -0.6997, -0.6996, -0.6995, -0.6994, -0.6993, -0.6992]
    payloads = [
        Payload(sample_time=float(t), fix=0 if t in (2, 5, 7) else 3, gps=[(latitude, 36.9, 1746.0, 40.0)])
        for t, latitude in enumerate(latitudes)
    ]
    ride = parse_ride(exiftool_text(*payloads), duration=9.0)

    route = ride.route_geojson(max_gap=1.5)

    assert route.type == "MultiLineString"
    assert route.coordinates == [
        [(36.9, -0.7), (36.9, -0.6999)],
        [(36.9, -0.6997), (36.9, -0.6996)],
    ]
