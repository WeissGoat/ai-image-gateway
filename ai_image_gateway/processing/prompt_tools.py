"""
Prompt 工具。

通用的 prompt 清洗、拼接和校验工具。
不包含任何业务特有的 artist/tag 过滤逻辑。
"""

from __future__ import annotations

import re


def sanitize(prompt: str) -> str:
    """
    清洗 prompt 字符串。

    - 移除多余逗号、空格、空括号
    - 规范化分隔符
    """
    if not prompt:
        return ""

    text = prompt.strip()
    # 替换非断行空格
    text = text.replace("\xa0", " ")
    # 移除空括号对 (多次确保嵌套也被清理)
    for _ in range(5):
        text = text.replace("{}", "")
        text = text.replace("[]", "")
    # 移除括号内紧邻的逗号
    text = re.sub(r"\{,", "{", text)
    text = re.sub(r",}", "}", text)
    text = re.sub(r"\[,", "[", text)
    text = re.sub(r",]", "]", text)
    # 折叠连续逗号和空格
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r"\s{2,}", " ", text)
    # 再次折叠 (空括号移除后可能留下连续逗号)
    text = re.sub(r",\s*,+", ",", text)
    # 移除首尾逗号
    text = text.strip(", ")
    return text


def merge(
    *segments: str | None,
    separator: str = ", ",
) -> str:
    """
    结构化拼接 prompt 片段。

    跳过 None 和空字符串，每段自动 sanitize。

    Usage::

        prompt = merge(
            style_base,       # "steampunk, worn metal, ..."
            subject,          # "tactical blade, narrow sharp blade, ..."
            composition,      # "centered single object, vertical silhouette"
            background,       # "transparent background"
        )
    """
    parts = [sanitize(s) for s in segments if s]
    return separator.join(p for p in parts if p)


def estimate_token_count(prompt: str) -> int:
    """
    粗略估算 prompt 的 token 数量。

    基于逗号分隔的 tag 数量 + 空格分词。
    这是一个启发式估算，不精确但足够用于预检。
    """
    if not prompt:
        return 0
    # 以逗号分隔的 tag 数
    tags = [t.strip() for t in prompt.split(",") if t.strip()]
    # 每个 tag 内的空格分词
    total = sum(len(tag.split()) for tag in tags)
    return total


def validate_length(
    prompt: str,
    max_tokens: int = 225,
    *,
    strict: bool = False,
) -> tuple[bool, int]:
    """
    校验 prompt 长度。

    Returns:
        (is_valid, estimated_tokens)

    Args:
        strict: True 时超出则抛出 ValueError。
    """
    count = estimate_token_count(prompt)
    is_valid = count <= max_tokens
    if strict and not is_valid:
        raise ValueError(
            f"Prompt exceeds max tokens: {count} > {max_tokens}"
        )
    return is_valid, count
