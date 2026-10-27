import { useEffect, useId, useRef, useState } from 'react';
import {
  BookOpen, Bot, Check, CheckCircle2, ChevronDown, ChevronRight, Circle, Copy,
  ExternalLink, FileText, FolderOpen, LoaderCircle, MessageSquare, Moon,
  Search, Send, Sun, Trash2, TriangleAlert, Upload, UserRound, X,
} from 'lucide-react';
import {
  exampleAnswer, initialAnswer, initialDocuments, makeDocument, MAX_FILE_BYTES,
  MAX_MESSAGE_LENGTH, requestId, searchExamples,
} from './demo';

function IconButton({ label, children, ...props }) {
  return <button type="button" className="icon-button" aria-label={label} title={label} {...props}>{children}</button>;
}

function Dialog({ title, children, onClose }) {
  const ref = useRef(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    dialog.showModal();
    return () => { if (dialog.open) dialog.close(); };
  }, []);
  return <dialog ref={ref} className="dialog" aria-labelledby={titleId}
    onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => { if (event.target === ref.current) onClose(); }}>
    <div className="dialog-content">
      <header><h2 id={titleId}>{title}</h2><IconButton label="关闭弹窗" onClick={onClose}><X size={20} /></IconButton></header>
      {children}
    </div>
  </dialog>;
}

function SourceExcerpt({ source }) {
  return <div className="source-excerpt" aria-label={`原文片段 ${source.citation}`}>
    {source.text.split('\n').map((line, index) => <div className="source-line" key={index}>
      <span className="line-number" aria-hidden="true">{source.start + index}</span>
      <span className="line-text">{line || '\u00a0'}</span>
    </div>)}
  </div>;
}

function SourcesPanel({ sources, selected, onSelect, onViewDocument }) {
  const active = sources.find(source => source.citation === selected) ?? sources[0];
  return <aside className="sources-panel" aria-label="本次来源" id="sources-panel">
    <div className="sources-heading"><h2>本次来源</h2><p>{sources.length} 个片段</p></div>
    {sources.length ? <>
      <div className="source-list">{sources.map(source => <button type="button"
        className={`source-row ${source === active ? 'selected' : ''}`} key={source.citation}
        aria-pressed={source === active} onClick={() => onSelect(source.citation)}>
        <span><strong>[{source.citation}] {source.filename}</strong><small>{source.heading} · 第 {source.start}–{source.end} 行</small></span>
        <ChevronRight size={20} />
      </button>)}</div>
      <div className="excerpt-section">
        <div className="excerpt-heading"><h3>原文 · {active.heading}</h3>
          <button type="button" className="secondary-button" onClick={() => onViewDocument(active.documentId)}><ExternalLink size={17} />查看文档</button>
        </div>
        <SourceExcerpt source={active} />
      </div>
    </> : <div className="sources-empty"><BookOpen size={27} /><p>回答的依据会显示在这里</p><small>使用知识库提问后，可查看文件名、行号和原文。</small></div>}
  </aside>;
}

function ImportDialog({ documents, onImport, onClose, announce }) {
  const [file, setFile] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const guard = useRef(false);
  const input = useRef(null);
  async function submit(event) {
    event.preventDefault();
    if (guard.current) return;
    if (!file) { setError('先选择一份 Markdown 或 TXT 文档。'); return; }
    if (!/\.(md|txt)$/i.test(file.name)) { setError('请选择 .md 或 .txt 文件。'); return; }
    if (file.size > MAX_FILE_BYTES) { setError('文件超过 256 KiB，请拆分后再导入。'); return; }
    guard.current = true;
    setBusy(true);
    setError('');
    try {
      const buffer = await file.arrayBuffer();
      const text = new TextDecoder('utf-8', { fatal: true }).decode(buffer);
      if (!text.trim() || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(text)) throw new Error('请选择包含有效内容的 UTF-8 文本文件。');
      const document = makeDocument(crypto.randomUUID(), file.name, text);
      if (documents.some(existing => existing.text === document.text)) {
        announce('这份文档已经导入，已复用现有内容。');
        onClose();
        return;
      }
      if (documents.length >= 20) throw new Error('当前页面最多保留 20 份文档，请先移除一份。');
      onImport(document);
      onClose();
    } catch (reason) {
      setError(reason instanceof TypeError ? '文件编码无法读取，请保存为 UTF-8 后再试。' : reason.message);
    } finally { guard.current = false; setBusy(false); }
  }
  return <Dialog title="导入项目文档" onClose={onClose}>
    <p className="muted">原型中的文档只保留在当前页面，刷新后恢复示例。</p>
    <form onSubmit={submit}>
      <div className="upload-zone" onDragOver={event => event.preventDefault()} onDrop={event => {
        event.preventDefault(); if (!busy) { setFile(event.dataTransfer.files[0] ?? null); setError(''); }
      }}>
        <Upload size={30} /><strong>{file ? file.name : '拖入文档，或选择文件'}</strong>
        <span>UTF-8 · .md / .txt · 单文件最多 256 KiB</span>
        <input type="file" ref={input} aria-label="选择导入文档" accept=".md,.txt" disabled={busy}
          onChange={event => { setFile(event.target.files?.[0] ?? null); setError(''); }} />
      </div>
      {error && <p className="inline-error" role="alert">{error}</p>}
      <div className="dialog-actions"><button type="button" className="secondary-button" onClick={onClose}>取消</button>
        <button className="primary-button" type="submit" disabled={busy}>{busy ? <LoaderCircle className="spinning" size={18} /> : <Upload size={18} />}导入文档</button></div>
    </form>
  </Dialog>;
}

function Knowledge({ documents, previewId, onPreview, onAsk, onImport, onDelete }) {
  const [query, setQuery] = useState('');
  const [searchState, setSearchState] = useState(null);
  const preview = documents.find(document => document.id === previewId);
  const results = searchState === null ? [] : searchExamples(documents, searchState);
  return <div className="knowledge-page">
    <div className="knowledge-intro"><p>导入项目说明或排障笔记，让问题有原文可查。</p>
      <button type="button" className="primary-button" onClick={onImport}><Upload size={18} />导入文档</button></div>
    <div className="library-heading"><h2>已导入文档 <span>{documents.length} / 20</span></h2><small>当前页面的演示数据</small></div>
    {documents.length ? <div className="document-table" role="list" aria-label="已导入文档">
      {documents.map(document => <div className="document-row" key={document.id} role="listitem">
        <button type="button" className={`document-open ${previewId === document.id ? 'selected' : ''}`} onClick={() => onPreview(previewId === document.id ? null : document.id)}>
          <FileText size={23} /><span><strong>{document.filename}</strong><small>{document.sections.length} 个片段 · {(document.bytes / 1024).toFixed(1)} KiB</small></span><ChevronRight size={18} />
        </button>
        <IconButton label={`移除 ${document.filename}`} onClick={() => onDelete(document)}><Trash2 size={18} /></IconButton>
      </div>)}
    </div> : <div className="library-empty"><FolderOpen size={34} /><h3>知识库还是空的</h3><p>先导入一份 .md 或 .txt 文档，再检索其中的内容。</p><button type="button" className="secondary-button" onClick={onImport}>导入第一份文档</button></div>}
    {preview && <section className="document-preview" aria-label="文档预览">
      <header><h3><FileText size={20} />{preview.filename}</h3><IconButton label="收起文档预览" onClick={() => onPreview(null)}><X size={18} /></IconButton></header>
      <p className="muted">文档内容按纯文本展示。</p><pre>{preview.text}</pre>
    </section>}
    <section className="library-search">
      <h2>在知识库中查找</h2><p className="muted">先看原文，再决定是否需要生成回答。</p>
      <form className="search-form" onSubmit={event => { event.preventDefault(); if (query.trim()) setSearchState(query.trim()); }}>
        <label className="sr-only" htmlFor="library-query">检索内容</label>
        <input id="library-query" placeholder="例如：读取超时是否重试" value={query} maxLength={1000} onChange={event => setQuery(event.target.value)} />
        <button type="submit" className="secondary-button" disabled={!query.trim()}><Search size={19} />检索</button>
      </form>
      {searchState !== null && <div className="search-results" aria-live="polite">
        <h3>检索结果 <span>{results.length} 个片段</span></h3>
        {results.length ? <>{results.map(source => <article className="search-result" key={`${source.documentId}-${source.start}`}>
          <details><summary><span><strong>[{source.citation}] {source.filename}</strong><small>{source.heading} · 第 {source.start}–{source.end} 行</small></span><ChevronDown size={18} /></summary>
            <SourceExcerpt source={source} />
          </details>
        </article>)}<button type="button" className="text-button ask-from-results" onClick={() => onAsk(searchState)}><MessageSquare size={18} />带着这个问题去问答</button></>
          : <div className="no-results"><Search size={25} /><h4>没有找到匹配的原文</h4><p>试试文档中的关键词，或导入相关资料后再检索。</p></div>}
      </div>}
    </section>
  </div>;
}

export function App() {
  const [documents, setDocuments] = useState(initialDocuments);
  const [screen, setScreen] = useState('chat');
  const [answer, setAnswer] = useState(() => initialAnswer(initialDocuments()));
  const [selected, setSelected] = useState('S1');
  const [message, setMessage] = useState('');
  const [submitted, setSubmitted] = useState('');
  const [useDocuments, setUseDocuments] = useState(true);
  const [phase, setPhase] = useState('success');
  const [error, setError] = useState(null);
  const [scenario, setScenario] = useState('success');
  const [menu, setMenu] = useState(null);
  const [tone, setTone] = useState('light');
  const [toast, setToast] = useState('');
  const [modal, setModal] = useState(null);
  const [previewId, setPreviewId] = useState(null);
  const [copied, setCopied] = useState(false);
  const [lastRequest, setLastRequest] = useState(null);
  const pending = useRef(false);
  const timer = useRef(null);
  const toastTimer = useRef(null);
  const copyTimer = useRef(null);
  const composer = useRef(null);
  const characterCount = Array.from(message).length;
  useEffect(() => () => { clearTimeout(timer.current); clearTimeout(toastTimer.current); clearTimeout(copyTimer.current); }, []);
  useEffect(() => {
    if (!menu) return;
    function close(event) { if (!event.target.closest('[data-menu]')) setMenu(null); }
    function escape(event) { if (event.key === 'Escape') setMenu(null); }
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape); };
  }, [menu]);
  function announce(text) {
    clearTimeout(toastTimer.current); setToast(text);
    toastTimer.current = setTimeout(() => setToast(''), 4500);
  }
  function navigate(next) { setScreen(next); setMenu(null); }
  function selectSource(citation) {
    setSelected(citation);
    if (window.innerWidth <= 1100) requestAnimationFrame(() => document.getElementById('sources-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }
  function resetExample() {
    if (pending.current) return;
    setAnswer(initialAnswer(documents)); setSelected('S1'); setMessage(''); setError(null);
    setPhase(documents.some(doc => doc.id === 'model-errors') ? 'success' : 'idle');
    setScenario('success'); setLastRequest(null); setMenu(null); setScreen('chat');
  }
  async function copyAnswer() {
    try {
      await navigator.clipboard.writeText([answer.title, ...answer.paragraphs.map(paragraph => paragraph.text)].join('\n\n'));
      setCopied(true); clearTimeout(copyTimer.current); copyTimer.current = setTimeout(() => setCopied(false), 2200);
    } catch { announce('浏览器未允许复制，请选中回答文字复制。'); }
  }
  function submit(event) {
    event.preventDefault();
    if (pending.current) return;
    const question = message.trim();
    if (!question || Array.from(question).length > MAX_MESSAGE_LENGTH) {
      setError({ message: question ? '问题最多 4000 个字符，请精简后再发送。' : '请先输入一个问题。' });
      composer.current?.focus(); return;
    }
    const id = requestId();
    const start = performance.now();
    pending.current = true;
    setSubmitted(question); setPhase('loading'); setError(null); setAnswer(null); setLastRequest(null);
    timer.current = setTimeout(() => {
      try {
        if (scenario !== 'success') {
          setError({ message: scenario === 'missing-key' ? '模型尚未配置，当前无法生成回答。你可以先在知识库中查看原文。' : '本次回答等待超时，问题已保留，可以重新发送。', code: scenario === 'missing-key' ? 'model_not_configured' : 'model_timeout', id });
          setPhase('error'); return;
        }
        const nextAnswer = exampleAnswer(question, documents, useDocuments);
        if (!nextAnswer) {
          setError({ message: '没有找到与问题匹配的原文。试试文档中的关键词，或先导入相关资料。', code: 'no_document_evidence', id });
          setPhase('error'); return;
        }
        setAnswer(nextAnswer); setSelected(nextAnswer.sources[0]?.citation ?? 'S1'); setPhase('success');
        setLastRequest({ id, elapsed: ((performance.now() - start) / 1000).toFixed(1) });
        setMessage('');
      } finally { pending.current = false; }
    }, 1400);
  }
  const shownQuestion = phase === 'loading' || phase === 'error' ? submitted : answer?.question;
  const currentSources = phase === 'success' ? answer?.sources ?? [] : [];
  return <div className={`app-shell tone-${tone}`}>
    <a className="skip-link" href="#main-content">跳到主要内容</a>
    <aside className="sidebar" aria-label="主导航">
      <button type="button" className="brand" onClick={() => navigate('chat')} aria-label="DevAssist 问答首页"><BookOpen size={33} /><span><strong>DevAssist</strong><small>开发知识助手</small></span></button>
      <nav><button type="button" className={screen === 'chat' ? 'active' : ''} aria-current={screen === 'chat' ? 'page' : undefined} onClick={() => navigate('chat')}><MessageSquare size={22} />问答工作台</button>
        <button type="button" className={screen === 'knowledge' ? 'active' : ''} aria-current={screen === 'knowledge' ? 'page' : undefined} onClick={() => navigate('knowledge')}><FileText size={22} />我的知识库</button></nav>
      <div className="sidebar-footer">
        <div className="popover-anchor appearance" data-menu><button type="button" className="sidebar-action" aria-expanded={menu === 'appearance'} onClick={() => setMenu(menu === 'appearance' ? null : 'appearance')}><Moon size={23} />外观设置</button>
          {menu === 'appearance' && <div className="popover bottom"><p>阅读外观</p>{[['light', '清爽浅色'], ['soft', '柔和浅色']].map(([value, label]) => <button type="button" className={tone === value ? 'chosen' : ''} key={value} onClick={() => { setTone(value); setMenu(null); }}><Sun size={17} />{label}{tone === value && <Check size={16} />}</button>)}</div>}
        </div>
        <div className="popover-anchor user" data-menu><button type="button" className="user-button" aria-expanded={menu === 'user'} onClick={() => setMenu(menu === 'user' ? null : 'user')}><span className="avatar">泽</span><strong>泽宝</strong><ChevronDown size={18} /></button>
          {menu === 'user' && <div className="popover bottom user-popover"><strong>泽宝的原型预览</strong><p>文档与问答仅保留在当前页面。</p><small>刷新后恢复示例数据。</small></div>}
        </div>
      </div>
    </aside>
    <main id="main-content" className="workspace">
      <header className="page-header"><div><h1>{screen === 'chat' ? <MessageSquare size={31} /> : <BookOpen size={31} />}{screen === 'chat' ? '问答工作台' : '我的知识库'}</h1>
        <p>{screen === 'chat' ? '从项目文档里找到答案，也看见依据。' : '把项目资料整理好，让每个问题有据可查。'}</p></div>
        <div className="header-status"><div className="popover-anchor" data-menu><button type="button" className="demo-badge" title="切换原型演示状态" aria-expanded={menu === 'demo'} onClick={() => setMenu(menu === 'demo' ? null : 'demo')}>演示示例</button>
          {menu === 'demo' && <div className="popover demo-menu"><p>演示状态</p>{[['success', '正常回答'], ['missing-key', '模型未配置'], ['timeout', '等待超时']].map(([value, label]) => <button type="button" className={scenario === value ? 'chosen' : ''} key={value} disabled={phase === 'loading'} onClick={() => { setScenario(value); setMenu(null); announce(`已切换为“${label}”，发送问题即可体验。`); }}>{label}{scenario === value && <Check size={16} />}</button>)}
            <div className="menu-divider" /><button type="button" disabled={phase === 'loading'} onClick={resetExample}>恢复初始问答示例</button><small>仅演示交互，不调用模型。</small></div>}
        </div><span className="model-status"><Circle size={8} fill="currentColor" aria-hidden="true" />模型未配置</span></div>
      </header>
      {screen === 'knowledge' ? <Knowledge documents={documents} previewId={previewId} onPreview={setPreviewId}
        onAsk={query => { setMessage(query); setUseDocuments(true); navigate('chat'); requestAnimationFrame(() => composer.current?.focus()); }}
        onImport={() => setModal({ type: 'import' })} onDelete={document => setModal({ type: 'delete', document })} />
        : <div className="workbench-grid"><section className="question-column" aria-label="问答">
          {shownQuestion && <div className="question-block"><div className="section-label"><span className="label-icon user-label"><UserRound size={23} /></span>我的问题</div><p className="question-text">{shownQuestion}</p></div>}
          <div className="answer-area" aria-live="polite" aria-busy={phase === 'loading'}>
            {phase === 'loading' ? <div className="loading-state"><span className="label-icon ai-label"><LoaderCircle size={21} className="spinning" /></span><h2>正在整理示例回答…</h2><p>问题已发送，请稍候。</p><small>原型演示，不调用模型</small></div>
              : phase === 'error' ? <div className="error-state" role="alert"><TriangleAlert size={25} /><h2>{error?.code === 'no_document_evidence' ? '先补充一点相关资料' : error?.code === 'model_timeout' ? '这次等待超时了' : '暂时无法生成回答'}</h2><p>{error?.message}</p>{error?.id && <small>演示请求标识：{error.id}</small>}<button type="button" className="secondary-button" onClick={() => navigate('knowledge')}><BookOpen size={17} />查看我的知识库</button></div>
                : answer ? <>
                  <div className="section-label"><span className="label-icon ai-label"><Bot size={22} /></span>示例回答</div><h2 className="answer-title">{answer.title}</h2>
                  <div className="answer-prose">{answer.paragraphs.map((paragraph, index) => <p key={index}>{paragraph.text}{paragraph.citation && <button type="button" className="citation-chip" aria-label={`查看引用 ${paragraph.citation}`} aria-pressed={selected === paragraph.citation} onClick={() => selectSource(paragraph.citation)}>[{paragraph.citation}]</button>}</p>)}</div>
                  <button type="button" className="secondary-button copy-button" onClick={copyAnswer}>{copied ? <Check size={18} /> : <Copy size={18} />}{copied ? '已复制' : '复制回答'}</button><p className="answer-note">{answer.sources.length ? '来源为检索片段，请结合原文核对。' : '当前是普通问答示例，未使用项目文档。'}</p>
                  {lastRequest && <p className="request-meta">演示请求：{lastRequest.id} · 界面等待 {lastRequest.elapsed} 秒</p>}
                </> : <div className="welcome-state"><MessageSquare size={31} /><h2>从一个项目问题开始</h2><p>输入你的问题，查看示例回答与对应原文。</p><button type="button" className="text-button" onClick={() => { setMessage('读取超时是否应该重试？'); composer.current?.focus(); }}>试试：读取超时是否应该重试？</button></div>}
          </div>
          <form className="composer" onSubmit={submit}><label htmlFor="question">继续提出一个新问题</label>
            <textarea id="question" ref={composer} rows={2} placeholder="例如：如何设置异步请求的超时？" value={message} disabled={phase === 'loading'} aria-describedby="question-help question-counter"
              onChange={event => { setMessage(event.target.value); if (error && !error.code) setError(null); }} />
            {error && !error.code && <p className="inline-error" role="alert">{error.message}</p>}
            <div className="composer-help"><span id="question-help">每次提问独立 · 刷新后清空</span><span id="question-counter" className={characterCount > MAX_MESSAGE_LENGTH ? 'over-limit' : ''}>{characterCount} / 4000</span></div>
            <div className="composer-controls"><label className="knowledge-toggle"><input type="checkbox" checked={useDocuments} disabled={phase === 'loading'} onChange={event => setUseDocuments(event.target.checked)} /><span>使用我的知识库</span><small>{documents.length} 份文档</small></label>
              <button type="submit" className="primary-button send-button" disabled={phase === 'loading' || !message.trim() || characterCount > MAX_MESSAGE_LENGTH}>{phase === 'loading' ? <LoaderCircle size={21} className="spinning" /> : <Send size={21} />}{phase === 'loading' ? '正在回答' : '发送问题'}</button></div>
          </form>
        </section><SourcesPanel sources={currentSources} selected={selected} onSelect={selectSource}
          onViewDocument={id => { if (!documents.some(doc => doc.id === id)) { announce('这份文档已移除，请重新检索。'); return; } setPreviewId(id); navigate('knowledge'); }} /></div>}
    </main>
    {toast && <div className="toast" role="status"><CheckCircle2 size={19} /><span>{toast}</span><IconButton label="关闭提示" onClick={() => setToast('')}><X size={16} /></IconButton></div>}
    {modal?.type === 'import' && <ImportDialog documents={documents} announce={announce} onClose={() => setModal(null)} onImport={document => { setDocuments(existing => [document, ...existing]); announce('文档已导入当前页面。'); }} />}
    {modal?.type === 'delete' && <Dialog title="移除这份文档？" onClose={() => setModal(null)}><p>将从当前原型中移除 <strong>{modal.document.filename}</strong> 及其片段。</p><p className="muted">这不会修改 DevAssist 应用数据库。刷新页面可恢复初始示例。</p><div className="dialog-actions"><button type="button" className="secondary-button" onClick={() => setModal(null)}>保留文档</button><button type="button" className="danger-button" onClick={() => { setDocuments(existing => existing.filter(doc => doc.id !== modal.document.id)); if (previewId === modal.document.id) setPreviewId(null); setAnswer(null); setPhase('idle'); setModal(null); announce('已从当前页面移除文档。'); }}>确认移除</button></div></Dialog>}
  </div>;
}
