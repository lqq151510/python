import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { type FormEvent, useState } from "react"
import { DocumentScope } from "@/components/Common/DocumentScope"
import { LoadingButton } from "@/components/ui/loading-button"
import useAuth from "@/hooks/useAuth"
import { describeKnowledgeError, listDocuments } from "@/lib/knowledge"
import {
  addLearningRecord,
  compareVectorStores,
  deleteLearningRecord,
  listLearningRecords,
  readCacheStats,
  type LearningRecord,
} from "@/lib/learning"

export const Route = createFileRoute("/_layout/learning")({
  component: Learning,
  head: () => ({ meta: [{ title: "学习实验 - DevAssist" }] }),
})

const databases = [
  [
    "PostgreSQL",
    "保存账号和知识库文档",
    "练习：上传一份文档，再核对它的片段。",
  ],
  [
    "MySQL",
    "保存学习笔记与实验结果",
    "练习：写下你理解的数据库职责，刷新后检查是否还在。",
  ],
  ["Redis", "短暂缓存检索结果", "练习：连续两次查同一个问题，观察缓存命中数。"],
  ["Milvus", "为应用提供向量检索", "练习：用含义相近、措辞不同的问题找原文。"],
  [
    "Chroma",
    "为本地实验提供检索对照",
    "练习：用相同模型和资料，与 Milvus 的结果对比。",
  ],
]

function Comparison({ record }: { record: LearningRecord }) {
  if (!record.result.stores) return null
  return (
    <div className="da-lab-comparison">
      {Object.entries(record.result.stores).map(([name, result]) => (
        <section key={name} aria-label={`${name} 实验结果`}>
          <h3>{name}</h3>
          <p className="da-note">
            {result.elapsed_ms} ms · 缓存：
            {result.cache_status === "hit"
              ? "命中"
              : result.cache_status === "miss"
                ? "未命中"
                : result.cache_status}
          </p>
          <ol>
            {result.hits.map((hit) => (
              <li key={hit.chunk_id}>
                {hit.filename} · 相似度 {hit.score.toFixed(3)}
              </li>
            ))}
          </ol>
          {!result.hits.length && <p>没有达到当前相似度阈值的片段。</p>}
        </section>
      ))}
      <p className="da-note">
        共有 {record.result.shared_hits} 个相同片段。
        {record.result.candidate_note}
      </p>
    </div>
  )
}

function Learning() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [title, setTitle] = useState("")
  const [notes, setNotes] = useState("")
  const [query, setQuery] = useState("")
  const [scope, setScope] = useState("")
  const key = ["learning-records", user?.id]
  const records = useQuery({
    queryKey: key,
    queryFn: listLearningRecords,
    enabled: Boolean(user),
  })
  const documents = useQuery({
    queryKey: ["documents", user?.id],
    queryFn: listDocuments,
    enabled: Boolean(user),
  })
  const stats = useQuery({
    queryKey: ["cache-stats", user?.id],
    queryFn: readCacheStats,
    enabled: Boolean(user),
  })
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: key })
    await queryClient.invalidateQueries({ queryKey: ["cache-stats", user?.id] })
  }
  const save = useMutation({
    mutationFn: addLearningRecord,
    retry: false,
    onSuccess: async () => {
      setTitle("")
      setNotes("")
      await refresh()
    },
  })
  const compare = useMutation({
    mutationFn: compareVectorStores,
    retry: false,
    onSuccess: refresh,
  })
  const remove = useMutation({
    mutationFn: deleteLearningRecord,
    retry: false,
    onSuccess: refresh,
  })
  const failure =
    records.error ||
    save.error ||
    compare.error ||
    remove.error ||
    documents.error

  function submitNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (save.isPending || !title.trim()) return
    save.mutate({ title: title.trim(), notes: notes.trim() })
  }
  function submitCompare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (compare.isPending || !query.trim()) return
    compare.mutate({
      query: query.trim(),
      ...(scope ? { document_id: scope } : {}),
    })
  }

  return (
    <>
      <header className="da-header">
        <div>
          <h1>学习实验</h1>
          <p>一次完成一个小实验，把观察到的结果写下来。</p>
        </div>
        <span className="da-badge">练习 → 记录 → 核对</span>
      </header>
      <div className="da-knowledge">
        <section className="da-lab-cards" aria-label="数据库分工">
          {databases.map(([name, purpose, exercise]) => (
            <article className="da-lab-card" key={name}>
              <h2>{name}</h2>
              <p>{purpose}</p>
              <p className="da-note">{exercise}</p>
            </article>
          ))}
        </section>
        <p className="da-note">
          这里的五种数据库用于分阶段学习。相似职责的方案也可以二选一；当前安排用于看清它们在项目中的差别。
        </p>
        <form className="da-upload-form da-lab-form" onSubmit={submitNote}>
          <h2>写一条学习记录</h2>
          <label htmlFor="learning-title">今天学了什么</label>
          <input
            id="learning-title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={120}
            required
          />
          <label htmlFor="learning-notes">我的理解或疑问</label>
          <textarea
            id="learning-notes"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            maxLength={2000}
            rows={3}
          />
          <LoadingButton type="submit" loading={save.isPending}>
            保存到学习记录
          </LoadingButton>
        </form>
        <form
          className="da-library-search da-lab-form"
          onSubmit={submitCompare}
        >
          <h2>做一次向量检索对照</h2>
          <p className="da-note">
            先到<Link to="/knowledge">知识库</Link>
            上传一份短文档。两边使用相同模型和候选片段；结果会保存为学习记录。
          </p>
          <DocumentScope
            value={scope}
            documents={documents.data?.data ?? []}
            disabled={compare.isPending}
            onChange={setScope}
          />
          <label htmlFor="learning-query">实验问题</label>
          <input
            id="learning-query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            maxLength={1000}
            required
          />
          <LoadingButton type="submit" loading={compare.isPending}>
            对照检索并保存
          </LoadingButton>
          {compare.isPending && (
            <p role="status">正在依次查询 Milvus 和 Chroma，请稍候。</p>
          )}
        </form>
        <section className="da-document-list" aria-label="缓存观察">
          <h2>观察 Redis 缓存</h2>
          <p>
            {stats.data?.available
              ? `最近一小时：命中 ${stats.data.hit} 次，未命中 ${stats.data.miss} 次。`
              : "缓存暂不可用，检索仍可继续。"}
          </p>
          <button
            type="button"
            className="da-secondary"
            onClick={() => stats.refetch()}
          >
            刷新缓存计数
          </button>
        </section>
        {failure && (
          <p role="alert" className="da-inline-error">
            {describeKnowledgeError(failure)}
          </p>
        )}
        <section className="da-document-list" aria-label="我的学习记录">
          <h2>我的学习记录</h2>
          {records.isPending && <p>正在读取学习记录…</p>}
          {records.data?.data.length === 0 && (
            <p>还没有记录，先写下今天的一个收获。</p>
          )}
          {records.data?.data.map((record) => (
            <article className="da-lab-record" key={record.id}>
              <h3>{record.title}</h3>
              <time className="da-note">
                {new Date(record.created_at).toLocaleString("zh-CN")}
              </time>
              <p style={{ whiteSpace: "pre-wrap" }}>{record.notes}</p>
              <Comparison record={record} />
              <button
                type="button"
                className="da-text-button"
                disabled={remove.isPending}
                onClick={() => {
                  if (window.confirm("删除这条学习记录？"))
                    remove.mutate(record.id)
                }}
              >
                删除记录
              </button>
            </article>
          ))}
        </section>
      </div>
    </>
  )
}
