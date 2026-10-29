import { X } from "lucide-react"
import { useEffect, useRef, useState } from "react"

export function NoteDialog({
  open,
  busy,
  error,
  onClose,
  onSave,
}: {
  open: boolean
  busy: boolean
  error: string | null
  onClose: () => void
  onSave: (title: string, body: string) => void
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [title, setTitle] = useState("")
  const [body, setBody] = useState("")
  const [localError, setLocalError] = useState<string | null>(null)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open && !dialog.open) {
      setTitle("")
      setBody("")
      setLocalError(null)
      dialog.showModal()
    }
    if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      className="zm-dialog"
      ref={dialogRef}
      aria-labelledby="zm-note-title"
      onClose={onClose}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
    >
      <form
        method="dialog"
        onSubmit={(event) => {
          event.preventDefault()
          if (!title.trim() || !body.trim()) {
            setLocalError("标题和正文都不能为空。")
            return
          }
          if (title.trim().length > 80 || body.trim().length > 4000) {
            setLocalError("标题最多 80 字，正文最多 4000 字。")
            return
          }
          onSave(title.trim(), body.trim())
        }}
      >
        <header className="zm-dialog__head">
          <h2 className="zm-dialog__title" id="zm-note-title">
            新建速记
          </h2>
          <button
            className="zm-icon-btn"
            type="button"
            onClick={onClose}
            aria-label="关闭"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="zm-dialog__body">
          <div className="zm-field">
            <label className="zm-field__label" htmlFor="zm-note-input-title">
              标题<span aria-hidden="true"> *</span>
            </label>
            <input
              className="zm-field__control"
              id="zm-note-input-title"
              type="text"
              autoComplete="off"
              value={title}
              maxLength={80}
              onChange={(event) => setTitle(event.target.value)}
            />
          </div>
          <div className="zm-field">
            <label className="zm-field__label" htmlFor="zm-note-input-body">
              正文
            </label>
            <textarea
              className="zm-field__control"
              id="zm-note-input-body"
              rows={5}
              value={body}
              maxLength={4000}
              required
              onChange={(event) => setBody(event.target.value)}
            />
            <p className="zm-field__hint">
              速记保存到「学习笔记」，会出现在左侧「我的速记」里，也能被检索到。
            </p>
          </div>
          {localError || error ? (
            <p className="zm-field__error">{localError ?? error}</p>
          ) : null}
        </div>

        <footer className="zm-dialog__foot">
          <button
            className="zm-btn zm-btn--ghost"
            type="button"
            onClick={onClose}
          >
            取消
          </button>
          <button
            className="zm-btn zm-btn--primary"
            type="submit"
            disabled={busy}
          >
            {busy ? "保存中…" : "保存速记"}
          </button>
        </footer>
      </form>
    </dialog>
  )
}
