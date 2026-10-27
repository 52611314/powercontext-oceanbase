import {
  ArrowLeftOutlined,
  ExportOutlined,
  FilePdfOutlined,
  LeftOutlined,
  ReloadOutlined,
  RightOutlined,
} from '@ant-design/icons'
import { App as AntApp, Button, Card, Descriptions, Empty, Select, Space, Spin, Tag, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import HighlightText from '../components/HighlightText'
import type { DocumentItem, PageText } from '../types'

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { message } = AntApp.useApp()
  const [doc, setDoc] = useState<DocumentItem | null>(null)
  const [pageData, setPageData] = useState<PageText | null>(null)
  const [loading, setLoading] = useState(true)

  const page = Math.max(1, Number(searchParams.get('page') || '1'))

  useEffect(() => {
    if (!id) return
    api
      .getDocument(id)
      .then(setDoc)
      .catch((error) => {
        message.error((error as Error).message)
        setDoc(null)
      })
  }, [id, message])

  useEffect(() => {
    if (!id) return
    setLoading(true)
    api
      .getPage(id, page)
      .then(setPageData)
      .catch(() => setPageData(null))
      .finally(() => setLoading(false))
  }, [id, page])

  const ranges = useMemo<Array<[number, number]>>(() => {
    const start = Number(searchParams.get('start'))
    const end = Number(searchParams.get('end'))
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) return [[start, end]]
    return []
  }, [searchParams])

  const goPage = (next: number) => {
    if (!id) return
    const max = doc?.page_count ?? next
    const bounded = Math.min(Math.max(1, next), Math.max(1, max))
    setSearchParams({ page: String(bounded) })
  }

  if (!doc) {
    return (
      <Card className="soft-card">
        {loading ? <Spin /> : <Empty description="文献不存在或已删除" />}
      </Card>
    )
  }

  return (
    <>
      <div className="page-head">
        <Space size={14} align="start">
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/library')} />
          <div>
            <h1 className="page-title">{doc.title}</h1>
            <div className="page-desc">
              {doc.authors || '未知作者'}
              {doc.year ? ` · ${doc.year}` : ''} · {doc.page_count} 页 · {doc.chunk_count} 个文本块
            </div>
          </div>
        </Space>
        <Space wrap>
          <Button
            icon={<FilePdfOutlined />}
            href={`/api/documents/${doc.id}/file#page=${page}`}
            target="_blank"
          >
            打开原始文件
          </Button>
          <Button
            icon={<ExportOutlined />}
            href={`/api/documents/${doc.id}/citation.bib`}
            target="_blank"
          >
            导出 BibTeX
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={async () => {
              await api.reindex(doc.id)
              message.info('已重新加入索引队列')
            }}
          >
            重建索引
          </Button>
        </Space>
      </div>

      <Card className="soft-card" style={{ marginBottom: 18 }}>
        <Descriptions size="small" column={4}>
          <Descriptions.Item label="类型">{doc.source_type.toUpperCase()}</Descriptions.Item>
          <Descriptions.Item label="文件">{doc.file_name}</Descriptions.Item>
          <Descriptions.Item label="索引状态">
            <Tag color={doc.status === 'ready' ? 'green' : doc.status === 'failed' ? 'red' : 'blue'}>
              {doc.status}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="标签">
            {doc.tags.length ? doc.tags.map((tag) => <Tag key={tag}>{tag}</Tag>) : '—'}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card className="soft-card">
        <Space style={{ marginBottom: 14 }} wrap>
          <Button size="small" icon={<LeftOutlined />} disabled={page <= 1} onClick={() => goPage(page - 1)}>
            上一页
          </Button>
          <Select
            size="small"
            value={page}
            style={{ width: 130 }}
            onChange={goPage}
            options={Array.from({ length: doc.page_count }, (_, index) => ({
              value: index + 1,
              label: `第 ${index + 1} 页`,
            }))}
          />
          <Button
            size="small"
            icon={<RightOutlined />}
            disabled={page >= doc.page_count}
            onClick={() => goPage(page + 1)}
          >
            下一页
          </Button>
          {pageData?.section ? <Tag>{pageData.section}</Tag> : null}
          {ranges.length ? <Typography.Text type="secondary">已高亮引用位置</Typography.Text> : null}
        </Space>
        {loading ? (
          <Spin />
        ) : pageData ? (
          <div className="page-text">
            <HighlightText text={pageData.text} ranges={ranges} />
          </div>
        ) : (
          <Empty description="无法读取该页" />
        )}
      </Card>
    </>
  )
}
