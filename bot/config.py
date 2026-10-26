import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _parse_ids(raw: str) -> list[int]:
    return [int(x) for x in raw.replace(" ", "").split(",") if x]


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "sqlite+aiosqlite:///./vahedyar.db"
        )
    )
    admin_ids: list[int] = field(
        default_factory=lambda: _parse_ids(os.getenv("ADMIN_IDS", ""))
    )
    channel_id: str = field(default_factory=lambda: os.getenv("CHANNEL_ID", ""))
    # Name used only when the database has no university yet (first run)
    university_name: str = field(
        default_factory=lambda: os.getenv("UNIVERSITY_NAME", "").strip() or "دانشگاه"
    )
    # Public link students use to join the channel (needed when the channel is private)
    channel_link: str = field(default_factory=lambda: os.getenv("CHANNEL_LINK", "").strip())
    # Optional HTTP/SOCKS proxy for reaching Telegram (needed where Telegram is filtered)
    proxy_url: str = field(default_factory=lambda: os.getenv("PROXY_URL", ""))
    # True: an admin must approve every review before it reaches the channel
    require_approval: bool = field(
        default_factory=lambda: os.getenv("REQUIRE_APPROVAL", "true").strip().lower()
        not in ("0", "false", "no", "off")
    )
    # Max reviews one student can submit per 24 hours
    daily_review_limit: int = field(
        default_factory=lambda: int(os.getenv("DAILY_REVIEW_LIMIT", "5") or "5")
    )
    # Students with at least this many approved reviews get clean reviews published without
    # waiting for an admin. 0 = off.
    auto_publish_after: int = field(
        default_factory=lambda: int(os.getenv("AUTO_PUBLISH_AFTER", "0") or "0")
    )

    @property
    def channel_url(self) -> str:
        """Link shown to students. CHANNEL_LINK if set, else built from a public @username."""
        if self.channel_link:
            return self.channel_link
        if self.channel_id.startswith("@"):
            return f"https://t.me/{self.channel_id[1:]}"
        return ""

    def post_url(self, message_id: int | None) -> str:
        """Direct link to one channel post, or "" when it can't be built.

        Public channel (@username): https://t.me/username/42
        Private channel (-100123...): https://t.me/c/123.../42 (works for members only)
        """
        if not message_id:
            return ""
        if self.channel_id.startswith("@"):
            return f"https://t.me/{self.channel_id[1:]}/{message_id}"
        if self.channel_id.startswith("-100"):
            return f"https://t.me/c/{self.channel_id[4:]}/{message_id}"
        return ""


settings = Settings()
