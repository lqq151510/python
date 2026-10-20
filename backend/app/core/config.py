import warnings
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    EmailStr,
    Field,
    HttpUrl,
    PostgresDsn,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CODE_WORKSPACE_DIR = Path(__file__).resolve().parents[2] / ".local" / "code-workspaces"
DEFAULT_AIGC_OUTPUT_DIR = Path(__file__).resolve().parents[2] / ".local" / "aigc"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"
    FASTAPI_ENV: Literal["development"] | None = None

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    DATABASE_URL: PostgresDsn

    DEEPSEEK_API_KEY: SecretStr | None = None
    DEEPSEEK_BASE_URL: HttpUrl = HttpUrl("https://api.deepseek.com")
    DEEPSEEK_MODEL: str = "deepseek-flash"
    CHAT_TOTAL_TIMEOUT_SECONDS: float = Field(default=30, gt=0, le=30)
    CHAT_CONNECT_TIMEOUT_SECONDS: float = Field(default=5, gt=0, le=5)
    CHAT_MAX_TOKENS: int = Field(default=1024, gt=0, le=1024)

    CODE_API_FORMAT: Literal[
        "openai_chat_completions",
        "openai_responses",
        "anthropic_messages",
        "gemini_generate_content",
    ] = "openai_chat_completions"
    CODE_API_KEY: SecretStr | None = None
    CODE_BASE_URL: HttpUrl | None = None
    CODE_MODEL: str | None = Field(default=None, min_length=1, max_length=128)
    CODE_MAX_OUTPUT_TOKENS: int = Field(default=4096, gt=0, le=4096)
    CODE_TIMEOUT_SECONDS: float = Field(default=60, gt=0, le=120)
    CODE_WORKSPACE_DIR: Path = DEFAULT_CODE_WORKSPACE_DIR

    # 本地 AIGC 视频生成（ComfyUI + MiniMax H3 MLX）。默认关闭：ComfyUI 要手动拉起，
    # 且单条 5 秒 864x480 在本机要跑约 12 分钟，必须串行执行。
    AIGC_ENABLED: bool = False
    AIGC_COMFY_URL: HttpUrl = HttpUrl("http://127.0.0.1:8188")
    AIGC_OUTPUT_DIR: Path = DEFAULT_AIGC_OUTPUT_DIR
    AIGC_MAX_QUEUE_DEPTH: int = Field(default=4, gt=0, le=20)
    AIGC_POLL_INTERVAL_SECONDS: float = Field(default=5, gt=0, le=60)
    AIGC_HTTP_TIMEOUT_SECONDS: float = Field(default=30, gt=0, le=120)
    AIGC_JOB_TIMEOUT_SECONDS: int = Field(default=3600, gt=0, le=21600)
    AIGC_MAX_PROMPT_CHARS: int = Field(default=2000, gt=0, le=2000)

    RAG_WORKER_ENABLED: bool = False
    RAG_WORKER_URL: HttpUrl = HttpUrl("http://127.0.0.1:8765")
    RAG_WORKER_TIMEOUT_SECONDS: float = Field(default=30, gt=0, le=60)
    RAG_MIN_SIMILARITY: float = Field(default=0.3, ge=-1, le=1)
    RAG_VECTOR_STORE: Literal["milvus", "chroma"] = "milvus"
    RAG_CACHE_ENABLED: bool = False
    # 换模型或加载 LoRA 适配器后必须换这个标识，否则缓存会复用旧模型的命中结果。
    RAG_EMBEDDING_IDENTITY: str = Field(
        default="qwen3-vl-text-v1", min_length=1, max_length=80
    )
    REDIS_URL: str = "redis://127.0.0.1:6379/1"
    MYSQL_DATABASE_URL: SecretStr | None = None

    @field_validator("RAG_WORKER_URL")
    @classmethod
    def _local_rag_worker(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "http" or value.host not in (
            "127.0.0.1",
            "localhost",
            "[::1]",
        ):
            raise ValueError("RAG worker must use a local HTTP loopback address")
        if (
            value.username
            or value.password
            or value.query
            or value.fragment
            or value.path not in (None, "/")
        ):
            raise ValueError("RAG worker URL must contain only scheme, host and port")
        return value

    @field_validator("AIGC_COMFY_URL")
    @classmethod
    def _local_comfy(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "http" or value.host not in (
            "127.0.0.1",
            "localhost",
            "[::1]",
        ):
            raise ValueError("ComfyUI must use a local HTTP loopback address")
        if (
            value.username
            or value.password
            or value.query
            or value.fragment
            or value.path not in (None, "/")
        ):
            raise ValueError("ComfyUI URL must contain only scheme, host and port")
        return value

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _use_psycopg_driver(cls, value: str | PostgresDsn) -> str:
        database_url = str(value)
        for scheme in ("postgres://", "postgresql://"):
            if database_url.startswith(scheme):
                return database_url.replace(scheme, "postgresql+psycopg://", 1)
        return database_url

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.FASTAPI_ENV == "development":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        for host in self.DATABASE_URL.hosts():
            self._check_default_secret("DATABASE_URL password", host["password"])
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )

        return self


settings = Settings()  # type: ignore # ty: ignore[unused-ignore-comment]
