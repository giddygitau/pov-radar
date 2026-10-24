# Road network comes from a local Geofabrik extract, not an online API

The Road network is read from the Geofabrik `africa/kenya` `.osm.pbf` extract, downloaded once into a shared user cache (`~/.cache/pov-radar/osm/`), clipped locally to each Ride's bounding box, and refreshed only by an explicit `pov-radar maps update`. Overpass and public tile servers are not bulk-download services and would make rendering depend on network availability and rate limits; a pinned local extract keeps processing local-first and reproducible (the extract date is recorded in each run's metadata).

## Consequences

- Rides outside Kenya need a different extract; the region is configuration, not code.
- OSM's ODbL requires "© OpenStreetMap contributors" credit wherever videos are published. Since Overlays carry no text by default, the tool writes `ATTRIBUTION.txt` beside the outputs; burning the credit in is opt-in.
