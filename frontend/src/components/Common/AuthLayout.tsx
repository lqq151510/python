import { BookOpen, Code2, Search } from "lucide-react"
import { Appearance } from "@/components/Common/Appearance"

interface AuthLayoutProps {
  children: React.ReactNode
}

export function AuthLayout({ children }: AuthLayoutProps) {
  return (
    <div className="grid min-h-svh lg:grid-cols-2">
      <div className="bg-muted dark:bg-zinc-900 relative hidden lg:flex lg:items-center lg:justify-center">
        <div className="max-w-md space-y-8 p-10">
          <div className="flex items-center gap-3">
            <img src="/devassist.svg" alt="" className="size-12" />
            <span className="text-3xl font-semibold tracking-tight">
              DevAssist
            </span>
          </div>
          <p className="text-2xl font-medium leading-relaxed">
            让资料、笔记和代码，
            <br />
            成为解决问题的线索。
          </p>
          <p className="leading-7 text-muted-foreground">
            你的项目知识与故障排查助手。从原文检索开始，在同一个工作台整理思路、核对来源。
          </p>
          <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
            <span className="flex items-center gap-2">
              <BookOpen size={16} />
              资料归档
            </span>
            <span className="flex items-center gap-2">
              <Search size={16} />
              来源核对
            </span>
            <span className="flex items-center gap-2">
              <Code2 size={16} />
              代码工作台
            </span>
          </div>
        </div>
      </div>
      <div className="flex flex-col gap-4 p-6 md:p-10">
        <div className="flex items-center justify-between gap-4">
          <span className="font-semibold lg:invisible">DevAssist</span>
          <Appearance />
        </div>
        <div className="flex flex-1 items-center justify-center">
          <div className="w-full max-w-xs">{children}</div>
        </div>
        <p className="text-center text-xs leading-5 text-muted-foreground">
          DevAssist · 项目知识与故障排查助手
        </p>
      </div>
    </div>
  )
}
