import yaml

from lintgate.config import load_controlplane_config


def test_load_disposition_enforcement_config(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()

    yaml_content = {
        "controlplane": {
            "enabled": True,
            "disposition_enforcement": {
                "enabled": False,
                "max_ignores_before_blocking": 5,
                "enforce_on_channels": ["behavior", "security"],
            },
        }
    }

    config_file = config_dir / "lintgate.yaml"
    with open(config_file, "w") as f:
        yaml.dump(yaml_content, f)

    cp_config = load_controlplane_config(str(tmp_path))
    assert cp_config is not None
    assert cp_config.disposition_enforcement.enabled is False
    assert cp_config.disposition_enforcement.max_ignores_before_blocking == 5
    assert cp_config.disposition_enforcement.enforce_on_channels == ["behavior", "security"]


def test_load_disposition_enforcement_default(tmp_path):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()

    yaml_content = {"controlplane": {"enabled": True}}

    config_file = config_dir / "lintgate.yaml"
    with open(config_file, "w") as f:
        yaml.dump(yaml_content, f)

    cp_config = load_controlplane_config(str(tmp_path))
    assert cp_config is not None
    assert cp_config.disposition_enforcement.enabled is True
    assert cp_config.disposition_enforcement.max_ignores_before_blocking == 3
    assert cp_config.disposition_enforcement.enforce_on_channels == ["behavior", "lint"]
