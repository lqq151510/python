# DevAssist 项目统一说明

2026-10-31 按泽宝要求，将 DevAssist 与同级 zebao-rag 收拢为一个维护项目。
唯一开发根目录是 `devassist/`，不再维护第二份 RAG 源码。

## 目录与入口

| 位置 | 职责 | 从 DevAssist 根目录执行 |
| --- | --- | --- |
| `frontend/` | React 页面 | 随联合启动入口运行 |
| `backend/` | FastAPI、用户权限、业务数据 | `bash scripts/devassist-dev.sh` |
| `services/zebao-rag/` | 检索 worker、本地 Agent/代码生成 CLI、评估工具 | `bash scripts/devassist-dev.sh --with-rag` |
| `scripts/rag-worker.sh` | 仅启动检索 worker | `bash scripts/rag-worker.sh` |
| `scripts/rag-cli.sh` | 从主项目调用本地 CLI | `bash scripts/rag-cli.sh --help` |
| `.local/legacy-zebao-rag-20261031/` | 本机旧项目完整归档，不参与运行或提交 | 不直接在此开发 |

发布入口仍为 `scripts/devassist-release.sh`，详见 [发布指南](RELEASE_CANDIDATE.md)。
这次目录合并没有启动、重启或发布现有应用。

## 源码与依赖的选择

旧项目的 12 个 Python 模块全部已存在于 `services/zebao-rag/src/zebao_rag`。
其中 7 个逐字相同，5 个由主项目增加了配置化、懒加载、模型身份或代码输出处理；
主项目还有 HTTP worker、Milvus 适配与测试。因此本次没有用旧代码或旧锁文件覆盖主项目。
`UPSTREAM.json` 保留首次导入时的哈希证据，不改写成当前版本哈希。

后端使用 Python 3.14，RAG 模块使用 Python 3.12，继续各用自己的虚拟环境和锁文件。
根 `pyproject.toml` 对 RAG 模块的 workspace 排除项是有意保留的依赖隔离。
合并的是维护目录与入口，不是把模型依赖混装进 Web 后端环境。

CLI 入口固定使用主项目源码，默认禁止 Hugging Face 自动下载，不自动安装依赖。
缺少模块环境时按脚本提示手动准备；模型调用命令仍需要本机对应模型或显式配置的服务。
帮助命令不加载模型、不打开资料；`sessions`、`build`、`chat` 等实际子命令保留原有行为。

## 旧资产的保留边界

旧目录通过同一文件系统的目录重命名整体移入 `.local/legacy-zebao-rag-20261031/`，
包括个人资料、会话、Chroma 索引、模型、4 个辅助脚本、旧 `.venv` 和 Git 元数据。
没有删除文件、重建索引、导入网页账号或加载模型。
归档前未发现打开旧目录文件的进程，也未发现 Ollama 模型目录中指向旧目录的符号链接；
这不能证明所有外部工具配置都没有历史路径。

归档前后 37,321 个目录项（含 32,943 个普通文件）的相对路径、设备、inode、权限、
大小、修改时间和符号链接目标一致。使用 inode 核对确认同一批文件原地移动，
没有逐字节读取约 11 GB 的私人资产来做内容哈希。
本机清单与结果位于 `.local/project-consolidation-20261031/`，清单包含私人文件名，
不应提交、粘贴到聊天或作为公开文档附件。

`.local/` 已被 Git、Docker 构建上下文及 Cursor 索引忽略。
RAG 模块下的 `models/` 也明确加入 Git 与 Docker 忽略，避免以后误收模型文件。
这些归档不会跟随 Git clone 或远端推送传输；搬电脑时需要单独备份。

没有保留原同级目录的符号链接。旧环境 shebang、脚本及索引中的绝对路径可能仍指向
原目录，因此归档不是可以直接搬到新路径继续运行的环境。
默认 CLI 使用主模块的 `data/`、`chroma_db/`，不会自动打开旧个人资料。
旧 CLI 按主题集合组织数据，网页按账号授权；如需迁入网页，需另行确定账号、资料范围、
模型身份和索引兼容性，不能仅复制索引目录。

## 验证与回滚

入口验证采用临时假解释器检查参数、工作目录、导入路径、环境变量和退出码，
不通过真实生成请求来证明脚本正确性。现有 RAG 离线测试另行检查模拟调用和隔离检索。
这类验证不代表真实模型回答效果、已有数据库联调或公网发布通过。

本次实际结果：

- 统一入口的 6 项假解释器测试通过；Bash 语法、ShellCheck、目标 Python 测试文件 Ruff 和 diff 检查通过。
- RAG 的 codegen、identity、worker、eval_protocol 离线回归共 23 项通过；
  使用模拟模型、临时内存 Chroma 和模拟 Milvus，不访问现有数据库。
- 真实主模块解释器运行 `sessions` 成功，退出码 0、无 stderr；数据与 Chroma 路径显式指定为
  一次性临时目录，返回无存档会话，未加载模型或读取旧会话。
- 独立复核归档当前清单，与归档前后两份清单完全一致。

如需恢复旧目录，先关闭归档目录中的编辑器或运行进程，再从 DevAssist 根目录执行：

```bash
python3 - <<'PY'
from pathlib import Path
import os

root = Path.cwd()
if not (root / "docs/PROJECT_LAYOUT.md").is_file():
    raise SystemExit("请从 DevAssist 根目录执行")
archive = root / ".local/legacy-zebao-rag-20261031"
original = root.parent / "zebao-rag"
if not archive.is_dir() or archive.is_symlink():
    raise SystemExit("归档不存在或不是预期目录")
if original.exists() or original.is_symlink():
    raise SystemExit("旧路径已存在，停止以避免覆盖")
os.rename(archive, original)
PY
```

本次入口与文档改动可以按本次 diff 单独撤销。不要使用全仓 reset 或清理 `.local/` 的命令；
主项目原有业务数据、环境配置、模型和 Git 历史均不需要回滚。
