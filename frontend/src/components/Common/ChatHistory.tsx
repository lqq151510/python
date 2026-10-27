import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Clock3, FileText, LoaderCircle, X } from "lucide-react"
import { useState } from "react"

import { LoadingButton } from "@/components/ui/loading-button"
import { listChatHistory, readChatHistory } from "@/lib/chat-history"
import { createNote, describeNoteError } from "@/lib/notes"
import { createStudyTask, describeTaskError } from "@/lib/tasks"

export function ChatHistory({ userId }: { userId: string }) {
  const [selectedId, setSelectedId] = useState("")
  const history = useQuery({
    queryKey: ["chat-history", userId],
    queryFn: listChatHistory,
    enabled: Boolean(userId),
  })
  const detail = useQuery({
    queryKey: ["chat-history-detail", userId, selectedId],
    queryFn: () => readChatHistory(selectedId),
    enabled: Boolean(userId && selectedId),
  })
  const queryClient = useQueryClient()
  const [savedNotice, setSavedNotice] = useState("")
  const saveNote = useMutation({
    mutationFn: () => {
      const item = detail.data
      if (!item) throw new Error("missing")
      const title = item.message.trim().slice(0, 80) || "问答笔记"
      const body = item.answer.trim().slice(0, 4000)
      return createNote({ title, body })
    },
    onSuccess: async () => {
      setSavedNotice("已存入学习笔记。")
      await queryClient.invalidateQueries({ queryKey: ["notes", userId] })
    },
    onError: (error) => setSavedNotice(describeNoteError(error)),
  })
  const saveTask = useMutation({
    mutationFn: () => {
      const item = detail.data
      if (!item) throw new Error("missing")
      return createStudyTask({
        title: item.message.trim().slice(0, 80) || "复习这条问答",
        detail: item.answer.trim().slice(0, 1000),
      })
    },
    onSuccess: async () => {
      setSavedNotice("已加入复习清单。")
      await queryClient.invalidateQueries({ queryKey: ["tasks", userId] })
    },
    onError: (error) => setSavedNotice(describeTaskError(error)),
  })

  return (
    <section className="da-chat-history" aria-label="问答历史">
      <details>
        <summary>
          <Clock3 size={17} aria-hidden="true" />
          查看历史
          <span>{history.data?.count ?? 0}</span>
        </summary>
        {history.isPending ? (
          <p className="da-chat-history-state">
            <LoaderCircle className="animate-spin" size={16} />{" "}
            正在读取历史记录…
          </p>
        ) : history.isError ? (
          <p className="da-chat-history-state" role="alert">
            历史记录读取失败，请检查连接后重试。
          </p>
        ) : history.data.data.length === 0 ? (
          <p className="da-chat-history-state">成功完成的问答会保存在这里。</p>
        ) : (
          <ol className="da-chat-history-list">
            {history.data.data.map((turn) => (
              <li key={turn.id}>
                <button
                  type="button"
                  className={selectedId === turn.id ? "is-selected" : ""}
                  onClick={() => setSelectedId(turn.id)}
                >
                  <strong>{turn.message}</strong>
                  <small>
                    {new Date(turn.created_at).toLocaleString("zh-CN", {
                      dateStyle: "short",
                      timeStyle: "short",
                    })}
                    {turn.source_count > 0 && ` · ${turn.source_count} 个引用`}
                  </small>
                  <span>{turn.answer_preview}</span>
                </button>
              </li>
            ))}
          </ol>
        )}
      </details>

      {selectedId && (
        <section className="da-chat-history-detail" aria-label="历史问答详情">
          <header>
            <h2>历史问答详情</h2>
            <button
              type="button"
              aria-label="关闭历史详情"
              onClick={() => setSelectedId("")}
            >
              <X size={17} aria-hidden="true" />
            </button>
          </header>
          {detail.isPending ? (
            <p className="da-chat-history-state">
              <LoaderCircle className="animate-spin" size={16} /> 正在读取…
            </p>
          ) : detail.isError ? (
            <p className="da-chat-history-state" role="alert">
              这条历史记录暂时无法读取。
            </p>
          ) : (
            <>
              <h3>问题</h3>
              <p>{detail.data.message}</p>
              <h3>回答</h3>
              <p className="da-chat-history-answer">{detail.data.answer}</p>
              <div>
                <LoadingButton
                  type="button"
                  variant="outline"
                  loading={saveNote.isPending}
                  disabled={!detail.data.answer.trim()}
                  onClick={() => {
                    setSavedNotice("")
                    saveNote.mutate()
                  }}
                >
                  存成笔记
                </LoadingButton>
                <LoadingButton
                  type="button"
                  variant="outline"
                  loading={saveTask.isPending}
                  disabled={!detail.data.message.trim()}
                  onClick={() => {
                    setSavedNotice("")
                    saveTask.mutate()
                  }}
                >
                  加入复习
                </LoadingButton>
              </div>
              {savedNotice ? <p role="status">{savedNotice}</p> : null}
              {detail.data.retrieval_warning && (
                <p className="da-chat-history-warning">
                  本次检索发生降级：{detail.data.retrieval_warning}
                </p>
              )}
              {detail.data.sources.length > 0 && (
                <div className="da-chat-history-sources">
                  <h3>
                    <FileText size={16} aria-hidden="true" /> 引用来源
                  </h3>
                  <ol>
                    {detail.data.sources.map((source) => (
                      <li key={source.chunk_id}>
                        <strong>
                          [{source.citation}] {source.filename} ·{" "}
                          {source.heading}
                        </strong>
                        <small>
                          第 {source.line_start}–{source.line_end} 行
                        </small>
                        <p>{source.content}</p>
                      </li>
                    ))}
                  </ol>
                </div>
              )}
              <small className="da-chat-history-request">
                请求编号：{detail.data.request_id}
              </small>
            </>
          )}
        </section>
      )}
    </section>
  )
}
