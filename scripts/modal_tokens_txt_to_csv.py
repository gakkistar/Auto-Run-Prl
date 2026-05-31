"""Convert a text file of `modal token set` lines into a tokens.csv.

Input format (each non-comment line):

    modal token set --token-id ak-XXXX --token-secret as-YYYY

Tolerates:
- Extra whitespace.
- `--token-id=ak-XXXX` (equals form).
- Reversed order (`--token-secret` before `--token-id`).
- Blank lines and `#` comments.
- Quoted values.
- Surrounding `$ ` / `> ` prompts (stripped).

Skips and warns on lines that don't yield both pieces.

Usage:

    python scripts/modal_tokens_txt_to_csv.py input.txt -o tokens.csv
    python scripts/modal_tokens_txt_to_csv.py input.txt          # writes to stdout
    cat input.txt | python scripts/modal_tokens_txt_to_csv.py -  # read stdin
"""
from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path


def parse_line(line: str) -> tuple[str, str] | None:
    """Extract (token_id, token_secret) from one `modal token set ...` line.

    Returns None if the line isn't a valid `modal token set` invocation.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    # Strip common shell prompts at the start.
    for prompt in ("$ ", "> ", "% ", "PS> "):
        if stripped.startswith(prompt):
            stripped = stripped[len(prompt):].strip()

    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        return None

    token_id: str | None = None
    token_secret: str | None = None

    i = 0
    while i < len(tokens):
        arg = tokens[i]
        if arg == "--token-id" and i + 1 < len(tokens):
            token_id = tokens[i + 1]
            i += 2
            continue
        if arg.startswith("--token-id="):
            token_id = arg.split("=", 1)[1]
            i += 1
            continue
        if arg == "--token-secret" and i + 1 < len(tokens):
            token_secret = tokens[i + 1]
            i += 2
            continue
        if arg.startswith("--token-secret="):
            token_secret = arg.split("=", 1)[1]
            i += 1
            continue
        i += 1

    if token_id and token_secret:
        return token_id, token_secret
    return None


def convert(lines: list[str]) -> tuple[list[tuple[str, str]], list[tuple[int, str]]]:
    """Return (pairs, warnings) where warnings = list of (line_no, raw_line)."""
    pairs: list[tuple[str, str]] = []
    warnings: list[tuple[int, str]] = []
    seen: set[str] = set()

    for line_no, raw in enumerate(lines, start=1):
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        result = parse_line(raw)
        if result is None:
            warnings.append((line_no, raw.rstrip("\n")))
            continue
        token_id, token_secret = result
        if token_id in seen:
            continue
        seen.add(token_id)
        pairs.append((token_id, token_secret))

    return pairs, warnings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Convert a txt of `modal token set` commands into tokens.csv.",
    )
    p.add_argument(
        "input",
        type=str,
        help="Input txt file path, or '-' to read from stdin.",
    )
    p.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output CSV path. Default: write to stdout.",
    )
    p.add_argument(
        "--no-header",
        action="store_true",
        help="Don't emit the 'token_id,token_secret' header row.",
    )
    args = p.parse_args(argv)

    if args.input == "-":
        text = sys.stdin.read()
    else:
        text = Path(args.input).read_text(encoding="utf-8-sig")

    lines = text.splitlines()
    pairs, warnings = convert(lines)

    # Emit CSV.
    out_lines: list[str] = []
    if not args.no_header:
        out_lines.append("token_id,token_secret")
    for tid, sec in pairs:
        out_lines.append(f"{tid},{sec}")
    csv_text = "\n".join(out_lines) + ("\n" if out_lines else "")

    if args.output is None:
        sys.stdout.write(csv_text)
    else:
        args.output.write_text(csv_text, encoding="utf-8")
        print(
            f"Wrote {len(pairs)} token pair(s) to {args.output}",
            file=sys.stderr,
        )

    if warnings:
        print(
            f"\n{len(warnings)} line(s) skipped (no --token-id/--token-secret pair found):",
            file=sys.stderr,
        )
        for line_no, raw in warnings:
            print(f"  line {line_no}: {raw}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
