# Tests for portable export/import of subracer configuration.

from __future__ import annotations

import tempfile
from pathlib import Path

from subracer.config.portable import (
  delete_file,
  export_bundle,
  install_bundle,
  read_bundle,
)
from subracer.config.secrets import Credentials, load_credentials
from subracer.config.settings import Settings, Source
from subracer.config.store import load_settings


def _tmp() -> Path:
  return Path(tempfile.mkdtemp())


def test_export_is_plaintext_and_drops_token():
  out = _tmp() / "subracer_export.toml"
  creds = Credentials(api_key="KEY123", username="priv", password="s3cret", token="JWTSHOULDNOTEXPORT")
  export_bundle(Settings(languages=["nl", "en"]), creds, out)
  text = out.read_text(encoding="utf-8")
  # Plaintext: credentials are readable; the warning header is present.
  assert "PLAINTEXT" in text
  assert "s3cret" in text and "priv" in text and "KEY123" in text
  # The short-lived session token is intentionally not exported.
  assert "JWTSHOULDNOTEXPORT" not in text
  # Sensitive file is locked down.
  assert oct(out.stat().st_mode & 0o777) == "0o600"


def test_install_reencrypts_on_this_machine():
  src = _tmp() / "export.toml"
  export_bundle(
    Settings(languages=["de"], source_order=[Source.OPENSUBTITLES_COM]),
    Credentials(api_key="k", username="u", password="p"),
    src,
  )
  target_cfg = _tmp()
  settings, creds = install_bundle(src, target_cfg)
  # Installed settings landed in settings.toml and load back.
  loaded = load_settings(target_cfg / "settings.toml")
  assert loaded.languages == ["de"]
  # Credentials were re-encrypted with this machine's key and decrypt locally.
  back = load_credentials(target_cfg)
  assert back.can_authenticate() and back.username == "u"
  # The encrypted store must be opaque ciphertext, not the plaintext fields.
  enc = (target_cfg / "secrets.enc").read_bytes()
  assert enc[:6] == b"gAAAAA"
  assert b'"password"' not in enc and b'"username"' not in enc
  assert (settings.languages, creds.username) == (["de"], "u")


def test_read_bundle_only():
  src = _tmp() / "b.toml"
  export_bundle(Settings(max_search_results=42), Credentials(username="x"), src)
  settings, creds = read_bundle(src)
  assert settings.max_search_results == 42 and creds.username == "x"


def test_delete_file_removes_file():
  src = _tmp() / "to_delete.toml"
  export_bundle(Settings(), Credentials(api_key="k"), src)
  assert src.exists()
  assert delete_file(src) is True
  assert not src.exists()
  # Deleting a missing file reports False rather than raising.
  assert delete_file(src) is False
