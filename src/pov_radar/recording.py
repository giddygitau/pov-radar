"""Find the Chapters of each Recording from the paths the rider gives."""

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple


class DiscoveryError(Exception):
    """The paths given can't be turned into Recordings; the message says why."""


@dataclass
class Recording:
    name: str
    """`<prefix><recording number>` for GoPro names, e.g. `GX0710`; otherwise the first Chapter's stem."""
    chapters: list[Path]
    """In playback order."""
    missing: list[int]
    """Chapter numbers absent between 1 and the last Chapter present."""


class _ChapterName(NamedTuple):
    recording: str
    """`<prefix><recording number>`, prefix upper-cased."""
    chapter: int


_CHAPTER_NAME = re.compile(r"^(G[A-Z])(\d{2})(\d{4})$", re.IGNORECASE)
"""A GoPro Chapter stem: prefix, chapter number, recording number, e.g. `GX` `04` `0710`."""


def discover(sources: list[Path]) -> list[Recording]:
    """Group the given paths into Recordings.

    A folder gives one Recording per Recording number found in it; a single Chapter gives its
    Recording, finding the other Chapters beside it; several Chapters are grouped by Recording,
    with no others looked for. Other names are each a Recording of their own, except in a list,
    where they are joined as one Recording in the order given.
    """
    for source in sources:
        if not source.exists():
            raise DiscoveryError(f"{source} does not exist")
    if len(sources) == 1 and sources[0].is_dir():
        chapters = _chapters_in(sources[0])
        others = [_alone(path) for path in chapters if _name(path) is None]
        return sorted(_by_recording(chapters) + others, key=lambda recording: recording.name)
    if any(source.is_dir() for source in sources):
        raise DiscoveryError("Pass one folder, or one or more Chapters, not both")
    if len(sources) == 1:
        (source,) = sources
        name = _name(source)
        if name is None:
            return [_alone(source)]
        siblings = [path for path in _chapters_in(source.parent) if _recording_of(path) == name.recording]
        return _by_recording(siblings if source in siblings else [*siblings, source])
    others = [source for source in sources if _name(source) is None]
    return _by_recording(sources) + ([Recording(name=others[0].stem, chapters=others, missing=[])] if others else [])


def _chapters_in(folder: Path) -> list[Path]:
    return [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".mp4"]


def _alone(path: Path) -> Recording:
    """A non-GoPro name, as a one-Chapter Recording."""
    return Recording(name=path.stem, chapters=[path], missing=[])


def _name(path: Path) -> _ChapterName | None:
    """Where a GoPro-named Chapter sits in its Recording; None for any other name."""
    if match := _CHAPTER_NAME.match(path.stem):
        prefix, chapter, number = match.groups()
        return _ChapterName(prefix.upper() + number, int(chapter))
    return None


def _recording_of(path: Path) -> str | None:
    name = _name(path)
    return name.recording if name else None


def _by_recording(paths: list[Path]) -> list[Recording]:
    """One Recording per Recording number among the GoPro-named paths, in name order."""
    recordings: dict[str, list[tuple[int, Path]]] = defaultdict(list)
    for path in paths:
        if name := _name(path):
            recordings[name.recording].append((name.chapter, path))
    return [_recording(name, recordings[name]) for name in sorted(recordings)]


def _recording(name: str, chapters: list[tuple[int, Path]]) -> Recording:
    present = {chapter for chapter, _ in chapters}
    missing = [chapter for chapter in range(1, max(present)) if chapter not in present]
    return Recording(name=name, chapters=[path for _, path in sorted(chapters)], missing=missing)
