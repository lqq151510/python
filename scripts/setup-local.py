#!/usr/bin/env python3
"""Create local development credentials once, without printing or overwriting them."""

import secrets
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / ".env"
    if target.exists():
        print(".env 已存在，保留现有配置。")
        return
    password = secrets.token_urlsafe(24)
    content = (root / ".env.example").read_text()
    content = content.replace("SECRET_KEY=\n", f"SECRET_KEY={secrets.token_urlsafe(48)}\n")
    content = content.replace(
        "FIRST_SUPERUSER_PASSWORD=\n",
        f"FIRST_SUPERUSER_PASSWORD={secrets.token_urlsafe(24)}\n",
    )
    content = content.replace("POSTGRES_PASSWORD=\n", f"POSTGRES_PASSWORD={password}\n")
    content = content.replace("YOUR_DATABASE_PASSWORD", password)
    with target.open("x") as output:
        target.chmod(0o600)
        output.write(content)
    print("已生成 .env；请在本地编辑器填写 DEEPSEEK_API_KEY。密钥和密码未输出。")


if __name__ == "__main__":
    main()
