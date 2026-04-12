import os
import re
import yaml
from pathlib import Path


class ConfigError(Exception):
    pass


def _substitute_env_vars(value: str) -> str:
    """Replace ${VAR_NAME} with environment variable values."""
    pattern = re.compile(r'\$\{([^}]+)\}')

    def replace(match):
        var_name = match.group(1)
        val = os.environ.get(var_name)
        if val is None:
            raise ConfigError(f"Environment variable '{var_name}' not set (referenced in config.yaml)")
        return val

    return pattern.sub(replace, value)


def _substitute_in_obj(obj):
    """Recursively substitute env vars in all string values."""
    if isinstance(obj, str):
        return _substitute_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _substitute_in_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_in_obj(item) for item in obj]
    return obj


def load_config(path: Path = None) -> dict:
    if path is None:
        path = Path("config.yaml")
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}")

    raw = yaml.safe_load(text)
    return _substitute_in_obj(raw)
