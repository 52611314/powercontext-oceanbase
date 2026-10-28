import {
  DeleteOutlined,
  DownloadOutlined,
  ExperimentOutlined,
  HistoryOutlined,
  SendOutlined,
} from '@ant-design/icons'
import {
  App as AntApp,
  Button,
  Card,
  Col,
  Empty,
  Input,
  Popconfirm,
  Row,
  Space,
  Tag,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import EvidenceDrawer from '../components/EvidenceDrawer'
import PageDrawer from '../components/PageDrawer'
import type { AnswerSummary, Bullet } from '../types'

interface AnswerView {
  answer_id: string
  question: string
  bullets: Bullet[]
  search_id: string
  latency_ms: number
  channel_counts?: Record<string, number>
}

interface PageTarget {
  docId: string
  page: number
  ranges: Array<[number, number]>
  title: string
}

export default function AskPage() {
  const { message } = AntApp.useApp()
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<AnswerView | null>(null)
  const [loading, setLoading] = useState(false)
  const [history, setHistory] = useState<AnswerSummary[]>([])
  const [evidenceId, setEvidenceId] = useState<string | null>(null)
  const [target, setTarget] = useState<PageTarget | null>(null)

  const loadHistory = useCallback(async () => {
    try {
      const data = await api.listAnswers(20)
      setHistory(data.items)
    } catch {
      /* history is non-critical */
    }
  }, [])

  useEffect(() => {
    void loadHistory()
  }, [loadHistory])

  const run = async () => {
    if (!question.trim()) {
      message.warning('请输入问题')
      return
    }
    setLoading(true)
    try {
      const result = await api.ask({ question: question.trim() })
      setAnswer({
        answer_id: result.answer_id,
        question: result.question,
        bullets: result.bullets,
        search_id: result.search_id,
        latency_ms: result.latency_ms,
        channel_counts: result.channel_counts,
      })
      await loadHistory()
    } catch (error) {
      message.error((error as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const openHistory = async (id: string) => {
    try {
      const record = await api.getAnswer(id)
      setAnswer({
        answer_id: record.id,
        question: record.question,
        bullets: record.bullets,
        search_id: record.search_id,
        latency_ms: record.latency_ms,
      })
      setQuestion(record.question)
    } catch (error) {
      message.error((error as Error).message)
    }
  }

  const removeHistory = async (id: string) => {
    try {
      await api.deleteAnswer(id)
      if (answer?.answer_id === id) setAnswer(null)
      await loadHistory()
    } catch (error) {
      message.error((error as Error).message)
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">带引用问答</h1>
          <div className="page-desc">
            先检索、再回答：每条结论都来自检索到的原文句子，点击出处可以跳回那一页。
          </div>
        </div>
      </div>

      <Row gutter={20}>
        <Col span={17}>
          <Card className="soft-card">
            <Input.TextArea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              autoSize={{ minRows: 2, maxRows: 5 }}
              placeholder="例如：处理长上下文时，这几份资料分别提到了哪些显存优化手段？"
            />
            <Space style={{ marginTop: 12 }} size={12}>
              <Button type="primary" icon={<SendOutlined />} loading={loading} onClick={() => void run()}>
                提问
              </Button>
              <span className="muted">默认使用混合检索，取 8 个证据块；不使用大模型，答案完全可溯源。</span>
            </Space>
          </Card>

          {answer ? (
            <Card className="soft-card" style={{ marginTop: 20 }}>
              <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
                <div>
                  <h2 className="answer-question">{answer.question}</h2>
                  <Space wrap size={8}>
                    <Tag color="green">{answer.latency_ms} ms</Tag>
                    {answer.channel_counts
                      ? Object.entries(answer.channel_counts).map(([channel, count]) => (
                          <Tag key={channel} color={channel === 'fts' ? 'gold' : 'geekblue'}>
                            {channel === 'fts' ? '全文' : '向量'} {count}
                          </Tag>
                        ))
                      : null}
                    <span className="muted">answer {answer.answer_id.slice(0, 8)}</span>
                  </Space>
                </div>
                <Space>
                  <Button
                    size="small"
                    icon={<DownloadOutlined />}
                    onClick={() => window.open(`/api/answers/${answer.answer_id}/export.md`, '_blank')}
                  >
                    导出 Markdown
                  </Button>
                  <Button
                    size="small"
                    type="text"
                    icon={<ExperimentOutlined />}
                    onClick={() => setEvidenceId(answer.search_id)}
                  >
                    检索证据
                  </Button>
                </Space>
              </Space>

              <div style={{ marginTop: 8 }}>
                {answer.bullets.length === 0 ? (
                  <Empty description="没有找到足够证据，换一种问法试试" />
                ) : (
                  answer.bullets.map((bullet, index) => (
                    <div className="answer-bullet" key={`${bullet.citation.chunk_id}-${index}`}>
                      <div className="bullet-index">{index + 1}</div>
                      <div>
                        <div className="bullet-text">{bullet.text}</div>
                        <Space wrap size={6} className="citation-chip">
                          <Button
                            size="small"
                            type="link"
                            style={{ paddingInline: 0 }}
                            onClick={() =>
                              setTarget({
                                docId: bullet.citation.doc_id,
                                page: bullet.citation.page,
                                ranges: [[bullet.citation.char_start, bullet.citation.char_end]],
                                title: bullet.citation.doc_title,
                              })
                            }
                          >
                            《{bullet.citation.doc_title}》 第 {bullet.citation.page} 页
                          </Button>
                          {bullet.citation.section ? <Tag>{bullet.citation.section}</Tag> : null}
                        </Space>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </Card>
          ) : null}
        </Col>

        <Col span={7}>
          <Card
            className="soft-card"
            title={
              <Space>
                <HistoryOutlined />
                问答历史
              </Space>
            }
            extra={
              <Button size="small" type="text" onClick={() => void loadHistory()}>
                刷新
              </Button>
            }
          >
            {history.length === 0 ? (
              <Typography.Text type="secondary">还没有提过问题</Typography.Text>
            ) : (
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                {history.map((item) => (
                  <div className="history-item" key={item.id} onClick={() => void openHistory(item.id)}>
                    <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
                      <div>
                        <div className="history-question">{item.question}</div>
                        <div className="muted" style={{ marginTop: 4 }}>
                          {item.created_at} · {item.latency_ms} ms
                        </div>
                      </div>
                      <Popconfirm
                        title="删除这条问答？"
                        onConfirm={(event) => {
                          event?.stopPropagation()
                          void removeHistory(item.id)
                        }}
                        onCancel={(event) => event?.stopPropagation()}
                      >
                        <Button
                          size="small"
                          type="text"
                          danger
                          icon={<DeleteOutlined />}
                          onClick={(event) => event.stopPropagation()}
                        />
                      </Popconfirm>
                    </Space>
                  </div>
                ))}
              </Space>
            )}
          </Card>
        </Col>
      </Row>

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
