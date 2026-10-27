import { useState } from 'react';
import { api, type SearchItem } from '../lib/api';
import { highlight } from '../lib/chunk';
import type { EmbeddingClient } from '../lib/embedding-client';

type Props = {
  kb: string;
  client: EmbeddingClient | null;
  disabled: boolean;
  append: (line: string) => void;
  onChanged: () => Promise<void>;
};

type Timing = { queryEmbed: number; search: number; total: number; candidateSize: number } | null;

export default function SearchPanel({ kb, client, disabled, append, onChanged }: Props) {
  const [query, setQuery] = useState('');
  const [running, setRunning] = useState(false);
  const [items, setItems] = useState<SearchItem[]>([]);
  const [timing, setTiming] = useState<Timing>(null);
  const [lastQuery, setLastQuery] = useState('');

  const run = async () => {
    if (!client) {
      append('请先加载模型');
      return;
    }
    if (!kb) {
      append('请先选择知识库');
      return;
    }
    const q = query.trim();
    if (!q) return;

    setRunning(true);
    setItems([]);
    setTiming(null);
    const started = performance.now();
    try {
      const queryVector = await client.embed([q], 'query');
      const response = await api.search(kb, { query: q, embedding: queryVector.vectors[0], nResults: 10 });
      const total = Math.round(performance.now() - started);
      setItems(response.items);
      setLastQuery(q);
      setTiming({
        queryEmbed: queryVector.embedMs,
        search: response.latencyMs,
        total,
        candidateSize: response.candidateSize,
      });
      append(
        `检索「${q}」：查询向量 ${queryVector.embedMs}ms · seekdb ${response.latencyMs}ms · 端到端 ${total}ms · ${response.items.length} 条`,
      );
    } catch (err) {
      append(`检索失败: ${(err as Error).message}`);
    } finally {
      setRunning(false);
    }
  };

  const remove = async (id: string) => {
    try {
      await api.deleteDocs(kb, [id]);
      setItems((prev) => prev.filter((item) => item.id !== id));
      append(`已删除文档块 ${id}`);
      await onChanged();
    } catch (err) {
      append(`删除失败: ${(err as Error).message}`);
    }
  };

  return (
    <section className="card">
      <h2>混合检索</h2>
      <div className="row">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !running && run()}
          placeholder="输入查询，例如：索引刷新后新数据多久可见"
        />
        <button onClick={run} disabled={running || disabled || !query.trim()}>
          {running ? '检索中…' : '检索'}
        </button>
      </div>
      <p className="hint small">
        查询向量在浏览器计算，随后由 seekdb 用一条 DBMS_HYBRID_SEARCH 语句完成全文 + 向量 RRF 融合。
      </p>

      {timing && (
        <table className="timing">
          <thead>
            <tr>
              <th>查询向量化</th>
              <th>seekdb 混合检索</th>
              <th>候选数</th>
              <th>端到端</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{timing.queryEmbed} ms</td>
              <td>{timing.search} ms</td>
              <td>{timing.candidateSize}</td>
              <td>{timing.total} ms</td>
            </tr>
          </tbody>
        </table>
      )}

      {items.length > 0 && (
        <ol className="results">
          {items.map((item, index) => (
            <li key={item.id}>
              <div className="result-head">
                <span className="rank">#{index + 1}</span>
                <span className="score">score {item.score.toFixed(4)}</span>
                <span className="meta">
                  {String(item.metadata.title ?? '未命名')} · chunk {String(item.metadata.chunk ?? '-')}
                </span>
                <button className="ghost tiny" onClick={() => remove(item.id)}>
                  删除
                </button>
              </div>
              <p className="result-text">
                {highlight(item.text, lastQuery).map((part, i) =>
                  part.hit ? <mark key={i}>{part.text}</mark> : <span key={i}>{part.text}</span>,
                )}
              </p>
            </li>
          ))}
        </ol>
      )}
      {!running && timing === null && <p className="hint small">还没有检索结果。</p>}
    </section>
  );
}
