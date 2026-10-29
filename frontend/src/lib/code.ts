import { isAxiosError } from "axios"
import { client } from "@/client/client.gen"

export type CodePermission = "read_only" | "workspace_modify" | "full"
export type CodeApiFormat =
  | "openai_chat_completions"
  | "openai_responses"
  | "anthropic_messages"
  | "gemini_generate_content"

export interface CodeFileSnapshot {
  path: string
  content: string
  base_version: string
}

export interface CodeWorkspaceFile {
  path: string
  size_bytes: number
}

export interface CodeWorkspace {
  files: CodeWorkspaceFile[]
  total_bytes: number
}

export interface CodeFileEdit {
  path: string
  content: string
}

export interface CodeApplyEdit extends CodeFileEdit {
  base_version: string
}

export interface CodeGeneration {
  plan: string[]
  summary: string
  edits: CodeFileEdit[]
  request_id: string
}

export interface CodeModelConfig {
  api_format: CodeApiFormat
  model: string | null
  configured: boolean
  local: boolean
  base_url_host: string | null
}

const security = [{ scheme: "bearer", type: "http" }] as const

export async function getCodeWorkspace(): Promise<CodeWorkspace> {
  const response = await client.request<{ 200: CodeWorkspace }, unknown, true>({
    method: "GET",
    url: "/api/v1/code/workspace",
    security: [...security],
    timeout: 15_000,
    throwOnError: true,
  })
  return response.data
}

export async function getCodeModelConfig(): Promise<CodeModelConfig> {
  const response = await client.request<
    { 200: CodeModelConfig },
    unknown,
    true
  >({
    method: "GET",
    url: "/api/v1/code/config",
    security: [...security],
    timeout: 15_000,
    throwOnError: true,
  })
  return response.data
}

export async function importGithubRepository(repository: string): Promise<
  CodeWorkspace & {
    imported: number
    repository: string
    branch: string
  }
> {
  const response = await client.request<
    {
      200: CodeWorkspace & {
        imported: number
        repository: string
        branch: string
      }
    },
    unknown,
    true
  >({
    method: "POST",
    url: "/api/v1/code/workspace/github",
    body: { repository },
    headers: { "Content-Type": "application/json" },
    security: [...security],
    timeout: 60_000,
    throwOnError: true,
  })
  return response.data
}

export async function uploadCodeFiles(files: File[]): Promise<CodeWorkspace> {
  const body = new FormData()
  for (const file of files) body.append("files", file, file.name)
  const response = await client.request<{ 200: CodeWorkspace }, unknown, true>({
    method: "POST",
    url: "/api/v1/code/workspace/files",
    security: [...security],
    body,
    timeout: 30_000,
    throwOnError: true,
  })
  return response.data
}

export async function readCodeFile(path: string): Promise<CodeFileSnapshot> {
  const encodedPath = path.split("/").map(encodeURIComponent).join("/")
  const response = await client.request<
    { 200: CodeFileSnapshot },
    unknown,
    true
  >({
    method: "GET",
    url: `/api/v1/code/workspace/files/${encodedPath}`,
    security: [...security],
    timeout: 15_000,
    throwOnError: true,
  })
  if (
    response.data.path !== path ||
    typeof response.data.content !== "string" ||
    typeof response.data.base_version !== "string" ||
    !/^sha256:[0-9a-f]{64}$/.test(response.data.base_version)
  ) {
    throw new Error("文件快照无效，请重新加载。")
  }
  return response.data
}

export async function saveCodeFile(
  path: string,
  content: string,
  baseVersion: string,
): Promise<CodeWorkspace> {
  const response = await client.request<{ 200: CodeWorkspace }, unknown, true>({
    method: "PUT",
    url: "/api/v1/code/workspace/files",
    body: { path, content, base_version: baseVersion },
    headers: { "Content-Type": "application/json" },
    security: [...security],
    timeout: 30_000,
    throwOnError: true,
  })
  return response.data
}

export async function deleteCodeFile(path: string): Promise<CodeWorkspace> {
  const encodedPath = path.split("/").map(encodeURIComponent).join("/")
  const response = await client.request<{ 200: CodeWorkspace }, unknown, true>({
    method: "DELETE",
    url: `/api/v1/code/workspace/files/${encodedPath}`,
    security: [...security],
    timeout: 15_000,
    throwOnError: true,
  })
  return response.data
}

export interface CodeDiffLine {
  kind: "add" | "delete"
  text: string
}

export interface CodeDiff {
  status: "new" | "unchanged" | "changed"
  lines: CodeDiffLine[]
  truncated: boolean
  base_version: string
}

export async function reviewCodeDiff(
  path: string,
  content: string,
  full = false,
): Promise<CodeDiff> {
  const response = await client.request<{ 200: CodeDiff }, unknown, true>({
    method: "POST",
    url: "/api/v1/code/workspace/diff",
    body: { path, content, full },
    headers: { "Content-Type": "application/json" },
    security: [...security],
    timeout: 15_000,
    throwOnError: true,
  })
  if (
    typeof response.data.base_version !== "string" ||
    !/^(missing|sha256:[0-9a-f]{64})$/.test(response.data.base_version)
  ) {
    throw new Error("预览缺少有效的文件版本，请重试。")
  }
  return response.data
}

export async function generateCode(input: {
  task: string
  goal: string
  planOnly: boolean
  permission: CodePermission
  fullPermissionsAcknowledged: boolean
  workspacePaths: string[]
}): Promise<CodeGeneration> {
  const response = await client.request<{ 200: CodeGeneration }, unknown, true>(
    {
      method: "POST",
      url: "/api/v1/code/generate",
      body: {
        task: input.task,
        goal: input.goal,
        plan_only: input.planOnly,
        permission: input.permission,
        full_permissions_acknowledged: input.fullPermissionsAcknowledged,
        workspace_paths: input.workspacePaths,
      },
      headers: { "Content-Type": "application/json" },
      security: [...security],
      timeout: 90_000,
      throwOnError: true,
    },
  )
  return response.data
}

export async function applyCodeEdits(input: {
  edits: CodeApplyEdit[]
  permission: CodePermission
  approved: boolean
  fullPermissionsAcknowledged: boolean
}): Promise<CodeWorkspace> {
  const response = await client.request<{ 200: CodeWorkspace }, unknown, true>({
    method: "POST",
    url: "/api/v1/code/workspace/apply",
    body: {
      edits: input.edits,
      permission: input.permission,
      approved: input.approved,
      full_permissions_acknowledged: input.fullPermissionsAcknowledged,
    },
    headers: { "Content-Type": "application/json" },
    security: [...security],
    timeout: 30_000,
    throwOnError: true,
  })
  return response.data
}

export function isWorkspaceConflict(error: unknown): boolean {
  return (
    isAxiosError(error) &&
    error.response?.status === 409 &&
    error.response.data?.detail?.code === "workspace_conflict"
  )
}

export function describeCodeError(error: unknown): string {
  if (isAxiosError(error)) {
    if (!error.response) return "无法连接代码服务，请确认后端已启动。"
    const detail = error.response.data?.detail
    if (detail && typeof detail.message === "string") return detail.message
    if (typeof detail === "string") return detail
    if (error.response.status === 401) return "登录已失效，请重新登录。"
  }
  return error instanceof Error
    ? error.message
    : "代码工作区操作失败，请稍后重试。"
}
