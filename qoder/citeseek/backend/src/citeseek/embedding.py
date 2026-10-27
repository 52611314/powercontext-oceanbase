from __future__ import annotations

import threading

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from .config import settings


class Embedder:
    """bge-small-zh-v1.5: CLS pooling + L2 normalize, 512 dims."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokenizer: Tokenizer | None = None
        self._session: ort.InferenceSession | None = None
        self._dim = 0

    def load(self) -> None:
        with self._lock:
            if self._session is not None:
                return
            model_dir = settings.model_dir
            tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
            tokenizer.enable_truncation(max_length=512)
            tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")

            quantized = model_dir / "onnx" / "model_quantized.onnx"
            onnx_path = quantized if quantized.exists() else model_dir / "onnx" / "model.onnx"
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            session = ort.InferenceSession(
                str(onnx_path), sess_options=options, providers=["CPUExecutionProvider"]
            )
            self._tokenizer = tokenizer
            self._session = session
            self._dim = int(session.get_outputs()[0].shape[-1])

    @property
    def ready(self) -> bool:
        return self._session is not None

    @property
    def dim(self) -> int:
        self.load()
        return self._dim

    def encode(self, texts: list[str]) -> np.ndarray:
        self.load()
        assert self._tokenizer is not None and self._session is not None
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)

        vectors: list[np.ndarray] = []
        input_names = {item.name for item in self._session.get_inputs()}
        with self._lock:
            for start in range(0, len(texts), settings.embed_batch):
                batch = texts[start : start + settings.embed_batch]
                encodings = self._tokenizer.encode_batch(batch)
                ids = np.array([e.ids for e in encodings], dtype=np.int64)
                mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
                feeds = {"input_ids": ids, "attention_mask": mask}
                if "token_type_ids" in input_names:
                    feeds["token_type_ids"] = np.zeros_like(ids)
                hidden = self._session.run(None, feeds)[0][:, 0, :]
                norm = np.clip(np.linalg.norm(hidden, axis=1, keepdims=True), 1e-12, None)
                vectors.append(hidden / norm)
        return np.vstack(vectors).astype(np.float32)


embedder = Embedder()


def vector_literal(vector) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in vector) + "]"
