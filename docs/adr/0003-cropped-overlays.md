# Overlays are cropped to the map, not full-frame

Each Overlay is encoded at the size of its map (e.g. ~520×520 for a 4K Minimap) rather than as a full-frame transparent canvas, contrary to the original spec's suggestion. Full-frame ProRes 4444 at 4K runs to hundreds of GB per hour of Ride; cropped Overlays are 20–50× smaller and faster to encode. The cost is that they must be positioned: the Composite step places them automatically using margins recorded in a sidecar file, and `--full-frame` remains available for editors where drag-to-place is inconvenient.
