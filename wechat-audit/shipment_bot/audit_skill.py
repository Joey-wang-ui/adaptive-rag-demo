"""Editable review skill and structured intent; no model can issue OS actions."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ReviewIntent(BaseModel):
    action: Literal["ignore", "review", "compare", "clarify"]
    scope: Literal["recent", "inline"] = "recent"
    document_text: str = Field(default="", max_length=20000)
    question: str = Field(default="", max_length=300)


def builtin_skill():
    return (Path(__file__).parent / "skills/shipment_review.md").read_text(encoding="utf-8")


def active_skill(override=""):
    return override.strip() or builtin_skill()


def literal_numbers(text):
    """Recognize literal Arabic and unambiguous Chinese integers, not derived sums."""
    import re
    import unicodedata
    from decimal import Decimal

    text = unicodedata.normalize("NFKC", text).replace(",", "")
    numbers = {Decimal(x) for x in re.findall(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])", text)}
    digits = dict(zip("零一二三四五六七八九", range(10), strict=True))
    units = {"十": 10, "百": 100, "千": 1000}

    def render(value):
        result, zero = "", False
        for scale, suffix in [(1000, "千"), (100, "百"), (10, "十"), (1, "")]:
            digit, value = divmod(value, scale)
            if digit:
                if zero:
                    result += "零"
                result += "零一二三四五六七八九"[digit] + suffix
                zero = False
            elif result and value:
                zero = True
        return "十" + result[2:] if result.startswith("一十") else result or "零"

    # Only accept canonical integers; colloquial '一百二' is ambiguous and is not guessed.
    pattern = r"([零〇一二两三四五六七八九十百千万]+)\s*(?:箱|袋|件|盒|包|瓶|桶|个|支|套|公斤|千克|斤|吨|元|块)"
    for token in re.findall(pattern, text):
        token = token.replace("两", "二").replace("〇", "零")
        value, pending, valid = 0, None, True
        last_scale = 10000
        for char in token:
            if char in digits:
                if pending not in (None, 0):
                    valid = False
                pending = digits[char]
            elif char in units and units[char] < last_scale:
                last_scale = units[char]
                value += (pending if pending is not None else 1) * last_scale
                pending = None
            else:
                valid = False
        value += pending or 0
        # render 10..19 without the leading 一.
        rendered = render(value) if value < 10000 else ""
        if 10 <= value < 20:
            rendered = "十" + ("零一二三四五六七八九"[value % 10] if value % 10 else "")
        if valid and rendered == token:
            numbers.add(Decimal(value))
    return numbers
