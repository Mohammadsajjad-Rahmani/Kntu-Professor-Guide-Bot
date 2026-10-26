"""Review form: almost all answers are buttons; only contact and comment are free text (optional)."""
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from bot.config import settings
from bot.db.models import (
    REVIEW_APPROVED,
    REVIEW_PENDING,
    REVIEW_REJECTED,
    Course,
    Professor,
    Review,
    User,
)
from bot.db.session import SessionLocal
from bot.services.formatting import SCORE_LABELS, format_review_for
from bot.services.notify import notify_new_review
from bot.services.precheck import precheck
from bot.services.publisher import publish_pending
from bot.utils.dates import parse_manual_term, terms_since
from bot.utils.filters import check_free_text

router = Router()


class ReviewForm(StatesGroup):
    term = State()
    contact = State()
    comment = State()


# (key, question, fixed options). The term options are generated from today's date.
OPTION_STEPS: list[tuple[str, str, list[str] | None]] = [
    ("term", "ترمی که با این استاد کلاس داشتی را انتخاب کن:", None),
    (
        "resources",
        "منابع آموزشی استاد چیست؟",
        ["فایل پاورپوینت یا PDF", "جزوهٔ دست‌نویس یا اسلاید", "کتاب مرجع", "منبع مشخصی ندارد"],
    ),
    ("attendance", "حضور و غیاب؟", ["حضور و غیاب می‌کند", "حضور و غیاب نمی‌کند", "گاهی"]),
    ("sufficient", "منابع معرفی‌شده برای امتحان کافی است؟", ["بله", "خیر"]),
    ("grading", "وضعیت نمره‌دادن؟", ["سخت‌گیر", "منصفانه", "آسان‌گیر"]),
]
CANCEL = InlineKeyboardButton(text="❌ انصراف", callback_data="cancel")
HOME_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")]]
)


def done_kb() -> InlineKeyboardMarkup:
    """Shown after a review is saved: a link to the channel plus the home button."""
    rows = []
    if settings.channel_url:
        rows.append([InlineKeyboardButton(text="📢 مشاهدهٔ کانال", url=settings.channel_url)])
    rows.append([InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def options_for(step: int) -> list[str]:
    key, _, fixed = OPTION_STEPS[step]
    return terms_since() if key == "term" else fixed


async def respond(target: CallbackQuery | Message, text: str, kb: InlineKeyboardMarkup) -> None:
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


async def show_step(target, step: int) -> None:
    key, question, _ = OPTION_STEPS[step]
    buttons = [
        InlineKeyboardButton(text=opt, callback_data=f"o:{step}:{i}")
        for i, opt in enumerate(options_for(step))
    ]
    per_row = 3 if key == "term" else 2  # terms are short, so three fit in a row
    rows = [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]
    if key == "term":
        rows.append(
            [InlineKeyboardButton(text="ترمم توی لیست نیست؟ دستی وارد می‌کنم ✍️", callback_data="term:manual")]
        )
    back = f"go:o{step - 1}" if step > 0 else "go:course"
    rows.append([back_button(back), CANCEL])
    await respond(target, question, InlineKeyboardMarkup(inline_keyboard=rows))


async def show_score(target, n: int) -> None:
    buttons = [
        InlineKeyboardButton(text=str(v), callback_data=f"s:{n}:{v}") for v in range(1, 11)
    ]
    back = f"go:s{n - 1}" if n > 0 else f"go:o{len(OPTION_STEPS) - 1}"
    rows = [buttons[:5], buttons[5:], [back_button(back), CANCEL]]
    text = f"نمره به «{SCORE_LABELS[n]}» ({n + 1} از {len(SCORE_LABELS)})\nاز ۱ تا ۱۰:"
    await respond(target, text, InlineKeyboardMarkup(inline_keyboard=rows))


def back_button(target: str) -> InlineKeyboardButton:
    """'Previous question' button. `target` is a callback like 'go:o2' (handled by go_back)."""
    return InlineKeyboardButton(text="⬅️ قبلی", callback_data=target)


def back_kb(target: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[back_button(target), CANCEL]])


def two_buttons(
    a: tuple[str, str], b: tuple[str, str], back: str | None = None
) -> InlineKeyboardMarkup:
    last_row = [back_button(back), CANCEL] if back else [CANCEL]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=a[0], callback_data=a[1]),
             InlineKeyboardButton(text=b[0], callback_data=b[1])],
            last_row,
        ]
    )


async def ask_contact(target) -> None:
    await respond(
        target,
        "راه ارتباطی با استاد (ایمیل، آیدی و...) را می‌دانی؟",
        two_buttons(("ندارم", "c:none"), ("می‌نویسم", "c:write"), back=f"go:s{len(SCORE_LABELS) - 1}"),
    )


async def ask_comment(target) -> None:
    await respond(
        target,
        "توضیحات اضافه داری؟ (اختیاری)",
        two_buttons(("رد کردن", "m:skip"), ("می‌نویسم", "m:write"), back="go:c"),
    )


async def load_names(session, data: dict) -> tuple[Professor, Course | None]:
    prof = await session.scalar(
        select(Professor).where(Professor.id == data["prof_id"]).options(selectinload(Professor.track))
    )
    course = await session.get(Course, data["course_id"]) if data.get("course_id") else None
    return prof, course


async def show_preview(target, state: FSMContext) -> None:
    data = await state.get_data()
    async with SessionLocal() as session:
        prof, course = await load_names(session, data)
    text = format_review_for(prof, course, data)
    kb = two_buttons(
        ("✅ ثبت نظر", "ok"),
        ("🔄 از اول", f"course:{prof.id}:{data.get('course_id') or 0}"),
        back="go:m",
    )
    await respond(target, "پیش‌نمایش نظر تو:\n\n" + text, kb)


# ---- entry -----------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("course:"))
async def start_form(cb: CallbackQuery, state: FSMContext) -> None:
    _, prof_id, course_id = cb.data.split(":")
    await state.clear()
    await state.update_data(
        prof_id=int(prof_id), course_id=int(course_id) or None, scores=[]
    )
    await show_step(cb, 0)
    await cb.answer()


@router.callback_query(F.data.startswith("go:"))
async def go_back(cb: CallbackQuery, state: FSMContext) -> None:
    """The 'previous question' button.

    go:course -> course list, go:o<k> -> option question k, go:s<n> -> score n (scores after it
    are dropped), go:c -> contact question, go:m -> comment question.
    """
    target = cb.data.split(":")[1]
    data = await state.get_data()
    if "prof_id" not in data:  # e.g. the bot was restarted while the student was in the form
        await cb.message.edit_text("این مرحله منقضی شده. دوباره از منوی اصلی شروع کن.", reply_markup=HOME_KB)
        await cb.answer()
        return
    await state.set_state(None)  # leaves any "waiting for typed text" state
    if target == "course":
        from bot.handlers.newprof import show_courses  # imported here: newprof imports this module

        await show_courses(cb, data["prof_id"])
    elif target.startswith("o"):
        await show_step(cb, int(target[1:]))
    elif target.startswith("s"):
        n = int(target[1:])
        await state.update_data(scores=list(data.get("scores", []))[:n])
        await show_score(cb, n)
    elif target == "c":
        await ask_contact(cb)
    elif target == "m":
        await ask_comment(cb)
    await cb.answer()


@router.callback_query(F.data == "cancel")
async def cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("انصراف داده شد.", reply_markup=HOME_KB)
    await cb.answer()


# ---- button steps ----------------------------------------------------------------------------

@router.callback_query(F.data.startswith("o:"))
async def pick_option(cb: CallbackQuery, state: FSMContext) -> None:
    _, step, idx = cb.data.split(":")
    step, idx = int(step), int(idx)
    key = OPTION_STEPS[step][0]
    value = options_for(step)[idx]
    await state.update_data({key: (value == "بله") if key == "sufficient" else value})
    if step + 1 < len(OPTION_STEPS):
        await show_step(cb, step + 1)
    else:
        await show_score(cb, 0)
    await cb.answer()


@router.callback_query(F.data.startswith("s:"))
async def pick_score(cb: CallbackQuery, state: FSMContext) -> None:
    _, n, value = cb.data.split(":")
    n, value = int(n), int(value)
    data = await state.get_data()
    scores = list(data.get("scores", []))
    if n != len(scores):  # double tap or an old message
        await cb.answer()
        return
    scores.append(value)
    await state.update_data(scores=scores)
    if len(scores) < len(SCORE_LABELS):
        await show_score(cb, len(scores))
    else:
        await ask_contact(cb)
    await cb.answer()


@router.callback_query(F.data == "term:manual")
async def term_manual(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ReviewForm.term)
    await cb.message.edit_text(
        "ترمت رو بنویس، مثلاً:\nتابستان ۱۳۹۹  یا  مهر ۱۳۹۸\n(حتماً سال رو هم بنویس)",
        reply_markup=back_kb("go:o0"),
    )
    await cb.answer()


@router.message(ReviewForm.term, F.text)
async def got_term(message: Message, state: FSMContext) -> None:
    term = parse_manual_term(message.text)
    if not term:
        await message.answer(
            "ترم معتبر نیست. اسم ترم و سال رو بنویس، مثلاً: تابستان ۱۳۹۹\n(فقط حروف و عدد، بدون علامت.)"
        )
        return
    await state.update_data(term=term)
    await state.set_state(None)
    await show_step(message, 1)


@router.callback_query(F.data == "c:none")
async def contact_none(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(contact=None)
    await ask_comment(cb)
    await cb.answer()


@router.callback_query(F.data == "c:write")
async def contact_write(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ReviewForm.contact)
    await cb.message.edit_text(
        "راه ارتباطی را بنویس (حداکثر ۱۰۰ کاراکتر):", reply_markup=back_kb("go:c")
    )
    await cb.answer()


@router.callback_query(F.data == "m:skip")
async def comment_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(comment=None)
    await show_preview(cb, state)
    await cb.answer()


@router.callback_query(F.data == "m:write")
async def comment_write(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ReviewForm.comment)
    await cb.message.edit_text(
        "توضیحاتت را بنویس (حداکثر ۵۰۰ کاراکتر).\nلینک، آیدی و شماره تلفن مجاز نیست.",
        reply_markup=back_kb("go:m"),
    )
    await cb.answer()


# ---- free-text steps -------------------------------------------------------------------------

@router.message(ReviewForm.contact, F.text)
async def got_contact(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if len(text) > 100:
        await message.answer("خیلی طولانیه. حداکثر ۱۰۰ کاراکتر بنویس.")
        return
    await state.update_data(contact=text)
    await state.set_state(None)
    await ask_comment(message)


@router.message(ReviewForm.comment, F.text)
async def got_comment(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    reason = check_free_text(text)
    if reason:
        await message.answer(f"{reason}\nدوباره بنویس.")
        return
    await state.update_data(comment=text)
    await state.set_state(None)
    await show_preview(message, state)


# ---- save ------------------------------------------------------------------------------------

@router.callback_query(F.data == "ok")
async def save_review(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if "scores" not in data or len(data["scores"]) != len(SCORE_LABELS):
        await cb.message.edit_text("فرم ناقصه. دوباره از اول شروع کن.", reply_markup=HOME_KB)
        await cb.answer()
        return

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == cb.from_user.id))
        if not user:
            user = User(telegram_id=cb.from_user.id)
            session.add(user)
            await session.flush()

        # Daily limit per student
        since = datetime.utcnow() - timedelta(days=1)
        recent = await session.scalar(
            select(func.count())
            .select_from(Review)
            .where(Review.user_id == user.id, Review.created_at >= since)
        )
        if recent >= settings.daily_review_limit:
            await session.commit()  # keeps the new user row
            await state.clear()
            await cb.message.edit_text(
                "امروز به سقف ثبت نظر رسیدی. فردا دوباره امتحان کن.", reply_markup=HOME_KB
            )
            await cb.answer()
            return

        s = data["scores"]
        # Automatic checks: label the review as clean or suspicious for the admin
        flags = precheck(data.get("comment"), data.get("contact"), s)
        if data.get("comment"):
            same_text = await session.scalar(
                select(func.count())
                .select_from(Review)
                .where(Review.comment == data["comment"], Review.user_id != user.id)
            )
            if same_text:
                flags.append("duplicate_text")
        # Trusted students (enough approved reviews) skip manual approval if nothing looks off
        auto = (
            settings.auto_publish_after > 0
            and user.trust_score >= settings.auto_publish_after
            and not flags
        )
        status = REVIEW_APPROVED if (auto or not settings.require_approval) else REVIEW_PENDING
        if auto:
            user.trust_score += 1
        review = Review(
                user_id=user.id,
                professor_id=data["prof_id"],
                course_id=data.get("course_id"),
                term=data["term"],
                resources=data["resources"],
                attendance=data["attendance"],
                resources_sufficient=data["sufficient"],
                grading=data["grading"],
                score_continuity=s[0],
                score_knowledge=s[1],
                score_delivery=s[2],
                score_management=s[3],
                score_responsiveness=s[4],
                score_behavior=s[5],
                contact=data.get("contact"),
                comment=data.get("comment"),
                status=status,
                flags=",".join(flags) or None,
        )
        # A rejected review must not block the student from submitting a corrected one.
        same_course = (
            Review.course_id.is_(None)
            if data.get("course_id") is None
            else Review.course_id == data["course_id"]
        )
        await session.execute(
            delete(Review).where(
                Review.user_id == user.id,
                Review.professor_id == data["prof_id"],
                Review.term == data["term"],
                Review.status == REVIEW_REJECTED,
                same_course,
            )
        )
        session.add(review)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            await state.clear()
            await cb.message.edit_text(
                "برای این استاد، درس و ترم قبلاً نظر ثبت کرده‌ای.", reply_markup=HOME_KB
            )
            await cb.answer()
            return

    await state.clear()
    thanks = (
        "✅ نظرت ثبت شد و بعد از تأیید ادمین منتشر می‌شه. ممنون که کمک کردی!"
        if status == REVIEW_PENDING
        else "✅ نظرت ثبت شد. ممنون که کمک کردی!"
    )
    if settings.channel_url:
        thanks += "\n\n📢 نظرهای منتشرشده رو توی کانال ببین و عضو شو."
    await cb.message.edit_text(thanks, reply_markup=done_kb())
    await cb.answer()
    # The review is already saved; a failure below only delays publishing or the admin alert.
    await publish_pending(cb.bot)
    await notify_new_review(cb.bot, review.id, auto=auto)
