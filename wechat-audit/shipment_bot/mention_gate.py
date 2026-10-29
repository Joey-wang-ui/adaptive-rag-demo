"""Explicit visible-text commands; not a server-side mention or an authorization check."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MentionCommand:
    action: Literal["审核", "测试"]
    reference: str


def parse_mention(text: str, nickname: str) -> MentionCommand | None:
    nickname = unicodedata.normalize("NFKC", nickname).strip()
    if not nickname or "\n" in nickname or "\r" in nickname:
        return None
    text = unicodedata.normalize("NFKC", text).strip()
    match = re.fullmatch(
        r"@" + re.escape(nickname) + r"[ \t]+(审核|测试)[ \t]+([A-Za-z0-9_-]{1,40})", text
    )
    if not match:
        return None
    return MentionCommand(
        action="审核" if match.group(1) == "审核" else "测试", reference=match.group(2)
    )
