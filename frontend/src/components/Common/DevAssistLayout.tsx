import { Link, useRouterState } from "@tanstack/react-router"
import {
  BookOpen,
  Code2,
  FileText,
  LogOut,
  MessageSquare,
  Settings,
  Sun,
  UserRound,
} from "lucide-react"
import { type ReactNode, useState } from "react"
import useAuth from "@/hooks/useAuth"
import "@/styles/devassist.css"

export function DevAssistLayout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  const path = useRouterState({ select: (state) => state.location.pathname })
  const [soft, setSoft] = useState(false)
  return (
    <div className={`da-shell ${soft ? "da-soft" : ""}`}>
      <a className="da-skip" href="#da-main">
        跳到主要内容
      </a>
      <aside className="da-sidebar" aria-label="主导航">
        <Link to="/chat" className="da-brand">
          <BookOpen size={32} aria-hidden="true" />
          <span>
            <strong>DevAssist</strong>
            <small>开发知识助手</small>
          </span>
        </Link>
        <nav className="da-nav">
          <Link to="/chat" aria-current={path === "/chat" ? "page" : undefined}>
            <MessageSquare size={22} aria-hidden="true" />
            问答工作台
          </Link>
          <Link
            to="/knowledge"
            aria-current={path === "/knowledge" ? "page" : undefined}
          >
            <FileText size={22} aria-hidden="true" />
            我的知识库
          </Link>
          <Link
            to="/learning"
            aria-current={path === "/learning" ? "page" : undefined}
          >
            <BookOpen size={22} aria-hidden="true" />
            学习实验
          </Link>
        </nav>
        <div className="da-sidebar-footer">
          <button
            type="button"
            className="da-appearance"
            aria-label={soft ? "切换为清爽浅色" : "切换为柔和浅色"}
            aria-pressed={soft}
            onClick={() => setSoft(!soft)}
          >
            <Sun size={22} aria-hidden="true" />
            <span>{soft ? "柔和浅色" : "清爽浅色"}</span>
          </button>
          <details className="da-account">
            <summary aria-label="我的账号菜单">
              <UserRound size={25} aria-hidden="true" />
              <span>{user?.full_name || "我的账号"}</span>
              <Settings size={17} aria-hidden="true" />
            </summary>
            <div className="da-account-menu">
              <p>{user?.email}</p>
              <Link to="/settings">账号设置</Link>
              <Link to="/">应用首页</Link>
              <Link to="/items">模板项目</Link>
              {user?.is_superuser && <Link to="/admin">用户管理</Link>}
              <button type="button" onClick={logout}>
                <LogOut size={16} aria-hidden="true" />
                退出登录
              </button>
            </div>
          </details>
        </div>
      </aside>
      <main id="da-main" className="da-workspace">
        {children}
      </main>
    </div>
  )
}

export function DevAssistHeader({
  knowledge = false,
  code = false,
}: {
  knowledge?: boolean
  code?: boolean
}) {
  const Icon = knowledge ? BookOpen : code ? Code2 : MessageSquare
  return (
    <header className="da-header">
      <div>
        <h1>
          <Icon size={31} aria-hidden="true" />
          {knowledge ? "我的知识库" : code ? "编程智能体" : "问答工作台"}
        </h1>
        <p>
          {knowledge
            ? "把项目资料整理好，让每个问题有据可查。"
            : code
              ? "从隔离工作区里的上传文件出发，制定计划并审阅代码改动。"
              : "从项目文档里找到答案，也看见依据。"}
        </p>
      </div>
      <div className="da-header-meta">
        <span className="da-badge">本地应用</span>
        <span>{knowledge ? "文档持久保存" : code ? "应用隔离沙箱" : "单轮问答"}</span>
      </div>
    </header>
  )
}
