# 一页技术说明

本模块作为已有开发助手的前置 CLI：任务 → seekdb 查询 → 有效规格及代码/测试依赖扩展 → PowerContext 历史状态 → 带资料指纹的 JSON 上下文 → MiniMax 修改源码 → 固定测试验证 → PowerContext 保存结果 → 下个进程读取并判断是否可复用。

**工程资料层（seekdb）**：engineering_docs 保存规格条目、代码文件、测试文件的真实路径、全文、SHA256、依赖链接和有效标志。import 在事务内替换当前资料；superseded 条目保留为不可检索历史。context 使用参数化 LIKE 进行任务词查询，再沿显式来源扩展同一文件契约，使“必须保持”的兼容行为不会因关键词不同而漏掉。实际 SQL、参数、来源 ID 和哈希记录在技术日志中，返回的正文来自数据库。读取时核对当前文件哈希，避免旧索引冒充有效上下文。

**状态层（PowerContext 1.4.1）**：嵌入 open_builtin_runtime，使用持久化 SQLite 后端；创建幂等 Scope，以任务 ID 的哈希作为 Atomic Memory kind，显式保存结构化 done、todo、validation、fingerprint。每次运行重新初始化 SDK，分页读取同任务的快照并取最新保存版本。没有自建 PowerContext 替代物，也不依赖保留整个 Agent 对话。只有当前规格、代码、测试的组合指纹与已有结果一致，且退出码为 0，validation_current 才为 true。

**开发执行层（MiniMax）**：agent.py 将当前 context 作为用户请求提供给国内原生 ChatCompletion V2 HTTP API，要求仅返回 orders.py。最终内容经语法解析检查后写入限定业务文件，同时保留请求、最终代码、模型元信息、前后快照与 diff。测试文件不交给模型修改。MiniMax 凭据不进入提交；不保存私有思维链。本次已通过真实 M3.1 Flash 调用完成业务修改，六项严格验收通过；缺少真实记录时仍明确 pending。

**验证与交付层**：acceptance.py 用同一份固定目标与回归测试加载 before / after 源码；前者必须因行为断言失败，后者全通过；日志同时绑定 Git 基线、代码及测试 SHA256。集成测试使用空 PowerContext 状态和随机数据库，以独立进程检验来源、失效规格排除、重启恢复、状态实际使用、规格刷新与历史测试失效。全六项结果见 summary.json；--require-model 会在模型未完成时失败。

边界：小工程关键词检索、人工规格生效标记、文件级代码相关性、单业务文件生成；无语义推断或自动多文件重构。复用官方 PowerContext（Apache-2.0）和 seekdb，业务样例与适配代码为原创。