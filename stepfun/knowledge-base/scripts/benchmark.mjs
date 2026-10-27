/**
 * 阶段 6 基准：在真实 Edge 中导入 20 篇语料、跑 30 条查询，计算 Recall@10 / nDCG@10 / MRR@10。
 *
 * 用法：
 *   BENCH_DTYPE=q8 BENCH_PREFER=wasm node scripts/benchmark.mjs
 *   BENCH_DTYPE=fp16 BENCH_PREFER=auto node scripts/benchmark.mjs
 *   BENCH_DTYPE=fp32 BENCH_PREFER=wasm node scripts/benchmark.mjs
 */
import { spawn } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';

const BROWSER = process.env.BROWSER_PATH ?? 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PROFILE = 'D:\\oceanbase\\.build-env\\edge-kb-profile';
const PORT = Number(process.env.BENCH_PORT ?? 9226);
const argDtype = process.argv.find((a) => a.startsWith('--dtype='))?.split('=')[1];
const argPrefer = process.argv.find((a) => a.startsWith('--prefer='))?.split('=')[1];
const DTYPE = argDtype ?? process.env.BENCH_DTYPE ?? 'q8';
const PREFER = argPrefer ?? process.env.BENCH_PREFER ?? 'wasm';
const KB = `bench_${DTYPE}`;
const OUT = new URL(`./benchmark-${DTYPE}.json`, import.meta.url);
const corpus = JSON.parse(readFileSync(new URL('../eval/corpus.json', import.meta.url), 'utf8'));
const queries = JSON.parse(readFileSync(new URL('../eval/queries.json', import.meta.url), 'utf8'));

mkdirSync(PROFILE, { recursive: true });

const browser = spawn(
  BROWSER,
  [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${PROFILE}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-sync',
    '--enable-unsafe-webgpu',
    '--window-size=1280,1000',
    'about:blank',
  ],
  { stdio: 'ignore' },
);

let ws;
let id = 0;
const pending = new Map();

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const n = ++id;
    pending.set(n, { resolve, reject });
    ws.send(JSON.stringify({ id: n, method, params }));
  });
}

async function evaluate(expression, awaitPromise = true) {
  const r = await send('Runtime.evaluate', { expression, awaitPromise, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
  return r.result?.value;
}

async function waitFor(expression, timeoutMs, label) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const value = await evaluate(expression);
      if (value) return value;
    } catch {}
    await sleep(600);
  }
  throw new Error(`等待超时(${label})`);
}

async function clickText(text) {
  return evaluate(`(() => {
    const btn = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === ${JSON.stringify(text)});
    if (!btn) return false;
    btn.click();
    return true;
  })()`);
}

async function setValue(selector, value, tag = 'input') {
  return evaluate(`(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return false;
    const proto = window.${tag === 'textarea' ? 'HTMLTextAreaElement' : 'HTMLInputElement'}.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, ${JSON.stringify(value)});
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  })()`);
}

const readTiming = () =>
  evaluate(`[...document.querySelectorAll('.timing')].map((t) => [...t.querySelectorAll('td')].map((td) => td.textContent.trim()))`);

function num(text) {
  const m = String(text ?? '').match(/-?\d+(\.\d+)?/);
  return m ? Number(m[0]) : 0;
}

async function main() {
  let version;
  for (let i = 0; i < 40; i++) {
    await sleep(500);
    try {
      version = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
      break;
    } catch {}
  }
  if (!version) throw new Error('CDP 未就绪');

  const tab = await (await fetch(`http://127.0.0.1:${PORT}/json/new`, { method: 'PUT' })).json();
  ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener('open', res);
    ws.addEventListener('error', rej);
  });
  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) p.reject(new Error(msg.error.message));
      else p.resolve(msg.result);
    }
  });

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Page.navigate', { url: `http://127.0.0.1:5173/?dtype=${DTYPE}&prefer=${PREFER}` });
  await waitFor("document.readyState === 'complete' && Boolean(document.querySelector('.badge'))", 60000, '页面加载');
  await sleep(800);

  // 清理同名库
  await fetch(`http://127.0.0.1:8787/api/collections/${KB}`, { method: 'DELETE' }).catch(() => {});
  await evaluate('location.reload()');
  await waitFor("document.readyState === 'complete' && Boolean(document.querySelector('.badge'))", 60000, '刷新');
  await sleep(800);

  await clickText('加载模型');
  const badge = await waitFor(
    `(() => { const t = document.querySelector('.badge')?.textContent ?? ''; return /WebGPU|WASM/.test(t) && !t.includes('未加载') ? t : ''; })()`,
    180000,
    '模型加载',
  );
  const loadDetail = await evaluate(`[...document.querySelectorAll('.mono')].map((n) => n.textContent).join(' | ')`);
  console.log(`[${DTYPE}] backend=${badge} ${loadDetail.slice(0, 80)}`);

  // 建库
  await setValue('.kb-create input', KB);
  await clickText('创建');
  await waitFor(
    `[...document.querySelectorAll('.kb-item')].some((b) => b.textContent.includes(${JSON.stringify(KB)}) && b.className.includes('active'))`,
    20000,
    '建库',
  );

  // 导入语料
  const importStats = [];
  for (const doc of corpus) {
    await setValue('input[placeholder^="文档标题"]', doc.title);
    await setValue('textarea', doc.text, 'textarea');
    await clickText('开始导入');
    await sleep(2200);
    const tables = await readTiming();
    const importRow = tables.find((row) => row.length === 5 && !row.includes('查询向量化'));
    if (importRow) importStats.push(importRow.map((cell, i) => (i === 0 ? num(cell) : num(cell))));
  }
  console.log(`[${DTYPE}] imported ${importStats.length}/${corpus.length}`);

  // 检索
  const perQuery = [];
  for (const q of queries) {
    await setValue('input[placeholder^="输入查询"]', q.query);
    await clickText('检索');
    await sleep(900);
    const titles = await evaluate(
      `[...document.querySelectorAll('.results .meta')].map((n) => n.textContent.split(' · ')[0])`,
    );
    const tables = await readTiming();
    const searchRow = tables.find((row) => row.length === 4);
    perQuery.push({ id: q.id, query: q.query, titles: titles ?? [], timing: searchRow ?? [] });
  }
  console.log(`[${DTYPE}] queries done: ${perQuery.length}`);

  // 指标
  const titleById = new Map(corpus.map((doc) => [doc.id, doc.title]));
  let recallSum = 0;
  let ndcgSum = 0;
  let mrrSum = 0;
  for (const q of queries) {
    const ranked = perQuery.find((x) => x.id === q.id)?.titles ?? [];
    const rel = new Set(q.relevant.map((docId) => titleById.get(docId) ?? docId));
    const top10 = ranked.slice(0, 10);
    recallSum += top10.filter((t) => rel.has(t)).length / rel.size;
    let dcg = 0;
    top10.forEach((t, i) => {
      if (rel.has(t)) dcg += 1 / Math.log2(i + 2);
    });
    let idcg = 0;
    for (let i = 0; i < Math.min(rel.size, 10); i++) idcg += 1 / Math.log2(i + 2);
    ndcgSum += idcg > 0 ? dcg / idcg : 0;
    const first = top10.findIndex((t) => rel.has(t));
    mrrSum += first >= 0 ? 1 / (first + 1) : 0;
  }

  const avg = (rows, index) => {
    const values = rows.map((row) => num(row[index])).filter((v) => Number.isFinite(v));
    return values.length ? Math.round((values.reduce((a, b) => a + b, 0) / values.length) * 10) / 10 : 0;
  };

  const result = {
    dtype: DTYPE,
    prefer: PREFER,
    backend: badge,
    browser: version.Browser,
    corpusDocs: corpus.length,
    queryCount: queries.length,
    import: {
      docs: importStats.length,
      avgEmbedMs: avg(importStats, 1),
      avgInsertMs: avg(importStats, 2),
      avgRefreshMs: avg(importStats, 3),
      avgTotalMs: avg(importStats, 4),
    },
    search: {
      avgQueryEmbedMs: avg(perQuery.map((q) => q.timing), 0),
      avgSearchMs: avg(perQuery.map((q) => q.timing), 1),
      avgTotalMs: avg(perQuery.map((q) => q.timing), 3),
    },
    metrics: {
      recall10: Number((recallSum / queries.length).toFixed(4)),
      ndcg10: Number((ndcgSum / queries.length).toFixed(4)),
      mrr10: Number((mrrSum / queries.length).toFixed(4)),
    },
    perQuery,
  };

  writeFileSync(OUT, JSON.stringify(result, null, 2));
  console.log(
    `[${DTYPE}] recall10=${result.metrics.recall10} ndcg10=${result.metrics.ndcg10} mrr10=${result.metrics.mrr10} importTotal=${result.import.avgTotalMs}ms searchTotal=${result.search.avgTotalMs}ms`,
  );

  await send('Browser.close').catch(() => {});
  ws.close();
  browser.kill();
}

main()
  .catch(async (err) => {
    console.error('[FAIL]', err.message);
    try {
      await send('Browser.close');
    } catch {}
    try {
      ws?.close();
    } catch {}
    browser.kill();
    process.exit(1);
  })
  .finally(() => setTimeout(() => process.exit(0), 1200));
