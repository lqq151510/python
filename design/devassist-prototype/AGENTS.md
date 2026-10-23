# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.

## DevAssist 的已确认设计

- 泽宝反馈初稿“太黑了”，已选择最新浅色稿的第 1 张。
- 视觉来源：`references/selected-light.png`；保持左导航、中央回答与输入、右侧来源原文的结构。
- 使用浅色表面、深色正文与少量青绿色强调；默认采用清爽浅色，可切换柔和浅色。
- 图标沿用最接近设计稿的 Lucide 线框图标；无插画或照片资产。
- 此目录是前端交互原型：不用应用认证，不读 `.env`，不调用模型或后端，不写应用数据库。
- 文档只在浏览器当前页面内存中保留，刷新恢复示例；问答保持单轮。
- 不新增聊天历史、Agent、PDF 支持、团队或模型密钥输入界面。
