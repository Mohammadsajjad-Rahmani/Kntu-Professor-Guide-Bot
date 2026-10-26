"""Collaborators: students apply, the owner approves and chooses what each may do.

A collaborator is tied to one faculty and has two independent permissions:
  📝 moderate: approve / reject / hide reviews of that faculty
  🗂 manage:   tracks, professors and courses of that faculty
Only owners (ADMIN_IDS) see the requests and the collaborator list.
"""
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.config import settings
from bot.db.models import Collaborator, CollabRequest, Faculty, User
from bot.db.session import SessionLocal
from bot.handlers.admin import deny
from bot.handlers.review import respond
from bot.keyboards import HOME_ROW, kb

log = logging.getLogger(__name__)
router = Router()
BACK_TO_PANEL = [("⬅️ پنل ادمین", "menu:admin")]
BACK_TO_COLLAB = [("⬅️ همکاران", "collab:home")]
PERMS = {"m": "moderate", "p": "manage", "b": "both"}


class CollabForm(StatesGroup):
    note = State()


def perm_text(c: Collaborator) -> str:
    parts = []
    if c.can_moderate:
        parts.append("📝 بررسی نظرها")
    if c.can_manage:
        parts.append("🗂 مدیریت اساتید و درس‌ها")
    return " + ".join(parts) or "بدون دسترسی"


def display_name(user) -> str:
    name = user.full_name
    return f"{name} (@{user.username})" if user.username else name


# ---- student: apply --------------------------------------------------------------------------

@router.callback_query(F.data == "menu:collab")
async def collab_start(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with SessionLocal() as session:
        faculties = (await session.scalars(select(Faculty).order_by(Faculty.name))).all()
    if not faculties:
        await respond(cb, "هنوز دانشکده‌ای ثبت نشده.", kb([HOME_ROW]))
    else:
        rows = [[(f.name, f"collab:f:{f.id}")] for f in faculties] + [HOME_ROW]
        await respond(
            cb,
            "🤝 همکاری با ما\n\n"
            "همکارها به بررسی نظرها یا مدیریت لیست اساتید و درس‌های دانشکدهٔ خودشان کمک می‌کنند.\n"
            "دانشکده‌ای که می‌خوای توش کمک کنی را انتخاب کن:",
            kb(rows),
        )
    await cb.answer()


@router.callback_query(F.data.startswith("collab:f:"))
async def collab_pick_faculty(cb: CallbackQuery, state: FSMContext) -> None:
    faculty_id = int(cb.data.split(":")[2])
    async with SessionLocal() as session:
        faculty = await session.get(Faculty, faculty_id)
        user = await session.scalar(select(User).where(User.telegram_id == cb.from_user.id))
        if user:
            has = await session.scalar(
                select(Collaborator.id).where(
                    Collaborator.user_id == user.id, Collaborator.faculty_id == faculty_id
                )
            )
            pending = await session.scalar(
                select(CollabRequest.id).where(
                    CollabRequest.user_id == user.id,
                    CollabRequest.faculty_id == faculty_id,
                    CollabRequest.status == "pending",
                )
            )
            if has or pending:
                msg = (
                    "تو همین الان همکار این دانشکده‌ای."
                    if has
                    else "درخواستت برای این دانشکده قبلاً ثبت شده و منتظر بررسیه."
                )
                await respond(cb, msg, kb([[("⬅️ دانشکده‌ها", "menu:collab")], HOME_ROW]))
                await cb.answer()
                return
    await state.set_state(CollabForm.note)
    await state.update_data(collab_faculty=faculty_id)
    await respond(
        cb,
        f"دانشکده: {faculty.name}\n\n"
        "یک خط دربارهٔ خودت بنویس (ترم، رشته، چرا می‌خوای کمک کنی).\n"
        "فقط ادمین این متن را می‌بیند.",
        kb([[("ارسال بدون توضیح", f"collab:send:{faculty_id}")], [("↩️ انصراف", "menu:collab")]]),
    )
    await cb.answer()


async def submit_request(target: CallbackQuery | Message, state: FSMContext, faculty_id: int, note: str | None) -> None:
    user_tg = target.from_user
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == user_tg.id))
        if not user:
            user = User(telegram_id=user_tg.id)
            session.add(user)
            await session.flush()
        duplicate = await session.scalar(
            select(CollabRequest.id).where(
                CollabRequest.user_id == user.id,
                CollabRequest.faculty_id == faculty_id,
                CollabRequest.status == "pending",
            )
        )
        faculty = await session.get(Faculty, faculty_id)
        if not duplicate:
            req = CollabRequest(
                user_id=user.id, faculty_id=faculty_id, note=note, display=display_name(user_tg)
            )
            session.add(req)
            await session.commit()
    await state.clear()
    if duplicate:
        await respond(target, "درخواستت قبلاً ثبت شده و منتظر بررسیه.", kb([HOME_ROW]))
        return
    await respond(target, "✅ درخواستت برای ادمین ارسال شد. نتیجه را همین‌جا بهت خبر می‌دم.", kb([HOME_ROW]))
    lines = [f"🤝 درخواست همکاری جدید (#{req.id})", f"از: {req.display}", f"دانشکده: {faculty.name}"]
    if note:
        lines.append(f"توضیح: {note}")
    buttons = [
        [("📝 فقط بررسی نظرها", f"collab:ok:{req.id}:m")],
        [("🗂 فقط مدیریت اساتید و درس‌ها", f"collab:ok:{req.id}:p")],
        [("✅ هر دو", f"collab:ok:{req.id}:b")],
        [("❌ رد", f"collab:no:{req.id}")],
    ]
    for owner_id in settings.admin_ids:
        try:
            await target.bot.send_message(owner_id, "\n".join(lines), reply_markup=kb(buttons))
        except TelegramAPIError as e:
            log.warning("Could not notify owner %s: %s", owner_id, e)


@router.message(CollabForm.note, F.text)
async def got_note(message: Message, state: FSMContext) -> None:
    note = " ".join(message.text.split())
    if len(note) > 300:
        await message.answer("حداکثر ۳۰۰ کاراکتر؛ کوتاه‌ترش کن.")
        return
    faculty_id = (await state.get_data())["collab_faculty"]
    await submit_request(message, state, faculty_id, note)


@router.callback_query(F.data.startswith("collab:send:"))
async def send_without_note(cb: CallbackQuery, state: FSMContext) -> None:
    await submit_request(cb, state, int(cb.data.split(":")[2]), None)
    await cb.answer()


# ---- owner: decide on a request --------------------------------------------------------------

@router.callback_query(F.data.startswith("collab:ok:"))
async def approve_request(cb: CallbackQuery) -> None:
    if await deny(cb):
        return
    _, _, req_id, code = cb.data.split(":")
    async with SessionLocal() as session:
        req = await session.get(CollabRequest, int(req_id))
        if not req or req.status != "pending":
            await cb.answer("این درخواست قبلاً بررسی شده.", show_alert=True)
            return
        grant = await session.scalar(
            select(Collaborator).where(
                Collaborator.user_id == req.user_id, Collaborator.faculty_id == req.faculty_id
            )
        )
        if not grant:
            grant = Collaborator(user_id=req.user_id, faculty_id=req.faculty_id, display=req.display)
            session.add(grant)
        grant.can_moderate = code in ("m", "b")
        grant.can_manage = code in ("p", "b")
        req.status = "approved"
        user = await session.get(User, req.user_id)
        telegram_id, faculty_name = user.telegram_id, req.faculty.name
        summary = perm_text(grant)
        await session.commit()
    try:
        await cb.bot.send_message(
            telegram_id,
            f"🎉 درخواست همکاریت برای «{faculty_name}» تأیید شد.\nدسترسی تو: {summary}\n"
            "از منوی اصلی، «🛠 پنل ادمین» را بزن.",
            reply_markup=kb([HOME_ROW]),
        )
    except TelegramAPIError as e:
        log.warning("Could not tell the new collaborator: %s", e)
    await respond(cb, f"✅ {req.display} همکار «{faculty_name}» شد ({summary}).", kb([BACK_TO_COLLAB]))
    await cb.answer()


@router.callback_query(F.data.startswith("collab:no:"))
async def reject_request(cb: CallbackQuery) -> None:
    if await deny(cb):
        return
    req_id = int(cb.data.split(":")[2])
    async with SessionLocal() as session:
        req = await session.get(CollabRequest, req_id)
        if not req or req.status != "pending":
            await cb.answer("این درخواست قبلاً بررسی شده.", show_alert=True)
            return
        req.status = "rejected"
        user = await session.get(User, req.user_id)
        telegram_id, faculty_name = user.telegram_id, req.faculty.name
        await session.commit()
    try:
        await cb.bot.send_message(
            telegram_id,
            f"درخواست همکاریت برای «{faculty_name}» این بار تأیید نشد. ممنون که پیشنهاد دادی 🙏",
        )
    except TelegramAPIError as e:
        log.warning("Could not tell the applicant: %s", e)
    await respond(cb, "❌ درخواست رد شد.", kb([BACK_TO_COLLAB]))
    await cb.answer()


# ---- owner: manage collaborators -------------------------------------------------------------

@router.callback_query(F.data == "collab:home")
async def collab_home(cb: CallbackQuery, state: FSMContext) -> None:
    if await deny(cb):
        return
    await state.clear()
    async with SessionLocal() as session:
        reqs = (
            await session.scalars(select(CollabRequest).where(CollabRequest.status == "pending").order_by(CollabRequest.id))
        ).all()
        collaborators = (await session.scalars(select(Collaborator).order_by(Collaborator.id))).all()
    rows = [
        [(f"📨 {r.display} · {r.faculty.name}", f"collab:req:{r.id}")] for r in reqs
    ] + [
        [(f"👤 {c.display or c.id} · {c.faculty.name}", f"collab:c:{c.id}")] for c in collaborators
    ]
    rows.append(BACK_TO_PANEL)
    text = f"🤝 همکاران\n📨 درخواست‌های منتظر: {len(reqs)}\n👤 همکاران فعال: {len(collaborators)}"
    await respond(cb, text, kb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("collab:req:"))
async def show_request(cb: CallbackQuery) -> None:
    if await deny(cb):
        return
    req_id = int(cb.data.split(":")[2])
    async with SessionLocal() as session:
        req = await session.get(CollabRequest, req_id)
    if not req or req.status != "pending":
        await respond(cb, "این درخواست دیگه منتظر نیست.", kb([BACK_TO_COLLAB]))
        await cb.answer()
        return
    text = f"🤝 درخواست #{req.id}\nاز: {req.display}\nدانشکده: {req.faculty.name}"
    if req.note:
        text += f"\nتوضیح: {req.note}"
    rows = [
        [("📝 فقط بررسی نظرها", f"collab:ok:{req.id}:m")],
        [("🗂 فقط مدیریت اساتید و درس‌ها", f"collab:ok:{req.id}:p")],
        [("✅ هر دو", f"collab:ok:{req.id}:b")],
        [("❌ رد", f"collab:no:{req.id}")],
        BACK_TO_COLLAB,
    ]
    await respond(cb, text, kb(rows))
    await cb.answer()


async def show_collaborator(cb: CallbackQuery, collab_id: int) -> None:
    async with SessionLocal() as session:
        c = await session.get(Collaborator, collab_id)
    if not c:
        await respond(cb, "این همکار پیدا نشد.", kb([BACK_TO_COLLAB]))
        return
    text = f"👤 {c.display or c.id}\nدانشکده: {c.faculty.name}\nدسترسی: {perm_text(c)}"
    rows = [
        [(("✅" if c.can_moderate else "⬜") + " بررسی نظرها", f"collab:t:{c.id}:m")],
        [(("✅" if c.can_manage else "⬜") + " مدیریت اساتید و درس‌ها", f"collab:t:{c.id}:p")],
        [("🗑 حذف همکار", f"collab:rm:{c.id}")],
        BACK_TO_COLLAB,
    ]
    await respond(cb, text, kb(rows))


@router.callback_query(F.data.startswith("collab:c:"))
async def collaborator_page(cb: CallbackQuery) -> None:
    if await deny(cb):
        return
    await show_collaborator(cb, int(cb.data.split(":")[2]))
    await cb.answer()


@router.callback_query(F.data.startswith("collab:t:"))
async def toggle_permission(cb: CallbackQuery) -> None:
    if await deny(cb):
        return
    _, _, collab_id, code = cb.data.split(":")
    async with SessionLocal() as session:
        c = await session.get(Collaborator, int(collab_id))
        if c:
            if code == "m":
                c.can_moderate = not c.can_moderate
            else:
                c.can_manage = not c.can_manage
            await session.commit()
    await show_collaborator(cb, int(collab_id))
    await cb.answer("انجام شد.")


@router.callback_query(F.data.startswith("collab:rm:"))
async def confirm_remove(cb: CallbackQuery) -> None:
    if await deny(cb):
        return
    collab_id = int(cb.data.split(":")[2])
    await respond(
        cb,
        "مطمئنی این همکار حذف بشه؟ دسترسی‌اش فوراً قطع می‌شه.",
        kb([[("✅ بله، حذف کن", f"collab:rmy:{collab_id}")], [("↩️ نه", f"collab:c:{collab_id}")]]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("collab:rmy:"))
async def remove_collaborator(cb: CallbackQuery) -> None:
    if await deny(cb):
        return
    async with SessionLocal() as session:
        c = await session.get(Collaborator, int(cb.data.split(":")[2]))
        if c:
            await session.delete(c)
            await session.commit()
    await respond(cb, "🗑 همکار حذف شد.", kb([BACK_TO_COLLAB]))
    await cb.answer()
