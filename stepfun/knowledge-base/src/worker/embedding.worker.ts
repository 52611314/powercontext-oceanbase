/**
 * 端侧 embedding Worker。
 *
 * - 优先 WebGPU（fp16），失败或不可用时降级 WASM（q8/INT8）
 * - 模型文件全部从本地 /models 加载，不访问任何云端推理 API
 * - 文档向量与查询向量都在这里计算
 */
import { env, pipeline, type FeatureExtractionPipeline } from '@huggingface/transformers';

env.allowLocalModels = true;
env.allowRemoteModels = false;
env.localModelPath = '/models/';

const MODEL_ID = 'bge-small-zh-v1.5';
const QUERY_INSTRUCTION = '为这个句子生成表示以用于检索相关文章：';

type Device = 'webgpu' | 'wasm';
type Dtype = 'fp16' | 'q8' | 'fp32';

type Backend = {
  device: Device;
  dtype: Dtype;
  adapter?: { vendor?: string; architecture?: string; description?: string } | null;
  reason: string;
  loadMs?: number;
};

type LoadMessage = { type: 'init'; prefer?: 'auto' | 'webgpu' | 'wasm'; dtype?: Dtype };
type EmbedMessage = { type: 'embed'; id: number; texts: string[]; kind?: 'doc' | 'query' };
type WorkerMessage = LoadMessage | EmbedMessage | { type: 'status' };

let extractor: FeatureExtractionPipeline | null = null;
let backend: Backend | null = null;
let loading: Promise<Backend> | null = null;

function post(message: Record<string, unknown>): void {
  self.postMessage(message);
}

async function detectWebGPU(): Promise<{ ok: boolean; adapter?: Backend['adapter']; reason: string }> {
  const gpu = (navigator as Navigator & { gpu?: GPU }).gpu;
  if (!gpu) return { ok: false, reason: 'navigator.gpu 不存在' };
  try {
    const adapter = await gpu.requestAdapter();
    if (!adapter) return { ok: false, reason: 'requestAdapter() 返回 null（无可用 GPU 适配器）' };
    const info = (adapter as GPUAdapter & { info?: Record<string, string> }).info ?? {};
    return {
      ok: true,
      adapter: {
        vendor: info.vendor ?? '',
        architecture: info.architecture ?? '',
        description: info.description ?? '',
      },
      reason: 'WebGPU 可用',
    };
  } catch (err) {
    return { ok: false, reason: `requestAdapter 异常: ${(err as Error).message}` };
  }
}

async function load(prefer: 'auto' | 'webgpu' | 'wasm', dtypeOverride?: Dtype): Promise<Backend> {
  if (extractor && backend) return backend;
  if (loading) return loading;

  loading = (async (): Promise<Backend> => {
    let device: Device = 'wasm';
    let reason = prefer === 'wasm' ? '用户指定 WASM' : 'WebGPU 不可用';
    let adapter: Backend['adapter'] = null;

    if (prefer !== 'wasm') {
      const gpu = await detectWebGPU();
      if (gpu.ok) {
        device = 'webgpu';
        reason = gpu.reason;
        adapter = gpu.adapter ?? null;
      } else {
        reason = gpu.reason;
      }
    }

    let dtype: Dtype = dtypeOverride ?? (device === 'webgpu' ? 'fp16' : 'q8');
    const start = performance.now();

    try {
      extractor = await pipeline('feature-extraction', MODEL_ID, {
        device,
        dtype,
        progress_callback: (progress: unknown) => post({ type: 'progress', progress }),
      });
    } catch (err) {
      if (device === 'webgpu') {
        const message = (err as Error).message;
        post({ type: 'fallback', from: 'webgpu', reason: message });
        device = 'wasm';
        dtype = 'q8';
        reason = `WebGPU 初始化失败: ${message}`;
        adapter = null;
        extractor = await pipeline('feature-extraction', MODEL_ID, {
          device,
          dtype,
          progress_callback: (progress: unknown) => post({ type: 'progress', progress }),
        });
      } else {
        throw err;
      }
    }

    backend = { device, dtype, adapter, reason, loadMs: Math.round(performance.now() - start) };
    post({ type: 'ready', backend });
    return backend;
  })();

  try {
    return await loading;
  } finally {
    loading = null;
  }
}

async function embed(texts: string[], kind: 'doc' | 'query'): Promise<{ vectors: number[][]; embedMs: number }> {
  if (!extractor) throw new Error('模型尚未加载');
  const input = kind === 'query' ? texts.map((text) => QUERY_INSTRUCTION + text) : texts;
  const start = performance.now();
  const output = await extractor(input, { pooling: 'mean', normalize: true });
  const vectors = output.tolist() as number[][];
  return { vectors, embedMs: Math.round(performance.now() - start) };
}

self.onmessage = async (event: MessageEvent<WorkerMessage>) => {
  const msg = event.data;
  try {
    if (msg.type === 'init') {
      const info = await load(msg.prefer ?? 'auto', msg.dtype);
      post({ type: 'inited', backend: info });
      return;
    }
    if (msg.type === 'embed') {
      const { vectors, embedMs } = await embed(msg.texts, msg.kind ?? 'doc');
      post({ type: 'result', id: msg.id, vectors, embedMs });
      return;
    }
    if (msg.type === 'status') {
      post({ type: 'status', backend, loaded: Boolean(extractor) });
      return;
    }
    post({ type: 'error', message: `未知消息类型: ${(msg as { type: string }).type}` });
  } catch (err) {
    post({
      type: 'error',
      id: 'id' in msg ? msg.id : undefined,
      message: (err as Error)?.message ?? String(err),
      stack: (err as Error)?.stack,
    });
  }
};
