/**
 * 汇总三档基准结果，生成 eval/benchmark-summary.json 与 eval/benchmark-summary.md。
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs';

const root = new URL('../', import.meta.url);
const configs = [
  { key: 'fp32', file: new URL('./benchmark-fp32.json', import.meta.url), label: 'FP32 基线', sizeMb: 90.46, backend: 'WASM', compression: 1.0 },
  { key: 'fp16', file: new URL('./benchmark-fp16.json', import.meta.url), label: 'FP16', sizeMb: 45.3, backend: 'WebGPU', compression: 2.0 },
  { key: 'q8', file: new URL('./benchmark-q8.json', import.meta.url), label: 'INT8', sizeMb: 22.81, backend: 'WASM', compression: 3.97 },
];

const rows = [];
for (const config of configs) {
  if (!existsSync(config.file)) {
    console.error(`缺少 ${config.file.pathname}`);
    process.exit(1);
  }
  const data = JSON.parse(readFileSync(config.file, 'utf8'));
  rows.push({
    key: config.key,
    label: config.label,
    backend: data.backend,
    sizeMb: config.sizeMb,
    compression: config.compression,
    recall10: data.metrics.recall10,
    ndcg10: data.metrics.ndcg10,
    mrr10: data.metrics.mrr10,
    importAvgTotalMs: data.import.avgTotalMs,
    importAvgEmbedMs: data.import.avgEmbedMs,
    searchAvgTotalMs: data.search.avgTotalMs,
    searchAvgEmbedMs: data.search.avgQueryEmbedMs,
    searchAvgDbMs: data.search.avgSearchMs,
    docs: data.corpusDocs,
    queries: data.queryCount,
    browser: data.browser,
  });
}

const base = rows.find((r) => r.key === 'fp32');
const summary = {
  generatedAt: new Date().toISOString(),
  browser: base.browser,
  device: 'Windows 11 / Intel i7-14700HX / NVIDIA RTX 4070 Laptop',
  corpus: { docs: base.docs, queries: base.queries },
  rows: rows.map((r) => ({
    ...r,
    recallDeltaVsFp32: Number((r.recall10 - base.recall10).toFixed(4)),
    ndcgDeltaVsFp32: Number((r.ndcg10 - base.ndcg10).toFixed(4)),
    mrrDeltaVsFp32: Number((r.mrr10 - base.mrr10).toFixed(4)),
  })),
};

writeFileSync(new URL('./eval/benchmark-summary.json', root), JSON.stringify(summary, null, 2));

const md = [
  '# 基准结果（自动生成）',
  '',
  `- 浏览器：${summary.browser}`,
  `- 设备：${summary.device}`,
  `- 语料：${summary.corpus.docs} 篇 / 查询 ${summary.corpus.queries} 条`,
  '',
  '| 配置 | 后端 | 模型体积 | 压缩比 | Recall@10 | nDCG@10 | MRR@10 | 导入/篇 | 检索（端到端） |',
  '| --- | --- | --- | --- | --- | --- | --- | --- | --- |',
  ...summary.rows.map(
    (r) =>
      `| ${r.label} | ${r.backend} | ${r.sizeMb} MB | ${r.compression}x | ${r.recall10.toFixed(4)} | ${r.ndcg10.toFixed(4)} | ${r.mrr10.toFixed(4)} | ${r.importAvgTotalMs} ms | ${r.searchAvgTotalMs} ms |`,
  ),
  '',
  '相对 FP32 基线的质量变化：',
  '',
  '| 配置 | ΔRecall@10 | ΔnDCG@10 | ΔMRR@10 |',
  '| --- | --- | --- | --- |',
  ...summary.rows.map(
    (r) => `| ${r.label} | ${r.recallDeltaVsFp32.toFixed(4)} | ${r.ndcgDeltaVsFp32.toFixed(4)} | ${r.mrrDeltaVsFp32.toFixed(4)} |`,
  ),
  '',
  '分阶段耗时（平均）：',
  '',
  '| 配置 | 导入-向量化 | 检索-查询向量化 | 检索-seekdb |',
  '| --- | --- | --- | --- |',
  ...summary.rows.map(
    (r) => `| ${r.label} | ${r.importAvgEmbedMs} ms | ${r.searchAvgEmbedMs} ms | ${r.searchAvgDbMs} ms |`,
  ),
  '',
].join('\n');

writeFileSync(new URL('./eval/benchmark-summary.md', root), md);
console.log(md);
