import { useEffect, useRef, useState } from 'react';
import ImportPanel from './components/ImportPanel';
import SearchPanel from './components/SearchPanel';
import Sidebar from './components/Sidebar';
import { api, type Kb } from './lib/api';
import { EmbeddingClient, type Backend } from './lib/embedding-client';

type Progress = { file?: string; pct?: number } | null;

export default function App() {
  const clientRef = useRef<EmbeddingClient | null>(null);
  const [backend, setBackend] = useState<Backend | null>(null);
  const [fallback, setFallback] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress>(null);
  const [loading, setLoading] = useState(false);
  const [kbs, setKbs] = useState<Kb[]>([]);
  const [current, setCurrent] = useState('');
  const [log, setLog] = useState<string[]>([]);
  const [bffError, setBffError] = useState('');
  const [kbsBusy, setKbsBusy] = useState(false);

  const append = (line: string) =>
    setLog((prev) => [...prev.slice(-60), `${new Date().toLocaleTimeString()} ${line}`]);

  const getClient = () => {
    if (!clientRef.current) {
      const client = new EmbeddingClient();
      client.onProgress = (p) =>
        setProgress({ file: p.file, pct: typeof p.progress === 'number' ? p.progress : undefined });
      client.onFallback = (info) => {
        setFallback(info.reason);
        append(`WebGPU 降级: ${info.reason}`);
      };
      clientRef.current = client;
    }
    return clientRef.current;
  };

  const refreshKbs = async (): Promise<Kb[]> => {
    try {
      const list = await api.listKb();
      setKbs(list);
      setBffError('');
      setCurrent((prev) => (prev && list.some((kb) => kb.name === prev) ? prev : list[0]?.name ?? ''));
      return list;
    } catch (err) {
      setBffError((err as Error).message);
      return [];
    }
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const list = await refreshKbs();
      if (!cancelled && list.length === 0) {
        try {
          await api.createKb('default');
          await refreshKbs();
          append('已创建默认知识库 default');
        } catch (err) {
          append(`创建默认知识库失败: ${(err as Error).message}`);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadModel = async () => {
    setLoading(true);
    setProgress(null);
    setFallback(null);
    try {
      const params = new URLSearchParams(window.location.search);
      const preferParam = params.get('prefer');
      const dtypeParam = params.get('dtype');
      const prefer = preferParam === 'wasm' || preferParam === 'webgpu' ? preferParam : 'auto';
      const dtype = dtypeParam === 'fp32' || dtypeParam === 'fp16' || dtypeParam === 'q8' ? dtypeParam : undefined;
      const info = await getClient().init(prefer, dtype);
      setBackend(info);
      append(`模型就绪: ${info.device}/${info.dtype}, 加载 ${info.loadMs ?? '?'}ms`);
    } catch (err) {
      append(`模型加载失败: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const createKb = async (name: string) => {
    setKbsBusy(true);
    try {
      await api.createKb(name);
      await refreshKbs();
      setCurrent(name);
      append(`已创建知识库 ${name}`);
    } finally {
      setKbsBusy(false);
    }
  };

  const deleteKb = async (name: string) => {
    setKbsBusy(true);
    try {
      await api.deleteKb(name);
      const list = await refreshKbs();
      if (!list.some((kb) => kb.name === current)) setCurrent(list[0]?.name ?? '');
      append(`已删除知识库 ${name}`);
    } finally {
      setKbsBusy(false);
    }
  };

  const badge = backend
    ? `${backend.device === 'webgpu' ? 'WebGPU' : 'WASM'} · ${backend.dtype.toUpperCase()}`
    : '未加载模型';

  return (
    <main className="app">
      <header className="topbar">
        <div>
          <h1>seekdb 迷你知识库</h1>
          <p className="subtitle">浏览器本地向量化 · seekdb 库内混合检索</p>
        </div>
        <div className={`badge ${backend ? (backend.device === 'webgpu' ? 'badge-gpu' : 'badge-wasm') : 'badge-idle'}`}>
          {badge}
        </div>
      </header>

      {fallback && <div className="notice">已自动降级到 WASM：{fallback}</div>}
      {bffError && <div className="notice error">BFF 不可用：{bffError}</div>}

      <div className="layout">
        <Sidebar
          kbs={kbs}
          current={current}
          busy={kbsBusy}
          onSelect={setCurrent}
          onCreate={createKb}
          onDelete={deleteKb}
        />

        <div className="main-col">
          <section className="card">
            <div className="row between">
              <h2>端侧模型</h2>
              <button onClick={loadModel} disabled={loading}>
                {loading ? '加载中…' : backend ? '重新加载模型' : '加载模型'}
              </button>
            </div>
            <p className="hint">
              bge-small-zh-v1.5（INT8 22.8MB / FP16 45.3MB / FP32 90.5MB），从本地 <code>/models</code> 读取，
              不调用任何云端推理接口。
            </p>
            <div className="row">
              {progress?.file && (
                <span className="mono">
                  {progress.file} {progress.pct != null ? `${progress.pct.toFixed(0)}%` : ''}
                </span>
              )}
              {backend && (
                <span className="mono">
                  {backend.device} / {backend.dtype} · 加载 {backend.loadMs ?? '?'}ms · {backend.reason}
                </span>
              )}
            </div>
          </section>

          <ImportPanel
            kb={current}
            client={clientRef.current}
            disabled={!backend}
            onImported={async () => {
              await refreshKbs();
            }}
            append={append}
          />

          <SearchPanel
            kb={current}
            client={clientRef.current}
            disabled={!backend}
            append={append}
            onChanged={async () => {
              await refreshKbs();
            }}
          />

          <section className="card">
            <h2>运行日志</h2>
            <pre className="log">{log.join('\n') || '（暂无）'}</pre>
          </section>
        </div>
      </div>
    </main>
  );
}
