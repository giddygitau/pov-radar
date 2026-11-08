# pov-radar

Turns GoPro motorcycle footage into game-style navigation overlays driven by the camera's own GPS telemetry. See `docs/spec.md`.

Requires `exiftool` and `ffprobe` on `PATH`.

```sh
uv run pov-radar inspect input/GX040710.MP4
```

## Themes

The Upcoming route is drawn in the Theme's Accent, with a Completed route grey to suit it: `cyan` (the default), `amber`, `magenta`, `lime` or `violet`.

```sh
uv run pov-radar render <footage> --theme amber
```

or in `config.yaml`:

```yaml
style:
  theme: amber
```

A colour set under `style` itself, such as `completed`, wins over the Theme.
