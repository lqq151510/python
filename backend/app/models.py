import uuid
from datetime import UTC, datetime

from pydantic import EmailStr
from sqlalchemy import DateTime, Index, Text, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(UTC)


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(SQLModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_superuser: bool | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(default_factory=get_datetime_utc)
    items: list[Item] = Relationship(back_populates="owner", cascade_delete=True)


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Shared properties
class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Properties to receive on item creation
class ItemCreate(ItemBase):
    pass


# Properties to receive on item update
class ItemUpdate(SQLModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Database model, database table inferred from class name
class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(default_factory=get_datetime_utc)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="items")


# Properties to return via API, id is always required
class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class Document(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("owner_id", "sha256", name="uq_document_owner_sha256"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    filename: str = Field(max_length=255)
    sha256: str = Field(max_length=64)
    size_bytes: int
    chunk_count: int
    created_at: datetime = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)
    )


class DocumentChunk(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("document_id", "position", name="uq_documentchunk_position"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    document_id: uuid.UUID = Field(
        foreign_key="document.id", ondelete="CASCADE", index=True
    )
    position: int
    heading: str = Field(max_length=255)
    line_start: int
    line_end: int
    content: str = Field(sa_type=Text)


class ChatTurn(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "client_request_id", name="uq_chatturn_owner_client_request"
        ),
        UniqueConstraint("request_id", name="uq_chatturn_request_id"),
        Index("ix_chatturn_owner_created_at", "owner_id", "created_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE")
    client_request_id: uuid.UUID | None = Field(default=None)
    request_hash: str = Field(max_length=64)
    request_id: str = Field(max_length=36)
    message: str = Field(sa_type=Text)
    answer: str = Field(sa_type=Text)
    sources_json: str = Field(default="[]", sa_type=Text)
    retrieval_method: str | None = Field(default=None, max_length=16)
    created_at: datetime = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)
    )

    retrieval_warning: str | None = Field(default=None, max_length=64)


class AigcJob(SQLModel, table=True):
    """一次 AIGC 视频生成任务。

    状态机：queued -> running -> succeeded | failed | canceled。
    ComfyUI 单卡串行，所以 queued 允许排队；progress 是按下单时给出的耗时公式
    估算的（ComfyUI 只提供 websocket 级别的逐步骤进度，走 HTTP 拿不到）。
    """

    __table_args__ = (
        Index("ix_aigcjob_owner_created_at", "owner_id", "created_at"),
        Index("ix_aigcjob_status_created_at", "status", "created_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    kind: str = Field(default="text2video", max_length=16)
    status: str = Field(default="queued", max_length=16, index=True)
    prompt: str = Field(sa_type=Text)
    params_json: str = Field(sa_type=Text)
    status_message: str | None = Field(default=None, max_length=120)
    progress: int = Field(default=0)
    comfy_prompt_id: str | None = Field(default=None, max_length=64)
    input_image_relpath: str | None = Field(default=None, max_length=512)
    input_image_name: str | None = Field(default=None, max_length=255)
    output_relpath: str | None = Field(default=None, max_length=512)
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=500)
    created_at: datetime = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)
    )
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
