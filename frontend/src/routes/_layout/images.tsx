import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Image as ImageIcon, Search, Upload } from "lucide-react"
import { type FormEvent, useEffect, useRef, useState } from "react"
import { DevAssistHeader } from "@/components/Common/DevAssistLayout"
import { LoadingButton } from "@/components/ui/loading-button"
import useAuth from "@/hooks/useAuth"
import {
  deleteImage,
  describeImageError,
  fetchImageObjectUrl,
  type ImageHit,
  listImages,
  searchByImage,
  searchImages,
  uploadImage,
} from "@/lib/images"

export const Route = createFileRoute("/_layout/images")({
  component: Images,
  head: () => ({ meta: [{ title: "图片检索 - DevAssist" }] }),
})

function Images() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [queryFile, setQueryFile] = useState<File | null>(null)
  const [query, setQuery] = useState("")
  const [notice, setNotice] = useState("")
  const [inputError, setInputError] = useState("")
  const [previews, setPreviews] = useState<Record<string, string>>({})
  const uploadLock = useRef(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const images = useQuery({
    queryKey: ["images", user?.id],
    queryFn: listImages,
    enabled: Boolean(user),
  })
  const upload = useMutation({
    mutationFn: uploadImage,
    retry: false,
    onSuccess: async (result) => {
      setNotice(
        result.duplicate
          ? "这张图片已经在你的图库里。"
          : "图片已入库，可以检索了。",
      )
      setFile(null)
      textSearch.reset()
      imageSearch.reset()
      if (fileInput.current) fileInput.current.value = ""
      await queryClient.invalidateQueries({ queryKey: ["images", user?.id] })
    },
  })
  const textSearch = useMutation({ mutationFn: searchImages, retry: false })
  const imageSearch = useMutation({ mutationFn: searchByImage, retry: false })
  const remove = useMutation({
    mutationFn: deleteImage,
    retry: false,
    onSuccess: async () => {
      setNotice("图片已删除。")
      textSearch.reset()
      imageSearch.reset()
      await queryClient.invalidateQueries({ queryKey: ["images", user?.id] })
    },
  })
  const hits = textSearch.data?.hits ?? imageSearch.data?.hits ?? []
  const hitKey = hits.map((hit) => hit.id).join(",")
  useEffect(() => {
    if (!hitKey) {
      setPreviews((current) => (Object.keys(current).length ? {} : current))
      return
    }
    let cancelled = false
    const created: string[] = []
    async function load() {
      const next: Record<string, string> = {}
      for (const id of hitKey.split(",")) {
        try {
          const url = await fetchImageObjectUrl(id)
          created.push(url)
          next[id] = url
        } catch {
          // 文件读不到时仍显示文件名。
        }
      }
      if (!cancelled) setPreviews(next)
    }
    void load()
    return () => {
      cancelled = true
      for (const url of created) URL.revokeObjectURL(url)
    }
  }, [hitKey])
  const failure =
    images.error ||
    upload.error ||
    textSearch.error ||
    imageSearch.error ||
    remove.error

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (uploadLock.current) return
    setInputError("")
    setNotice("")
    if (!file || !/\.(jpe?g|png)$/i.test(file.name)) {
      setInputError("请选择 JPG 或 PNG 图片。")
      return
    }
    if (file.size > 1_500_000) {
      setInputError("单张图片不能超过 1.5 MB。")
      return
    }
    uploadLock.current = true
    try {
      await upload.mutateAsync(file)
    } catch {
      // 错误显示在页面上。
    } finally {
      uploadLock.current = false
    }
  }

  async function handleTextSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setInputError("")
    const trimmed = query.trim()
    if (!trimmed || Array.from(trimmed).length > 1000) {
      setInputError("请输入 1～1000 个字符的检索内容。")
      return
    }
    imageSearch.reset()
    try {
      await textSearch.mutateAsync(trimmed)
    } catch {
      // 错误显示在页面上。
    }
  }

  async function handleImageSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setInputError("")
    textSearch.reset()
    if (!queryFile || !/\.(jpe?g|png)$/i.test(queryFile.name)) {
      setInputError(
        "请选择一张 JPG 或 PNG 作为查询图片。查询图片不会写入图库。",
      )
      return
    }
    try {
      await imageSearch.mutateAsync(queryFile)
    } catch {
      // 错误显示在页面上。
    }
  }

  return (
    <>
      <DevAssistHeader
        title="图片检索"
        description="用文字找自己的图片，或上传一张图找相似图。只能看到当前账号的图。"
        badge="按用户隔离"
      />
      <div className="da-knowledge">
        <div className="da-knowledge-intro">
          <p>
            图片和文档是分开的。文字搜文档仍在知识库。没有嵌入模型时会直接提示，不会一直转圈。
          </p>
          <Link to="/knowledge" className="da-text-button">
            去文档知识库
          </Link>
        </div>
        <form onSubmit={handleUpload} className="da-upload-form">
          <label htmlFor="image-file">把图片放进我的图库</label>
          <input
            ref={fileInput}
            id="image-file"
            type="file"
            accept="image/jpeg,image/png,.jpg,.jpeg,.png"
            disabled={upload.isPending}
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null)
              setInputError("")
              upload.reset()
            }}
          />
          <div className="da-form-actions">
            <p>JPG / PNG · 单张最多 1.5 MB · 每人最多 20 张</p>
            <LoadingButton
              className="da-primary"
              loading={upload.isPending}
              type="submit"
            >
              <Upload aria-hidden="true" />
              {upload.isPending ? "正在入库…" : "上传图片"}
            </LoadingButton>
          </div>
        </form>
        {inputError && (
          <p role="alert" className="da-inline-error">
            {inputError}
          </p>
        )}
        {failure && (
          <p role="alert" className="da-inline-error">
            {describeImageError(failure)}
          </p>
        )}
        {notice && (
          <p role="status" className="da-notice">
            {notice}
          </p>
        )}
        <section aria-label="我的图片" className="da-document-library">
          <div className="da-library-heading">
            <h2>
              我的图片 <span>{images.data?.count ?? 0} / 20</span>
            </h2>
            <small>只保存在当前账号下</small>
          </div>
          {images.isPending && <p className="da-note">正在读取图片…</p>}
          {images.data?.data.length === 0 && (
            <div className="da-library-empty">
              <ImageIcon size={30} aria-hidden="true" />
              <p>还没有图片。上传后才能做文字搜图和以图搜图。</p>
            </div>
          )}
          <div className="da-document-list">
            {images.data?.data.map((image) => (
              <div key={image.id} className="da-document-row">
                <div>
                  <strong>{image.filename}</strong>
                  <small>{(image.size_bytes / 1024).toFixed(1)} KiB</small>
                </div>
                <LoadingButton
                  className="da-secondary"
                  type="button"
                  loading={remove.isPending}
                  onClick={() => remove.mutate(image.id)}
                >
                  删除
                </LoadingButton>
              </div>
            ))}
          </div>
        </section>
        <form onSubmit={handleTextSearch} className="da-library-search">
          <h2>文字搜图片</h2>
          <label htmlFor="image-query" className="da-note">
            描述你要找的图片
          </label>
          <div className="da-search-controls">
            <input
              id="image-query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              disabled={textSearch.isPending}
              placeholder="例如：包装盒上的药品名称"
            />
            <LoadingButton
              className="da-secondary"
              type="submit"
              loading={textSearch.isPending}
            >
              <Search aria-hidden="true" />
              {textSearch.isPending ? "正在检索…" : "检索图片"}
            </LoadingButton>
          </div>
        </form>
        <form onSubmit={handleImageSearch} className="da-upload-form">
          <label htmlFor="image-query-file">用一张图找相似图</label>
          <input
            id="image-query-file"
            type="file"
            accept="image/jpeg,image/png,.jpg,.jpeg,.png"
            disabled={imageSearch.isPending}
            onChange={(event) => setQueryFile(event.target.files?.[0] ?? null)}
          />
          <div className="da-form-actions">
            <p>这张图只用来查询，不会存进图库。</p>
            <LoadingButton
              className="da-secondary"
              type="submit"
              loading={imageSearch.isPending}
            >
              <ImageIcon aria-hidden="true" />
              {imageSearch.isPending ? "正在比对…" : "找相似图"}
            </LoadingButton>
          </div>
        </form>
        {(textSearch.data || imageSearch.data) && (
          <section aria-label="图片检索结果" className="da-search-results">
            <h2>{textSearch.data ? "文字搜图结果" : "以图搜图结果"}</h2>
            {hits.length ? (
              <ImageHits hits={hits} previews={previews} />
            ) : (
              <div className="da-library-empty">
                <Search size={28} aria-hidden="true" />
                <p>没有命中你的图片。图库为空时不会去调用嵌入模型。</p>
              </div>
            )}
          </section>
        )}
      </div>
    </>
  )
}

function ImageHits({
  hits,
  previews,
}: {
  hits: ImageHit[]
  previews: Record<string, string>
}) {
  return (
    <div className="da-document-list">
      {hits.map((hit) => (
        <article key={hit.id} className="da-document-row">
          {previews[hit.id] && (
            <img
              className="da-image-thumb"
              src={previews[hit.id]}
              alt={hit.filename}
            />
          )}
          <div>
            <strong>{hit.filename}</strong>
            <small>
              来源：{hit.filename} · 相似度 {hit.score.toFixed(3)}
            </small>
          </div>
        </article>
      ))}
    </div>
  )
}
