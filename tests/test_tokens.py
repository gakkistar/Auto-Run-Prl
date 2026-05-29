import pytest
from pathlib import Path

from modal_orchestrator.tokens import TokenPair, load_tokens, MalformedTokenLine


def write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "tokens.csv"
    p.write_text(content, encoding="utf-8")
    return p


def test_parses_two_simple_lines(tmp_path):
    f = write(tmp_path, "ak-1,as-1\nak-2,as-2\n")
    assert load_tokens(f) == [
        TokenPair("ak-1", "as-1"),
        TokenPair("ak-2", "as-2"),
    ]


def test_skips_blank_lines_and_hash_comments(tmp_path):
    f = write(tmp_path, "# comment\n\nak-1,as-1\n\n# another\nak-2,as-2\n")
    assert load_tokens(f) == [
        TokenPair("ak-1", "as-1"),
        TokenPair("ak-2", "as-2"),
    ]


def test_skips_header_row_if_present(tmp_path):
    f = write(tmp_path, "token_id,token_secret\nak-1,as-1\n")
    assert load_tokens(f) == [TokenPair("ak-1", "as-1")]


def test_deduplicates_by_token_id_keeping_first(tmp_path):
    f = write(tmp_path, "ak-1,as-A\nak-2,as-2\nak-1,as-B\n")
    assert load_tokens(f) == [
        TokenPair("ak-1", "as-A"),
        TokenPair("ak-2", "as-2"),
    ]


def test_strips_whitespace_around_fields(tmp_path):
    f = write(tmp_path, "  ak-1 , as-1  \n")
    assert load_tokens(f) == [TokenPair("ak-1", "as-1")]


def test_raises_on_malformed_line(tmp_path):
    f = write(tmp_path, "ak-1,as-1\nbroken-line-no-comma\n")
    with pytest.raises(MalformedTokenLine) as exc:
        load_tokens(f)
    assert "line 2" in str(exc.value)


def test_raises_on_empty_field(tmp_path):
    f = write(tmp_path, "ak-1,\n")
    with pytest.raises(MalformedTokenLine):
        load_tokens(f)


def test_missing_file_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_tokens(tmp_path / "does-not-exist.csv")


def test_strips_utf8_bom_from_first_line(tmp_path):
    """PowerShell 5.1 `Set-Content -Encoding utf8` writes a UTF-8 BOM.
    The loader must transparently strip it from the first token_id."""
    p = tmp_path / "tokens.csv"
    p.write_bytes(b"\xef\xbb\xbfak-1,as-1\nak-2,as-2\n")
    assert load_tokens(p) == [
        TokenPair("ak-1", "as-1"),
        TokenPair("ak-2", "as-2"),
    ]
