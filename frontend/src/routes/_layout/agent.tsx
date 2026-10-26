import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Bot, Send } from "lucide-react"
import { type FormEvent, useState } from "react"
import { DevAssistHeader } from "@/components/Common/DevAssistLayout"
import { LoadingButton } from "@/components/ui/loading-button"
import useAuth from "@/hooks/useAuth"
import {
  describeAgentError,
  listAgentSessions,
  readAgentSession,
  sendAgentTurn,
} from "@/lib/agent"

export const Route = createFileRoute("/_layout/agent")({
  component: AgentPage,
  head: () => ({ meta: [{ title: "多轮 Agent - DevAssist" }] }),
})

function AgentPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [message, setMessage] = useState("")
  const [inputError, setInputError] = useState("")
  const sessions = useQuery({
    queryKey: ["agent-sessions", user?.id],
    queryFn: ({ signal }) => listAgentSessions(signal),
    enabled: Boolean(user),
    retry: false,
  })
  const detail = useQuery({
    queryKey: ["agent-session", user?.id, sessionId],
    queryFn: ({ signal }) => readAgentSession(sessionId as string, signal),
    enabled: Boolean(user && sessionId),
    retry: false,
  })
  const send = useMutation({
    mutationFn: (input: { message: string; sessionId?: string }) =>
      sendAgentTurn(input.message, input.sessionId),
    retry: false,
    onSuccess: async (result) => {
      setSessionId(result.session.id)
      setMessage("")
      await queryClient.invalidateQueries({
        queryKey: ["agent-sessions", user?.id],
      })
      await queryClient.invalidateQueries({
        queryKey: ["agent-session", user?.id, result.session.id],
      })
    },
  })

  async function handleSend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (send.isPending || (sessionId && !detail.isSuccess)) return
    setInputError("")
    const trimmed = message.trim()
    if (!trimmed || Array.from(trimmed).length > 4000) {
      setInputError("请输入 1～4000 个字符。")
      return
    }
    try {
      await send.mutateAsync({
        message: trimmed,
        sessionId: sessionId ?? undefined,
      })
    } catch {
      // 错误显示在页面上。
    }
  }

  const turns = detail.isSuccess ? detail.data.turns : []
  const canSend = !send.isPending && (!sessionId || detail.isSuccess)
  return (
    <>
      <DevAssistHeader
        title="多轮 Agent"
        description="按当前账号续聊。它只会只读检索你的文档和图片，最多 5 步，不能跑命令。"
        badge="最多 5 步"
      />
      <div className="da-knowledge">
        <p className="da-note">
          这不是问答页里的聊天记录，也不是编程沙箱。会话只属于当前登录用户。库里没有内容时，第一轮不会调用模型。
        </p>
        <div className="da-agent-layout">
          <section aria-label="我的会话" className="da-document-library">
            <div className="da-library-heading">
              <h2>我的会话</h2>
              <button
                type="button"
                className="da-secondary"
                disabled={send.isPending}
                onClick={() => {
                  setSessionId(null)
                  send.reset()
                  setInputError("")
                }}
              >
                新会话
              </button>
            </div>
            {sessions.isPending && (
              <p role="status" className="da-note">
                正在加载会话…
              </p>
            )}
            {sessions.isError && (
              <div role="alert" className="da-inline-error">
                <p>会话列表加载失败。{describeAgentError(sessions.error)}</p>
                <button
                  type="button"
                  className="da-secondary"
                  disabled={sessions.isFetching}
                  onClick={() => sessions.refetch()}
                >
                  重试加载会话
                </button>
              </div>
            )}
            {sessions.isSuccess && sessions.data.data.length === 0 && (
              <div className="da-library-empty">
                <Bot size={28} aria-hidden="true" />
                <p>还没有会话。提一个问题就会开始。</p>
              </div>
            )}
            <div className="da-document-list">
              {sessions.isSuccess &&
                sessions.data.data.map((session) => (
                  <button
                    key={session.id}
                    type="button"
                    className="da-document-row"
                    disabled={send.isPending}
                    aria-current={session.id === sessionId ? "true" : undefined}
                    onClick={() => {
                      setSessionId(session.id)
                      send.reset()
                      setInputError("")
                    }}
                  >
                    <strong>{session.title}</strong>
                    <small>{session.turn_count} 轮</small>
                  </button>
                ))}
            </div>
          </section>
          <section aria-label="当前对话" className="da-search-results">
            {sessionId && detail.isPending && (
              <p role="status" className="da-note">
                正在加载对话…
              </p>
            )}
            {sessionId && detail.isError && (
              <div role="alert" className="da-inline-error">
                <p>对话加载失败。{describeAgentError(detail.error)}</p>
                <button
                  type="button"
                  className="da-secondary"
                  disabled={detail.isFetching}
                  onClick={() => detail.refetch()}
                >
                  重试加载对话
                </button>
              </div>
            )}
            {(!sessionId || detail.isSuccess) && turns.length === 0 && (
              <p className="da-note">从下面输入第一轮问题。</p>
            )}
            {turns.map((turn) => (
              <article key={turn.id} className="da-document-row">
                <p>
                  <strong>你：</strong>
                  {turn.message}
                </p>
                <p>
                  <strong>助手：</strong>
                  {turn.answer}
                </p>
                {turn.stopped && (
                  <p className="da-inline-error">这一轮已在 5 步后停止。</p>
                )}
                <ul>
                  {turn.steps.map((step, index) => (
                    <li key={`${turn.id}-${index}`}>
                      {step.ok ? "检索" : "工具失败"}：{step.name}
                      <small>{step.detail}</small>
                    </li>
                  ))}
                </ul>
              </article>
            ))}
            <form onSubmit={handleSend} className="da-library-search">
              <label htmlFor="agent-message">继续这一轮会话</label>
              <textarea
                id="agent-message"
                value={message}
                rows={4}
                disabled={!canSend}
                onChange={(event) => setMessage(event.target.value)}
                placeholder="例如：刚才那个文件里，超时是怎么处理的？"
              />
              {inputError && (
                <p role="alert" className="da-inline-error">
                  {inputError}
                </p>
              )}
              {send.error && (
                <p role="alert" className="da-inline-error">
                  {describeAgentError(send.error)}
                </p>
              )}
              <LoadingButton
                className="da-primary"
                type="submit"
                disabled={!canSend}
                loading={send.isPending}
              >
                <Send aria-hidden="true" />
                {send.isPending ? "正在思考…" : "发送"}
              </LoadingButton>
            </form>
          </section>
        </div>
      </div>
    </>
  )
}
