"""Admin: approve or reject reviews (with a reason), browse them, hide a review, ban students.

The student's Telegram ID is never shown here, so reviews stay anonymous.
"""
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from bot.config import settings
from bot.db.models import (
    REVIEW_APPROVED,
    REVIEW_HIDDEN,
    REVIEW_PENDING,
    REVIEW_REJECTED,
    Course,
    Professor,
    Review,
    User,
)
from bot.db.session import SessionLocal
from bot.handlers.review import respond
from bot.keyboards import kb
from bot.services.access import MODERATE, deny_scoped, faculties_for, is_owner, review_scope
from bot.services.formatting import format_review_for
from bot.services.precheck import flags_to_text
from bot.services.publisher import publish_pending, review_to_data

log = logging.getLogger(__name__)
router = Router()
PAGE_SIZE = 5
STATUS_FA = {"approved": "تأییدشده", "pending": "منتظر تأیید", "rejected": "ردشده", "hidden": "مخفی"}
BACK_TO_PANEL = [("⬅️ پنل ادمین", "menu:admin")]
REJECT_REASONS = [
    "توهین یا لحن نامناسب",
    "اطلاعات تماس یا شخصی",
    "بی‌ربط یا اسپم",
    "اتهام بدون دلیل یا نادرست",
    "بدون ذکر دلیل",
]


async def deny(cb: CallbackQuery) -> bool:
    """Owner-only actions (ban, collaborators, statistics, publishing)."""
    if is_owner(cb.from_user.id):
        return False
    await cb.answer("دسترسی نداری.", show_alert=True)
    return True


async def deny_review(cb: CallbackQuery, review_id: int) -> bool:
    """Owners, or collaborators who may moderate the faculty this review belongs to."""
    return await deny_scoped(cb, MODERATE, review_id=review_id)


def average(r: Review) -> float:
    scores = review_to_data(r)["scores"]
    return round(sum(scores) / len(scores), 1)


async def tell_student(bot: Bot, telegram_id: int, text: str, reply_markup=None) -> None:
    try:
        await bot.send_message(telegram_id, text, reply_markup=reply_markup)
    except TelegramAPIError as e:
        log.warning("Could not message student: %s", e)


# ---- lists -----------------------------------------------------------------------------------

async def render_list(cb: CallbackQuery, page: int, pending_only: bool) -> None:
    prefix = "adm:pend" if pending_only else "adm:list"
    scope = await faculties_for(cb.from_user.id, MODERATE)
    if scope is not None and not scope:
        await cb.answer("دسترسی نداری.", show_alert=True)
        return
    condition = ([Review.status == REVIEW_PENDING] if pending_only else []) + review_scope(scope)
    async with SessionLocal() as session:
        total = await session.scalar(select(func.count()).select_from(Review).where(*condition))
        rows = (
            await session.execute(
                select(Review, Professor.name)
                .join(Professor, Professor.id == Review.professor_id)
                .where(*condition)
                .order_by(Review.id.asc() if pending_only else Review.id.desc())
                .offset(page * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
        ).all()

    if not total:
        empty = "نظری منتظر تأیید نیست. 🎉" if pending_only else "هنوز نظری ثبت نشده."
        await respond(cb, empty, kb([BACK_TO_PANEL]))
        return

    buttons = []
    for r, prof_name in rows:
        if r.status == REVIEW_PENDING:
            mark = "⏳⚠️" if r.flags else "⏳"
        elif r.status == REVIEW_HIDDEN:
            mark = "🚫"
        elif r.status == REVIEW_REJECTED:
            mark = "❌"
        else:
            mark = "🟢" if r.channel_message_id else "🕓"
        label = f"{mark} #{r.id} {prof_name} · {r.term} · {average(r)}"
        buttons.append([(label, f"adm:r:{r.id}:{page}")])
    nav = []
    if page > 0:
        nav.append(("⬅️ قبلی", f"{prefix}:{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(("بعدی ➡️", f"{prefix}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append(BACK_TO_PANEL)
    title = "📥 منتظر تأیید" if pending_only else "📋 همهٔ نظرها"
    legend = (
        "\n⏳ منتظر (⚠️ مشکوک)  🟢 منتشرشده  🕓 منتشرنشده  ❌ ردشده  🚫 مخفی"
        if not pending_only
        else "\n⚠️ یعنی بررسی خودکار مورد مشکوکی پیدا کرده"
    )
    await respond(cb, f"{title} ({total}){legend}", kb(buttons))


@router.callback_query(F.data.startswith("adm:list:"))
async def list_all(cb: CallbackQuery) -> None:
    await render_list(cb, int(cb.data.split(":")[2]), pending_only=False)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:pend:"))
async def list_pending(cb: CallbackQuery) -> None:
    await render_list(cb, int(cb.data.split(":")[2]), pending_only=True)
    await cb.answer()


# ---- detail ----------------------------------------------------------------------------------

async def render_detail(cb: CallbackQuery, review_id: int, page: int) -> None:
    async with SessionLocal() as session:
        r = await session.get(Review, review_id)
        if not r:
            await respond(cb, "این نظر پیدا نشد.", kb([[("⬅️ لیست", f"adm:list:{page}")]]))
            return
        prof = await session.scalar(
            select(Professor)
            .where(Professor.id == r.professor_id)
            .options(selectinload(Professor.track))
        )
        course = await session.get(Course, r.course_id) if r.course_id else None
        author = await session.get(User, r.user_id)

    text = format_review_for(prof, course, review_to_data(r))
    text += (
        f"\n\n— #{r.id} | وضعیت: {STATUS_FA.get(r.status, r.status)} | "
        f"کانال: {'منتشرشده' if r.channel_message_id else 'منتشرنشده'} | "
        f"ثبت: {r.created_at:%Y-%m-%d %H:%M} UTC"
        f"\nبررسی خودکار: " + (f"⚠️ {flags_to_text(r.flags)}" if r.flags else "✅ بدون مورد مشکوک")
    )
    if r.reject_reason:
        text += f"\nدلیل رد: {r.reject_reason}"
    if author.is_banned and is_owner(cb.from_user.id):
        text += "\n⛔ نویسندهٔ این نظر مسدود است."

    rows = []
    if r.status == REVIEW_PENDING:
        rows.append([("✅ تأیید و انتشار", f"adm:ok:{r.id}"), ("❌ رد", f"adm:no:{r.id}")])
    elif r.status not in (REVIEW_HIDDEN, REVIEW_REJECTED):
        rows.append([("🗑 حذف از کانال و مخفی کردن", f"adm:hide:{r.id}:{page}")])
    if is_owner(cb.from_user.id):  # banning is owner-only
        if author.is_banned:
            rows.append([("🔓 رفع مسدودی نویسنده", f"adm:unban:{r.id}:{page}")])
        else:
            rows.append([("⛔ مسدود کردن نویسنده", f"adm:ban:{r.id}:{page}")])
    rows.append([("⬅️ لیست نظرها", f"adm:list:{page}")])
    await respond(cb, text, kb(rows))


@router.callback_query(F.data.startswith("adm:r:"))
async def show_review(cb: CallbackQuery) -> None:
    _, _, review_id, page = cb.data.split(":")
    if await deny_review(cb, int(review_id)):
        return
    await render_detail(cb, int(review_id), int(page))
    await cb.answer()


# ---- approve / reject ------------------------------------------------------------------------

AFTER_DECISION = [[("📥 بعدی در انتظار تأیید", "adm:pend:0")], BACK_TO_PANEL]


@router.callback_query(F.data.startswith("adm:ok:"))
async def approve(cb: CallbackQuery) -> None:
    review_id = int(cb.data.split(":")[2])
    if await deny_review(cb, review_id):
        return
    async with SessionLocal() as session:
        r = await session.get(Review, review_id)
        if not r or r.status != REVIEW_PENDING:
            await cb.answer("این نظر قبلاً بررسی شده.", show_alert=True)
            return
        r.status = REVIEW_APPROVED
        student = await session.get(User, r.user_id)
        student.trust_score += 1  # trusted students can skip manual approval (AUTO_PUBLISH_AFTER)
        telegram_id = student.telegram_id
        await session.commit()

    # publish_pending itself tells the student, with a button to their post, once it is live
    _, error = await publish_pending(cb.bot)
    async with SessionLocal() as session:
        published = (await session.get(Review, review_id)).channel_message_id
    if not published:
        await tell_student(
            cb.bot, telegram_id, "✅ نظرت تأیید شد و به‌زودی توی کانال منتشر می‌شه. ممنون که کمک کردی!"
        )
    if error:
        text = (
            f"نظر #{review_id} تأیید شد، ولی انتشار در کانال انجام نشد:\n{error}\n"
            "بعد از رفع مشکل، از پنل ادمین «انتشار نظرهای تأییدشده» را بزن."
        )
    else:
        text = f"✅ نظر #{review_id} تأیید و در کانال منتشر شد."
    await respond(cb, text, kb(AFTER_DECISION))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:no:"))
async def reject_reasons(cb: CallbackQuery) -> None:
    """Step 1 of rejecting: pick a reason (the student is told why)."""
    review_id = int(cb.data.split(":")[2])
    if await deny_review(cb, review_id):
        return
    rows = [[(reason, f"adm:nr:{review_id}:{i}")] for i, reason in enumerate(REJECT_REASONS)]
    rows.append([("↩️ انصراف", f"adm:r:{review_id}:0")])
    await respond(cb, f"دلیل رد نظر #{review_id} چیه؟", kb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:nr:"))
async def reject(cb: CallbackQuery) -> None:
    _, _, review_id, idx = cb.data.split(":")
    review_id, idx = int(review_id), int(idx)
    if await deny_review(cb, review_id):
        return
    reason = REJECT_REASONS[idx]
    give_reason = idx != len(REJECT_REASONS) - 1  # the last option means "no reason given"
    async with SessionLocal() as session:
        r = await session.get(Review, review_id)
        if not r or r.status != REVIEW_PENDING:
            await cb.answer("این نظر قبلاً بررسی شده.", show_alert=True)
            return
        r.status = REVIEW_REJECTED
        r.reject_reason = reason if give_reason else None
        student = await session.get(User, r.user_id)
        student.trust_score = 0  # a rejected review resets trust
        telegram_id = student.telegram_id
        await session.commit()

    message = "❌ نظرت منتشر نشد."
    if give_reason:
        message += f"\nدلیل: {reason}"
    message += "\nمی‌تونی نظر اصلاح‌شده رو دوباره ثبت کنی."
    await tell_student(cb.bot, telegram_id, message)
    await respond(cb, f"❌ نظر #{review_id} رد شد.", kb(AFTER_DECISION))
    await cb.answer()


# ---- hide ------------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:hide:"))
async def confirm_hide(cb: CallbackQuery) -> None:
    _, _, review_id, page = cb.data.split(":")
    if await deny_review(cb, int(review_id)):
        return
    await respond(
        cb,
        f"مطمئنی؟ نظر #{review_id} از کانال حذف و مخفی می‌شود.",
        kb([
            [("✅ بله، حذف کن", f"adm:hidey:{review_id}:{page}")],
            [("↩️ نه", f"adm:r:{review_id}:{page}")],
        ]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:hidey:"))
async def hide_review(cb: CallbackQuery) -> None:
    _, _, review_id, page = cb.data.split(":")
    if await deny_review(cb, int(review_id)):
        return
    async with SessionLocal() as session:
        r = await session.get(Review, int(review_id))
        msg_id = r.channel_message_id
        r.status = REVIEW_HIDDEN
        r.channel_message_id = None
        await session.commit()

    note = ""
    if msg_id and settings.channel_id:
        try:
            await cb.bot.delete_message(settings.channel_id, msg_id)
        except TelegramAPIError as e:
            note = (
                f"\n⚠️ حذف از کانال انجام نشد ({e}).\n"
                "دسترسی «Delete messages» برای بات لازمه؛ ممکنه پیام رو باید دستی پاک کنی."
            )
    await respond(
        cb,
        f"نظر #{review_id} مخفی شد.{note}",
        kb([[("⬅️ لیست نظرها", f"adm:list:{page}")], BACK_TO_PANEL]),
    )
    await cb.answer()


# ---- ban / unban the author of a review ------------------------------------------------------

async def set_author_banned(cb: CallbackQuery, banned: bool) -> None:
    _, _, review_id, page = cb.data.split(":")
    async with SessionLocal() as session:
        r = await session.get(Review, int(review_id))
        author = await session.get(User, r.user_id)
        author.is_banned = banned
        if banned:
            author.trust_score = 0
        await session.commit()
    text = (
        "⛔ نویسندهٔ این نظر مسدود شد و دیگه نمی‌تونه از بات استفاده کنه."
        if banned
        else "🔓 مسدودیت نویسندهٔ این نظر برداشته شد."
    )
    await respond(cb, text, kb([[("👁 بازگشت به نظر", f"adm:r:{review_id}:{page}")]]))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:ban:"))
async def ban_author(cb: CallbackQuery) -> None:
    if await deny(cb):
        return
    await set_author_banned(cb, True)


@router.callback_query(F.data.startswith("adm:unban:"))
async def unban_author(cb: CallbackQuery) -> None:
    if await deny(cb):
        return
    await set_author_banned(cb, False)
