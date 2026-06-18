# Tests for the local OpenSubtitles DB layer: builder schema/ingest/language
# split, index title + hash search, and the local_osdb provider download path
# (zstd data DB). All synthetic -- no real milahu data needed.

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import zstandard

from subracer.core.identify import MediaInfo
from subracer.osdb.builder import create_index, ingest, language_split
from subracer.osdb.index import METADATA_TABLE, LocalOsdbIndex
from subracer.osdb.local_osdb import LocalOsdbProvider


def _tmp() -> Path:
  return Path(tempfile.mkdtemp())


def _records():
  return [
    {"IDSubtitle": 1, "MovieName": "Inception", "_MovieNameClean": "inception",
     "MovieYear": 2010, "ISO639": "en", "SubAddDate": "2020-01-01",
     "ImdbID": "1375666", "MovieReleaseName": "Inception.2010.1080p", "MovieKind": "movie"},
    {"IDSubtitle": 2, "MovieName": "Inception", "MovieYear": 2010, "ISO639": "nl",
     "SubAddDate": "2020-02-01", "MovieReleaseName": "Inception.2010.NL", "MovieKind": "movie"},
    {"IDSubtitle": 3, "MovieName": "The Show", "MovieYear": None, "ISO639": "en",
     "SubAddDate": "2021-01-01", "SeriesSeason": 2, "SeriesEpisode": 4,
     "MovieReleaseName": "The.Show.S02E04", "MovieKind": "episode"},
    {"IDSubtitle": 4, "MovieName": "The Show", "ISO639": "en",
     "SeriesSeason": 2, "SeriesEpisode": 5, "MovieReleaseName": "The.Show.S02E05",
     "MovieKind": "episode"},
    {"IDSubtitle": 5, "MovieName": "Inception", "MovieYear": 2010, "ISO639": "en",
     "SubAddDate": "2019-01-01", "MovieHash": "abc123def4567890",
     "MovieReleaseName": "Inception.HASHMATCH", "MovieKind": "movie"},
  ]


def test_ingest_and_title_search_movie():
  db = _tmp() / "meta.db"
  assert ingest(db, _records()) == 5
  idx = LocalOsdbIndex(db)
  res = idx.search(MediaInfo("movie", "Inception", "Inception 2010", 2010), "en", 10)
  ids = {r.id for r in res}
  # English Inception rows (1 and 5), not the Dutch (2).
  assert 1 in ids and 5 in ids and 2 not in ids


def test_title_search_tv_season_episode():
  db = _tmp() / "meta.db"
  ingest(db, _records())
  idx = LocalOsdbIndex(db)
  res = idx.search(MediaInfo("tv", "The Show", "The Show S02E04", None, 2, 4), "en", 10)
  ids = [r.id for r in res]
  assert 3 in ids and 4 not in ids  # S02E04 matches row 3, not S02E05 (row 4)


def test_hash_first_search(tmp_path):
  db = tmp_path / "meta.db"
  ingest(db, _records())
  # A video whose moviehash matches row 5's MovieHash should surface row 5 first.
  video = tmp_path / "Inception.mkv"
  video.write_bytes(b"x" * 200000)
  idx = LocalOsdbIndex(db)
  # Monkeypatch the hash function used by the index to return our known hash.
  import subracer.osdb.index as index_mod
  orig = index_mod.opensubtitles_hash
  index_mod.opensubtitles_hash = lambda p: "abc123def4567890"
  try:
    res = idx.search(MediaInfo("movie", "Inception", "Inception 2010", 2010), "en", 10, video)
  finally:
    index_mod.opensubtitles_hash = orig
  assert res and res[0].id == 5  # hash-exact match comes first


def test_language_split():
  src = _tmp() / "all.db"
  ingest(src, _records())
  dst = _tmp() / "en.db"
  copied = language_split(src, dst, ["en"])
  assert copied == 4  # rows 1,3,4,5 are en; row 2 is nl
  with sqlite3.connect(dst) as conn:
    langs = {r[0] for r in conn.execute(f"SELECT DISTINCT ISO639 FROM {METADATA_TABLE}")}
  assert langs == {"en"}


def test_provider_search_and_zstd_download():
  d = _tmp()
  meta = d / "meta.db"
  ingest(meta, _records())
  # Build a data DB holding a zstd-compressed SRT for subtitle num=1.
  data = d / "data.db"
  srt = b"1\n00:00:01,000 --> 00:00:02,000\nHello.\n"
  blob = zstandard.ZstdCompressor().compress(srt)
  with sqlite3.connect(data) as conn:
    conn.execute("CREATE TABLE subtitles (num INTEGER, srt_zstd BLOB)")
    conn.execute("INSERT INTO subtitles VALUES (?, ?)", (1, blob))
    conn.commit()

  prov = LocalOsdbProvider(meta, [data])
  assert prov.available()
  cands = prov.search(MediaInfo("movie", "Inception", "Inception 2010", 2010), "en")
  assert any(c.download_ref == "1" for c in cands)
  one = next(c for c in cands if c.download_ref == "1")
  out = prov.download(one, d / "Inception.en.srt")
  assert out and out.read_bytes() == srt


def test_provider_download_missing_blob_returns_none():
  d = _tmp()
  meta = d / "meta.db"
  ingest(meta, _records())
  prov = LocalOsdbProvider(meta, [])  # no data DBs -> nothing downloadable
  cands = prov.search(MediaInfo("movie", "Inception", "Inception 2010", 2010), "en")
  assert cands  # discovery still works
  assert prov.download(cands[0], d / "x.srt") is None
