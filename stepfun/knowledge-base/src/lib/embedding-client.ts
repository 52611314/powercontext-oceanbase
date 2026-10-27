/**
 * 主线程侧的 Worker 封装：Promise 化的模型加载与批量向量化。
 */
export type Backend = {
  device: 'webgpu' | 'wasm';
  dtype: string;
  adapter?: { vendor?: string; architecture?: string; description?: string } | null;
  reason: string;
  loadMs?: number;
};

type Pending = {
  resolve: (value: any) => void;
  reject: (reason: Error) => void;
};

export class EmbeddingClient {
  private worker: Worker;
  private seq = 0;
  private pending = new Map<number, Pending>();

  onProgress?: (progress: any) => void;
  onBackend?: (backend: Backend) => void;
  onFallback?: (info: { from: string; reason: string }) => void;

  constructor() {
    this.worker = new Worker(new URL('../worker/embedding.worker.ts', import.meta.url), { type: 'module' });
    this.worker.onmessage = (event: MessageEvent) => this.handle(event.data);
    this.worker.onerror = (event) => {
      for (const [, pending] of this.pending) pending.reject(new Error(event.message || 'Worker 错误'));
      this.pending.clear();
    };
  }

  private handle(msg: any) {
    if (msg.type === 'progress') {
      this.onProgress?.(msg.progress);
      return;
    }
    if (msg.type === 'ready') {
      this.onBackend?.(msg.backend);
      return;
    }
    if (msg.type === 'fallback') {
      this.onFallback?.(msg);
      return;
    }
    if (msg.type === 'result' || msg.type === 'error') {
      const pending = this.pending.get(msg.id);
      if (!pending) return;
      this.pending.delete(msg.id);
      if (msg.type === 'result') pending.resolve(msg);
      else pending.reject(new Error(msg.message));
      return;
    }
    if (msg.type === 'inited') {
      const pending = this.pending.get(-1);
      if (pending) {
        this.pending.delete(-1);
        pending.resolve(msg.backend);
      }
    }
  }

  private request<T>(payload: any, id: number): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage(payload);
    });
  }

  init(prefer: 'auto' | 'webgpu' | 'wasm' = 'auto', dtype?: string): Promise<Backend> {
    return this.request<Backend>({ type: 'init', prefer, dtype }, -1);
  }

  async embed(texts: string[], kind: 'doc' | 'query' = 'doc'): Promise<{ vectors: number[][]; embedMs: number }> {
    const id = ++this.seq;
    return this.request({ type: 'embed', id, texts, kind }, id);
  }

  terminate() {
    this.worker.terminate();
    for (const [, pending] of this.pending) pending.reject(new Error('客户端已关闭'));
    this.pending.clear();
  }
}

export function cosine(a: number[], b: number[]): number {
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  return dot / (Math.sqrt(na) * Math.sqrt(nb) || 1);
}
