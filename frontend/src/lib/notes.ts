import { isAxiosError } from "axios"

import { client } from "@/client/client.gen"

const security = [{ scheme: "bearer", type: "http" }] as const

export interface NoteRecord {
  id: string
  title: string
  body: string
  created_at: string
  updated_at: string
}

export async function listNotes() {
  const response = await client.get<
    { 200: { data: NoteRecord[]; count: number } },
    unknown,
    true
  >({
    url: "/api/v1/notes",
    security: [...security],
    throwOnError: true,
  })
  return response.data
}

export async function createNote(input: { title: string; body: string }) {
  const response = await client.post<{ 201: NoteRecord }, unknown, true>({
    url: "/api/v1/notes",
    security: [...security],
    body: input,
    throwOnError: true,
  })
  return response.data
}

export async function updateNote(
  id: string,
  input: { title?: string; body?: string },
) {
  const response = await client.patch<{ 200: NoteRecord }, unknown, true>({
    url: `/api/v1/notes/${encodeURIComponent(id)}`,
    security: [...security],
    body: input,
    throwOnError: true,
  })
  return response.data
}

export async function deleteNote(id: string) {
  await client.delete({
    url: `/api/v1/notes/${encodeURIComponent(id)}`,
    security: [...security],
    throwOnError: true,
  })
}

export function describeNoteError(error: unknown): string {
  if (isAxiosError(error)) {
    if (!error.response) return "无法连接服务，请确认后端已启动。"
    const detail = error.response.data?.detail
    if (typeof detail === "string") return detail
    if (error.response.status === 422) return "标题和内容不能为空。"
  }
  return "操作失败，请稍后重试。"
}
