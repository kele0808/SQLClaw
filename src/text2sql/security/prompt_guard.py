"""Prompt-injection screening for untrusted metadata text (Spec 02 M5).

Comments written by database users reach the LLM context later (Spec 08), so
suspicious ones are flagged at collection time and hidden until reviewed.
"""

import re

MAX_COMMENT_LENGTH = 500

INSTRUCTION_OVERRIDE = "INSTRUCTION_OVERRIDE"
ROLE_PLAY = "ROLE_PLAY"
PRIVILEGE_REQUEST = "PRIVILEGE_REQUEST"
CODE_OR_MARKUP = "CODE_OR_MARKUP"
URL = "URL"
TOO_LONG = "TOO_LONG"

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        INSTRUCTION_OVERRIDE,
        re.compile(
            r"ignore\s+(all\s+)?(previous|above)"
            r"|忽略(之前|以上|上面)的?(所有)?(规则|指令|要求)",
            re.IGNORECASE,
        ),
    ),
    (
        ROLE_PLAY,
        re.compile(r"you\s+are\s+now|你现在是|act\s+as|system\s+prompt|系统提示", re.IGNORECASE),
    ),
    (
        PRIVILEGE_REQUEST,
        re.compile(r"所有租户|all\s+tenants|bypass|绕过|越权", re.IGNORECASE),
    ),
    (CODE_OR_MARKUP, re.compile(r"```|<\||</|\{\{")),
    (URL, re.compile(r"https?://", re.IGNORECASE)),
)


def scan_text(text: str) -> tuple[str, ...]:
    """Return the codes of all matching rules, in rule order; empty means clean."""
    hits = [code for code, pattern in _RULES if pattern.search(text)]
    if len(text) > MAX_COMMENT_LENGTH:
        hits.append(TOO_LONG)
    return tuple(hits)
