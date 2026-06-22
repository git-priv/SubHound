# subhound.config.portable
#
# Portable export / import of subhound configuration, like a password manager's
# plaintext export. Because credentials are encrypted with a machine-bound key
# (see config/secrets.py), the encrypted store is NOT portable between machines.
# This module bridges that:
#
#   * export_bundle()  writes settings + credentials to a PLAINTEXT TOML file so
#                      they can be moved to another machine / backed up.
#   * install_bundle() reads such a file on the target machine and re-saves it:
#                      settings -> settings.toml, credentials -> encrypted with
#                      THIS machine's hardware key.
#   * delete_file()    delete the plaintext file afterwards.
#
# The plaintext file contains credentials in the clear; callers (the TUI) must
# strongly recommend deleting it and should offer to do so via delete_file().

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict
from pathlib import Path

import tomli_w

from .secrets import Credentials, save_credentials
from .settings import Settings, settings_from_dict, settings_to_dict
from .store import save_settings

# Marker so import can sanity-check it's our format, and a loud header warning.
BUNDLE_VERSION = 1
_HEADER = (
  "# subhound configuration export\n"
  "# !!! PLAINTEXT - THIS FILE CONTAINS YOUR CREDENTIALS IN THE CLEAR !!!\n"
  "# Import it on the target machine, then DELETE this file (subhound can shred\n"
  "# it for you). It is NOT encrypted and is portable between machines.\n\n"
)


# Function Summary:
#    Export settings and credentials to a plaintext TOML bundle for transfer to
#    another machine or backup. The session JWT token is intentionally excluded
#    (it is short-lived and re-fetched on login).
#
#  Input (parameters):
#    settings [Settings]:           the non-secret settings to export
#    creds [Credentials]:           the credentials to export (token dropped)
#    out_path [Path]:               destination plaintext .toml file
#    include_credentials [bool]:    set False to export settings only
#
#  Output:
#    written [Path]:  the path written (mode 0600)
#
# Example:
#    export_bundle(Settings(), Credentials(api_key="k"), Path("/tmp/subhound.toml"))
#      ->  PosixPath("/tmp/subhound.toml")
def export_bundle(
  settings: Settings,
  creds: Credentials,
  out_path: Path,
  include_credentials: bool = True,
) -> Path:
  payload: dict = {
    "subhound_export_version": BUNDLE_VERSION,
    "settings": settings_to_dict(settings),
  }
  if include_credentials:
    cred_map = {k: v for k, v in asdict(creds).items() if v and k != "token"}
    if cred_map:
      payload["credentials"] = cred_map
  out_path.parent.mkdir(parents=True, exist_ok=True)
  text = _HEADER + tomli_w.dumps(payload)
  tmp = out_path.with_suffix(out_path.suffix + ".tmp")
  tmp.write_text(text, encoding="utf-8")
  os.chmod(tmp, 0o600)  # still sensitive: it's plaintext credentials
  tmp.replace(out_path)
  return out_path


# Function Summary:
#    Parse a plaintext bundle file into (Settings, Credentials) without saving
#    anything. Unknown keys are ignored so older/newer bundles still load.
#
#  Input (parameters):
#    in_path [Path]:  the plaintext .toml bundle to read
#
#  Output:
#    parsed [tuple[Settings, Credentials]]:  the settings and credentials
#
# Example:
#    read_bundle(Path("/tmp/subhound.toml"))  ->  (Settings(...), Credentials(...))
def read_bundle(in_path: Path) -> tuple[Settings, Credentials]:
  with in_path.open("rb") as fh:
    data = tomllib.load(fh)
  settings = settings_from_dict(data.get("settings", {}))
  cred_map = data.get("credentials", {}) or {}
  creds = Credentials(**{
    k: cred_map[k] for k in ("api_key", "username", "password") if k in cred_map
  })
  return settings, creds


# Function Summary:
#    Import a plaintext bundle and install it on THIS machine: write the settings
#    TOML and re-encrypt the credentials with this machine's hardware-derived
#    key. Does NOT delete the source bundle (call secure_delete() for that).
#
#  Input (parameters):
#    in_path [Path]:           the plaintext bundle to import
#    config_directory [Path]:  the subhound config directory to install into
#
#  Output:
#    installed [tuple[Settings, Credentials]]:  what was installed
#
# Example:
#    install_bundle(Path("/tmp/subhound.toml"), Path("~/.config/subhound"))
#      ->  (Settings(...), Credentials(...))
def install_bundle(in_path: Path, config_directory: Path) -> tuple[Settings, Credentials]:
  settings, creds = read_bundle(in_path)
  save_settings(settings, config_directory / "settings.toml")
  save_credentials(creds, config_directory)
  return settings, creds


# Function Summary:
#    Delete the plaintext bundle file. A plain filesystem remove (no shredding);
#    on modern SSD/journaling/copy-on-write filesystems an overwrite would not
#    reliably destroy the bytes anyway.
#
#  Input (parameters):
#    path [Path]:  the file to delete
#
#  Output:
#    deleted [bool]:  True if the file was removed, False if it did not exist or
#                     could not be removed
#
# Example:
#    delete_file(Path("/tmp/subhound.toml"))  ->  True
def delete_file(path: Path) -> bool:
  try:
    path.unlink()
    return True
  except OSError:
    return False
