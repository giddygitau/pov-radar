"""Read a Chapter's frame timing through ffprobe, and write Overlays and the Composite through ffmpeg."""

import json
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from types import TracebackType
from typing import IO, Any

import numpy as np
from numpy.typing import NDArray


class VideoError(Exception):
    """A video can't be probed or an Overlay can't be written; the message says why."""


@dataclass(frozen=True)
class Frames:
    """The video frames of a Ride's Chapters, joined end to end."""

    rate: Fraction
    """Frames per second, exactly, e.g. 30000/1001."""
    count: int
    width: int
    height: int
    """Pixels."""

    @property
    def size(self) -> tuple[int, int]:
        """Width and height in pixels."""
        return self.width, self.height


def read_frames(chapters: list[Path]) -> Frames:
    """The frame rate, total frame count and size of the Chapters' video streams, which must share a rate and size
    for one Overlay to span them."""
    probed = [_probe_frames(path) for path in chapters]
    formats = {(rate, width, height) for rate, _, width, height in probed}
    if len(formats) > 1:
        found = ", ".join(
            f"{path.name} {width}×{height} at {float(rate):.3f} fps"
            for path, (rate, _, width, height) in zip(chapters, probed)
        )
        raise VideoError(f"The Chapters differ in frame rate or size, so one Overlay can't span them: {found}")
    ((rate, width, height),) = formats
    return Frames(rate=rate, count=sum(count for _, count, _, _ in probed), width=width, height=height)


def _probe_frames(path: Path) -> tuple[Fraction, int, int, int]:
    """A Chapter's frame rate, frame count, width and height."""
    result = subprocess.run(
        [
            _require("ffprobe"),
            *("-v", "error", "-select_streams", "v:0", "-of", "json"),
            *("-show_entries", "stream=r_frame_rate,nb_frames,width,height"),
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    streams: list[dict[str, Any]] = json.loads(result.stdout or "{}").get("streams", [])
    if result.returncode != 0 or not streams:
        raise VideoError(f"ffprobe could not read the frames of {path.name}: {result.stderr.strip()}")
    stream = streams[0]
    return Fraction(stream["r_frame_rate"]), int(stream["nb_frames"]), int(stream["width"]), int(stream["height"])


PIXEL_FORMAT = "yuva444p10le"
"""ProRes 4444 with a 10-bit alpha channel."""


class OverlayWriter:
    """Streams straight-alpha RGBA frames to ffmpeg as a ProRes 4444 `.mov` with alpha, at `rate` frames a second.

    The file appears at `path` only once it is complete and its alpha channel is verified.
    """

    def __init__(self, path: Path, width: int, height: int, rate: Fraction) -> None:
        self.path = path
        self._partial = path.with_name(f"{path.stem}.partial{path.suffix}")
        self._size = (height, width, 4)
        self._errors: IO[bytes] = tempfile.TemporaryFile()
        self._ffmpeg = subprocess.Popen(
            [
                _require("ffmpeg"),
                *("-v", "error", "-y"),
                *("-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}", "-framerate", str(rate), "-i", "-"),
                *("-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", PIXEL_FORMAT, "-alpha_bits", "16"),
                *("-vendor", "apl0", "-f", "mov", str(self._partial)),
            ],
            stdin=subprocess.PIPE,
            stderr=self._errors,
        )

    def write(self, frame: NDArray[np.uint8]) -> None:
        if frame.shape != self._size:
            raise ValueError(f"A {frame.shape} frame for a {self._size} Overlay")
        assert self._ffmpeg.stdin
        try:
            self._ffmpeg.stdin.write(np.ascontiguousarray(frame).tobytes())
        except BrokenPipeError:
            self._finish()
            raise VideoError(f"ffmpeg stopped reading frames for {self.path.name}") from None

    def __enter__(self) -> "OverlayWriter":
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, traceback: TracebackType | None
    ) -> None:
        try:
            if kind is None:
                self._finish()
                _verify_alpha(self._partial)
                self._partial.replace(self.path)
            else:
                self._ffmpeg.kill()
                self._ffmpeg.wait()
        finally:
            self._errors.close()
            self._partial.unlink(missing_ok=True)

    def _finish(self) -> None:
        """Close ffmpeg's input and wait for it, raising its complaint if it failed."""
        assert self._ffmpeg.stdin
        try:
            self._ffmpeg.stdin.close()
        except BrokenPipeError:
            pass
        if self._ffmpeg.wait() != 0:
            self._errors.seek(0)
            message = self._errors.read().decode(errors="replace").strip()
            raise VideoError(f"ffmpeg could not write {self.path.name}: {message}")


def _verify_alpha(path: Path) -> None:
    """Check ffprobe sees an alpha channel, so no one imports an opaque black box."""
    result = subprocess.run(
        [
            _require("ffprobe"),
            *("-v", "error", "-select_streams", "v:0", "-show_entries", "stream=pix_fmt", "-of", "csv=p=0"),
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    pixel_format = result.stdout.strip()
    if not pixel_format.startswith("yuva"):
        raise VideoError(f"{path.name} came out as {pixel_format or 'unreadable'}, without an alpha channel")


@dataclass(frozen=True)
class Encoding:
    """How the Composite's video is encoded."""

    encoder: str
    """The ffmpeg video encoder, e.g. `libx265`."""
    preset: str
    """The encoder's trade of speed against size, e.g. `slow`."""
    crf: float
    """The encoder's constant quality: lower is better, and bigger."""
    pixel_format: str
    """e.g. `yuv420p10le`, 10-bit."""
    threads: int | None = None
    """How many threads ffmpeg decodes, filters and encodes on; None for every core."""

    def __str__(self) -> str:
        on = f", on {self.threads} threads" if self.threads else ""
        return f"{self.encoder} -preset {self.preset} -crf {self.crf:g}, {self.pixel_format}{on}"

    def thread_options(self) -> tuple[list[str], list[str]]:
        """The options limiting ffmpeg to `threads`: those for each input's decoder, and the rest. libx265 ignores
        `-threads`, so it is limited to a thread pool that size."""
        if self.threads is None:
            return [], []
        threads = str(self.threads)
        encoder = ["-x265-params", f"pools={threads}"] if self.encoder == "libx265" else ["-threads", threads]
        return ["-threads", threads], ["-filter_complex_threads", threads, *encoder]


@dataclass(frozen=True)
class PlacedOverlay:
    """An Overlay's file, and where its top-left pixel goes on the footage's frame."""

    path: Path
    x: int
    y: int


_HEVC_ENCODERS = {"libx265", "hevc_videotoolbox"}
"""Encoders whose streams are tagged `hvc1`, which QuickTime needs to play them."""

_COLOUR_TAGS = {
    "color_range": "range",
    "color_space": "colorspace",
    "color_transfer": "color_trc",
    "color_primaries": "color_primaries",
}
"""ffprobe's name for each colour tag, and the `setparams` filter's."""


def composite(chapters: list[Path], overlays: list[PlacedOverlay], path: Path, encoding: Encoding) -> Iterator[int]:
    """Burn each Overlay, in order, onto the Chapters joined end to end, into a new video at `path`, tagged with the
    first Chapter's colour tags and with its audio copied unchanged; yielding how many frames are done as it goes.

    The Chapters are only read. The file appears at `path` only once it is complete.
    """
    partial = path.with_name(f"{path.stem}.partial{path.suffix}")
    graph, last = [f"[0:v]format={encoding.pixel_format}[v0]"], "v0"
    for i, overlay in enumerate(overlays, start=1):
        graph.append(f"[{last}][{i}:v]overlay=x={overlay.x}:y={overlay.y}:format=auto[v{i}]")
        last = f"v{i}"
    tags = ":".join(f"{_COLOUR_TAGS[name]}={value}" for name, value in _colour_tags(chapters[0]).items())
    graph.append(f"[{last}]setparams={tags}[out]" if tags else f"[{last}]null[out]")
    decoding, threads = encoding.thread_options()
    with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryFile() as errors:
        joined = Path(temp) / "chapters.txt"
        joined.write_text("".join(f"file '{_quoted(chapter.absolute())}'\n" for chapter in chapters))
        ffmpeg = subprocess.Popen(
            [
                _require("ffmpeg"),
                *("-v", "error", "-y", "-nostdin", "-progress", "pipe:1", "-nostats"),
                *decoding, *("-f", "concat", "-safe", "0", "-i", str(joined)),
                *(arg for overlay in overlays for arg in (*decoding, "-i", str(overlay.path))),
                *threads,
                *("-filter_complex", ";".join(graph), "-map", "[out]", "-map", "0:a?", "-c:a", "copy"),
                *("-c:v", encoding.encoder, "-preset", encoding.preset, "-crf", f"{encoding.crf:g}"),
                *("-pix_fmt", encoding.pixel_format),
                *(("-tag:v", "hvc1") if encoding.encoder in _HEVC_ENCODERS else ()),
                *("-movflags", "+faststart", "-f", "mp4", str(partial)),
            ],
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
        )
        try:
            assert ffmpeg.stdout
            for line in ffmpeg.stdout:
                key, _, value = line.strip().partition("=")
                if key == "frame":
                    yield int(value)
            if (status := ffmpeg.wait()) != 0:
                errors.seek(0)
                message = errors.read().decode(errors="replace").strip()
                if not message:
                    message = f"killed by signal {-status}" if status < 0 else f"exited with status {status}"
                raise VideoError(f"ffmpeg could not write {path.name}: {message}")
            partial.replace(path)
        finally:
            if ffmpeg.poll() is None:
                ffmpeg.kill()
                ffmpeg.wait()
            partial.unlink(missing_ok=True)


def _colour_tags(path: Path) -> dict[str, str]:
    """The colour tags of a video's stream that it has, by ffprobe's names for them."""
    result = subprocess.run(
        [
            _require("ffprobe"),
            *("-v", "error", "-select_streams", "v:0", "-of", "json"),
            *("-show_entries", f"stream={','.join(_COLOUR_TAGS)}"),
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    streams: list[dict[str, str]] = json.loads(result.stdout or "{}").get("streams", [])
    if result.returncode != 0 or not streams:
        raise VideoError(f"ffprobe could not read the colours of {path.name}: {result.stderr.strip()}")
    return {name: value for name, value in streams[0].items() if name in _COLOUR_TAGS and value != "unknown"}


def _quoted(path: Path) -> str:
    """`path` for a single-quoted entry in ffmpeg's concat list."""
    return str(path).replace("'", "'\\''")


_INSTALL = "brew install ffmpeg"


def _require(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise VideoError(f"{name} not found on PATH; install it with `{_INSTALL}`")
    return found
