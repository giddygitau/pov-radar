# Show only recorded GPS: no extrapolation, no road snapping

The Current position is interpolated between valid Samples only across Fix gaps of ≤ 3 s (configurable). Before the first Fix both Overlays are fully transparent; during longer Fix gaps the marker holds at the last valid position, dimmed, and the Minimap freezes; after the last Fix the marker holds at the final position. The Route is drawn from the Track as recorded, never snapped to the nearest road. A plausible-looking invented position is worse than an honest gap, because the whole point of the Overlays is to show where the motorcycle actually was.

## Consequences

- Where OSM has no road (common for rural murram roads in Kenya), the Route is drawn over empty map rather than moved onto a nearby road.
- Map matching may be added later as an option, but the original Track must stay available alongside it.
