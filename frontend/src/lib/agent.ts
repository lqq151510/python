import { isAxiosError } from "axios"

import { client } from "@/client/client.gen"

const security = [{ scheme: "bearer", type: "http" }] as const

export interface AgentStep {
  name: string
  ok: boolean
  detail: string
}

export interface AgentTurn {
  id: string
  message: string
  answer: string
  steps: AgentStep[]
  stopped: boolean
  created_at: string
}

export interface AgentSession {
  id: string
  title: string
  turn_count: number
  created_at: string
  updated_at: string
}

export interface AgentTurnResult {
  session: AgentSession
  turn: AgentTurn
  request_id: string
}

export async function listAgentSessions(signal?: AbortSignal) {
  const response = await client.get<
    { 200: { data: AgentSession[]; count: number } },
    unknown,
    true
  >({
    url: "/api/v1/agent/sessions",
    signal,
    security: [...security],
    throwOnError: true,
  })
  return response.data
}

export async function readAgentSession(
  sessionId: string,
  signal?: AbortSignal,
) {
  const response = await client.get<
    { 200: { session: AgentSession; turns: AgentTurn[] } },
    unknown,
    true
  >({
    url: `/api/v1/agent/sessions/${encodeURIComponent(sessionId)}`,
    signal,
    security: [...security],
    throwOnError: true,
  })
  return response.data
}

export async function sendAgentTurn(message: string, sessionId?: string) {
  const response = await client.post<{ 200: AgentTurnResult }, unknown, true>({
    url: "/api/v1/agent/turns",
    security: [...security],
    body: {
      message,
      ...(sessionId ? { session_id: sessionId } : {}),
    },
    headers: { "Content-Type": "application/json" },
    timeout: 35_000,
    throwOnError: true,
  })
  return response.data
}

export function describeAgentError(error: unknown): string {
  if (isAxiosError(error)) {
    if (!error.response) return "无法连接服务，请确认后端已启动。"
    const detail = error.response.data?.detail
    if (typeof detail === "string") return detail
    if (detail && typeof detail.message === "string") return detail.message
  }
  return "操作失败，请稍后重试。"
}
