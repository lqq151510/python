// Frontend-only examples. No API request, model request or application database access.
import errorsText from '../../../examples/knowledge/model-errors.md?raw';
import asyncText from '../../../examples/knowledge/async.md?raw';
import operationsText from '../../../examples/knowledge/operations.md?raw';

export const MAX_MESSAGE_LENGTH = 4000;
export const MAX_FILE_BYTES = 256 * 1024;

export function makeDocument(id, filename, text) {
  const normalized = text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
  const lines = normalized.split('\n');
  if (lines.at(-1) === '') lines.pop();
  const sections = [];
  let start = 0;
  let heading = filename;
  for (let i = 0; i < lines.length; i++) {
    const match = lines[i].match(/^#{1,6}\s+(.+)/);
    if (!match) continue;
    if (i > start && lines.slice(start, i).some(line => line.trim())) {
      sections.push({ heading, start: start + 1, end: i, text: lines.slice(start, i).join('\n') });
    }
    start = i;
    heading = match[1];
  }
  if (lines.slice(start).some(line => line.trim())) {
    sections.push({ heading, start: start + 1, end: lines.length, text: lines.slice(start).join('\n') });
  }
  return { id, filename, text: normalized, sections, bytes: new TextEncoder().encode(normalized).length };
}

export function initialDocuments() {
  return [
    makeDocument('model-errors', 'model-errors.md', errorsText),
    makeDocument('operations', 'operations.md', operationsText),
    makeDocument('async', 'async.md', asyncText),
  ];
}

function sourceFor(document, section, index) {
  return { ...section, documentId: document.id, filename: document.filename, citation: `S${index + 1}` };
}

export function initialAnswer(documents) {
  const document = documents.find(doc => doc.id === 'model-errors');
  if (!document) return null;
  return {
    question: '读取超时是否应该重试？',
    title: '读取超时，不自动重试。',
    paragraphs: [
      { text: '上游可能已经接受并处理了请求。再次发送可能产生重复处理或额外费用。', citation: 'S1' },
      { text: '整个模型操作共用 30 秒预算。只对连接建立失败、429 或 5xx 最多重试一次。', citation: 'S2' },
    ],
    sources: [sourceFor(document, document.sections[3], 0), sourceFor(document, document.sections[2], 1)],
  };
}

function tokensFor(query) {
  const runs = query.toLowerCase().match(/[\p{Script=Han}]+|[a-z0-9_]+/gu) ?? [];
  const tokens = [];
  for (const run of runs) {
    if (/^\p{Script=Han}+$/u.test(run)) {
      const characters = Array.from(run);
      if (characters.length === 1) tokens.push(run);
      for (let i = 0; i < characters.length - 1; i++) tokens.push(characters[i] + characters[i + 1]);
    } else tokens.push(run);
  }
  return [...new Set(tokens)];
}

// A small illustrative word matcher, not the backend BM25 implementation.
export function searchExamples(documents, query) {
  const tokens = tokensFor(query);
  return documents.flatMap(document => document.sections.map(section => ({ document, section,
    hits: tokens.reduce((count, token) => count + Number(section.text.toLowerCase().includes(token)), 0),
  }))).filter(item => item.hits > 0).sort((a, b) => b.hits - a.hits).slice(0, 5)
    .map((item, index) => sourceFor(item.document, item.section, index));
}

export function exampleAnswer(question, documents, useDocuments) {
  const sources = useDocuments ? searchExamples(documents, question) : [];
  if (useDocuments && sources.length === 0) return null;
  if (/读取|read\s*timeout/i.test(question) && /超时|timeout/i.test(question)) {
    const answer = initialAnswer(documents);
    if (answer) return { ...answer, question, sources: useDocuments ? answer.sources : [],
      paragraphs: answer.paragraphs.map(paragraph => ({ ...paragraph, citation: useDocuments ? paragraph.citation : undefined })) };
  }
  if (/异步|async|await/i.test(question)) return {
    question, title: '异步让等待期间也能处理其他请求。',
    paragraphs: [
      { text: 'await 等待网络 I/O 时，会把控制权交回事件循环。异步不会让模型本身生成得更快。', citation: sources[0]?.citation },
      { text: '文件解析和阻塞的数据库操作应放在线程池；长时间训练应作为独立任务。', citation: sources[1]?.citation },
    ], sources,
  };
  if (/迁移|数据库|postgres|docker/i.test(question)) return {
    question, title: '先准备数据库，再执行迁移。',
    paragraphs: [
      { text: '数据库结构由 Alembic 迁移管理。开发数据与临时测试数据库分开，测试结束后只移除临时数据库。', citation: sources[0]?.citation },
      { text: '这里展示的是随项目附带的示例内容，实际操作前请核对自己的运行环境。' },
    ], sources,
  };
  return {
    question, title: sources.length ? '先从这些原文片段开始核对。' : '先明确问题，再查资料验证。',
    paragraphs: [{ text: sources.length ? '下面的片段与问题中的关键词匹配。请结合项目上下文核对，避免把局部说明当作完整结论。' : '这是普通问答的界面示例。接入真实模型后，回答将由后端模型接口返回。', citation: sources[0]?.citation }], sources,
  };
}

export function requestId() {
  return `demo-${crypto.randomUUID().slice(0, 8)}`;
}
