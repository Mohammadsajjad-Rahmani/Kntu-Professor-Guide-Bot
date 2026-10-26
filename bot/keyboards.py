from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

HOME_ROW = [("🏠 منوی اصلی", "menu:home")]


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Build an inline keyboard from rows of (text, callback_data).

    If the second item starts with http:// or https://, the button opens that link instead.
    """

    def button(text: str, data: str) -> InlineKeyboardButton:
        if data.startswith(("http://", "https://")):
            return InlineKeyboardButton(text=text, url=data)
        return InlineKeyboardButton(text=text, callback_data=data)

    return InlineKeyboardMarkup(
        inline_keyboard=[[button(t, d) for t, d in row] for row in rows]
    )
