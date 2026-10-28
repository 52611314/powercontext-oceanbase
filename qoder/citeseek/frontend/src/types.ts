export type DocStatus = 'pending' | 'indexing' | 'ready' | 'failed'

export interface DocumentItem {
  id: string
  title: string
  authors: string
  year: number | null
  venue: string
  source_type: string
  file_name: string
  file_size: number
  page_count: number
  progress: number
  status: DocStatus
  read_status: 'unread' | 'reading' | 'read'
  error: string
  tags: string[]
  chunk_count: number
  created_at: string
  updated_at: string
}

export interface PageText {
  page: number
  section: string
  text: string
}

export interface ChannelInfo {
  rank: number
  score?: number | null
  distance?: number | null
}

export interface SearchHit {
  chunk_id: string
  doc_id: string
  doc_title: string
  page: number
  section: string
  text: string
  snippet: string
  spans: [number, number][]
  score: number
  channels: Record<string, ChannelInfo>
  char_start: number
  char_end: number
}

export interface SearchResult {
  search_id: string
  query: string
  mode: 'hybrid' | 'fts' | 'vector'
  latency_ms: number
  timings: { embed_ms: number; sql_ms: number; merge_ms: number }
  channel_counts: Record<string, number>
  hits: SearchHit[]
}

export interface Citation {
  chunk_id: string
  doc_id: string
  doc_title: string
  page: number
  section: string
  char_start: number
  char_end: number
}

export interface Bullet {
  text: string
  score: number
  citation: Citation
  channels: Record<string, ChannelInfo>
}

export interface AskResult {
  answer_id: string
  question: string
  bullets: Bullet[]
  search_id: string
  latency_ms: number
  timings: { embed_ms: number; sql_ms: number; merge_ms: number }
  channel_counts: Record<string, number>
  hits: Array<Pick<SearchHit, 'chunk_id' | 'doc_id' | 'doc_title' | 'page' | 'section' | 'score' | 'channels'>>
}

export interface AnswerRecord {
  id: string
  question: string
  search_id: string
  latency_ms: number
  created_at: string
  bullets: Bullet[]
}

export interface AnswerSummary {
  id: string
  question: string
  search_id: string
  latency_ms: number
  created_at: string
}

export interface DebugInfo {
  search_id: string
  query: string
  mode: string
  filters: Record<string, unknown>
  sql: string
  explain: string
  channel_counts: Record<string, number>
  rows: number
  indexes: { fulltext: boolean; vector: boolean }
}

export interface Stats {
  documents: number
  pages: number
  chunks: number
  ready: number
  indexing: number
  failed: number
}
