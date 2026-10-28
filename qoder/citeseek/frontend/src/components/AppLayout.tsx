import { DatabaseOutlined, FileSearchOutlined, ReadOutlined, SearchOutlined } from '@ant-design/icons'
import { Badge, Layout, Menu } from 'antd'
import { useEffect, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api'

const { Sider, Content } = Layout

const items = [
  { key: '/library', icon: <ReadOutlined />, label: '文献库' },
  { key: '/search', icon: <SearchOutlined />, label: '混合检索' },
  { key: '/ask', icon: <FileSearchOutlined />, label: '带引用问答' },
]

export default function AppLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const [ready, setReady] = useState<boolean | null>(null)

  useEffect(() => {
    api.ready().then(() => setReady(true)).catch(() => setReady(false))
  }, [])

  const selected = items.find((item) => location.pathname.startsWith(item.key))?.key ?? '/library'

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={228} theme="light" className="app-sider">
        <div className="brand">
          <div className="brand-mark">
            <DatabaseOutlined />
          </div>
          <div>
            <div className="brand-name">CiteSeek</div>
            <div className="brand-sub">文献与资料检索工作台</div>
          </div>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          items={items}
          onClick={({ key }) => navigate(key)}
        />
        <div className="sider-footer">
          <Badge status={ready === null ? 'processing' : ready ? 'success' : 'error'} />
          {ready === null ? '连接中…' : ready ? 'seekdb 已连接 · 混合检索就绪' : 'seekdb 未连接'}
        </div>
      </Sider>
      <Layout>
        <Content className="app-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
