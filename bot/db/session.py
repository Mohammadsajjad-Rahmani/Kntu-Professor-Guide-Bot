from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import settings
from bot.db.models import Base, Faculty, Track, University
from bot.utils.text import normalize

engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Tracks created before faculties existed are moved under this faculty.
DEFAULT_FACULTY = "مهندسی کامپیوتر"


def _add_missing_columns(sync_conn) -> None:
    """Add columns that were added to the models after the table already existed.

    Only simple nullable columns are supported. Alembic replaces this in a later phase.
    """
    existing_tables = set(inspect(sync_conn).get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {c["name"] for c in inspect(sync_conn).get_columns(table.name)}
        for col in table.columns:
            if col.name not in have:
                col_type = col.type.compile(dialect=sync_conn.dialect)
                sync_conn.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}')
                )


async def default_university(session) -> University:
    """The first university in the database (created from UNIVERSITY_NAME if there is none)."""
    uni = await session.scalar(select(University).order_by(University.id).limit(1))
    if not uni:
        name = settings.university_name
        uni = University(name=name, name_normalized=normalize(name))
        session.add(uni)
        await session.flush()
    return uni


async def get_or_create_faculty(session, university_id: int, name: str) -> Faculty:
    norm = normalize(name)
    fac = await session.scalar(
        select(Faculty).where(Faculty.university_id == university_id, Faculty.name_normalized == norm)
    )
    if not fac:
        fac = Faculty(university_id=university_id, name=" ".join(name.split()), name_normalized=norm)
        session.add(fac)
        await session.flush()
    return fac


async def _backfill_faculties() -> None:
    async with SessionLocal() as session:
        orphans = (await session.scalars(select(Track).where(Track.faculty_id.is_(None)))).all()
        for track in orphans:
            fac = await get_or_create_faculty(session, track.university_id, DEFAULT_FACULTY)
            track.faculty_id = fac.id
        await session.commit()


async def init_db() -> None:
    """Create missing tables and columns, and move old tracks under a default faculty."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
    await _backfill_faculties()
