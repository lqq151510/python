import { isAxiosError } from "axios"

import { client } from "@/client/client.gen"

const security = [{ scheme: "bearer", type: "http" }] as const

export interface ImageRecord {
  id: string
  filename: string
  size_bytes: number
  media_type: string
  created_at: string
}

export interface ImageHit {
  id: string
  filename: string
  media_type: string
  score: number
}

export interface ImageSearchResult {
  kind: string
  hits: ImageHit[]
  request_id: string
}

export async function listImages() {
  const response = await client.get<
    { 200: { data: ImageRecord[]; count: number } },
    unknown,
    true
  >({
    url: "/api/v1/images",
    security: [...security],
    throwOnError: true,
  })
  return response.data
}

export async function uploadImage(file: File) {
  const body = new FormData()
  body.append("file", file)
  const response = await client.post<
    {
      200: { image: ImageRecord; duplicate: boolean }
      201: { image: ImageRecord; duplicate: boolean }
    },
    unknown,
    true
  >({
    url: "/api/v1/images",
    security: [...security],
    body,
    timeout: 35_000,
    throwOnError: true,
  })
  return response.data
}

export async function searchImages(query: string): Promise<ImageSearchResult> {
  const response = await client.post<{ 200: ImageSearchResult }, unknown, true>(
    {
      url: "/api/v1/images/search",
      security: [...security],
      body: { query, limit: 5 },
      headers: { "Content-Type": "application/json" },
      timeout: 35_000,
      throwOnError: true,
    },
  )
  return response.data
}

export async function searchByImage(file: File): Promise<ImageSearchResult> {
  const body = new FormData()
  body.append("file", file)
  const response = await client.post<{ 200: ImageSearchResult }, unknown, true>(
    {
      url: "/api/v1/images/search-by-image",
      security: [...security],
      body,
      timeout: 35_000,
      throwOnError: true,
    },
  )
  return response.data
}

export async function fetchImageObjectUrl(id: string): Promise<string> {
  const response = await client.get<{ 200: Blob }, unknown, true>({
    url: `/api/v1/images/${encodeURIComponent(id)}/file`,
    security: [...security],
    responseType: "blob",
    throwOnError: true,
    timeout: 15_000,
  })
  return URL.createObjectURL(response.data as Blob)
}

export async function deleteImage(id: string) {
  await client.delete({
    url: `/api/v1/images/${encodeURIComponent(id)}`,
    security: [...security],
    throwOnError: true,
  })
}

export function describeImageError(error: unknown): string {
  if (isAxiosError(error)) {
    if (!error.response) return "无法连接服务，请确认后端已启动。"
    const detail = error.response.data?.detail
    if (typeof detail === "string") return detail
    if (detail && typeof detail.message === "string") return detail.message
    if (error.response.status === 422) return "图片或检索内容不符合要求。"
  }
  return "操作失败，请稍后重试。"
}
