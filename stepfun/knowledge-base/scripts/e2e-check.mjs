/**
 * 阶段 4/5 端到端联调：真实 Edge 中完成 导入 → 检索 → 建库/切换 → 跨库隔离 → 删除。
 *
 * 运行：node scripts/e2e-check.mjs --headless
 * 输出：scripts/e2e-result.json + scripts/e2e-screenshot.png
 */
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';

const BROWSER =
  process.env.BROWSER_PATH ?? 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PROFILE = 'D:\\oceanbase\\.build-env\\edge-kb-profile';
const PORT = 9225;
const URL_BASE = 'http://127.0.0.1:5173/';
const headful = process.argv.includes('--headful');

mkdirSync(PROFILE, { recursive: true });

const browser = spawn(
  BROWSER,
  [
    headful ? '--window-position=-2400,-2400' : '--headless=new',
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
const events = [];

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
    await sleep(700);
  }
  throw new Error(`等待超时(${label})`);
}

async function clickText(text) {
  return evaluate(`(() => {
    const btn = [...document.querySelectorAll('button')].find((b) => b.textContent.includes(${JSON.stringify(text)}));
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
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, ${JSON.stringify(value)});
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  })()`);
}

async function apiJson(path, options) {
  const res = await fetch(`http://127.0.0.1:8787${path}`, options);
  return res.json();
}

const DOC_A = `seekdb 是 OceanBase 推出的 AI 原生搜索数据库，支持向量索引与全文索引。
HNSW 是近似最近邻算法，通过分层图结构加速向量检索，适合大规模语料。
全文索引使用 IK 分词器，对中文查询友好，可以精确匹配专有名词和数字。
索引刷新采用异步方式，写入后需要调用 refresh_index 才能让新数据被近似检索到。
混合检索通过 DBMS_HYBRID_SEARCH 在一次调用中完成全文与向量两路召回，并用 RRF 融合排序。`;

const DOC_B = `浏览器端推理使用 Transformers.js 加载 ONNX 模型，优先使用 WebGPU 后端。
WebGPU 不可用时会自动降级到 WASM，并在界面中显示当前后端与降级原因。
INT8 动态量化把模型体积压缩到约四分之一，推理速度提升，但精度会有少量损失。
FP16 适合 WebGPU，模型体积约为 FP32 的一半，数值精度对检索影响很小。
量化前后需要在同一评测集上比较 Recall@10 与 nDCG@10，避免只看体积。`;

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
      return;
    }
    if (msg.method === 'Page.javascriptDialogOpening') {
      send('Page.handleJavaScriptDialog', { accept: true }).catch(() => {});
    }
    if (msg.method === 'Runtime.exceptionThrown') {
      events.push({ type: 'exception', text: msg.params.exceptionDetails.exception?.description ?? '' });
    }
    if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
      events.push({ type: 'console-error', text: msg.params.args.map((a) => a.value ?? a.description).join(' ') });
    }
  });

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Page.navigate', { url: URL_BASE });
  await waitFor("document.readyState === 'complete' && Boolean(document.querySelector('.badge'))", 60000, '页面加载');
  await sleep(800);

  const result = { browser: version.Browser, steps: [], ok: false };

  // 0. 清理历史测试库
  for (const kb of ['e2e_a', 'e2e_b']) {
    await apiJson(`/api/collections/${kb}`, { method: 'DELETE' }).catch(() => {});
  }
  await evaluate('location.reload()');
  await waitFor("document.readyState === 'complete' && Boolean(document.querySelector('.badge'))", 60000, '刷新');
  await sleep(800);

  // 1. 加载模型
  await clickText('加载模型');
  const badge = await waitFor(
    `(() => { const t = document.querySelector('.badge')?.textContent ?? ''; return /WebGPU|WASM/.test(t) && !t.includes('未加载') ? t : ''; })()`,
    180000,
    '模型加载',
  );
  result.badge = badge;
  console.log('[1] backend:', badge);

  // 2. 创建 e2e_a 并导入 DOC_A
  await setValue('.kb-create input', 'e2e_a');
  await clickText('创建');
  await waitFor(`[...document.querySelectorAll('.kb-item')].some((b) => b.textContent.includes('e2e_a') && b.className.includes('active'))`, 20000, '切换到 e2e_a');
  await setValue('input[placeholder^="文档标题"]', 'DOC-A');
  await setValue('textarea', DOC_A, 'textarea');
  await clickText('开始导入');
  const importTiming = await waitFor(
    `(() => { const cells = [...document.querySelectorAll('.timing tbody tr td')].map((td) => td.textContent.trim()); return cells.length ? cells.join(' | ') : ''; })()`,
    180000,
    '导入完成',
  );
  result.importTiming = importTiming;
  console.log('[2] import A:', importTiming);

  // 3. 检索
  await setValue('input[placeholder^="输入查询"]', 'refresh_index 之后新数据什么时候能被近似检索到');
  await clickText('检索');
  const firstResult = await waitFor(
    `(() => { const li = document.querySelector('.results li'); return li ? li.textContent.trim().slice(0, 160) : ''; })()`,
    120000,
    '检索结果',
  );
  const resultCount = await evaluate(`document.querySelectorAll('.results li').length`);
  result.searchA = { firstResult, count: resultCount };
  console.log('[3] search A:', resultCount, '条，首条:', firstResult.slice(0, 60));

  // 4. 建 e2e_b 导入 DOC_B
  await setValue('.kb-create input', 'e2e_b');
  await clickText('创建');
  await waitFor(`[...document.querySelectorAll('.kb-item')].some((b) => b.textContent.includes('e2e_b') && b.className.includes('active'))`, 20000, '切换到 e2e_b');
  await setValue('input[placeholder^="文档标题"]', 'DOC-B');
  await setValue('textarea', DOC_B, 'textarea');
  await clickText('开始导入');
  await waitFor(
    `(() => { const rows = document.querySelectorAll('.timing tbody tr'); return rows.length > 0; })()`,
    180000,
    '导入 B',
  );
  console.log('[4] import B done');

  // 5. 在 e2e_b 检索与 B 相关的问题：结果只能来自 DOC-B
  await setValue('input[placeholder^="输入查询"]', 'WebGPU 不可用时浏览器会怎么处理');
  await clickText('检索');
  await waitFor(`document.querySelectorAll('.results li').length > 0`, 120000, 'B 检索');
  const bMeta = await evaluate(`[...document.querySelectorAll('.results .meta')].map((n) => n.textContent)`);
  result.searchB = { metas: bMeta };
  result.isolationB = bMeta.every((m) => m.includes('DOC-B'));
  console.log('[5] search in B metas:', JSON.stringify(bMeta), 'isolated:', result.isolationB);

  // 6. 切回 e2e_a，用同一问题检索：结果只能来自 DOC-A
  await evaluate(`[...document.querySelectorAll('.kb-item')].find((b) => b.textContent.includes('e2e_a')).click()`);
  await sleep(600);
  await setValue('input[placeholder^="输入查询"]', 'refresh_index 之后新数据什么时候能被近似检索到');
  await clickText('检索');
  await waitFor(`document.querySelectorAll('.results li').length > 0`, 120000, 'A 检索');
  const aMeta = await evaluate(`[...document.querySelectorAll('.results .meta')].map((n) => n.textContent)`);
  result.searchA2 = { metas: aMeta };
  result.isolationA = aMeta.every((m) => m.includes('DOC-A'));
  console.log('[6] search back in A metas:', JSON.stringify(aMeta), 'isolated:', result.isolationA);

  // 7. 删除当前库 e2e_a（confirm 自动 accept）
  await clickText('删除当前库');
  const deleted = await waitFor(
    `(() => { return ![...document.querySelectorAll('.kb-item')].some((b) => b.textContent.includes('e2e_a')); })()`,
    30000,
    '删除 e2e_a',
  );
  result.deleted = deleted;
  console.log('[7] delete e2e_a:', deleted);

  // 8. 截图
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(new URL('./e2e-screenshot.png', import.meta.url), Buffer.from(shot.data, 'base64'));

  result.events = events.slice(0, 10);
  result.ok = true;
  writeFileSync(new URL('./e2e-result.json', import.meta.url), JSON.stringify(result, null, 2));
  console.log('[done] e2e-result.json / e2e-screenshot.png');

  await send('Browser.close').catch(() => {});
  ws.close();
  browser.kill();
}

main().catch(async (err) => {
  console.error('[FAIL]', err.message);
  console.error('events:', JSON.stringify(events.slice(0, 10), null, 2));
  try {
    await send('Browser.close');
  } catch {}
  try {
    ws?.close();
  } catch {}
  browser.kill();
  process.exit(1);
}).finally(() => setTimeout(() => process.exit(0), 1200));
