import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pov_radar import cli
from pov_radar.cli import app
from pov_radar.ride import Issue, Ride, Sample

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


def test_a_ride_with_no_usable_samples_stops_after_its_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "GX010001.MP4"
    source.write_bytes(b"")
    samples = [
        Sample(time=t, latitude=0.0, longitude=0.0, altitude=0.0, speed=0.0, fix=0, precision=99.0, usable=False)
        for t in (0.0, 0.5, 1.0)
    ]
    issues = [Issue(kind="no_fix", start=0.0, end=0.5), Issue(kind="invalid_coordinates", start=1.0, end=1.0)]
    ride = Ride(duration=1.5, samples=samples, issues=issues)
    def read_ride(path: Path) -> Ride:
        return ride

    monkeypatch.setattr(cli, "read_ride", read_ride)

    code, output = inspect(source)

    assert code == 1
    assert "Samples:  3 at 2.0 Hz" in output
    assert "Usable:   0 of 3" in output
    assert "Issues:   no_fix 0.0–0.5 s\n          invalid_coordinates 1.0–1.0 s\n" in output
    assert "no usable Samples" in output
    assert_wrote_nothing(source)


@pytest.mark.skipif(not REFERENCE_CLIP.exists(), reason="reference clip is not in input/")
def test_the_reference_clip_is_summarised_and_its_telemetry_written(tmp_path: Path) -> None:
    source = tmp_path / REFERENCE_CLIP.name
    source.symlink_to(REFERENCE_CLIP)

    code, output = inspect(source)

    assert code == 0, output
    duration = re.search(r"Duration: +([\d.]+) s", output)
    count = re.search(r"Samples: +(\d+) at ([\d.]+) Hz", output)
    mean = re.search(r"Speed: +[\d.]+–[\d.]+ km/h, mean ([\d.]+) km/h", output)
    assert duration and count and mean, output
    assert float(duration[1]) == pytest.approx(94.9, abs=0.05)
    assert int(count[1]) == pytest.approx(950, abs=20)
    assert float(count[2]) == pytest.approx(10, abs=0.5)
    assert 40 <= float(mean[1]) <= 50
    assert "Usable:   950 of 950" in output
    assert "Issues:   none" in output

    telemetry = json.loads((tmp_path / "pov-radar" / "GX0710" / "telemetry.json").read_text())
    assert len(telemetry["samples"]) == int(count[1])
    assert set(telemetry["samples"][0]) >= {"time", "latitude", "longitude", "fix", "precision", "usable"}
    assert all(s["usable"] for s in telemetry["samples"])
    assert telemetry["issues"] == []


@pytest.mark.skipif(not REFERENCE_CLIP.exists(), reason="reference clip is not in input/")
def test_out_overrides_the_output_folder(tmp_path: Path) -> None:
    out = tmp_path / "elsewhere"

    code, output = inspect(REFERENCE_CLIP, "--out", str(out))

    assert code == 0, output
    assert (out / "telemetry.json").exists()
