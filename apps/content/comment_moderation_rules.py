from __future__ import annotations

import re


DEFAULT_COMMENT_BLOCKED_KEYWORDS = (
    "操你妈",
    "草你妈",
    "艹你妈",
    "草泥马",
    "曹尼玛",
    "槽尼玛",
    "槽你妈",
    "草尼玛",
    "cao ni ma",
    "caonima",
    "cnm",
    "cao nm",
    "傻逼",
    "煞笔",
    "妈的",
    "他妈的",
    "你妈的",
)


COMMENT_TEXT_CANONICAL_RULES = (
    ("艹", "操"),
    ("草你妈", "操你妈"),
    ("草泥马", "操你妈"),
    ("草尼玛", "操你妈"),
    ("曹尼玛", "操你妈"),
    ("曹你妈", "操你妈"),
    ("槽尼玛", "操你妈"),
    ("槽你妈", "操你妈"),
    ("日你妈", "操你妈"),
    ("caonima", "操你妈"),
    ("cao n i ma", "操你妈"),
    ("caonm", "操你妈"),
    ("caonimaa", "操你妈"),
    ("cnm", "操你妈"),
    ("傻b", "傻逼"),
    ("傻比", "傻逼"),
    ("煞b", "傻逼"),
)


COMMENT_PATTERN_RULES = (
    (re.compile(r"s[\s._-]*b", re.IGNORECASE), "sb"),
)


COMMENT_ASCII_SUBSTITUTIONS = str.maketrans({
    "0": "o",
    "1": "i",
    "3": "e",
    "4": "a",
    "5": "s",
    "6": "g",
    "7": "t",
    "8": "b",
    "9": "g",
    "@": "a",
    "$": "s",
})
