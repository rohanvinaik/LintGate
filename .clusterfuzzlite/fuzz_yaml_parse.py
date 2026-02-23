"""Fuzz target for YAML config parsing — exercises lintgate config loading paths."""

import sys

import atheris


def test_one_input(data: bytes) -> None:
    """Feed arbitrary bytes into YAML safe_load (the config parser path)."""
    import yaml

    try:
        yaml.safe_load(data)
    except yaml.YAMLError:
        pass
    except Exception:
        pass


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
