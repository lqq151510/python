"""集中配置：路径、模型名、集合名。不写任何密钥。"""
import os
from pathlib import Path

# 模型：多模态向量模型，文本/图片统一编码为 2048 维
MODEL_NAME = os.environ.get("ZEBAO_RAG_MODEL", "Qwen/Qwen3-VL-Embedding-2B")
EMBED_DIM = 2048

# 项目根目录（src/zebao_rag/config.py 向上三级）
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("ZEBAO_RAG_DATA_DIR", ROOT / "data")).expanduser().resolve()
DOCS_DIR = DATA_DIR / "docs"
IMAGES_DIR = DATA_DIR / "images"

# Chroma 持久化目录：删除整个目录即可彻底抹掉全部向量
CHROMA_DIR = Path(os.environ.get("ZEBAO_RAG_CHROMA_DIR", ROOT / "chroma_db")).expanduser().resolve()
COLLECTION_NAME = "zebao_multimodal"

# 个人资料（获奖证书等）单独建集合，与技术笔记/药品图隔离，便于单独删除
PERSONAL_DOCS_DIR = DATA_DIR / "personal" / "docs"
PERSONAL_IMAGES_DIR = DATA_DIR / "personal" / "images"
PERSONAL_COLLECTION = "zebao_personal"

# 日语学习资料单独建集合：原文件在 source/，提取出的纯文本在 docs/（本轮无配图）
JAPANESE_DOCS_DIR = DATA_DIR / "japanese" / "docs"
JAPANESE_IMAGES_DIR = DATA_DIR / "japanese" / "images"
JAPANESE_COLLECTION = "zebao_japanese"

# 分块参数（先小步快跑，后续按检索效果调）
CHUNK_MAX_CHARS = 500
CHUNK_OVERLAP = 80

# 本地 Ollama（Agent 用）：只走本机回环地址，不含任何密钥
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
AGENT_MODEL = os.environ.get("ZEBAO_RAG_AGENT_MODEL", "qwen-fast:latest")
AGENT_MAX_STEPS = 5          # 单轮问答的最大工具调用步数，防死循环
AGENT_TIMEOUT_S = 120.0      # 单次请求超时
AGENT_NUM_CTX = 8192         # 上下文窗口：够用且不撑爆内存

# 会话落盘：对话存本地 JSON，可跨进程续聊（含个人内容，不提交仓库）
SESSIONS_DIR = DATA_DIR / "sessions"
AGENT_DEFAULT_SESSION = "default"
AGENT_KEEP_TURNS = 4         # 原文逐字保留的轮数；更早的轮次压成摘要
AGENT_SUMMARY_MAX_CHARS = 400  # 摘要长度上限，防止摘要本身膨胀
AGENT_SUMMARY_TIMEOUT_S = 90.0  # 摘要单独超时，失败则退化为丢弃旧轮次
