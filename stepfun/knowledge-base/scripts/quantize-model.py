"""生成 INT8 与 FP16 模型，并报告体积、压缩比与数值一致性。

运行：.build-env/kb-venv/Scripts/python.exe scripts/quantize-model.py
输入：public/models/bge-small-zh-v1.5/onnx/model.onnx (FP32)
输出：同目录 model_int8_self.onnx、model_fp16_self.onnx
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import onnx
from onnxconverter_common import float16
from onnxruntime.quantization import QuantType, quantize_dynamic
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "public" / "models" / "bge-small-zh-v1.5" / "onnx"
FP32 = MODEL_DIR / "model.onnx"
INT8 = MODEL_DIR / "model_int8_self.onnx"
FP16 = MODEL_DIR / "model_fp16_self.onnx"
REPORT = ROOT / "scripts" / "model-quantize-result.json"


def mb(path: Path) -> float:
    return round(path.stat().st_size / 1024 / 1024, 2)


def make_inputs(dim_batch: int = 2, seq: int = 16):
    rng = np.random.default_rng(42)
    ids = rng.integers(1, 20000, size=(dim_batch, seq), dtype=np.int64)
    mask = np.ones((dim_batch, seq), dtype=np.int64)
    types = np.zeros((dim_batch, seq), dtype=np.int64)
    return {"input_ids": ids, "attention_mask": mask, "token_type_ids": types}


def run(model_path: Path, feeds: dict) -> np.ndarray:
    sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    names = [i.name for i in sess.get_inputs()]
    inputs = {k: v for k, v in feeds.items() if k in names}
    out = sess.run(None, inputs)[0]
    # 取均值池化后的句向量，便于跨模型比较
    return np.asarray(out).mean(axis=1)


def cosine(a: np.ndarray, b: np.ndarray) -> list[float]:
    a = a / np.linalg.norm(a, axis=1, keepdims=True)
    b = b / np.linalg.norm(b, axis=1, keepdims=True)
    return [round(float(x), 6) for x in (a * b).sum(axis=1)]


def main() -> None:
    assert FP32.is_file(), f"缺少 FP32 模型: {FP32}"
    result: dict = {"source": str(FP32), "sizes_mb": {"fp32": mb(FP32)}}

    feeds = make_inputs()
    base = run(FP32, feeds)

    started = time.time()
    quantize_dynamic(str(FP32), str(INT8), weight_type=QuantType.QInt8)
    result["int8_build_seconds"] = round(time.time() - started, 1)
    result["sizes_mb"]["int8_self"] = mb(INT8)
    result["compression_int8"] = round(mb(FP32) / mb(INT8), 2)
    result["cosine_int8_vs_fp32"] = cosine(base, run(INT8, feeds))

    started = time.time()
    fp32_model = onnx.load(str(FP32))
    fp16_model = float16.convert_float_to_float16(fp32_model, keep_io_types=True)
    onnx.save(fp16_model, str(FP16))
    result["fp16_build_seconds"] = round(time.time() - started, 1)
    result["sizes_mb"]["fp16_self"] = mb(FP16)
    result["compression_fp16"] = round(mb(FP32) / mb(FP16), 2)
    try:
        result["cosine_fp16_vs_fp32"] = cosine(base, run(FP16, feeds))
    except Exception as exc:  # WebGPU 用 fp16，CPU EP 可能不完全支持全部算子
        result["cosine_fp16_vs_fp32_error"] = str(exc)

    # 官方量化文件（若已下载）作为参照
    for name, path in (("int8_official", MODEL_DIR / "model_quantized.onnx"), ("fp16_official", MODEL_DIR / "model_fp16.onnx")):
        if path.is_file():
            result["sizes_mb"][name] = mb(path)
            result["compression_" + name] = round(mb(FP32) / mb(path), 2)
            try:
                result["cosine_" + name + "_vs_fp32"] = cosine(base, run(path, feeds))
            except Exception as exc:
                result["cosine_" + name + "_vs_fp32_error"] = str(exc)

    REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
