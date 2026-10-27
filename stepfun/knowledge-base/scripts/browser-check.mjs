/**
 * 浏览器端验收脚本：用 CDP 驱动 Edge，验证 Worker 内模型加载、向量化与量化对比。
 *
 * 用法：
 *   node scripts/browser-check.mjs --headless        # WASM 路径（默认）
 *   node scripts/browser-check.mjs --headful         # 可用真实 GPU 验证 WebGPU
 *
 * 输出：scripts/browser-check-result.json
 */
import { spawn } from 'node:child_process';
import { writeFileSync, mkdirSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';

const BROWSER =
  process.env.BROWSER_PATH ?? 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PROFILE = 'D:\\oceanbase\\.build-env\\edge-kb-profile';
const PORT = Number(process.env.CDP_PORT ?? 9222);
const URL_BASE = process.env.APP_URL ?? 'http://127.0.0.1:5173/';
const headful = process.argv.includes('--headful');
const disableGpu = process.argv.includes('--disable-gpu');
const OUT = new URL('./browser-check-result.json', import.meta.url);

mkdirSync(PROFILE, { recursive: true });

const browserArgs = [
  headful ? '--window-position=-2400,-2400' : '--headless=new',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${PROFILE}`,
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-background-networking',
  '--disable-renderer-backgrounding',
  '--disable-sync',
  ...(disableGpu ? ['--disable-gpu'] : ['--enable-unsafe-webgpu']),
  '--window-size=1280,900',
  'about:blank',
];

const browser = spawn(BROWSER, browserArgs, { stdio: 'ignore', detached: false });

let ws = null;
let msgId = 0;
const pending = new Map();

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++msgId;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression, awaitPromise = true) {
  const result = await send('Runtime.evaluate', { expression, awaitPromise, returnByValue: true });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
  }
  return result.result?.value;
}

async function waitFor(expression, timeoutMs, label) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const value = await evaluate(expression);
      if (value) return value;
    } catch {
      /* 页面可能还在加载 */
    }
    await sleep(800);
  }
  throw new Error(`等待超时(${label}): ${expression}`);
}

async function clickByText(text) {
  return evaluate(`(() => {
    const btn = [...document.querySelectorAll('button')].find((b) => b.textContent.includes(${JSON.stringify(text)}));
    if (!btn) return 'not-found';
    btn.click();
    return 'clicked';
  })()`);
}

async function main() {
  // 等 DevTools 端口
  let version = null;
  for (let i = 0; i < 40; i++) {
    await sleep(500);
    try {
      version = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
      break;
    } catch {
      /* 继续等 */
    }
  }
  if (!version) throw new Error('Edge DevTools 端口未就绪');
  console.log('[ok] Edge', version.Browser);

  const tab = await (await fetch(`http://127.0.0.1:${PORT}/json/new`, { method: 'PUT' })).json();
  ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve);
    ws.addEventListener('error', reject);
  });
  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const entry = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) entry.reject(new Error(msg.error.message));
      else entry.resolve(msg.result);
    }
  });

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Page.navigate', { url: URL_BASE });
  await waitFor("document.readyState === 'complete' && Boolean(document.querySelector('.badge'))", 90000, '页面加载');
  await sleep(1000);

  const result = { url: URL_BASE, mode: headful ? 'headful' : 'headless', browser: version.Browser, steps: [] };

  // 1. WebGPU 探测器（页面上下文）
  const gpu = await evaluate(`(async () => {
    if (!navigator.gpu) return { available: false, reason: 'navigator.gpu 不存在' };
    try {
      const adapter = await navigator.gpu.requestAdapter();
      return adapter ? { available: true, info: adapter.info ?? {} } : { available: false, reason: 'adapter=null' };
    } catch (e) { return { available: false, reason: String(e) }; }
  })()`);
  console.log('[gpu]', JSON.stringify(gpu));
  result.webgpu = gpu;
  result.steps.push({ step: 'webgpu-detect', gpu });

  // 2. 加载模型
  const clicked = await clickByText('加载模型');
  if (clicked !== 'clicked') throw new Error('没找到“加载模型”按钮');
  const badge = await waitFor(
    `(() => { const b = document.querySelector('.badge')?.textContent ?? ''; return /WebGPU|WASM/.test(b) && !b.includes('未加载') ? b : ''; })()`,
    180000,
    '模型加载',
  );
  console.log('[backend]', badge);
  result.backendBadge = badge;

  const backendDetail = await evaluate(`(() => {
    const items = [...document.querySelectorAll('.kv li')].map((li) => li.textContent);
    return items;
  })()`);
  result.backendDetail = backendDetail;
  result.fallbackNotice = await evaluate(`document.querySelector('.notice')?.textContent ?? null`);

  // 3. 生成查询向量
  await clickByText('生成查询向量');
  const vectorText = await waitFor(
    `(() => { return document.querySelector('[data-testid="vector-result"]')?.textContent?.trim() || ''; })()`,
    120000,
    '向量化',
  );
  console.log('[vector]', vectorText);
  result.vectorText = vectorText;

  // 4. 量化对比（FP32 vs INT8, WASM）
  await clickByText('运行 FP32 vs INT8');
  const compareText = await waitFor(
    `(() => { return document.querySelector('[data-testid="compare-result"]')?.textContent?.trim() || ''; })()`,
    240000,
    '量化对比',
  );
  console.log('[compare]', compareText);
  result.compareText = compareText;

  result.log = await evaluate(`document.querySelector('.log')?.textContent ?? ''`);
  result.ok = true;

  writeFileSync(OUT, JSON.stringify(result, null, 2));
  console.log('[done] 写入 scripts/browser-check-result.json');

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
  .finally(() => setTimeout(() => process.exit(0), 1500));
