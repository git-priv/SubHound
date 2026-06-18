# subracer.core.subtitle_lang
#
# Detect the *human language of a subtitle file's text* (e.g. is this .srt
# English or Dutch?). Used to label subtitles we extract from a video or
# download from a source that doesn't reliably state the language.
#
# Replaces Subservient's detect_language / to_iso639_2 helpers. Uses langdetect
# (deterministic seed for reproducibility) and pycountry for code normalization.

from __future__ import annotations

import re
from pathlib import Path

import pycountry
from langdetect import DetectorFactory, LangDetectException, detect

# Make langdetect deterministic across runs.
DetectorFactory.seed = 0

# Strip SubRip sequence numbers and timestamp lines so only dialogue is sampled.
_SRT_INDEX_RE = re.compile(r"^\d+\s*$")
_SRT_TIME_RE = re.compile(r"-->|\d{1,2}:\d{2}:\d{2}")
_TAG_RE = re.compile(r"<[^>]+>|\{[^}]*\}")


# Function Summary:
#    Read a subtitle file and return just its dialogue text, stripped of cue
#    numbers, timestamps and markup, capped to a sample size for speed.
#
#  Input (parameters):
#    path [Path]:        path to a subtitle file (.srt/.ass/.vtt etc.)
#    max_chars [int]:    maximum number of characters to return
#
#  Output:
#    text [str]:  concatenated dialogue text (possibly empty)
#
# Example:
#    extract_text(Path("movie.srt"), 100)  ->  "Hello there. How are you?"
def extract_text(path: Path, max_chars: int = 5000) -> str:
  lines: list[str] = []
  total = 0
  try:
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
      for raw in fh:
        line = raw.strip()
        if not line or _SRT_INDEX_RE.match(line) or _SRT_TIME_RE.search(line):
          continue
        line = _TAG_RE.sub("", line).strip()
        if not line:
          continue
        lines.append(line)
        total += len(line)
        if total >= max_chars:
          break
  except OSError:
    return ""
  return " ".join(lines)


# Function Summary:
#    Normalize a language name or code to a 2-letter ISO 639-1 code where one
#    exists (falling back to the input lowercased if no mapping is found).
#
#  Input (parameters):
#    code [str]:  a language name or 2/3-letter code (e.g. "English", "eng", "en")
#
#  Output:
#    iso [str]:  the 2-letter code, or the lowercased input if unmappable
#
# Example:
#    to_iso639_1("eng")  ->  "en"
def to_iso639_1(code: str) -> str:
  code = code.strip()
  if not code:
    return ""
  try:
    if len(code) == 2:
      lang = pycountry.languages.get(alpha_2=code.lower())
    elif len(code) == 3:
      lang = pycountry.languages.get(alpha_3=code.lower())
    else:
      lang = pycountry.languages.get(name=code.title())
    if lang and hasattr(lang, "alpha_2"):
      return lang.alpha_2
  except (KeyError, AttributeError):
    pass
  return code.lower()


# Function Summary:
#    Detect the language of a subtitle file's dialogue text, returning a 2-letter
#    ISO 639-1 code. Returns None when there is too little text or detection fails.
#
#  Input (parameters):
#    path [Path]:  path to the subtitle file
#
#  Output:
#    lang [str | None]:  ISO 639-1 code (e.g. "en"), or None if undetectable
#
# Example:
#    detect_subtitle_language(Path("movie.en.srt"))  ->  "en"
def detect_subtitle_language(path: Path) -> str | None:
  text = extract_text(path)
  if len(text) < 20:
    return None
  try:
    return to_iso639_1(detect(text))
  except LangDetectException:
    return None
