import math
from dataclasses import dataclass, field
from typing import Literal

import pytest

from pov_radar.ride import KMH_PER_MS, FrameState, HeadingSmoothing, Ride, TelemetryError, join, parse_ride

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


def riding_east(fixes: list[int]) -> Ride:
    """A Sample a second, 0.0001° of longitude (~11.1 m at 0.7° S) further east each time, with these Fixes."""
    payloads = [
        Payload(sample_time=float(t), fix=fix, gps=[(ROW[0], ROW[1] + t * 0.0001, ROW[2], 40.0)])
        for t, fix in enumerate(fixes)
    ]
    return parse_ride(exiftool_text(*payloads), duration=float(len(fixes)))


EAST_STEP = 0.0001 * METRES_PER_DEGREE * math.cos(math.radians(0.7))
"""Metres between consecutive Samples of `riding_east`."""


def test_the_current_position_is_interpolated_between_samples() -> None:
    ride = riding_east([3, 3, 3])

    start, quarter, later = ride.at(0.0), ride.at(0.25), ride.at(1.5)

    assert quarter.longitude == pytest.approx(ROW[1] + 0.000025)
    assert quarter.latitude == pytest.approx(ROW[0])
    assert math.dist(start.metres, quarter.metres) == pytest.approx(EAST_STEP / 4, rel=0.01)
    assert later.metres[0] - start.metres[0] == pytest.approx(EAST_STEP * 1.5, rel=0.01)
    assert later.metres[1] == pytest.approx(start.metres[1], abs=0.01), "due east"


def test_the_current_position_skips_invalid_samples() -> None:
    ride = riding_east([3, 0, 3])

    assert ride.at(1.0).longitude == pytest.approx(ROW[1] + 0.0001), "halfway between the valid Samples either side"
    assert ride.at(1.0).metres[0] - ride.at(0.0).metres[0] == pytest.approx(EAST_STEP, rel=0.01)


def test_the_current_position_does_not_depend_on_query_order() -> None:
    ride = riding_east([3] * 6)
    times = [0.1 * k for k in range(60)]

    forwards = [described(ride.at(t)) for t in times]
    shuffled = {t: described(ride.at(t)) for t in sorted(times, key=lambda t: (t * 7.3) % 1)}

    assert [shuffled[t] for t in times] == forwards


def described(state: FrameState) -> tuple[object, ...]:
    """Everything a frame state says, comparable with `==`."""
    return (
        state.time, state.latitude, state.longitude, state.metres, state.completed.tolist(), state.upcoming.tolist()
    )


def riding(steps: list[tuple[int, int]]) -> Ride:
    """A Sample a second at each (east, north), in steps of 0.0001° (~11 m) from ROW."""
    payloads = [
        Payload(sample_time=float(t), gps=[(ROW[0] + north * 0.0001, ROW[1] + east * 0.0001, ROW[2], 40.0)])
        for t, (east, north) in enumerate(steps)
    ]
    return parse_ride(exiftool_text(*payloads), duration=float(len(steps)))


def assert_split_at_the_current_position(state: FrameState) -> None:
    assert tuple(state.completed[-1]) == tuple(state.upcoming[0]) == state.metres


def test_the_split_point_lies_between_samples_when_the_time_does() -> None:
    ride = riding_east([3, 3, 3])

    state = ride.at(0.25)

    assert_split_at_the_current_position(state)
    assert len(state.completed) == 2, "the first Sample, then the Split point"
    assert len(state.upcoming) == 3, "the Split point, then the second and third Samples"
    assert state.completed[1][0] - state.completed[0][0] == pytest.approx(EAST_STEP / 4, rel=0.01)
    assert state.upcoming[1][0] - state.upcoming[0][0] == pytest.approx(EAST_STEP * 3 / 4, rel=0.01)


@pytest.mark.parametrize("t", [-1.0, 0.0, 0.5, 2.0, 3.99, 4.0, 10.0])
def test_completed_and_upcoming_make_up_the_whole_route_without_gap_or_overlap(t: float) -> None:
    ride = riding_east([3, 3, 0, 3, 3, 3])
    # The valid Samples' positions, at their own times.
    route = [ride.at(float(time)).metres for time in (0, 1, 3, 4, 5)]

    state = ride.at(t)

    assert_split_at_the_current_position(state)
    joined = [tuple(point) for point in [*state.completed[:-1], *state.upcoming[1:]]]
    assert joined == pytest.approx(route)


def test_an_out_and_back_route_splits_by_time_not_by_place() -> None:
    # East 4 steps and back, a Sample a second: 1.5 steps east is passed at 1.5 s and again at 6.5 s.
    ride = riding([(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (3, 0), (2, 0), (1, 0), (0, 0)])
    out, back = ride.at(1.5), ride.at(6.5)

    assert back.metres == pytest.approx(out.metres)
    assert (len(out.completed), len(out.upcoming)) == (3, 8), "on the way out, the way back is all Upcoming"
    assert (len(back.completed), len(back.upcoming)) == (8, 3), "on the way back, the way out is all Completed"
    assert back.upcoming[-1][0] < back.upcoming[0][0], "what's left heads west, home"


def test_a_self_crossing_route_splits_by_time_at_the_crossing() -> None:
    # East along the bottom, up, back west, and south through (2, 0) again at 10 s, which it first crossed at 2 s.
    ride = riding(
        [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1), (4, 2), (3, 2), (2, 2), (2, 1), (2, 0), (2, -1), (2, -2)]
    )
    first, second = ride.at(2.0), ride.at(10.0)

    assert second.metres == pytest.approx(first.metres)
    assert len(first.completed) - 1 == 3, "Samples at 0, 1 and 2 s are Completed"
    assert len(second.completed) - 1 == 11, "the first pass through the crossing stays Completed"
    assert len(second.upcoming) - 1 == 2, "only the leg south of the crossing is Upcoming"


def test_before_the_first_fix_the_overlays_are_hidden() -> None:
    ride = riding_east([0, 0, 3, 3, 3])

    assert ride.at(0.0).visibility == ride.at(1.9).visibility == "hidden"
    assert ride.at(2.0).visibility == "live", "from the first Fix on"


def test_after_the_last_fix_the_current_position_holds_at_the_final_sample() -> None:
    ride = riding_east([3, 3, 3, 0])

    last, after = ride.at(2.0), ride.at(3.5)

    assert last.visibility == "live"
    assert after.visibility == "held_final"
    assert after.metres == last.metres


def test_across_a_short_fix_gap_the_current_position_is_interpolated() -> None:
    ride = riding_east([3, 0, 3])

    state = ride.at(1.0)

    assert state.visibility == "live"
    assert state.metres[0] - ride.at(0.0).metres[0] == pytest.approx(EAST_STEP, rel=0.01), "halfway across 2 s"


def test_during_a_long_fix_gap_the_current_position_holds_dimmed_at_the_last_sample() -> None:
    # Valid at 0 s, then no Fix until 10 s.
    ride = riding_east([3] + [0] * 9 + [3, 3])

    held = [ride.at(t) for t in (0.1, 5.0, 9.9)]

    assert [state.visibility for state in held] == ["held_dimmed"] * 3
    assert all(state.metres == ride.at(0.0).metres for state in held), "never moving towards the next Fix"
    assert ride.at(0.0).visibility == ride.at(10.0).visibility == "live", "at the valid Samples either side"


def test_the_longest_fix_gap_interpolated_across_is_configurable() -> None:
    ride = riding_east([3, 0, 3])

    assert ride.at(1.0, max_interpolated_gap=1.5).visibility == "held_dimmed"
    assert ride.at(1.0, max_interpolated_gap=2.0).visibility == "live", "a gap exactly at the limit is interpolated"


def travelling(steps: list[tuple[float, float, float]], rate: int = 10) -> Ride:
    """`rate` Samples a second at each (east, north, km/h), east and north in steps of 0.00001° (~1.1 m) from ROW."""
    payloads = [
        Payload(
            sample_time=float(t),
            gps=[
                (ROW[0] + north * 0.00001, ROW[1] + east * 0.00001, ROW[2], kmh)
                for east, north, kmh in steps[t * rate : (t + 1) * rate]
            ],
        )
        for t in range(math.ceil(len(steps) / rate))
    ]
    return parse_ride(exiftool_text(*payloads), duration=len(steps) / rate)


def straight(start: tuple[float, float], heading: float, count: int, kmh: float = 40.0) -> list[tuple[float, float, float]]:
    """`count` Samples from `start` towards `heading`, degrees clockwise from north, at 10 Hz and `kmh`."""
    step = kmh / KMH_PER_MS / 10 / 1.1
    east, north = math.sin(math.radians(heading)) * step, math.cos(math.radians(heading)) * step
    return [(start[0] + east * k, start[1] + north * k, kmh) for k in range(count)]


def turned(a: float, b: float) -> float:
    """Degrees clockwise from Heading `a` to Heading `b`, the short way round: -180 up to 180."""
    return (b - a + 180) % 360 - 180


def headings(ride: Ride, start: float, end: float, fps: int = 30) -> list[float]:
    """The Heading at every frame from `start` to `end` seconds, at `fps` frames a second."""
    return [ride.at(start + k / fps).heading for k in range(round((end - start) * fps) + 1)]


def arc(centre: tuple[float, float], radius: float, start: float, sweep: float, kmh: float) -> list[tuple[float, float, float]]:
    """Samples at 10 Hz and `kmh` round a circle of `radius` steps, from `start` degrees clockwise from north
    of `centre` through `sweep` degrees clockwise."""
    count = round(abs(sweep) * math.pi / 180 * radius * 1.1 / (kmh / KMH_PER_MS / 10))
    angles = [math.radians(start + sweep * k / count) for k in range(count)]
    return [(centre[0] + radius * math.sin(a), centre[1] + radius * math.cos(a), kmh) for a in angles]


@pytest.mark.parametrize("heading", [0.0, 45.0, 90.0, 180.0, 270.0])
def test_along_a_straight_line_the_heading_is_its_direction_clockwise_from_north(heading: float) -> None:
    ride = travelling(straight((0, 0), heading, 50))

    assert all(abs(turned(heading, ride.at(t).heading)) < 1 for t in (0.0, 2.0, 4.9))


def test_through_a_right_angle_bend_the_heading_turns_smoothly_across_about_a_second() -> None:
    # North for 3 s, then east for 3 s from the corner at 3 s.
    north = straight((0, 0), 0.0, 30)
    ride = travelling(north + straight((north[-1][0], north[-1][1] + north[1][1]), 90.0, 30))
    turn = headings(ride, 2.0, 4.0)

    assert abs(turned(0.0, ride.at(2.4).heading)) < 1, "north until half a second before the corner"
    assert ride.at(3.0).heading == pytest.approx(45.0, abs=5.0), "halfway round at the corner"
    assert ride.at(3.6).heading == pytest.approx(90.0, abs=1.0), "east from half a second after"
    assert all(0 <= turned(a, b) < 5 for a, b in zip(turn, turn[1:])), "clockwise, a few degrees a frame at most"


def test_through_a_u_turn_the_heading_comes_round_the_way_the_motorcycle_does() -> None:
    # North at 20 km/h, round a 10 m-wide semicircle to the right, then south.
    up = straight((0, 0), 0.0, 20, kmh=20)
    radius = 4.5
    bend = arc((up[-1][0] + radius, up[-1][1]), radius, 270.0, 180.0, kmh=20)
    ride = travelling(up + bend + straight((2 * radius, up[-1][1]), 180.0, 20, kmh=20))
    turn = headings(ride, 1.0, ride.duration - 1.0)

    assert abs(turned(0.0, turn[0])) < 2 and abs(turned(180.0, turn[-1])) < 2
    assert abs(turned(90.0, ride.at(ride.duration / 2).heading)) < 10, "east at the far end of the turn"
    assert all(0 <= turned(a, b) < 15 for a, b in zip(turn, turn[1:])), "clockwise all the way, without flipping"


def test_turning_through_north_the_heading_wraps_from_just_under_360_to_just_over_0() -> None:
    # Round a wide right-hand curve from north-west to north-east.
    ride = travelling(arc((50, 0), 50.0, 225.0, 90.0, kmh=40))
    turn = headings(ride, 0.5, ride.duration - 0.5)

    assert 300 < turn[0] < 360 and 0 <= turn[-1] < 60
    assert all(0 <= heading < 360 for heading in turn)
    assert all(0 <= turned(a, b) < 3 for a, b in zip(turn, turn[1:])), "steadily clockwise, no jump at north"


def stopping(start: tuple[float, float], heading: float, kmh: float) -> list[tuple[float, float, float]]:
    """Slowing evenly from `kmh` to a stop over 2 s along `heading`, at 10 Hz."""
    rows: list[tuple[float, float, float]] = []
    east, north = start
    for k in range(20):
        speed = kmh * (1 - k / 20)
        rows.append((east, north, speed))
        step = speed / KMH_PER_MS / 10 / 1.1
        east, north = east + math.sin(math.radians(heading)) * step, north + math.cos(math.radians(heading)) * step
    return rows


def jittering(at: tuple[float, float], seconds: float, metres: float = 1.5) -> list[tuple[float, float, float]]:
    """Standing at `at` for `seconds`, at 10 Hz, as GPS reports it: positions scattered by up to `metres` and a
    speed just above zero."""
    scatter = [math.sin(k * 12.9898) * 43758.5453 % 1 * 2 - 1 for k in range(round(seconds * 20))]
    return [(at[0] + scatter[2 * k] * metres / 1.1, at[1] + scatter[2 * k + 1] * metres / 1.1, 0.4) for k in range(round(seconds * 10))]


def test_standing_still_with_gps_jitter_the_heading_holds_exactly_at_the_last_good_one() -> None:
    # North-east at 40 km/h, slowing to a stop, then 3 s standing still while the position wanders.
    moving = straight((0, 0), 45.0, 20)
    slowing = stopping((moving[-1][0] + moving[1][0], moving[-1][1] + moving[1][1]), 45.0, 40.0)
    ride = travelling(moving + slowing + jittering(slowing[-1][:2], 3.0))
    standing = headings(ride, 4.0, ride.duration)

    assert len(set(standing)) == 1, "perfectly still, not a flicker"
    assert abs(turned(45.0, standing[0])) < 1, "still facing the way it stopped"


def test_stopping_at_a_junction_then_turning_away_holds_the_heading_until_moving_again() -> None:
    # North, stopped 4 s at the junction with the position wandering, then off east, slowly at first.
    north = straight((0, 0), 0.0, 30)
    slowing = stopping((north[-1][0], north[-1][1] + north[1][1]), 0.0, 40.0)
    junction = slowing[-1][:2]
    pulling_away = [(junction[0] + 0.25 * k * k / 1.1 / 10, junction[1], 2.0 * k) for k in range(1, 11)]
    east = straight((pulling_away[-1][0] + 0.6, junction[1]), 90.0, 30, kmh=20)
    ride = travelling(north + slowing + jittering(junction, 4.0) + pulling_away + east)
    stopped_at = 5.0

    held = headings(ride, stopped_at, 9.0)
    assert len(set(held)) == 1 and abs(turned(0.0, held[0])) < 1, "north, still, through the stop"
    assert len(set(headings(ride, 9.0, 9.25))) == 1, "still north while pulling away below 8 km/h"
    assert abs(turned(90.0, ride.at(10.6).heading)) < 2, "east once moving east"
    away = headings(ride, 9.0, 11.0)
    assert all(0 <= turned(a, b) < 15 for a, b in zip(away, away[1:])), "turning to it without a snap"


def test_during_a_long_fix_gap_the_heading_holds_with_the_current_position() -> None:
    # East for 2 s, no Fix for 5 s, then north: the Heading mustn't turn towards where the Fix comes back.
    east = straight((0, 0), 90.0, 20)
    north = straight((east[-1][0], 100.0), 0.0, 20)
    ride = travelling(east + [(0.0, 0.0, 0.0)] * 50 + north)
    ride = Ride(duration=ride.duration, samples=[s for s in ride.samples if not 2.0 <= s.time < 7.0])

    assert ride.at(3.0).visibility == "held_dimmed"
    assert ride.at(3.0).heading == ride.at(6.9).heading == ride.at(1.9).heading
    assert abs(turned(90.0, ride.at(6.9).heading)) < 1


def test_the_heading_smoothing_is_configurable() -> None:
    # East at 6 km/h, under the default 8 km/h below which the Heading holds; it never got faster, so north.
    ride = travelling(straight((0, 0), 90.0, 30, kmh=6.0))

    assert ride.at(1.5).heading == 0.0
    assert abs(turned(90.0, ride.at(1.5, heading=HeadingSmoothing(min_speed=5 / KMH_PER_MS)).heading)) < 1
    north = straight((0, 0), 0.0, 30)
    bend = travelling(north + straight((north[-1][0], north[-1][1] + north[1][1]), 90.0, 30))
    wide = HeadingSmoothing(window=4.0)
    assert abs(turned(45.0, bend.at(3.0, heading=wide).heading)) < 5
    assert 10 < bend.at(2.4, heading=wide).heading < 40, "a wider window starts turning sooner"


def test_a_moment_of_speed_noise_while_standing_still_does_not_turn_the_heading() -> None:
    # As standing still with jitter, but two Samples report 12 km/h halfway through.
    moving = straight((0, 0), 45.0, 20)
    slowing = stopping((moving[-1][0] + moving[1][0], moving[-1][1] + moving[1][1]), 45.0, 40.0)
    standing = jittering(slowing[-1][:2], 4.0)
    standing[20:22] = [(east, north, 12.0) for east, north, _ in standing[20:22]]
    ride = travelling(moving + slowing + standing)
    still = headings(ride, 4.0, ride.duration)

    assert len(set(still)) == 1 and abs(turned(45.0, still[0])) < 1


def test_creeping_in_traffic_the_heading_never_snaps() -> None:
    # East along a road, the speed swinging between 5 and 11 km/h every second, with the position wandering.
    rows: list[tuple[float, float, float]] = []
    east = 0.0
    for k in range(150):
        kmh = 8 + 3 * math.sin(k * math.pi / 5)
        wander = math.sin(k * 12.9898) * 43758.5453 % 1 - 0.5
        rows.append((east, wander / 1.1, kmh))
        east += kmh / KMH_PER_MS / 10 / 1.1
    ride = travelling(rows)
    creeping = headings(ride, 1.0, ride.duration - 1.0)

    assert all(abs(turned(90.0, heading)) < 30 for heading in creeping), "east, give or take the wander"
    assert all(abs(turned(a, b)) < 5 for a, b in zip(creeping, creeping[1:])), "a few degrees a frame at most"
