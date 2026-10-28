# 规有所依：MiniMax SDD 工程上下文

根据任务从 seekdb 选择当前有效规格、代码与测试，使用 PowerContext 保存和复用状态，再由 MiniMax 修改业务代码并验证兼容行为。本目录是可接到已有 AI 开发助手的独立 CLI。

**本地严格验收六项全部通过**，详见 [evidence/summary.json](evidence/summary.json)。当前 sample/orders.py 为真实 MiniMax-M3.1-Flash 生成的代码。修改未提交、未推送；最终评分以赛事平台实际评测为准。

## 环境与初始化

Linux / WSL2；实测 Python 3.12.3、Docker Compose v2、seekdb 1.4.0.0、PowerContext 1.4.1、PyMySQL 1.2.0。完整实测依赖固定在 requirements.lock.txt。数据库使用 MySQL wire protocol，模型使用标准库 urllib.request 调用国内原生 HTTP JSON，无额外模型 SDK。至少 2GB 可用内存。

从仓库根目录执行：

```bash
cd minimax
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock.txt
cp .env.example .env
# 按实际环境填写 DB_*；复核已交付模型证据无需真实模型密钥。
docker compose up -d
# 等待数据库就绪后执行：
python context.py import
python develop.py
```

已有 seekdb/OceanBase 时直接配置 DB_*，不必另启容器。Compose 固定实测镜像摘要，卷挂载 /var/lib/seekdb。默认本地空密码，可在 .env 设置 DB_PASSWORD。启动可能需要几分钟；连接失败时检查 docker compose logs seekdb，ready 后重试 import。DB_USER 需创建本项目数据库及建表权限；验收还需创建、删除随机 minimax_verify_* 数据库。

**python develop.py 不调用模型**：复核交付的真实模型证据，执行严格验收，将实际验证写入 PowerContext，再以独立进程恢复；退出码应为 0。无需开发者遗留的 .runtime、虚拟环境或数据库状态。

## 重新执行模型开发

评审无需重新生成完全相同补丁。如需实际重新生成，配置 .env：

```dotenv
MINIMAX_API_KEY=replace-with-your-own-key
MINIMAX_BASE_URL=https://api.minimaxi.com/v1
MINIMAX_CHAT_PATH=/text/chatcompletion_v2
MINIMAX_MODEL=MiniMax-M3.1-Flash
```

```bash
python develop.py --generate
```

该命令备份当前业务文件到 .runtime/pre-model-orders.py，恢复 before 快照，导入资料，运行修改前测试，将失败结果和待办保存到 PowerContext，再提供真实检索与恢复的上下文给 MiniMax。应用补丁后完成严格验收、保存和恢复状态。会消耗模型额度并更新 evidence；需要保留旧证据时先备份该目录。

本次实际采用国内原生 ChatCompletion V2，max_tokens=4096、reasoning_split=true。只保存最终答案与公共元信息，不保存私有思维链。兼容路径可通过 MINIMAX_CHAT_PATH 指定，但本次 /chat/completions 返回 404；旧 M2.5 返回已下线错误，故使用 M3.1 Flash。实际调用方式以 response.json 为准。

## 输入输出与入口

```bash
python context.py context --task-id orders-status \
  --task "orders list_orders status filter customer regression" --out .runtime/context.json
python context.py save --task-id orders-status --file .runtime/state.json
python acceptance.py --require-model
python -m unittest discover -s sample -p test_orders.py -v
```

| 入口 | 输入和行为 | 输出 |
| --- | --- | --- |
| context.py import | 导入真实规格、代码、测试，事务替换索引 | 数量、规格版本 |
| context.py context | 任务 ID 与文本，检索和依赖扩展 | 有效来源、全文、SHA256、历史状态、validation_current、next_action |
| context.py save | 任务 ID 与 JSON：done、todo、validation、fingerprint | Atomic Memory 写入结果 |
| agent.py | 当前上下文与 MiniMax 配置 | 请求、结果、前后快照、差异；仅修改 orders.py |
| acceptance.py --require-model | 固定测试和交付模型证据 | 六项验收日志；缺少模型证据时非零退出 |
| develop.py [--generate] | 完整工作流；加参数才调用模型 | 严格验收、任务状态保存、新进程恢复 |

状态文件示例：{"done":["reviewed specs"],"todo":["implement"],"validation":{},"fingerprint":"复制当前上下文的64位指纹"}。

每次 CLI 都是独立进程。.runtime/powercontext.db 是官方 SDK 的持久化数据库，状态通过 Atomic Memory API 写入和分页读取。只有当前规格、代码、测试的组合指纹与成功验证一致，validation_current 才为 true。未重新导入的旧索引会被拒绝，规格变更会使历史验证失效。

## 材料与验证

参阅 [验证样例](EXAMPLE.md)、[一页技术说明](TECHNICAL.md)、[技术使用记录](evidence/README.md)。

- sample/spec.json：ORD-001、ORD-002 有效，ORD-OLD 已失效。
- sample/orders.before.py 与 sample/orders.py：修改前快照及真实模型最终实现。
- sample/test_orders.py：固定 3 个目标方法、4 个相关回归方法。修改前原有回归通过、目标断言失败；修改后 7 个方法全部通过。
- evidence/minimax/：请求、最终答案、模型 ID/usage/耗时、前后源码、diff、哈希溯源。
- evidence/*tests.json：命令、退出码、Git 基线与代码/测试 SHA256。
- evidence/technology-events.jsonl：隔离验收的实际 SQL 与 PowerContext 操作。
- evidence/task-events.jsonl、task-resumed.json：同一次真实开发的状态写入和新进程使用。
- evidence/context-updated.json：规格更新后读取 revision 2，历史验证失效。
- evidence/workflow.json：最后完整验收、保存与恢复的各命令及退出码。
- evidence/development.json：真实开发经过、失败原因及成功结果。

集成验收使用临时工程、随机数据库、全新 PowerContext 状态，以多个进程检验来源、状态复用和规格变化；仅清理该随机测试数据库，不删除正式库或正式状态。固定测试在修改前后完全一致。

## 重置与限制

恢复未实现版本：cp sample/orders.before.py sample/orders.py；恢复模型交付版本：cp evidence/minimax/after.py sample/orders.py；之后均运行 import。
重置任务：退出 CLI 后备份并重命名 .runtime；新运行从空状态开始。重置索引：import 仅替换本项目 engineering_docs。
刷新规格：修改 spec.json 的 revision/items，再 import/context；自动验收在临时副本演示，无需改变交付契约或测试。

小工程关键词 LIKE 检索、人工 active/superseded 标记、文件级依赖和单文件补丁；没有语义检索、多文件重构或自动权威性推断。任务建议包含 orders/status 标识。状态为追加快照，受 SDK 单条 8KiB 限制，不承诺并发合并。模型调用需有效账户额度；首次安装和拉取镜像需要网络。可通过标准 HTTPS_PROXY/NO_PROXY 配置代理，源码无硬编码代理。

.env、.venv、.runtime 均被忽略；示例只含占位值，不提交真实凭据。保持 2026-competition-ecosystem 开发基线。

## 来源与修改说明

样例、规格、测试、上下文模块及接入程序均为本次新增。先由当前开发助手搭建模块，再由 MiniMax 从 before 快照真实完成业务修改；最终源码与模型 after.py 一致。

复用 [PowerContext 官方 SDK](https://github.com/oceanbase/powercontext)（Apache-2.0，未复制或修改源码）、[seekdb 镜像](https://github.com/oceanbase/docker-images/blob/main/seekdb/README.md)。参考 [MiniMax 官方文档](https://platform.minimax.cn/docs/api-reference/text-openai-api)，实际采用可达的国内原生路径，请求与响应随作品交付。
