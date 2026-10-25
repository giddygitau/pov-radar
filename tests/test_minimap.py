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
DOT = (round(RADIUS * 1.22), round(RADIUS))
"""Row and column of the dot: 0.22 of the radius below centre, so more lies ahead than behind."""
PX_PER_METRE = RADIUS * 1.22 / 250
"""The top edge is 250 m ahead of the dot."""



def test_a_straight_road_is_drawn_around_the_dot_north_up(request: pytest.FixtureRequest) -> None:
    minimap = Minimap(NORTH, FRAME_HEIGHT)

    image = minimap.frames(NORTH_ROAD)(NORTH.at(50.0))

    assert image.shape == (259, 259, 4), "24% of 1080 across"
    assert image[0, 0, 3] == image[-1, -1, 3] == 0, "outside the circle is transparent"
    white = np.argwhere((image[:, :, :3] > 240).all(axis=2) & (image[:, :, 3] > 240))
    assert white.mean(axis=0) == pytest.approx((DOT[0], RADIUS), abs=1.5)
    column = image[:, DOT[1]]
    assert is_upcoming(column[DOT[0] - 40]) and is_upcoming(column[30]), "the Upcoming route runs north, up"
    assert is_completed(column[DOT[0] + 40]), "the Completed route runs south, down"
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

    ahead, behind = round(DOT[0] - 100 * PX_PER_METRE), round(DOT[0] + 60 * PX_PER_METRE)
    brightness = image[:, :, :3].astype(int).sum(axis=2)
    surface = brightness[ahead - 15, 60]
    assert brightness[ahead, 60] > surface + 150 < brightness[ahead, 200], "the mid road crosses both sides"
    assert surface + 60 < brightness[behind, 60], "the minor road leaves westward"
    assert brightness[behind, 200] == pytest.approx(surface, abs=10), "and not eastward"
    assert brightness[ahead, 60] > brightness[behind, 60], "the mid road is brighter than the minor"
    assert_matches_golden(image, "minimap-junction", request)


def test_during_a_long_fix_gap_the_map_freezes_and_the_dot_dims(request: pytest.FixtureRequest) -> None:
    # No valid Sample from 51 s to 59 s: the last before the gap is at the origin at 50 s.
    gap = Ride(duration=NORTH.duration, samples=[s for s in NORTH.samples if not 50 < s.time < 60])
    draw = Minimap(gap, FRAME_HEIGHT).frames(NORTH_ROAD)

    live, early, late = draw(gap.at(50.0)), draw(gap.at(52.0)), draw(gap.at(58.0))

    assert np.array_equal(early, late), "frozen through the gap"
    assert (live[DOT][:3] > 240).all(), "white while the Fix lasts"
    assert early[DOT][:3].max() < 200, "dimmed while held"
    away = np.ones(live.shape[:2], bool)
    away[DOT[0] - 12 : DOT[0] + 13, DOT[1] - 12 : DOT[1] + 13] = False
    assert np.array_equal(live[away], early[away]), "the map stays where it was at the last valid Sample"
    assert_matches_golden(early, "minimap-held", request)


def test_before_the_first_fix_the_minimap_is_fully_transparent() -> None:
    late = Ride(duration=NORTH.duration, samples=NORTH.samples[10:])
    minimap = Minimap(late, FRAME_HEIGHT)

    image = minimap.frames(NORTH_ROAD)(late.at(5.0))

    assert image.shape == (minimap.height, minimap.width, 4)
    assert not image.any()
