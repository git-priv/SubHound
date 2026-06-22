# Stress tests for subhound.core.identify across messy real-world naming:
# many TV season/episode dialects, movie names polluted with release-group and
# codec tags (YTS/YIFY/RARBG/x264/1080p/...), folder-structure fallbacks, and
# genuinely-undetermined files.

from __future__ import annotations

from pathlib import Path

import pytest

from subhound.config.settings import Settings
from subhound.core.identify import MOVIE, TV, UNKNOWN, identify

UT = Settings().unwanted_terms

# Junk tokens that must never survive into title_or_show.
JUNK = [
  "yts", "yify", "rarbg", "x264", "x265", "1080p", "720p", "2160p", "hevc",
  "bluray", "webrip", "web-dl", "bdrip", "hdtv", "ddp", "atmos", "remux",
]

# Each case: (path, expected_type, expected_season, expected_episode, expected_year)
# Use None for "don't assert" on season/episode/year.
TV_CASES = [
  ("13.Reasons.Why.S02E04.1080p.WEBRip.x265-RARBG.mkv", TV, 2, 4, None),
  ("Breaking.Bad.S01E01.720p.BluRay.x264-REWARD.mkv", TV, 1, 1, None),
  ("The.Office.US.s09e23.HDTV.x264-LOL.mp4", TV, 9, 23, None),
  ("Andor.s1e2.mkv", TV, 1, 2, None),
  ("Show.Name.S01.E05.1080p.mkv", TV, 1, 5, None),
  ("Show_Name_s03_e07_HDTV.mkv", TV, 3, 7, None),
  ("Some Show S01 E12 [1080p].mkv", TV, 1, 12, None),
  ("Friends.1x05.The.One.With.x264.mkv", TV, 1, 5, None),
  ("Mr.Robot.04x10.WEB.mkv", TV, 4, 10, None),
  ("Severance Season 1 Episode 8 2160p.mkv", TV, 1, 8, None),
  ("Game.of.Thrones.S08E06.The.Iron.Throne.1080p.WEB-DL.DDP5.1.mkv", TV, 8, 6, None),
  # Folder-derived show name and/or season:
  ("Breaking Bad/Season 01/breaking.bad.s01e01.720p.x264.mkv", TV, 1, 1, None),
  ("Breaking Bad/S03/E07 - The Reveal.mkv", TV, 3, 7, None),
  ("The Office/s2/the_office_s02_e12.mkv", TV, 2, 12, None),
  ("Some Show/Season 4/Episode 8.mkv", TV, 4, 8, None),
  ("Severance/Season 2/E05 - Trojan's Horse [2160p][HEVC].mkv", TV, 2, 5, None),
  ("TV/Chernobyl/Season 01/Chernobyl.S01E03.1080p.AMZN.WEB-DL.mkv", TV, 1, 3, None),
]

MOVIE_CASES = [
  ("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv", MOVIE, "The Matrix", 1999),
  ("Inception.2010.2160p.UHD.BluRay.x265-TERMINAL.mkv", MOVIE, "Inception", 2010),
  ("Interstellar.2014.1080p.BluRay.x264.YIFY.mp4", MOVIE, "Interstellar", 2014),
  ("The Dark Knight (2008) [1080p] [YTS.MX]/The Dark Knight.mkv", MOVIE, "The Dark Knight", 2008),
  ("Parasite.2019.LIMITED.1080p.BluRay.x264-RARBG.mkv", MOVIE, "Parasite", 2019),
  ("Blade.Runner.2049.2017.REMUX.2160p.HEVC.mkv", MOVIE, "Blade Runner 2049", 2017),
  ("Avengers.Endgame.2019.1080p.WEBRip.DDP5.1.Atmos.x264-NTb.mkv", MOVIE, "Avengers Endgame", 2019),
  ("Dune.Part.Two.2024.IMAX.2160p.WEB-DL.mkv", MOVIE, "Dune Part Two", 2024),
  # Title from folder when filename is generic:
  ("Movies/Avatar (2009)/movie.mkv", MOVIE, "Avatar", 2009),
  ("Spirited.Away.2001.1080p.BluRay.x264.AAC-[YTS.MX].mp4", MOVIE, "Spirited Away", 2001),
]

UNKNOWN_CASES = [
  "downloads/complete/x264.mkv",
  "Movies/misc/1080p.mkv",
]


@pytest.mark.parametrize("path,exp_type,exp_s,exp_e,exp_y", TV_CASES)
def test_tv(path, exp_type, exp_s, exp_e, exp_y):
  info = identify(Path(path), None, UT)
  assert info.media_type == exp_type, f"{path} -> {info}"
  if exp_s is not None:
    assert info.season == exp_s, f"{path} season {info.season}"
  if exp_e is not None:
    assert info.episode == exp_e, f"{path} episode {info.episode}"
  assert info.title_or_show, f"{path} has empty show name"
  low = info.title_or_show.lower()
  for tok in JUNK:
    assert tok not in low, f"{path}: junk {tok!r} leaked into show {info.title_or_show!r}"


@pytest.mark.parametrize("path,exp_type,exp_title,exp_y", MOVIE_CASES)
def test_movie(path, exp_type, exp_title, exp_y):
  info = identify(Path(path), None, UT)
  assert info.media_type == exp_type, f"{path} -> {info}"
  assert info.year == exp_y, f"{path} year {info.year}"
  assert info.title_or_show == exp_title, f"{path} title {info.title_or_show!r} != {exp_title!r}"
  assert str(exp_y) not in info.title_or_show, f"{path}: year leaked into title"


@pytest.mark.parametrize("path", UNKNOWN_CASES)
def test_unknown(path):
  info = identify(Path(path), None, UT)
  assert info.media_type == UNKNOWN, f"{path} -> {info.media_type} ({info})"
  assert info.note, f"{path}: unknown result should carry an explanatory note"


if __name__ == "__main__":
  # Pretty table for manual inspection.
  rows = [c[0] for c in TV_CASES] + [c[0] for c in MOVIE_CASES] + UNKNOWN_CASES
  for p in rows:
    info = identify(Path(p), None, UT)
    print(f"{info.media_type:7} | S={info.season} E={info.episode} Y={info.year} "
          f"| title={info.title_or_show!r:22} | q={info.query!r}")
    if info.note:
      print(f"          note: {info.note[:90]}")
