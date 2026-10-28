import { CheckCircleTwoTone, CloseCircleTwoTone } from '@ant-design/icons'
import { Descriptions, Drawer, Skeleton, Space, Tabs, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api'
import type { DebugInfo } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  searchId: string | null
}

export default function EvidenceDrawer({ open, onClose, searchId }: Props) {
  const [data, setData] = useState<DebugInfo | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || !searchId) return
    setLoading(true)
    api
      .debug(searchId)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [open, searchId])

  return (
    <Drawer width={900} open={open} onClose={onClose} title="检索证据 · seekdb 实际执行记录">
      {loading ? (
        <Skeleton active paragraph={{ rows: 10 }} />
      ) : data ? (
        <>
          <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
            <Descriptions.Item label="检索模式">{data.mode}</Descriptions.Item>
            <Descriptions.Item label="返回候选">{data.rows}</Descriptions.Item>
            <Descriptions.Item label="全文索引" span={1}>
              {data.indexes.fulltext ? (
                <Space>
                  <CheckCircleTwoTone twoToneColor="#52c41a" /> TEXT RETRIEVAL SCAN
                </Space>
              ) : (
                <Space>
                  <CloseCircleTwoTone twoToneColor="#bfbfbf" /> 未使用
                </Space>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="向量索引">
              {data.indexes.vector ? (
                <Space>
                  <CheckCircleTwoTone twoToneColor="#52c41a" /> VECTOR INDEX SCAN
                </Space>
              ) : (
                <Space>
                  <CloseCircleTwoTone twoToneColor="#bfbfbf" /> 未使用
                </Space>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="通道候选" span={2}>
              <Space>
                {Object.entries(data.channel_counts).map(([channel, count]) => (
                  <Tag key={channel} color={channel === 'fts' ? 'gold' : 'geekblue'}>
                    {channel === 'fts' ? '全文' : '向量'} {count}
                  </Tag>
                ))}
              </Space>
            </Descriptions.Item>
          </Descriptions>
          <Tabs
            items={[
              {
                key: 'sql',
                label: '实际 SQL',
                children: <pre className="code-block">{data.sql}</pre>,
              },
              {
                key: 'explain',
                label: 'EXPLAIN',
                children: <pre className="code-block">{data.explain}</pre>,
              },
              {
                key: 'filters',
                label: '过滤条件',
                children: (
                  <Typography.Paragraph>
                    <pre className="code-block">{JSON.stringify(data.filters, null, 2)}</pre>
                  </Typography.Paragraph>
                ),
              },
            ]}
          />
        </>
      ) : (
        <Typography.Text type="secondary">暂无证据</Typography.Text>
      )}
    </Drawer>
  )
}
