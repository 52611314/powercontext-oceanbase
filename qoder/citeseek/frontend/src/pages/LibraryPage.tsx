import { DeleteOutlined, InboxOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import {
  App as AntApp,
  Button,
  Card,
  Col,
  Empty,
  Input,
  Popconfirm,
  Progress,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import type { DocumentItem, Stats } from '../types'

const { Dragger } = Upload

function StatusCell({ doc }: { doc: DocumentItem }) {
  if (doc.status === 'ready') return <Tag color="green">已索引</Tag>
  if (doc.status === 'failed') {
    return (
      <Tooltip title={doc.error || '索引失败'}>
        <Tag color="red">失败</Tag>
      </Tooltip>
    )
  }
  return (
    <div style={{ minWidth: 92 }}>
      <Tag color="blue">{doc.status === 'indexing' ? '索引中' : '等待中'}</Tag>
      <Progress percent={doc.progress} size="small" showInfo={false} />
    </div>
  )
}

export default function LibraryPage() {
  const { message } = AntApp.useApp()
  const [docs, setDocs] = useState<DocumentItem[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [tags, setTags] = useState('')
  const [keyword, setKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [documents, summary] = await Promise.all([api.listDocuments(), api.stats()])
      setDocs(documents.items)
      setStats(summary)
    } catch (error) {
      message.error((error as Error).message)
    } finally {
      setLoading(false)
    }
  }, [message])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    const indexing = docs.some((doc) => doc.status === 'pending' || doc.status === 'indexing')
    if (!indexing) return
    const timer = setInterval(() => {
      void load()
    }, 1800)
    return () => clearInterval(timer)
  }, [docs, load])

  const handleFile = async (file: File) => {
    setUploading(true)
    try {
      const result = await api.upload([file], tags)
      const item = result.items[0]
      if (item?.duplicate) message.info(`${file.name} 已存在，未重复导入`)
      else if (item?.error) message.error(`${file.name}：${item.error}`)
      else message.success(`${file.name} 已加入索引队列`)
      await load()
    } catch (error) {
      message.error((error as Error).message)
    } finally {
      setUploading(false)
    }
  }

  const updateReadStatus = async (doc: DocumentItem, value: string) => {
    try {
      await api.patchDocument(doc.id, { read_status: value })
      await load()
    } catch (error) {
      message.error((error as Error).message)
    }
  }

  const filtered = useMemo(
    () =>
      docs.filter((doc) => {
        if (statusFilter && doc.status !== statusFilter) return false
        if (!keyword.trim()) return true
        const text = `${doc.title} ${doc.authors} ${doc.tags.join(' ')}`.toLowerCase()
        return text.includes(keyword.trim().toLowerCase())
      }),
    [docs, keyword, statusFilter],
  )

  const columns: ColumnsType<DocumentItem> = [
    {
      title: '标题',
      dataIndex: 'title',
      render: (_, doc) => (
        <Space direction="vertical" size={2}>
          <Link to={`/documents/${doc.id}?page=1`}>{doc.title}</Link>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {doc.authors || doc.file_name}
            {doc.year ? ` · ${doc.year}` : ''}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '标签',
      dataIndex: 'tags',
      width: 110,
      render: (value: string[]) =>
        value.length ? value.map((tag) => <Tag key={tag}>{tag}</Tag>) : <span className="muted">—</span>,
    },
    {
      title: '规模',
      width: 72,
      render: (_, doc) => (
        <span className="muted">
          {doc.page_count} 页 · {doc.chunk_count} 块
        </span>
      ),
    },
    { title: '索引', dataIndex: 'status', width: 88, render: (_, doc) => <StatusCell doc={doc} /> },
    {
      title: '阅读',
      width: 76,
      render: (_, doc) => (
        <Select
          size="small"
          value={doc.read_status}
          style={{ width: 84 }}
          onChange={(value) => void updateReadStatus(doc, value)}
          options={[
            { value: 'unread', label: '未读' },
            { value: 'reading', label: '在读' },
            { value: 'read', label: '已读' },
          ]}
        />
      ),
    },
    {
      title: '导入时间',
      dataIndex: 'created_at',
      width: 138,
      responsive: ['xl'],
      render: (value: string) => <span className="muted">{value}</span>,
    },
    {
      title: '操作',
      width: 68,
      render: (_, doc) => (
        <Space size={2}>
          <Button
            size="small"
            type="text"
            icon={<ReloadOutlined />}
            onClick={async () => {
              await api.reindex(doc.id)
              message.info('已重新加入索引队列')
              await load()
            }}
          />
          <Popconfirm
            title="删除该文献？"
            description="将同时删除已建立的索引，不可恢复。"
            onConfirm={async () => {
              await api.deleteDocument(doc.id)
              message.success('已删除')
              await load()
            }}
          >
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">文献库</h1>
          <div className="page-desc">导入论文、课程笔记或实验记录，系统会自动解析、切块并建立混合索引。</div>
        </div>
        <Button onClick={() => void load()} loading={loading}>
          刷新
        </Button>
      </div>

      <Row gutter={16} style={{ marginBottom: 20 }}>
        {[
          ['文献', stats?.documents ?? 0],
          ['总页数', stats?.pages ?? 0],
          ['文本块', stats?.chunks ?? 0],
          ['索引中', stats?.indexing ?? 0],
        ].map(([label, value]) => (
          <Col span={6} key={label}>
            <Card className="stat-card" size="small">
              <div className="stat-value">{value}</div>
              <div className="stat-label">{label}</div>
            </Card>
          </Col>
        ))}
      </Row>

      <Card className="soft-card upload-card" style={{ marginBottom: 20 }}>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Space style={{ width: '100%' }} size={12}>
            <span className="muted">导入标签</span>
            <Input
              value={tags}
              onChange={(event) => setTags(event.target.value)}
              placeholder="例如：课程笔记, 大模型（可留空）"
              style={{ width: 360 }}
            />
          </Space>
          <Dragger
            multiple
            showUploadList={false}
            disabled={uploading}
            accept=".pdf,.md,.markdown,.txt"
            beforeUpload={(file) => {
              void handleFile(file as File)
              return Upload.LIST_IGNORE
            }}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽文件到此处上传</p>
            <p className="ant-upload-hint">支持 PDF / Markdown / TXT，单文件 ≤ 100MB；相同文件会自动去重</p>
          </Dragger>
        </Space>
      </Card>

      <Card className="soft-card">
        <Space style={{ marginBottom: 14 }} size={10}>
          <Input
            prefix={<SearchOutlined />}
            placeholder="按标题、作者或标签过滤"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            style={{ width: 300 }}
            allowClear
          />
          <Select
            value={statusFilter}
            onChange={setStatusFilter}
            style={{ width: 140 }}
            options={[
              { value: '', label: '全部状态' },
              { value: 'ready', label: '已索引' },
              { value: 'indexing', label: '索引中' },
              { value: 'failed', label: '失败' },
            ]}
          />
          <span className="muted">共 {filtered.length} 篇</span>
        </Space>
        <Table
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={filtered}
          loading={loading}
          pagination={{ pageSize: 8, showSizeChanger: false }}
          locale={{ emptyText: <Empty description="还没有文献，先上传几份资料吧" /> }}
        />
      </Card>
    </>
  )
}
