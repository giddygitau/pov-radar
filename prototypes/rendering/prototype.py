# /// script
# requires-python = ">=3.12"
# dependencies = ["skia-python", "osmium", "pyproj", "numpy"]
# ///
"""Throwaway rendering prototype (issue #15): does skia vector drawing give the look at acceptable speed?

Run from the repo root:

    uv run prototypes/rendering/prototype.py

Reads `input/pov-radar/GX0710/telemetry.json` (from `pov-radar inspect`), the reference Chapter, and the Kenya
OSM extract in `~/.cache/pov-radar/osm/`. Writes PNG frames, a preview MP4 and timings to the git-ignored
`prototypes/rendering/output/`. Nothing here is imported by the package; findings go back to the ticket.
"""

import argparse
import json
import math
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import osmium
import skia
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "output"
TELEMETRY = ROOT / "input/pov-radar/GX0710/telemetry.json"
CHAPTER = ROOT / "input/GX040710.MP4"
OSM = Path.home() / ".cache/pov-radar/osm/kenya-latest.osm.pbf"
FPS = 30000 / 1001

ROAD_CLASSES = {
    **dict.fromkeys(["motorway", "trunk", "primary", "motorway_link", "trunk_link", "primary_link"], "major"),
    **dict.fromkeys(["secondary", "tertiary", "secondary_link", "tertiary_link"], "mid"),
    **dict.fromkeys(["residential", "unclassified", "track", "living_street", "road"], "minor"),
}


# --- Style: every size is a fraction of frame height, so one style works at 1080p, 2.7K and 4K. ---


@dataclass
class Style:
    surface: int = skia.ColorSetARGB(212, 10, 12, 16)
    ring: int = skia.ColorSetARGB(70, 255, 255, 255)
    roads: tuple[tuple[str, int, float], ...] = (
        # class, colour, Minimap width (fraction of frame height); drawn faintest first
        ("minor", skia.ColorSetARGB(255, 84, 89, 98), 0.0018),
        ("mid", skia.ColorSetARGB(255, 118, 123, 132), 0.0026),
        ("major", skia.ColorSetARGB(255, 150, 155, 164), 0.0034),
    )
    upcoming: int = skia.ColorSetARGB(255, 0x00, 0xE5, 0xFF)
    completed: int = skia.ColorSetARGB(255, 128, 128, 128)
    route_width: float = 0.0040
    glow: bool = True
    edge_fade: bool = True
    marker: int = skia.ColorWHITE
    # Minimap
    minimap_diameter: float = 0.24
    ahead_m: float = 250.0
    forward_bias: float = 0.22
    """Marker sits this fraction of the radius below centre, so more road shows ahead than behind."""
    # Overview
    overview_height: float = 0.28
    overview_width_scale: float = 0.55
    """Overview line widths relative to the Minimap's."""
    overview_padding: float = 0.08
    margin: float = 0.03


# --- Track: a minimal stand-in for the future Ride model (linear interpolation, windowed Heading). ---


class Track:
    def __init__(self, telemetry: dict):
        samples = [s for s in telemetry["samples"] if s["fix"] >= 2]  # Samples with a Fix only
        self.duration: float = telemetry["duration"]
        self.t = np.array([s["time"] for s in samples])
        lat = np.array([s["latitude"] for s in samples])
        lon = np.array([s["longitude"] for s in samples])
        self.lat0, self.lon0 = float(lat.mean()), float(lon.mean())
        self.to_metres = Transformer.from_crs(
            "EPSG:4326", f"+proj=tmerc +lat_0={self.lat0} +lon_0={self.lon0} +k=1 +ellps=WGS84 +units=m", always_xy=True
        )
        self.x, self.y = self.to_metres.transform(lon, lat)
        self.lonlat_bounds = (lon.min(), lat.min(), lon.max(), lat.max())

    def position(self, t: float) -> tuple[float, float]:
        t = min(max(t, self.t[0]), self.t[-1])  # hold at the ends, never extrapolate
        return float(np.interp(t, self.t, self.x)), float(np.interp(t, self.t, self.y))

    def split(self, t: float) -> tuple[np.ndarray, np.ndarray]:
        """Completed and Upcoming polylines meeting at the Current position, by time order."""
        i = int(np.searchsorted(self.t, t, side="right"))
        here = np.array([self.position(t)])
        points = np.column_stack([self.x, self.y])
        return np.vstack([points[:i], here]), np.vstack([here, points[i:]])

    def headings(self, times: np.ndarray, window: float = 1.0, min_kmh: float = 8.0) -> np.ndarray:
        """Heading from movement across `window` seconds of travel, held below `min_kmh`; degrees clockwise from north."""
        out = np.empty(len(times))
        held = None
        for n, t in enumerate(times):
            (x0, y0), (x1, y1) = self.position(t - window / 2), self.position(t + window / 2)
            dx, dy = x1 - x0, y1 - y0
            if math.hypot(dx, dy) / window * 3.6 >= min_kmh or held is None:
                held = math.degrees(math.atan2(dx, dy))
            out[n] = held
        return out


# --- Road network from the local OSM extract, clipped to the Ride plus a margin; cached as JSON. ---


def load_roads(track: Track, margin_deg: float = 0.012) -> dict[str, list[np.ndarray]]:
    cache = OUT / "roads.json"
    if not cache.exists():
        w, s, e, n = track.lonlat_bounds
        w, s, e, n = w - margin_deg, s - margin_deg, e + margin_deg, n + margin_deg
        started = time.perf_counter()
        ways: list[dict] = []
        processor = osmium.FileProcessor(str(OSM)).with_locations().with_filter(osmium.filter.KeyFilter("highway"))
        for obj in processor:
            if not obj.is_way() or (road_class := ROAD_CLASSES.get(obj.tags["highway"])) is None:
                continue
            coords = [(node.lon, node.lat) for node in obj.nodes if node.location.valid()]
            if any(w <= lon <= e and s <= lat <= n for lon, lat in coords):
                ways.append({"class": road_class, "coords": coords})
        OUT.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(ways))
        print(f"Extracted {len(ways)} roads from {OSM.name} in {time.perf_counter() - started:.0f} s")
    roads: dict[str, list[np.ndarray]] = {"major": [], "mid": [], "minor": []}
    for way in json.loads(cache.read_text()):
        lon, lat = np.array(way["coords"]).T
        roads[way["class"]].append(np.column_stack(track.to_metres.transform(lon, lat)))
    return roads


def to_path(polylines: list[np.ndarray]) -> skia.Path:
    path = skia.Path()
    for line in polylines:
        path.addPoly([skia.Point(float(x), float(y)) for x, y in line], False)
    return path


def stroke(color: int, width: float, blur: float = 0.0) -> skia.Paint:
    paint = skia.Paint(
        Color=color, AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=width,
        StrokeCap=skia.Paint.kRound_Cap, StrokeJoin=skia.Paint.kRound_Join,
    )
    if blur:
        paint.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur))
    return paint


def draw_route(canvas: skia.Canvas, track: Track, t: float, style: Style, width: float, glow_px: float) -> None:
    """Completed muted underneath, Upcoming in the accent colour on top."""
    completed, upcoming = track.split(t)
    canvas.drawPath(to_path([completed]), stroke(style.completed, width))
    upcoming_path = to_path([upcoming])
    if style.glow and glow_px:
        canvas.drawPath(upcoming_path, stroke(skia.ColorSetA(style.upcoming, 110), width * 2.2, glow_px))
    canvas.drawPath(upcoming_path, stroke(style.upcoming, width))


# --- Minimap: circle, Heading-up, ~250 m ahead of the marker. ---


class Minimap:
    def __init__(self, track: Track, roads: dict[str, list[np.ndarray]], style: Style, frame_height: int):
        self.track, self.style, self.h = track, style, frame_height
        self.size = round(style.minimap_diameter * frame_height)
        self.radius = self.size / 2
        self.bias = style.forward_bias * self.radius
        self.scale = (self.radius + self.bias) / style.ahead_m  # px per metre
        self.road_paths = [(to_path(roads[c]), colour, width) for c, colour, width in style.roads]
        self.fade = skia.Paint(
            Shader=skia.GradientShader.MakeRadial(
                skia.Point(self.radius, self.radius), self.radius,
                [skia.ColorBLACK, skia.ColorBLACK, skia.ColorTRANSPARENT], [0.0, 0.78, 1.0],
            ),
            BlendMode=skia.BlendMode.kDstIn,
        )

    def render(self, canvas: skia.Canvas, t: float, heading: float) -> None:
        s, r = self.style, self.radius
        canvas.save()
        canvas.clipPath(skia.Path().addCircle(r, r, r), doAntiAlias=True)
        canvas.drawColor(s.surface)
        if s.edge_fade:
            canvas.saveLayer()
        canvas.save()
        x, y = self.track.position(t)
        canvas.translate(r, r + self.bias)
        canvas.scale(self.scale, -self.scale)  # metres, north up
        canvas.rotate(heading)  # Heading up
        canvas.translate(-x, -y)
        for path, colour, width in self.road_paths:
            canvas.drawPath(path, stroke(colour, width * self.h / self.scale))
        draw_route(canvas, self.track, t, s, s.route_width * self.h / self.scale, 0.004 * self.h / self.scale)
        canvas.restore()
        if s.edge_fade:
            canvas.drawPaint(self.fade)
            canvas.restore()
        canvas.restore()
        canvas.drawCircle(r, r, r - 0.0012 * self.h, stroke(s.ring, 0.0016 * self.h))
        self._marker(canvas, r, r + self.bias, 0.022 * self.h)

    def _marker(self, canvas: skia.Canvas, cx: float, cy: float, size: float) -> None:
        arrow = skia.Path()
        arrow.moveTo(cx, cy - size)
        arrow.lineTo(cx + 0.72 * size, cy + 0.75 * size)
        arrow.lineTo(cx, cy + 0.38 * size)
        arrow.lineTo(cx - 0.72 * size, cy + 0.75 * size)
        arrow.close()
        canvas.drawPath(arrow, skia.Paint(Color=skia.ColorSetARGB(150, 0, 0, 0), AntiAlias=True,
                                          MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, size * 0.18)))
        canvas.drawPath(arrow, skia.Paint(Color=self.style.marker, AntiAlias=True))


# --- Overview: north-up rounded rectangle fitted to the whole Route; roads drawn once into an image. ---


class Overview:
    def __init__(self, track: Track, roads: dict[str, list[np.ndarray]], style: Style, frame_height: int):
        self.track, self.style, self.h = track, style, frame_height
        x0, x1, y0, y1 = track.x.min(), track.x.max(), track.y.min(), track.y.max()
        pad = style.overview_padding * max(x1 - x0, y1 - y0)
        x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
        aspect = min(max((x1 - x0) / (y1 - y0), 0.7), 1.8)
        self.height = round(style.overview_height * frame_height)
        self.width = round(self.height * aspect)
        self.scale = min(self.width / (x1 - x0), self.height / (y1 - y0))
        self.centre = ((x0 + x1) / 2, (y0 + y1) / 2)
        self.corner = 0.07 * self.height
        self.shape = skia.Path().addRRect(skia.RRect.MakeRectXY(skia.Rect(self.width, self.height), self.corner, self.corner))

        background = skia.Surface(self.width, self.height)
        canvas = background.getCanvas()
        canvas.clear(skia.ColorTRANSPARENT)
        canvas.clipPath(self.shape, doAntiAlias=True)
        canvas.drawColor(style.surface)
        self._to_world(canvas)
        for road_class, colour, width in style.roads:
            canvas.drawPath(to_path(roads[road_class]), stroke(colour, width * style.overview_width_scale * self.h / self.scale))
        self.background = background.makeImageSnapshot()

    def _to_world(self, canvas: skia.Canvas) -> None:
        canvas.translate(self.width / 2, self.height / 2)
        canvas.scale(self.scale, -self.scale)
        canvas.translate(-self.centre[0], -self.centre[1])

    def render(self, canvas: skia.Canvas, t: float) -> None:
        s = self.style
        canvas.drawImage(self.background, 0, 0)
        canvas.save()
        canvas.clipPath(self.shape, doAntiAlias=True)
        self._to_world(canvas)
        k = s.overview_width_scale * self.h / self.scale
        draw_route(canvas, self.track, t, s, s.route_width * k * 1.2, 0.003 * self.h / self.scale)
        x, y = self.track.position(t)
        canvas.drawCircle(x, y, 0.012 * self.h / self.scale, skia.Paint(Color=skia.ColorSetARGB(160, 0, 0, 0), AntiAlias=True))
        canvas.drawCircle(x, y, 0.0075 * self.h / self.scale, skia.Paint(Color=s.marker, AntiAlias=True))
        canvas.restore()
        canvas.drawPath(self.shape, stroke(s.ring, 0.0016 * self.h))


# --- Outputs ---


def snapshot(width: int, height: int, draw) -> skia.Image:
    surface = skia.Surface(width, height)
    surface.getCanvas().clear(skia.ColorTRANSPARENT)
    draw(surface.getCanvas())
    return surface.makeImageSnapshot()


def video_frame(t: float) -> skia.Image:
    png = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(CHAPTER), "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"],
        capture_output=True, check=True,
    ).stdout
    return skia.Image.MakeFromEncoded(skia.Data.MakeWithCopy(png))


def place(canvas: skia.Canvas, frame_w: int, frame_h: int, minimap: Minimap, overview: Overview, t: float, heading: float) -> None:
    m = round(minimap.style.margin * frame_h)
    canvas.save()
    canvas.translate(frame_w - m - minimap.size, frame_h - m - minimap.size)
    minimap.render(canvas, t, heading)
    canvas.restore()
    canvas.save()
    canvas.translate(m, m)
    overview.render(canvas, t)
    canvas.restore()


def benchmark(name: str, width: int, height: int, draw, count: int) -> dict:
    surface = skia.Surface(width, height)
    canvas = surface.getCanvas()
    started = time.perf_counter()
    for n in range(count):
        canvas.clear(skia.ColorTRANSPARENT)
        draw(canvas, n)
    drawn = time.perf_counter() - started
    started = time.perf_counter()
    for n in range(count):
        canvas.clear(skia.ColorTRANSPARENT)
        draw(canvas, n)
        surface.toarray(colorType=skia.kRGBA_8888_ColorType)  # the straight-alpha RGBA bytes the encoder would be fed
    with_readback = time.perf_counter() - started
    result = {"overlay": name, "size": f"{width}x{height}", "frames": count,
              "draw_fps": round(count / drawn, 1), "draw_and_readback_fps": round(count / with_readback, 1)}
    print(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview-start", type=float, default=30.0)
    parser.add_argument("--preview-seconds", type=float, default=30.0)
    parser.add_argument("--stills", type=float, nargs="*", default=[8.0, 31.0, 47.0, 62.0, 88.0])
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    track = Track(json.loads(TELEMETRY.read_text()))
    roads = load_roads(track)
    print({c: len(r) for c, r in roads.items()}, "roads by class")
    style = Style()
    H, W = 2160, 3840
    minimap, overview = Minimap(track, roads, style, H), Overview(track, roads, style, H)

    # Standalone transparent Overlay frames and composites over real 4K frames.
    stills = np.array(args.stills)
    for t, heading in zip(stills, track.headings(stills)):
        snapshot(minimap.size, minimap.size, lambda c: minimap.render(c, t, heading)).save(str(OUT / f"minimap-{t:05.1f}s.png"))
        snapshot(overview.width, overview.height, lambda c: overview.render(c, t)).save(str(OUT / f"overview-{t:05.1f}s.png"))
        background = video_frame(t)

        def composite(canvas: skia.Canvas) -> None:
            canvas.drawImage(background, 0, 0)
            place(canvas, W, H, minimap, overview, t, heading)

        snapshot(W, H, composite).save(str(OUT / f"composite-{t:05.1f}s.png"))
    print(f"Wrote {len(stills)} stills of each kind")

    # Render speed over 300 consecutive frames at 4K proportions.
    times = 20.0 + np.arange(300) / FPS
    headings = track.headings(times)
    timings = [
        benchmark("minimap", minimap.size, minimap.size, lambda c, n: minimap.render(c, times[n], headings[n]), 300),
        benchmark("overview", overview.width, overview.height, lambda c, n: overview.render(c, times[n]), 300),
    ]
    for flag in ("glow", "edge_fade"):
        lean = Minimap(track, roads, Style(**{flag: False}), H)
        timings.append(benchmark(f"minimap without {flag}", lean.size, lean.size,
                                 lambda c, n: lean.render(c, times[n], headings[n]), 300))
    (OUT / "timings.json").write_text(json.dumps(timings, indent=2))

    # Preview MP4: overlays rendered at 1080p proportions, streamed under ffmpeg's overlay of the real footage.
    PW, PH = 1920, 1080
    small_minimap, small_overview = Minimap(track, roads, style, PH), Overview(track, roads, style, PH)
    count = round(args.preview_seconds * FPS)
    times = args.preview_start + np.arange(count) / FPS
    headings = track.headings(times)
    ffmpeg = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{args.preview_start:.3f}", "-t", f"{args.preview_seconds:.3f}", "-i", str(CHAPTER),
         "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{PW}x{PH}", "-framerate", "30000/1001", "-i", "-",
         "-filter_complex", f"[0:v]scale={PW}:{PH}[bg];[bg][1:v]overlay=0:0", "-an",
         "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", str(OUT / "preview.mp4")],
        stdin=subprocess.PIPE,
    )
    surface = skia.Surface(PW, PH)
    started = time.perf_counter()
    for t, heading in zip(times, headings):
        surface.getCanvas().clear(skia.ColorTRANSPARENT)
        place(surface.getCanvas(), PW, PH, small_minimap, small_overview, t, heading)
        ffmpeg.stdin.write(surface.toarray(colorType=skia.kRGBA_8888_ColorType).tobytes())
    ffmpeg.stdin.close()
    ffmpeg.wait()
    print(f"Wrote preview.mp4 ({count} frames in {time.perf_counter() - started:.0f} s, exit {ffmpeg.returncode})")


if __name__ == "__main__":
    main()
