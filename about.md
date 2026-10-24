# Project Specification: GoPro GPS Motorcycle Video Minimap and Route Overlay Generator

## 1. Project Overview

### 1.1 Summary

Build a local-first video-processing application that extracts GPS telemetry from GoPro motorcycle footage and uses it to generate two synchronized, visually consistent navigation overlays.

The first overlay is a **racing-game-style minimap** positioned in the bottom-right corner of the video. It displays the surrounding road network, follows the motorcycle's current GPS position, and rotates with its direction of travel.

The second overlay is a **full-route overview map** positioned in the top-left corner. It shows the entire route recorded during the ride, maintains a north-up orientation, and uses a moving position marker to show progress along the route.

Both overlays must use actual road-network geometry, preferably sourced from OpenStreetMap or a compatible provider. The route taken by the motorcycle must be highlighted against the surrounding road network. The completed portion of the route should appear muted grey, while the upcoming portion should use a bright, high-contrast accent color.

The application should export the overlays as separate, transparent video files that can be composited over the original GoPro footage in a video editor or through an automated FFmpeg pipeline. The original footage must remain unchanged.

The project is intended for personal use, with a preference for local processing, reproducible output, configurable styling, and a modular architecture that can be extended over time.

### 1.2 Primary objective

Turn raw GoPro footage into motorcycle POV videos with an informative, game-inspired navigation HUD, without requiring manual animation, map drawing, or frame-by-frame editing.

The system should make the video more informative by communicating three things simultaneously:

- The road geometry immediately surrounding the motorcycle.
- The exact route taken during the ride, including the distinction between completed and upcoming sections.
- The motorcycle's current position and progress along the entire journey.

The overlays should be visually legible without competing with the main footage.

### 1.3 Core design principles

**Accuracy over visual approximation.** GPS data must determine the route and marker position. The renderer must not invent a path simply because a nearby road looks plausible.

**Actual roads, not decorative lines.** The map should reflect the real road network. The highlighted route must follow the roads represented by the recorded GPS track rather than being replaced with a simplified, straight-line connection between points.

**Visual consistency.** Both overlays must use the same route colors, position semantics, and underlying GPS timeline. Their different layouts should reflect different purposes, not different interpretations of the ride.

**Independent rendering.** Each overlay must be generated independently so it can be repositioned, resized, restyled, or omitted without regenerating the other.

**Deterministic output.** Given the same source footage, configuration, and map data, the application should produce substantially identical output. This makes debugging, iteration, and batch processing easier.

**Local-first processing.** GPS extraction, route processing, animation, and encoding should run locally. External services may supply map data, but the core pipeline should not require uploading personal motorcycle footage to a cloud service.

---

## 2. Intended Workflow

The expected workflow is:

1. Record a motorcycle ride using a GoPro with GPS enabled.
2. Copy the resulting MP4 file to the local machine.
3. Run the application against the source video.
4. Extract and validate the GPS telemetry.
5. Align GPS timestamps with the video timeline.
6. Obtain the road geometry needed for the ride.
7. Generate the racing minimap and full-route overview.
8. Encode each overlay as a transparent video.
9. Optionally composite both overlays onto the source footage.
10. Import the overlays into a video editor or use the completed video directly.

The normal workflow should require as little manual intervention as possible.

For the initial version, a command-line interface is sufficient. A graphical interface is not necessary because the primary task is a repeatable media-processing workflow rather than an interactive mapping application.

The application should expose useful options through configuration and command-line arguments rather than requiring source-code changes for every recording.

---

## 3. Source Video and GPS Telemetry

### 3.1 Supported input

The primary input is a GoPro MP4 recording containing embedded GPS telemetry.

The implementation should not assume that every MP4 file contains usable GPS data. GPS may be disabled, unavailable during part of a ride, or missing from footage that has been transcoded or processed by another application.

The application should inspect the source before beginning expensive processing and report whether the required telemetry is available.

### 3.2 GPS extraction

GoPro commonly stores telemetry in its GPMF format, which can include GPS coordinates, timestamps, speed, altitude, and other sensor measurements.

Use an established extraction tool or library, such as GoPro's GPMF parser or an appropriate GPX extraction utility, rather than implementing the binary telemetry format from scratch.

This reduces the risk of misinterpreting telemetry records and allows development to focus on mapping, synchronization, and rendering.

The extracted representation should preserve, where available:

- GPS latitude and longitude.
- GPS sample timestamps.
- GPS fix quality or validity information.
- Speed and altitude.
- Relevant timing metadata.
- Gaps or discontinuities in the telemetry.

GPX can be used as an intermediate representation, but it must not be assumed to preserve every detail of the original telemetry. If the extraction tool loses precision, sample timing, or useful metadata, retain a richer internal representation or parse the original GPMF stream directly.

### 3.3 Telemetry validation

Before rendering, validate the GPS track.

The application should identify:

- Missing or invalid coordinates.
- Duplicate timestamps.
- Non-monotonic timestamps.
- Long gaps between consecutive samples.
- Implausible jumps in position.
- Missing telemetry at the beginning or end of the recording.
- Coordinates that fall outside expected geographic bounds.

Invalid data should be handled explicitly. It should not silently create a route line across a large geographic distance or cause the position marker to jump across the map.

Where possible, the application should report the affected time ranges and continue processing valid portions of the recording.

It must not fabricate GPS data to conceal telemetry gaps.

### 3.4 Video timeline and synchronization

The video timeline is the authoritative timeline for overlay rendering.

The application must determine the relationship between the video presentation timestamps and the timestamps of the GPS samples. It must not assume that the first GPS sample occurs at exactly the first video frame.

The synchronization layer should account for:

- Video start time and GPS start time.
- GPS sample intervals.
- Variable-frame-rate video, if supported.
- Telemetry delays or offsets.
- Missing GPS samples.
- Differences between video timestamps and wall-clock time.

The implementation should prefer presentation timestamps over simplistic frame-number calculations when handling source video with variable frame rates.

A configurable time offset may be provided to correct a consistent synchronization error. Such an offset should be explicit and recorded in the output metadata or processing logs.

### 3.5 Position interpolation

GPS samples are generally less frequent than video frames. The renderer must therefore estimate the motorcycle's position at each frame's timestamp.

For ordinary short intervals, interpolation between valid GPS samples is acceptable. Geographic interpolation should be performed using a suitable local projected coordinate system or geodesic method rather than treating latitude and longitude as ordinary Cartesian coordinates over arbitrary distances.

Interpolation must not cross long telemetry gaps or obvious GPS discontinuities. In those cases, the renderer should use an explicit fallback policy, such as temporarily holding the last valid position, hiding the marker, or marking the affected interval as unavailable.

The policy should be configurable where useful, but invalid position estimates must never be presented as reliable GPS measurements.

---

## 4. Map Data and Road Geometry

### 4.1 Actual road network

Both overlays must display a real road network, using OpenStreetMap data or another suitable geographic data source.

The map must not be a generic background with only the recorded route drawn on it. Surrounding roads are important because they provide spatial context and make the minimap useful for understanding corners, junctions, bends, and alternative roads.

The chosen map source must support the intended use, including any offline rendering, caching, and redistribution requirements. The project must respect the source's attribution and licensing requirements.

Public map-tile services must not be treated as unrestricted bulk-download services. If large-scale or repeatable offline rendering is needed, use a provider that permits it or host an appropriate regional dataset locally.

### 4.2 Map geometry and styling

The map renderer should distinguish between road classes without making the map visually busy.

A reasonable visual hierarchy is:

- Major roads: slightly brighter or thicker.
- Secondary roads: moderately subdued.
- Local roads: thin and dark, but still visible.
- Minor paths and irrelevant features: hidden or extremely subdued.

The exact road classifications depend on the available source data.

The objective is not to reproduce a conventional navigation map with labels, business icons, and detailed points of interest. The objective is to create a clean, game-like road map where road shape and route position are immediately understandable.

Labels should be limited to those that provide meaningful context. Excessive text, icons, or unrelated map features would compete with the motorcycle footage.

### 4.3 Geographic coverage

The map data must cover the full recorded route, including the surrounding area required for the moving minimap.

The full-route overview needs coverage for the complete ride. The racing minimap needs sufficient surrounding road geometry for its current zoom level at every point along the route.

The implementation should avoid downloading or rendering an unnecessarily large geographic area. Coverage should be calculated from the GPS track and the configured minimap viewport, with a reasonable margin.

### 4.4 Map projection

The application must transform geographic coordinates into map coordinates consistently.

The route, current position, road network, and viewport must use compatible coordinate transformations. Any mismatch would cause the marker to drift away from the road or the highlighted route to appear offset from the map.

The implementation should use an established mapping library or well-tested projection utilities rather than inventing its own coordinate conversion.

---

## 5. Shared Route Model

The route model is the common source of truth for both renderers.

It should represent the ride as an ordered sequence of timestamped geographic positions, along with derived information needed for rendering.

The route model should provide:

- Timestamped GPS positions.
- Validity information and telemetry gaps.
- Geographic coordinates and projected coordinates.
- Cumulative distance along the recorded route.
- The current position at any video timestamp.
- The completed route segment.
- The upcoming route segment.
- A route-wide geographic bounding box.
- Heading estimates where the telemetry supports them.

The route model should be independent of the visual presentation. It should not contain styling decisions such as marker colors or line thicknesses.

This separation is important because the two overlays need different viewport behavior but must agree on where the motorcycle is and which parts of the route have been completed.

The same model should also support future features such as a speed display, a progress indicator, or an altitude profile without requiring a new GPS-processing pipeline.

---

## 6. Route Highlighting and Progress

### 6.1 Visual semantics

The central visual feature is the distinction between the portion of the route already traveled and the portion that remains ahead.

Use the following default styling:

- **Upcoming route:** a bright, saturated accent color with strong contrast against the dark map.
- **Completed route:** muted grey, visibly less prominent than the upcoming route.
- **Current position:** a high-contrast marker that remains distinguishable from both route segments.
- **Road network:** subdued neutral tones that remain visible beneath the highlighted route.

The completed route should not disappear. Keeping it visible preserves context and helps the viewer understand where the motorcycle came from.

The upcoming route should remain the most visually prominent route segment because it provides immediate directional context.

Colors, opacity, and line widths should be configurable.

### 6.2 Splitting the route at the current position

At every rendered video timestamp, the route must be divided into two logical segments:

1. The completed route, from the beginning of the recorded ride to the current interpolated position.
2. The upcoming route, from the current interpolated position to the end of the recorded ride.

The split must occur at the estimated current position, not simply at the nearest original GPS sample.

This matters because the video may contain many frames between two GPS samples. If the renderer switches colors only at the original samples, the route transition can visibly jump or lag behind the marker.

The two segments should meet at the same geographic position, avoiding a gap, overlap, or color discontinuity.

The route line should preserve the shape of the recorded track. Simplification may be used to reduce rendering cost, but only if it preserves road geometry and does not introduce visible shortcuts across corners or junctions.

### 6.3 Handling overlapping and repeated roads

A motorcycle may travel along the same road multiple times, including out-and-back rides, loops, and repeated passes through junctions.

The application must distinguish the recorded route sequence from the underlying road geometry.

The completed and upcoming portions should be derived from the temporal order of the recorded track, not from a simplistic rule that colors every geographic location behind the motorcycle grey.

This distinction is essential when the ride revisits the same location. A road segment may be completed during one part of the journey and upcoming again during a later pass.

### 6.4 GPS drift and road alignment

Consumer GPS tracks may deviate from the actual road centerline because of measurement error.

The default behavior should prioritize faithful representation of the recorded GPS path rather than silently snapping the motorcycle to the nearest road.

Road snapping can be considered as an optional enhancement if it improves visual quality without changing the apparent route or creating incorrect turns.

If implemented, map matching should account for route continuity, heading, speed, and plausible road connections. A nearest-road-only algorithm is insufficient at junctions, parallel roads, service roads, and divided highways.

The original GPS track should remain available so any map-matched result can be compared with the source data.

---

## 7. Overlay A: Racing-Game-Style Minimap

### 7.1 Purpose

The racing minimap provides immediate context about the road ahead and the motorcycle's current position.

It should resemble a compact navigation HUD found in a driving game, rather than a scaled-down conventional navigation application.

Its purpose is to help the viewer understand the upcoming road geometry without having to infer it from the POV footage alone.

### 7.2 Placement and dimensions

The default position is the bottom-right corner of the final video.

The overlay should have configurable:

- Width and height.
- Distance from the right edge.
- Distance from the bottom edge.
- Internal padding.
- Corner radius.
- Background opacity.
- Road and route line widths.
- Marker size.
- Map zoom level.

The default dimensions should be suitable for a 16:9 video, but the renderer must not hard-code assumptions that make it unusable at other resolutions.

The overlay must maintain its aspect ratio unless an explicit configuration says otherwise.

### 7.3 Following the motorcycle

The map viewport should follow the current interpolated GPS position.

The motorcycle should remain near the center of the viewport, with a configurable positional bias if the design later reserves more room for the road ahead.

A fixed center position is a reasonable starting point. A slight forward bias can be useful because upcoming road geometry is more relevant than the road behind the motorcycle.

The map should move smoothly as the motorcycle travels. GPS noise should not make the entire map visibly jitter from frame to frame.

Any smoothing must balance stability with responsiveness. Excessive smoothing would introduce a noticeable delay between the motorcycle's actual position and the map.

### 7.4 Heading and rotation

The default racing minimap should rotate with the direction of travel so that the motorcycle's forward direction points toward the top of the overlay.

This orientation makes the road geometry easier to interpret while riding through corners and changing direction.

Heading should be estimated from reliable GPS movement or taken from a valid heading measurement if the source telemetry provides one.

Heading estimation must account for GPS noise at low speeds. When the motorcycle is stationary or moving too slowly for a reliable course-over-ground estimate, the renderer should retain a stable previous heading or use another trustworthy heading source.

The map should not rapidly rotate back and forth because of tiny position fluctuations.

Heading smoothing and rotation interpolation should be configurable independently of position smoothing.

### 7.5 Zoom and visible area

The minimap should show enough surrounding road to make upcoming bends and junctions visible while retaining sufficient detail to distinguish nearby roads.

A fixed zoom level is a good initial implementation because it makes behavior predictable and simplifies testing.

Automatic zoom can be added later if it provides a clear benefit. Any dynamic zoom should avoid constant scaling changes caused by small GPS movements.

The map must remain centered on the current position and display the surrounding road network within the available viewport.

### 7.6 Route and marker rendering

The completed route should appear muted grey, and the upcoming route should remain bright.

The current position marker should be rendered above the route lines so it does not disappear beneath them.

A directional arrow is preferred over a simple dot because it communicates the motorcycle's heading.

The marker must be transformed together with the map so its geographic position remains correct as the map rotates.

### 7.7 Visual treatment

The minimap should use a restrained, high-contrast HUD design.

A dark translucent background or dark map surface will help it remain legible over footage with varying brightness.

Rounded corners, subtle borders, and limited shadowing may be used, but decorative effects should not obscure road geometry.

The map must remain readable at the final video resolution, including after typical video compression.

---

## 8. Overlay B: Full-Route Overview

### 8.1 Purpose

The full-route overview provides a persistent view of the entire journey.

Unlike the racing minimap, it should not move or rotate with the motorcycle. Its purpose is to communicate the larger geographic context and show progress through the ride.

This allows viewers to understand where the motorcycle is within the overall journey while the racing minimap focuses on the immediate surroundings.

### 8.2 Placement and dimensions

The default position is the top-left corner of the final video.

The dimensions, margins, padding, corner radius, and background opacity should be configurable independently of the racing minimap.

The overview should be large enough to make the route recognizable, but small enough to avoid competing with the main footage.

### 8.3 Full-route framing

The renderer must calculate the geographic extent of the entire valid route and fit it into the available viewport.

The map should maintain a north-up orientation and should not rotate as the motorcycle turns.

The route should occupy a useful proportion of the viewport without being clipped at the beginning or end.

The renderer should include appropriate margins around the route so that the highlighted line does not touch the edge of the map.

Automatic fitting should account for the map's aspect ratio. If the ride is long and narrow, the renderer must not distort the map to fill a square viewport.

### 8.4 Moving position marker

A current-position marker should move along the route in synchronization with the video.

The marker should be positioned using the same interpolated GPS position used by the racing minimap.

The marker must follow the route's temporal sequence, not merely the closest point in geographic space. This is necessary when a route loops back on itself or crosses its own path.

A simple, high-contrast dot is sufficient for the overview because the map is intended to communicate overall progress rather than local heading.

The marker should remain visible above the route lines, including where the route crosses itself.

### 8.5 Route progress styling

The completed route should be muted grey, and the upcoming route should use the same bright accent color as the racing minimap.

The visual transition must occur at the same interpolated position in both overlays.

Because the overview shows the entire route, it should provide a clear visual summary of progress without relying on text or numerical indicators.

### 8.6 Background map

The overview must use actual road geometry rather than displaying the recorded GPS trace in isolation.

The map should include enough of the surrounding road network to make the route geographically recognizable.

Road density must be controlled so that a ride through a dense urban area does not become an unreadable mass of lines.

The renderer may simplify or suppress less relevant map features at this scale, but it must preserve the main roads and the geographic context needed to interpret the route.

### 8.7 Stable framing

The overview should use a stable map extent throughout the video.

It must not continually zoom or pan as the motorcycle progresses because this would defeat its purpose as an overall journey reference.

If the GPS track contains a small number of invalid outliers, the renderer should not automatically zoom out to include them without validation. An erroneous coordinate could otherwise make the actual ride occupy only a tiny portion of the map.

---

## 9. Rendering Architecture

### 9.1 Separation of concerns

The implementation should separate GPS extraction, route processing, map rendering, overlay encoding, and final video compositing.

Each component should have a clearly defined input and output.

This is important for testing because failures can occur at different stages. For example, a marker that appears in the wrong location may be caused by bad timestamps, incorrect coordinate transformations, map-matching errors, or a rendering bug.

A modular architecture makes it possible to inspect intermediate results and isolate these problems.

### 9.2 Recommended processing pipeline

The pipeline should contain the following stages:

1. **Input inspection:** identify video properties, available telemetry, and timing metadata.
2. **Telemetry extraction:** obtain GPS samples and relevant sensor measurements.
3. **Track validation:** detect missing samples, invalid coordinates, jumps, and timing problems.
4. **Timeline alignment:** establish the relationship between GPS time and video presentation timestamps.
5. **Route preparation:** build the shared route model, calculate cumulative distance, and prepare interpolation data.
6. **Map-data preparation:** acquire or load the necessary road geometry.
7. **Static map preparation:** prepare the road network and route-wide map extent.
8. **Frame evaluation:** determine the current position, heading, and route split at each required timestamp.
9. **Minimap rendering:** render the moving, rotating viewport.
10. **Overview rendering:** render the stable full-route map with the moving marker.
11. **Overlay encoding:** produce transparent video files.
12. **Optional compositing:** place both overlays over the source video and encode the final output.
13. **Validation:** verify synchronization, duration, resolution, transparency, and final output integrity.

The processing stages should be reusable independently. For example, rerunning the renderer after changing the marker color should not require re-extracting the GPS data if the cached telemetry remains valid.

### 9.3 Rendering technology

A Python-based implementation is suitable for the initial version because it provides a strong ecosystem for geographic processing, image rendering, and video orchestration.

A possible stack is:

- `gopro2gpx`, ExifTool, or GoPro's GPMF parser for telemetry extraction.
- `gpxpy` for GPX parsing when GPX is used.
- `pyproj` for coordinate transformations.
- Shapely or equivalent geometry tools for route operations.
- MapLibre GL JS or a comparable renderer for road-map styling.
- FFmpeg for video decoding, encoding, and compositing.

The project should choose one primary map-rendering approach rather than mixing several renderers without a clear reason.

MapLibre GL JS is a viable option if the project needs flexible road styling, vector-map data, and dynamic viewport transformations. However, rendering every frame through a browser-based renderer may introduce unnecessary complexity or performance overhead.

An alternative is to render a static road-map image for each relevant map region and transform it with a dedicated image-processing pipeline. This may be simpler for a fixed-style racing minimap, provided the implementation can preserve adequate map detail and avoid visible artifacts.

The choice should be validated with a small end-to-end prototype before committing to a larger architecture.

### 9.4 Static and dynamic layers

The rendering engine should distinguish between relatively static map content and per-frame content.

For the racing minimap, the road geometry and the complete route are geographically static, but the viewport changes as the motorcycle moves and turns.

For the overview, the map extent and background remain static, while the marker and route-progress styling change over time.

The implementation should avoid unnecessarily rebuilding road geometry or re-fetching map data for every frame.

This separation should improve performance and make rendering more deterministic.

---

## 10. Output Formats and Transparency

### 10.1 Independent overlays

The application must produce two independent overlay files:

- `minimap.mov`
- `overview.mov`

Both should have transparent backgrounds outside their visible map surfaces.

The overlays should match the source video's duration and timing so that they can be placed at the start of the video timeline without manually adjusting synchronization.

Their canvas dimensions should be configurable. For a simple initial implementation, both overlays can use the same dimensions as the source video, with the actual map occupying only its configured corner region.

Using full-frame transparent overlays simplifies placement in video editors and makes the final compositing process predictable.

### 10.2 Encoding

A suitable default is QuickTime MOV with Apple ProRes 4444, which supports an alpha channel and is commonly used for compositing workflows.

The exact pixel format must preserve alpha. The application should verify that the chosen encoder and pixel format actually retain transparency rather than assuming that every MOV file supports it.

A PNG image sequence can be retained as an intermediate or debugging output when needed.

The user should not have to retain every intermediate frame after successful encoding unless explicitly requested.

### 10.3 Final composite

The application should optionally produce a final MP4 containing the original footage with both overlays composited on top.

The original video must remain untouched. The compositing stage should read it as an input and create a separate output file.

The final output should preserve the original video dimensions and frame rate where practical, and retain the source audio unless the user explicitly requests otherwise.

The final video encoder and quality settings should be configurable.

### 10.4 File organization

A suggested output structure is:

    project/
      input/
        ride.mp4
      cache/
        telemetry.json
        route.geojson
        map-data/
      output/
        minimap.mov
        overview.mov
        final.mp4
      config.yaml

The application should not require this exact directory structure, but the default should be predictable and easy to manage.

Temporary files and caches should be distinguishable from final outputs.

---

## 11. Configuration and Command-Line Interface

### 11.1 General requirements

The application should support a command-line interface that accepts a source video, an output directory, and optional configuration.

The objective is to make the workflow reproducible and suitable for batch processing.

A graphical interface is outside the scope of the initial implementation.

### 11.2 Configurable options

Configuration should cover at least:

**Input and processing**

- Source video path.
- Output directory.
- Telemetry extraction method.
- Optional GPS-to-video time offset.
- Handling of missing or invalid GPS samples.
- Intermediate-file retention.
- Output frame rate, where applicable.

**Racing minimap**

- Enabled or disabled.
- Width, height, and screen margins.
- Zoom level.
- Map rotation behavior.
- Position-following behavior.
- Position and heading smoothing.
- Background and border styling.

**Full-route overview**

- Enabled or disabled.
- Width, height, and screen margins.
- Route-wide map extent and padding.
- Map styling and label density.

**Shared styling**

- Upcoming-route color.
- Completed-route color and opacity.
- Road-class colors and widths.
- Current-position marker style.
- Background color and opacity.
- Corner radius and border styling.

**Output**

- Overlay encoding.
- Video codec and quality settings.
- Final composite generation.
- Final output filename.

The application should provide sensible defaults so that a basic invocation does not require an extensive configuration file.

### 11.3 Configuration precedence

A reasonable precedence order is:

1. Explicit command-line arguments.
2. Project configuration file.
3. Built-in defaults.

The effective configuration should be logged at the start of processing. This makes it easier to reproduce output and understand why a particular overlay was generated.

Sensitive information, such as provider API keys, must not be written to logs.

---

## 12. Performance and Resource Management

### 12.1 Practical performance target

The application is intended for personal video production, so it does not need real-time rendering.

However, it should be efficient enough to process typical motorcycle rides without excessive waiting or unnecessary resource consumption.

The implementation should measure actual rendering speed before introducing complex optimization.

### 12.2 Caching

Cache reusable intermediate results, particularly:

- Extracted GPS telemetry.
- Validated route data.
- Prepared geographic geometry.
- Static map data or rendered map assets.
- Shared coordinate transformations.

Caches should be invalidated when their underlying inputs change.

For example, changing only the marker color should not invalidate the GPS extraction cache. Changing the source video or telemetry extraction settings should invalidate the affected downstream stages.

A simple cache key based on source identity, relevant configuration, and tool version is preferable to relying solely on filenames.

### 12.3 Memory use

The renderer should avoid loading every decoded source-video frame into memory.

Frame generation and encoding should be streamed or processed in manageable batches.

Static map assets should be reused where possible, and intermediate images should be released when no longer needed.

### 12.4 Parallelism

Parallel frame rendering may be considered after the basic pipeline works correctly.

However, it must not introduce inconsistent map state, incorrect route transitions, or non-deterministic output.

The renderer should be designed so that the position and route state for a frame can be calculated independently from its timestamp. This provides a path toward parallelism without making it a prerequisite for the initial implementation.

---

## 13. Error Handling and Diagnostics

The application should fail clearly when a required input is missing or unusable.

Examples include:

- The video contains no accessible GPS telemetry.
- GPS timestamps cannot be aligned with the video.
- The route contains insufficient valid positions.
- Required map data cannot be obtained.
- The configured map provider rejects the request.
- The chosen encoder does not support the required alpha channel.
- The output directory is not writable.
- An intermediate stage produces invalid or incomplete data.

Errors should identify the failed stage and explain what the user can do next.

The application should distinguish between fatal errors and recoverable problems. A short GPS dropout may be recoverable, while a video with no usable GPS track should not silently produce an animated minimap based on fabricated positions.

Progress reporting should identify the current processing stage and, where practical, show the percentage completed.

A diagnostic mode should preserve useful intermediate artifacts and additional logs for investigating incorrect route geometry, synchronization offsets, and rendering problems.

---

## 14. Testing and Acceptance Criteria

The project should be validated against representative recordings rather than only synthetic examples.

### 14.1 Telemetry extraction

- A supported GoPro recording produces a valid timestamped GPS track.
- GPS samples retain sufficient precision for the intended map scale.
- Missing or invalid telemetry is detected.
- Extracted telemetry can be cached and reused.

### 14.2 Synchronization

- The marker aligns with the recorded route at the beginning, middle, and end of the video.
- Interpolation works between GPS samples.
- The two overlays show the same current position at a given video timestamp.
- GPS gaps do not produce implausible position jumps.
- The overlay duration and timing match the source video.

### 14.3 Racing minimap

- The map follows the motorcycle's position.
- The map rotates according to the configured heading behavior.
- The motorcycle marker remains in the intended viewport region.
- Road geometry remains correctly aligned with the route.
- Completed and upcoming route colors meet at the current position.
- Heading changes do not cause excessive jitter.

### 14.4 Full-route overview

- The entire valid route fits within the viewport.
- The map remains north-up and stable throughout the recording.
- The marker moves according to the same timestamped position used by the racing minimap.
- The route remains legible at the intended output size.
- Loops, crossings, and repeated road segments are handled correctly.

### 14.5 Output and compositing

- Both overlays have genuine transparency.
- The overlays can be placed over the original video without unwanted opaque backgrounds.
- Both overlays can be repositioned independently.
- The final composite preserves the source audio.
- The original footage remains unchanged.
- Output encoding errors are detected rather than silently ignored.

### 14.6 Visual quality

Visual testing should include:

- Straight roads.
- Tight bends.
- Junctions and roundabouts.
- Roads that run parallel to each other.
- Out-and-back routes.
- Loops and self-intersections.
- Areas with dense road networks.
- Periods of poor GPS reception.
- Changes in heading while moving slowly.

The system should be considered successful when the overlays remain visually stable, geographically plausible, correctly synchronized, and readable across these situations.

---

## 15. Implementation Phases

### Phase 1: Telemetry extraction and route inspection

Build a small command-line utility that extracts GPS samples from one GoPro recording and writes them to an inspectable intermediate file.

Validate the timestamps, coordinates, track duration, and route shape.

**Why:** Every later stage depends on trustworthy GPS data. It is better to establish this foundation before introducing map rendering or animation complexity.

**Completion criterion:** One real recording produces a valid route that can be inspected independently of the video.

### Phase 2: Static full-route map

Load the actual road network, draw the complete GPS route, and generate a static overview image.

Use a single accent color initially if that simplifies the first rendering milestone.

**Why:** This validates the geographic data source, projection, route alignment, map styling, and framing before introducing time-dependent behavior.

**Completion criterion:** The complete route is accurately represented over real roads and fits within the overview viewport.

### Phase 3: Animated full-route overview

Add timestamp-based position interpolation and animate the marker along the route.

Split the route into completed and upcoming segments using the interpolated current position.

**Why:** The overview is the simpler of the two animated maps because its viewport remains fixed and north-up. It provides an effective way to validate synchronization and route-progress logic.

**Completion criterion:** The marker moves smoothly along the route, and the route colors transition correctly throughout the video.

### Phase 4: Racing minimap

Implement the moving viewport, position-following behavior, heading estimation, rotation, and zoom.

Reuse the existing route model and styling configuration.

**Why:** This is the most technically demanding visual component because it combines geographic accuracy, changing viewport transforms, heading stability, and road-context preservation.

**Completion criterion:** The minimap follows the motorcycle, rotates correctly, and shows the upcoming road network without excessive jitter.

### Phase 5: Transparent video export

Render both overlays into transparent video files, preserving the source timeline and configured dimensions.

Add optional FFmpeg compositing to generate a final MP4.

**Why:** This establishes the actual deliverable rather than leaving the project as a map-rendering prototype.

**Completion criterion:** Both overlays can be imported into a video editor or composited automatically without losing transparency or synchronization.

### Phase 6: Configuration and reliability

Add the configuration system, caching, progress reporting, diagnostics, and explicit handling of telemetry and encoding failures.

**Why:** These features reduce repetitive work and make the application usable across many rides rather than only one demonstration recording.

**Completion criterion:** A new supported recording can be processed using the documented workflow, with no manual frame editing.

### Phase 7: Refinement and optional enhancements

Only after the core workflow is reliable should the project consider:

- Adaptive zoom.
- Better heading estimation.
- Optional map matching.
- Speed and altitude displays.
- Route-distance or journey-progress indicators.
- Alternative visual themes.
- Batch processing of multiple recordings.
- Improved render performance.

**Why:** These additions should improve an already-working pipeline rather than complicate the initial implementation before its core requirements have been validated.

---

## 16. Explicit Non-Goals

The initial version does not need to provide:

- Turn-by-turn voice navigation.
- Live navigation during the ride.
- Route planning or destination search.
- Automatic identification of the road the rider intended to take.
- A full-featured interactive map application.
- A video-editing user interface.
- Cloud storage or cloud video processing.
- Social sharing or a public hosting service.
- Automatic correction of all GPS errors.
- A general-purpose telemetry dashboard.

These features may be useful in other contexts, but they are not necessary to achieve the primary objective: generating accurate, synchronized navigation overlays from recorded GoPro footage.

---

## 17. Technical Risks and Design Decisions

### 17.1 GPS quality

Consumer GPS is not perfectly accurate. The recorded track may drift away from the road or fluctuate around the true position.

The system must preserve the distinction between recorded measurements and inferred corrections. Optional map matching should be introduced only when its behavior can be validated against real recordings.

### 17.2 Timestamp alignment

A visually attractive map is still incorrect if its marker is several seconds ahead of or behind the footage.

Timestamp alignment should therefore be treated as a first-class part of the system, not as an incidental implementation detail.

### 17.3 Rendering performance

A browser-based map renderer may provide excellent styling flexibility but can be expensive when invoked for every video frame.

The implementation should measure this cost early and keep the rendering architecture replaceable enough to adopt a more efficient approach if necessary.

### 17.4 Map availability and licensing

The project depends on access to suitable road data. The chosen provider must permit the actual rendering and caching workflow.

Provider limits, attribution requirements, and offline-use restrictions should be considered before selecting a production data source.

### 17.5 Alpha-channel compatibility

Some video formats and pixel formats do not preserve transparency. The export stage must explicitly verify alpha-channel support and produce files compatible with the intended editing workflow.

### 17.6 Geographic correctness

The route and road network may use different coordinate systems or levels of geometric detail. The renderer must maintain consistent transformations and avoid treating a visual approximation as geographic truth.

---

## 18. Definition of Done

The initial project is complete when a supported GoPro MP4 recording can be processed locally into two independently usable, synchronized transparent overlays.

The bottom-right overlay must display a rotating, position-following racing minimap with real surrounding roads and a route split into muted completed and bright upcoming sections.

The top-left overlay must display a stable, north-up overview of the entire ride, with the same route styling and a marker that advances in synchronization with the footage.

Both overlays must preserve their transparency, maintain their intended screen dimensions and timing, and be usable in a video editor or automated compositing pipeline.

The application must provide clear errors for missing or unusable GPS telemetry, avoid silently inventing route information, and allow the most important visual properties to be configured without changing the implementation.

The resulting workflow should make producing a navigation-enhanced motorcycle POV video a repeatable operation rather than a manual editing task.
