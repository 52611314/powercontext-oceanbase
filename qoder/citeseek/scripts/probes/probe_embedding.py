import json
import sys
import time

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MODEL_DIR = r"D:\oceanbase\powercontext-oceanbase\stepfun\knowledge-base\public\models\bge-small-zh-v1.5"


def load():
    tok = Tokenizer.from_file(f"{MODEL_DIR}/tokenizer.json")
    tok.enable_truncation(max_length=512)
    tok.enable_padding(pad_id=0, pad_token="[PAD]")
    sess = ort.InferenceSession(
        f"{MODEL_DIR}/onnx/model.onnx", providers=["CPUExecutionProvider"]
    )
    return tok, sess


def embed(tok, sess, texts):
    encs = tok.encode_batch(texts)
    ids = np.array([e.ids for e in encs], dtype=np.int64)
    mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
    feeds = {"input_ids": ids, "attention_mask": mask}
    names = {i.name for i in sess.get_inputs()}
    if "token_type_ids" in names:
        feeds["token_type_ids"] = np.zeros_like(ids)
    out = sess.run(None, feeds)[0]
    cls = out[:, 0, :]
    norm = np.linalg.norm(cls, axis=1, keepdims=True)
    return cls / np.clip(norm, 1e-12, None)


def main():
    tok, sess = load()
    print("inputs:", [(i.name, i.shape) for i in sess.get_inputs()])
    print("outputs:", [(o.name, o.shape) for o in sess.get_outputs()])
    texts = [
        "注意力机制如何降低长文本推理的显存占用？",
        "KV cache 压缩可以显著减少大模型推理的显存开销。",
        "今天食堂的饭菜很一般。",
        "efficient attention reduces memory for long context",
    ]
    t0 = time.perf_counter()
    vecs = embed(tok, sess, texts)
    dt = (time.perf_counter() - t0) * 1000
    print("dim:", vecs.shape, "batch_ms:", round(dt, 1))
    sim = vecs @ vecs.T
    for i, row in enumerate(sim):
        print(i, [round(float(x), 3) for x in row])
    result = {
        "dim": int(vecs.shape[1]),
        "batch_ms": round(dt, 1),
        "sim_q_first": round(float(sim[0, 1]), 4),
        "sim_q_unrelated": round(float(sim[0, 2]), 4),
        "pass": bool(sim[0, 1] > sim[0, 2] + 0.1 and vecs.shape[1] == 512),
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
