"""
semantic.py — SpaceRank NYC: the "matching by meaning" layer
=============================================================
Gives the engine ONE function it can rely on:

    scores = similarity(query_text, [description1, description2, ...])
    # -> list of floats in [0, 1], one per description

TWO BACKENDS, chosen automatically:

1. sentence-transformers (the real headline tech, used when installed):
   Each text becomes an EMBEDDING — a vector of ~384 numbers that encodes its
   MEANING, learned by a neural network from billions of sentences. Similar
   meanings end up as nearby vectors, so "bright, good foot traffic" lands
   close to "sunlit, busy pedestrian street" even with zero shared words.
   Similarity = cosine of the angle between the two vectors.

   Install on your machine (one-time, ~1-2 GB with PyTorch):
       python -m pip install sentence-transformers

2. TF-IDF fallback (pure Python, always works, no installs):
   A classic technique: each text becomes a vector of WORD WEIGHTS, where a
   word counts more if it's frequent in this text but rare across all texts
   (that's Term-Frequency × Inverse-Document-Frequency). Same cosine math,
   but it can only match literal shared words — it is NOT semantic. It keeps
   the pipeline runnable anywhere and is a great baseline to compare against.

The engine doesn't care which backend produced the score — that's the point
of keeping this in its own module.
"""

import math
import re
from collections import Counter

# ---------------------------------------------------------------------------
# Backend 1: real embeddings (preferred)
# ---------------------------------------------------------------------------
try:
    from sentence_transformers import SentenceTransformer, util
    _MODEL = SentenceTransformer("all-MiniLM-L6-v2")   # small, fast, solid
    BACKEND = "sentence-transformers"

    def similarity(query: str, texts: list[str]) -> list[float]:
        """Embed everything once, then cosine-compare query vs each text."""
        q_vec = _MODEL.encode(query, convert_to_tensor=True)
        t_vecs = _MODEL.encode(texts, convert_to_tensor=True)
        cos = util.cos_sim(q_vec, t_vecs)[0]           # values in [-1, 1]
        return [(float(c) + 1) / 2 for c in cos]       # rescale to [0, 1]

# ---------------------------------------------------------------------------
# Backend 2: TF-IDF + cosine, implemented from scratch (no dependencies)
# ---------------------------------------------------------------------------
except ImportError:
    BACKEND = "tf-idf (fallback — install sentence-transformers for real embeddings)"

    _STOP = set("the a an and or of to in on at for with is are was were be been "
                "this that it its as by from has have had all also".split())

    def _tokens(text: str) -> list[str]:
        """Lowercase words, letters only, minus filler words."""
        return [w for w in re.findall(r"[a-z]+", text.lower())
                if w not in _STOP and len(w) > 2]

    def similarity(query: str, texts: list[str]) -> list[float]:
        docs = [_tokens(t) for t in texts]
        q = _tokens(query)

        # idf: log(N / how many docs contain the word) — rare words weigh more
        n = len(docs) + 1
        df = Counter()
        for d in docs:
            for w in set(d):
                df[w] += 1
        idf = {w: math.log(n / (1 + c)) + 1 for w, c in df.items()}

        def vec(tokens):
            tf = Counter(tokens)
            total = max(1, len(tokens))
            return {w: (c / total) * idf.get(w, 1.0) for w, c in tf.items()}

        def cosine(a, b):
            shared = set(a) & set(b)
            num = sum(a[w] * b[w] for w in shared)
            den = (math.sqrt(sum(x * x for x in a.values()))
                   * math.sqrt(sum(x * x for x in b.values())))
            return num / den if den else 0.0

        qv = vec(q)
        return [cosine(qv, vec(d)) for d in docs]
