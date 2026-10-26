"""Notifications to the admins (the people listed in ADMIN_IDS)."""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.config import settings
from bot.db.models import REVIEW_PENDING, Collaborator, Course, Professor, Review, User
from bot.db.session import SessionLocal
from bot.keyboards import kb
from bot.services.access import MODERATE
from bot.services.precheck import flags_to_text
from bot.services.publisher import review_to_data

log = logging.getLogger(__name__)


async def notify_admins(
    bot: Bot,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    faculty_id: int | None = None,
    perm: str = MODERATE,
) -> None:
    """Send to the owners, plus the collaborators who have `perm` for this faculty."""
    recipients = list(settings.admin_ids)
    if faculty_id is not None:
        flag = Collaborator.can_moderate if perm == MODERATE else Collaborator.can_manage
        async with SessionLocal() as session:
            extra = await session.scalars(
                select(User.telegram_id)
                .join(Collaborator, Collaborator.user_id == User.id)
                .where(Collaborator.faculty_id == faculty_id, flag.is_(True))
            )
            recipients += [t for t in extra.all() if t not in recipients]
    for chat_id in recipients:
        try:
            await bot.send_message(chat_id, text, reply_markup=reply_markup)
        except TelegramAPIError as e:
            # Most common cause: the person never pressed /start in the bot.
            log.warning("Could not notify %s: %s", chat_id, e)


async def notify_new_review(bot: Bot, review_id: int, auto: bool = False) -> None:
    async with SessionLocal() as session:
        r = await session.get(Review, review_id)
        prof = await session.scalar(
            select(Professor)
            .where(Professor.id == r.professor_id)
            .options(selectinload(Professor.track))
        )
        course = await session.get(Course, r.course_id) if r.course_id else None

    scores = review_to_data(r)["scores"]
    if r.status == REVIEW_PENDING:
        status_line = "منتظر تأیید شما ⏳"
    else:
        status_line = ("تأیید خودکار (دانشجوی معتمد) " if auto else "") + (
            "منتشر شد ✅" if r.channel_message_id else "تأییدشده، هنوز منتشر نشده 🕓"
        )
    lines = [
        f"🆕 نظر جدید ثبت شد (#{r.id})",
        f"استاد: {prof.name} ({prof.track.name})",
        f"درس: {course.name if course else 'مشخص نشده'}"
        + (" ⚠️ (درس جدید، تأییدنشده)" if course and course.status == "unverified" else ""),
        f"ترم: {r.term}",
        f"میانگین نمره: {sum(scores) / len(scores):.1f}",
        f"وضعیت: {status_line}",
        "بررسی خودکار: "
        + (f"⚠️ {flags_to_text(r.flags)}" if r.flags else "✅ بدون مورد مشکوک"),
    ]
    if r.comment:
        preview = r.comment if len(r.comment) <= 150 else r.comment[:150] + "…"
        lines += ["", f"توضیحات: {preview}"]
    rows = []
    if r.status == REVIEW_PENDING:
        rows.append([("✅ تأیید و انتشار", f"adm:ok:{r.id}"), ("❌ رد", f"adm:no:{r.id}")])
    rows.append([("👁 مشاهدهٔ کامل", f"adm:r:{r.id}:0")])
    await notify_admins(bot, "\n".join(lines), kb(rows), faculty_id=prof.track.faculty_id)
