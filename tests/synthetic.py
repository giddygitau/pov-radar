"""Synthetic Rides and Road networks around Nairobi, and golden-image comparison, for the Renderer tests."""

from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from pov_radar.drawing import read_png, write_png
from pov_radar.projection import LocalProjection
from pov_radar.ride import Ride, Sample
from pov_radar.roads import Road, RoadClass, RoadNetwork

GOLDENS = Path(__file__).parent / "goldens"

FRAME_HEIGHT = 1080

TOLERANCE = 8
"""Largest difference in any channel, out of 255, for a pixel to count as unchanged."""

MAX_CHANGED = 0.002
"""Fraction of pixels allowed to change, for antialiasing that differs between machines."""

ORIGIN = (-1.29, 36.82)
"""Nairobi, a little south of the equator."""

DEGREES_PER_KM = 1 / 111.32


def ride(points_km: list[tuple[float, float]], spacing: float = 2.0) -> Ride:
    """Valid Samples `spacing` seconds apart at (east, north) km from ORIGIN."""
    return Ride(
        duration=spacing * len(points_km),
        samples=[sample(i * spacing, east, north) for i, (east, north) in enumerate(points_km)],
    )


def sample(time: float, east_km: float, north_km: float) -> Sample:
    return Sample(
        time=time,
        latitude=ORIGIN[0] + north_km * DEGREES_PER_KM,
        longitude=ORIGIN[1] + east_km * DEGREES_PER_KM,
        altitude=1700.0,
        speed=10.0,
        fix=3,
        precision=2.0,
    )


def roads(ride: Ride, lines_km: list[tuple[RoadClass, list[tuple[float, float]]]]) -> RoadNetwork:
    """Roads through (east, north) km from ORIGIN, in the Ride's projection as the map data would give them."""
    projection = LocalProjection.around(ride)
    return RoadNetwork(
        [
            Road(
                id=i,
                road_class=road_class,
                points=projection.to_metres(
                    [ORIGIN[1] + east * DEGREES_PER_KM for east, _ in line],
                    [ORIGIN[0] + north * DEGREES_PER_KM for _, north in line],
                ),
            )
            for i, (road_class, line) in enumerate(lines_km)
        ]
    )


def along(start: tuple[float, float], end: tuple[float, float], count: int) -> list[tuple[float, float]]:
    return [(start[0] + (end[0] - start[0]) * k / count, start[1] + (end[1] - start[1]) * k / count) for k in range(count + 1)]


def assert_matches_golden(image: NDArray[np.uint8], name: str, request: pytest.FixtureRequest) -> None:
    golden = GOLDENS / f"{name}.png"
    if request.config.getoption("--regenerate-goldens"):
        GOLDENS.mkdir(exist_ok=True)
        write_png(image, golden)
    assert golden.exists(), f"No golden {golden.name}; draw one deliberately with `pytest --regenerate-goldens`"
    expected = read_png(golden)
    assert image.shape == expected.shape, f"{name}: {image.shape[1]}×{image.shape[0]}, golden {expected.shape[1]}×{expected.shape[0]}"
    changed = np.abs(image.astype(np.int16) - expected.astype(np.int16)).max(axis=2) > TOLERANCE
    assert changed.mean() <= MAX_CHANGED, f"{name}: {changed.mean():.2%} of pixels differ from the golden"


def is_upcoming(pixel: NDArray[np.uint8]) -> bool:
    red, green, _, _ = (int(c) for c in pixel)
    return green > 200 and red < 60


def is_completed(pixel: NDArray[np.uint8]) -> bool:
    red, green, blue, _ = (int(c) for c in pixel)
    return red > 90 and abs(red - blue) < 30 and abs(red - green) < 30
