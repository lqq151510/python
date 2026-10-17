import { isAxiosError } from "axios"

import { client } from "@/client/client.gen"
import type { KnowledgeSource, RetrievalMethod } from "@/lib/knowledge"

export interface ChatMessage {
  message: string
  useDocuments: boolean
  retrievalMethod?: RetrievalMethod
  documentId?: string
  clientRequestId?: string
}

export interface ChatResponse {
  answer: string
  request_id: string
  sources?: KnowledgeSource[]
  retrieval_method?: RetrievalMethod | null
  retrieval_warning?: string | null
}

export interface ChatErrorMessage {
  message: string
  requestId?: string
}

export async function sendChatMessage(
  input: ChatMessage,
): Promise<ChatResponse> {
  const response = await client.post<{ 200: ChatResponse }, unknown, true>({
    url: "/api/v1/chat",
    body: {
      message: input.message,
      ...(input.clientRequestId
        ? { client_request_id: input.clientRequestId }
        : {}),
      ...(input.useDocuments
        ? {
            use_documents: true,
            ...(input.retrievalMethod && input.retrievalMethod !== "bm25"
              ? { retrieval_method: input.retrievalMethod }
              : {}),
            ...(input.documentId ? { document_id: input.documentId } : {}),
          }
        : {}),
    },
    security: [{ scheme: "bearer", type: "http" }],
    headers: { "Content-Type": "application/json" },
    responseType: "json",
    throwOnError: true,
    timeout: 95_000,
  })

  if (
    typeof response.data?.answer !== "string" ||
    !response.data.answer.trim()
  ) {
    throw new Error("服务未返回有效回答，请重试。")
  }

  return {
    answer: response.data.answer,
    request_id:
      response.data.request_id || response.headers["x-request-id"] || "",
    sources: response.data.sources,
    retrieval_method: response.data.retrieval_method,
    retrieval_warning: response.data.retrieval_warning,
  }
}

export function describeChatError(error: unknown): ChatErrorMessage {
  if (!isAxiosError(error)) {
    return {
      message:
        error instanceof Error ? error.message : "请求失败，请稍后重试。",
    }
  }

  const requestId = error.response?.headers["x-request-id"]
  const detail = error.response?.data?.detail
  let message = "服务暂时不可用，请稍后重试。"

  if (!error.response) {
    message =
      error.code === "ECONNABORTED" || error.code === "ETIMEDOUT"
        ? "等待回答超时，请稍后重试。"
        : "无法连接服务，请检查网络或后端是否启动。"
  } else if (error.response.status === 422 && Array.isArray(detail)) {
    message = "问题格式不正确，请输入去除首尾空白后 1～4000 个字符的问题。"
  } else if ([401, 403].includes(error.response.status)) {
    message = "登录已失效，请重新登录。"
  } else if (
    detail &&
    typeof detail === "object" &&
    typeof detail.message === "string"
  ) {
    message = detail.message
  }

  return { message, requestId }
}
