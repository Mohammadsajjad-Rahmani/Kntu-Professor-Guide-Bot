"""Turns review data into the channel message format."""
import re

from bot.utils.text import normalize, normalize_person

SCORE_LABELS = [
    "پیوستگی و یکپارچگی تدریس",
    "دانش عمومی",
    "توانایی انتقال مطالب و نکات مهم درس",
    "نحوه مدیریت کلاس (نظم و زمان)",
    "پاسخگویی (حضوری و غیرحضوری)",
    "آداب و رفتار اجتماعی با دانشجویان",
]


def hashtag(name: str) -> str:
    """'شبکه‌های بی‌سیم (پیشرفته)' -> '#شبکه_های_بی_سیم_پیشرفته'. Empty string if nothing is left."""
    cleaned = re.sub(r"[^\w\s]", " ", normalize(name))
    parts = cleaned.split()
    return "#" + "_".join(parts) if parts else ""


def _block(title: str, value: str) -> str:
    return f"{title}\n┘ {value}"


def format_review(
    prof_name: str,
    track_name: str,
    course_name: str | None,
    data: dict,
    faculty_name: str | None = None,
) -> str:
    """`data` keys: term, resources, attendance, sufficient (bool), grading,
    scores (list of 6 ints), contact (optional), comment (optional)."""
    place_tags = [hashtag(n) for n in (faculty_name, track_name) if n]
    if faculty_name and normalize(faculty_name) == normalize(track_name):
        place_tags = place_tags[:1]
    head = ["🧑‍🏫" + f" دکتر{prof_name}", "🏫 " + " ".join(t for t in place_tags if t)]
    if course_name:
        head.append(f"📒 {course_name}")

    scores = "\n".join(
        f"┤ {label}: {value}" for label, value in zip(SCORE_LABELS, data["scores"])
    )
    parts = [
        "\n".join(head),
        _block("منابع آموزش", data["resources"]),
        _block("حضور و غیاب", data["attendance"]),
        _block(
            "منابع معرفی شده برای امتحان کافی است؟",
            "بله" if data["sufficient"] else "خیر",
        ),
        _block("وضعیت نمره دادن:", data["grading"]),
        "ارزیابی دانشجو:\n" + scores,
        _block("راه ارتباطی:", data.get("contact") or "چیزی ندارم"),
        _block("ترمی که دانشجو با این استاد کلاس داشته:", data["term"]),
    ]
    if data.get("comment"):
        parts.append(_block("توضیحات:", data["comment"]))

    # Search tags: the professor (without a title such as 'دکتر') and the course
    search_tags = [hashtag(normalize_person(prof_name))]
    if course_name:
        search_tags.append(hashtag(course_name))
    search_tags = [t for t in search_tags if t]
    if search_tags:
        parts.append("🔎 " + " ".join(search_tags))
    return "\n\n".join(parts)


def format_review_for(prof, course, data: dict) -> str:
    """Same as format_review, taking the Professor (with .track.faculty loaded) and Course objects."""
    faculty = prof.track.faculty
    return format_review(
        prof.name,
        prof.track.name,
        course.name if course else None,
        data,
        faculty_name=faculty.name if faculty else None,
    )
