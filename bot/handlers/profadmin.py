"""Admin: manage professors and their courses.

Verify / deactivate, rename, add a course, add a professor, and merge duplicates
(reviews and courses move to the professor you keep).
"""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from bot.db.models import (
    PROF_ACTIVE,
    PROF_INACTIVE,
    PROF_UNVERIFIED,
    Course,
    Faculty,
    Material,
    Professor,
    ProfessorCourse,
    ProfessorSuggestion,
    Review,
    Track,
)
from bot.db.session import SessionLocal, default_university
from bot.handlers.admin import deny
from bot.handlers.review import respond
from bot.keyboards import kb
from bot.services.access import (
    MANAGE,
    can,
    deny_scoped,
    faculties_for,
    faculty_of_prof,
    faculty_of_track,
    is_owner,
)
from bot.services.formatting import hashtag
from bot.utils.text import normalize, normalize_person

router = Router()
PAGE_SIZE = 8
STATUS_FA = {PROF_ACTIVE: "فعال", PROF_INACTIVE: "غیرفعال", PROF_UNVERIFIED: "تأییدنشده"}
STATUS_MARK = {PROF_ACTIVE: "✅", PROF_INACTIVE: "⏸", PROF_UNVERIFIED: "⚠️"}
BACK_TO_PANEL = [("⬅️ پنل ادمین", "menu:admin")]


class ProfAdminForm(StatesGroup):
    rename = State()
    add_course = State()
    add_prof = State()
    add_faculty = State()
    add_track = State()
    rename_faculty = State()
    rename_track = State()


def clean_name(text: str, min_len: int = 2, max_len: int = 60) -> str | None:
    name = " ".join(text.split())
    return name if min_len <= len(name) <= max_len else None


def is_admin_message(message: Message) -> bool:
    """Owner-only text input (new faculty, faculty rename)."""
    return is_owner(message.from_user.id)


# ---- faculties -------------------------------------------------------------------------------

@router.callback_query(F.data == "pm:facs")
async def faculties(cb: CallbackQuery, state: FSMContext) -> None:
    scope = await faculties_for(cb.from_user.id, MANAGE)
    if scope is not None and not scope:
        await cb.answer("دسترسی نداری.", show_alert=True)
        return
    await state.clear()
    query = (
        select(Faculty, func.count(Track.id))
        .outerjoin(Track, Track.faculty_id == Faculty.id)
        .group_by(Faculty.id)
        .order_by(Faculty.name)
    )
    if scope is not None:
        query = query.where(Faculty.id.in_(scope))
    async with SessionLocal() as session:
        rows = (await session.execute(query)).all()
    buttons = [[(f"{f.name} ({n} رشته)", f"pm:f:{f.id}")] for f, n in rows]
    if is_owner(cb.from_user.id):
        buttons.append([("➕ افزودن دانشکده", "pm:newfac")])
    buttons.append(BACK_TO_PANEL)
    await respond(cb, "👨‍🏫 مدیریت دانشکده‌ها و اساتید\nدانشکده را انتخاب کن:", kb(buttons))
    await cb.answer()


async def show_faculty(target: CallbackQuery | Message, faculty_id: int) -> None:
    async with SessionLocal() as session:
        fac = await session.get(Faculty, faculty_id)
        if not fac:
            await respond(target, "این دانشکده پیدا نشد.", kb([[("⬅️ دانشکده‌ها", "pm:facs")]]))
            return
        rows = (
            await session.execute(
                select(Track, func.count(Professor.id))
                .outerjoin(Professor, Professor.track_id == Track.id)
                .where(Track.faculty_id == faculty_id)
                .group_by(Track.id)
                .order_by(Track.name)
            )
        ).all()
    buttons = [[(f"{t.name} ({n} استاد)", f"pm:t:{t.id}:0")] for t, n in rows]
    buttons.append([("➕ افزودن رشته یا گروه", f"pm:newtr:{faculty_id}")])
    if is_owner(target.from_user.id):  # renaming changes the hashtag, so owners only
        buttons.append([("✏️ ویرایش نام دانشکده", f"pm:rnf:{faculty_id}")])
    buttons.append([("⬅️ دانشکده‌ها", "pm:facs")])
    text = f"🏛 {fac.name}\nهشتگ در کانال: {hashtag(fac.name)}\n\n"
    text += "رشته‌ها و گروه‌ها:" if rows else "هنوز رشته‌ای ندارد؛ با «افزودن رشته یا گروه» بساز."
    await respond(target, text, kb(buttons))


@router.callback_query(F.data.startswith("pm:f:"))
async def faculty_page(cb: CallbackQuery, state: FSMContext) -> None:
    if await deny_scoped(cb, MANAGE, faculty_id=int(cb.data.split(":")[2])):
        return
    await state.clear()
    await show_faculty(cb, int(cb.data.split(":")[2]))
    await cb.answer()


@router.callback_query(F.data == "pm:newfac")
async def ask_new_faculty(cb: CallbackQuery, state: FSMContext) -> None:
    if await deny(cb):
        return
    await state.set_state(ProfAdminForm.add_faculty)
    await respond(
        cb,
        "اسم دانشکدهٔ جدید را بنویس (مثلاً: مهندسی مکانیک):",
        kb([[("↩️ انصراف", "pm:facs")]]),
    )
    await cb.answer()


@router.message(ProfAdminForm.add_faculty, F.text)
async def got_new_faculty(message: Message, state: FSMContext) -> None:
    if not is_admin_message(message):
        return
    name = clean_name(message.text)
    if not name:
        await message.answer("اسم باید بین ۲ تا ۶۰ کاراکتر باشه. دوباره بنویس.")
        return
    norm = normalize(name)
    async with SessionLocal() as session:
        uni = await default_university(session)
        duplicate = await session.scalar(
            select(Faculty.id).where(Faculty.university_id == uni.id, Faculty.name_normalized == norm)
        )
        if duplicate:
            await message.answer("این دانشکده قبلاً ثبت شده. اسم دیگه‌ای بنویس یا انصراف بده.")
            return
        fac = Faculty(university_id=uni.id, name=name, name_normalized=norm)
        session.add(fac)
        await session.commit()
    await state.clear()
    await show_faculty(message, fac.id)


@router.callback_query(F.data.startswith("pm:rnf:"))
async def ask_rename_faculty(cb: CallbackQuery, state: FSMContext) -> None:
    if await deny(cb):
        return
    faculty_id = int(cb.data.split(":")[2])
    await state.set_state(ProfAdminForm.rename_faculty)
    await state.update_data(pm_fid=faculty_id)
    await respond(
        cb,
        "اسم جدید دانشکده را بنویس.\n(پیام‌های قبلی کانال با هشتگ قدیمی می‌مونن.)",
        kb([[("↩️ انصراف", f"pm:f:{faculty_id}")]]),
    )
    await cb.answer()


@router.message(ProfAdminForm.rename_faculty, F.text)
async def got_rename_faculty(message: Message, state: FSMContext) -> None:
    if not is_admin_message(message):
        return
    name = clean_name(message.text)
    if not name:
        await message.answer("اسم باید بین ۲ تا ۶۰ کاراکتر باشه. دوباره بنویس.")
        return
    faculty_id = (await state.get_data())["pm_fid"]
    norm = normalize(name)
    async with SessionLocal() as session:
        fac = await session.get(Faculty, faculty_id)
        duplicate = await session.scalar(
            select(Faculty.id).where(
                Faculty.university_id == fac.university_id,
                Faculty.name_normalized == norm,
                Faculty.id != faculty_id,
            )
        )
        if duplicate:
            await message.answer("دانشکدهٔ دیگری با این اسم هست. اسم دیگه‌ای بنویس.")
            return
        fac.name = name
        fac.name_normalized = norm
        await session.commit()
    await state.clear()
    await show_faculty(message, faculty_id)


# ---- tracks ----------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("pm:newtr:"))
async def ask_new_track(cb: CallbackQuery, state: FSMContext) -> None:
    if await deny_scoped(cb, MANAGE, faculty_id=int(cb.data.split(":")[2])):
        return
    faculty_id = int(cb.data.split(":")[2])
    await state.set_state(ProfAdminForm.add_track)
    await state.update_data(pm_fid=faculty_id)
    await respond(
        cb,
        "اسم رشته یا گروه جدید را بنویس (مثلاً: طراحی کاربردی):",
        kb([[("↩️ انصراف", f"pm:f:{faculty_id}")]]),
    )
    await cb.answer()


@router.message(ProfAdminForm.add_track, F.text)
async def got_new_track(message: Message, state: FSMContext) -> None:
    name = clean_name(message.text)
    if not name:
        await message.answer("اسم باید بین ۲ تا ۶۰ کاراکتر باشه. دوباره بنویس.")
        return
    faculty_id = (await state.get_data())["pm_fid"]
    if not await can(message.from_user.id, MANAGE, faculty_id):
        return
    norm = normalize(name)
    async with SessionLocal() as session:
        fac = await session.get(Faculty, faculty_id)
        duplicate = await session.scalar(
            select(Track.id).where(Track.university_id == fac.university_id, Track.name_normalized == norm)
        )
        if duplicate:
            await message.answer(
                "رشته یا گروهی با این اسم قبلاً ثبت شده (حتی در دانشکدهٔ دیگر).\n"
                "اسم دیگه‌ای بنویس، مثلاً با اضافه‌کردن اسم دانشکده."
            )
            return
        session.add(
            Track(university_id=fac.university_id, faculty_id=faculty_id, name=name, name_normalized=norm)
        )
        await session.commit()
    await state.clear()
    await show_faculty(message, faculty_id)


@router.callback_query(F.data.startswith("pm:rnt:"))
async def ask_rename_track(cb: CallbackQuery, state: FSMContext) -> None:
    if await deny_scoped(cb, MANAGE, track_id=int(cb.data.split(":")[2])):
        return
    track_id = int(cb.data.split(":")[2])
    await state.set_state(ProfAdminForm.rename_track)
    await state.update_data(pm_tid=track_id)
    await respond(
        cb,
        "اسم جدید رشته یا گروه را بنویس.\n(پیام‌های قبلی کانال با هشتگ قدیمی می‌مونن.)",
        kb([[("↩️ انصراف", f"pm:t:{track_id}:0")]]),
    )
    await cb.answer()


@router.message(ProfAdminForm.rename_track, F.text)
async def got_rename_track(message: Message, state: FSMContext) -> None:
    name = clean_name(message.text)
    if not name:
        await message.answer("اسم باید بین ۲ تا ۶۰ کاراکتر باشه. دوباره بنویس.")
        return
    track_id = (await state.get_data())["pm_tid"]
    if not await can(message.from_user.id, MANAGE, await faculty_of_track(track_id)):
        return
    norm = normalize(name)
    async with SessionLocal() as session:
        track = await session.get(Track, track_id)
        duplicate = await session.scalar(
            select(Track.id).where(
                Track.university_id == track.university_id,
                Track.name_normalized == norm,
                Track.id != track_id,
            )
        )
        if duplicate:
            await message.answer("رشته یا گروه دیگری با این اسم هست. اسم دیگه‌ای بنویس.")
            return
        track.name = name
        track.name_normalized = norm
        faculty_id = track.faculty_id
        await session.commit()
    await state.clear()
    if faculty_id:
        await show_faculty(message, faculty_id)
    else:
        await respond(message, "✅ انجام شد.", kb([[("⬅️ دانشکده‌ها", "pm:facs")]]))


@router.callback_query(F.data.startswith("pm:t:"))
async def track_professors(cb: CallbackQuery, state: FSMContext) -> None:
    if await deny_scoped(cb, MANAGE, track_id=int(cb.data.split(":")[2])):
        return
    await state.clear()
    _, _, track_id, page = cb.data.split(":")
    track_id, page = int(track_id), int(page)
    async with SessionLocal() as session:
        track = await session.get(Track, track_id)
        total = await session.scalar(
            select(func.count()).select_from(Professor).where(Professor.track_id == track_id)
        )
        profs = (
            await session.scalars(
                select(Professor)
                .where(Professor.track_id == track_id)
                .order_by(Professor.name)
                .offset(page * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
        ).all()
    buttons = [[(f"{STATUS_MARK.get(p.status, '')} {p.name}", f"pm:p:{p.id}")] for p in profs]
    nav = []
    if page > 0:
        nav.append(("⬅️ قبلی", f"pm:t:{track_id}:{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(("بعدی ➡️", f"pm:t:{track_id}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([("➕ افزودن استاد", f"pm:new:{track_id}")])
    buttons.append([("✏️ ویرایش نام رشته", f"pm:rnt:{track_id}")])
    buttons.append(
        [("⬅️ بازگشت", f"pm:f:{track.faculty_id}" if track.faculty_id else "pm:facs")]
    )
    faculty = f"{track.faculty.name} ← " if track.faculty else ""
    await respond(
        cb,
        f"{faculty}{track.name} ({total} استاد)\nهشتگ رشته: {hashtag(track.name)}\n"
        "✅ فعال   ⚠️ تأییدنشده   ⏸ غیرفعال",
        kb(buttons),
    )
    await cb.answer()


# ---- professor page --------------------------------------------------------------------------

async def show_prof(target: CallbackQuery | Message, prof_id: int) -> None:
    async with SessionLocal() as session:
        prof = await session.scalar(
            select(Professor)
            .where(Professor.id == prof_id)
            .options(selectinload(Professor.track), selectinload(Professor.courses))
        )
        if not prof:
            await respond(target, "این استاد پیدا نشد.", kb([BACK_TO_PANEL]))
            return
        reviews = await session.scalar(
            select(func.count()).select_from(Review).where(Review.professor_id == prof_id)
        )
    marks = {"unverified": " ⚠️", "hidden": " 🚫"}
    courses = "\n".join(f"• {c.name}{marks.get(c.status, '')}" for c in prof.courses) or "درسی ثبت نشده"
    text = (
        f"👨‍🏫 {prof.name}\n"
        + (f"دانشکده: {prof.track.faculty.name}\n" if prof.track.faculty else "")
        + f"رشته: {prof.track.name}\n"
        f"وضعیت: {STATUS_FA.get(prof.status, prof.status)}\n"
        f"تعداد نظرها: {reviews}\n"
        f"پیشنهاددهنده‌ها: {prof.suggestion_count}\n\n"
        f"درس‌ها:\n{courses}"
    )
    rows = []
    if prof.status == PROF_UNVERIFIED:
        rows.append([("✅ تأیید استاد", f"pm:st:{prof.id}:{PROF_ACTIVE}")])
    elif prof.status == PROF_ACTIVE:
        rows.append([("⏸ غیرفعال کردن", f"pm:st:{prof.id}:{PROF_INACTIVE}")])
    else:
        rows.append([("▶️ فعال کردن", f"pm:st:{prof.id}:{PROF_ACTIVE}")])
    rows.append([("✏️ ویرایش نام", f"pm:rn:{prof.id}"), ("➕ افزودن درس", f"pm:ac:{prof.id}")])
    rows.append([("🔀 ادغام با استاد دیگر", f"pm:mg:{prof.id}")])
    rows.append([("⬅️ بازگشت", f"pm:t:{prof.track_id}:0")])
    await respond(target, text, kb(rows))


@router.callback_query(F.data.startswith("pm:p:"))
async def prof_page(cb: CallbackQuery, state: FSMContext) -> None:
    if await deny_scoped(cb, MANAGE, prof_id=int(cb.data.split(":")[2])):
        return
    await state.clear()
    await show_prof(cb, int(cb.data.split(":")[2]))
    await cb.answer()


@router.callback_query(F.data.startswith("pm:st:"))
async def set_status(cb: CallbackQuery) -> None:
    if await deny_scoped(cb, MANAGE, prof_id=int(cb.data.split(":")[2])):
        return
    _, _, prof_id, status = cb.data.split(":")
    if status not in (PROF_ACTIVE, PROF_INACTIVE):
        await cb.answer()
        return
    async with SessionLocal() as session:
        prof = await session.get(Professor, int(prof_id))
        prof.status = status
        await session.commit()
    await show_prof(cb, int(prof_id))
    await cb.answer("انجام شد.")


# ---- courses typed by students ---------------------------------------------------------------

@router.callback_query(F.data.startswith("cv:"))
async def course_verdict(cb: CallbackQuery) -> None:
    """Approve or delete a course a student typed (⚠️ in the professor page)."""
    if await deny_scoped(cb, MANAGE, course_id=int(cb.data.split(":")[2])):
        return
    _, verdict, course_id = cb.data.split(":")
    async with SessionLocal() as session:
        course = await session.get(Course, int(course_id))
        if verdict == "ok":
            course.status = "active"
            text = f"✅ درس «{course.name}» تأیید شد."
        else:
            course.status = "hidden"
            text = f"🗑 درس «{course.name}» حذف شد و دیگه توی لیست دانشجوها نمایش داده نمی‌شه."
        await session.commit()
    await respond(cb, text, kb([BACK_TO_PANEL]))
    await cb.answer()


# ---- rename / add course / add professor (text input) ----------------------------------------

def cancel_to(prof_id: int):
    return kb([[("↩️ انصراف", f"pm:p:{prof_id}")]])


@router.callback_query(F.data.startswith("pm:rn:"))
async def ask_rename(cb: CallbackQuery, state: FSMContext) -> None:
    if await deny_scoped(cb, MANAGE, prof_id=int(cb.data.split(":")[2])):
        return
    prof_id = int(cb.data.split(":")[2])
    await state.set_state(ProfAdminForm.rename)
    await state.update_data(pm_pid=prof_id)
    await respond(cb, "اسم جدید استاد را بنویس:", cancel_to(prof_id))
    await cb.answer()


@router.message(ProfAdminForm.rename, F.text)
async def got_rename(message: Message, state: FSMContext) -> None:
    name = " ".join(message.text.split())
    if not 3 <= len(name) <= 60:
        await message.answer("اسم باید بین ۳ تا ۶۰ کاراکتر باشه. دوباره بنویس.")
        return
    prof_id = (await state.get_data())["pm_pid"]
    if not await can(message.from_user.id, MANAGE, await faculty_of_prof(prof_id)):
        return
    norm = normalize_person(name)
    async with SessionLocal() as session:
        prof = await session.get(Professor, prof_id)
        duplicate = await session.scalar(
            select(Professor.id).where(
                Professor.university_id == prof.university_id,
                Professor.track_id == prof.track_id,
                Professor.name_normalized == norm,
                Professor.id != prof_id,
            )
        )
        if duplicate:
            await message.answer("استاد دیگری با این اسم هست. برای یکی‌کردن، از «ادغام» استفاده کن.")
            return
        prof.name = name
        prof.name_normalized = norm
        await session.commit()
    await state.clear()
    await show_prof(message, prof_id)


@router.callback_query(F.data.startswith("pm:ac:"))
async def ask_course(cb: CallbackQuery, state: FSMContext) -> None:
    if await deny_scoped(cb, MANAGE, prof_id=int(cb.data.split(":")[2])):
        return
    prof_id = int(cb.data.split(":")[2])
    await state.set_state(ProfAdminForm.add_course)
    await state.update_data(pm_pid=prof_id)
    await respond(cb, "اسم درس را بنویس (اگه قبلاً ثبت شده باشه، به همون وصل می‌شه):", cancel_to(prof_id))
    await cb.answer()


@router.message(ProfAdminForm.add_course, F.text)
async def got_course(message: Message, state: FSMContext) -> None:
    name = " ".join(message.text.split())
    if not 2 <= len(name) <= 100:
        await message.answer("اسم درس باید بین ۲ تا ۱۰۰ کاراکتر باشه. دوباره بنویس.")
        return
    prof_id = (await state.get_data())["pm_pid"]
    if not await can(message.from_user.id, MANAGE, await faculty_of_prof(prof_id)):
        return
    norm = normalize(name)
    async with SessionLocal() as session:
        prof = await session.get(Professor, prof_id)
        course = await session.scalar(
            select(Course).where(
                Course.university_id == prof.university_id, Course.name_normalized == norm
            )
        )
        if not course:
            course = Course(university_id=prof.university_id, name=name, name_normalized=norm)
            session.add(course)
            await session.flush()
        linked = await session.scalar(
            select(ProfessorCourse.course_id).where(
                ProfessorCourse.professor_id == prof_id, ProfessorCourse.course_id == course.id
            )
        )
        if not linked:
            session.add(ProfessorCourse(professor_id=prof_id, course_id=course.id))
        await session.commit()
    await state.clear()
    await show_prof(message, prof_id)


@router.callback_query(F.data.startswith("pm:new:"))
async def ask_new_prof(cb: CallbackQuery, state: FSMContext) -> None:
    if await deny_scoped(cb, MANAGE, track_id=int(cb.data.split(":")[2])):
        return
    track_id = int(cb.data.split(":")[2])
    await state.set_state(ProfAdminForm.add_prof)
    await state.update_data(pm_tid=track_id)
    await respond(cb, "اسم استاد جدید را بنویس:", kb([[("↩️ انصراف", f"pm:t:{track_id}:0")]]))
    await cb.answer()


@router.message(ProfAdminForm.add_prof, F.text)
async def got_new_prof(message: Message, state: FSMContext) -> None:
    name = " ".join(message.text.split())
    if not 3 <= len(name) <= 60:
        await message.answer("اسم باید بین ۳ تا ۶۰ کاراکتر باشه. دوباره بنویس.")
        return
    track_id = (await state.get_data())["pm_tid"]
    if not await can(message.from_user.id, MANAGE, await faculty_of_track(track_id)):
        return
    norm = normalize_person(name)
    async with SessionLocal() as session:
        track = await session.get(Track, track_id)
        duplicate = await session.scalar(
            select(Professor.id).where(
                Professor.university_id == track.university_id,
                Professor.track_id == track_id,
                Professor.name_normalized == norm,
            )
        )
        if duplicate:
            await message.answer("این استاد توی همین گرایش هست. اسم دیگه‌ای بنویس یا انصراف بده.")
            return
        prof = Professor(
            university_id=track.university_id,
            track_id=track_id,
            name=name,
            name_normalized=norm,
            status=PROF_ACTIVE,
        )
        session.add(prof)
        await session.commit()
    await state.clear()
    await show_prof(message, prof.id)


# ---- merge -----------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("pm:mg:"))
async def merge_pick(cb: CallbackQuery) -> None:
    if await deny_scoped(cb, MANAGE, prof_id=int(cb.data.split(":")[2])):
        return
    prof_id = int(cb.data.split(":")[2])
    async with SessionLocal() as session:
        src = await session.get(Professor, prof_id)
        others = (
            await session.scalars(
                select(Professor)
                .where(Professor.track_id == src.track_id, Professor.id != prof_id)
                .order_by(Professor.name)
            )
        ).all()
    buttons = [[(p.name, f"pm:mgc:{prof_id}:{p.id}")] for p in others]
    buttons.append([("↩️ انصراف", f"pm:p:{prof_id}")])
    await respond(
        cb,
        f"«{src.name}» با کدام استاد ادغام شود؟\n(استاد انتخابی می‌ماند و «{src.name}» حذف می‌شود)",
        kb(buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("pm:mgc:"))
async def merge_confirm(cb: CallbackQuery) -> None:
    if await deny_scoped(cb, MANAGE, prof_id=int(cb.data.split(":")[2])):
        return
    _, _, src_id, dst_id = cb.data.split(":")
    async with SessionLocal() as session:
        src = await session.get(Professor, int(src_id))
        dst = await session.get(Professor, int(dst_id))
    await respond(
        cb,
        f"«{src.name}» در «{dst.name}» ادغام شود؟\n"
        f"نظرها و درس‌های «{src.name}» به «{dst.name}» منتقل می‌شود و «{src.name}» حذف می‌شود. "
        "این کار برگشت‌پذیر نیست.",
        kb([
            [("✅ بله، ادغام کن", f"pm:mgy:{src_id}:{dst_id}")],
            [("↩️ نه", f"pm:p:{src_id}")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("pm:mgy:"))
async def merge_do(cb: CallbackQuery) -> None:
    if await deny_scoped(cb, MANAGE, prof_id=int(cb.data.split(":")[2])):
        return
    _, _, src_id, dst_id = cb.data.split(":")
    src_id, dst_id = int(src_id), int(dst_id)
    if src_id == dst_id:
        await cb.answer()
        return
    async with SessionLocal() as session:
        try:
            await session.execute(
                update(Review).where(Review.professor_id == src_id).values(professor_id=dst_id)
            )
            await session.execute(
                update(Material).where(Material.professor_id == src_id).values(professor_id=dst_id)
            )
            src_courses = set(
                (
                    await session.scalars(
                        select(ProfessorCourse.course_id).where(ProfessorCourse.professor_id == src_id)
                    )
                ).all()
            )
            dst_courses = set(
                (
                    await session.scalars(
                        select(ProfessorCourse.course_id).where(ProfessorCourse.professor_id == dst_id)
                    )
                ).all()
            )
            for course_id in src_courses - dst_courses:
                session.add(ProfessorCourse(professor_id=dst_id, course_id=course_id))
            await session.flush()
            await session.execute(delete(ProfessorCourse).where(ProfessorCourse.professor_id == src_id))
            await session.execute(
                delete(ProfessorSuggestion).where(ProfessorSuggestion.professor_id == src_id)
            )
            await session.execute(delete(Professor).where(Professor.id == src_id))
            await session.commit()
        except IntegrityError:
            await session.rollback()
            await respond(
                cb,
                "ادغام انجام نشد: یک دانشجو برای هر دو استاد نظر هم‌درس و هم‌ترم ثبت کرده.\n"
                "یکی از آن نظرها را از «همهٔ نظرها» مخفی کن و دوباره تلاش کن.",
                kb([[("⬅️ بازگشت", f"pm:p:{src_id}")]]),
            )
            await cb.answer()
            return
    await respond(cb, "✅ ادغام انجام شد.", kb([[("👁 مشاهدهٔ استاد", f"pm:p:{dst_id}")]]))
    await cb.answer()
