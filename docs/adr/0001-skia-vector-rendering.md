# Render maps by drawing OSM vectors with skia-python, not MapLibre

Both Overlays are drawn per frame by projecting OpenStreetMap road geometry to local metres and painting it with skia-python under a rotate/translate transform. We rejected MapLibre GL JS in headless Chromium (richest styling, but slow per frame, fragile, and hard to make deterministic) and pre-rendered raster crops (fast, but rotated raster lines blur and degrade further under video compression). Vector drawing in-process is deterministic, parallelisable per frame, and gives full control of the game-HUD look; it was to be confirmed by a throwaway prototype on real footage before committing.

## Consequences

- We own all map styling code; there is no style-spec or tile pipeline to lean on.
- No labels in v1 partly because text placement under rotation is our problem, not a library's.
