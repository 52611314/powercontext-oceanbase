/**
 * 迷你知识库 BFF。
 *
 * 职责边界：
 *  - 浏览器负责 embedding（WebGPU/WASM），本服务只接收向量并写入 seekdb
 *  - 混合检索只调用一次 collection.hybridSearch（seekdb 内 DBMS_HYBRID_SEARCH 完成 RRF）
 *  - 本服务不参与排序、不拼合两路候选
 */
import express from 'express';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import {
  EMBEDDING_DIM,
  MAX_BATCH,
  SEEKDB_DATABASE,
  SEEKDB_HOST,
  SEEKDB_PORT,
  badRequest,
  ensureCollection,
  fromCollectionName,
  getClient,
  getCollectionOrThrow,
  isValidSlug,
} from './db.mjs';

const app = express();
app.disable('x-powered-by');
app.use(express.json({ limit: '64mb' }));

const asyncHandler = (fn) => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);

function assertSlug(name) {
  if (!isValidSlug(name)) {
    throw badRequest('知识库名需为 3-40 位小写字母、数字、下划线或连字符，且首尾不能是符号');
  }
  return name;
}

function assertEmbedding(vec, where) {
  if (!Array.isArray(vec) || vec.length !== EMBEDDING_DIM) {
    throw badRequest(`${where} 必须是长度为 ${EMBEDDING_DIM} 的数字数组`);
  }
  if (!vec.every((x) => typeof x === 'number' && Number.isFinite(x))) {
    throw badRequest(`${where} 含非法数值`);
  }
}

function sanitizeMetadata(meta, where) {
  if (meta == null) return {};
  if (typeof meta !== 'object' || Array.isArray(meta)) throw badRequest(`${where} 必须是对象`);
  const out = {};
  for (const [key, value] of Object.entries(meta)) {
    if (!/^[a-zA-Z0-9_]{1,32}$/.test(key)) throw badRequest(`${where}.${key} 字段名非法`);
    const type = typeof value;
    if (type !== 'string' && type !== 'number' && type !== 'boolean') {
      throw badRequest(`${where}.${key} 只支持字符串、数字、布尔值`);
    }
    out[key] = value;
  }
  return out;
}

app.get(
  '/api/health',
  asyncHandler(async (_req, res) => {
    const client = await getClient();
    const rows = await client.execute('SELECT VERSION() AS version');
    res.json({
      ok: true,
      seekdb: {
        host: SEEKDB_HOST,
        port: SEEKDB_PORT,
        database: SEEKDB_DATABASE,
        version: rows?.[0]?.version ?? 'unknown',
      },
      embeddingDim: EMBEDDING_DIM,
      maxBatch: MAX_BATCH,
      hybridSearch: 'seekdb DBMS_HYBRID_SEARCH (RRF in database)',
    });
  }),
);

app.get(
  '/api/collections',
  asyncHandler(async (_req, res) => {
    const client = await getClient();
    const collections = await client.listCollections({ withEmbeddingFunction: false });
    const items = [];
    for (const collection of collections) {
      const name = fromCollectionName(collection.name);
      if (!name) continue;
      items.push({ name, count: await collection.count(), dimension: collection.dimension });
    }
    items.sort((a, b) => a.name.localeCompare(b.name));
    res.json({ collections: items });
  }),
);

app.post(
  '/api/collections',
  asyncHandler(async (req, res) => {
    const name = assertSlug(req.body?.name);
    const collection = await ensureCollection(name);
    res.status(201).json({ name, count: await collection.count(), dimension: collection.dimension });
  }),
);

app.delete(
  '/api/collections/:name',
  asyncHandler(async (req, res) => {
    const name = assertSlug(req.params.name);
    const client = await getClient();
    const collectionName = `kb_${name}`;
    if (!(await client.hasCollection(collectionName))) {
      res.status(404).json({ error: { code: 404, message: `知识库 ${name} 不存在` } });
      return;
    }
    await client.deleteCollection(collectionName);
    res.json({ deleted: true, name });
  }),
);

app.post(
  '/api/collections/:name/documents',
  asyncHandler(async (req, res) => {
    const name = assertSlug(req.params.name);
    const items = req.body?.items;
    if (!Array.isArray(items) || items.length === 0) throw badRequest('items 必须是非空数组');
    if (items.length > MAX_BATCH) throw badRequest(`单次最多写入 ${MAX_BATCH} 条`);

    const seen = new Set();
    const ids = [];
    const embeddings = [];
    const documents = [];
    const metadatas = [];
    items.forEach((item, index) => {
      const where = `items[${index}]`;
      if (typeof item?.id !== 'string' || !item.id.trim()) throw badRequest(`${where}.id 必须是非空字符串`);
      if (seen.has(item.id)) throw badRequest(`${where}.id 重复: ${item.id}`);
      seen.add(item.id);
      if (typeof item.text !== 'string' || !item.text.trim()) throw badRequest(`${where}.text 必须是非空字符串`);
      assertEmbedding(item.embedding, `${where}.embedding`);
      ids.push(item.id);
      embeddings.push(item.embedding);
      documents.push(item.text);
      metadatas.push(sanitizeMetadata(item.metadata, `${where}.metadata`));
    });

    const collection = await getCollectionOrThrow(name);
    const insertStart = Date.now();
    await collection.add({ ids, embeddings, documents, metadatas });
    const insertMs = Date.now() - insertStart;

    const refreshStart = Date.now();
    await collection.refresh_index();
    const refreshMs = Date.now() - refreshStart;

    res.status(201).json({
      inserted: items.length,
      insertMs,
      refreshMs,
      count: await collection.count(),
    });
  }),
);

app.post(
  '/api/collections/:name/delete-documents',
  asyncHandler(async (req, res) => {
    const name = assertSlug(req.params.name);
    const ids = req.body?.ids;
    if (!Array.isArray(ids) || ids.length === 0 || !ids.every((id) => typeof id === 'string')) {
      throw badRequest('ids 必须是非空字符串数组');
    }
    const collection = await getCollectionOrThrow(name);
    const start = Date.now();
    await collection.delete({ ids });
    await collection.refresh_index();
    res.json({ deleted: ids.length, elapsedMs: Date.now() - start, count: await collection.count() });
  }),
);

app.post(
  '/api/collections/:name/search',
  asyncHandler(async (req, res) => {
    const name = assertSlug(req.params.name);
    const { query, embedding, nResults = 10, rankWindowSize = 60 } = req.body ?? {};
    if (typeof query !== 'string' || !query.trim()) throw badRequest('query 必须是非空字符串');
    assertEmbedding(embedding, 'embedding');
    if (!Number.isInteger(nResults) || nResults < 1 || nResults > 50) throw badRequest('nResults 需为 1-50 的整数');
    if (!Number.isInteger(rankWindowSize) || rankWindowSize < 1 || rankWindowSize > 1000) {
      throw badRequest('rankWindowSize 需为 1-1000 的整数');
    }

    const collection = await getCollectionOrThrow(name);
    const candidateSize = Math.min(Math.max(nResults * 4, 40), 200);
    const start = Date.now();
    const result = await collection.hybridSearch({
      query: { whereDocument: { $contains: query.trim() }, nResults: candidateSize },
      knn: { queryEmbeddings: [embedding], nResults: candidateSize },
      rank: { rrf: { rankWindowSize, rankConstant: 60 } },
      nResults,
      include: ['documents', 'metadatas', 'distances'],
    });
    const latencyMs = Date.now() - start;

    const items = (result.ids[0] ?? []).map((id, index) => ({
      id,
      text: result.documents?.[0]?.[index] ?? '',
      score: result.distances?.[0]?.[index] ?? 0,
      metadata: result.metadatas?.[0]?.[index] ?? {},
    }));
    res.json({ query, items, latencyMs, candidateSize });
  }),
);

app.post(
  '/api/collections/:name/refresh',
  asyncHandler(async (req, res) => {
    const name = assertSlug(req.params.name);
    const collection = await getCollectionOrThrow(name);
    const start = Date.now();
    await collection.refresh_index();
    res.json({ refreshed: true, elapsedMs: Date.now() - start });
  }),
);

app.get(
  '/api/collections/:name',
  asyncHandler(async (req, res) => {
    const name = assertSlug(req.params.name);
    const collection = await getCollectionOrThrow(name);
    res.json({ name, count: await collection.count(), dimension: collection.dimension, distance: collection.distance });
  }),
);

// 未匹配的 /api 路径统一 404，避免被静态资源兜底成 HTML
app.use('/api', (_req, res) => {
  res.status(404).json({ error: { code: 404, message: '接口不存在' } });
});

app.use((err, _req, res, _next) => {
  const status = err.status ?? (err.type === 'entity.parse.failed' ? 400 : 500);
  if (status >= 500) console.error('[bff]', err);
  res.status(status).json({
    error: {
      code: status,
      message: status >= 500 ? '服务内部错误' : err.message,
    },
  });
});

// 生产模式：托管 vite build 产物，单端口提供页面与 API
const distDir = fileURLToPath(new URL('../dist/', import.meta.url));
if (existsSync(distDir)) {
  app.use(express.static(distDir, { index: 'index.html' }));
  app.get('/*splat', (req, res, next) => {
    if (req.path.startsWith('/api')) return next();
    res.sendFile(join(distDir, 'index.html'));
  });
  console.log(`[bff] serving static files from ${distDir}`);
} else {
  console.log('[bff] dist/ 不存在，仅提供 API（开发模式请用 npm run dev 启动 Vite）');
}

const port = Number(process.env.PORT ?? 8787);
app.listen(port, () => {
  console.log(`[bff] listening on http://127.0.0.1:${port} (seekdb ${SEEKDB_HOST}:${SEEKDB_PORT}/${SEEKDB_DATABASE})`);
});
