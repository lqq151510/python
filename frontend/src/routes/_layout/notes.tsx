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
  const [notice, setNotice] = useState("")
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
    mutationFn: ({ id, text }: { id: string; text: string }) =>
      updateNote(id, { body: text }),
    onSuccess: async () => {
      setNotice("笔记已更新。")
      await queryClient.invalidateQueries({ queryKey: ["notes", user?.id] })
    },
    onError: (error) => setNotice(describeNoteError(error)),
  })

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setNotice("")
    save.mutate({ title, body })
  }

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
      <ul className="da-note-list">
        {notes.data?.data.map((note) => (
          <NoteItem
            key={note.id}
            title={note.title}
            body={note.body}
            pending={remove.isPending || revise.isPending}
            onDelete={() => remove.mutate(note.id)}
            onSave={(text) => revise.mutate({ id: note.id, text })}
          />
        ))}
      </ul>
      {notes.data?.count === 0 ? <p>还没有笔记。先写下一句今天要记住的结论。</p> : null}
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
  onSave: (text: string) => void
}) {
  const [text, setText] = useState(body)
  return (
    <li>
      <h2>{title}</h2>
      <textarea
        aria-label={`${title}的内容`}
        value={text}
        maxLength={4000}
        rows={4}
        onChange={(event) => setText(event.target.value)}
      />
      <div>
        <LoadingButton
          type="button"
          variant="outline"
          loading={pending}
          onClick={() => onSave(text)}
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
