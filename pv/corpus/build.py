"""D2: idempotent corpus pipeline: read -> parse -> chunk -> (embed) -> manifest.

    python -m pv.corpus.build [--force] [--embed]

Idempotent: a PDF whose SHA-256 and chunker version are unchanged is not re-parsed.
Embedding is a SEPARATE stage keyed by (chunk_hash, model): changing the model, or a
chunk, re-embeds only what changed (L1: "re-embedding without rebuilding the index").
"""
import argparse, hashlib, json, re, time
from datetime import datetime, timezone
from pathlib import Path

CHUNKER_VERSION = "2"
WINDOW, STRIDE = 1200, 800          # 400-char overlap > longest provision sentence we cite
SECTION_RE = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d{1,2}){1,4})\s+[A-Z]")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_pdf(path: Path):
    """Return ([(page_number_1_based, text)], extractor_name).

    Prefers poppler's pdftotext (clean words on the Gazette PDF); falls back to pypdf, which
    inserts stray spaces inside words on that file ("mus t"). Text layer only: a scanned PDF
    yields no chunks and is reported as an error (OCR it first)."""
    import shutil, subprocess
    if shutil.which("pdftotext"):
        try:
            out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, timeout=180, check=True).stdout
            raw = out.decode("utf-8", errors="replace").split("\f")
            if raw and not raw[-1].strip():
                raw = raw[:-1]
            return [(n, re.sub(r"\s+", " ", t).strip()) for n, t in enumerate(raw, 1)], "pdftotext"
        except Exception:  # noqa
            pass
    from pypdf import PdfReader
    pages = []
    for n, page in enumerate(PdfReader(str(path)).pages, 1):
        pages.append((n, re.sub(r"\s+", " ", page.extract_text() or "").strip()))
    return pages, "pypdf"


def chunk_page(source, page, text):
    chunks, n = [], 0
    if len(text) < 40:
        return chunks
    for start in range(0, len(text), STRIDE):
        frag = text[start:start + WINDOW]
        if len(frag) < 40:
            break
        n += 1
        sec = SECTION_RE.search(frag)
        chunks.append({
            "id": f"{source}_p{page:04d}_c{n:02d}", "source": source, "page": page,
            "section": sec.group(1) if sec else None, "text": frag,
            "hash": hashlib.sha1(frag.encode()).hexdigest()[:16],
        })
        if start + WINDOW >= len(text):
            break
    return chunks


def build(corpus_dir="corpus", index_dir="data/index", force=False, embed=False, model=None, log=print):
    t0 = time.monotonic()
    src, out = Path(corpus_dir) / "source_pdfs", Path(index_dir)
    out.mkdir(parents=True, exist_ok=True)
    mpath, cpath = out / "manifest.json", out / "chunks.jsonl"
    old = json.loads(mpath.read_text()) if mpath.exists() else {}
    old_docs = {d["name"]: d for d in old.get("documents", [])}
    pdfs = sorted(src.glob("*.pdf"))
    errors = []
    if not pdfs:
        errors.append(f"No PDFs in {src}")

    by_doc = {}
    if cpath.exists():
        for line in cpath.read_text(encoding="utf-8").splitlines():
            c = json.loads(line)
            by_doc.setdefault(c["source"], []).append(c)

    docs, reparsed = [], 0
    for pdf in pdfs:
        name, sha = pdf.stem, _sha(pdf)
        prev = old_docs.get(name)
        if (not force and prev and prev["sha256"] == sha and old.get("chunker_version") == CHUNKER_VERSION
                and name in by_doc):
            docs.append(prev)
            log(f"skip   {name} (unchanged)")
            continue
        try:
            pages, extractor = parse_pdf(pdf)
            chunks = [c for p, t in pages for c in chunk_page(name, p, t)]
            if not chunks:
                errors.append(f"{pdf.name}: no extractable text (scanned? run OCR first)")
            by_doc[name] = chunks
            reparsed += 1
            docs.append({"name": name, "sha256": sha, "pdf_pages": len(pages), "chunks": len(chunks),
                         "text_extractable": bool(chunks), "extractor": extractor})
            log(f"parsed {name}: {len(pages)} pages -> {len(chunks)} chunks")
        except Exception as exc:  # noqa
            errors.append(f"{pdf.name}: {type(exc).__name__}: {exc}")

    names = {d["name"] for d in docs}
    all_chunks = [c for n in sorted(by_doc) if n in names for c in by_doc[n]]
    tmp = cpath.with_suffix(".tmp")
    tmp.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in all_chunks), encoding="utf-8")
    tmp.replace(cpath)                       # atomic: readers never see a half-written index

    embedded = old.get("embedded_chunks", 0)
    emb_model = old.get("embedding_model")
    if embed:
        from pv.corpus.retriever import VectorBackend
        emb_model = model
        if not emb_model:
            raise SystemExit("EMBEDDING_MODEL must be set (see .env.example) when embeddings are enabled")
        embedded = VectorBackend(str(out / "chroma"), emb_model).sync(all_chunks, log=log)

    elapsed = max(time.monotonic() - t0, 1e-6)
    manifest = {
        "built_utc": datetime.now(timezone.utc).isoformat(), "chunker_version": CHUNKER_VERSION,
        "documents": docs, "chunks": len(all_chunks), "reparsed_documents": reparsed,
        "embedding_model": emb_model, "embedded_chunks": embedded, "errors": errors,
        "build_seconds": round(elapsed, 2), "throughput_chunks_per_s": round(len(all_chunks) / elapsed, 1),
        "corpus_version": hashlib.sha1("".join(sorted(d["sha256"] for d in docs)).encode()).hexdigest()[:12],
    }
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    from pv.config import settings
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--embed", action="store_true", default=settings.embeddings_enabled)
    a = ap.parse_args()
    m = build(settings.corpus_dir, settings.index_dir, force=a.force, embed=a.embed, model=settings.embedding_model)
    print(json.dumps({k: v for k, v in m.items() if k != "documents"}, indent=2))
    raise SystemExit(1 if m["errors"] else 0)
