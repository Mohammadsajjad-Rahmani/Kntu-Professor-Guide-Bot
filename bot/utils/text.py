"""Persian text normalization, used to match names and avoid duplicate records."""
import re

_ARABIC_TO_PERSIAN = str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه"})
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
# Diacritics (tashkeel) and tatweel
_DIACRITICS = re.compile("[ً-ٰٟـ]")
_ZWNJ = "‌"
_SPACES = re.compile(r"\s+")

_TITLES = {
    "دکتر", "مهندس", "استاد", "جناب", "آقای", "اقای", "خانم", "سرکار",
    "پروفسور", "دکترا", "dr", "dr.", "prof", "prof.",
}


def normalize(text: str) -> str:
    """Unify Arabic/Persian letters and digits, drop diacritics and ZWNJ, collapse spaces."""
    text = text.translate(_ARABIC_TO_PERSIAN).translate(_DIGITS)
    text = _DIACRITICS.sub("", text)
    text = text.replace(_ZWNJ, " ")
    text = _SPACES.sub(" ", text).strip().lower()
    return text


def normalize_person(name: str) -> str:
    """Normalize a person's name and strip titles such as 'دکتر'."""
    words = [w for w in normalize(name).split(" ") if w not in _TITLES]
    return " ".join(words)
