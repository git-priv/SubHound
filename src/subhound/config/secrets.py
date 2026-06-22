# subhound.config.secrets
#
# Storage for sensitive credentials (OpenSubtitles username/password/api_key and
# the cached JWT token). These are NEVER written to the TOML settings file.
#
# Credentials are stored in a single encrypted file (`secrets.enc`, 0600) in the
# config dir. Encryption is authenticated AES (Fernet). The key is derived at
# runtime from stable hardware/OS identifiers (Linux /etc/machine-id, macOS
# IOPlatformUUID, Windows MachineGuid, with a MAC+hostname fallback) -- so:
#   * no key is ever written to disk, and
#   * no key is hard-coded in the source.
# The same machine reconstructs the key transparently with no master password.
# Caveat (accepted by design): anyone who can both read this source AND run code
# on the same machine can reproduce the key; this protects the file at rest /
# against casual inspection and copying to another machine, not against a local
# attacker executing our derivation.

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

FALLBACK_FILENAME = "secrets.enc"
# Fixed application salt mixed into key derivation. Not secret (a salt never is);
# it just domain-separates subhound's key from anything else derived from the
# same machine id.
_APP_SALT = b"subhound/credentials/v1"


@dataclass
class Credentials:
  # OpenSubtitles credentials + cached session token. Any field may be empty.
  api_key: str = ""
  username: str = ""
  password: str = ""
  token: str = ""  # cached JWT, refreshed by the provider

  # Function Summary:
  #    Whether the user has supplied enough to authenticate (api_key plus a
  #    username/password pair). Without these we run OpenSubtitles unauthenticated.
  #
  #  Input (parameters):
  #    self [Credentials]:  the credentials instance
  #
  #  Output:
  #    ok [bool]:  True if api_key and username and password are all set
  #
  # Example:
  #    Credentials(api_key="k", username="u", password="p").can_authenticate()  ->  True
  def can_authenticate(self) -> bool:
    return bool(self.api_key and self.username and self.password)


# Function Summary:
#    Collect a stable per-machine identifier string from OS/hardware sources,
#    preferring identifiers that survive reboots and network changes. Falls back
#    to the MAC address + hostname only when no stable id is available.
#
#  Input (parameters):
#    (none)
#
#  Output:
#    machine_id [str]:  a stable identifier string for this machine
#
# Example:
#    machine_id()  ->  "b9f1c2...e7"  (the Linux /etc/machine-id, for instance)
def machine_id() -> str:
  # Linux: systemd/D-Bus machine id.
  for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
    try:
      value = Path(path).read_text(encoding="utf-8").strip()
      if value:
        return "linux:" + value
    except OSError:
      pass
  # macOS: IOPlatformUUID from ioreg.
  if sys.platform == "darwin":
    try:
      out = subprocess.run(
        ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
        capture_output=True, text=True, timeout=5,
      ).stdout
      m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
      if m:
        return "macos:" + m.group(1)
    except (OSError, subprocess.SubprocessError):
      pass
  # Windows: MachineGuid from the registry.
  if sys.platform.startswith("win"):
    try:
      import winreg  # noqa: PLC0415 - platform-only import
      with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
      ) as key:
        guid, _ = winreg.QueryValueEx(key, "MachineGuid")
        if guid:
          return "windows:" + guid
    except (OSError, ImportError):
      pass
  # Fallback: MAC address + hostname (less stable, but always available).
  return f"fallback:{uuid.getnode():x}:{platform.node()}"


# Function Summary:
#    Derive the Fernet encryption key from the machine id and the app salt. The
#    key is reproducible on this machine and never stored anywhere.
#
#  Input (parameters):
#    (none)
#
#  Output:
#    fernet [Fernet]:  an authenticated-encryption cipher bound to this machine
#
# Example:
#    _cipher().encrypt(b"hi")  ->  b"gAAAAA..."  (machine-specific ciphertext)
def _cipher() -> Fernet:
  digest = hashlib.sha256(_APP_SALT + machine_id().encode("utf-8")).digest()
  key = base64.urlsafe_b64encode(digest)  # 32 bytes -> valid Fernet key
  return Fernet(key)


# Function Summary:
#    Resolve the path of the encrypted secrets file.
#
#  Input (parameters):
#    config_directory [Path]:  the subhound config directory
#
#  Output:
#    path [Path]:  full path to the encrypted secrets file
#
# Example:
#    secrets_path(Path("/cfg")).name  ->  "secrets.enc"
def secrets_path(config_directory: Path) -> Path:
  return config_directory / FALLBACK_FILENAME


# Function Summary:
#    Load and decrypt stored credentials. Returns empty Credentials when nothing
#    is stored or when the file cannot be decrypted on this machine (e.g. it was
#    copied from a different machine, or the hardware id changed).
#
#  Input (parameters):
#    config_directory [Path]:  the subhound config directory
#
#  Output:
#    creds [Credentials]:  the loaded credentials (possibly all-empty)
#
# Example:
#    load_credentials(Path("/cfg")).username  ->  "priv"
def load_credentials(config_directory: Path) -> Credentials:
  path = secrets_path(config_directory)
  if not path.exists():
    return Credentials()
  try:
    plaintext = _cipher().decrypt(path.read_bytes())
    data = json.loads(plaintext.decode("utf-8"))
    return Credentials(**{k: v for k, v in data.items() if k in Credentials.__annotations__})
  except (InvalidToken, ValueError, json.JSONDecodeError, TypeError):
    return Credentials()


# Function Summary:
#    Encrypt and persist credentials to the secrets file with 0600 permissions.
#
#  Input (parameters):
#    creds [Credentials]:      the credentials to store
#    config_directory [Path]:  the subhound config directory
#
#  Output:
#    path [Path]:  the path the encrypted secrets were written to
#
# Example:
#    save_credentials(Credentials(api_key="k"), Path("/cfg"))  ->  PosixPath("/cfg/secrets.enc")
def save_credentials(creds: Credentials, config_directory: Path) -> Path:
  config_directory.mkdir(parents=True, exist_ok=True)
  path = secrets_path(config_directory)
  token = _cipher().encrypt(json.dumps(asdict(creds)).encode("utf-8"))
  # Write atomically, then lock down permissions.
  tmp = path.with_suffix(path.suffix + ".tmp")
  tmp.write_bytes(token)
  os.chmod(tmp, 0o600)
  tmp.replace(path)
  return path
