import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Clapperboard, Film, ImagePlus, Trash2, X } from "lucide-react"
import {
  type ChangeEvent,
  type FormEvent,
  useEffect,
  useRef,
  useState,
} from "react"
import { DevAssistHeader } from "@/components/Common/DevAssistLayout"
import { LoadingButton } from "@/components/ui/loading-button"
import useAuth from "@/hooks/useAuth"
import {
  type AigcJobDetail,
  type AigcKind,
  cancelAigcJob,
  createAigcJob,
  deleteAigcJob,
  describeAigcError,
  estimateSeconds,
  fetchAigcVideoUrl,
  formatDuration,
  listAigcJobs,
  readAigcConfig,
  readAigcJob,
  statusLabels,
  terminalStatuses,
} from "@/lib/aigc"

export const Route = createFileRoute("/_layout/aigc")({
  component: AigcStudio,
  head: () => ({ meta: [{ title: "AI 视频生成 - DevAssist" }] }),
})

const resolutions = [
  { label: "480 × 288 · 草稿最快", width: 480, height: 288 },
  { label: "640 × 352 · 均衡", width: 640, height: 352 },
  { label: "864 × 480 · 清晰但最慢", width: 864, height: 480 },
]

const durations = [5, 8, 10, 15]

function AigcStudio() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [kind, setKind] = useState<AigcKind>("text2video")
  const [prompt, setPrompt] = useState("")
  const [resolutionIndex, setResolutionIndex] = useState(0)
  const [duration, setDuration] = useState(5)
  const [profile, setProfile] = useState("Turbo 4 Fast")
  const [seedText, setSeedText] = useState("")
  const [firstFrame, setFirstFrame] = useState<File | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [notice, setNotice] = useState("")
  const [formError, setFormError] = useState("")
  const [videoUrl, setVideoUrl] = useState("")
  const submitLock = useRef(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const config = useQuery({
    queryKey: ["aigc-config", user?.id],
    queryFn: readAigcConfig,
    enabled: Boolean(user),
    refetchInterval: 20_000,
  })
  const jobs = useQuery({
    queryKey: ["aigc-jobs", user?.id],
    queryFn: listAigcJobs,
    enabled: Boolean(user),
    refetchInterval: (query) =>
      query.state.data?.some((job) => !terminalStatuses.has(job.status))
        ? 4_000
        : false,
  })
  const detail = useQuery({
    queryKey: ["aigc-job", selectedId],
    queryFn: () => readAigcJob(selectedId as string),
    enabled: Boolean(selectedId),
    refetchInterval: (query) =>
      query.state.data && !terminalStatuses.has(query.state.data.status)
        ? 3_000
        : false,
  })

  const active = detail.data?.status
  const succeeded = active === "succeeded"

  useEffect(() => {
    let cancelled = false
    let objectUrl = ""
    if (!selectedId || !succeeded) {
      setVideoUrl("")
      return
    }
    fetchAigcVideoUrl(selectedId)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url)
          return
        }
        objectUrl = url
        setVideoUrl(url)
      })
      .catch(() => setVideoUrl(""))
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [selectedId, succeeded])

  const resolution = resolutions[resolutionIndex]
  const estimated = estimateSeconds(resolution.width, resolution.height)
  const blockedReason = !config.data
    ? ""
    : !config.data.enabled
      ? "本地视频生成还没开启：在 .env 里设置 AIGC_ENABLED=true，再启动 ComfyUI。"
      : !config.data.comfy_reachable
        ? `连不上 ComfyUI（${config.data.comfy_error ?? "未启动"}），先启动 ComfyUI 再提交。`
        : ""

  const create = useMutation({
    mutationFn: createAigcJob,
    retry: false,
    onSuccess: async (job) => {
      setNotice("任务已提交，排队中。生成期间可以留在本页看进度。")
      setFormError("")
      setPrompt("")
      setFirstFrame(null)
      if (fileInput.current) fileInput.current.value = ""
      setSelectedId(job.id)
      await queryClient.invalidateQueries({ queryKey: ["aigc-jobs", user?.id] })
    },
    onError: (error) => setFormError(describeAigcError(error)),
  })

  const cancel = useMutation({
    mutationFn: cancelAigcJob,
    retry: false,
    onSuccess: async () => {
      setNotice("已取消该任务。")
      await queryClient.invalidateQueries({ queryKey: ["aigc-jobs", user?.id] })
      await queryClient.invalidateQueries({
        queryKey: ["aigc-job", selectedId],
      })
    },
    onError: (error) => setFormError(describeAigcError(error)),
  })

  const remove = useMutation({
    mutationFn: deleteAigcJob,
    retry: false,
    onSuccess: async (_, jobId) => {
      setNotice("任务和产物已删除。")
      if (selectedId === jobId) setSelectedId(null)
      await queryClient.invalidateQueries({ queryKey: ["aigc-jobs", user?.id] })
    },
    onError: (error) => setFormError(describeAigcError(error)),
  })

  const onPickFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null
    setFirstFrame(file)
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (submitLock.current || create.isPending) return
    const trimmed = prompt.trim()
    if (!trimmed) {
      setFormError("先写一句提示词，描述想要的画面。")
      return
    }
    if (kind === "image2video" && !firstFrame) {
      setFormError("图生视频需要一张首帧图片。")
      return
    }
    const seed = seedText.trim()
    const parsedSeed = seed === "" ? undefined : Number(seed)
    if (
      parsedSeed !== undefined &&
      (!Number.isInteger(parsedSeed) || parsedSeed < 0)
    ) {
      setFormError("随机种子必须是非负整数，或留空。")
      return
    }
    setFormError("")
    submitLock.current = true
    create.mutate(
      {
        kind,
        prompt: trimmed,
        params: {
          width: resolution.width,
          height: resolution.height,
          duration_seconds: duration,
          generation_profile: profile,
          ...(parsedSeed === undefined ? {} : { seed: parsedSeed }),
        },
        firstFrame,
      },
      { onSettled: () => (submitLock.current = false) },
    )
  }

  return (
    <>
      <DevAssistHeader aigc />
      <section className="da-aigc" aria-label="AI 视频生成">
        {blockedReason && <p className="da-notice">{blockedReason}</p>}
        {notice && <p className="da-notice">{notice}</p>}
        {formError && (
          <p className="da-inline-error" role="alert">
            {formError}
          </p>
        )}

        <div className="da-aigc-grid">
          <form className="da-aigc-form" onSubmit={onSubmit}>
            <div className="da-mode-switch" role="group" aria-label="生成方式">
              <button
                type="button"
                className={kind === "text2video" ? "is-active" : ""}
                aria-pressed={kind === "text2video"}
                onClick={() => setKind("text2video")}
              >
                <Clapperboard size={18} aria-hidden="true" /> 文生视频
              </button>
              <button
                type="button"
                className={kind === "image2video" ? "is-active" : ""}
                aria-pressed={kind === "image2video"}
                onClick={() => setKind("image2video")}
              >
                <ImagePlus size={18} aria-hidden="true" /> 图生视频
              </button>
            </div>

            <label>
              提示词
              <textarea
                rows={4}
                value={prompt}
                maxLength={2000}
                placeholder="例如：霓虹城市雨夜，镜头缓慢推进，胶片颗粒感"
                onChange={(event) => setPrompt(event.target.value)}
              />
            </label>

            {kind === "image2video" && (
              <label>
                首帧图片（PNG / JPEG / WebP，≤ 8 MiB）
                <input
                  ref={fileInput}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  onChange={onPickFile}
                />
                {firstFrame && (
                  <span className="da-note">
                    已选择 {firstFrame.name}（
                    {(firstFrame.size / 1024 / 1024).toFixed(1)} MiB）
                  </span>
                )}
              </label>
            )}

            <label>
              分辨率
              <select
                value={resolutionIndex}
                onChange={(event) =>
                  setResolutionIndex(Number(event.target.value))
                }
              >
                {resolutions.map((item, index) => (
                  <option key={item.label} value={index}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>

            <label>
              时长
              <select
                value={duration}
                onChange={(event) => setDuration(Number(event.target.value))}
              >
                {durations.map((value) => (
                  <option key={value} value={value}>
                    {value} 秒
                  </option>
                ))}
              </select>
            </label>

            <label>
              速度档
              <select
                value={profile}
                onChange={(event) => setProfile(event.target.value)}
              >
                {(
                  config.data?.generation_profiles ?? [
                    "Turbo 4 Fast",
                    "Turbo 8 Balanced",
                    "Full 20 Quality",
                  ]
                ).map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>

            <label>
              随机种子（留空则随机）
              <input
                type="text"
                inputMode="numeric"
                value={seedText}
                placeholder="例如 42"
                onChange={(event) => setSeedText(event.target.value)}
              />
            </label>

            <p className="da-note">
              按节点自带的实测公式估算：{resolution.width} × {resolution.height}
              、{duration} 秒，本机约需 {formatDuration(estimated)}
              ；单卡串行，同时只跑一条。
            </p>

            <div className="da-form-actions">
              <LoadingButton
                type="submit"
                className="da-primary"
                loading={create.isPending}
                disabled={Boolean(blockedReason)}
              >
                提交生成任务
              </LoadingButton>
              {config.data && (
                <span className="da-note">
                  队列上限 {config.data.max_queue_depth} 条，当前排队 + 生成中{" "}
                  {config.data.active_jobs} 条
                </span>
              )}
            </div>
          </form>

          <div className="da-aigc-side">
            <h2 className="da-section-label">最近任务</h2>
            {jobs.isLoading && <p className="da-state">正在读取任务…</p>}
            {jobs.data?.length === 0 && (
              <p className="da-state">还没有生成记录，左边提交一条试试。</p>
            )}
            <ul className="da-aigc-job-list">
              {jobs.data?.map((job) => (
                <li key={job.id}>
                  <button
                    type="button"
                    className={`da-aigc-job ${
                      selectedId === job.id ? "da-aigc-job-active" : ""
                    }`}
                    onClick={() => setSelectedId(job.id)}
                  >
                    <span className="da-aigc-job-head">
                      <strong>{statusLabels[job.status]}</strong>
                      <span>
                        {job.kind === "text2video" ? "文生视频" : "图生视频"}
                      </span>
                    </span>
                    <span className="da-excerpt">{job.prompt}</span>
                    {!terminalStatuses.has(job.status) && (
                      <span className="da-note">
                        {job.status_message ?? "处理中"} · {job.progress}%
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>

        {detail.data && (
          <JobDetail
            job={detail.data}
            videoUrl={videoUrl}
            onCancel={() => cancel.mutate(detail.data.id)}
            onDelete={() => remove.mutate(detail.data.id)}
            busy={cancel.isPending || remove.isPending}
          />
        )}
      </section>
    </>
  )
}

function JobDetail({
  job,
  videoUrl,
  onCancel,
  onDelete,
  busy,
}: {
  job: AigcJobDetail
  videoUrl: string
  onCancel: () => void
  onDelete: () => void
  busy: boolean
}) {
  const finished = terminalStatuses.has(job.status)
  return (
    <section className="da-aigc-detail" aria-label="任务详情">
      <header className="da-section-label">
        <Film size={18} aria-hidden="true" /> {statusLabels[job.status]} ·{" "}
        {job.params.width} × {job.params.height} · {job.params.duration_seconds}{" "}
        秒
      </header>
      {!finished && (
        <>
          <progress max={100} value={job.progress} />
          <p className="da-note">
            {job.status_message ?? "处理中"} · {job.progress}%
            {job.eta_seconds !== null && job.eta_seconds > 0
              ? ` · 预计还要 ${formatDuration(job.eta_seconds)}`
              : ""}
          </p>
        </>
      )}
      {job.status === "failed" && (
        <p className="da-inline-error" role="alert">
          {job.error_message ?? "生成失败"}
          {job.error_code ? `（${job.error_code}）` : ""}
        </p>
      )}
      {job.status === "succeeded" &&
        (videoUrl ? (
          // biome-ignore lint/a11y/useMediaCaption: 本地生成的无字幕样片
          <video className="da-aigc-video" src={videoUrl} controls loop />
        ) : (
          <p className="da-state">正在取回视频…</p>
        ))}
      <div className="da-form-actions">
        {!finished && (
          <button
            type="button"
            className="da-secondary"
            onClick={onCancel}
            disabled={busy}
          >
            <X size={16} aria-hidden="true" /> 取消任务
          </button>
        )}
        {finished && (
          <button
            type="button"
            className="da-text-button da-text-danger"
            onClick={onDelete}
            disabled={busy}
          >
            <Trash2 size={16} aria-hidden="true" /> 删除任务与产物
          </button>
        )}
      </div>
    </section>
  )
}
