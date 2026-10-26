"""Posts approved reviews to the Telegram channel."""
import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.config import settings
from bot.db.models import REVIEW_APPROVED, Course, Professor, Review, User
from bot.db.session import SessionLocal
from bot.keyboards import kb
from bot.services.formatting import format_review_for

log = logging.getLogger(__name__)
_lock = asyncio.Lock()  # one publisher at a time, so a review is never posted twice


def review_to_data(r: Review) -> dict:
    return {
        "term": r.term,
        "resources": r.resources,
        "attendance": r.attendance,
        "sufficient": bool(r.resources_sufficient),
        "grading": r.grading,
        "scores": [
            r.score_continuity, r.score_knowledge, r.score_delivery,
            r.score_management, r.score_responsiveness, r.score_behavior,
        ],
        "contact": r.contact,
        "comment": r.comment,
    }


async def tell_author(bot: Bot, telegram_id: int, message_id: int) -> None:
    """Tell the student their review is live, with a button that opens the post itself."""
    url = settings.post_url(message_id) or settings.channel_url
    rows = [[("📢 دیدن نظرت توی کانال", url)]] if url else []
    rows.append([("🏠 منوی اصلی", "menu:home")])
    try:
        await bot.send_message(
            telegram_id, "✅ نظرت تأیید و منتشر شد. ممنون که کمک کردی!", reply_markup=kb(rows)
        )
    except TelegramAPIError as e:
        log.warning("Could not tell the author their review is published: %s", e)


async def publish_pending(bot: Bot) -> tuple[int, str | None]:
    """Post every approved review that is not in the channel yet.

    Returns (number posted, error message or None)."""
    if not settings.channel_id:
        return 0, "CHANNEL_ID توی فایل .env تنظیم نشده."

    posted = 0
    error = None
    async with _lock:
        try:
            username = (await bot.me()).username
        except TelegramAPIError as e:
            return 0, str(e)
        footer = (
            "\n\n~~~~~~~~~~~~~~~~~\n"
            f"برای ثبت نظر درباره استاد به ربات زیر پیام بدید\n@{username}"
        )
        async with SessionLocal() as session:
            reviews = (
                await session.scalars(
                    select(Review)
                    .where(Review.status == REVIEW_APPROVED, Review.channel_message_id.is_(None))
                    .order_by(Review.id)
                )
            ).all()
            for r in reviews:
                prof = await session.scalar(
                    select(Professor)
                    .where(Professor.id == r.professor_id)
                    .options(selectinload(Professor.track))
                )
                course = await session.get(Course, r.course_id) if r.course_id else None
                text = format_review_for(prof, course, review_to_data(r))
                try:
                    msg = await bot.send_message(settings.channel_id, text + footer)
                except TelegramAPIError as e:
                    log.error("Publishing review %s failed: %s", r.id, e)
                    error = str(e)
                    break  # keep the rest for the next attempt
                r.channel_message_id = msg.message_id
                author = await session.get(User, r.user_id)
                await session.commit()
                posted += 1
                await tell_author(bot, author.telegram_id, msg.message_id)
    return posted, error
