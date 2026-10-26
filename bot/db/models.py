"""Database models.

Design notes:
- `university` is on every main table from day one so more universities can be added later.
- Statuses are plain strings so new states can be added without a migration.
- Reviews keep the Telegram ID only in `users`; it is never published.
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# Status values (kept as strings)
PROF_ACTIVE, PROF_INACTIVE, PROF_UNVERIFIED, PROF_PENDING = (
    "active", "inactive", "unverified", "pending",
)
REVIEW_PENDING, REVIEW_APPROVED, REVIEW_REJECTED, REVIEW_HIDDEN = (
    "pending", "approved", "rejected", "hidden",
)


class University(Base):
    __tablename__ = "universities"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    name_normalized: Mapped[str] = mapped_column(String(120), index=True)
    channel_id: Mapped[str | None] = mapped_column(String(40))


class Faculty(Base):
    """A faculty or school (e.g. computer engineering, mechanical engineering)."""

    __tablename__ = "faculties"
    __table_args__ = (UniqueConstraint("university_id", "name_normalized"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"))
    name: Mapped[str] = mapped_column(String(120))
    name_normalized: Mapped[str] = mapped_column(String(120), index=True)

    tracks: Mapped[list["Track"]] = relationship(back_populates="faculty")


class Track(Base):
    """A department or specialization inside a faculty (e.g. networks, software, AI)."""

    __tablename__ = "tracks"
    __table_args__ = (UniqueConstraint("university_id", "name_normalized"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"))
    # Nullable only so old databases can be upgraded; init_db fills it in.
    faculty_id: Mapped[int | None] = mapped_column(ForeignKey("faculties.id"))
    name: Mapped[str] = mapped_column(String(120))
    name_normalized: Mapped[str] = mapped_column(String(120))
    hashtag: Mapped[str | None] = mapped_column(String(60))

    faculty: Mapped[Faculty | None] = relationship(back_populates="tracks", lazy="selectin")
    professors: Mapped[list["Professor"]] = relationship(back_populates="track")


class Professor(Base):
    __tablename__ = "professors"
    __table_args__ = (UniqueConstraint("university_id", "track_id", "name_normalized"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"))
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"))
    name: Mapped[str] = mapped_column(String(120))
    name_normalized: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(20), default=PROF_ACTIVE)
    # Number of different students who suggested this professor (used to auto-verify)
    suggestion_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    track: Mapped[Track] = relationship(back_populates="professors", lazy="selectin")
    courses: Mapped[list["Course"]] = relationship(
        secondary="professor_courses", back_populates="professors"
    )


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("university_id", "name_normalized"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"))
    name: Mapped[str] = mapped_column(String(160))
    name_normalized: Mapped[str] = mapped_column(String(160), index=True)
    units: Mapped[int | None] = mapped_column(Integer)
    # None/"active": normal, "unverified": typed by a student, "hidden": removed by an admin
    status: Mapped[str | None] = mapped_column(String(20))

    professors: Mapped[list[Professor]] = relationship(
        secondary="professor_courses", back_populates="courses"
    )


class ProfessorCourse(Base):
    __tablename__ = "professor_courses"

    professor_id: Mapped[int] = mapped_column(
        ForeignKey("professors.id"), primary_key=True
    )
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), primary_key=True)


class ProfessorSuggestion(Base):
    """Which students proposed which (unverified) professor. Two different students
    proposing the same name makes the professor active automatically."""

    __tablename__ = "professor_suggestions"
    __table_args__ = (UniqueConstraint("professor_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    professor_id: Mapped[int] = mapped_column(ForeignKey("professors.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default="student")  # student / admin
    # Grows with approved contributions; high-trust users skip moderation (phase 2)
    trust_score: Mapped[int] = mapped_column(Integer, default=0)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Collaborator(Base):
    """A helper limited to one faculty. The owners (ADMIN_IDS) are not stored here."""

    __tablename__ = "collaborators"
    __table_args__ = (UniqueConstraint("user_id", "faculty_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculties.id"), index=True)
    can_moderate: Mapped[bool] = mapped_column(Boolean, default=False)  # approve / reject / hide
    can_manage: Mapped[bool] = mapped_column(Boolean, default=False)  # professors and courses
    display: Mapped[str | None] = mapped_column(String(200))  # name shown to the owner only
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    faculty: Mapped[Faculty] = relationship(lazy="selectin")


class CollabRequest(Base):
    """A student asking to become a collaborator of a faculty."""

    __tablename__ = "collab_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculties.id"))
    note: Mapped[str | None] = mapped_column(String(300))
    display: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/approved/rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    faculty: Mapped[Faculty] = relationship(lazy="selectin")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("user_id", "professor_id", "course_id", "term"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    professor_id: Mapped[int] = mapped_column(ForeignKey("professors.id"), index=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"))
    term: Mapped[str] = mapped_column(String(20))  # e.g. "بهمن ۱۴۰۴"

    # Button-based answers
    resources: Mapped[str | None] = mapped_column(String(40))
    attendance: Mapped[str | None] = mapped_column(String(40))
    resources_sufficient: Mapped[bool | None] = mapped_column(Boolean)
    grading: Mapped[str | None] = mapped_column(String(40))

    # Six scores, 1..10
    score_continuity: Mapped[int | None] = mapped_column(Integer)
    score_knowledge: Mapped[int | None] = mapped_column(Integer)
    score_delivery: Mapped[int | None] = mapped_column(Integer)
    score_management: Mapped[int | None] = mapped_column(Integer)
    score_responsiveness: Mapped[int | None] = mapped_column(Integer)
    score_behavior: Mapped[int | None] = mapped_column(Integer)

    # The only free-text fields (both optional)
    contact: Mapped[str | None] = mapped_column(String(200))
    comment: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(20), default=REVIEW_PENDING, index=True)
    report_count: Mapped[int] = mapped_column(Integer, default=0)
    channel_message_id: Mapped[int | None] = mapped_column(Integer)
    flags: Mapped[str | None] = mapped_column(String(200))  # auto-check flags, comma separated
    reject_reason: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Material(Base):
    """Notes, slides, sample exams. Files live in Telegram; we only store identifiers."""

    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    professor_id: Mapped[int | None] = mapped_column(ForeignKey("professors.id"))
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # note/slides/exam/project/solution
    term: Mapped[str | None] = mapped_column(String(20))
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_size: Mapped[int | None] = mapped_column(Integer)
    tg_file_id: Mapped[str] = mapped_column(String(255))
    tg_file_unique_id: Mapped[str] = mapped_column(String(100), unique=True)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="approved")
    downloads: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
