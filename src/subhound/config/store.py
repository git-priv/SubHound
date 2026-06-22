# subhound.config.store
#
# Load/save the (non-secret) Settings model as TOML in the platform's user
# config directory. Credentials are never written here -- see config/secrets.py.

from __future__ import annotations

import tomllib
from pathlib import Path

import tomli_w
from platformdirs import user_config_dir, user_data_dir

from .settings import Settings, settings_from_dict, settings_to_dict

APP_NAME = "subhound"
CONFIG_FILENAME = "settings.toml"


# Function Summary:
#    Return the path to subhound's config directory, creating it if needed.
#
#  Input (parameters):
#    (none)
#
#  Output:
#    path [Path]:  the per-user config directory for subhound
#
# Example:
#    config_dir()  ->  PosixPath("/home/priv/.config/subhound")
def config_dir() -> Path:
  path = Path(user_config_dir(APP_NAME))
  path.mkdir(parents=True, exist_ok=True)
  return path


# Function Summary:
#    Return subhound's data directory (for the local OSDB, mirror, logs),
#    creating it if needed.
#
#  Input (parameters):
#    (none)
#
#  Output:
#    path [Path]:  the per-user data directory for subhound
#
# Example:
#    data_dir()  ->  PosixPath("/home/priv/.local/share/subhound")
def data_dir() -> Path:
  path = Path(user_data_dir(APP_NAME))
  path.mkdir(parents=True, exist_ok=True)
  return path


# Function Summary:
#    Return the full path to the settings TOML file.
#
#  Input (parameters):
#    (none)
#
#  Output:
#    path [Path]:  path to settings.toml inside the config directory
#
# Example:
#    config_path().name  ->  "settings.toml"
def config_path() -> Path:
  return config_dir() / CONFIG_FILENAME


# Function Summary:
#    Load settings from the TOML file. If the file does not exist yet, return a
#    fresh default Settings instance (first-run behaviour).
#
#  Input (parameters):
#    path [Path | None]:  override path to load from; defaults to config_path()
#
#  Output:
#    settings [Settings]:  the loaded (or default) settings
#
# Example:
#    load_settings()  ->  Settings(languages=["en"], ...)
def load_settings(path: Path | None = None) -> Settings:
  path = path or config_path()
  if not path.exists():
    return Settings()
  with path.open("rb") as fh:
    data = tomllib.load(fh)
  return settings_from_dict(data)


# Function Summary:
#    Persist settings to the TOML file (atomic write via a temp file + replace).
#
#  Input (parameters):
#    settings [Settings]:    the settings to save
#    path [Path | None]:     override path to write to; defaults to config_path()
#
#  Output:
#    written [Path]:  the path that was written
#
# Example:
#    save_settings(Settings())  ->  PosixPath("/home/priv/.config/subhound/settings.toml")
def save_settings(settings: Settings, path: Path | None = None) -> Path:
  path = path or config_path()
  path.parent.mkdir(parents=True, exist_ok=True)
  payload = settings_to_dict(settings)
  tmp = path.with_suffix(path.suffix + ".tmp")
  with tmp.open("wb") as fh:
    tomli_w.dump(payload, fh)
  tmp.replace(path)
  return path
