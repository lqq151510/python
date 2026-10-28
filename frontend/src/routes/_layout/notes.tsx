import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { type FormEvent, useState } from "react"

import { DevAssistHeader } from "@/components/Common/DevAssistLayout"
import { LoadingButton } from "@/components/ui/loading-button"
import useAuth from "@/hooks/useAuth"
import {
  createNote,
  deleteNote,
  describeNoteError,
  listNotes,
  updateNote,
} from "@/lib/notes"

export const Route = createFileRoute("/_layout/notes")({
  component: NotesPage,
  head: () => ({ meta: [{ title: "学习笔记 - DevAssist" }] }),
})

function NotesPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [title, setTitle] = useState("")
  const [body, setBody] = useState("")
  const [keyword, setKeyword] = useState("")
  const [notice, setNotice] = useState("")
  const [retrying, setRetrying] = useState(false)
  const notes = useQuery({
    queryKey: ["notes", user?.id],
    queryFn: listNotes,
    enabled: Boolean(user),
  })
  const save = useMutation({
    mutationFn: createNote,
    onSuccess: async () => {
      setTitle("")
      setBody("")
      setNotice("笔记已保存，只在当前账号下可见。")
      await queryClient.invalidateQueries({ queryKey: ["notes", user?.id] })
    },
    onError: (error) => setNotice(describeNoteError(error)),
  })
  const remove = useMutation({
    mutationFn: deleteNote,
    onSuccess: async () => {
      setNotice("笔记已删除。")
      await queryClient.invalidateQueries({ queryKey: ["notes", user?.id] })
    },
    onError: (error) => setNotice(describeNoteError(error)),
  })
  const revise = useMutation({
    mutationFn: ({
      id,
      title: nextTitle,
      text,
    }: {
      id: string
      title: string
      text: string
    }) => updateNote(id, { title: nextTitle, body: text }),
    onSuccess: async () => {
      setNotice("笔记已更新。")
      await queryClient.invalidateQueries({ queryKey: ["notes", user?.id] })
    },
    onError: (error) => setNotice(describeNoteError(error)),
  })

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (save.isPending) return
    setNotice("")
    save.mutate({ title, body })
  }

  function onRetry() {
    if (retrying || notes.isFetching) return
    setRetrying(true)
    void notes.refetch().finally(() => setRetrying(false))
  }

  const needle = keyword.trim().toLowerCase()
  const visibleNotes = (notes.data?.data ?? []).filter((note) => {
    if (!needle) return true
    return (
      note.title.toLowerCase().includes(needle) ||
      note.body.toLowerCase().includes(needle)
    )
  })

  return (
    <section className="da-notes">
      <DevAssistHeader
        title="学习笔记"
        description="记下结论、待查问题和复习要点。笔记按登录账号隔离。"
      />
      <form className="da-note-form" onSubmit={onSubmit}>
        <label>
          标题
          <input
            value={title}
            maxLength={80}
            required
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          内容
          <textarea
            value={body}
            maxLength={4000}
            required
            rows={5}
            onChange={(event) => setBody(event.target.value)}
          />
        </label>
        <LoadingButton type="submit" loading={save.isPending}>
          保存笔记
        </LoadingButton>
        {notice ? <p role="status">{notice}</p> : null}
      </form>
      <label className="da-note-form">
        搜索笔记
        <input
          value={keyword}
          placeholder="标题或内容"
          onChange={(event) => setKeyword(event.target.value)}
        />
      </label>
      {notes.isPending && !retrying ? (
        <p className="da-empty" role="status">
          正在读取笔记…
        </p>
      ) : null}
      {notes.isError || retrying ? (
        <div className="da-inline-alert" role="alert">
          <p>笔记读取失败，请检查连接。</p>
          <button
            type="button"
            className="da-secondary mt-3"
            disabled={retrying || notes.isFetching}
            onClick={onRetry}
          >
            {retrying ? "正在重试…" : "重新加载"}
          </button>
        </div>
      ) : null}
      <ul className="da-note-list">
        {visibleNotes.map((note) => (
          <NoteItem
            key={note.id}
            title={note.title}
            body={note.body}
            pending={remove.isPending || revise.isPending}
            onDelete={() => remove.mutate(note.id)}
            onSave={(nextTitle, text) =>
              revise.mutate({ id: note.id, title: nextTitle, text })
            }
          />
        ))}
      </ul>
      {notes.data?.count === 0 ? (
        <p className="da-empty">还没有笔记。先写下一句今天要记住的结论。</p>
      ) : null}
      {notes.data && notes.data.count > 0 && visibleNotes.length === 0 ? (
        <p className="da-empty">没有匹配的笔记。</p>
      ) : null}
    </section>
  )
}

function NoteItem({
  title,
  body,
  pending,
  onDelete,
  onSave,
}: {
  title: string
  body: string
  pending: boolean
  onDelete: () => void
  onSave: (title: string, text: string) => void
}) {
  const [nextTitle, setNextTitle] = useState(title)
  const [text, setText] = useState(body)
  return (
    <li>
      <label>
        标题
        <input
          aria-label={`${title}的标题`}
          value={nextTitle}
          maxLength={80}
          onChange={(event) => setNextTitle(event.target.value)}
        />
      </label>
      <label>
        内容
        <textarea
          aria-label={`${title}的内容`}
          value={text}
          maxLength={4000}
          rows={4}
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      <div>
        <LoadingButton
          type="button"
          variant="outline"
          loading={pending}
          onClick={() => onSave(nextTitle, text)}
        >
          更新
        </LoadingButton>
        <LoadingButton
          type="button"
          variant="ghost"
          loading={pending}
          onClick={onDelete}
        >
          删除
        </LoadingButton>
      </div>
    </li>
  )
}
