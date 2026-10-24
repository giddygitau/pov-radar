from pathlib import Path

import pytest

from pov_radar.recording import DiscoveryError, Recording, discover


def files(folder: Path, *names: str) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).touch()
    return [folder / name for name in names]


def test_one_chapter_finds_and_orders_the_other_chapters_of_its_recording(tmp_path: Path) -> None:
    files(tmp_path, "GX030710.MP4", "GX010710.MP4", "GX020710.MP4", "GX010711.MP4", "notes.txt")

    (recording,) = discover([tmp_path / "GX020710.MP4"])

    assert recording == Recording(
        name="GX0710",
        chapters=[tmp_path / "GX010710.MP4", tmp_path / "GX020710.MP4", tmp_path / "GX030710.MP4"],
        missing=[],
    )


def test_chapter_numbers_absent_before_the_last_one_present_are_missing(tmp_path: Path) -> None:
    files(tmp_path, "GX020710.MP4", "GX040710.MP4", "GX070710.MP4")

    (recording,) = discover([tmp_path / "GX040710.MP4"])

    assert [p.name for p in recording.chapters] == ["GX020710.MP4", "GX040710.MP4", "GX070710.MP4"]
    assert recording.missing == [1, 3, 5, 6]


def test_a_folder_yields_one_recording_per_recording_number_and_per_other_video(tmp_path: Path) -> None:
    files(
        tmp_path,
        "GX030711.MP4",
        "GX010710.MP4",
        "GX010711.MP4",
        "GH010711.MP4",
        "sunset.mp4",
        "GX010712.LRV",
        "GX010712.THM",
    )
    (tmp_path / "pov-radar").mkdir()

    recordings = discover([tmp_path])

    assert [(r.name, [p.name for p in r.chapters]) for r in recordings] == [
        ("GH0711", ["GH010711.MP4"]),
        ("GX0710", ["GX010710.MP4"]),
        ("GX0711", ["GX010711.MP4", "GX030711.MP4"]),
        ("sunset", ["sunset.mp4"]),
    ]
    assert [r.missing for r in recordings] == [[], [], [2], []]


def test_an_explicit_list_is_grouped_by_recording_without_looking_for_other_chapters(tmp_path: Path) -> None:
    given = files(tmp_path, "GX030711.MP4", "GX020710.MP4", "GX010711.MP4", "GX010710.MP4", "GX020711.MP4")

    recordings = discover([given[0], given[1], given[2]])

    assert recordings == [
        Recording(name="GX0710", chapters=[tmp_path / "GX020710.MP4"], missing=[1]),
        Recording(name="GX0711", chapters=[tmp_path / "GX010711.MP4", tmp_path / "GX030711.MP4"], missing=[2]),
    ]


def test_other_names_in_an_explicit_list_are_one_recording_in_the_order_given(tmp_path: Path) -> None:
    given = files(tmp_path, "morning ride.mp4", "GX010710.MP4", "afternoon.mov")

    recordings = discover([given[2], given[1], given[0]])

    assert recordings == [
        Recording(name="GX0710", chapters=[given[1]], missing=[]),
        Recording(name="afternoon", chapters=[given[2], given[0]], missing=[]),
    ]


def test_chapter_names_are_grouped_whatever_their_case(tmp_path: Path) -> None:
    files(tmp_path, "GX010710.MP4", "gx020710.mp4")

    (recording,) = discover([tmp_path])

    assert recording == Recording(
        name="GX0710", chapters=[tmp_path / "GX010710.MP4", tmp_path / "gx020710.mp4"], missing=[]
    )


def test_a_source_that_does_not_exist_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(DiscoveryError, match="GX010710.MP4 does not exist"):
        discover([tmp_path / "GX010710.MP4"])


def test_a_folder_in_a_list_of_sources_is_an_error(tmp_path: Path) -> None:
    (chapter,) = files(tmp_path / "footage", "GX010710.MP4")

    with pytest.raises(DiscoveryError, match="one folder"):
        discover([chapter, tmp_path / "footage"])


def test_a_non_gopro_file_is_a_recording_of_its_own_named_by_its_stem(tmp_path: Path) -> None:
    files(tmp_path, "sunset.mp4", "sunrise.mp4", "GX010710.MP4")

    (recording,) = discover([tmp_path / "sunset.mp4"])

    assert recording == Recording(name="sunset", chapters=[tmp_path / "sunset.mp4"], missing=[])
