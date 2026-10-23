"""zebao-rag：图文混检的多模态检索底座。

命令行入口直接复用 cli.main，使 `uv run zebao-rag <子命令>` 与
`uv run python -m zebao_rag.cli <子命令>` 行为一致。
"""


def main() -> None:
    # Importing a utility (session store / HTTP worker) must not load Torch.
    from .cli import main as cli_main

    cli_main()


__all__ = ["main"]
