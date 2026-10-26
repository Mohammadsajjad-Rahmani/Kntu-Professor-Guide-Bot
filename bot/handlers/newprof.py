"""'My professor / my course is not in the list': the student types only a name.

Existing similar names are offered first (fuzzy match) so nobody creates duplicates. A new
professor starts as 'unverified' and becomes 'active' once two different students proposed it.
A new course is usable right away (status 'unverified') and the admins get approve/delete buttons.
"""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from bot.db.models import (
    PROF_ACTIVE,
    PROF_INACTIVE,
    PROF_UNVERIFIED,
    Course,
    Professor,
    ProfessorCourse,
    ProfessorSuggestion,
    Track,
    User,
)
from bot.db.session import SessionLocal
from bot.handlers.review import respond, show_step
from bot.keyboards import kb
from bot.services.notify import notify_admins
from bot.utils.filters import check_free_text
from bot.utils.text import normalize, normalize_person

router = Router()

MATCH_THRESHOLD = 80  # similarity (0-100) above which an existing name is offered
CANCEL_ROW = [("❌ انصراف", "cancel")]


class NewProfForm(StatesGroup):
    name = State()


class NewCourseForm(StatesGroup):
    name = State()


async def show_courses(target: CallbackQuery | Message, prof_id: int) -> None:
    async with SessionLocal() as session:
        prof = await session.scalar(
            select(Professor).where(Professor.id == prof_id).options(selectinload(Professor.courses))
        )
    courses = sorted((c for c in prof.courses if c.status != "hidden"), key=lambda c: c.name)
    rows = [[(c.name, f"course:{prof_id}:{c.id}")] for c in courses]
    rows.append([("✍️ درسم توی لیست نیست (خودم می‌نویسم)", f"cnew:{prof_id}")])
    rows.append([("⬅️ بازگشت", f"track:{prof.track_id}")])
    await respond(target, f"درس را انتخاب کن ({prof.name}):", kb(rows))


async def begin_form(
    target: CallbackQuery | Message, state: FSMContext, prof_id: int, course_id: int | None = None
) -> None:
    """Start the review form for this professor (and course, if one is known)."""
    await state.clear()
    await state.update_data(prof_id=prof_id, course_id=course_id, scores=[])
    await show_step(target, 0)


async def record_suggestion(session, prof: Professor, telegram_id: int) -> None:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if not user:
        user = User(telegram_id=telegram_id)
        session.add(user)
        await session.flush()
    exists = await session.scalar(
        select(ProfessorSuggestion.id).where(
            ProfessorSuggestion.professor_id == prof.id, ProfessorSuggestion.user_id == user.id
        )
    )
    if not exists:
        session.add(ProfessorSuggestion(professor_id=prof.id, user_id=user.id))
        await session.flush()
    count = await session.scalar(
        select(func.count())
        .select_from(ProfessorSuggestion)
        .where(ProfessorSuggestion.professor_id == prof.id)
    )
    prof.suggestion_count = count
    if prof.status == PROF_UNVERIFIED and count >= 2:
        prof.status = PROF_ACTIVE


async def create_and_continue(target: CallbackQuery | Message, state: FSMContext) -> None:
    data = await state.get_data()
    if "np_name" not in data:
        await respond(target, "این مرحله منقضی شده. از منوی اصلی دوباره شروع کن.", kb([[("🏠 منوی اصلی", "menu:home")]]))
        return
    track_id, name = data["np_track_id"], data["np_name"]
    norm = normalize_person(name)
    async with SessionLocal() as session:
        track = await session.get(Track, track_id)
        prof = await session.scalar(
            select(Professor).where(
                Professor.university_id == track.university_id,
                Professor.track_id == track_id,
                Professor.name_normalized == norm,
            )
        )
        created = prof is None
        if not prof:
            prof = Professor(
                university_id=track.university_id,
                track_id=track_id,
                name=name,
                name_normalized=norm,
                status=PROF_UNVERIFIED,
            )
            session.add(prof)
            await session.flush()
        await record_suggestion(session, prof, target.from_user.id)
        await session.commit()
    if created:
        await notify_admins(
            target.bot,
            f"🆕 استاد جدید پیشنهاد شد: {name}\nگرایش: {track.name}",
            kb([[("✅ تأیید استاد", f"pm:st:{prof.id}:active"), ("👁 مشاهده", f"pm:p:{prof.id}")]]),
            faculty_id=track.faculty_id,
            perm="manage",
        )
    # A new professor has no courses yet: the student types the course on the next screen
    await state.clear()
    await show_courses(target, prof.id)


# ---- handlers --------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("newprof:"))
async def ask_name(cb: CallbackQuery, state: FSMContext) -> None:
    track_id = int(cb.data.split(":")[1])
    await state.clear()
    await state.set_state(NewProfForm.name)
    await state.update_data(np_track_id=track_id)
    await respond(
        cb,
        "اسم استاد را بنویس (مثلاً: رضا طاهری).\nفقط اسم، بدون توضیح اضافه.",
        kb([CANCEL_ROW]),
    )
    await cb.answer()


@router.message(NewProfForm.name, F.text)
async def got_name(message: Message, state: FSMContext) -> None:
    name = " ".join(message.text.split())
    if not 3 <= len(name) <= 60:
        await message.answer("اسم باید بین ۳ تا ۶۰ کاراکتر باشه. دوباره بنویس.")
        return
    if check_free_text(name, max_len=60) or any(ch.isdigit() for ch in name):
        await message.answer("فقط اسم استاد را بنویس (بدون عدد، لینک یا آیدی). دوباره بنویس.")
        return
    norm = normalize_person(name)
    if not norm:
        await message.answer("اسم معتبر نیست. دوباره بنویس.")
        return

    data = await state.get_data()
    async with SessionLocal() as session:
        profs = (
            await session.scalars(
                select(Professor).where(
                    Professor.track_id == data["np_track_id"],
                    Professor.status != PROF_INACTIVE,
                )
            )
        ).all()
    scored = sorted(
        ((fuzz.token_sort_ratio(norm, p.name_normalized), p) for p in profs),
        key=lambda x: -x[0],
    )
    matches = [p for score, p in scored[:3] if score >= MATCH_THRESHOLD]
    await state.update_data(np_name=name)
    await state.set_state(None)

    if not matches:
        await create_and_continue(message, state)
        return
    rows = [[(p.name, f"npsel:{p.id}")] for p in matches]
    rows.append([("نه، استاد جدیدیه", "np:create")])
    rows.append(CANCEL_ROW)
    await message.answer("آیا منظورت یکی از این استادهاست؟", reply_markup=kb(rows))


@router.callback_query(F.data.startswith("npsel:"))
async def pick_candidate(cb: CallbackQuery, state: FSMContext) -> None:
    prof_id = int(cb.data.split(":")[1])
    async with SessionLocal() as session:
        prof = await session.get(Professor, prof_id)
        was_unverified = prof.status == PROF_UNVERIFIED
        if was_unverified:
            await record_suggestion(session, prof, cb.from_user.id)
            await session.commit()
    await state.clear()
    await show_courses(cb, prof_id)
    await cb.answer()


@router.callback_query(F.data == "np:create")
async def create_new(cb: CallbackQuery, state: FSMContext) -> None:
    await create_and_continue(cb, state)
    await cb.answer()


# ---- "my course is not in the list" ----------------------------------------------------------

async def use_course(target: CallbackQuery | Message, state: FSMContext, prof_id: int, course_id: int) -> None:
    """Link an existing course to the professor (if it is not yet) and start the form."""
    async with SessionLocal() as session:
        linked = await session.scalar(
            select(ProfessorCourse.course_id).where(
                ProfessorCourse.professor_id == prof_id, ProfessorCourse.course_id == course_id
            )
        )
        if not linked:
            session.add(ProfessorCourse(professor_id=prof_id, course_id=course_id))
            await session.commit()
    await begin_form(target, state, prof_id, course_id)


async def create_course_and_continue(target: CallbackQuery | Message, state: FSMContext) -> None:
    data = await state.get_data()
    if "nc_name" not in data:
        await respond(target, "این مرحله منقضی شده. از منوی اصلی دوباره شروع کن.", kb([[("🏠 منوی اصلی", "menu:home")]]))
        return
    prof_id, name = data["nc_prof_id"], data["nc_name"]
    async with SessionLocal() as session:
        prof = await session.get(Professor, prof_id)
        course = Course(
            university_id=prof.university_id,
            name=name,
            name_normalized=normalize(name),
            status="unverified",
        )
        session.add(course)
        await session.flush()
        session.add(ProfessorCourse(professor_id=prof_id, course_id=course.id))
        await session.commit()
    await notify_admins(
        target.bot,
        f"🆕 درس جدید پیشنهاد شد: {name}\nاستاد: {prof.name}",
        kb([[("✅ تأیید درس", f"cv:ok:{course.id}"), ("🗑 حذف", f"cv:no:{course.id}")]]),
        faculty_id=prof.track.faculty_id,
        perm="manage",
    )
    await begin_form(target, state, prof_id, course.id)


@router.callback_query(F.data.startswith("cnew:"))
async def ask_course_name(cb: CallbackQuery, state: FSMContext) -> None:
    prof_id = int(cb.data.split(":")[1])
    await state.clear()
    await state.set_state(NewCourseForm.name)
    await state.update_data(nc_prof_id=prof_id)
    await respond(
        cb,
        "اسم درس را بنویس (مثلاً: شبکه‌های بی‌سیم).\nفقط اسم درس، بدون توضیح اضافه.",
        kb([
            [("ادامه بدون ذکر درس", f"course:{prof_id}:0")],
            [("⬅️ بازگشت", f"prof:{prof_id}")],
            CANCEL_ROW,
        ]),
    )
    await cb.answer()


@router.message(NewCourseForm.name, F.text)
async def got_course_name(message: Message, state: FSMContext) -> None:
    name = " ".join(message.text.split())
    if not 2 <= len(name) <= 80:
        await message.answer("اسم درس باید بین ۲ تا ۸۰ کاراکتر باشه. دوباره بنویس.")
        return
    if check_free_text(name, max_len=80):
        await message.answer("فقط اسم درس را بنویس (بدون لینک، آیدی یا شماره). دوباره بنویس.")
        return
    norm = normalize(name)
    prof_id = (await state.get_data())["nc_prof_id"]

    async with SessionLocal() as session:
        prof = await session.get(Professor, prof_id)
        courses = (
            await session.scalars(select(Course).where(Course.university_id == prof.university_id))
        ).all()
    exact = next((c for c in courses if c.name_normalized == norm), None)
    if exact and exact.status == "hidden":
        await message.answer("این اسم درس قابل ثبت نیست. اسم دیگه‌ای بنویس یا «ادامه بدون ذکر درس» را بزن.")
        return

    visible = [c for c in courses if c.status != "hidden"]
    scored = sorted(
        ((fuzz.token_sort_ratio(norm, c.name_normalized), c) for c in visible), key=lambda x: -x[0]
    )
    matches = [c for score, c in scored[:3] if score >= 85 and c is not exact]
    await state.update_data(nc_name=name)
    await state.set_state(None)

    if exact:
        await use_course(message, state, prof_id, exact.id)
    elif matches:
        rows = [[(c.name, f"ncsel:{c.id}")] for c in matches]
        rows.append([("نه، درس جدیدیه", "nc:create")])
        rows.append(CANCEL_ROW)
        await message.answer("آیا منظورت یکی از این درس‌هاست؟", reply_markup=kb(rows))
    else:
        await create_course_and_continue(message, state)


@router.callback_query(F.data.startswith("ncsel:"))
async def pick_course_candidate(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if "nc_prof_id" not in data:
        await respond(cb, "این مرحله منقضی شده. از منوی اصلی دوباره شروع کن.", kb([[("🏠 منوی اصلی", "menu:home")]]))
    else:
        await use_course(cb, state, data["nc_prof_id"], int(cb.data.split(":")[1]))
    await cb.answer()


@router.callback_query(F.data == "nc:create")
async def create_course_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await create_course_and_continue(cb, state)
    await cb.answer()
