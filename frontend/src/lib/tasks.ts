import { isAxiosError } from "axios"

import { client } from "@/client/client.gen"

const security = [{ scheme: "bearer", type: "http" }] as const

export interface StudyTaskRecord {
  id: string
  title: string
  detail: string
  done: boolean
  created_at: string
  updated_at: string
}

export interface StudyTaskList {
  data: StudyTaskRecord[]
  count: number
  open_count: number
}

export async function listStudyTasks() {
  const response = await client.get<{ 200: StudyTaskList }, unknown, true>({
    url: "/api/v1/tasks",
    security: [...security],
    throwOnError: true,
  })
  return response.data
}

export async function createStudyTask(input: { title: string; detail: string }) {
  const response = await client.post<{ 201: StudyTaskRecord }, unknown, true>({
    url: "/api/v1/tasks",
    security: [...security],
    body: input,
    throwOnError: true,
  })
  return response.data
}

export async function updateStudyTask(
  id: string,
  input: { title?: string; detail?: string; done?: boolean },
) {
  const response = await client.patch<{ 200: StudyTaskRecord }, unknown, true>({
    url: `/api/v1/tasks/${encodeURIComponent(id)}`,
    security: [...security],
    body: input,
    throwOnError: true,
  })
  return response.data
}

export async function deleteStudyTask(id: string) {
  await client.delete({
    url: `/api/v1/tasks/${encodeURIComponent(id)}`,
    security: [...security],
    throwOnError: true,
  })
}

export function describeTaskError(error: unknown): string {
  if (isAxiosError(error)) {
    if (!error.response) return "无法连接服务，请确认后端已启动。"
    const detail = error.response.data?.detail
    if (typeof detail === "string") return detail
    if (error.response.status === 422) return "标题不能为空。"
  }
  return "操作失败，请稍后重试。"
}
