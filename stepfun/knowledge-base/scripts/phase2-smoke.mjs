/**
 * 阶段 2 冒烟测试：覆盖 BFF 全部接口与主要错误分支。
 *
 * 前提：BFF 已启动在 http://127.0.0.1:8787，seekdb 可用。
 * 运行：node scripts/phase2-smoke.mjs
 */
const BASE = process.env.BFF_BASE ?? 'http://127.0.0.1:8787';
const DIM = 512;
const KB = 'phase2_smoke';

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

function assert(condition, message) {
  if (!condition) throw new Error(`断言失败: ${message}`);
  console.log(`  [ok] ${message}`);
}

async function call(path, { method = 'GET', body, expect } = {}) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let payload = null;
  try {
    payload = await res.json();
  } catch {
    payload = null;
  }
  if (expect && res.status !== expect) {
    throw new Error(`${method} ${path} 期望 ${expect}，实际 ${res.status}: ${JSON.stringify(payload)}`);
  }
  return { status: res.status, payload };
}

async function main() {
  console.log('[1] health');
  const health = await call('/api/health', { expect: 200 });
  assert(health.payload.ok === true, 'health.ok');
  assert(health.payload.seekdb.version.includes('seekdb'), `seekdb 版本 = ${health.payload.seekdb.version}`);
  assert(health.payload.embeddingDim === DIM, `embeddingDim = ${DIM}`);

  console.log('[2] 清理残留');
  await call(`/api/collections/${KB}`, { method: 'DELETE' });

  console.log('[3] 创建集合');
  const created = await call('/api/collections', { method: 'POST', body: { name: KB }, expect: 201 });
  assert(created.payload.count === 0, '初始 count = 0');

  console.log('[4] 批量写入 3 条');
  const docs = [
    { id: 'doc-1', text: 'HNSW 是近似最近邻算法，通过分层图结构加速向量检索。', metadata: { tag: 'vector' } },
    { id: 'doc-2', text: 'seekdb 提供向量索引与全文索引，可以在同一条 SQL 中完成混合检索。', metadata: { tag: 'hybrid' } },
    { id: 'doc-3', text: 'INT8 量化把模型体积压缩到原来的约四分之一。', metadata: { tag: 'quant' } },
  ];
  const written = await call(`/api/collections/${KB}/documents`, {
    method: 'POST',
    body: { items: docs.map((d, i) => ({ ...d, embedding: unitVector(i) })) },
    expect: 201,
  });
  assert(written.payload.inserted === 3, '写入 3 条');
  assert(written.payload.refreshMs >= 0, `refreshMs = ${written.payload.refreshMs}`);
  assert(written.payload.count === 3, 'count = 3');

  console.log('[5] 混合检索');
  const queryEmbedding = unitVector(0).map((x, i) => x * 0.97 + unitVector(77)[i] * 0.03);
  const norm = Math.hypot(...queryEmbedding);
  const search = await call(`/api/collections/${KB}/search`, {
    method: 'POST',
    body: { query: '向量检索', embedding: queryEmbedding.map((x) => x / norm), nResults: 3 },
    expect: 200,
  });
  assert(search.payload.items.length === 3, '返回 3 条');
  assert(search.payload.items[0].id === 'doc-1', `首条为 doc-1（实际 ${search.payload.items[0].id}）`);
  assert(search.payload.latencyMs >= 0, `检索耗时 ${search.payload.latencyMs}ms`);

  console.log('[6] 增量写入 + 刷新后可检索');
  const extra = await call(`/api/collections/${KB}/documents`, {
    method: 'POST',
    body: { items: [{ id: 'doc-4', text: '索引刷新是异步的，写入后需要 refresh_index。', embedding: unitVector(3), metadata: { tag: 'refresh' } }] },
    expect: 201,
  });
  assert(extra.payload.count === 4, 'count = 4');
  const search2 = await call(`/api/collections/${KB}/search`, {
    method: 'POST',
    body: { query: 'refresh_index', embedding: unitVector(3), nResults: 4 },
    expect: 200,
  });
  assert(search2.payload.items.some((x) => x.id === 'doc-4'), '新文档刷新后可检索');

  console.log('[7] 删除文档');
  const removed = await call(`/api/collections/${KB}/delete-documents`, {
    method: 'POST',
    body: { ids: ['doc-4'] },
    expect: 200,
  });
  assert(removed.payload.count === 3, '删除后 count = 3');

  console.log('[8] 错误分支');
  await call('/api/collections', { method: 'POST', body: { name: 'A' }, expect: 400 });
  await call(`/api/collections/${KB}/documents`, {
    method: 'POST',
    body: { items: [{ id: 'x', text: 'x', embedding: [1, 2, 3] }] },
    expect: 400,
  });
  await call('/api/collections/not_exist_kb/search', {
    method: 'POST',
    body: { query: 'x', embedding: unitVector(1) },
    expect: 404,
  });
  await call(`/api/collections/${KB}/search`, {
    method: 'POST',
    body: { query: '', embedding: unitVector(1) },
    expect: 400,
  });
  console.log('  [ok] 4 个错误分支均返回预期状态码');

  console.log('[9] 清理');
  const deleted = await call(`/api/collections/${KB}`, { method: 'DELETE', expect: 200 });
  assert(deleted.payload.deleted === true, '集合已删除');
  const list = await call('/api/collections', { expect: 200 });
  assert(!list.payload.collections.some((c) => c.name === KB), '列表中不再出现');

  console.log('\n[PASS] 阶段 2 冒烟测试全部通过');
}

main().catch((err) => {
  console.error('\n[FAIL]', err.message);
  process.exit(1);
});
