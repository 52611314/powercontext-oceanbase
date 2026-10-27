/**
 * seekdb 连接与集合命名。
 *
 * 约定：
 *  - 一个知识库 = 一个 seekdb collection，名称 kb_<slug>
 *  - 向量由浏览器计算后传入，服务端只校验维度，不生成 embedding
 *  - 全文索引使用 IK 分词（createCollection 的 fulltextConfig）
 */
import { SeekdbClient } from 'seekdb';

export const SEEKDB_HOST = process.env.SEEKDB_HOST ?? '127.0.0.1';
export const SEEKDB_PORT = Number(process.env.SEEKDB_PORT ?? 2881);
export const SEEKDB_DATABASE = process.env.SEEKDB_DATABASE ?? 'kb';
export const EMBEDDING_DIM = Number(process.env.EMBEDDING_DIM ?? 512);
export const MAX_BATCH = 128;

const COLLECTION_PREFIX = 'kb_';
const SLUG_RE = /^[a-z0-9][a-z0-9_-]{1,38}[a-z0-9]$/;

let clientPromise = null;

export function isValidSlug(slug) {
  return typeof slug === 'string' && SLUG_RE.test(slug);
}

export function toCollectionName(slug) {
  return `${COLLECTION_PREFIX}${slug}`;
}

export function fromCollectionName(name) {
  return name.startsWith(COLLECTION_PREFIX) ? name.slice(COLLECTION_PREFIX.length) : null;
}

async function connect() {
  const admin = new SeekdbClient({
    host: SEEKDB_HOST,
    port: SEEKDB_PORT,
    user: 'root',
    password: '',
    database: 'information_schema',
  });
  await admin.execute(`CREATE DATABASE IF NOT EXISTS \`${SEEKDB_DATABASE}\``);
  await admin.close();

  return new SeekdbClient({
    host: SEEKDB_HOST,
    port: SEEKDB_PORT,
    user: 'root',
    password: '',
    database: SEEKDB_DATABASE,
  });
}

/** 懒连接，失败后允许下次请求重试。 */
export function getClient() {
  if (!clientPromise) {
    clientPromise = connect().catch((err) => {
      clientPromise = null;
      throw err;
    });
  }
  return clientPromise;
}

export async function ensureCollection(slug) {
  const client = await getClient();
  const name = toCollectionName(slug);
  if (await client.hasCollection(name)) {
    return client.getCollection({ name, embeddingFunction: null });
  }
  return client.createCollection({
    name,
    configuration: {
      hnsw: { dimension: EMBEDDING_DIM, distance: 'cosine', type: 'hnsw', lib: 'vsag' },
      fulltextConfig: { analyzer: 'ik', properties: { ik_mode: 'smart' } },
    },
    embeddingFunction: null,
  });
}

export async function getCollectionOrThrow(slug) {
  const client = await getClient();
  const name = toCollectionName(slug);
  if (!(await client.hasCollection(name))) {
    const err = new Error(`知识库 ${slug} 不存在`);
    err.status = 404;
    throw err;
  }
  return client.getCollection({ name, embeddingFunction: null });
}

export function badRequest(message) {
  const err = new Error(message);
  err.status = 400;
  return err;
}
