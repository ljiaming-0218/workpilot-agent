"""Dependency-free tokenizer for mixed Chinese and technical English text."""

import re
import unicodedata


TOKEN_PATTERN = re.compile(
    r"[a-z0-9]+(?:[._:/-][a-z0-9]+)*|[\u4e00-\u9fff]+",
    re.IGNORECASE,
)


def tokenize(text: str) -> list[str]:
    """Normalize text and emit technical terms plus overlapping Chinese bigrams."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    for match in TOKEN_PATTERN.finditer(normalized):
        value = match.group(0)
        if _is_chinese(value):
            tokens.extend(_chinese_tokens(value))
        else:
            tokens.append(value)
    return tokens


def _is_chinese(value: str) -> bool:
    return bool(value) and "\u4e00" <= value[0] <= "\u9fff"


def _chinese_tokens(value: str) -> list[str]:
    if len(value) <= 2:
        return [value]
    tokens = [value]
    tokens.extend(value[index : index + 2] for index in range(len(value) - 1))
    return tokens
