import { useState } from 'react';
import type { Kb } from '../lib/api';

type Props = {
  kbs: Kb[];
  current: string;
  busy: boolean;
  onSelect: (name: string) => void;
  onCreate: (name: string) => Promise<void>;
  onDelete: (name: string) => Promise<void>;
};

export default function Sidebar({ kbs, current, busy, onSelect, onCreate, onDelete }: Props) {
  const [name, setName] = useState('');
  const [error, setError] = useState('');

  const create = async () => {
    const slug = name.trim().toLowerCase();
    if (!/^[a-z0-9][a-z0-9_-]{1,38}[a-z0-9]$/.test(slug)) {
      setError('名称需 3-40 位小写字母、数字、_ 或 -');
      return;
    }
    setError('');
    try {
      await onCreate(slug);
      setName('');
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const remove = async () => {
    if (!current) return;
    if (!window.confirm(`删除知识库 ${current} 及其全部文档？`)) return;
    try {
      await onDelete(current);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <aside className="sidebar">
      <h2>知识库</h2>
      <ul className="kb-list">
        {kbs.length === 0 && <li className="kb-empty">还没有知识库</li>}
        {kbs.map((kb) => (
          <li key={kb.name}>
            <button className={`kb-item ${kb.name === current ? 'active' : ''}`} onClick={() => onSelect(kb.name)} disabled={busy}>
              <span className="kb-name">{kb.name}</span>
              <span className="kb-count">{kb.count}</span>
            </button>
          </li>
        ))}
      </ul>

      <div className="kb-create">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="新建库名称"
          onKeyDown={(e) => e.key === 'Enter' && create()}
        />
        <button onClick={create} disabled={busy || !name.trim()}>
          创建
        </button>
      </div>
      {current && (
        <button className="danger" onClick={remove} disabled={busy}>
          删除当前库
        </button>
      )}
      {error && <p className="error">{error}</p>}
      <p className="hint small">一个知识库对应 seekdb 的一个 collection（kb_&lt;名称&gt;），切换后检索只查当前库。</p>
    </aside>
  );
}
