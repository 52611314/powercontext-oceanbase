/**
 * 下载 bge-small-zh-v1.5 的 ONNX 模型与分词文件到 public/models 下。
 *
 * 网络说明：本项目所在网络直连 HF 约 0.03 MB/s；设置 MODEL_SOCKS 后走本机 SOCKS5
 * （本机实测 0.2 MB/s），下载脚本用 node:https 手动跟随 CDN 重定向。
 *
 * 运行：
 *   MODEL_SOCKS=socks5h://127.0.0.1:1080 node scripts/download-model.mjs
 */
import { createWriteStream, existsSync, mkdirSync, statSync } from 'node:fs';
import { pipeline } from 'node:stream/promises';
import https from 'node:https';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SocksProxyAgent } from 'socks-proxy-agent';

const BASE = 'https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/main/';
const SOCKS = process.env.MODEL_SOCKS ?? '';
const agent = SOCKS ? new SocksProxyAgent(SOCKS) : undefined;
const ROOT = fileURLToPath(new URL('../public/models/bge-small-zh-v1.5/', import.meta.url));

const FILES = [
  'config.json',
  'tokenizer.json',
  'tokenizer_config.json',
  'special_tokens_map.json',
  'vocab.txt',
  'onnx/model.onnx',
  'onnx/model_fp16.onnx',
  // 官方 INT8 仅用于与自量化结果对照，默认不下载：
  // 'onnx/model_quantized.onnx',
];

const mb = (n) => `${(n / 1024 / 1024).toFixed(2)} MB`;

function openUrl(url, headers = {}, redirects = 0) {
  return new Promise((resolve, reject) => {
    const req = https.get(
      url,
      { agent, timeout: 120000, headers: { 'User-Agent': 'seekdb-mini-kb/0.1', ...headers } },
      (res) => {
        if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location && redirects < 6) {
          res.resume();
          resolve(openUrl(new URL(res.headers.location, url).href, headers, redirects + 1));
          return;
        }
        resolve(res);
      },
    );
    req.on('error', reject);
    req.on('timeout', () => req.destroy(new Error('timeout')));
  });
}

async function download(file, attempt = 1) {
  const target = join(ROOT, file);
  mkdirSync(dirname(target), { recursive: true });

  const probe = await openUrl(BASE + file, { Range: 'bytes=0-0' });
  const range = probe.headers['content-range'];
  const total = Number(range ? range.split('/')[1] : probe.headers['content-length'] ?? 0);
  probe.destroy();

  if (!probe.statusCode || probe.statusCode >= 400) throw new Error(`probe ${file} -> ${probe.statusCode}`);
  if (existsSync(target) && statSync(target).size === total && total > 0) {
    console.log(`[skip] ${file} 已完整 ${mb(total)}`);
    return;
  }

  console.log(`[get ] ${file} ${mb(total)} (第 ${attempt} 次${SOCKS ? '，经 SOCKS' : '，直连'})`);
  const started = Date.now();
  const res = await openUrl(BASE + file);
  if (res.statusCode >= 400) throw new Error(`GET ${file} -> ${res.statusCode}`);

  let received = 0;
  let lastPct = -1;
  res.on('data', (chunk) => {
    received += chunk.length;
    const pct = total ? Math.floor((received / total) * 100) : -1;
    if (pct >= lastPct + 10) {
      lastPct = pct;
      console.log(`       ${file} ${pct}% ${mb(received)}`);
    }
  });
  await pipeline(res, createWriteStream(target));

  const size = statSync(target).size;
  const seconds = (Date.now() - started) / 1000;
  console.log(`[done] ${file} ${mb(size)} ${seconds.toFixed(1)}s (${(size / 1048576 / seconds).toFixed(2)} MB/s)`);
  if (total && size !== total) throw new Error(`${file} 大小不符: ${size} != ${total}`);
}

let failed = 0;
for (const file of FILES) {
  let ok = false;
  for (let attempt = 1; attempt <= 3 && !ok; attempt++) {
    try {
      await download(file, attempt);
      ok = true;
    } catch (err) {
      console.error(`[retry] ${file}: ${err.message}`);
      await new Promise((r) => setTimeout(r, 1500 * attempt));
    }
  }
  if (!ok) failed += 1;
}

if (failed) {
  console.error(`[fail] ${failed} 个文件下载失败`);
  process.exit(1);
}
console.log('[ok] 全部文件就绪');
