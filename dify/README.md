# 记仇小镇（Grudge Town）

一个"会记住你恶作剧"的 NPC 互动叙事应用，使用 **Dify 1.10.1** 编排对话、
**seekdb 1.4.0** 承担数据存储与检索，配套一个本地记忆服务。

> 赛题：Dify × OceanBase「Just for Fun——用 Dify 做一个好玩的智能应用」
> 提交分支：`2026-competition-ecosystem`，资产目录：`dify/`

---

## 1. 作品介绍

小镇杂货店老板"老陈"记性极好。

你可以偷他的苹果、在他门口放鞭炮、到处造谣，也可以帮他搬货、写道歉信、送二锅头。
**你做的每件事，他都记着**：

- 下次进店，他会主动翻旧账："上次偷我苹果那事，我可没忘。"
- 你的行为会改变他的愤怒值与信任值，他的语气、称呼、报价随之变化。
- 关闭页面、隔天再来，他仍然记得——记忆存在 seekdb 里，不在浏览器里。
- 把他惹急了，他会记住每一笔账；真心道歉并送礼，则可能揭过去。

玩法是聊天式的：直接用自然语言和他说话，系统会识别你做了什么、写入记忆、
更新关系，再让老陈带着这些记忆回应你。

## 2. 技术架构

```
玩家（Dify Web 聊天界面）
   │
   ▼
Dify Chatflow「记仇小镇」
   ├─ HTTP 节点 ──► town-backend:8000
   │                   ├─ /memory/search   seekdb 向量检索历史事件
   │                   ├─ /relation/get    读取愤怒值 / 信任值
   │                   ├─ /npc/profile     读取人设
   │                   ├─ /memory/write    写入本次事件（含 512 维向量）+ 更新关系
   │                   └─ /v1/embeddings   本地 bge-small-zh-v1.5
   ├─ LLM 节点（DeepSeek deepseek-flash）注入人设 + 记忆 + 关系，生成回复
   ├─ Code 节点解析模型输出（reply / event_type / memory）
   └─ Answer 节点输出老陈的回复
```

### 2.1 Dify 的作用

- 承担核心流程编排：输入、HTTP 调用、LLM 生成、代码解析、结果输出。
- 提交完整 DSL（`dsl/grudge-town.yml`），导入即可还原应用。

### 2.2 seekdb 的作用（三重角色）

| 角色 | 说明 |
| --- | --- |
| Dify 元数据库 | `DB_TYPE=mysql`，Dify 的用户、应用、会话、工作流数据全部落在 seekdb |
| Dify 向量库 | `VECTOR_STORE=oceanbase`，知识库向量与全文索引由 seekdb 承担 |
| 应用记忆库 | 独立 `town` 库：`npc_profile`、`event_memory`（VECTOR(512) + HNSW）、`relation` |

版本：**seekdb 1.4.0.0**（镜像 `oceanbase/seekdb:latest`，2026-10 实测）。

### 2.3 本地记忆服务（town-backend）

- FastAPI 服务，提供 OpenAI 兼容的 `/v1/embeddings`（Dify 知识库也用它）。
- Embedding 模型：**BAAI/bge-small-zh-v1.5**（512 维，约 92MB）。
  模型文件不随仓库提交，按第 3.1 节下载。
- 记忆检索：`l2_distance(embedding, query)` + 结构化过滤（player_id / npc_id）。

## 3. 部署步骤

### 3.1 前置条件

- Docker Desktop（Linux 容器），建议内存 ≥ 8GB；
- DeepSeek API Key（用于 LLM 节点）；
- 可访问 Docker Hub、Dify Marketplace、Hugging Face（或使用镜像站）。

下载 Embedding 模型到 `data/models/bge-small-zh-v1.5/`：

```bash
pip install huggingface_hub
python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='BAAI/bge-small-zh-v1.5', local_dir='data/models/bge-small-zh-v1.5', allow_patterns=['*.json','*.safetensors','*.txt','*.model'])"
```

目录下应有 `model.safetensors`、`config.json`、`tokenizer.json` 等文件（约 92MB）。
国内网络可设置 `HF_ENDPOINT=https://hf-mirror.com`。

### 3.2 启动服务

```bash
cd docker
cp .env.example .env
docker compose up -d
```

启动的服务：

| 容器 | 作用 |
| --- | --- |
| seekdb | 元数据库 + 向量库（宿主端口 2882，容器内 2881） |
| dify-api / worker / worker_beat | Dify 后端（首次启动自动执行数据库迁移） |
| dify-web / dify-nginx | Web 界面（宿主端口 **8081**） |
| dify-plugin_daemon | 插件运行时 |
| dify-sandbox | Code 节点沙箱 |
| dify-redis / dify-ssrf_proxy | 缓存 / 网络代理 |
| town-backend | 本地记忆服务 + Embedding |

等待 `dify-api` 日志出现 `Database migration successful!`，然后访问
<http://127.0.0.1:8081>。

> 首次启动若 nginx 先于 api 就绪，可能出现 502；执行
> `docker compose restart nginx` 即可。

### 3.3 初始化管理员

首次访问会进入安装页，设置管理员邮箱和密码（`.env` 中 `INIT_PASSWORD` 仅用于初始化校验）。
本文档的验证使用：

- 邮箱：`admin@dify.ai`
- 密码：`Dify@2026`（请自行修改）

也可以通过 API 初始化：

```bash
curl -X POST http://127.0.0.1:8081/console/api/init -H "Content-Type: application/json" -d '{"password":"Dify@2026"}'
curl -X POST http://127.0.0.1:8081/console/api/setup -H "Content-Type: application/json" -d '{"email":"admin@dify.ai","name":"admin","password":"Dify@2026"}'
```

### 3.4 安装模型插件

在 Dify 界面「插件」页安装：

- `langgenius/openai_api_compatible`（0.0.73，用于本地 Embedding）
- `langgenius/deepseek`（0.0.26，用于 LLM）

导入 DSL 时 Dify 也会提示安装缺失依赖，按提示安装即可。

### 3.5 配置模型

**DeepSeek（LLM）**

设置 → 模型供应商 → DeepSeek → 填入 API Key。
可用模型：`deepseek-flash`（默认）、`deepseek-v4-pro`。

**本地 Embedding**

设置 → 模型供应商 → OpenAI-API-compatible → 添加模型：

| 项 | 值 |
| --- | --- |
| 模型类型 | Text Embedding |
| 模型名称 | `bge-small-zh-v1.5` |
| API Key | 任意非空字符串（如 `local`） |
| API endpoint URL | `http://town-backend:8000/v1` |
| context_size | 512 |
| max_chunks | 512 |

保存时 Dify 会调用 `http://town-backend:8000/v1/embeddings` 验证，
成功即说明 Embedding 链路可用。

### 3.6 导入应用 DSL

应用 → 导入 DSL → 选择 `dsl/grudge-town.yml`。

导入后检查工作流，应有 8 个节点：
`开始 → 检索记忆 → 读取关系 → 读取人设 → 老陈回应 → 解析回复 → 写入记忆 → 回答`。

### 3.7 体验

打开应用「记仇小镇」，直接聊天即可。建议按第 4 节的三组样例体验。

## 4. 验证样例

### 4.1 初始状态

- 重新部署时 seekdb 中 `town` 库为空（首次启动自动建表并写入老陈人设）；
- 若想重置演示数据，执行：

```bash
docker exec seekdb mysql -h127.0.0.1 -P2881 -uroot -pdifyai123456 town -e "DELETE FROM event_memory; UPDATE relation SET anger=0, trust=50;"
```

### 4.2 三组样例

| # | 输入 | 预期表现 | 实际结果（2026-10-28） |
| --- | --- | --- | --- |
| 1 | 老板，我偷你一个苹果尝尝 | 老陈拒绝并记下这笔账 | "哟，偷字说得这么响亮……我这账本可是记了三十年了！" |
| 2 | 我又来了，今天有什么新鲜事 | 主动翻旧账，提到上次偷苹果 | "上次放话要偷我苹果那事，我可没忘。" |
| 3 | 上次偷苹果是我不对，这瓶二锅头给你赔罪 | 语气缓和，接受道歉 | "二锅头都拎来了？算你还有点良心……这回就揭过去了。" |

运行脚本可复现并保存证据：

```bash
python verify/run_demo.py
```

输出 `evidence/demo-run.json`，其中每轮包含：

- `answer`：老陈的回复；
- `memory_search_body`：seekdb 向量检索返回的记忆（含 distance）；
- `nodes`：8 个节点的执行状态与输出。

### 4.3 seekdb 侧核对

```bash
docker exec seekdb mysql -h127.0.0.1 -P2881 -uroot -pdifyai123456 -e \
  "USE town; SELECT id,event_type,content FROM event_memory; SELECT anger,trust,last_event FROM relation;"
```

实测：3 轮对话后 `event_memory` 有 7 条记录，`relation` 中 `player-1`
的愤怒值随恶作剧上升、随道歉下降（18 → 11），`last_event` 为最后一次事件描述。

## 5. 目录结构

```
dify/
├── README.md
├── backend/                  # 本地记忆服务
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
├── docker/                   # Dify 1.10.1 官方部署文件 + 本地配置
│   ├── docker-compose.yaml
│   ├── docker-compose.override.yaml
│   ├── .env                  # 已按 seekdb 配置（无真实密钥）
│   └── ...
├── data/models/              # 模型目录（不提交）
├── dsl/
│   ├── build_dsl.py          # 生成 DSL 的脚本
│   └── grudge-town.yml       # 应用 DSL
├── verify/run_demo.py        # 验证脚本
├── evidence/demo-run.json    # 验证证据
└── .secrets/                 # 本地密钥（不提交）
```

## 6. 已知限制

- 当前为单用户演示：`player_id` 固定为 `player-1`，多人使用需在 DSL 中改为输入变量。
- 记忆检索只按 `player_id + npc_id` 过滤，未启用 seekdb 全文检索（Dify 知识库侧已启用）。
- DeepSeek 模型输出带 `<think>` 推理块，Code 节点已做剥离；若更换模型需保留该逻辑。
- 首次安装插件时 Dify 会为插件创建 Python 环境，耗时可能 10 分钟以上，属正常现象。
- 本机校园网 DNS 无法解析 `api.deepseek.com`，`docker-compose.override.yaml` 中已为 Dify
  服务统一配置公共 DNS（223.5.5.5 / 114.114.114.114）；其他网络环境可按需调整。
