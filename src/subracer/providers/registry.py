# subracer.providers.registry
#
# Builds the ordered set of network providers for a run from the user's settings,
# and yields them per media type in the configured fallback order. Only providers
# that are implemented are instantiated; configured-but-unimplemented sources are
# skipped (added in later phases). The local pipeline stages (embedded extract,
# existing dir subs) and the local OSDB are handled outside this registry.

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..config.secrets import Credentials
from ..config.settings import OsdbMode, Settings, Source
from ..config.store import data_dir
from ..osdb.local_osdb import LocalOsdbProvider
from ..osdb.mirror import MirrorManager
from .base import Provider
from .gestdown import GestdownProvider
from .opensubtitles_com import OpenSubtitlesComProvider
from .podnapisi import PodnapisiProvider
from .subsource import SubSourceProvider
from .tvsubtitles import TVsubtitlesProvider
from .yify import YifyProvider

# Maps a config Source to a factory building its Provider. Sources absent here
# are not yet implemented and are silently skipped by build_providers().
ProviderFactory = Callable[[Settings, Credentials], Provider]


# Function Summary:
#    Build the OpenSubtitles.com provider from settings + credentials.
#
#  Input (parameters):
#    settings [Settings]:        the run settings (api_url, user_agent)
#    creds [Credentials]:        OpenSubtitles credentials (may be empty)
#
#  Output:
#    provider [Provider]:  a configured OpenSubtitlesComProvider
#
# Example:
#    _build_opensubtitles_com(Settings(), Credentials())  ->  OpenSubtitlesComProvider(...)
def _build_opensubtitles_com(settings: Settings, creds: Credentials) -> Provider:
  return OpenSubtitlesComProvider(
    api_url=settings.api_url,
    api_key=creds.api_key,
    user_agent=settings.user_agent,
    username=creds.username,
    password=creds.password,
    token=creds.token,
  )


# Function Summary:
#    Resolve the local OSDB paths from settings: the metadata DB and any data
#    DBs (zstd SRT shards). Defaults to <data_dir>/osdb when no path is set.
#
#  Input (parameters):
#    settings [Settings]:  the run settings (osdb_storage_path)
#
#  Output:
#    paths [tuple[Path, list[Path]]]:  (metadata_db_path, data_db_paths)
#
# Example:
#    osdb_paths(Settings())  ->  (PosixPath(".../osdb/subtitles_all.db"), [])
def osdb_paths(settings: Settings) -> tuple[Path, list[Path]]:
  base = Path(settings.osdb_storage_path) if settings.osdb_storage_path else data_dir() / "osdb"
  metadata_db = base / "subtitles_all.db"
  data_dir_path = base / "data"
  data_dbs = sorted(data_dir_path.glob("*.db")) if data_dir_path.exists() else []
  return metadata_db, data_dbs


# Function Summary:
#    Build the local OSDB provider from settings (metadata DB + data shards).
#
#  Input (parameters):
#    settings [Settings]:   the run settings (osdb paths, max_search_results)
#    creds [Credentials]:   unused (local DB needs no credentials)
#
#  Output:
#    provider [Provider]:  a LocalOsdbProvider
#
# Example:
#    _build_local_osdb(Settings(), Credentials())  ->  LocalOsdbProvider(...)
def _build_local_osdb(settings: Settings, creds: Credentials) -> Provider:
  if settings.osdb_mode == OsdbMode.MIRROR:
    mirror = MirrorManager(settings)
    metadata = mirror.metadata_db()
    data_dbs = mirror.available_data_dbs()
    # metadata may be None when the mirror hasn't been downloaded yet; the
    # provider's available() check will return False and it will be skipped.
    return LocalOsdbProvider(
      metadata or (mirror.storage_dir() / "metadata.db"),
      data_dbs,
      max_results=settings.max_search_results,
    )
  metadata_db, data_dbs = osdb_paths(settings)
  return LocalOsdbProvider(metadata_db, data_dbs, max_results=settings.max_search_results)


# Function Summary:
#    Build the SubSource provider.
#
#  Input (parameters):
#    settings [Settings]:   the run settings (user_agent, max_search_results)
#    creds [Credentials]:   unused (no credentials)
#
#  Output:
#    provider [Provider]:  a SubSourceProvider
#
# Example:
#    _build_subsource(Settings(), Credentials())  ->  SubSourceProvider(...)
def _build_subsource(settings: Settings, creds: Credentials) -> Provider:
  return SubSourceProvider(user_agent=settings.user_agent, max_results=settings.max_search_results)


# Function Summary:
#    Build the Gestdown provider (Addic7ed proxy, TV-capable).
#
#  Input (parameters):
#    settings [Settings]:   the run settings (user_agent)
#    creds [Credentials]:   unused
#
#  Output:
#    provider [Provider]:  a GestdownProvider
#
# Example:
#    _build_gestdown(Settings(), Credentials())  ->  GestdownProvider(...)
def _build_gestdown(settings: Settings, creds: Credentials) -> Provider:
  return GestdownProvider(user_agent=settings.user_agent)


# Function Summary:
#    Build the YIFY provider (yts-subs.com scraper, movies only).
#
#  Input (parameters):
#    settings [Settings]:   the run settings (max_search_results)
#    creds [Credentials]:   unused
#
#  Output:
#    provider [Provider]:  a YifyProvider
#
# Example:
#    _build_yify(Settings(), Credentials())  ->  YifyProvider(...)
def _build_yify(settings: Settings, creds: Credentials) -> Provider:
  return YifyProvider(max_results=settings.max_search_results)


# Function Summary:
#    Build the Podnapisi provider (movies + TV).
#
#  Input (parameters):
#    settings [Settings]:   the run settings (user_agent, max_search_results)
#    creds [Credentials]:   unused
#
#  Output:
#    provider [Provider]:  a PodnapisiProvider
#
# Example:
#    _build_podnapisi(Settings(), Credentials())  ->  PodnapisiProvider(...)
def _build_podnapisi(settings: Settings, creds: Credentials) -> Provider:
  return PodnapisiProvider(user_agent=settings.user_agent, max_results=settings.max_search_results)


# Function Summary:
#    Build the TVsubtitles provider (TV only).
#
#  Input (parameters):
#    settings [Settings]:   the run settings (max_search_results)
#    creds [Credentials]:   unused
#
#  Output:
#    provider [Provider]:  a TVsubtitlesProvider
#
# Example:
#    _build_tvsubtitles(Settings(), Credentials())  ->  TVsubtitlesProvider(...)
def _build_tvsubtitles(settings: Settings, creds: Credentials) -> Provider:
  return TVsubtitlesProvider(max_results=settings.max_search_results)


# Registry of implemented providers, keyed by config Source.
PROVIDER_FACTORIES: dict[Source, ProviderFactory] = {
  Source.LOCAL_OSDB: _build_local_osdb,
  Source.OPENSUBTITLES_COM: _build_opensubtitles_com,
  Source.SUBSOURCE: _build_subsource,
  Source.GESTDOWN: _build_gestdown,
  Source.YIFY: _build_yify,
  Source.PODNAPISI: _build_podnapisi,
  Source.TVSUBTITLES: _build_tvsubtitles,
}


# Function Summary:
#    Instantiate the enabled, implemented network providers in the user's
#    configured order, keyed by source.
#
#  Input (parameters):
#    settings [Settings]:    the run settings (source order + enabled set)
#    creds [Credentials]:    credentials for providers that need them
#
#  Output:
#    providers [dict[Source, Provider]]:  built providers (insertion-ordered)
#
# Example:
#    build_providers(Settings(), Credentials())  ->  {Source.OPENSUBTITLES_COM: <provider>}
def build_providers(settings: Settings, creds: Credentials) -> dict[Source, Provider]:
  enabled = set(settings.enabled_sources)
  providers: dict[Source, Provider] = {}
  for src in settings.source_order:
    if src not in enabled or src not in PROVIDER_FACTORIES:
      continue
    if src == Source.LOCAL_OSDB and settings.osdb_mode == OsdbMode.OFF:
      continue
    provider = PROVIDER_FACTORIES[src](settings, creds)
    # Only include the local OSDB when its metadata DB actually exists.
    if src == Source.LOCAL_OSDB and not getattr(provider, "available", lambda: True)():
      continue
    providers[src] = provider
  return providers


# Function Summary:
#    Return the providers applicable to a given media type, in fallback order.
#    Uses the per-media-type source filtering from Settings.sources_for() and the
#    provider's own supports() check.
#
#  Input (parameters):
#    providers [dict[Source, Provider]]:  built providers (from build_providers)
#    settings [Settings]:                 the run settings (for ordering/filtering)
#    media_type [str]:                    "movie", "tv", or "unknown"
#
#  Output:
#    ordered [list[Provider]]:  providers to try for this media type, in order
#
# Example:
#    providers_for(built, Settings(), "tv")  ->  [<opensubtitles_com provider>]
def providers_for(
  providers: dict[Source, Provider],
  settings: Settings,
  media_type: str,
) -> list[Provider]:
  ordered: list[Provider] = []
  for src in settings.sources_for(media_type):
    provider = providers.get(src)
    if provider is not None and provider.supports(media_type):
      ordered.append(provider)
  return ordered
