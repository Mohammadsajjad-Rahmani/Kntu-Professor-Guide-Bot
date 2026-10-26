"""Blocks banned students from using the bot at all."""
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.config import settings
from bot.db.models import User
from bot.db.session import SessionLocal

BANNED_TEXT = "دسترسی تو به این ربات محدود شده است."


class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and user.id not in settings.admin_ids:
            async with SessionLocal() as session:
                banned = await session.scalar(
                    select(User.is_banned).where(User.telegram_id == user.id)
                )
            if banned:
                if isinstance(event, CallbackQuery):
                    await event.answer(BANNED_TEXT, show_alert=True)
                elif isinstance(event, Message):
                    await event.answer(BANNED_TEXT)
                return None
        return await handler(event, data)
