#!/usr/bin/env python3
"""Create a separate local release configuration without reading the developer env."""

import os
import secrets
from pathlib import Path


def main() -> None:
    target = Path(__file__).resolve().parents[1] / ".env.release"
    content = (
        "# Local release only; keep this file private and out of Git.\n"
        "PROJECT_NAME=DevAssist\n"
        "RELEASE_PORT=8088\n"
        "RELEASE_TAG=local\n"
        f"SECRET_KEY={secrets.token_hex(32)}\n"
        f"POSTGRES_PASSWORD={secrets.token_hex(24)}\n"
        "FIRST_SUPERUSER=admin@devassist.example.com\n"
        f"FIRST_SUPERUSER_PASSWORD={secrets.token_urlsafe(24)}\n"
        "# Optional model configuration. Blank keys allow document-only demos.\n"
        "DEEPSEEK_API_KEY=\n"
        "DEEPSEEK_BASE_URL=https://api.deepseek.com\n"
        "DEEPSEEK_MODEL=deepseek-flash\n"
    )
    try:
        # O_EXCL rejects existing files and symlinks; 0600 applies at creation.
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(".env.release 已存在，保留现有配置。")
        return
    with os.fdopen(descriptor, "w") as output:
        output.write(content)
    print("已生成独立的 .env.release（仅本人可读写）。")
    print("登录密码请在本地编辑器查看 FIRST_SUPERUSER_PASSWORD，勿复制到聊天或 Git。")
    print("启动：bash scripts/devassist-release.sh --env-file .env.release up")


if __name__ == "__main__":
    main()
