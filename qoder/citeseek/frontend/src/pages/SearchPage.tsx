import { ExperimentOutlined, FileTextOutlined, SearchOutlined } from '@ant-design/icons'
import {
  App as AntApp,
  Button,
  Card,
  Col,
  Empty,
  Input,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import EvidenceDrawer from '../components/EvidenceDrawer'
import HighlightText from '../components/HighlightText'
import PageDrawer from '../components/PageDrawer'
import type { DocumentItem, SearchResult } from '../types'

interface PageTarget {
  docId: string
  page: number
  ranges: Array<[number, number]>
  title: string
}

export default function SearchPage() {
  const { message } = AntApp.useApp()
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<'hybrid' | 'fts' | 'vector'>('hybrid')
  const [docs, setDocs] = useState<DocumentItem[]>([])
  const [docIds, setDocIds] = useState<string[]>([])
  const [tagInput, setTagInput] = useState('')
  const [result, setResult] = useState<SearchResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [evidenceId, setEvidenceId] = useState<string | null>(null)
  const [target, setTarget] = useState<PageTarget | null>(null)

  useEffect(() => {
    api.listDocuments().then((data) => setDocs(data.items)).catch(() => setDocs([]))
  }, [])

  const tags = useMemo(
    () =>
      tagInput
        .split(/[,，]/)
        .map((tag) => tag.trim())
        .filter(Boolean),
    [tagInput],
  )

  const run = async (text?: string) => {
    const finalQuery = (text ?? query).trim()
    if (!finalQuery) {
      message.warning('请输入检索词')
      return
    }
    setLoading(true)
    try {
      const data = await api.search({
        query: finalQuery,
        mode,
        doc_ids: docIds,
        tags,
        top_k: 10,
      })
      setResult(data)
      setQuery(finalQuery)
    } catch (error) {
      message.error((error as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">混合检索</h1>
          <div className="page-desc">
            向量负责语义、全文负责术语，两路候选在 seekdb 内一次查出，再用 RRF 融合排序。
          </div>
        </div>
      </div>

      <Card className="soft-card" style={{ marginBottom: 20 }}>
        <Space direction="vertical" size={14} style={{ width: '100%' }}>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              size="large"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onPressEnter={() => void run()}
              placeholder="例如：长上下文推理时显存占用怎么降低？"
              prefix={<SearchOutlined />}
              allowClear
            />
            <Button size="large" type="primary" loading={loading} onClick={() => void run()}>
              检索
            </Button>
          </Space.Compact>
          <Row gutter={16} align="middle">
            <Col>
              <Segmented
                value={mode}
                onChange={(value) => setMode(value as 'hybrid' | 'fts' | 'vector')}
                options={[
                  { value: 'hybrid', label: '混合检索' },
                  { value: 'fts', label: '仅全文' },
                  { value: 'vector', label: '仅向量' },
                ]}
              />
            </Col>
            <Col flex="auto">
              <Space size={10} wrap>
                <Select
                  mode="multiple"
                  allowClear
                  value={docIds}
                  onChange={setDocIds}
                  placeholder="限定文献（默认全部）"
                  style={{ minWidth: 260 }}
                  maxTagCount={2}
                  options={docs.map((doc) => ({ value: doc.id, label: doc.title }))}
                />
                <Input
                  value={tagInput}
                  onChange={(event) => setTagInput(event.target.value)}
                  placeholder="标签，逗号分隔"
                  style={{ width: 180 }}
                />
              </Space>
            </Col>
          </Row>
        </Space>
      </Card>

      {loading ? (
        <div style={{ textAlign: 'center', padding: '60px 0' }}>
          <Spin size="large" />
        </div>
      ) : result ? (
        <>
          <Space style={{ marginBottom: 14 }} wrap>
            <Typography.Text strong>「{result.query}」</Typography.Text>
            <Typography.Text type="secondary">
              共 {result.hits.length} 条 · {result.latency_ms} ms
            </Typography.Text>
            {Object.entries(result.channel_counts).map(([channel, count]) => (
              <Tag key={channel} color={channel === 'fts' ? 'gold' : 'geekblue'}>
                {channel === 'fts' ? '全文' : '向量'} {count}
              </Tag>
            ))}
            <Tag>{`${result.timings.embed_ms} ms 嵌入 · ${result.timings.sql_ms} ms 检索`}</Tag>
            <Button size="small" type="text" icon={<ExperimentOutlined />} onClick={() => setEvidenceId(result.search_id)}>
              查看 seekdb 证据
            </Button>
          </Space>
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            {result.hits.length === 0 ? <Empty description="没有命中结果" /> : null}
            {result.hits.map((hit) => (
              <Card key={hit.chunk_id} className="hit-card" size="small">
                <Space wrap size={8}>
                  <Typography.Text strong>{hit.doc_title}</Typography.Text>
                  <Tag color="blue">p.{hit.page}</Tag>
                  {hit.section ? <Tag>{hit.section}</Tag> : null}
                  {Object.entries(hit.channels).map(([channel, info]) => (
                    <Tag key={channel} color={channel === 'fts' ? 'gold' : 'geekblue'}>
                      {channel === 'fts' ? '全文' : '向量'} #{info.rank}
                    </Tag>
                  ))}
                  <span className="muted">RRF {hit.score.toFixed(4)}</span>
                </Space>
                <div className="hit-text">
                  <HighlightText text={hit.text} ranges={hit.spans} />
                </div>
                <Space>
                  <Button
                    size="small"
                    icon={<FileTextOutlined />}
                    onClick={() =>
                      setTarget({
                        docId: hit.doc_id,
                        page: hit.page,
                        ranges: hit.spans.map(([start, end]) => [
                          hit.char_start + start,
                          hit.char_start + end,
                        ]),
                        title: hit.doc_title,
                      })
                    }
                  >
                    查看原文
                  </Button>
                  <span className="muted">点击结果中的出处可回到原文定位</span>
                </Space>
              </Card>
            ))}
          </Space>
        </>
      ) : (
        <Card className="soft-card">
          <Empty
            description={
              <span>
                试试问「B+ 树和哈希索引分别适合什么查询？」，或切换到「仅全文」看术语匹配的差异。
              </span>
            }
          />
        </Card>
      )}

      <PageDrawer
        open={Boolean(target)}
        onClose={() => setTarget(null)}
        docId={target?.docId ?? null}
        docTitle={target?.title}
        page={target?.page ?? 1}
        ranges={target?.ranges}
      />
      <EvidenceDrawer
        open={Boolean(evidenceId)}
        onClose={() => setEvidenceId(null)}
        searchId={evidenceId}
      />
    </>
  )
}
