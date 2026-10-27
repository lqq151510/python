import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { type FormEvent, useState } from "react"

import { DevAssistHeader } from "@/components/Common/DevAssistLayout"
import { LoadingButton } from "@/components/ui/loading-button"
import useAuth from "@/hooks/useAuth"
import {
  createStudyTask,
  deleteStudyTask,
  describeTaskError,
  listStudyTasks,
  updateStudyTask,
} from "@/lib/tasks"

export const Route = createFileRoute("/_layout/tasks")({
  component: TasksPage,
  head: () => ({ meta: [{ title: "复习清单 - DevAssist" }] }),
})

function TasksPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [title, setTitle] = useState("")
  const [detail, setDetail] = useState("")
  const [notice, setNotice] = useState("")
  const [showDone, setShowDone] = useState(false)
  const tasks = useQuery({
    queryKey: ["tasks", user?.id],
    queryFn: listStudyTasks,
    enabled: Boolean(user),
  })
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["tasks", user?.id] })
  const save = useMutation({
    mutationFn: createStudyTask,
    onSuccess: async () => {
      setTitle("")
      setDetail("")
      setNotice("已加入复习清单。")
      await refresh()
    },
    onError: (error) => setNotice(describeTaskError(error)),
  })
  const toggle = useMutation({
    mutationFn: ({ id, done }: { id: string; done: boolean }) =>
      updateStudyTask(id, { done }),
    onSuccess: refresh,
    onError: (error) => setNotice(describeTaskError(error)),
  })
  const remove = useMutation({
    mutationFn: deleteStudyTask,
    onSuccess: async () => {
      setNotice("已从清单删除。")
      await refresh()
    },
    onError: (error) => setNotice(describeTaskError(error)),
  })

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setNotice("")
    save.mutate({ title, detail })
  }

  const visible = (tasks.data?.data ?? []).filter((task) =>
    showDone ? task.done : !task.done,
  )

  return (
    <section className="da-notes">
      <DevAssistHeader
        title="复习清单"
        description={`未完成 ${tasks.data?.open_count ?? 0} 项。只保存在当前账号下。`}
      />
      <form className="da-note-form" onSubmit={onSubmit}>
        <label>
          要复习的内容
          <input
            value={title}
            maxLength={80}
            required
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          补充说明
          <textarea
            value={detail}
            maxLength={1000}
            rows={3}
            onChange={(event) => setDetail(event.target.value)}
          />
        </label>
        <LoadingButton type="submit" loading={save.isPending}>
          加入清单
        </LoadingButton>
        {notice ? <p role="status">{notice}</p> : null}
      </form>
      <div className="da-task-switch">
        <button type="button" aria-pressed={!showDone} onClick={() => setShowDone(false)}>
          未完成
        </button>
        <button type="button" aria-pressed={showDone} onClick={() => setShowDone(true)}>
          已完成
        </button>
      </div>
      <ul className="da-note-list">
        {visible.map((task) => (
          <li key={task.id}>
            <h2>{task.title}</h2>
            {task.detail ? <p>{task.detail}</p> : null}
            <div>
              <LoadingButton
                type="button"
                variant="outline"
                loading={toggle.isPending}
                onClick={() => toggle.mutate({ id: task.id, done: !task.done })}
              >
                {task.done ? "标为未完成" : "标为完成"}
              </LoadingButton>
              <LoadingButton
                type="button"
                variant="ghost"
                loading={remove.isPending}
                onClick={() => remove.mutate(task.id)}
              >
                删除
              </LoadingButton>
            </div>
          </li>
        ))}
      </ul>
      {tasks.data && visible.length === 0 ? (
        <p>{showDone ? "还没有完成的复习项。" : "未完成清单是空的。"}</p>
      ) : null}
    </section>
  )
}
