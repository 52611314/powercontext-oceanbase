import { ExportOutlined, LeftOutlined, RightOutlined } from '@ant-design/icons'
import { Button, Drawer, Empty, Skeleton, Space, Tag, Typography } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { PageText } from '../types'
import HighlightText from './HighlightText'

interface Props {
  open: boolean
  onClose: () => void
  docId: string | null
  docTitle?: string
  page: number
  ranges?: Array<[number, number]>
}

export default function PageDrawer({ open, onClose, docId, docTitle, page, ranges = [] }: Props) {
  const [current, setCurrent] = useState(page)
  const [data, setData] = useState<PageText | null>(null)
  const [loading, setLoading] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setCurrent(page)
  }, [page, docId, open])

  useEffect(() => {
    if (!open || !docId) return
    setLoading(true)
    api
      .getPage(docId, current)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [open, docId, current])

  const activeRanges = useMemo(() => (current === page ? ranges : []), [current, page, ranges])

  useEffect(() => {
    if (loading || !data) return
    const container = containerRef.current
    const mark = container?.querySelector('mark') as HTMLElement | null
    if (!container || !mark) return
    const markRect = mark.getBoundingClientRect()
    const containerRect = container.getBoundingClientRect()
    container.scrollTop += markRect.top - containerRect.top - container.clientHeight / 2
  }, [loading, data, activeRanges])

  return (
    <Drawer
      width={760}
      open={open}
      onClose={onClose}
      title={
        <Space size={10}>
          <Typography.Text strong>{docTitle ?? '原文'}</Typography.Text>
          <Tag color="blue">第 {current} 页</Tag>
          {data?.section ? <Tag>{data.section}</Tag> : null}
        </Space>
      }
      extra={
        docId ? (
          <Button
            size="small"
            icon={<ExportOutlined />}
            href={`/api/documents/${docId}/file#page=${current}`}
            target="_blank"
          >
            打开原始 PDF
          </Button>
        ) : null
      }
    >
      <Space style={{ marginBottom: 14 }}>
        <Button
          size="small"
          icon={<LeftOutlined />}
          disabled={current <= 1}
          onClick={() => setCurrent((value) => Math.max(1, value - 1))}
        >
          上一页
        </Button>
        <Button size="small" icon={<RightOutlined />} onClick={() => setCurrent((value) => value + 1)}>
          下一页
        </Button>
      </Space>
      {loading ? (
        <Skeleton active paragraph={{ rows: 12 }} />
      ) : data ? (
        <div className="page-text" ref={containerRef}>
          <HighlightText text={data.text} ranges={activeRanges} />
        </div>
      ) : (
        <Empty description="无法读取该页" />
      )}
    </Drawer>
  )
}
