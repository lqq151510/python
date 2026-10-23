import { client } from "@/client/client.gen"

export interface LearningRecord {
  id: string
  kind: string
  title: string
  notes: string
  created_at: string
  result: {
    shared_hits?: number
    candidate_note?: string
    stores?: Record<
      string,
      {
        elapsed_ms: number
        cache_status: string
        hits: { chunk_id: string; filename: string; score: number }[]
      }
    >
  }
}

const security = [{ scheme: "bearer", type: "http" }] as const

export async function listLearningRecords() {
  const response = await client.get<
    { 200: { data: LearningRecord[] } },
    unknown,
    true
  >({ url: "/api/v1/learning", security: [...security], throwOnError: true })
  return response.data
}

export async function addLearningRecord(input: {
  title: string
  notes: string
}) {
  const response = await client.post<{ 201: LearningRecord }, unknown, true>({
    url: "/api/v1/learning",
    body: input,
    security: [...security],
    headers: { "Content-Type": "application/json" },
    throwOnError: true,
  })
  return response.data
}

export async function compareVectorStores(input: {
  query: string
  document_id?: string
}) {
  const response = await client.post<{ 200: LearningRecord }, unknown, true>({
    url: "/api/v1/learning/compare",
    body: input,
    security: [...security],
    headers: { "Content-Type": "application/json" },
    timeout: 130_000,
    throwOnError: true,
  })
  return response.data
}

export async function readCacheStats() {
  const response = await client.get<
    { 200: { available: boolean; hit: number; miss: number } },
    unknown,
    true
  >({
    url: "/api/v1/learning/cache/stats",
    security: [...security],
    throwOnError: true,
  })
  return response.data
}

export async function deleteLearningRecord(id: string) {
  await client.delete({
    url: `/api/v1/learning/${encodeURIComponent(id)}`,
    security: [...security],
    throwOnError: true,
  })
}
