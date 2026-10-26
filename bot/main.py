"""Bot entry point: main menu, browsing (faculty -> track -> professor -> course), admin panel.

Choosing a course starts the review form (bot/handlers/review.py).
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from bot.config import settings
from bot.db.models import (
    PROF_ACTIVE,
    PROF_UNVERIFIED,
    REVIEW_APPROVED,
    REVIEW_PENDING,
    CollabRequest,
    Faculty,
    Professor,
    Review,
    Track,
    User,
)
from bot.db.session import SessionLocal, init_db
from bot.handlers import admin, collab, newprof, profadmin, review
from bot.handlers.newprof import show_courses
from bot.handlers.review import respond
from bot.keyboards import HOME_ROW, kb
from bot.middlewares import BanMiddleware
from bot.services.access import MANAGE, MODERATE, faculties_for, is_owner, is_staff, review_scope
from bot.services.publisher import publish_pending

try:
    from aiohttp_socks import ProxyError
except ImportError:  # no proxy library installed: nothing to retry
    class ProxyError(Exception):
        pass

log = logging.getLogger(__name__)


class ResilientSession(AiohttpSession):
    """Retries when the proxy fails before reaching Telegram (e.g. PythonAnywhere's 503).

    The request never reached Telegram in that case, so retrying cannot send anything twice.
    """

    async def make_request(self, bot, method, timeout=None):
        last: Exception | None = None
        for attempt in range(6):
            try:
                return await super().make_request(bot, method, timeout)
            except ProxyError as e:
                last = e
                log.warning("Proxy error (%s), retry %s/6", e, attempt + 1)
                await asyncio.sleep(1 + attempt)
        raise TelegramNetworkError(method=method, message=f"ProxyError: {last}") from last

dp = Dispatcher()
dp.include_router(review.router)
dp.include_router(newprof.router)
dp.include_router(admin.router)
dp.include_router(profadmin.router)
dp.include_router(collab.router)
dp.message.outer_middleware(BanMiddleware())
dp.callback_query.outer_middleware(BanMiddleware())

MENU_TEXT = "سلام 👋\nچه کاری می‌خوای انجام بدی؟" + (
    "\n\n📢 نظرهای تأییدشده توی کانال منتشر می‌شن؛ حتماً عضو کانال شو."
    if settings.channel_url
    else ""
)
HELP_TEXT = (
    "ℹ️ راهنما\n\n"
    "با این ربات می‌تونی تجربهٔ کلاس‌هات با اساتید رو ثبت کنی تا بقیهٔ دانشجوها ازش استفاده کنن.\n\n"
    "• از منوی اصلی «ثبت نظر» رو بزن.\n"
    "• دانشکده، رشته، استاد و درس رو انتخاب کن و به چند سؤال دکمه‌ای جواب بده.\n"
    "• نظرت بی‌نام منتشر می‌شه و آیدی تو هیچ‌جا نمایش داده نمی‌شه.\n\n"
    "در هر مرحله می‌تونی با «❌ انصراف» برگردی."
)


def is_admin(user_id: int) -> bool:
    """Owner (ADMIN_IDS). Collaborators are checked with services.access instead."""
    return is_owner(user_id)


async def main_menu(user_id: int) -> InlineKeyboardMarkup:
    rows = [[("📝 ثبت نظر درباره استاد", "menu:review")]]
    if settings.channel_url:
        rows.append([("📢 کانال نظرات اساتید", settings.channel_url)])
    rows.append([("ℹ️ راهنما", "menu:help")])
    if not is_owner(user_id):
        rows.append([("🤝 همکاری با ما", "menu:collab")])
    if await is_staff(user_id):
        rows.append([("🛠 پنل ادمین", "menu:admin")])
    return kb(rows)


# ---- main menu -------------------------------------------------------------------------------

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(MENU_TEXT, reply_markup=await main_menu(message.from_user.id))


@dp.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=kb([HOME_ROW]))


@dp.callback_query(F.data == "menu:home")
async def menu_home(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await respond(cb, MENU_TEXT, await main_menu(cb.from_user.id))
    await cb.answer()


@dp.callback_query(F.data == "menu:help")
async def menu_help(cb: CallbackQuery) -> None:
    await respond(cb, HELP_TEXT, kb([HOME_ROW]))
    await cb.answer()


# ---- browsing --------------------------------------------------------------------------------

async def faculties_with_tracks() -> list[Faculty]:
    async with SessionLocal() as session:
        return list(
            (
                await session.scalars(
                    select(Faculty)
                    .where(select(Track.id).where(Track.faculty_id == Faculty.id).exists())
                    .order_by(Faculty.name)
                )
            ).all()
        )


async def show_faculty_tracks(cb: CallbackQuery, faculty_id: int, many_faculties: bool) -> None:
    async with SessionLocal() as session:
        faculty = await session.get(Faculty, faculty_id)
        tracks = (
            await session.scalars(
                select(Track).where(Track.faculty_id == faculty_id).order_by(Track.name)
            )
        ).all()
    rows = [[(t.name, f"track:{t.id}")] for t in tracks]
    rows.append([("⬅️ بازگشت", "menu:review") if many_faculties else HOME_ROW[0]])
    await respond(cb, f"{faculty.name}\nرشته یا گروه خودت را انتخاب کن:", kb(rows))


@dp.callback_query(F.data == "menu:review")
async def menu_review(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    faculties = await faculties_with_tracks()
    if not faculties:
        await respond(cb, "هنوز لیست اساتید وارد نشده است.", kb([HOME_ROW]))
    elif len(faculties) == 1:  # nothing to choose between: go straight to the tracks
        await show_faculty_tracks(cb, faculties[0].id, many_faculties=False)
    else:
        rows = [[(f.name, f"fac:{f.id}")] for f in faculties] + [HOME_ROW]
        await respond(cb, "دانشکده‌ات را انتخاب کن:", kb(rows))
    await cb.answer()


@dp.callback_query(F.data.startswith("fac:"))
async def pick_faculty(cb: CallbackQuery) -> None:
    await show_faculty_tracks(cb, int(cb.data.split(":")[1]), many_faculties=True)
    await cb.answer()


@dp.callback_query(F.data.startswith("track:"))
async def pick_track(cb: CallbackQuery) -> None:
    track_id = int(cb.data.split(":")[1])
    async with SessionLocal() as session:
        track = await session.get(Track, track_id)
        profs = (
            await session.scalars(
                select(Professor)
                .where(
                    Professor.track_id == track_id,
                    Professor.status.in_([PROF_ACTIVE, PROF_UNVERIFIED]),
                )
                .order_by(Professor.name)
            )
        ).all()
    rows = [[(p.name, f"prof:{p.id}")] for p in profs]
    rows.append([("استادم توی لیست نیست", f"newprof:{track_id}")])
    many = len(await faculties_with_tracks()) > 1
    rows.append([("⬅️ بازگشت", f"fac:{track.faculty_id}" if many and track.faculty_id else "menu:review")])
    await respond(cb, f"{track.name}\nاستاد را انتخاب کن:", kb(rows))
    await cb.answer()


@dp.callback_query(F.data.startswith("prof:"))
async def pick_prof(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()  # leaves any typing state (e.g. when coming back from "type a course")
    await show_courses(cb, int(cb.data.split(":")[1]))
    await cb.answer()


# ---- admin -----------------------------------------------------------------------------------

async def count_unpublished() -> int:
    async with SessionLocal() as session:
        return await session.scalar(
            select(func.count())
            .select_from(Review)
            .where(Review.status == REVIEW_APPROVED, Review.channel_message_id.is_(None))
        )


async def run_publish(bot: Bot) -> str:
    count, error = await publish_pending(bot)
    text = f"{count} نظر در کانال منتشر شد."
    if error:
        text += (
            f"\n\n❌ خطا: {error}\n"
            "چک کن بات ادمین کانال باشه (با دسترسی ارسال پیام) و CHANNEL_ID درست باشه."
        )
    return text


@dp.message(Command("publish_pending"))
async def publish_pending_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer(
            "این دستور مخصوص ادمینه.\n"
            f"آیدی عددی تو: {message.from_user.id}\n"
            "اگه ادمینی، این عدد رو توی ADMIN_IDS فایل .env بذار و بات رو دوباره اجرا کن."
        )
        return
    await message.answer(await run_publish(message.bot))


@dp.callback_query(F.data == "menu:admin")
async def menu_admin(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    owner = is_owner(uid)
    mod = await faculties_for(uid, MODERATE)  # None = every faculty
    man = await faculties_for(uid, MANAGE)
    can_moderate = mod is None or bool(mod)
    can_manage = man is None or bool(man)
    if not (can_moderate or can_manage):
        await cb.answer("دسترسی نداری.", show_alert=True)
        return
    rows = []
    if can_moderate:
        async with SessionLocal() as session:
            awaiting = await session.scalar(
                select(func.count())
                .select_from(Review)
                .where(Review.status == REVIEW_PENDING, *review_scope(mod))
            )
        rows.append([(f"📥 منتظر تأیید ({awaiting})", "adm:pend:0")])
        rows.append([("📋 همهٔ نظرها", "adm:list:0")])
    if owner:
        rows.append([(f"📤 انتشار نظرهای تأییدشدهٔ منتشرنشده ({await count_unpublished()})", "admin:publish")])
    if can_manage:
        rows.append([("👨‍🏫 مدیریت دانشکده‌ها و اساتید", "pm:facs")])
    if owner:
        async with SessionLocal() as session:
            requests = await session.scalar(
                select(func.count()).select_from(CollabRequest).where(CollabRequest.status == "pending")
            )
        rows.append([(f"🤝 همکاران ({requests} درخواست)", "collab:home")])
        rows.append([("📊 آمار", "admin:stats")])
    rows.append(HOME_ROW)
    await respond(cb, "🛠 پنل ادمین" if owner else "🛠 پنل همکار", kb(rows))
    await cb.answer()


@dp.callback_query(F.data == "admin:publish")
async def admin_publish(cb: CallbackQuery) -> None:
    if not is_admin(cb.from_user.id):
        await cb.answer("دسترسی نداری.", show_alert=True)
        return
    await cb.answer("در حال انتشار...")
    text = await run_publish(cb.bot)
    await respond(cb, text, kb([[("⬅️ پنل ادمین", "menu:admin")], HOME_ROW]))


@dp.callback_query(F.data == "admin:stats")
async def admin_stats(cb: CallbackQuery) -> None:
    if not is_admin(cb.from_user.id):
        await cb.answer("دسترسی نداری.", show_alert=True)
        return
    async with SessionLocal() as session:
        reviews = await session.scalar(select(func.count()).select_from(Review))
        profs = await session.scalar(select(func.count()).select_from(Professor))
        users = await session.scalar(select(func.count()).select_from(User))
        unverified = await session.scalar(
            select(func.count()).select_from(Professor).where(Professor.status == PROF_UNVERIFIED)
        )
    pending = await count_unpublished()
    text = (
        "📊 آمار\n\n"
        f"نظرها: {reviews}\n"
        f"منتشرنشده: {pending}\n"
        f"اساتید: {profs} (تأییدنشده: {unverified})\n"
        f"کاربران: {users}"
    )
    await respond(cb, text, kb([[("⬅️ پنل ادمین", "menu:admin")], HOME_ROW]))
    await cb.answer()


# ---- startup ---------------------------------------------------------------------------------

async def set_commands(bot: Bot) -> None:
    """Fill Telegram's own command menu (the '/' button next to the text box)."""
    base = [
        BotCommand(command="start", description="منوی اصلی"),
        BotCommand(command="help", description="راهنما"),
    ]
    try:
        await bot.set_my_commands(base)
        for admin_id in settings.admin_ids:
            await bot.set_my_commands(
                base + [BotCommand(command="publish_pending", description="انتشار نظرهای منتشرنشده")],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
    except TelegramAPIError as e:
        log.warning("Could not set bot commands: %s", e)


async def main() -> None:
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    logging.basicConfig(level=logging.INFO)
    await init_db()
    session = ResilientSession(proxy=settings.proxy_url or None)
    bot = Bot(settings.bot_token, session=session)
    await set_commands(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())