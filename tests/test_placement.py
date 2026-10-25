import numpy as np
import pytest

from pov_radar.config import Config, Corner
from pov_radar.minimap import Minimap
from pov_radar.overview import Overview
from pov_radar.placement import Placement, PlacementError, in_frame, place
from synthetic import along, ride

RIDE = ride(along((0, 0), (3, 1), 50))


@pytest.mark.parametrize(
    ("corner", "x", "y"),
    [("top-left", 32, 32), ("top-right", 1920 - 32 - 200, 32), ("bottom-left", 32, 1080 - 32 - 100), ("bottom-right", 1920 - 32 - 200, 1080 - 32 - 100)],
)
def test_an_overlay_sits_its_margin_from_both_edges_beside_its_corner(corner: Corner, x: int, y: int) -> None:
    placement = place(corner, 0.03, 200, 100, (1920, 1080))

    assert placement == Placement(corner=corner, margin=32, x=x, y=y, width=200, height=100)


@pytest.mark.parametrize("make", [Minimap, Overview])
def test_the_same_config_gives_overlays_the_same_proportion_of_the_frame_at_1080p_and_4k(make: type[Minimap | Overview]) -> None:
    config = Config()
    settings = config.minimap if make is Minimap else config.overview

    def placed(frame: tuple[int, int]) -> Placement:
        overlay = make(RIDE, frame[1], config.style)
        return place(settings.corner, settings.margin, overlay.width, overlay.height, frame)

    hd, uhd = placed((1920, 1080)), placed((3840, 2160))

    for name in ("margin", "x", "y", "width", "height"):
        assert getattr(hd, name) / 1080 == pytest.approx(getattr(uhd, name) / 2160, abs=1 / 1080), name


def test_drawn_full_frame_an_overlay_is_in_place_on_a_transparent_frame() -> None:
    overlay = np.full((100, 200, 4), 200, np.uint8)
    placement = place("bottom-right", 0.03, 200, 100, (1920, 1080))

    frame = in_frame(overlay, placement, (1920, 1080))

    assert frame.shape == (1080, 1920, 4)
    assert (frame[placement.y : placement.y + 100, placement.x : placement.x + 200] == 200).all()
    assert frame[:, : placement.x].sum() == frame[: placement.y].sum() == 0
    assert frame[placement.y + 100 :].sum() == frame[:, placement.x + 200 :].sum() == 0


@pytest.mark.parametrize(("width", "height", "margin"), [(1900, 100, 0.03), (200, 1050, 0.03), (200, 100, 0.5)])
def test_an_overlay_that_does_not_fit_the_frame_beside_its_margin_is_an_error(width: int, height: int, margin: float) -> None:
    with pytest.raises(PlacementError, match="doesn't fit"):
        place("bottom-right", margin, width, height, (1920, 1080))
