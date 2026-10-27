# 浅色界面接入与验收

验收日期：2026-10-11，Asia/Shanghai。

## 实现范围

沿用已选中的 `design/devassist-prototype/references/selected-light.png` 视觉方向，将浅色导航、问答工作区和来源面板接入真实主应用的 `/chat` 与 `/knowledge`。5174 端口仍是独立设计原型；实际应用运行在 5173，接口运行在 IPv4 回环地址 8000。

- `DevAssistLayout.tsx` 与 `devassist.css` 提供限定作用域的布局、窄屏适配、跳转至主要内容和账号菜单。现有登录鉴权、账号设置及模板功能入口保留。
- `chat.tsx` 继续使用真实聊天接口，增加“只检索原文”：调用已有文档检索接口，不请求模型。两种操作共享提交锁，开始新操作时清除旧结果。
- `KnowledgeSourcePanel.tsx` 显示本次来源，可切换片段，展示后端返回的文件名、标题、原文行号与纯文本内容。原文不会执行为 HTML。
- `knowledge.tsx` 保留真实上传、刷新列表、重复内容复用与删除接口；“带着这个问题去问答”传递查询词并启用知识库选项。
- 模板的默认主题改为浅色。账号菜单仍可访问原有功能；窄屏隐藏文字时，图标控件保留明确的可访问名称。

未修改 `.env`，未增加模型密钥、后端接口或数据库迁移。问答仍为单轮，刷新后不保存回答。BM25 仍是关键词检索，同义表达可能漏检。

## 验证证据

| 验证层 | 实际结果 |
|---|---|
| 静态检查 | 本次 10 个前端代码、样式和测试文件通过 Biome |
| 构建 | `bun run build`，TypeScript 与 Vite 构建成功 |
| 页面模拟回归 | Playwright 共 18 项通过，包含只检索原文不调用聊天接口、来源切换、查询传递及已有鉴权/异常/删除确认测试 |
| 真实浏览器与数据库 | 本地账号登录成功；合成 Markdown 上传返回 201；刷新后文档保留；同内容再次上传返回 200，数量不变 |
| 真实检索 | 合成关键词命中 2 个来源；工作台可切换并核对原文。现有“读取超时是否重试”查询命中 `model-errors.md` 的 3 个片段 |
| 真实缺配置分支 | 点击发送及独立 API 核验均得到 HTTP 503、`model_not_configured` 与请求标识；模型密钥为空 |
| 删除与保留边界 | 仅清理本次合成文档，先核对其内容 SHA-256；删除后专属关键词来源为 0；原有 3 份文档的 ID 集合不变 |
| 响应式与浏览器错误 | 1487、390、320 像素宽度均无横向溢出；390 像素账号菜单在可视范围内；最终页面浏览器错误日志为空 |

前端模拟测试不会调用真实模型。真实模型成功回答、回答引用忠实度和模型延迟尚未验证。此次没有修改后端代码，因此没有重复运行完整后端测试集；真实 PostgreSQL 与接口链路已单独核验。

命令从 `frontend/` 执行：

```bash
bun run build
PLAYWRIGHT_BROWSER_CHANNEL=chrome PLAYWRIGHT_BASE_URL=http://localhost:5173 bunx --no-install playwright test -c playwright.knowledge.config.ts
```

本地证据位于忽略的 `.local/`，不包含密钥：

- `light-ui-desktop.png`：真实主应用、原有文档检索及来源面板。
- `light-ui-mobile.png`：390 像素完整页面。
- `light-ui-knowledge.png`：原有 3 份文档与实际检索结果。
- `light-ui-real-check.json`：缺配置、删除后检索与原有文档保留的 API 断言结果。
- `light-ui-import.md`：本次合成验收文件，其数据库副本已清理。

## 回滚与后续

已有工作区包含此前的后端、接口、配置和知识库改动，此次未提交、推送或发布。不得使用全工作区 reset 回滚本次界面接入。

关键修改前副本位于 `.local/light-integration-baseline/`，覆盖布局路由、问答页、知识库页、主题入口和两份页面测试。回滚时逐文件比较并恢复；新建的 `DevAssistLayout.tsx`、`KnowledgeSourcePanel.tsx`、`devassist.css` 应在解除引用后移除。`KnowledgeSources.tsx` 沿用此前已有组件，回滚时保留来源内容与纯文本语义。路由树由生成器同步。

下一步接入真实模型时，先单独验证普通问答、带来源回答、未命中与超时分支，再评估回答是否忠实引用资料。当前成果是文档导入和检索闭环，尚未加入工具调用或有状态 Agent。
