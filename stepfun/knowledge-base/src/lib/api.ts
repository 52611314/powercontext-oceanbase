/** BFF 客户端。所有向量都由浏览器计算后传入，服务端只做校验与 seekdb 读写。 */

export type Kb = { name: string; count: number; dimension: number };

export type SearchItem = {
  id: string;
  text: string;
  score: number;
  metadata: Record<string, string | number | boolean>;
};

export type Health = {
  ok: boolean;
  seekdb: { host: string; port: number; database: string; version: string };
  embeddingDim: number;
  maxBatch: number;
  hybridSearch: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: init?.body ? { 'content-type': 'application/json' } : undefined,
    ...init,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(data?.error?.message ?? `请求失败 HTTP ${res.status}`);
  }
  return data as T;
}

export const api = {
  health: () => request<Health>('/health'),

  listKb: () => request<{ collections: Kb[] }>('/collections').then((r) => r.collections),

  createKb: (name: string) =>
    request<Kb>('/collections', { method: 'POST', body: JSON.stringify({ name }) }),

  deleteKb: (name: string) =>
    request<{ deleted: boolean }>(`/collections/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  addDocs: (
    kb: string,
    items: Array<{ id: string; text: string; embedding: number[]; metadata: Record<string, string | number | boolean> }>,
  ) =>
    request<{ inserted: number; insertMs: number; refreshMs: number; count: number }>(
      `/collections/${encodeURIComponent(kb)}/documents`,
      { method: 'POST', body: JSON.stringify({ items }) },
    ),

  deleteDocs: (kb: string, ids: string[]) =>
    request<{ deleted: number; elapsedMs: number; count: number }>(
      `/collections/${encodeURIComponent(kb)}/delete-documents`,
      { method: 'POST', body: JSON.stringify({ ids }) },
    ),

  search: (kb: string, body: { query: string; embedding: number[]; nResults?: number }) =>
    request<{ query: string; items: SearchItem[]; latencyMs: number; candidateSize: number }>(
      `/collections/${encodeURIComponent(kb)}/search`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
};
