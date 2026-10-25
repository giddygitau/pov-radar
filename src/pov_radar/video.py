"""Read a Chapter's frame timing through ffprobe, and write Overlays through ffmpeg."""

import json
import shutil
import subprocess
import tempfile
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


_INSTALL = "brew install ffmpeg"


def _require(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise VideoError(f"{name} not found on PATH; install it with `{_INSTALL}`")
    return found
