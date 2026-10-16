import warnings
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

    RAG_WORKER_ENABLED: bool = False
    RAG_WORKER_URL: HttpUrl = HttpUrl("http://127.0.0.1:8765")
    RAG_WORKER_TIMEOUT_SECONDS: float = Field(default=30, gt=0, le=60)
    RAG_MIN_SIMILARITY: float = Field(default=0.3, ge=-1, le=1)
    RAG_VECTOR_STORE: Literal["milvus", "chroma"] = "milvus"
    RAG_CACHE_ENABLED: bool = False
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
