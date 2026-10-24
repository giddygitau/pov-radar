# pov-radar

Turns GoPro motorcycle footage into game-style navigation overlays driven by the camera's own GPS telemetry.

## Footage

**Recording**:
Everything the camera captured between one press of record and the next stop, identified by its 4-digit recording number.
_Avoid_: Video, file, clip

**Chapter**:
One of the consecutive files the camera splits a Recording into (`GX01xxxx`, `GX02xxxx`, …).
_Avoid_: Part, segment, file

**Ride**:
The unit the tool processes: the Chapters of one Recording joined end to end into one continuous timeline.
_Avoid_: Trip, journey, session

**Video timeline**:
The presentation time of the Ride's frames; the authoritative clock everything else is aligned to.
_Avoid_: Frame number, wall-clock time

## Telemetry

**Sample**:
One GPS measurement (position, speed, fix state) at a point on the Video timeline.
_Avoid_: Point, reading, record

**Track**:
The ordered sequence of Samples for a Ride, exactly as recorded.
_Avoid_: GPX, trace, path

**Fix**:
The camera's state of having a valid satellite position; Samples without one carry no trustworthy position.
_Avoid_: Lock, signal

**Issue**:
A run of consecutive Samples rejected as invalid for the same reason (e.g. no Fix, impossible coordinates), reported with its time range.
_Avoid_: Error, warning, problem

**Fix gap**:
A stretch of the Video timeline with no valid Samples, including before the first Fix and after the last.
_Avoid_: Dropout, outage, missing data

## Route

**Route**:
The Ride's Track as drawn on the map, in recorded temporal order; a location visited twice appears twice.
_Avoid_: Path, line, trail

**Current position**:
Where the motorcycle is at a given instant on the Video timeline, interpolated between valid Samples but never extrapolated.
_Avoid_: Location, marker position

**Split point**:
The Current position, dividing the Route into the Completed route and the Upcoming route.
_Avoid_: Progress point, cursor

**Completed route**:
The part of the Route from the start of the Ride up to the Split point, drawn muted.
_Avoid_: Past route, travelled route, history

**Upcoming route**:
The part of the Route from the Split point to the end of the Ride, drawn in the accent colour.
_Avoid_: Future route, remaining route, next route

**Heading**:
The motorcycle's direction of travel, derived from movement since the camera does not record one.
_Avoid_: Bearing, course, orientation

**Road network**:
The real roads around the Route, taken from OpenStreetMap, drawn beneath it for context.
_Avoid_: Basemap, tiles, background

## Output

**Overlay**:
A transparent video, aligned to the Ride's Video timeline, carrying exactly one map.
_Avoid_: Layer, HUD, graphic

**Minimap**:
The Overlay that follows the Current position and rotates with Heading, racing-game style.
_Avoid_: Radar, local map, mini-map

**Overview**:
The Overlay showing the whole Route north-up at a fixed extent, with a moving dot at the Current position.
_Avoid_: Route map, full map, progress map

**Composite**:
A new video of the Ride's footage with the Overlays burned on top; the source footage is never modified.
_Avoid_: Final cut, render, export
