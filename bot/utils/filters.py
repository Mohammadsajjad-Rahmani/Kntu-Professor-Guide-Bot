"""Rule-based checks for free text (first moderation layer)."""
import re

from bot.utils.text import normalize

_URL = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/|\b\w+\.(com|ir|org|net|me)\b)", re.I)
_HANDLE = re.compile(r"@\w{3,}")
_PHONE = re.compile(r"(\+?98|0)?9\d{9}|\d{8,}")


def check_free_text(text: str, max_len: int = 500) -> str | None:
    """Return a reason (Persian, shown to the student) if the text is not allowed, else None."""
    if len(text) > max_len:
        return f"متن خیلی طولانیه. حداکثر {max_len} کاراکتر."
    norm = normalize(text)
    if _URL.search(norm):
        return "لینک توی توضیحات مجاز نیست."
    if _HANDLE.search(norm):
        return "آیدی و نام‌کاربری توی توضیحات مجاز نیست."
    if _PHONE.search(norm.replace(" ", "").replace("-", "")):
        return "شماره تلفن توی توضیحات مجاز نیست."
    return None
