import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { type FormEvent, useState } from "react"

import { UsersService } from "@/client"
import { DevAssistHeader } from "@/components/Common/DevAssistLayout"
import { LoadingButton } from "@/components/ui/loading-button"
import useAuth from "@/hooks/useAuth"
import { listImages } from "@/lib/images"
import { listDocuments } from "@/lib/knowledge"
import { listNotes } from "@/lib/notes"
import { listStudyTasks } from "@/lib/tasks"

export const Route = createFileRoute("/_layout/settings")({
  component: PersonalCenter,
  head: () => ({ meta: [{ title: "个人中心 - DevAssist" }] }),
})

function countText(
  query: {
    isPending: boolean
    isError: boolean
    data?: { count?: number; open_count?: number }
  },
  field: "count" | "open_count",
) {
  if (query.isPending) return "…"
  if (query.isError) return "失败"
  const value = query.data?.[field]
  return value ?? "—"
}

function accountError(error: unknown): string {
  if (!(error instanceof AxiosError)) return "操作失败，请稍后重试。"
  const detail = error.response?.data?.detail
  const message = typeof detail === "string" ? detail : ""
  if (message === "Incorrect password") return "当前密码不正确。"
  if (message === "New password cannot be the same as the current one") {
    return "新密码不能和当前密码相同。"
  }
  if (message === "Super users are not allowed to delete themselves") {
    return "超级用户不能删除自己的账号。"
  }
  if (!error.response) return "无法连接服务，请确认后端已启动。"
  return message || "操作失败，请稍后重试。"
}

function PersonalCenter() {
  const { user, logout } = useAuth()
  const notes = useQuery({
    queryKey: ["notes", user?.id],
    queryFn: listNotes,
    enabled: Boolean(user),
  })
  const documents = useQuery({
    queryKey: ["documents", user?.id],
    queryFn: listDocuments,
    enabled: Boolean(user),
  })
  const images = useQuery({
    queryKey: ["images", user?.id],
    queryFn: listImages,
    enabled: Boolean(user),
  })
  const tasks = useQuery({
    queryKey: ["tasks", user?.id],
    queryFn: listStudyTasks,
    enabled: Boolean(user),
  })
  if (!user) return null
  const joined = user.created_at
    ? new Date(user.created_at).toLocaleDateString("zh-CN")
    : "未知"

  return (
    <section className="da-notes">
      <DevAssistHeader
        title="个人中心"
        description="查看当前账号，修改显示名、邮箱和密码。"
      />
      <div className="da-profile-grid">
        <div>
          <span>显示名</span>
          <strong>{user.full_name || "未填写"}</strong>
        </div>
        <div>
          <span>邮箱</span>
          <strong>{user.email}</strong>
        </div>
        <div>
          <span>身份</span>
          <strong>{user.is_superuser ? "超级用户" : "普通用户"}</strong>
        </div>
        <div>
          <span>注册日期</span>
          <strong>{joined}</strong>
        </div>
      </div>
      <div className="da-profile-grid">
        <Link to="/notes">
          <span>学习笔记</span>
          <strong>{countText(notes, "count")}</strong>
        </Link>
        <Link to="/tasks">
          <span>未完成复习</span>
          <strong>{countText(tasks, "open_count")}</strong>
        </Link>
        <Link to="/knowledge">
          <span>知识库文档</span>
          <strong>{countText(documents, "count")}</strong>
        </Link>
        <Link to="/images">
          <span>图片</span>
          <strong>{countText(images, "count")}</strong>
        </Link>
      </div>
      <ProfileForm />
      <PasswordForm />
      {user.is_superuser ? (
        <p className="da-empty">超级用户不能在这里删除自己的账号。</p>
      ) : (
        <DeleteOwnAccount onDeleted={logout} />
      )}
    </section>
  )
}

function ProfileForm() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [name, setName] = useState(user?.full_name ?? "")
  const [email, setEmail] = useState(user?.email ?? "")
  const [notice, setNotice] = useState("")
  const save = useMutation({
    mutationFn: () => {
      const body: { full_name?: string; email?: string } = {}
      if (name !== (user?.full_name ?? "")) body.full_name = name
      if (email !== user?.email) body.email = email
      return UsersService.updateUserMe({ body })
    },
    onSuccess: async () => {
      setNotice("资料已更新。")
      await queryClient.invalidateQueries({ queryKey: ["currentUser"] })
    },
    onError: (error) => setNotice(accountError(error)),
  })

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setNotice("")
    if (!email.trim()) {
      setNotice("邮箱不能为空。")
      return
    }
    save.mutate()
  }

  return (
    <form className="da-note-form" onSubmit={onSubmit}>
      <h2>账号资料</h2>
      <label>
        显示名
        <input
          value={name}
          maxLength={255}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label>
        邮箱
        <input
          type="email"
          required
          value={email}
          maxLength={255}
          onChange={(event) => setEmail(event.target.value)}
        />
      </label>
      <LoadingButton type="submit" loading={save.isPending}>
        保存资料
      </LoadingButton>
      {notice ? <p role="status">{notice}</p> : null}
    </form>
  )
}

function PasswordForm() {
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [notice, setNotice] = useState("")
  const save = useMutation({
    mutationFn: () =>
      UsersService.updatePasswordMe({
        body: {
          current_password: currentPassword,
          new_password: newPassword,
        },
      }),
    onSuccess: () => {
      setCurrentPassword("")
      setNewPassword("")
      setConfirmPassword("")
      setNotice("密码已更新。")
    },
    onError: (error) => setNotice(accountError(error)),
  })

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setNotice("")
    if (newPassword.length < 8) {
      setNotice("新密码至少 8 位。")
      return
    }
    if (newPassword !== confirmPassword) {
      setNotice("两次输入的新密码不一致。")
      return
    }
    save.mutate()
  }

  return (
    <form className="da-note-form" onSubmit={onSubmit}>
      <h2>修改密码</h2>
      <label>
        当前密码
        <input
          type="password"
          autoComplete="current-password"
          required
          value={currentPassword}
          onChange={(event) => setCurrentPassword(event.target.value)}
        />
      </label>
      <label>
        新密码
        <input
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          value={newPassword}
          onChange={(event) => setNewPassword(event.target.value)}
        />
      </label>
      <label>
        确认新密码
        <input
          type="password"
          autoComplete="new-password"
          required
          value={confirmPassword}
          onChange={(event) => setConfirmPassword(event.target.value)}
        />
      </label>
      <LoadingButton type="submit" loading={save.isPending}>
        更新密码
      </LoadingButton>
      {notice ? <p role="status">{notice}</p> : null}
    </form>
  )
}

function DeleteOwnAccount({ onDeleted }: { onDeleted: () => Promise<void> }) {
  const [confirmText, setConfirmText] = useState("")
  const [notice, setNotice] = useState("")
  const remove = useMutation({
    mutationFn: () => UsersService.deleteUserMe(),
    onSuccess: () => onDeleted(),
    onError: (error) => setNotice(accountError(error)),
  })

  return (
    <form
      className="da-note-form"
      onSubmit={(event) => {
        event.preventDefault()
        setNotice("")
        remove.mutate()
      }}
    >
      <h2>删除账号</h2>
      <p>删除后，这个账号下的笔记、知识库和图片会一并清除，不能恢复。</p>
      <label>
        输入“删除账号”以确认
        <input
          value={confirmText}
          onChange={(event) => setConfirmText(event.target.value)}
        />
      </label>
      <LoadingButton
        type="submit"
        variant="destructive"
        loading={remove.isPending}
        disabled={confirmText !== "删除账号"}
      >
        删除我的账号
      </LoadingButton>
      {notice ? <p role="status">{notice}</p> : null}
    </form>
  )
}
