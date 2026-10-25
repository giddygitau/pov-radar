from pathlib import Path

import pytest

from pov_radar.config import Config, ConfigError, load_config


def written(tmp_path: Path, text: str, name: str = "config.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def test_with_no_file_and_no_options_the_built_in_defaults_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    config = load_config(None)

    assert config == Config()
    assert config.minimap.corner == "bottom-right" and config.overview.corner == "top-left"
    assert config.minimap.margin == config.overview.margin == 0.03
    assert config.style.upcoming == (0x00, 0xE5, 0xFF, 255)
    assert config.attribution == "file" and not config.full_frame
    assert str(config.composite.encoding()) == "libx265 -preset slow -crf 16, yuv420p10le"


def test_the_file_overrides_the_defaults_and_options_override_the_file(tmp_path: Path) -> None:
    path = written(
        tmp_path,
        """
telemetry:
  max_gap: 1.0
  time_offset: 0.25
minimap:
  margin: 0.05
style:
  upcoming: "#FF8800"
""",
    )

    config = load_config(path, {"telemetry": {"time_offset": -0.5}, "full_frame": True})

    assert config.telemetry.max_gap == 1.0, "from the file"
    assert config.telemetry.time_offset == -0.5, "the option wins over the file"
    assert config.full_frame, "from the option"
    assert config.minimap.margin == 0.05 and config.minimap.corner == "bottom-right", "the rest of the section kept"
    assert config.style.upcoming == (0xFF, 0x88, 0x00, 255), "colours as hex, opaque unless given alpha"
    assert config.style.completed == Config().style.completed
    assert config.telemetry.max_speed == Config().telemetry.max_speed


def test_options_left_unset_leave_the_file_alone(tmp_path: Path) -> None:
    path = written(tmp_path, "telemetry:\n  max_gap: 1.0\n")

    config = load_config(path, {"telemetry": {"max_gap": None}})

    assert config.telemetry.max_gap == 1.0


def test_one_road_class_can_be_restyled_alone(tmp_path: Path) -> None:
    path = written(tmp_path, 'style:\n  roads:\n    minor:\n      colour: "#10203040"\n')

    roads = load_config(path).style.roads

    assert roads["minor"].colour == (0x10, 0x20, 0x30, 0x40)
    assert roads["minor"].width == Config().style.roads["minor"].width
    assert roads["major"] == Config().style.roads["major"]


def test_config_yaml_in_the_working_directory_is_read_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written(tmp_path, "attribution: burn_in\n")
    monkeypatch.chdir(tmp_path)

    assert load_config(None).attribution == "burn_in"


def test_an_empty_file_is_the_defaults(tmp_path: Path) -> None:
    assert load_config(written(tmp_path, "")) == Config()


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        ('style:\n  upcoming: "#00E5FG"\n', "style.upcoming"),
        ("style:\n  upcoming: [0, 300, 0, 255]\n", "style.upcoming"),
        ("minimap:\n  margin: -0.01\n", "minimap.margin"),
        ("minimap:\n  corner: middle\n", "minimap.corner"),
        ("style:\n  minimap_diameter: 0\n", "style.minimap_diameter"),
        ("style:\n  overview_aspect: [1.8, 0.7]\n", "narrowest"),
        ("heading:\n  window: fast\n", "heading.window"),
        ("telemetry:\n  max_gapp: 1.0\n", "telemetry.max_gapp"),
        ("attribution: sometimes\n", "attribution"),
        ("composite:\n  crf: 52\n", "composite.crf"),
        ("composite:\n  threads: 0\n", "composite.threads"),
        ("- just\n- a list\n", "a mapping"),
        ("minimap: [unclosed\n", "not valid YAML"),
    ],
)
def test_a_bad_value_is_an_error_naming_the_file_and_the_setting(tmp_path: Path, text: str, complaint: str) -> None:
    path = written(tmp_path, text)

    with pytest.raises(ConfigError) as raised:
        load_config(path)

    assert str(path) in str(raised.value)
    assert complaint in str(raised.value)


def test_a_bad_option_is_an_error_naming_the_option() -> None:
    with pytest.raises(ConfigError, match="telemetry.max_speed"):
        load_config(None, {"telemetry": {"max_speed": -1.0}})


def test_a_named_config_file_that_is_missing_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no such file"):
        load_config(tmp_path / "missing.yaml")
