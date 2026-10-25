import numpy as np
import pytest
from numpy.typing import NDArray

from pov_radar.overview import Overview
from pov_radar.ride import Ride, join
from pov_radar.roads import RoadClass, RoadNetwork
from synthetic import FRAME_HEIGHT, along, assert_matches_golden, is_completed, is_upcoming, ride, roads, sample


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


@pytest.mark.parametrize(
    ("name", "time", "at_km"),
    [("dot-start", 0.0, (0.0, 0.0)), ("dot-corner", 40.0, (2.0, 0.0)), ("dot-between", 51.0, (2.0, 0.55))],
)
def test_a_white_dot_marks_the_current_position(
    name: str, time: float, at_km: tuple[float, float], request: pytest.FixtureRequest
) -> None:
    # The square's Samples are 0.1 km and 2 s apart: 40 s is its south-east corner and 51 s lies between two Samples 0.55 km north of it.
    draw = Overview(SQUARE, FRAME_HEIGHT).frames(roads(SQUARE, SQUARE_ROADS))

    image = draw(SQUARE.at(time))

    white = np.argwhere((image[:, :, :3] > 240).all(axis=2) & (image[:, :, 3] > 240))
    assert len(white), "the dot is drawn"
    row, column = white.mean(axis=0)
    # Rows count down from the top edge, so they measure km south of the square's north side.
    east, south = at_km[0], 2 - at_km[1]
    assert row == pytest.approx(_square_pixels(image.shape[0], south, south).start, abs=2)
    assert column == pytest.approx(_square_pixels(image.shape[1], east, east).start, abs=2)
    assert_matches_golden(image, name, request)


# East along the bottom, up the east side, back west along the top, then south through the bottom leg at (1, 0.5) km,
# which it first passed at 20 s and passes again at 120 s; Samples 2 s apart, 132 s in all.
CROSSING = ride(
    along((0, 0.5), (2, 0.5), 20)
    + along((2, 0.5), (2, 2), 15)[1:]
    + along((2, 2), (1, 2), 10)[1:]
    + along((1, 2), (1, 0), 20)[1:]
)


def colours_at(image: NDArray[np.uint8], east_km: float, north_km: float) -> tuple[bool, bool]:
    """Whether the Upcoming and Completed routes' colours show within 2 pixels of a point on the square framing."""
    row = _square_pixels(image.shape[0], 2 - north_km, 2 - north_km).start
    column = _square_pixels(image.shape[1], east_km, east_km).start
    patch = image[row - 2 : row + 3, column - 2 : column + 3].reshape(-1, 4)
    return any(is_upcoming(p) for p in patch), any(is_completed(p) for p in patch)


UPCOMING, COMPLETED = (True, False), (False, True)


@pytest.mark.parametrize(
    ("name", "time", "expected"),
    [
        ("split-start", 0.0, {(0.5, 0.5): UPCOMING, (2.0, 1.2): UPCOMING, (1.0, 0.2): UPCOMING}),
        ("split-crossing", 120.0, {(0.5, 0.5): COMPLETED, (2.0, 1.2): COMPLETED, (1.0, 0.2): UPCOMING}),
        ("split-end", 132.0, {(0.5, 0.5): COMPLETED, (2.0, 1.2): COMPLETED, (1.0, 0.2): COMPLETED}),
    ],
)
def test_the_route_is_completed_up_to_the_dot_and_upcoming_after_it_by_time(
    name: str, time: float, expected: dict[tuple[float, float], tuple[bool, bool]], request: pytest.FixtureRequest
) -> None:
    draw = Overview(CROSSING, FRAME_HEIGHT).frames(RoadNetwork([]))

    image = draw(CROSSING.at(time))

    assert {point: colours_at(image, *point) for point in expected} == expected
    assert_matches_golden(image, name, request)


def test_before_the_first_fix_the_overview_is_fully_transparent() -> None:
    late = Ride(duration=SQUARE.duration, samples=SQUARE.samples[2:])
    overview = Overview(late, FRAME_HEIGHT)
    draw = overview.frames(roads(late, SQUARE_ROADS))

    image = draw(late.at(2.0))

    assert image.shape == (overview.height, overview.width, 4), "the Overlay keeps its size"
    assert not image.any(), "every pixel is transparent black"


def test_during_a_long_fix_gap_the_dot_holds_dimmed_at_the_last_position(request: pytest.FixtureRequest) -> None:
    # No valid Sample between the south-east corner at 40 s and the next at 46 s.
    gap = Ride(duration=SQUARE.duration, samples=[s for s in SQUARE.samples if s.time not in (42.0, 44.0)])
    draw = Overview(gap, FRAME_HEIGHT).frames(roads(gap, SQUARE_ROADS))

    live, held = draw(gap.at(40.0)), draw(gap.at(43.0))

    row = _square_pixels(held.shape[0], 2.0, 2.0).start
    column = _square_pixels(held.shape[1], 2.0, 2.0).start
    assert (live[row, column, :3] > 240).all(), "white at the corner while the Fix lasts"
    assert held[row, column, :3].max() < 200, "dimmed at the corner during the gap"
    assert int(held[row, column, 0]) > int(held[row + 20, column - 20, 0]), "but still brighter than the bare surface"
    assert_matches_golden(held, "dot-held-dimmed", request)
