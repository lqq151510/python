import { createFileRoute, Link } from "@tanstack/react-router"
import {
  BookOpen,
  Bot,
  Clapperboard,
  FileText,
  Image as ImageIcon,
  MessageSquare,
  ListChecks,
  NotebookPen,
  UserRound,
} from "lucide-react"

import { DevAssistHeader } from "@/components/Common/DevAssistLayout"
import useAuth from "@/hooks/useAuth"

const modules = [
  {
    to: "/chat",
    title: "问答工作台",
    text: "从项目文档里找答案，并看到依据。",
    icon: MessageSquare,
  },
  {
    to: "/knowledge",
    title: "我的知识库",
    text: "上传资料，按当前账号隔离检索。",
    icon: FileText,
  },
  {
    to: "/images",
    title: "图片检索",
    text: "用文字或图片找自己的图库。",
    icon: ImageIcon,
  },
  {
    to: "/agent",
    title: "多轮 Agent",
    text: "只读检索知识库，会话留在当前账号。",
    icon: Bot,
  },
  {
    to: "/notes",
    title: "学习笔记",
    text: "记下结论和待查问题，不和其他账号共享。",
    icon: NotebookPen,
  },
  {
    to: "/tasks",
    title: "复习清单",
    text: "把还没掌握的点标成待复习，完成后再勾掉。",
    icon: ListChecks,
  },
  {
    to: "/learning",
    title: "学习实验",
    text: "按需打开数据库和检索实验。",
    icon: BookOpen,
  },
  {
    to: "/aigc",
    title: "AI 视频生成",
    text: "提交本机任务并查看进度。",
    icon: Clapperboard,
  },
  {
    to: "/settings",
    title: "个人中心",
    text: "查看账号、修改资料和密码。",
    icon: UserRound,
  },
] as const

export const Route = createFileRoute("/_layout/")({
  component: HomePage,
  head: () => ({ meta: [{ title: "DevAssist" }] }),
})

function HomePage() {
  const { user } = useAuth()
  return (
    <section>
      <DevAssistHeader
        title="应用首页"
        description={`${user?.full_name || user?.email || "当前账号"} 的功能从这里进入。`}
      />
      <div className="da-module-grid">
        {modules.map((module) => (
          <Link key={module.to} to={module.to} className="da-module-card">
            <module.icon size={22} aria-hidden="true" />
            <strong>{module.title}</strong>
            <span>{module.text}</span>
          </Link>
        ))}
      </div>
    </section>
  )
}
