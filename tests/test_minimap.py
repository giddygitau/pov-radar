import math

import numpy as np
import pytest

from pov_radar.minimap import Minimap
from pov_radar.ride import Ride
from synthetic import FRAME_HEIGHT, along, assert_matches_golden, is_completed, is_upcoming, ride, roads

# Due north along a straight road at 36 km/h, a Sample every 10 m; at 50 s it is at the origin.
NORTH = ride(along((0, -0.5), (0, 0.5), 100), spacing=1.0)
NORTH_ROAD = roads(NORTH, [("major", [(0.004, -1.0), (0.004, 1.0)])])

RADIUS = 129.5
"""Pixels: the Minimap is 24% of 1080, 259 pixels, across."""
CURRENT = (round(RADIUS * 1.22), round(RADIUS))
"""Row and column of the Current position: 0.22 of the radius below centre, so more lies ahead than behind."""
PX_PER_METRE = RADIUS * 1.22 / 250
"""The top edge is 250 m ahead of the Current position."""


def facing(heading: float, right_km: float, ahead_km: float) -> tuple[float, float]:
    """(east, north) km of a point `right_km` to the right of and `ahead_km` ahead of the origin, facing `heading`."""
    h = math.radians(heading)
    return (right_km * math.cos(h) + ahead_km * math.sin(h), -right_km * math.sin(h) + ahead_km * math.cos(h))


def test_heading_north_a_straight_road_is_drawn_around_the_arrow_north_up(request: pytest.FixtureRequest) -> None:
    minimap = Minimap(NORTH, FRAME_HEIGHT)

    image = minimap.frames(NORTH_ROAD)(NORTH.at(50.0))

    assert image.shape == (259, 259, 4), "24% of 1080 across"
    assert image[0, 0, 3] == image[-1, -1, 3] == 0, "outside the circle is transparent"
    assert_points_up(image)
    column = image[:, CURRENT[1]]
    assert is_upcoming(column[CURRENT[0] - 40]) and is_upcoming(column[30]), "the Upcoming route runs north, up"
    assert is_completed(column[CURRENT[0] + 40]), "the Completed route runs south, down"
    assert_matches_golden(image, "minimap-straight", request)


def test_roads_meeting_ahead_show_by_class(request: pytest.FixtureRequest) -> None:
    # 100 m ahead a mid road crosses east–west, and a minor road leaves it westward 60 m behind.
    junction = roads(
        NORTH,
        [
            ("major", [(0.004, -1.0), (0.004, 1.0)]),
            ("mid", [(-1.0, 0.1), (1.0, 0.1)]),
            ("minor", [(0.004, -0.06), (-1.0, -0.06)]),
        ],
    )
    minimap = Minimap(NORTH, FRAME_HEIGHT)

    image = minimap.frames(junction)(NORTH.at(50.0))

    ahead, behind = round(CURRENT[0] - 100 * PX_PER_METRE), round(CURRENT[0] + 60 * PX_PER_METRE)
    brightness = image[:, :, :3].astype(int).sum(axis=2)
    surface = brightness[ahead - 15, 60]
    assert brightness[ahead, 60] > surface + 150 < brightness[ahead, 200], "the mid road crosses both sides"
    assert surface + 60 < brightness[behind, 60], "the minor road leaves westward"
    assert brightness[behind, 200] == pytest.approx(surface, abs=10), "and not eastward"
    assert brightness[ahead, 60] > brightness[behind, 60], "the mid road is brighter than the minor"
    assert_matches_golden(image, "minimap-junction", request)


def test_during_a_long_fix_gap_the_map_freezes_and_the_arrow_dims(request: pytest.FixtureRequest) -> None:
    # No valid Sample from 51 s to 59 s: the last before the gap is at the origin at 50 s.
    gap = Ride(duration=NORTH.duration, samples=[s for s in NORTH.samples if not 50 < s.time < 60])
    draw = Minimap(gap, FRAME_HEIGHT).frames(NORTH_ROAD)

    live, early, late = draw(gap.at(50.0)), draw(gap.at(52.0)), draw(gap.at(58.0))

    assert np.array_equal(early, late), "frozen through the gap"
    assert (live[CURRENT][:3] > 240).all(), "white while the Fix lasts"
    assert early[CURRENT][:3].max() < 200, "dimmed while held"
    away = np.ones(live.shape[:2], bool)
    away[CURRENT[0] - 30 : CURRENT[0] + 26, CURRENT[1] - 24 : CURRENT[1] + 25] = False
    assert np.array_equal(live[away], early[away]), "the map stays where it was at the last valid Sample"
    assert_matches_golden(early, "minimap-held", request)


def test_before_the_first_fix_the_minimap_is_fully_transparent() -> None:
    late = Ride(duration=NORTH.duration, samples=NORTH.samples[10:])
    minimap = Minimap(late, FRAME_HEIGHT)

    image = minimap.frames(NORTH_ROAD)(late.at(5.0))

    assert image.shape == (minimap.height, minimap.width, 4)
    assert not image.any()


@pytest.mark.parametrize("heading", [90.0, 180.0])
def test_the_map_turns_so_the_heading_is_up_under_an_arrow_pointing_up(heading: float, request: pytest.FixtureRequest) -> None:
    # Along a major road through the origin at 50 s; 100 m ahead a mid road crosses it, and 60 m behind a minor
    # road leaves it to the left.
    travel = ride(along(facing(heading, 0, -0.5), facing(heading, 0, 0.5), 100), spacing=1.0)
    junction = roads(
        travel,
        [
            ("major", [facing(heading, 0, -1.0), facing(heading, 0, 1.0)]),
            ("mid", [facing(heading, -1.0, 0.1), facing(heading, 1.0, 0.1)]),
            ("minor", [facing(heading, 0, -0.06), facing(heading, -1.0, -0.06)]),
        ],
    )
    state = travel.at(50.0)
    assert state.heading == pytest.approx(heading, abs=0.5)

    image = Minimap(travel, FRAME_HEIGHT).frames(junction)(state)

    column = image[:, CURRENT[1]]
    assert is_upcoming(column[CURRENT[0] - 50]) and is_upcoming(column[30]), "the Upcoming route runs up"
    assert is_completed(column[CURRENT[0] + 40]), "the Completed route runs down"
    ahead, behind = round(CURRENT[0] - 100 * PX_PER_METRE), round(CURRENT[0] + 60 * PX_PER_METRE)
    brightness = image[:, :, :3].astype(int).sum(axis=2)
    surface = brightness[ahead - 15, 60]
    assert brightness[ahead, 60] > surface + 150 < brightness[ahead, 200], "the mid road crosses ahead"
    assert surface + 60 < brightness[behind, 60], "the minor road leaves to the left"
    assert brightness[behind, 200] == pytest.approx(surface, abs=10), "and not to the right"
    assert_points_up(image)
    assert_matches_golden(image, f"minimap-heading-{heading:03.0f}", request)


def assert_points_up(image: np.ndarray) -> None:
    """The white arrow at the Current position is centred on it across, and tapers to a tip above it."""
    white = np.argwhere((image[:, :, :3] > 240).all(axis=2) & (image[:, :, 3] > 240))
    rows, columns = white[:, 0], white[:, 1]
    assert columns.mean() == pytest.approx(RADIUS, abs=1.0), "symmetrical about the Current position's column"
    assert CURRENT[0] - rows.min() > 1.2 * (rows.max() - CURRENT[0]), "longer above the Current position than below"
    width = {row: (rows == row).sum() for row in (CURRENT[0] - 12, CURRENT[0] + 8)}
    assert width[CURRENT[0] - 12] < width[CURRENT[0] + 8], "narrow at the tip, wide at the back"
