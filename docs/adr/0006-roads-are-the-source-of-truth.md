# The Route follows the roads: Matching replaces "no snapping"

Supersedes the no-snapping half of [ADR-0004](./0004-never-extrapolate-gps.md) and spec principle 3. The Route is now drawn from the Track after Matching: each valid Sample is placed onto the drawn road it plausibly lies on, and a Fix gap is filled along the road when only one road path fits it. Matching is on by default. On the KEFRI Ride the settled GoPro Track sat ~2.6 m off major roads but ~5 m off minor ones, and up to tens of metres off in the minutes after a cold-start Fix. The camera's precision value didn't flag either. A Route drifting beside the road it was obviously on looked wrong where the Road network already showed the truth.

The rest of ADR-0004 stands: nothing is extrapolated, and where no road fits the Route keeps the recorded positions rather than inventing a road.

## Considered Options

- **Matching as an opt-in option.** Rejected: the matched Route is what the rider wants by default.
- **Replacing the Track outright.** Rejected: the recorded Track stays in `telemetry.json` and `route.geojson` alongside what Matching did, so a wrong match can always be seen and turned off.
- **Discarding the first minutes after the first Fix.** Rejected for now: it hides the Overlays longer, and Matching already places most of those Samples onto the road ridden.

## Consequences

- Only the drawn road classes are matched onto. A Sample further than the fallback distance (30 m by default) from any plausible road keeps its recorded position, so murram roads missing from OSM still show where the bike went.
- Matched and recorded stretches look the same on the Overlays; only the data files say which is which.
- Through a filled Fix gap the Current position moves along the road at the gap's average speed. That is interpolation between two recorded Samples, along a road rather than a straight line, not extrapolation.
- The road index has to keep the roads' connections, not just their geometry.
