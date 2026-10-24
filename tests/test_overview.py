from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from pov_radar.overview import Overview, read_png, write_png
from pov_radar.projection import LocalProjection
from pov_radar.ride import Ride, Sample, join
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


def ride(points_km: list[tuple[float, float]], spacing: float = 5.0) -> Ride:
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


SQUARE = ride(along((0, 0), (2, 0), 20) + along((2, 0), (2, 2), 20)[1:] + along((2, 2), (0, 2), 20)[1:])
SQUARE_ROADS: list[tuple[RoadClass, list[tuple[float, float]]]] = [
    ("major", [(-1.0, -0.02), (3.0, -0.02)]),
    ("mid", [(2.03, -1.0), (2.03, 3.0)]),
    ("minor", [(0.0, 1.0), (2.0, 1.0)]),
    ("minor", [(1.0, -1.0), (1.0, 3.0)]),
]


def test_a_square_route_is_drawn_over_its_roads_north_up(request: pytest.FixtureRequest) -> None:
    image = Overview(SQUARE, FRAME_HEIGHT).render(roads(SQUARE, SQUARE_ROADS))

    height, width, _ = image.shape
    assert height == 302, "28% of 1080"
    assert abs(width - height) <= 3, "about as wide as high, for a square Route"
    assert image[0, 0, 3] == image[-1, -1, 3] == 0, "outside the rounded corners is transparent"
    assert_matches_golden(image, "square", request)


def test_a_long_thin_route_widens_the_overview_as_far_as_allowed_without_distortion(request: pytest.FixtureRequest) -> None:
    # 12 km east by 1 km north: wider than the widest Overview, so it fits the width and is centred vertically.
    thin = ride(along((0, 0), (6, 1), 60) + along((6, 1), (12, 0), 60)[1:])
    image = Overview(thin, FRAME_HEIGHT).render(roads(thin, [("major", [(-1.0, 0.0), (13.0, 0.0)])]))

    height, width, _ = image.shape
    assert (height, width) == (302, 544), "1.8 times as wide as high"
    route = np.argwhere((image[:, :, 1] > 200) & (image[:, :, 0] < 60))
    (top, left), (bottom, right) = route.min(axis=0), route.max(axis=0)
    assert (right - left) / (bottom - top) == pytest.approx(12, rel=0.1), "the Route keeps its 12:1 shape"
    assert_matches_golden(image, "long-thin", request)


def test_a_loop_is_drawn_whole(request: pytest.FixtureRequest) -> None:
    angles = np.linspace(0, 2 * np.pi, 120)
    loop = ride([(float(1.5 * np.sin(a)), float(1.0 - np.cos(a))) for a in angles])
    image = Overview(loop, FRAME_HEIGHT).render(
        roads(loop, [("mid", [(-2.0, 0.0), (2.0, 0.0)]), ("minor", [(0.0, -1.0), (0.0, 3.0)])])
    )

    assert_matches_golden(image, "loop", request)


def test_a_rejected_far_off_sample_does_not_change_the_framing(request: pytest.FixtureRequest) -> None:
    between = (SQUARE.samples[29].time + SQUARE.samples[30].time) / 2
    samples = [*SQUARE.samples[:30], sample(between, 40.0, -25.0), *SQUARE.samples[30:]]
    with_outlier = join([Ride(duration=SQUARE.duration, samples=[s.model_copy() for s in samples])])
    assert [issue.kind for issue in with_outlier.issues] == ["implausible_jump"]

    image = Overview(with_outlier, FRAME_HEIGHT).render(roads(with_outlier, SQUARE_ROADS))

    assert_matches_golden(image, "square", request)


def test_dense_minor_roads_are_thinned_but_sparse_ones_kept(request: pytest.FixtureRequest) -> None:
    # A city grid of minor roads 25 m apart over the west half of the square; one lone minor road in the east half.
    grid = [k * 0.025 for k in range(41)]
    lines: list[tuple[RoadClass, list[tuple[float, float]]]] = [
        *(("minor", [(x, 0.0), (x, 2.0)]) for x in grid),
        *(("minor", [(0.0, 2 * y), (1.0, 2 * y)]) for y in grid),
        ("minor", [(1.5, 0.2), (1.5, 1.8)]),
    ]
    image = Overview(SQUARE, FRAME_HEIGHT).render(roads(SQUARE, lines))

    road = image[:, :, 0] > 40  # Any road is far brighter than the near-black surface.
    rows = _square_pixels(image.shape[0], 0.1, 1.9)  # Symmetric, so north–south order is moot.
    assert road[rows, _square_pixels(image.shape[1], 0.1, 0.9)].mean() < 0.3, "the city grid shows streets, not a solid block"
    lone = road[_square_pixels(image.shape[0], 0.25, 1.75), _square_pixels(image.shape[1], 1.48, 1.52)].any(axis=1)
    assert lone.mean() > 0.95, "the lone road is drawn its whole length"
    assert_matches_golden(image, "dense-minor-roads", request)


def _square_pixels(size: int, start_km: float, end_km: float) -> slice:
    """The pixels from `start_km` to `end_km` along a side of the square Route's Overview, measured from the left
    edge, or for rows the top edge; the Route's 2 km plus 8% padding either side spans the Overview."""
    route = 2 / 2.32 * size
    edge = size / 2 - route / 2
    return slice(round(edge + start_km / 2 * route), round(edge + end_km / 2 * route) + 1)
