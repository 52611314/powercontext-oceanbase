import type {
  AnswerRecord,
  AnswerSummary,
  AskResult,
  DebugInfo,
  DocumentItem,
  PageText,
  SearchResult,
  Stats,
} from './types'

const BASE = ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init)
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      message = body?.detail?.message ?? body?.detail ?? message
    } catch {
      /* keep the status text */
    }
    throw new Error(typeof message === 'string' ? message : JSON.stringify(message))
  }
  return response.json() as Promise<T>
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  ready: () => request<{ ok: boolean; dim: number }>('/api/ready'),

  listDocuments: (params: Record<string, string> = {}) => {
    const query = new URLSearchParams(params).toString()
    return request<{ items: DocumentItem[]; total: number }>(`/api/documents${query ? `?${query}` : ''}`)
  },

  getDocument: (id: string) => request<DocumentItem>(`/api/documents/${id}`),

  upload: (files: File[], tags: string) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    if (tags) form.append('tags', tags)
    return request<{ items: Array<{ file_name: string; duplicate?: boolean; document?: DocumentItem; error?: string }> }>(
      '/api/documents',
      { method: 'POST', body: form },
    )
  },

  deleteDocument: (id: string) =>
    request<{ deleted: boolean }>(`/api/documents/${id}`, { method: 'DELETE' }),

  reindex: (id: string) =>
    request<{ id: string; status: string }>(`/api/documents/${id}/reindex`, { method: 'POST' }),

  patchDocument: (id: string, body: Record<string, unknown>) =>
    request<DocumentItem>(`/api/documents/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  getPage: (id: string, page: number) => request<PageText>(`/api/documents/${id}/pages/${page}`),

  search: (body: {
    query: string
    mode: string
    doc_ids?: string[]
    tags?: string[]
    top_k?: number
  }) => request<SearchResult>('/api/search', json(body)),

  ask: (body: { question: string; doc_ids?: string[]; tags?: string[] }) =>
    request<AskResult>('/api/ask', json(body)),

  listAnswers: (limit = 20) =>
    request<{ items: AnswerSummary[] }>(`/api/answers?limit=${limit}`),

  getAnswer: (id: string) => request<AnswerRecord>(`/api/answers/${id}`),

  deleteAnswer: (id: string) =>
    request<{ deleted: boolean }>(`/api/answers/${id}`, { method: 'DELETE' }),

  debug: (searchId: string) => request<DebugInfo>(`/api/debug/retrieval/${searchId}`),

  stats: () => request<Stats>('/api/stats'),
}
