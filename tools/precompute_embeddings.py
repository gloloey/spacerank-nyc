"""
tools/precompute_embeddings.py — the OFFLINE half of the embedding backend
==========================================================================
Runs where PyTorch is cheap (a GitHub Actions runner, or any dev machine
with sentence-transformers installed) and produces the two artifacts the
LIVE site needs to do real embedding similarity without PyTorch:

  embeddings.npz            one 384-dim vector per unique space description
                            (sha1(description) -> vector; ~0.6 MB for ~400)
  models/model_quantized.onnx   the SAME MiniLM model, quantized ONNX (~23 MB)
  models/tokenizer.json         its tokenizer

At request time the deployed site only has to embed the tenant's QUERY —
onnxruntime handles that in milliseconds with no torch dependency.

Descriptions are embedded with the fp32 sentence-transformers model; the
query is embedded with the int8-quantized ONNX export of the same model.
Quantization shifts cosines by <0.01 — irrelevant at ranking granularity.

Run:  python tools/precompute_embeddings.py     (from the repo root)
CI :  .github/workflows/embeddings.yml runs this on every data change.
"""

import hashlib
import os
import shutil
import sys

import numpy as np
import pandas as pd
from huggingface_hub import hf_hub_download
from sentence_transformers import SentenceTransformer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(ROOT, "spaces_clean.csv")
OUT_NPZ = os.path.join(ROOT, "embeddings.npz")
MODEL_DIR = os.path.join(ROOT, "models")

ST_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
ONNX_REPO = "Xenova/all-MiniLM-L6-v2"          # community ONNX export of the same weights


def sha1(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


def main():
    # ---- 1. embed every unique description (fp32, normalized) --------------
    df = pd.read_csv(CSV)
    descs = sorted({d for d in df["description"].dropna() if d.strip()})
    print(f"{len(descs)} unique descriptions to embed")

    model = SentenceTransformer(ST_MODEL)
    vectors = model.encode(descs, normalize_embeddings=True,
                           show_progress_bar=True).astype(np.float32)
    hashes = np.array([sha1(d) for d in descs], dtype="U40")
    np.savez_compressed(OUT_NPZ, hashes=hashes, vectors=vectors)
    print(f"wrote {OUT_NPZ} ({os.path.getsize(OUT_NPZ) / 1e6:.2f} MB, "
          f"{vectors.shape[0]}x{vectors.shape[1]})")

    # ---- 2. fetch the quantized ONNX export for query-time encoding --------
    os.makedirs(MODEL_DIR, exist_ok=True)
    onnx_src = hf_hub_download(ONNX_REPO, "onnx/model_quantized.onnx")
    tok_src = hf_hub_download(ONNX_REPO, "tokenizer.json")
    shutil.copy(onnx_src, os.path.join(MODEL_DIR, "model_quantized.onnx"))
    shutil.copy(tok_src, os.path.join(MODEL_DIR, "tokenizer.json"))
    print(f"models/ ready ({os.path.getsize(os.path.join(MODEL_DIR, 'model_quantized.onnx')) / 1e6:.1f} MB onnx)")

    # ---- 3. sanity: fp32 vs quantized must agree on an easy meaning pair ---
    sys.path.insert(0, ROOT)
    q = model.encode(["bright sunlit office"], normalize_embeddings=True)[0]
    a = model.encode(["space flooded with natural light"], normalize_embeddings=True)[0]
    b = model.encode(["windowless basement storage"], normalize_embeddings=True)[0]
    assert float(q @ a) > float(q @ b), "embedding sanity check failed"
    print(f"sanity: 'bright sunlit office' ~ 'natural light' cos={float(q @ a):.3f} "
          f"vs 'windowless basement' cos={float(q @ b):.3f}  OK")


if __name__ == "__main__":
    main()
