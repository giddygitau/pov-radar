import shutil
from pathlib import Path

import pytest

from pov_radar.maps import EXTRACT_URL, extract_path
from pov_radar.projection import LocalProjection
from pov_radar.ride import Ride, Sample
from pov_radar.roads import MapData

FIXTURE = Path(__file__).parent / "fixtures" / "roads.osm"


def sample(time: float, latitude: float, longitude: float) -> Sample:
    return Sample(time=time, latitude=latitude, longitude=longitude, altitude=1746.0, speed=10.0, fix=3, precision=2.0)


RIDE = Ride(duration=2.0, samples=[sample(0.0, -0.700, 36.900), sample(1.0, -0.700, 36.910)])


@pytest.fixture
def map_data(tmp_path: Path) -> MapData:
    extract = tmp_path / FIXTURE.name
    shutil.copy(FIXTURE, extract)
    return MapData.build(extract)


def test_roads_are_classed_major_mid_or_minor_by_their_highway_tag(map_data: MapData) -> None:
    network = map_data.roads_around(RIDE)

    by_class = {road.id: road.road_class for road in network.roads if road.id < 200}
    assert by_class == {
        **dict.fromkeys([101, 102, 103, 104], "major"),
        **dict.fromkeys([111, 112], "mid"),
        **dict.fromkeys([121, 122, 123, 124], "minor"),
    }


def test_footways_cycleways_paths_service_roads_steps_bridleways_and_non_roads_are_dropped(
    map_data: MapData,
) -> None:
    network = map_data.roads_around(RIDE)

    assert not [road.id for road in network.roads if 200 <= road.id < 300]


def test_roads_whose_bounds_reach_the_ride_bounding_box_plus_margin_are_kept_and_far_ones_left_out(map_data: MapData) -> None:
    ids = {road.id for road in map_data.roads_around(RIDE).roads}

    assert 301 in ids, "a road crossing the box is kept whole, though none of its nodes lie inside"
    assert 302 not in ids


def test_the_margin_is_metres_beyond_the_valid_samples(map_data: MapData) -> None:
    # Road 301 runs north–south 550 m east of the first Sample; a Ride there alone reaches it only with a wide margin.
    ride = Ride(duration=1.0, samples=[sample(0.0, -0.700, 36.900)])

    assert 301 not in {road.id for road in map_data.roads_around(ride, margin=500).roads}
    assert 301 in {road.id for road in map_data.roads_around(ride, margin=600).roads}


def test_a_road_through_a_sample_position_lands_where_that_sample_does(map_data: MapData) -> None:
    projection = LocalProjection.around(RIDE)
    (motorway,) = [road for road in map_data.roads_around(RIDE).roads if road.id == 101]

    first = RIDE.samples[0]
    assert motorway.points[0] == pytest.approx(projection.to_metres([first.longitude], [first.latitude])[0], abs=0.01)
    assert motorway.points[0][0] == pytest.approx(-556.6, abs=1), "x is metres east of the Ride's centre"
    assert motorway.points[0][1] == pytest.approx(0.0, abs=1)


def test_the_extract_date_falls_back_to_its_newest_road_when_the_header_has_none(map_data: MapData) -> None:
    assert map_data.date == "2026-10-22T09:30:00Z"


def test_an_extract_is_indexed_once_and_reopened(map_data: MapData) -> None:
    reopened = MapData.open(map_data.extract)

    assert reopened is not None
    assert reopened.date == map_data.date
    assert reopened.roads_around(RIDE).counts() == {"major": 4, "mid": 3, "minor": 4}


def test_an_extract_without_an_index_does_not_open(tmp_path: Path) -> None:
    assert MapData.open(tmp_path / "kenya-latest.osm.pbf") is None


KENYA_EXTRACT = extract_path(EXTRACT_URL)


@pytest.mark.skipif(not MapData.open(KENYA_EXTRACT), reason="needs an indexed Kenya extract (`pov-radar maps update`)")
def test_the_kenya_extract_classes_thika_road_as_a_major_road() -> None:
    # The reference clip has no major road within 3 km, so this covers the major class on real data.
    map_data = MapData.open(KENYA_EXTRACT)
    assert map_data
    roysambu = Ride(duration=1.0, samples=[sample(0.0, -1.2186, 36.8865)])

    assert map_data.roads_around(roysambu).counts()["major"] >= 1
