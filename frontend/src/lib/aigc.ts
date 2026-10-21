import { isAxiosError } from "axios"

import { client } from "@/client/client.gen"

export type AigcKind = "text2video" | "image2video"
export type AigcStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "canceled"

export const terminalStatuses: ReadonlySet<AigcStatus> = new Set([
  "succeeded",
  "failed",
  "canceled",
])

export interface AigcParams {
  width: number
  height: number
  duration_seconds: number
  seed: number
  model_profile: string
  generation_profile: string
  memory_mode: string
  qwen_precision: string
  attention: string
  sol_tau: number
}

export interface AigcJobSummary {
  id: string
  kind: AigcKind
  status: AigcStatus
  progress: number
  prompt: string
  status_message: string | null
  error_code: string | null
  output_url: string | null
  created_at: string
}

export interface AigcJobDetail extends AigcJobSummary {
  params: AigcParams
  estimate_seconds: number
  eta_seconds: number | null
  error_message: string | null
  started_at: string | null
  finished_at: string | null
}

export interface AigcConfig {
  enabled: boolean
  runner_running: boolean
  comfy_reachable: boolean
  comfy_error: string | null
  active_jobs: number
  max_queue_depth: number
  defaults: AigcParams
  limits: {
    width: [number, number]
    height: [number, number]
    side_step: number
    duration_seconds: [number, number]
    max_prompt_chars: number
    max_first_frame_bytes: number
  }
  model_profiles: string[]
  generation_profiles: string[]
}

export interface AigcJobInput {
  kind: AigcKind
  prompt: string
  params: Partial<AigcParams>
  firstFrame?: File | null
}

const security = [{ scheme: "bearer", type: "http" }] as const

function paramsBody(params: Partial<AigcParams>) {
  const body: Record<string, unknown> = {}
  if (params.width !== undefined) body.width = params.width
  if (params.height !== undefined) body.height = params.height
  if (params.duration_seconds !== undefined) {
    body.duration_seconds = params.duration_seconds
  }
  if (params.seed !== undefined) body.seed = params.seed
  if (params.generation_profile)
    body.generation_profile = params.generation_profile
  if (params.memory_mode) body.memory_mode = params.memory_mode
  if (params.model_profile) body.model_profile = params.model_profile
  if (params.attention) body.attention = params.attention
  return body
}

export async function readAigcConfig(): Promise<AigcConfig> {
  const response = await client.get<{ 200: AigcConfig }, unknown, true>({
    url: "/api/v1/aigc/config",
    security: [...security],
    throwOnError: true,
    timeout: 15_000,
  })
  return response.data
}

export async function listAigcJobs(): Promise<AigcJobSummary[]> {
  const response = await client.get<{ 200: AigcJobSummary[] }, unknown, true>({
    url: "/api/v1/aigc/jobs",
    security: [...security],
    throwOnError: true,
    timeout: 15_000,
  })
  return response.data
}

export async function readAigcJob(jobId: string): Promise<AigcJobDetail> {
  const response = await client.get<{ 200: AigcJobDetail }, unknown, true>({
    url: `/api/v1/aigc/jobs/${encodeURIComponent(jobId)}`,
    security: [...security],
    throwOnError: true,
    timeout: 15_000,
  })
  return response.data
}

export async function createAigcJob(
  input: AigcJobInput,
): Promise<AigcJobDetail> {
  if (input.kind === "image2video") {
    const body = new FormData()
    if (!input.firstFrame) throw new Error("图生视频需要先选择首帧图片。")
    body.append("first_frame", input.firstFrame)
    body.append("prompt", input.prompt)
    body.append("params", JSON.stringify(paramsBody(input.params)))
    const response = await client.post<{ 201: AigcJobDetail }, unknown, true>({
      url: "/api/v1/aigc/jobs/image",
      security: [...security],
      body,
      throwOnError: true,
      timeout: 120_000,
    })
    return response.data
  }
  const response = await client.post<{ 201: AigcJobDetail }, unknown, true>({
    url: "/api/v1/aigc/jobs",
    security: [...security],
    body: {
      kind: "text2video",
      prompt: input.prompt,
      params: paramsBody(input.params),
    },
    headers: { "Content-Type": "application/json" },
    throwOnError: true,
    timeout: 30_000,
  })
  return response.data
}

export async function cancelAigcJob(jobId: string): Promise<AigcJobDetail> {
  const response = await client.post<{ 200: AigcJobDetail }, unknown, true>({
    url: `/api/v1/aigc/jobs/${encodeURIComponent(jobId)}/cancel`,
    security: [...security],
    throwOnError: true,
    timeout: 15_000,
  })
  return response.data
}

export async function deleteAigcJob(jobId: string): Promise<void> {
  await client.delete<{ 204: unknown }, unknown, true>({
    url: `/api/v1/aigc/jobs/${encodeURIComponent(jobId)}`,
    security: [...security],
    throwOnError: true,
    timeout: 15_000,
  })
}

export async function fetchAigcVideoUrl(jobId: string): Promise<string> {
  const response = await client.get<{ 200: Blob }, unknown, true>({
    url: `/api/v1/aigc/jobs/${encodeURIComponent(jobId)}/video`,
    security: [...security],
    responseType: "blob",
    throwOnError: true,
    timeout: 120_000,
  })
  return URL.createObjectURL(response.data as Blob)
}

export function describeAigcError(error: unknown): string {
  if (!isAxiosError(error)) {
    return error instanceof Error ? error.message : "请求失败，请稍后重试。"
  }
  const status = error.response?.status
  const detail = error.response?.data?.detail
  if (!error.response) {
    return status === 0
      ? "无法连接服务，请检查后端是否启动。"
      : "请求超时或连接中断，请稍后重试。"
  }
  if (status === 401 || status === 403) return "登录已失效，请重新登录。"
  if (status === 413) return "首帧图片太大，请压缩到 8 MiB 以内。"
  if (
    detail &&
    typeof detail === "object" &&
    typeof detail.message === "string"
  )
    return detail.message
  if (typeof detail === "string") return detail
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0]
    const field = Array.isArray(first?.loc) ? first.loc.join(".") : "参数"
    return `${field}：${first?.msg ?? "格式不正确"}`
  }
  return "请求失败，请稍后重试。"
}

/** 与后端 aigc.ESTIMATE_* 保持一致的粗略耗时估算（秒）。 */
export function estimateSeconds(width: number, height: number): number {
  return 16 + 0.00173 * width * height
}

export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  if (minutes === 0) return `${rest} 秒`
  return rest === 0 ? `${minutes} 分钟` : `${minutes} 分 ${rest} 秒`
}

export const statusLabels: Record<AigcStatus, string> = {
  queued: "排队中",
  running: "生成中",
  succeeded: "已完成",
  failed: "失败",
  canceled: "已取消",
}
