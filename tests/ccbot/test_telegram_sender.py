"""Tests for telegram_sender.split_message."""

import pytest

from ccbot.telegram_sender import split_message, utf16_len


class TestSplitMessage:
    @pytest.mark.parametrize(
        "text, expected",
        [
            pytest.param("hello world", ["hello world"], id="short_text"),
            pytest.param("", [""], id="empty_string"),
            pytest.param("a" * 4096, ["a" * 4096], id="exactly_4096_chars"),
        ],
    )
    def test_single_chunk_returned(self, text: str, expected: list[str]):
        assert split_message(text) == expected

    def test_split_on_newline_boundaries(self):
        line = "x" * 2000
        text = f"{line}\n{line}\n{line}"
        chunks = split_message(text)
        assert len(chunks) == 2
        assert chunks[0] == f"{line}\n{line}"
        assert chunks[1] == line

    def test_single_long_line_force_split(self):
        text = "a" * 8192
        chunks = split_message(text)
        assert len(chunks) == 2
        assert chunks[0] == "a" * 4096
        assert chunks[1] == "a" * 4096

    def test_custom_max_length(self):
        text = "aaaa\nbbbb\ncccc"
        chunks = split_message(text, max_length=10)
        assert chunks == ["aaaa\nbbbb", "cccc"]

    def test_custom_max_length_force_split(self):
        text = "a" * 120
        chunks = split_message(text, max_length=50)
        assert len(chunks) == 3
        assert chunks[0] == "a" * 50
        assert chunks[1] == "a" * 50
        assert chunks[2] == "a" * 20

    def test_trailing_newline_handling(self):
        text = "line1\nline2"
        chunks = split_message(text, max_length=50)
        assert len(chunks) == 1
        assert chunks[0] == "line1\nline2"

    def test_mixed_lines_grouping(self):
        short = "short"
        long_line = "x" * 60
        text = f"{short}\n{long_line}\n{short}"
        chunks = split_message(text, max_length=50)
        assert len(chunks) == 4
        assert chunks[0] == short
        assert chunks[1] == "x" * 50
        assert chunks[2] == "x" * 10
        assert chunks[3] == short

    @pytest.mark.parametrize(
        "text, max_len, expected_count",
        [
            pytest.param("a\nb\nc\nd\ne", 4, 3, id="many_short_lines"),
            pytest.param("ab\ncd\nef\ngh", 6, 2, id="pairs_of_lines"),
        ],
    )
    def test_chunk_count(self, text: str, max_len: int, expected_count: int):
        chunks = split_message(text, max_length=max_len)
        assert len(chunks) == expected_count

    def test_all_chunks_within_max_length(self):
        lines = [f"line-{i:04d} " + "x" * 40 for i in range(100)]
        text = "\n".join(lines)
        chunks = split_message(text, max_length=200)
        for chunk in chunks:
            assert len(chunk) <= 200

    def test_code_block_split_closes_and_reopens(self):
        """When splitting inside a code block, close with ``` and reopen."""
        code = "```python\n" + "\n".join(f"line{i}" for i in range(20)) + "\n```"
        chunks = split_message(code, max_length=60)
        assert len(chunks) > 1
        # First chunk should end with ``` (closing the block)
        assert chunks[0].endswith("```")
        # Second chunk should start with ```python (reopening)
        assert chunks[1].startswith("```python")
        # Last chunk should end with ``` (the original close)
        assert chunks[-1].rstrip().endswith("```")

    def test_code_block_not_split_fits(self):
        """Code block that fits in one chunk should not be modified."""
        code = "```python\nprint('hi')\n```"
        chunks = split_message(code, max_length=100)
        assert chunks == [code]

    def test_text_before_and_after_code_block(self):
        """Text around a code block that gets split."""
        text = "before\n```js\nvar x = 1;\nvar y = 2;\n```\nafter"
        chunks = split_message(text, max_length=30)
        # Every chunk should have balanced ``` pairs or none
        for chunk in chunks:
            fence_count = chunk.count("```")
            assert fence_count % 2 == 0, f"Unbalanced fences in: {chunk!r}"

    def test_multiple_code_blocks(self):
        """Multiple code blocks should each be handled independently."""
        text = "text\n```py\na=1\n```\nmid\n```sh\nls\n```\nend"
        chunks = split_message(text, max_length=30)
        for chunk in chunks:
            fence_count = chunk.count("```")
            assert fence_count % 2 == 0, f"Unbalanced fences in: {chunk!r}"


class TestUtf16Len:
    """Telegram counts message length in UTF-16 code units, not code points."""

    def test_ascii(self):
        assert utf16_len("hello") == 5

    def test_bmp_cjk(self):
        # CJK in the BMP is one UTF-16 unit per character
        assert utf16_len("中文") == 2

    def test_emoji_counts_double(self):
        # Non-BMP characters (surrogate pairs) count as two units
        assert utf16_len("😀") == 2
        assert utf16_len("a😀b") == 4


class TestSplitMessageUtf16:
    """split_message must budget by UTF-16 units or emoji-heavy text
    exceeds Telegram's real limit and the send fails."""

    def test_emoji_text_within_utf16_budget(self):
        # 3000 emoji = 3000 code points but 6000 UTF-16 units
        text = "😀" * 3000
        chunks = split_message(text, max_length=4096)
        assert len(chunks) > 1
        for chunk in chunks:
            assert utf16_len(chunk) <= 4096

    def test_emoji_lines_within_utf16_budget(self):
        lines = ["😀" * 50 for _ in range(200)]
        text = "\n".join(lines)
        chunks = split_message(text, max_length=500)
        for chunk in chunks:
            assert utf16_len(chunk) <= 500

    def test_force_split_never_splits_surrogate_pair(self):
        # An odd budget with 2-unit chars: pieces must stay whole characters
        text = "😀" * 100
        chunks = split_message(text, max_length=51)
        for chunk in chunks:
            assert utf16_len(chunk) <= 51
            # Round-trip through UTF-16 must not raise (no lone surrogates)
            chunk.encode("utf-16")


class TestSplitMessageCodeBlockForceSplit:
    """Overlong single lines inside a code block keep fence integrity."""

    def test_long_line_in_code_block_gets_fenced_pieces(self):
        long_line = "x" * 200
        text = f"```js\n{long_line}\n```"
        chunks = split_message(text, max_length=60)
        for chunk in chunks:
            assert chunk.count("```") % 2 == 0, f"Unbalanced fences in: {chunk!r}"
            assert utf16_len(chunk) <= 60
        # The forced pieces are wrapped as standalone code blocks
        assert any(c.startswith("```js\nx") and c.endswith("```") for c in chunks)
