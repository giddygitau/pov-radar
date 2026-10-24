import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pov_radar import cli
from pov_radar.cli import app
from pov_radar.ride import Ride, Sample

runner = CliRunner()

REFERENCE_CLIP = Path(__file__).parent.parent / "input" / "GX040710.MP4"


def inspect(source: Path, *args: str) -> tuple[int, str]:
    result = runner.invoke(app, ["inspect", str(source), *args])
    return result.exit_code, result.output


def assert_wrote_nothing(source: Path) -> None:
    assert sorted(p.name for p in source.parent.iterdir()) == [source.name]


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


def test_a_folder_is_inspected_one_ride_per_recording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    code, output = inspect(footage)

    assert code == 0, output
    first, second = output.split("\n\n")
    assert first.startswith("GX0001: GX010001.MP4, GX020001.MP4\n")
    assert "Duration: 3.0 s" in first and "Samples:  3 at 1.0 Hz" in first
    assert second.startswith("GX0002: GX020002.MP4\nWarning: GX0002 is missing chapter 01\n")
    assert "Duration: 3.0 s" in second
    telemetry = json.loads((footage / "pov-radar" / "GX0001" / "telemetry.json").read_text())
    assert [(s["time"], s["latitude"]) for s in telemetry["samples"]] == [(0.0, -0.1), (1.0, -0.2), (2.0, -0.3)]
    assert (footage / "pov-radar" / "GX0002" / "telemetry.json").exists()


@pytest.mark.skipif(not REFERENCE_CLIP.exists(), reason="reference clip is not in input/")
def test_the_reference_clip_is_summarised_and_its_telemetry_written(tmp_path: Path) -> None:
    source = tmp_path / REFERENCE_CLIP.name
    source.symlink_to(REFERENCE_CLIP)

    code, output = inspect(source)

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

    telemetry = json.loads((tmp_path / "pov-radar" / "GX0710" / "telemetry.json").read_text())
    assert len(telemetry["samples"]) == int(count[1])
    assert set(telemetry["samples"][0]) >= {"time", "latitude", "longitude", "fix", "precision", "valid"}
    assert all(s["valid"] for s in telemetry["samples"])
    assert telemetry["issues"] == []


@pytest.mark.skipif(not REFERENCE_CLIP.exists(), reason="reference clip is not in input/")
def test_out_overrides_the_output_folder(tmp_path: Path) -> None:
    out = tmp_path / "elsewhere"

    code, output = inspect(REFERENCE_CLIP, "--out", str(out))

    assert code == 0, output
    assert (out / "telemetry.json").exists()
