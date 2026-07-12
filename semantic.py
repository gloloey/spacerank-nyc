"""
semantic.py — SpaceRank NYC: the "matching by meaning" layer (v2)
=================================================================
Gives the engine two functions:

    scores = similarity(query, [text1, text2, ...])   # floats in [0, 1]
    phrase, kind = explain(query, text)               # WHY it matched

THREE BACKENDS, tried in order at import time. BACKEND says which is live —
the API and UI surface it, so nobody is ever told keyword matching is
"semantic understanding".

1. "embeddings (MiniLM, sentence-transformers)"  — full PyTorch, local dev.
   Real embedding cosine similarity, model downloaded on first use.
   Enable with:  python -m pip install sentence-transformers

2. "embeddings (MiniLM via ONNX, precomputed)"   — the deployed path.
   The SAME MiniLM model, exported to quantized ONNX (~23 MB, in models/,
   produced by the GitHub Action in .github/workflows/embeddings.yml).
   All 400+ description vectors are PREcomputed offline into embeddings.npz
   (~0.6 MB), so at request time we only embed the tenant's QUERY —
   onnxruntime is a ~40 MB pip install, no PyTorch needed on serverless.
   Descriptions not found in the precomputed file (e.g. freshly scraped)
   are embedded on the fly with the same ONNX model — never faked.

3. "tf-idf (keyword overlap — NOT semantic)"     — last-resort fallback.
   From-scratch term-frequency × inverse-document-frequency + cosine.
   It only matches literal shared words, and its label says so.

The embedding math, in one breath: MiniLM turns a text into 384 numbers
whose direction encodes MEANING (learned from ~1B sentence pairs). Mean-pool
the token vectors, L2-normalize, and the dot product of two texts' vectors
IS the cosine of the angle between them: 1 = same meaning, 0 = unrelated.
That's how "sunlit" can match "bright" with zero shared letters.
"""

import hashlib
import math
import os
import re
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODEL_DIR = os.path.join(_HERE, "models")
_EMB_FILE = os.path.join(_HERE, "embeddings.npz")

_MODE = None          # "st" | "onnx" | "tfidf"
BACKEND = None        # human-readable, shown in the API + UI footer


def _hash(text: str) -> str:
    """Stable key for a description — how precomputed vectors are looked up."""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Backend 1: sentence-transformers (full PyTorch — local development)
# ---------------------------------------------------------------------------
try:
    from sentence_transformers import SentenceTransformer

    _ST_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    _MODE, BACKEND = "st", "embeddings (MiniLM, sentence-transformers)"

    def _encode(texts):
        return _ST_MODEL.encode(list(texts), convert_to_numpy=True,
                                normalize_embeddings=True)
except Exception:
    pass

# ---------------------------------------------------------------------------
# Backend 2: ONNX + precomputed vectors (the deployed path)
# ---------------------------------------------------------------------------
if _MODE is None:
    try:
        import numpy as _np
        import onnxruntime as _ort
        from tokenizers import Tokenizer as _Tokenizer

        _tok = _Tokenizer.from_file(os.path.join(_MODEL_DIR, "tokenizer.json"))
        _tok.enable_truncation(max_length=256)
        _sess = _ort.InferenceSession(
            os.path.join(_MODEL_DIR, "model_quantized.onnx"),
            providers=["CPUExecutionProvider"])
        _needs_type_ids = any(i.name == "token_type_ids"
                              for i in _sess.get_inputs())
        _pre = _np.load(_EMB_FILE, allow_pickle=False)
        _PRECOMPUTED = dict(zip(_pre["hashes"].tolist(),
                                _pre["vectors"].astype(_np.float32)))
        _MODE = "onnx"
        BACKEND = (f"embeddings (MiniLM via ONNX, "
                   f"{len(_PRECOMPUTED)} descriptions precomputed)")

        def _encode(texts):
            """Tokenize -> run the transformer -> mean-pool -> L2-normalize.
            This mirrors exactly what sentence-transformers does internally."""
            encs = [_tok.encode(t if t.strip() else " ") for t in texts]
            width = max(len(e.ids) for e in encs)
            ids = _np.zeros((len(encs), width), dtype=_np.int64)
            mask = _np.zeros_like(ids)
            for i, e in enumerate(encs):
                ids[i, :len(e.ids)] = e.ids
                mask[i, :len(e.ids)] = 1
            feeds = {"input_ids": ids, "attention_mask": mask}
            if _needs_type_ids:
                feeds["token_type_ids"] = _np.zeros_like(ids)
            hidden = _sess.run(None, feeds)[0]           # (batch, tokens, 384)
            m = mask[..., None].astype(hidden.dtype)
            emb = (hidden * m).sum(axis=1) / m.sum(axis=1).clip(min=1e-9)
            norms = _np.linalg.norm(emb, axis=1, keepdims=True).clip(min=1e-9)
            return emb / norms
    except Exception:
        _MODE = None

# ---------------------------------------------------------------------------
# Backend 3: TF-IDF fallback (honest label: keyword overlap, NOT semantic)
# ---------------------------------------------------------------------------
if _MODE is None:
    _MODE = "tfidf"
    BACKEND = "tf-idf (keyword overlap — NOT semantic)"

_STOP = set("the a an and or of to in on at for with is are was were be been "
            "this that it its as by from has have had all also".split())


def _tokens(text: str):
    return [w for w in re.findall(r"[a-z]+", text.lower())
            if w not in _STOP and len(w) > 2]


def _tfidf_similarity(query, texts):
    docs = [_tokens(t) for t in texts]
    q = _tokens(query)
    n = len(docs) + 1
    df = Counter(w for d in docs for w in set(d))
    idf = {w: math.log(n / (1 + c)) + 1 for w, c in df.items()}

    def vec(tokens):
        tf = Counter(tokens)
        total = max(1, len(tokens))
        return {w: (c / total) * idf.get(w, 1.0) for w, c in tf.items()}

    def cosine(a, b):
        num = sum(a[w] * b[w] for w in set(a) & set(b))
        den = (math.sqrt(sum(x * x for x in a.values()))
               * math.sqrt(sum(x * x for x in b.values())))
        return num / den if den else 0.0

    qv = vec(q)
    return [cosine(qv, vec(d)) for d in docs]


# ---------------------------------------------------------------------------
# The public API — same two functions whatever the backend
# ---------------------------------------------------------------------------
def similarity(query: str, texts: list) -> list:
    """One score in [0, 1] per text: how close is its MEANING to the query
    (embedding backends) or its keyword profile (tf-idf fallback)."""
    if _MODE == "tfidf":
        return _tfidf_similarity(query, texts)

    import numpy as np
    q = _encode([query])[0]
    if _MODE == "onnx":
        # precomputed where possible; embed the stragglers on the fly
        vecs, missing, where = [], [], []
        for i, t in enumerate(texts):
            v = _PRECOMPUTED.get(_hash(t))
            vecs.append(v)
            if v is None:
                missing.append(t if t.strip() else " ")
                where.append(i)
        if missing:
            fresh = _encode(missing)
            for j, i in enumerate(where):
                vecs[i] = fresh[j]
        matrix = np.vstack(vecs)
    else:                                    # "st": encode everything live
        matrix = _encode([t if t.strip() else " " for t in texts])
    cos = matrix @ q                         # normalized -> dot = cosine
    return [float((c + 1) / 2) for c in cos]  # [-1,1] -> [0,1]


def explain(query: str, text: str):
    """(phrase, kind): the piece of `text` that drove the score.
    Embedding backends -> the closest-in-MEANING sentence ("phrase").
    TF-IDF            -> the literal shared keywords ("keywords")."""
    if not query.strip() or not text.strip():
        return "", "none"
    if _MODE in ("st", "onnx"):
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text)
                     if len(s.strip()) > 20][:40]
        if not sentences:
            return "", "none"
        scores = similarity(query, sentences)
        best = sentences[max(range(len(scores)), key=scores.__getitem__)]
        return (best[:110] + "…") if len(best) > 110 else best, "phrase"
    q_tokens = _tokens(query)
    t_tokens = set(_tokens(text))
    shared = [w for w in dict.fromkeys(q_tokens) if w in t_tokens]
    return ", ".join(shared[:4]), "keywords"
