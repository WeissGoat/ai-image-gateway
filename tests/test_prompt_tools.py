"""prompt_tools 测试。"""

from ai_image_gateway.processing.prompt_tools import (
    estimate_token_count,
    merge,
    sanitize,
    validate_length,
)


class TestSanitize:
    def test_basic_cleanup(self):
        assert sanitize("a,,b,, c") == "a,b, c"

    def test_empty_brackets(self):
        assert sanitize("a, {}, [], b") == "a, b"

    def test_bracket_commas(self):
        assert sanitize("{,a,}") == "{a}"
        assert sanitize("[,b,]") == "[b]"

    def test_nbsp(self):
        assert sanitize("a\xa0b") == "a b"

    def test_leading_trailing(self):
        assert sanitize(", , hello, , ") == "hello"

    def test_empty(self):
        assert sanitize("") == ""


class TestMerge:
    def test_basic(self):
        result = merge("steampunk", "rusty dagger", "transparent background")
        assert result == "steampunk, rusty dagger, transparent background"

    def test_skip_none_and_empty(self):
        result = merge("a", None, "", "b")
        assert result == "a, b"

    def test_sanitizes_each(self):
        result = merge("a,,b", "c, ,d")
        assert ",," not in result


class TestEstimateTokenCount:
    def test_basic(self):
        count = estimate_token_count("game item icon, rusty dagger, steampunk")
        assert count == 6  # "game item icon"=3, "rusty dagger"=2, "steampunk"=1

    def test_empty(self):
        assert estimate_token_count("") == 0


class TestValidateLength:
    def test_valid(self):
        ok, count = validate_length("a, b, c", max_tokens=10)
        assert ok is True
        assert count == 3

    def test_strict_raises(self):
        import pytest
        with pytest.raises(ValueError):
            validate_length("a, b, c", max_tokens=1, strict=True)
