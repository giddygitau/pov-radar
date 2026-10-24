from dataclasses import dataclass, field

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


def exiftool_text(*payloads: Payload) -> str:
    """Mimics `exiftool -a -ee -G3 -n -s <tags>`: grouped by tag, then by payload."""
    lines: list[str] = []

    def emit(name: str, values: list[tuple[int, object]]) -> None:
        lines.extend(f"[Doc{doc}]          {name:<32}: {value}" for doc, value in values)

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


def test_samples_are_spread_evenly_across_their_payload() -> None:
    text = exiftool_text(
        Payload(sample_time=0.0, sample_duration=1.0, gps=[ROW] * 4),
        Payload(sample_time=1.0, sample_duration=0.5, gps=[ROW] * 2),
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
