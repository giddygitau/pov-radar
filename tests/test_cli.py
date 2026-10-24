import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from pov_radar import cli, maps
from pov_radar.cli import app
from pov_radar.ride import Ride, Sample
from pov_radar.roads import MapData

runner = CliRunner()

REFERENCE_CLIP = Path(__file__).parent.parent / "input" / "GX040710.MP4"
ROADS_FIXTURE = Path(__file__).parent / "fixtures" / "roads.osm"


def inspect(source: Path, *args: str, input: str | None = None) -> tuple[int, str]:
    result = runner.invoke(app, ["inspect", str(source), *args], input=input)
    return result.exit_code, result.output


def assert_wrote_nothing(source: Path) -> None:
    assert sorted(p.name for p in source.parent.iterdir()) == [source.name]


@pytest.fixture
def map_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty map cache, with the default extract pointing at the hand-written roads fixture."""
    cache = tmp_path / "cache"
    monkeypatch.setenv("POV_RADAR_CACHE", str(cache))
    monkeypatch.setattr(maps, "EXTRACT_URL", ROADS_FIXTURE.as_uri())
    return cache


@pytest.fixture
def plain_mp4(tmp_path: Path) -> Path:
    """A real, playable MP4 with no `gpmd` telemetry track."""
    path = tmp_path / "footage" / "GX010001.MP4"
    path.parent.mkdir()
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=10", str(path)],
        check=True,
    )
    return path


@pytest.mark.parametrize("missing", ["exiftool", "ffprobe"])
def test_a_missing_tool_is_named(
    missing: str, plain_mp4: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in {"exiftool", "ffprobe"} - {missing}:
        found = shutil.which(tool)
        assert found
        (bin_dir / tool).symlink_to(found)
    monkeypatch.setenv("PATH", str(bin_dir))

    code, output = inspect(plain_mp4)

    assert code == 1
    assert f"{missing} not found" in output
    assert_wrote_nothing(plain_mp4)


def test_a_video_without_a_telemetry_track_is_rejected(plain_mp4: Path) -> None:
    code, output = inspect(plain_mp4)

    assert code == 1
    assert "no GoPro telemetry track" in output
    assert "GPS" in output and "exported copy" in output
    assert_wrote_nothing(plain_mp4)


def test_a_file_that_is_not_a_video_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "GX010001.MP4"
    source.write_text("not a video")

    code, output = inspect(source)

    assert code == 1
    assert "not a readable video" in output
    assert_wrote_nothing(source)


def test_a_ride_with_no_valid_samples_stops_after_its_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "GX010001.MP4"
    source.write_bytes(b"")
    samples = [
        Sample(time=0.0, latitude=-0.7, longitude=36.9, altitude=0.0, speed=0.0, fix=0, precision=99.0),
        Sample(time=0.5, latitude=-0.7, longitude=36.9, altitude=0.0, speed=0.0, fix=0, precision=99.0),
        Sample(time=1.0, latitude=0.0, longitude=0.0, altitude=0.0, speed=0.0, fix=3, precision=99.0),
    ]
    ride = Ride(duration=1.5, samples=samples)

    def read_ride(path: Path) -> Ride:
        return ride

    monkeypatch.setattr(cli, "read_ride", read_ride)

    code, output = inspect(source)

    assert code == 1
    assert "Samples:  3 at 2.0 Hz" in output
    assert "Valid:    0 of 3" in output
    assert "Issues:   no_fix 0.0–0.5 s\n          invalid_coordinates 1.0–1.0 s\n" in output
    assert "no valid Samples" in output
    assert_wrote_nothing(source)


def test_a_folder_without_chapters_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").touch()

    code, output = inspect(tmp_path)

    assert code == 1
    assert "no Chapters found" in output
    assert_wrote_nothing(tmp_path / "notes.txt")


def test_a_source_that_does_not_exist_is_rejected(tmp_path: Path) -> None:
    code, output = inspect(tmp_path / "GX010710.MP4")

    assert code == 1
    assert "GX010710.MP4 does not exist" in output


def chapter_ride(duration: float, *latitudes: float) -> Ride:
    """A one-Chapter Ride with a Sample per second at each latitude."""
    samples = [
        Sample(time=float(t), latitude=lat, longitude=36.9, altitude=1746.0, speed=10.0, fix=3, precision=2.0)
        for t, lat in enumerate(latitudes)
    ]
    return Ride(duration=duration, samples=samples)


def test_a_folder_is_inspected_one_ride_per_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    footage = tmp_path / "footage"
    footage.mkdir()
    rides = {
        "GX010001.MP4": chapter_ride(2.0, -0.1, -0.2),
        "GX020001.MP4": chapter_ride(1.0, -0.3),
        "GX020002.MP4": chapter_ride(3.0, -0.4, -0.5, -0.6),
    }
    for name in rides:
        (footage / name).touch()

    def read_ride(path: Path) -> Ride:
        return rides[path.name]

    monkeypatch.setattr(cli, "read_ride", read_ride)

    code, output = inspect(footage, input="n\n")

    assert code == 0, output
    assert output.count("Download") == 1, "the rider is asked once, not once per Ride"
    first, second = output.split("\n\n")
    assert first.startswith("GX0001: GX010001.MP4, GX020001.MP4\n")
    assert "Duration: 3.0 s" in first and "Samples:  3 at 1.0 Hz" in first
    assert second.startswith("GX0002: GX020002.MP4\nWarning: GX0002 is missing chapter 01\n")
    assert "Duration: 3.0 s" in second
    telemetry = json.loads((footage / "pov-radar" / "GX0001" / "telemetry.json").read_text())
    assert [(s["time"], s["latitude"]) for s in telemetry["samples"]] == [(0.0, -0.1), (1.0, -0.2), (2.0, -0.3)]
    assert (footage / "pov-radar" / "GX0002" / "telemetry.json").exists()


@pytest.mark.skipif(not REFERENCE_CLIP.exists(), reason="reference clip is not in input/")
def test_the_reference_clip_is_summarised_and_its_telemetry_written(tmp_path: Path, map_cache: Path) -> None:
    source = tmp_path / REFERENCE_CLIP.name
    source.symlink_to(REFERENCE_CLIP)

    code, output = inspect(source, input="n\n")

    assert code == 0, output
    assert output.startswith("GX0710: GX040710.MP4\nWarning: GX0710 is missing chapters 01–03\n")
    duration = re.search(r"Duration: +([\d.]+) s", output)
    count = re.search(r"Samples: +(\d+) at ([\d.]+) Hz", output)
    mean = re.search(r"Speed: +[\d.]+–[\d.]+ km/h, mean ([\d.]+) km/h", output)
    assert duration and count and mean, output
    assert float(duration[1]) == pytest.approx(94.9, abs=0.05)
    assert int(count[1]) == pytest.approx(950, abs=20)
    assert float(count[2]) == pytest.approx(10, abs=0.5)
    assert 40 <= float(mean[1]) <= 50
    assert "Valid:    950 of 950" in output
    assert "Issues:   none" in output
    assert "Fix gaps: none" in output

    route = json.loads((tmp_path / "pov-radar" / "GX0710" / "route.geojson").read_text())
    assert route["type"] == "MultiLineString"
    (line,) = route["coordinates"]
    assert len(line) == int(count[1])
    # Kenya, a little south of the equator.
    assert all(33 < lon < 42 and -5 < lat < 0 for lon, lat in line)

    telemetry = json.loads((tmp_path / "pov-radar" / "GX0710" / "telemetry.json").read_text())
    assert len(telemetry["samples"]) == int(count[1])
    assert set(telemetry["samples"][0]) >= {"time", "latitude", "longitude", "fix", "precision", "valid"}
    assert all(s["valid"] for s in telemetry["samples"])
    assert telemetry["issues"] == []


@pytest.mark.skipif(not REFERENCE_CLIP.exists(), reason="reference clip is not in input/")
def test_out_overrides_the_output_folder(tmp_path: Path, map_cache: Path) -> None:
    out = tmp_path / "elsewhere"

    code, output = inspect(REFERENCE_CLIP, "--out", str(out), input="n\n")

    assert code == 0, output
    assert (out / "telemetry.json").exists()


KENYA_EXTRACT = maps.extract_path(maps.EXTRACT_URL)


@pytest.mark.skipif(
    not (REFERENCE_CLIP.exists() and MapData.open(KENYA_EXTRACT)),
    reason="needs the reference clip in input/ and an indexed Kenya extract (`pov-radar maps update`)",
)
def test_the_reference_clip_reports_the_roads_around_it_in_moments(tmp_path: Path) -> None:
    started = time.perf_counter()
    code, output = inspect(REFERENCE_CLIP, "--out", str(tmp_path))
    elapsed = time.perf_counter() - started

    assert code == 0, output
    roads = re.search(r"Roads: +(\d+) major, (\d+) mid, (\d+) minor \(OpenStreetMap, \d{4}-\d{2}-\d{2}\)", output)
    assert roads, output
    # The reference Chapter's ~1.2 km has no motorway, trunk or primary road within 3 km, so major is 0.
    assert int(roads[2]) > 0 and int(roads[3]) > 0
    assert elapsed < 5, f"inspect took {elapsed:.1f} s"


def test_maps_update_downloads_and_indexes_the_extract_and_reports_its_date(map_cache: Path) -> None:
    result = runner.invoke(app, ["maps", "update"])

    assert result.exit_code == 0, result.output
    assert "Downloading" in result.output and "roads.osm" in result.output
    assert "Map data dated 2026-10-22" in result.output
    assert (map_cache / "osm" / "roads.osm").read_bytes() == ROADS_FIXTURE.read_bytes()
    assert (map_cache / "osm" / "roads.osm.roads.npz").exists()


def test_maps_update_that_cannot_download_says_so(map_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(maps, "EXTRACT_URL", (map_cache / "nowhere.osm.pbf").as_uri())

    result = runner.invoke(app, ["maps", "update"])

    assert result.exit_code == 1
    assert "Error: could not download" in result.output
    assert not list(map_cache.glob("osm/nowhere.osm.pbf*")), "nothing half-downloaded is left behind"


def test_a_new_extract_is_indexed_afresh_rather_than_served_from_the_old_index(map_cache: Path) -> None:
    assert runner.invoke(app, ["maps", "update"]).exit_code == 0
    extract = map_cache / "osm" / "roads.osm"
    extract.write_text(ROADS_FIXTURE.read_text().replace('k="highway" v="motorway"', 'k="highway" v="footway"'))

    assert MapData.open(extract) is None, "the index was built from the previous extract"



def ride_on_the_fixture_roads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A one-Chapter Ride along the fixture's roads, read through a stub."""
    source = tmp_path / "footage" / "GX010001.MP4"
    source.parent.mkdir()
    source.touch()
    ride = chapter_ride(61.0, -0.7, -0.7)
    # 1.1 km east a minute later.
    ride.samples[1].longitude = 36.91
    ride.samples[1].time = 60.0

    def read_ride(path: Path) -> Ride:
        return ride

    monkeypatch.setattr(cli, "read_ride", read_ride)
    return source


def test_inspect_reports_the_roads_around_the_ride_and_records_the_map_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = ride_on_the_fixture_roads(tmp_path, monkeypatch)
    assert runner.invoke(app, ["maps", "update"]).exit_code == 0

    code, output = inspect(source)

    assert code == 0, output
    assert "Roads:    4 major, 3 mid, 4 minor (OpenStreetMap, 2026-10-22)" in output
    telemetry = json.loads((source.parent / "pov-radar" / "GX0001" / "telemetry.json").read_text())
    assert telemetry["map"] == {
        "extract": "roads.osm",
        "date": "2026-10-22T09:30:00Z",
        "roads": {"major": 4, "mid": 3, "minor": 4},
    }


def test_a_missing_extract_is_offered_for_download_and_declining_skips_the_roads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = ride_on_the_fixture_roads(tmp_path, monkeypatch)

    code, output = inspect(source, input="n\n")

    assert code == 0, output
    assert "Download it now?" in output
    assert "Roads:    no map data; run `pov-radar maps update`" in output
    telemetry = json.loads((source.parent / "pov-radar" / "GX0001" / "telemetry.json").read_text())
    assert telemetry["map"] is None
    assert not (map_cache / "osm").exists()


def test_with_no_one_to_answer_the_download_offer_the_roads_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = ride_on_the_fixture_roads(tmp_path, monkeypatch)

    def no_answer(*args: object, **kwargs: object) -> bool:
        raise typer.Abort()  # What click raises when stdin ends before an answer, as when run from a script.

    monkeypatch.setattr(typer, "confirm", no_answer)

    code, output = inspect(source)

    assert code == 0, output
    assert "Roads:    no map data; run `pov-radar maps update`" in output
    assert (source.parent / "pov-radar" / "GX0001" / "telemetry.json").exists()


def test_accepting_the_download_fetches_the_extract_and_reports_the_roads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = ride_on_the_fixture_roads(tmp_path, monkeypatch)

    code, output = inspect(source, input="y\n")

    assert code == 0, output
    assert "Roads:    4 major, 3 mid, 4 minor" in output
    assert (map_cache / "osm" / "roads.osm.roads.npz").exists()


def test_a_cached_extract_without_an_index_is_indexed_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = ride_on_the_fixture_roads(tmp_path, monkeypatch)
    (map_cache / "osm").mkdir(parents=True)
    (map_cache / "osm" / "roads.osm").write_bytes(ROADS_FIXTURE.read_bytes())

    code, output = inspect(source)

    assert code == 0, output
    assert "Download" not in output
    assert "Roads:    4 major, 3 mid, 4 minor" in output


def test_max_speed_sets_the_speed_above_which_a_jump_is_implausible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = ride_on_the_fixture_roads(tmp_path, monkeypatch)

    _, by_default = inspect(source, input="n\n")
    code, output = inspect(source, "--max-speed", "50", input="n\n")

    assert "Valid:    2 of 2" in by_default
    assert code == 0, output
    assert "Valid:    1 of 2" in output
    assert "implausible_jump" in output


def test_inspect_reports_fix_gaps_and_writes_the_route_broken_at_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = tmp_path / "GX010001.MP4"
    source.touch()
    # A Sample a second, ~11 m apart, for 4 s of a 6 s Chapter, and no Fix at 2 s.
    ride = chapter_ride(6.0, -0.7, -0.6999, -0.6998, -0.6997)
    ride.samples[2].fix = 0

    def read_ride(path: Path) -> Ride:
        return ride

    monkeypatch.setattr(cli, "read_ride", read_ride)

    code, output = inspect(source, "--max-gap", "1.5", input="n\n")

    assert code == 0, output
    assert "Fix gaps: 1.0–3.0 s\n          3.0–6.0 s\n" in output
    route = json.loads((tmp_path / "pov-radar" / "GX0001" / "route.geojson").read_text())
    assert route == {"type": "MultiLineString", "coordinates": [[[36.9, -0.7], [36.9, -0.6999]]]}
