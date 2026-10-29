"""L1: retrieval. TF-IDF is always available; vectors (Chroma + sentence-transformers via
LangChain) are an optional second signal fused with reciprocal-rank fusion.

Why hybrid: TF-IDF is exact on provision wording ("15 calendar days") and clause ids; vectors
help the lay -> medical gap. If the vector stack is missing the system degrades to TF-IDF and
says so (backend name is recorded in every trace)."""
import json, re
from collections import OrderedDict
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer


class VectorBackend:
    """Chroma collection driven through LangChain. Embeddings are keyed by (chunk hash, model)."""

    def __init__(self, path, model_name):
        from langchain_chroma import Chroma
        from langchain_core.embeddings import Embeddings
        from sentence_transformers import SentenceTransformer
        from chromadb.config import Settings as ChromaSettings
        st = SentenceTransformer(model_name)

        class _Emb(Embeddings):
            def embed_documents(self, texts): return st.encode(texts, normalize_embeddings=True).tolist()
            def embed_query(self, text): return st.encode([text], normalize_embeddings=True)[0].tolist()

        self.model_name = model_name
        self.store = Chroma(collection_name="pv_" + re.sub(r"\W", "_", model_name)[-40:],
                            embedding_function=_Emb(), persist_directory=path,
                            client_settings=ChromaSettings(anonymized_telemetry=False))

    def sync(self, chunks, log=print):
        have = self.store.get(include=["metadatas"])
        known = {i: (m or {}).get("hash") for i, m in zip(have["ids"], have["metadatas"])}
        todo = [c for c in chunks if known.get(c["id"]) != c["hash"]]
        gone = [i for i in known if i not in {c["id"] for c in chunks}]
        if gone:
            self.store.delete(ids=gone)
        for i in range(0, len(todo), 128):
            batch = todo[i:i + 128]
            self.store.add_texts([c["text"] for c in batch],
                                 metadatas=[{"hash": c["hash"], "source": c["source"], "page": c["page"]} for c in batch],
                                 ids=[c["id"] for c in batch])
        log(f"embedded {len(todo)} new/changed chunks, removed {len(gone)}, model={self.model_name}")
        return len(chunks)

    def search(self, query, k, source=None):
        flt = {"source": source} if source else None
        return [(d.metadata and None, d) for d in []] or [
            (doc, score) for doc, score in self.store.similarity_search_with_score(query, k=k, filter=flt)]


class Index:
    def __init__(self, index_dir="data/index", vectors=None):
        d = Path(index_dir)
        self.manifest = json.loads((d / "manifest.json").read_text()) if (d / "manifest.json").exists() else {}
        self.chunks = [json.loads(l) for l in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        self.by_id = {c["id"]: c for c in self.chunks}
        self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", sublinear_tf=True, max_features=120000)
        self.matrix = self.vectorizer.fit_transform([c["text"] for c in self.chunks]) if self.chunks else None
        self.vectors = vectors
        self.version = self.manifest.get("corpus_version", "none")
        self._cache = OrderedDict()
        self.cache_hits = self.cache_misses = 0

    @property
    def backend(self):
        return "hybrid(tfidf+vectors)" if self.vectors else "tfidf"

    def _lexical(self, query, k, source):
        if self.matrix is None:
            return []
        scores = (self.matrix @ self.vectorizer.transform([query]).T).toarray().ravel()
        order = scores.argsort()[::-1]
        out = []
        for i in order:
            if scores[i] <= 0:
                break
            if source and self.chunks[i]["source"] != source:
                continue
            out.append((self.chunks[i], float(scores[i])))
            if len(out) == k:
                break
        return out

    def search(self, query, k=5, source=None):
        key = (query, k, source, self.version)
        if key in self._cache:
            self._cache.move_to_end(key)
            self.cache_hits += 1
            return [dict(p) for p in self._cache[key]]
        self.cache_misses += 1
        lex = self._lexical(query, k * 3, source)
        ranked = {c["id"]: 2.0 / (60 + r) for r, (c, _) in enumerate(lex)}
        if self.vectors:
            try:
                for r, (doc, _s) in enumerate(self.vectors.search(query, k * 3, source)):
                    cid = doc.id if hasattr(doc, "id") and doc.id else None
                    if cid in self.by_id:
                        ranked[cid] = ranked.get(cid, 0) + 1.0 / (60 + r)
            except Exception:  # noqa: vector path must never take the run down
                pass
        top = sorted(ranked.items(), key=lambda kv: kv[1], reverse=True)[:k]
        res = [{**self.by_id[cid], "score": round(sc, 5)} for cid, sc in top]
        self._cache[key] = res
        if len(self._cache) > 512:
            self._cache.popitem(last=False)
        return [dict(p) for p in res]

    def lookup_clause(self, identifier, source=None, limit=3):
        pat = re.compile(r"(?<![\d.])" + re.escape(identifier) + r"(?![\d])")
        hits = [c for c in self.chunks if (not source or c["source"] == source) and pat.search(c["text"])]
        return [dict(c, score=1.0) for c in hits[:limit]]

    def top_terms(self, passages, n=8, exclude=()):
        """Distinctive terms of passages already read: the basis for a *derived* follow-up query."""
        if self.matrix is None or not passages:
            return []
        names = self.vectorizer.get_feature_names_out()
        row = self.vectorizer.transform([" ".join(p["text"] for p in passages)]).toarray().ravel()
        ex = {e.lower() for e in exclude}
        out = []
        for i in row.argsort()[::-1]:
            if row[i] <= 0 or len(out) >= n:
                break
            t = names[i]
            if any(tok in ex for tok in t.lower().split()) or t.isdigit() or any(c.isdigit() for c in t):
                continue
            out.append(t)
        return out


try:  # LangChain adapter so the agent's retrieval is a LangChain component (L1)
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever

    class PVRetriever(BaseRetriever):
        index: object
        source: str | None = None
        k: int = 5

        def _get_relevant_documents(self, query, *, run_manager=None):
            return [Document(page_content=p["text"], id=p["id"],
                             metadata={"source": p["source"], "page": p["page"], "score": p["score"]})
                    for p in self.index.search(query, self.k, self.source)]
except Exception:  # pragma: no cover  (langchain-core not installed in the minimal test env)
    PVRetriever = None

_INDEX = {}


def get_index(index_dir=None, use_vectors=None):
    from pv.config import settings
    d = index_dir or settings.index_dir
    if d not in _INDEX:
        vec = None
        if (settings.embeddings_enabled if use_vectors is None else use_vectors):
            try:
                vec = VectorBackend(str(Path(d) / "chroma"), settings.embedding_model)
            except Exception as exc:  # noqa
                print(f"[retriever] vector backend unavailable, using tfidf only: {exc}")
        _INDEX[d] = Index(d, vec)
    return _INDEX[d]
