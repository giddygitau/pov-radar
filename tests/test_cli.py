import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest
import typer
import yaml
from typer.testing import CliRunner

from pov_radar import cli, maps
from pov_radar.config import Config
from pov_radar.cli import RenderRecord, app
from pov_radar.drawing import read_png
from pov_radar.placement import Placement
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


def footage(tmp_path: Path, size: str) -> Path:
    """A real, playable 1 s, 10 fps MP4 of `size` pixels, with no `gpmd` telemetry track."""
    path = tmp_path / "footage" / "GX010001.MP4"
    path.parent.mkdir()
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration=1:size={size}:rate=10", str(path)],
        check=True,
    )
    return path


@pytest.fixture
def plain_mp4(tmp_path: Path) -> Path:
    return footage(tmp_path, "64x64")


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
    assert (tmp_path / "preview.png").exists()


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


def test_inspect_draws_a_preview_of_the_overview_at_4k_proportions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = ride_on_the_fixture_roads(tmp_path, monkeypatch)
    assert runner.invoke(app, ["maps", "update"]).exit_code == 0

    code, output = inspect(source)

    assert code == 0, output
    preview = source.parent / "pov-radar" / "GX0001" / "preview.png"
    assert f"Wrote {preview}" in output
    image = read_png(preview)
    # 28% of 2160 high; a Ride due east is as wide as the Overview may be, 1.8 times its height.
    assert image.shape == (605, 1089, 4)
    road_grey = (image[:, :, 0] > 40) & (np.abs(image[:, :, 0].astype(int) - image[:, :, 2]) < 30)
    assert road_grey.any(), "the fixture's roads are drawn"


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


def probe_video(path: Path, count_frames: bool = False) -> dict[str, str]:
    """The first video stream's codec, profile, pixel format, frame rate, frame count and duration, per ffprobe;
    the frames are counted by decoding them if `count_frames`, otherwise read from the container."""
    frames = "nb_read_frames" if count_frames else "nb_frames"
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0", *(["-count_frames"] if count_frames else []),
            "-show_entries", f"stream=codec_name,profile,pix_fmt,r_frame_rate,{frames},duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    stream["frames"] = stream.pop(frames)
    return stream


def first_frame(path: Path) -> np.ndarray[tuple[int, int, int], np.dtype[np.uint8]]:
    """The video's first frame as (height, width, 4) RGBA, decoded by ffmpeg."""
    size = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path),
        ],
        capture_output=True, text=True, check=True,
    ).stdout.strip().split(",")
    width, height = int(size[0]), int(size[1])
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(height, width, 4)


def assert_is_an_overlay_matching(overlay: Path, source: Path) -> None:
    """ProRes 4444 with a genuine alpha channel, frame for frame as long as the source, transparent at the corner."""
    # Decoding a 4K source to count its frames takes minutes, so its container's count is trusted.
    rendered, original = probe_video(overlay, count_frames=True), probe_video(source)
    assert rendered["codec_name"] == "prores" and "4444" in rendered["profile"]
    assert rendered["pix_fmt"].startswith("yuva"), f"{rendered['pix_fmt']} has no alpha channel"
    assert rendered["r_frame_rate"] == original["r_frame_rate"]
    assert rendered["frames"] == original["frames"]
    assert float(rendered["duration"]) == pytest.approx(float(original["duration"]), abs=0.001)
    assert first_frame(overlay)[0, 0, 3] == 0, "outside the Overlay's rounded edge is transparent"


def rendering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Footage big enough for the Overlays' rounded edges to be more than a pixel, its Ride a Sample at each end
    ~1.1 km apart."""
    source = footage(tmp_path, "640x360")
    ride = chapter_ride(1.0, -0.7, -0.7)
    ride.samples[1].longitude = 36.91

    def read_ride(path: Path) -> Ride:
        return ride

    monkeypatch.setattr(cli, "read_ride", read_ride)
    return source


def test_render_writes_both_overlays_transparent_and_frame_for_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = rendering(tmp_path, monkeypatch)

    result = runner.invoke(app, ["render", str(source)], input="n\n")

    assert result.exit_code == 0, result.output
    folder = source.parent / "pov-radar" / "GX0001"
    for name in ("overview.mov", "minimap.mov"):
        assert f"Wrote {folder / name}" in result.output
        assert f"Rendering {name}" in result.output
        assert_is_an_overlay_matching(folder / name, source)


@pytest.mark.parametrize(("switch", "kept", "left_out"), [("--no-minimap", "overview", "minimap"), ("--no-overview", "minimap", "overview")])
def test_either_overlay_can_be_switched_off_without_affecting_the_other(
    switch: str, kept: str, left_out: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = rendering(tmp_path, monkeypatch)

    result = runner.invoke(app, ["render", str(source), switch], input="n\n")

    assert result.exit_code == 0, result.output
    folder = source.parent / "pov-radar" / "GX0001"
    assert_is_an_overlay_matching(folder / f"{kept}.mov", source)
    assert not (folder / f"{left_out}.mov").exists()


def test_render_with_both_overlays_switched_off_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = rendering(tmp_path, monkeypatch)

    result = runner.invoke(app, ["render", str(source), "--no-overview", "--no-minimap"], input="n\n")

    assert result.exit_code == 1
    assert "nothing to render" in result.output
    assert_wrote_nothing(source)


@pytest.mark.skipif(not REFERENCE_CLIP.exists(), reason="reference clip is not in input/")
def test_the_reference_clip_renders_both_overlays(tmp_path: Path, map_cache: Path) -> None:
    result = runner.invoke(app, ["render", str(REFERENCE_CLIP), "--out", str(tmp_path)], input="n\n")

    assert result.exit_code == 0, result.output
    assert_is_an_overlay_matching(tmp_path / "overview.mov", REFERENCE_CLIP)
    assert_is_an_overlay_matching(tmp_path / "minimap.mov", REFERENCE_CLIP)


def test_render_refuses_chapters_that_differ_in_frame_rate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    first = footage(tmp_path, "640x360")
    second = first.with_name("GX020001.MP4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=25", str(second)],
        check=True,
    )
    rides = {first.name: chapter_ride(1.0, -0.7), second.name: chapter_ride(1.0, -0.7001)}

    def read_ride(path: Path) -> Ride:
        return rides[path.name]

    monkeypatch.setattr(cli, "read_ride", read_ride)

    result = runner.invoke(app, ["render", str(first)], input="n\n")

    assert result.exit_code == 1
    assert "differ in frame rate or size" in result.output
    assert not (first.parent / "pov-radar" / "GX0001" / "overview.mov").exists()


def sidecar(folder: Path) -> RenderRecord:
    return RenderRecord.model_validate_json((folder / "render.json").read_text())


def test_every_render_writes_the_placement_sidecar_and_the_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = rendering(tmp_path, monkeypatch)

    result = runner.invoke(app, ["render", str(source)], input="n\n")

    assert result.exit_code == 0, result.output
    folder = source.parent / "pov-radar" / "GX0001"
    record = sidecar(folder)
    assert (record.frame.width, record.frame.height, record.frame.rate) == (640, 360, "10")
    minimap, overview = record.overlays["minimap"], record.overlays["overview"]
    # The Minimap is 24% of 360 across, 3% of 360 from the edges beside the bottom-right corner.
    assert (minimap.file, minimap.full_frame) == ("minimap.mov", False)
    assert minimap.placement == Placement(corner="bottom-right", margin=11, x=640 - 11 - 86, y=360 - 11 - 86, width=86, height=86)
    # The Overview is 28% of 360 high, its width following the Route's shape, in the top-left corner.
    assert overview.file == "overview.mov"
    assert (overview.placement.corner, overview.placement.x, overview.placement.y) == ("top-left", 11, 11)
    assert overview.placement.height == 101 and 0.7 * 101 <= overview.placement.width <= 1.8 * 101
    assert record.time_offset == 0.0
    assert record.version == "0.1.0"
    assert record.map is None, "the rider declined the download"
    assert record.config == Config().dumped()
    assert "OpenStreetMap contributors" in (folder / "ATTRIBUTION.txt").read_text()
    assert f"Wrote {folder / 'ATTRIBUTION.txt'}" in result.output
    assert f"Wrote {folder / 'render.json'}" in result.output
    assert "/" not in record.model_dump_json(include={"overlays"}), "files named, not paths that might say whose machine it is"


def test_render_logs_the_effective_config_first_and_nothing_else_about_the_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = rendering(tmp_path, monkeypatch)
    monkeypatch.setenv("POV_RADAR_SECRET_TOKEN", "hunter2")

    result = runner.invoke(app, ["render", str(source), "--time-offset", "0.5"], input="n\n")

    assert result.exit_code == 0, result.output
    logged = result.output[: result.output.index("GX0001:")]
    first, *settings = logged.strip().splitlines()
    assert first == "Config: built-in defaults, options"
    expected = Config.model_validate({**Config().dumped(), "telemetry": {"time_offset": 0.5}})
    assert yaml.safe_load("\n".join(settings)) == expected.dumped(), "the settings, and only the settings"
    assert "hunter2" not in result.output and str(map_cache) not in logged


def test_options_override_the_config_file_which_overrides_the_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = rendering(tmp_path, monkeypatch)
    config = tmp_path / "ride.yaml"
    config.write_text("telemetry:\n  time_offset: 0.5\n  max_gap: 0.75\nminimap:\n  corner: top-right\n")

    result = runner.invoke(app, ["render", str(source), "--config", str(config), "--time-offset", "0.25"], input="n\n")

    assert result.exit_code == 0, result.output
    assert result.output.startswith(f"Config: built-in defaults, {config}, options\n")
    record = sidecar(source.parent / "pov-radar" / "GX0001")
    assert record.time_offset == 0.25
    assert record.config["telemetry"]["max_gap"] == 0.75
    assert record.overlays["minimap"].placement.corner == "top-right"


def test_the_time_offset_moves_the_telemetry_later_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = rendering(tmp_path, monkeypatch)

    result = runner.invoke(app, ["inspect", str(source), "--time-offset", "0.25"], input="n\n")

    assert result.exit_code == 0, result.output
    telemetry = json.loads((source.parent / "pov-radar" / "GX0001" / "telemetry.json").read_text())
    assert [s["time"] for s in telemetry["samples"]] == [0.25, 1.25]
    assert telemetry["time_offset"] == 0.25


def test_full_frame_overlays_are_the_footage_size_with_the_map_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = rendering(tmp_path, monkeypatch)

    result = runner.invoke(app, ["render", str(source), "--full-frame", "--no-overview"], input="n\n")

    assert result.exit_code == 0, result.output
    folder = source.parent / "pov-radar" / "GX0001"
    frame = first_frame(folder / "minimap.mov")
    assert frame.shape == (360, 640, 4)
    minimap = sidecar(folder).overlays["minimap"]
    assert minimap.full_frame
    x, y = minimap.placement.x, minimap.placement.y
    assert frame[: y - 1].max() == 0 and frame[:, : x - 1, 3].max() == 0, "transparent away from the map"
    assert frame[y + 43, x + 43, 3] > 200, "the map at its place"


def test_a_bad_config_stops_the_run_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = rendering(tmp_path, monkeypatch)
    config = tmp_path / "bad.yaml"
    config.write_text("minimap:\n  margin: -1\n")

    result = runner.invoke(app, ["render", str(source), "--config", str(config)], input="n\n")

    assert result.exit_code == 1
    assert f"Error: {config}: minimap.margin" in result.output
    assert_wrote_nothing(source)


def test_an_overlay_too_big_for_the_footage_is_an_error_not_a_misplaced_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = rendering(tmp_path, monkeypatch)
    config = tmp_path / "big.yaml"
    config.write_text("minimap:\n  margin: 0.4\n")

    result = runner.invoke(app, ["render", str(source), "--config", str(config), "--no-overview"], input="n\n")

    assert result.exit_code == 1
    assert "Error: A 86×86 Overlay with a 144-pixel margin doesn't fit a 640×360 frame" in result.output
    folder = source.parent / "pov-radar" / "GX0001"
    assert not (folder / "minimap.mov").exists() and not (folder / "render.json").exists()


def test_a_bad_option_is_refused_as_the_config_file_would_refuse_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, map_cache: Path
) -> None:
    source = rendering(tmp_path, monkeypatch)

    result = runner.invoke(app, ["render", str(source), "--heading-window", "0"], input="n\n")

    assert result.exit_code == 1
    assert "Error: options: heading.window: Input should be greater than 0" in result.output
    assert_wrote_nothing(source)
