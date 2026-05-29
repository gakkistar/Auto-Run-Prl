"""Load Modal workspace tokens from a CSV file."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class TokenPair:
    """A Modal workspace authentication pair."""
    token_id: str
    token_secret: str


class MalformedTokenLine(ValueError):
    """Raised when a line in the token file cannot be parsed."""


def load_tokens(path: Path | str) -> list[TokenPair]:
    """Read a CSV of token_id,token_secret pairs.

    Skips blank lines and lines starting with '#'. If the first non-skipped
    line is exactly 'token_id,token_secret' it is treated as a header.
    Duplicate token_ids are dropped (first occurrence wins). Raises
    MalformedTokenLine on any line that doesn't have exactly two non-empty
    comma-separated fields.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)

    pairs: list[TokenPair] = []
    seen_ids: set[str] = set()

    # utf-8-sig strips a leading BOM if present (common when the CSV is
    # produced by PowerShell `Set-Content -Encoding utf8` on Windows) and
    # behaves identically to utf-8 otherwise.
    with p.open("r", encoding="utf-8-sig") as f:
        lines = f.readlines()

    header_consumed = False
    for line_no, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if not header_consumed and stripped == "token_id,token_secret":
            header_consumed = True
            continue
        header_consumed = True  # only the first eligible line can be a header

        parts = [col.strip() for col in stripped.split(",")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise MalformedTokenLine(
                f"line {line_no}: expected 'token_id,token_secret', got {raw!r}"
            )

        token_id, token_secret = parts
        if token_id in seen_ids:
            continue
        seen_ids.add(token_id)
        pairs.append(TokenPair(token_id, token_secret))

    return pairs
