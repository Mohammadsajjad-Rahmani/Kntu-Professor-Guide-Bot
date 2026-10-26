"""Import professors and their courses from a CSV file.

CSV columns: name,track,courses   (courses separated by ';', may be empty)
Optional column: faculty. Rows without it go to --faculty (default: مهندسی کامپیوتر).

Usage:
    python -m scripts.import_csv data/professors.csv
    python -m scripts.import_csv data/professors.csv --faculty "مهندسی مکانیک"
    python -m scripts.import_csv data/professors.csv --university "دانشگاه خواجه نصیر"

The university defaults to the one already in the database. Safe to run repeatedly:
existing records are matched by normalized name and not duplicated.
"""
import argparse
import asyncio
import csv
from pathlib import Path

from sqlalchemy import select

from bot.db.models import Course, Professor, Track, University
from bot.db.session import (
    DEFAULT_FACULTY,
    SessionLocal,
    default_university,
    get_or_create_faculty,
    init_db,
)
from bot.utils.text import normalize, normalize_person


async def get_or_create_university(session, name: str | None) -> University:
    if not name:
        return await default_university(session)
    norm = normalize(name)
    obj = await session.scalar(select(University).where(University.name_normalized == norm))
    if not obj:
        obj = University(name=name.strip(), name_normalized=norm)
        session.add(obj)
        await session.flush()
    return obj


async def get_or_create_track(session, uni: University, faculty_id: int, name: str) -> Track:
    norm = normalize(name)
    obj = await session.scalar(
        select(Track).where(Track.university_id == uni.id, Track.name_normalized == norm)
    )
    if not obj:
        obj = Track(university_id=uni.id, faculty_id=faculty_id, name=name.strip(), name_normalized=norm)
        session.add(obj)
        await session.flush()
    elif obj.faculty_id is None:
        obj.faculty_id = faculty_id
    return obj


async def get_or_create_course(session, uni: University, name: str) -> Course:
    norm = normalize(name)
    obj = await session.scalar(
        select(Course).where(Course.university_id == uni.id, Course.name_normalized == norm)
    )
    if not obj:
        obj = Course(university_id=uni.id, name=name.strip(), name_normalized=norm)
        session.add(obj)
        await session.flush()
    return obj


async def import_csv(path: Path, university_name: str | None, default_faculty: str) -> dict[str, int]:
    await init_db()
    stats = {"professors_new": 0, "professors_existing": 0, "courses_linked": 0}

    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    async with SessionLocal() as session:
        uni = await get_or_create_university(session, university_name)

        for i, row in enumerate(rows, start=2):
            name = (row.get("name") or "").strip()
            track_name = (row.get("track") or "").strip()
            if not name or not track_name:
                print(f"line {i}: skipped (name or track missing)")
                continue

            faculty_name = (row.get("faculty") or "").strip() or default_faculty
            faculty = await get_or_create_faculty(session, uni.id, faculty_name)
            track = await get_or_create_track(session, uni, faculty.id, track_name)
            if track.faculty_id != faculty.id:
                print(f"line {i}: track '{track_name}' already belongs to another faculty, kept there")

            norm = normalize_person(name)
            prof = await session.scalar(
                select(Professor).where(
                    Professor.university_id == uni.id,
                    Professor.track_id == track.id,
                    Professor.name_normalized == norm,
                )
            )
            if prof:
                stats["professors_existing"] += 1
            else:
                prof = Professor(
                    university_id=uni.id, track_id=track.id, name=name, name_normalized=norm
                )
                session.add(prof)
                await session.flush()
                stats["professors_new"] += 1

            # Load the relationship explicitly (async sessions can't lazy-load)
            await session.refresh(prof, ["courses"])
            existing_ids = {c.id for c in prof.courses}
            for cname in filter(None, (c.strip() for c in (row.get("courses") or "").split(";"))):
                course = await get_or_create_course(session, uni, cname)
                if course.id not in existing_ids:
                    prof.courses.append(course)
                    existing_ids.add(course.id)
                    stats["courses_linked"] += 1

        await session.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--university", default=None)
    parser.add_argument("--faculty", default=DEFAULT_FACULTY)
    args = parser.parse_args()
    print(asyncio.run(import_csv(args.csv_path, args.university, args.faculty)))


if __name__ == "__main__":
    main()
