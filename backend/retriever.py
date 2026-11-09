import math
import re
from collections import Counter


def _tok(s: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", s.lower())


class Retriever:
    """Embeddings (fastembed + bge-small) if installed, else BM25 fallback."""

    def __init__(self):
        self.rules, self.mode, self._model = [], "bm25", None
        try:
            from fastembed import TextEmbedding

            self._model = TextEmbedding("BAAI/bge-small-en-v1.5")
            self.mode = "embeddings"
        except Exception:
            pass

    def index(self, rules):
        self.rules = list(rules)
        if not self.rules:
            return
        if self._model:
            import numpy as np

            v = np.array(list(self._model.embed([r.text for r in self.rules])))
            self._vecs = v / np.linalg.norm(v, axis=1, keepdims=True)
        else:
            self._docs = [_tok(r.text) for r in self.rules]
            self._avgdl = sum(map(len, self._docs)) / len(self._docs)
            self._df = Counter(t for d in self._docs for t in set(d))

    def search(self, query: str, k: int = 4):
        if not self.rules:
            return []
        if self._model:
            import numpy as np

            q = np.array(list(self._model.embed([query])))[0]
            q = q / np.linalg.norm(q)
            scores = self._vecs @ q
        else:
            qt, n = _tok(query), len(self._docs)
            scores = []
            for d in self._docs:
                tf, s = Counter(d), 0.0
                for t in qt:
                    if t in tf:
                        idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                        s += idf * tf[t] * 2.5 / (tf[t] + 1.5 * (0.25 + 0.75 * len(d) / self._avgdl))
                scores.append(s)
        order = sorted(range(len(self.rules)), key=lambda i: -scores[i])[:k]
        return [self.rules[i] for i in order]
