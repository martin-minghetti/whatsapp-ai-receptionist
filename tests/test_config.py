import os
import pytest
from pathlib import Path
from config.loader import load_config, ConfigError

MINIMAL_CONFIG = """
client:
  name: "Test Client"
  timezone: "America/Argentina/Buenos_Aires"
modules:
  booking: false
  payments: false
  reminders: false
"""

def test_load_minimal_config(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(MINIMAL_CONFIG)
    config = load_config(f)
    assert config["client"]["name"] == "Test Client"
    assert config["modules"]["booking"] is False

def test_env_var_substitution(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "abc123")
    f = tmp_path / "config.yaml"
    f.write_text('token: "${MY_TOKEN}"')
    config = load_config(f)
    assert config["token"] == "abc123"

def test_missing_env_var_raises(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text('token: "${NONEXISTENT_VAR_XYZ}"')
    with pytest.raises(ConfigError, match="NONEXISTENT_VAR_XYZ"):
        load_config(f)

def test_file_not_found_raises():
    with pytest.raises(ConfigError):
        load_config(Path("/nonexistent/config.yaml"))
