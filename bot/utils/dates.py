"""Jalali (Persian) calendar helpers, used to offer recent academic terms as buttons."""
import re
from datetime import date

from bot.utils.text import normalize

_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def to_persian_digits(value: object) -> str:
    return str(value).translate(_PERSIAN_DIGITS)


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        355666
        + 365 * gy
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
        + gd
        + g_d_m[gm - 1]
    )
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return jy, jm, jd


TERMS_START_YEAR = 1400  # the term list starts at Mehr 1400


def terms_since(today: date | None = None, start_year: int = TERMS_START_YEAR) -> list[str]:
    """Every term from today back to Mehr `start_year`, newest first."""
    today = today or date.today()
    jy, jm, _ = gregorian_to_jalali(today.year, today.month, today.day)
    if jm >= 11:
        name, year = "بهمن", jy
    elif jm >= 7:
        name, year = "مهر", jy
    else:
        name, year = "بهمن", jy - 1

    terms = []
    while True:
        terms.append(f"{name} {to_persian_digits(year)}")
        if name == "مهر" and year <= start_year:
            break
        if name == "مهر":
            name, year = "بهمن", year - 1
        else:
            name = "مهر"
    return terms


def parse_manual_term(text: str, today: date | None = None) -> str | None:
    """Validate a term typed by the student, e.g. 'تابستان ۱۳۹۹'. Returns it cleaned or None."""
    t = normalize(text)
    if not t or len(t) > 25 or not re.fullmatch(r"[\w\s]+", t):
        return None
    if not re.search(r"[^\W\d_]", t):  # needs at least one word (term name), not only digits
        return None
    m = re.search(r"(?<!\d)(1[34]\d\d)(?!\d)", t)
    if not m:
        return None
    today = today or date.today()
    current_year = gregorian_to_jalali(today.year, today.month, today.day)[0]
    if not 1380 <= int(m.group(1)) <= current_year + 1:
        return None
    return to_persian_digits(" ".join(t.split()))


def current_terms(today: date | None = None, count: int = 4) -> list[str]:
    """Most recent terms, newest first, e.g. ['مهر ۱۴۰۵', 'بهمن ۱۴۰۴', ...].

    Two main terms per year: Mehr (from month 7) and Bahman (from month 11,
    running until the end of summer of the next Jalali year).
    """
    today = today or date.today()
    jy, jm, _ = gregorian_to_jalali(today.year, today.month, today.day)
    if jm >= 11:
        name, year = "بهمن", jy
    elif jm >= 7:
        name, year = "مهر", jy
    else:
        name, year = "بهمن", jy - 1

    terms = []
    for _ in range(count):
        terms.append(f"{name} {to_persian_digits(year)}")
        if name == "مهر":
            name, year = "بهمن", year - 1
        else:
            name = "مهر"
    return terms
