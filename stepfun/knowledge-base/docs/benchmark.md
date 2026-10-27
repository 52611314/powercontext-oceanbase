# 基准测试详情

## 测试条件

| 项 | 值 |
| --- | --- |
| 设备 | Windows 11（26H2）/ Intel Core i7-14700HX / NVIDIA RTX 4070 Laptop |
| 浏览器 | Edge 156.0.4314.42（headless + CDP 自动化，独立 profile） |
| 运行时 | Transformers.js（ONNX Runtime Web），Web Worker 内推理 |
| 模型 | bge-small-zh-v1.5，512 维，FP32 / FP16（官方）/ INT8（自量化） |
| 语料 | `eval/corpus.json`：20 篇中文技术短文，每篇 1 块（400 字分块、50 字重叠） |
| 查询 | `eval/queries.json`：30 条，人工标注 1–2 个相关文档 |
| seekdb | 1.4.0.0（server 模式，127.0.0.1:2881），HNSW + IK 全文 |
| 检索参数 | `nResults=10`，候选上限 200，RRF `rank_window_size=60`、`rank_constant=60` |
| 计时口径 | 导入：分块/向量化/写入/刷新/合计；检索：查询向量化/seekdb/端到端（含渲染前） |

## 结果

见 `eval/benchmark-summary.md`（由 `npm run bench:summary` 生成），原始逐查询数据分别在：

- `scripts/benchmark-fp32.json`
- `scripts/benchmark-fp16.json`
- `scripts/benchmark-q8.json`

## 复现步骤

```bash
# 1. 启动 seekdb（2881）与前端
docker compose up -d
npm install --legacy-peer-deps
npm run build && npm start        # 生产模式 8787

# 2. 逐配置跑基准（每次会自动启动独立 Edge）
npm run bench:fp32
npm run bench:fp16
npm run bench:q8
npm run bench:summary
```

> 基准脚本依赖本机 Edge 路径；如路径不同，设置 `BROWSER_PATH` 环境变量。
> Windows 下若终端有沙箱限制，浏览器启动可能被拒绝；这是环境限制，不是应用问题。

## 局限与说明

1. 每个配置只跑了一轮 30 条查询，未做多轮重复取中位数；冷启动与预热未拆分，因此计时包含首轮着色器编译/缓存未命中的影响；
2. 语料规模为 20 篇（20 个块），属于功能验证规模，不代表大规模语料下的 HNSW 性能；
3. WebGPU 与 WASM 的后端不同（FP16 只能跑 WebGPU），因此 FP16 与 FP32 的延迟对比同时包含后端差异；
4. INT8 的质量下降集中在少数查询（见 `perQuery` 字段），整体 Recall 下降 3.3 个百分点，未做二次排序或混合精度补偿；
5. 未在其它 GPU / 无 GPU 设备上重复，跨设备结论需重新测量。
