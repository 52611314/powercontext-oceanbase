import { useRef, useState } from 'react';
import { api } from '../lib/api';
import { chunkText } from '../lib/chunk';
import type { EmbeddingClient } from '../lib/embedding-client';

type Props = {
  kb: string;
  client: EmbeddingClient | null;
  disabled: boolean;
  onImported: () => Promise<void>;
  append: (line: string) => void;
};

type Timing = { chunks: number; embedMs: number; insertMs: number; refreshMs: number; totalMs: number } | null;

const EMBED_BATCH = 8;
const WRITE_BATCH = 64;

export default function ImportPanel({ kb, client, disabled, onImported, append }: Props) {
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<{ phase: string; done: number; total: number } | null>(null);
  const [timing, setTiming] = useState<Timing>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadFile = async (file: File | undefined) => {
    if (!file) return;
    const content = await file.text();
    setText(content);
    if (!title) setTitle(file.name.replace(/\.[^.]+$/, ''));
  };

  const run = async () => {
    if (!client) {
      append('请先加载模型');
      return;
    }
    if (!kb) {
      append('请先选择或创建知识库');
      return;
    }
    const chunks = chunkText(text);
    if (chunks.length === 0) {
      append('文档内容为空');
      return;
    }

    setRunning(true);
    setTiming(null);
    const started = performance.now();
    try {
      const docId = Date.now().toString(36);
      const items: Array<{
        id: string;
        text: string;
        embedding: number[];
        metadata: Record<string, string | number>;
      }> = chunks.map((chunk, index) => ({
        id: `${kb}-${docId}-${index}`,
        text: chunk,
        embedding: [],
        metadata: {
          title: title.trim() || '未命名文档',
          chunk: index,
          docId,
          chars: chunk.length,
        },
      }));

      let embedMs = 0;
      for (let i = 0; i < items.length; i += EMBED_BATCH) {
        setProgress({ phase: '向量化', done: i, total: items.length });
        const slice = items.slice(i, i + EMBED_BATCH);
        const result = await client.embed(slice.map((item) => item.text), 'doc');
        embedMs += result.embedMs;
        result.vectors.forEach((vector, index) => {
          slice[index].embedding = vector;
        });
      }

      setProgress({ phase: '写入 seekdb', done: 0, total: items.length });
      let insertMs = 0;
      let refreshMs = 0;
      for (let i = 0; i < items.length; i += WRITE_BATCH) {
        const result = await api.addDocs(kb, items.slice(i, i + WRITE_BATCH));
        insertMs += result.insertMs;
        refreshMs += result.refreshMs;
        setProgress({ phase: '写入 seekdb', done: Math.min(i + WRITE_BATCH, items.length), total: items.length });
      }

      const totalMs = Math.round(performance.now() - started);
      setTiming({ chunks: items.length, embedMs, insertMs, refreshMs, totalMs });
      append(
        `已导入 ${items.length} 块到 ${kb}：向量化 ${embedMs}ms · 写入 ${insertMs}ms · 刷新 ${refreshMs}ms · 合计 ${totalMs}ms`,
      );
      setText('');
      setProgress(null);
      await onImported();
    } catch (err) {
      append(`导入失败: ${(err as Error).message}`);
    } finally {
      setRunning(false);
    }
  };

  const pct = progress && progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;

  return (
    <section className="card">
      <h2>导入文档</h2>
      <div className="row">
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="文档标题（可选）" />
        <button className="ghost" onClick={() => fileRef.current?.click()} disabled={running}>
          选择 .txt / .md
        </button>
        <input ref={fileRef} type="file" accept=".txt,.md,text/plain,text/markdown" hidden onChange={(e) => loadFile(e.target.files?.[0])} />
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="粘贴要导入的文本，或选择文件。导入时会按 400 字分块（重叠 50 字），在浏览器本地向量化后写入 seekdb。"
        rows={7}
        disabled={running}
      />
      <div className="row">
        <button onClick={run} disabled={running || disabled || !text.trim()}>
          {running ? '导入中…' : '开始导入'}
        </button>
        <span className="hint small">目标库：{kb || '未选择'}</span>
      </div>
      {progress && (
        <div className="progress">
          <div className="progress-bar" style={{ width: `${pct}%` }} />
          <span>
            {progress.phase} {progress.done}/{progress.total}（{pct}%）
          </span>
        </div>
      )}
      {timing && (
        <table className="timing">
          <thead>
            <tr>
              <th>分块</th>
              <th>向量化</th>
              <th>写入</th>
              <th>刷新索引</th>
              <th>合计</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{timing.chunks}</td>
              <td>{timing.embedMs} ms</td>
              <td>{timing.insertMs} ms</td>
              <td>{timing.refreshMs} ms</td>
              <td>{timing.totalMs} ms</td>
            </tr>
          </tbody>
        </table>
      )}
    </section>
  );
}
