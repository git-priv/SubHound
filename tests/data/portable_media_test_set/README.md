# Portable Media Directory Parser Test Set — 400 Files

This archive is designed to extract and behave consistently on Linux and
Windows, including case-insensitive Windows filesystems.

## Portability checks

The build validates that there are no:

- Windows-forbidden characters: `< > : " / \ | ? *`
- trailing spaces or periods
- reserved Windows names such as `CON`, `PRN`, `AUX`, `NUL`, `COM1`, or `LPT1`
- control characters or null bytes
- sibling names that collide when compared case-insensitively
- symbolic links
- absolute or `..` path-traversal ZIP entries
- relative file paths longer than 180 characters
- individual path components longer than 120 characters

## Season-folder case variants

Linux can store `s01` and `S01` beside one another. Default Windows
filesystems cannot. Each case variant therefore has a unique wrapper:

```text
season_folder_filename_matrix/
  variant_01/
    The X-Files (1993)/
      s01/
  variant_06/
    The X-Files (1993)/
      S01/
```

The parser-relevant section still has the requested form:

```text
The X-Files (1993)/
  <season-folder variant>/
    <episode filename variant>
```

## Contents

- 395 parser test files
- README.md
- manifest.csv
- manifest.json
- directory_tree.txt
- SHA256SUMS.txt

All apparent media files are tiny placeholders, not playable video.

`SHA256SUMS.txt` is an optional integrity ledger and can be ignored by the
media parser.
