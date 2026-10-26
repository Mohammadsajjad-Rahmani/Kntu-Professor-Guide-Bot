"""First moderation layer: cheap rule-based checks that label a review as clean or suspicious.

The result never rejects anything by itself. It only helps the admin (a clean review can be
approved in one tap) and blocks automatic publishing for trusted students when something looks off.
"""
import re

from bot.utils.text import normalize

FLAG_LABELS = {
    "profanity": "کلمهٔ نامناسب یا اتهام",
    "contact_info": "شماره یا آیدی در «راه ارتباطی»",
    "extreme_no_comment": "نمرهٔ خیلی پایین بدون توضیح",
    "duplicate_text": "متن تکراری",
}

# Starter list. Extend it as you meet real cases (words are matched after normalization).
BLOCKLIST_WORDS = {
    "احمق", "عوضی", "کثافت", "بیشعور", "حیوان", "خرف", "نفهم", "دزد", "کلاهبردار", "رشوه",
}
BLOCKLIST_PHRASES = ("بی شعور", "بی سواد", "بی لیاقت", "پول میگیره", "پول می گیره")

_PHONE_OR_ID = re.compile(r"@\w{3,}|\d{8,}")
_WORD = re.compile(r"\w+")


def precheck(comment: str | None, contact: str | None, scores: list[int]) -> list[str]:
    """Return a list of flag codes (empty list = clean)."""
    flags: list[str] = []
    if comment:
        norm = normalize(comment)
        if set(_WORD.findall(norm)) & BLOCKLIST_WORDS or any(p in norm for p in BLOCKLIST_PHRASES):
            flags.append("profanity")
    if contact and _PHONE_OR_ID.search(normalize(contact).replace(" ", "").replace("-", "")):
        flags.append("contact_info")
    if scores and max(scores) <= 3 and not comment:
        flags.append("extreme_no_comment")
    return flags


def flags_to_text(flags: str | list[str] | None) -> str:
    """Human-readable (Persian) text for a stored comma-separated flags string."""
    if not flags:
        return ""
    codes = flags.split(",") if isinstance(flags, str) else flags
    return "، ".join(FLAG_LABELS.get(c, c) for c in codes if c)
