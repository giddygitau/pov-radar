# pov-radar v1 specification

The agreed v1 scope, distilled from the wish-list in [`about.md`](../about.md). Where they disagree, this document wins. Terms are defined in [`GLOSSARY.md`](../GLOSSARY.md); significant decisions are recorded in [`docs/adr/`](./adr/).

## Goal

Turn a GoPro HERO10 Black Ride into two transparent Overlays — a rotating Minimap (bottom-right) and a north-up Overview (top-left) — plus an optional Composite, with no manual animation or editing.

## Non-negotiable principles

1. **Real roads.** The Road network comes from OpenStreetMap ([ADR-0002](./adr/0002-local-osm-extract.md)).
2. **Temporal Route split.** Completed vs Upcoming is decided by the Track's time order at the interpolated Split point, never by geography. Out-and-back rides and loops must render correctly.
3. **No invented GPS.** No extrapolation, no snapping ([ADR-0004](./adr/0004-never-extrapolate-gps.md)).
4. **Video timeline is the master clock.** Use presentation timestamps, not frame-number arithmetic.
5. **Independent Overlays.** Each can be re-rendered, restyled, or omitted without touching the other. Both use the same Current position at every instant.
6. **Source footage is never modified.**

## Input

- **Camera:** GoPro HERO10 Black. Telemetry is GPMF GPS5: ~10–18 Hz position/speed Samples, timestamps only per ~1 s payload (GPSU), fix state (GPSF) and precision (GPSP). No heading.
- **Reference clip characteristics:** 3840×2160 HEVC Main, 8-bit full-range (`yuvj420p`), BT.709, 29.97 fps, ~45 Mbps, AAC audio, `gpmd` data track.
- **Invocation:** a Chapter file (siblings with the same recording number are discovered and ordered), a folder (one Ride per recording number), or an explicit file list.
- **Missing Chapters:** process the ones present in order; warn naming the missing chapter numbers. The Overview covers only the footage present.
- **Scope:** one Recording = one Ride = one Overview. Grouping several Recordings into a day's Ride is out of scope for v1, but the route model must not assume a single file.
- Overlays always span the whole Ride; trimming happens in the editor.

## Telemetry

- Extract with an established tool/library (exiftool is verified to read the reference clip); reconstruct per-Sample time by distributing each payload's Samples across its MP4 sample duration on the Video timeline.
- Validate: invalid/zero coordinates, no-Fix Samples, duplicate or non-monotonic times, Fix gaps, implausible jumps (speed-based), outliers that would distort Overview framing.
- Report affected time ranges; continue with valid portions. Fail clearly if there are no valid Samples.
- Optional explicit time offset (config/CLI), recorded in run metadata.

## Route model

The single source of truth for both Overlays; contains no styling.

- Samples in local projected metres (pyproj, a local transverse Mercator or UTM 37S/37N as appropriate) plus geographic coordinates.
- Cumulative distance; route-wide bounding box (outliers excluded).
- `position_at(t)` → Current position or an explicit "unavailable/held" state.
- `split_at(t)` → Completed and Upcoming polylines meeting exactly at the Split point.
- `heading_at(t)`.
- Every per-frame quantity is a pure function of `t`, so frames can be rendered in any order.

### Fix gap policy

| Situation | Behaviour |
|---|---|
| Before first Fix | Both Overlays fully transparent |
| Gap ≤ 3 s (configurable) | Interpolate |
| Gap > 3 s | Hold marker at last valid position, dimmed; Minimap frozen |
| After last Fix | Hold marker at final position |

### Heading

- Derived from movement. Below ~8 km/h hold the last good Heading; above, smooth over ~1 s of travel. At standstill the Minimap is fully still.
- Position smoothing and Heading smoothing are configured independently. All thresholds are configurable and tuned during the prototype.

## Map data

- Geofabrik `africa/kenya` `.osm.pbf`, in `~/.cache/pov-radar/osm/`. First run offers to download it if missing; `pov-radar maps update` refreshes it. Extract date recorded in run metadata.
- Clip to the Ride's bounding box plus the Minimap's visible radius as margin.
- Road classes: motorway/trunk/primary brightest; secondary/tertiary subdued; residential/unclassified/`track` faintest but visible; footways, cycleways, paths and service roads hidden. The Overview additionally thins minor classes in dense areas.
- No labels in v1.
- Write `ATTRIBUTION.txt` ("© OpenStreetMap contributors") beside outputs and print a reminder; `attribution: burn_in` optionally renders it.

## Rendering

skia-python vector drawing per frame ([ADR-0001](./adr/0001-skia-vector-rendering.md)). Static content (projected roads, full Route) is prepared once; only transforms, Split point and marker change per frame.

### Visual defaults (all configurable)

Sizes are proportional to frame height so one config works at 1080p, 2.7K, and 4K.

| | Minimap | Overview |
|---|---|---|
| Position | bottom-right, 3% margin | top-left, 3% margin |
| Shape | circle | rounded rectangle |
| Size | 24% of frame height diameter | 28% of frame height, aspect fitted to the Route |
| Orientation | Heading-up | north-up, fixed extent for the whole Ride |
| View | ~250 m ahead of marker, slight forward bias | whole valid Route with padding |
| Marker | white arrow | white dot |

Style: Forza Horizon-like. Near-black translucent surface, grey roads, Upcoming route electric cyan `#00E5FF`, Completed route ~50% grey, marker always drawn on top.

## Output

- Default folder: `pov-radar/GX<recording>/` next to the source; override with `--out`. Config file `config.yaml` in the working directory, or `--config`. Precedence: CLI > config file > built-in defaults. Effective config is logged.
- `minimap.mov`, `overview.mov`: ProRes 4444 with alpha (`yuva444p10le`), cropped to the map size ([ADR-0003](./adr/0003-cropped-overlays.md)); `--full-frame` optional. Alpha is verified after encoding. A sidecar records each Overlay's placement. One file per Overlay per Ride, spanning all Chapters; frame rate matches source.
- `final.mp4` (optional Composite): chapters joined, Overlays placed per sidecar, `libx265 -preset slow -crf 16`, 10-bit `yuv420p10le`, BT.709 tags, **source full-range flag preserved**, audio stream-copied. `--fast` uses `-preset medium`. Encoder settings configurable.
- Frames are streamed to the encoder; no full-frame PNG sequences kept unless diagnostics are requested.

## CLI

- `pov-radar inspect <input>` — writes `telemetry.json`, `route.geojson`, `preview.png` (Route over real roads) and prints a summary: duration, sample rate, Fix gaps, speed range, validation issues.
- `pov-radar render <input>` — produces the Overlays and, with `--composite`, `final.mp4`.
- `pov-radar maps update` — refresh the OSM extract.

## Stack

Python 3.12, uv, Typer, pydantic, pyproj, Shapely, skia-python, FFmpeg 7. Target: macOS on Apple Silicon (Linux not blocked, not tested).

## Testing

- Strict TDD (pytest) for the route model, timing reconstruction, interpolation, gap policy, Split point, Heading. Synthetic Tracks cover loops, out-and-back, self-intersection, Fix gaps, late first Fix, slow-speed turns.
- Golden-image snapshot tests (pixel tolerance) for renderers.
- Real footage lives only in git-ignored `input/` and is used for manual acceptance checks. No personal data is committed.

## v1 delivery order

0. Throwaway skia prototype on the reference clip (validate rendering approach, look, render speed).
1. Telemetry extraction, validation, `inspect`.
2. Static Overview over real roads.
3. Animated Overview (route model, interpolation, Split point).
4. Minimap (Heading, rotation, follow).
5. Transparent Overlay export and Composite.

Later: caching beyond telemetry, diagnostics mode, multi-Recording Rides, adaptive zoom, map matching, speed/altitude readouts, themes, parallel rendering.
