/**
 * 阶段 0 验证脚本：seekdb server + seekdb-js 打通、索引刷新、库内混合检索。
 *
 * 验收点：
 *  1. 连接 seekdb server，创建数据库与 collection（512 维 cosine + IK 全文索引，禁用库内 embedding）
 *  2. 手动写入向量与文档，refresh_index 前后对比近似检索可见性
 *  3. hybridSearch 返回结果，并用 DBMS_HYBRID_SEARCH.GET_SQL 打印实际执行的 SQL
 *  4. SHOW CREATE TABLE 证明 VECTOR INDEX 与 FULLTEXT INDEX(IK) 均存在
 *
 * 运行：node scripts/phase0-verify.mjs
 */
import { writeFileSync } from 'node:fs';
import { SeekdbClient } from 'seekdb';

const HOST = process.env.SEEKDB_HOST ?? '127.0.0.1';
const PORT = Number(process.env.SEEKDB_PORT ?? 2881);
const DB = process.env.SEEKDB_DATABASE ?? 'kb_test';
const DIM = 512;
const COLLECTION = 'phase0_docs';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...args) => console.log(...args);

function unitVector(seed, dim = DIM) {
  const v = new Array(dim);
  let norm = 0;
  for (let i = 0; i < dim; i++) {
    const x = Math.sin((seed + 1) * 1.618 + i * 0.017) * Math.cos(i * 0.0031 + seed);
    v[i] = x;
    norm += x * x;
  }
  norm = Math.sqrt(norm) || 1;
  return v.map((x) => x / norm);
}

const docs = [
  { id: 'doc-1', text: 'HNSW 是近似最近邻算法，通过分层图结构加速向量检索。', tag: 'vector' },
  { id: 'doc-2', text: 'seekdb 提供向量索引与全文索引，可以在同一条 SQL 中完成混合检索。', tag: 'hybrid' },
  { id: 'doc-3', text: '浏览器端使用 Transformers.js 加载 ONNX 模型，WebGPU 优先，不支持时降级 WASM。', tag: 'browser' },
  { id: 'doc-4', text: 'INT8 量化把模型体积压缩到原来的约四分之一，代价是少量精度损失。', tag: 'quant' },
  { id: 'doc-5', text: '索引刷新采用异步方式，写入后需要调用 refresh_index 才能让新数据被近似检索到。', tag: 'refresh' },
];

const vectors = docs.map((_, i) => unitVector(i));
const queryVector = unitVector(0).map((x, i) => x * 0.98 + unitVector(99)[i] * 0.02);
const norm = Math.hypot(...queryVector);
const queryVec = queryVector.map((x) => x / norm);

const client = new SeekdbClient({ host: HOST, port: PORT, user: 'root', password: '', database: 'information_schema' });

async function waitForServer(timeoutMs = 90_000) {
  const start = Date.now();
  let lastErr;
  while (Date.now() - start < timeoutMs) {
    try {
      await client.execute('SELECT 1');
      return;
    } catch (err) {
      lastErr = err;
      await sleep(1500);
    }
  }
  throw new Error(`seekdb 未就绪: ${lastErr?.message}`);
}

async function main() {
  const started = Date.now();
  await waitForServer();
  log(`[ok] seekdb 就绪 ${HOST}:${PORT} (${Date.now() - started}ms)`);

  await client.execute(`CREATE DATABASE IF NOT EXISTS ${DB}`);
  await client.close();

  const db = new SeekdbClient({ host: HOST, port: PORT, user: 'root', password: '', database: DB });
  if (await db.hasCollection(COLLECTION)) await db.deleteCollection(COLLECTION);

  const collection = await db.createCollection({
    name: COLLECTION,
    configuration: {
      hnsw: { dimension: DIM, distance: 'cosine', type: 'hnsw', lib: 'vsag' },
      fulltextConfig: { analyzer: 'ik', properties: { ik_mode: 'smart' } },
    },
    embeddingFunction: null,
  });
  log(`[ok] collection 创建完成 dim=${collection.dimension} distance=${collection.distance}`);

  await collection.add({
    ids: docs.map((d) => d.id),
    embeddings: vectors,
    documents: docs.map((d) => d.text),
    metadatas: docs.map((d) => ({ tag: d.tag, source: 'phase0' })),
  });
  log(`[ok] 写入 ${docs.length} 条`);

  const count = await collection.count();
  log(`[ok] count() = ${count}`);

  const beforeRefresh = await collection.query({
    queryEmbeddings: [queryVec],
    nResults: 3,
    approximate: true,
    include: ['documents', 'distances'],
  });
  log(`[info] refresh 前近似检索命中: ${beforeRefresh.ids[0].length} 条`);

  const tRefresh = Date.now();
  await collection.refresh_index();
  const refreshMs = Date.now() - tRefresh;
  const afterRefresh = await collection.query({
    queryEmbeddings: [queryVec],
    nResults: 3,
    approximate: true,
    include: ['documents', 'distances'],
  });
  log(`[ok] refresh_index ${refreshMs}ms，刷新后近似检索命中 ${afterRefresh.ids[0].length} 条: ${afterRefresh.ids[0].join(', ')}`);

  const tHybrid = Date.now();
  const hybrid = await collection.hybridSearch({
    query: { whereDocument: { $contains: '向量检索' }, nResults: 10 },
    knn: { queryEmbeddings: [queryVec], nResults: 10 },
    rank: { rrf: { rankWindowSize: 60, rankConstant: 60 } },
    nResults: 5,
    include: ['documents', 'metadatas', 'distances'],
  });
  const hybridMs = Date.now() - tHybrid;
  log(`[ok] hybridSearch ${hybridMs}ms，返回 ${hybrid.ids[0].length} 条`);
  hybrid.ids[0].forEach((id, i) => {
    log(`   ${i + 1}. ${id} dist=${hybrid.distances[0][i]} ${hybrid.documents[0][i]?.slice(0, 40)}`);
  });

  const tables = await db.execute('SHOW TABLES');
  log(`[info] 表: ${JSON.stringify(tables)}`);
  const tableName = Object.values(tables?.[0] ?? {})[0];
  const ddl = await db.execute(`SHOW CREATE TABLE \`${tableName}\``);
  const ddlText = Object.values(ddl?.[0] ?? {}).find((v) => typeof v === 'string') ?? '';
  const hasVector = /VECTOR\s+INDEX|VECTOR\s*\(/i.test(ddlText);
  const hasFulltext = /FULLTEXT\s+INDEX/i.test(ddlText);
  const hasIk = /ik/i.test(ddlText);
  log(`[check] VECTOR INDEX=${hasVector} FULLTEXT INDEX=${hasFulltext} IK=${hasIk}`);

  // 用与 SDK 相同的 search_parm 调 GET_SQL，确认库内生成的 SQL 使用 MATCH ... AGAINST
  const parm = {
    query: { query_string: { fields: ['document'], query: '向量检索' } },
    knn: { field: 'embedding', k: 10, query_vector: queryVec },
    rank: { rrf: {} },
    size: 5,
  };
  await db.execute('SET @search_parm = ?', [JSON.stringify(parm)]);
  const getSql = await db.execute(
    `SELECT DBMS_HYBRID_SEARCH.GET_SQL('${tableName}', @search_parm) AS query_sql FROM dual`,
  );
  const innerSql = String(getSql?.[0]?.query_sql ?? '').replace(/^['"]|['"]$/g, '');
  const usesMatch = /MATCH\s*\(/i.test(innerSql) && /AGAINST/i.test(innerSql);
  log(`[check] DBMS_HYBRID_SEARCH 生成的 SQL 使用 MATCH/AGAINST = ${usesMatch}`);
  log('---- 生成的 SQL ----');
  log(innerSql.slice(0, 1200));
  log('--------------------');

  const result = {
    ok: true,
    host: `${HOST}:${PORT}`,
    collection: COLLECTION,
    count,
    refreshMs,
    hybridMs,
    beforeRefreshHits: beforeRefresh.ids[0].length,
    afterRefreshHits: afterRefresh.ids[0].length,
    hybridIds: hybrid.ids[0],
    hybridDistances: hybrid.distances[0],
    hasVectorIndex: hasVector,
    hasFulltextIndex: hasFulltext,
    fulltextAnalyzerIsIk: hasIk,
    hybridSqlUsesMatchAgainst: usesMatch,
    innerSql,
    tables: JSON.stringify(tables),
    totalMs: Date.now() - started,
  };
  writeFileSync(new URL('./phase0-result.json', import.meta.url), JSON.stringify(result, null, 2));
  log(`[done] 总耗时 ${result.totalMs}ms，结果写入 scripts/phase0-result.json`);

  await db.deleteCollection(COLLECTION);
  await db.close();
}

main().catch(async (err) => {
  console.error('[fail]', err);
  try {
    await client.close();
  } catch {}
  process.exit(1);
});
