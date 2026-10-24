"""The local OpenStreetMap extract (ADR-0002): where it is cached and how it is fetched; never refreshed unasked."""

import os
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Self
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

EXTRACT_URL = "https://download.geofabrik.de/africa/kenya-latest.osm.pbf"


class DownloadError(Exception):
    """The extract can't be fetched; the message says why."""


def extract_path(url: str) -> Path:
    """Where the extract downloaded from `url` is cached: `~/.cache/pov-radar/osm/`, or under `$POV_RADAR_CACHE`."""
    cache = Path(os.environ.get("POV_RADAR_CACHE") or Path.home() / ".cache" / "pov-radar")
    return cache / "osm" / Path(urlparse(url).path).name


class Download:
    """Fetches an extract into the cache, replacing the cached one only once the download completes."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.path = extract_path(url)
        self.total: int | None = None
        """Bytes to fetch, if the server says."""
        self._partial = self.path.with_name(self.path.name + ".part")
        self._response: Any = None
        self._file: IO[bytes] | None = None

    def __enter__(self) -> Self:
        try:
            self._response = urlopen(self.url)
        except (URLError, OSError) as error:
            raise DownloadError(f"could not download {self.url}: {error}") from error
        length = self._response.headers.get("Content-Length")
        self.total = int(length) if length else None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self._partial.open("wb")
        except OSError:
            self._response.close()
            raise
        return self

    def chunks(self) -> Iterator[int]:
        """Fetch the extract, yielding the size of each chunk written."""
        assert self._file is not None
        while chunk := self._response.read(1 << 20):
            self._file.write(chunk)
            yield len(chunk)

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, traceback: TracebackType | None
    ) -> None:
        if self._file is not None:
            self._file.close()
        self._response.close()
        if kind is None:
            self._partial.replace(self.path)
        else:
            self._partial.unlink(missing_ok=True)
