"""Who may do what.

Owners are the people in ADMIN_IDS: they can do everything. Collaborators are students the
owner approved; each grant is tied to one faculty and carries two separate permissions:
  - MODERATE: approve / reject / hide reviews of that faculty
  - MANAGE:   tracks, professors and courses of that faculty
Banning students, collaborators, statistics and publishing stay owner-only.
"""
from aiogram.types import CallbackQuery
from sqlalchemy import select

from bot.config import settings
from bot.db.models import Collaborator, Professor, ProfessorCourse, Review, Track, User
from bot.db.session import SessionLocal

MODERATE, MANAGE = "moderate", "manage"


def is_owner(telegram_id: int) -> bool:
    return telegram_id in settings.admin_ids


async def faculties_for(telegram_id: int, perm: str) -> set[int] | None:
    """Faculty ids this person may act on. None means all of them (owner)."""
    if is_owner(telegram_id):
        return None
    flag = Collaborator.can_moderate if perm == MODERATE else Collaborator.can_manage
    async with SessionLocal() as session:
        ids = await session.scalars(
            select(Collaborator.faculty_id)
            .join(User, User.id == Collaborator.user_id)
            .where(User.telegram_id == telegram_id, flag.is_(True))
        )
        return set(ids.all())


async def can(telegram_id: int, perm: str, faculty_id: int | None) -> bool:
    scope = await faculties_for(telegram_id, perm)
    return scope is None or (faculty_id is not None and faculty_id in scope)


async def is_staff(telegram_id: int) -> bool:
    if is_owner(telegram_id):
        return True
    return bool(await faculties_for(telegram_id, MODERATE) or await faculties_for(telegram_id, MANAGE))


def review_scope(scope: set[int] | None) -> list:
    """WHERE conditions limiting Review rows to the given faculties (none for owners)."""
    if scope is None:
        return []
    return [
        Review.professor_id.in_(
            select(Professor.id).join(Track, Track.id == Professor.track_id).where(Track.faculty_id.in_(scope))
        )
    ]


# ---- which faculty does something belong to? ------------------------------------------------

async def faculty_of_review(review_id: int) -> int | None:
    async with SessionLocal() as session:
        return await session.scalar(
            select(Track.faculty_id)
            .join(Professor, Professor.track_id == Track.id)
            .join(Review, Review.professor_id == Professor.id)
            .where(Review.id == review_id)
        )


async def faculty_of_prof(prof_id: int) -> int | None:
    async with SessionLocal() as session:
        return await session.scalar(
            select(Track.faculty_id)
            .join(Professor, Professor.track_id == Track.id)
            .where(Professor.id == prof_id)
        )


async def faculty_of_track(track_id: int) -> int | None:
    async with SessionLocal() as session:
        return await session.scalar(select(Track.faculty_id).where(Track.id == track_id))


async def faculties_of_course(course_id: int) -> set[int]:
    """A course is shared between professors, so it can touch several faculties."""
    async with SessionLocal() as session:
        ids = await session.scalars(
            select(Track.faculty_id)
            .join(Professor, Professor.track_id == Track.id)
            .join(ProfessorCourse, ProfessorCourse.professor_id == Professor.id)
            .where(ProfessorCourse.course_id == course_id)
        )
        return {i for i in ids.all() if i}


async def deny_scoped(
    cb: CallbackQuery,
    perm: str,
    *,
    faculty_id: int | None = None,
    track_id: int | None = None,
    prof_id: int | None = None,
    review_id: int | None = None,
    course_id: int | None = None,
) -> bool:
    """True (after telling the user) when they may not act on this item."""
    uid = cb.from_user.id
    if is_owner(uid):
        return False
    if track_id is not None:
        faculty_id = await faculty_of_track(track_id)
    elif prof_id is not None:
        faculty_id = await faculty_of_prof(prof_id)
    elif review_id is not None:
        faculty_id = await faculty_of_review(review_id)
    if course_id is not None:
        allowed = any([await can(uid, perm, f) for f in await faculties_of_course(course_id)])
    else:
        allowed = await can(uid, perm, faculty_id)
    if allowed:
        return False
    await cb.answer("دسترسی نداری.", show_alert=True)
    return True
