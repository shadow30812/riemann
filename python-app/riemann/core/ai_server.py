"""
The AI search engine: a small FastAPI server the app runs as a child process.

It embeds text with all-MiniLM-L6-v2 exported to ONNX, run by onnxruntime on
the CPU, with the model's own tokenizer (the tokenizers package). Embeddings
are the mean of the token vectors over the attention mask, normalised to unit
length, which is what sentence-transformers computes for this model (checked
to 1e-7). Each PDF's chunks are kept as a matrix of unit vectors, so a search
is one matrix product. No PyTorch, no faiss.

The model files are downloaded on first use from the Hugging Face repository
at a fixed commit (MODEL_COMMIT) into RIEMANN_AI_MODELS, and each is checked
against its SHA-256 in MODEL_FILES before it is used; a file that does not
match is deleted. The server listens before the model is ready: until then
every endpoint answers 503 with a "loading" detail that says how far the
download has got, and the app keeps retrying. If loading fails, endpoints
answer 500 with the reason.

The server never opens PDFs. The app extracts page text with the Rust core
(so password-protected documents work, and the text matches what the reader
shows) and sends it. /index with no "pages" answers {"status": "needs_text"}
unless the document's vectors are already cached; the app then sends the
pages. /search_library searches the papers it has vectors for and lists the
rest in "needs_text"; the app indexes those and asks again.

Security: the app starts the server with RIEMANN_AI_PORT (a free port on
127.0.0.1) and RIEMANN_AI_TOKEN (a random secret). Every request must carry
the token in the X-Riemann-Token header, compared with hmac.compare_digest. A
web page cannot add that header without a CORS preflight, which the server
never approves. Without a token the server refuses to start, and a server
whose token is somehow empty refuses every request, so a stray launch can
never serve unauthenticated.

Vectors are cached on disk when RIEMANN_AI_CACHE is set, keyed by the file's
path, size, modification time, the chunking and MODEL_NAME, so a paper is
embedded once. Each PDF gets its own in-memory index, keyed by its path; up to
MAX_INDEXES are kept. Requests that name no path use the most recently indexed
document.

/tag scores a text chunk against the subject labels the app sends (the user's
list from Settings), or DEFAULT_TAGS when it sends none.

Run it with python -m riemann.core.ai_server, or in a frozen build as the app
executable with --riemann-ai-sidecar (see build_entry.py). It must not import
Qt.
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import urllib.request
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, model_validator

from .endpoints import HUGGINGFACE, LOOPBACK

MODEL_NAME = "all-MiniLM-L6-v2-onnx"
MODEL_REPO = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_COMMIT = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MODEL_FILES = {
    "onnx/model.onnx": "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
    "tokenizer.json": "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
}
MAX_SEQ_LENGTH = 256
BATCH = 32
MIN_SCORE = 0.35
MAX_INDEXES = 16
MAX_LIBRARY_PATHS = 2000
MAX_PAGES = 20_000

CACHE_DIR = os.environ.get("RIEMANN_AI_CACHE", "")
MODELS_DIR = os.environ.get("RIEMANN_AI_MODELS", "")
AUTH_TOKEN = os.environ.get("RIEMANN_AI_TOKEN", "")

DEFAULT_TAGS = [
    "Mathematics",
    "Physics",
    "Computer Science",
    "Machine Learning",
    "Statistics",
    "Economics",
    "Electrical Engineering",
    "Biology",
    "Chemistry",
    "Medicine",
]
MAX_TAG_SETS = 8

embedder: Any = None
load_status = "loading"
load_error = ""
vectors: np.ndarray | None = None
chunk_metadata: list[dict[str, Any]] = []
indexes: dict[str, tuple[np.ndarray, list[dict[str, Any]], float | None]] = {}
tag_embeddings: dict[tuple[str, ...], np.ndarray] = {}


class OnnxEmbedder:
    def __init__(self, model_dir: str) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.tokenizer = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        self.tokenizer.enable_truncation(MAX_SEQ_LENGTH)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) // 2))
        self.session = ort.InferenceSession(
            os.path.join(model_dir, "onnx", "model.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self.inputs = {i.name for i in self.session.get_inputs()}

    def encode(self, texts: list[str]) -> np.ndarray:
        out = []
        for start in range(0, len(texts), BATCH):
            batch = self.tokenizer.encode_batch(texts[start : start + BATCH])
            ids = np.array([e.ids for e in batch], dtype=np.int64)
            mask = np.array([e.attention_mask for e in batch], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
            hidden = self.session.run(None, {k: v for k, v in feeds.items() if k in self.inputs})[0]
            weights = mask[..., None].astype(np.float32)
            pooled = (hidden * weights).sum(1) / np.clip(weights.sum(1), 1e-9, None)
            pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
            out.append(pooled.astype(np.float32))
        return np.concatenate(out) if out else np.zeros((0, 384), np.float32)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model(model_dir: str) -> None:
    """Downloads any missing or damaged model file into model_dir, checking each SHA-256."""
    global load_status
    for name, digest in MODEL_FILES.items():
        dest = os.path.join(model_dir, *name.split("/"))
        if os.path.exists(dest) and _sha256(dest) == digest:
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        url = f"{HUGGINGFACE}/{MODEL_REPO}/resolve/{MODEL_COMMIT}/{name}"
        tmp = dest + ".part"
        h = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            for chunk in iter(lambda: resp.read(1 << 20), b""):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if total:
                    load_status = f"loading: downloading the search model ({done * 100 // total}% of {total >> 20} MB)"
        if h.hexdigest() != digest:
            os.remove(tmp)
            raise RuntimeError(f"{name} from {url} does not match its pinned SHA-256")
        os.replace(tmp, dest)


def load_model() -> None:
    global embedder, load_status, load_error
    if embedder is not None:
        return
    try:
        model_dir = MODELS_DIR or os.path.join(os.path.expanduser("~"), ".cache", "Riemann", "models", MODEL_NAME)
        ensure_model(model_dir)
        load_status = "loading: starting the search model"
        embedder = OnnxEmbedder(model_dir)
        load_status = "ready"
    except Exception as e:
        load_error = f"{type(e).__name__}: {e}"
        load_status = "failed"


def require_model() -> None:
    if load_status == "failed":
        raise HTTPException(status_code=500, detail=f"The search model could not be loaded: {load_error}")
    if embedder is None or load_status != "ready":
        raise HTTPException(status_code=503, detail=load_status)


class IndexRequest(BaseModel):
    pdf_path: str
    pages: list[str] | None = Field(None, max_length=MAX_PAGES)
    chunk_size: int = Field(200, ge=1, le=10_000)
    chunk_overlap: int = Field(50, ge=0)

    @model_validator(mode="after")
    def _overlap_below_size(self) -> "IndexRequest":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(5, ge=1, le=100)
    pdf_path: str | None = None


class SearchResult(BaseModel):
    page: int
    text: str
    score: float


class TagRequest(BaseModel):
    text_chunk: str
    threshold: float = 0.25
    tags: list[str] | None = Field(None, max_length=200)


class LibrarySearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    pdf_paths: list[str] = Field(..., max_length=MAX_LIBRARY_PATHS)
    top_k: int = Field(20, ge=1, le=200)


def token_ok(token: str | None) -> bool:
    if not AUTH_TOKEN:
        return False
    return hmac.compare_digest((token or "").encode(), AUTH_TOKEN.encode())


def require_token(x_riemann_token: str | None = Header(default=None)) -> None:
    if not token_ok(x_riemann_token):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def chunk_text(text: str, page_num: int, chunk_size: int, overlap: int) -> list[dict[str, Any]]:
    """Sliding windows of chunk_size words, overlapping by overlap words; page_num is 1-based."""
    words = text.split()
    chunks: list[dict[str, Any]] = []
    if not words:
        return chunks
    step = max(1, chunk_size - overlap)
    for i in range(0, len(words), step):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk:
            chunks.append({"page": page_num, "text": chunk})
        if i + chunk_size >= len(words):
            break
    return chunks


def _mtime(path: str) -> float | None:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _cache_key(pdf_path: str, chunk_size: int, chunk_overlap: int) -> str:
    try:
        st = os.stat(pdf_path)
    except OSError:
        return ""
    raw = f"{os.path.abspath(pdf_path)}|{st.st_size}|{st.st_mtime_ns}|{chunk_size}|{chunk_overlap}|{MODEL_NAME}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_cached(key: str) -> tuple[np.ndarray, list[dict[str, Any]]] | None:
    if not CACHE_DIR or not key:
        return None
    try:
        embeddings = np.load(os.path.join(CACHE_DIR, f"{key}.npy"))
        with open(os.path.join(CACHE_DIR, f"{key}.json"), encoding="utf-8") as f:
            chunks = json.load(f)
    except (OSError, ValueError):
        return None
    if len(chunks) != len(embeddings):
        return None
    return embeddings.astype("float32"), chunks


def _save_cached(key: str, embeddings: np.ndarray, chunks: list[dict[str, Any]]) -> None:
    if not CACHE_DIR or not key:
        return
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        vec_tmp = os.path.join(CACHE_DIR, f"{key}.npy.tmp")
        with open(vec_tmp, "wb") as f:
            np.save(f, embeddings, allow_pickle=False)
        os.replace(vec_tmp, os.path.join(CACHE_DIR, f"{key}.npy"))
        tmp = os.path.join(CACHE_DIR, f"{key}.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(chunks, f)
        os.replace(tmp, os.path.join(CACHE_DIR, f"{key}.json"))
    except OSError:
        pass


def _remember(pdf_path: str, matrix: np.ndarray, chunks: list[dict[str, Any]], mtime: float | None) -> None:
    global vectors, chunk_metadata
    indexes.pop(pdf_path, None)
    indexes[pdf_path] = (matrix, chunks, mtime)
    while len(indexes) > MAX_INDEXES:
        indexes.pop(next(iter(indexes)))
    vectors, chunk_metadata = matrix, chunks


def load_index(pdf_path: str, chunk_size: int, chunk_overlap: int) -> int | None:
    """The chunk count of a document already in memory or on disk, else None."""
    mtime = _mtime(pdf_path)
    cached = indexes.get(pdf_path)
    if cached is not None and cached[2] is not None and cached[2] == mtime:
        _remember(pdf_path, cached[0], cached[1], mtime)
        return len(cached[1])
    stored = _load_cached(_cache_key(pdf_path, chunk_size, chunk_overlap))
    if stored is None:
        return None
    _remember(pdf_path, stored[0], stored[1], mtime)
    return len(stored[1])


def build_index(pdf_path: str, pages: list[str], chunk_size: int, chunk_overlap: int) -> int:
    """Chunks and embeds the given page texts as pdf_path's index. Raises ValueError if they hold no text."""
    chunks: list[dict[str, Any]] = []
    for number, page in enumerate(pages, 1):
        text = clean_text(page)
        if text:
            chunks.extend(chunk_text(text, number, chunk_size, chunk_overlap))
    if not chunks:
        raise ValueError("No readable text found in PDF.")
    matrix = embedder.encode([c["text"] for c in chunks])
    _save_cached(_cache_key(pdf_path, chunk_size, chunk_overlap), matrix, chunks)
    _remember(pdf_path, matrix, chunks, _mtime(pdf_path))
    return len(chunks)


def run_search(query: str, top_k: int, pdf_path: str | None) -> list[dict[str, Any]] | None:
    """Searches one document, or the last one indexed when pdf_path is None; None if it has no index."""
    if pdf_path:
        entry = indexes.get(pdf_path)
        if entry is None:
            return None
        matrix, chunks = entry[0], entry[1]
    else:
        matrix, chunks = vectors, chunk_metadata
    if matrix is None or not chunks:
        return None
    scores = matrix @ embedder.encode([query])[0]
    order = np.argsort(-scores)[:top_k]
    return [
        {"page": chunks[i]["page"], "text": chunks[i]["text"], "score": float(scores[i])}
        for i in order
        if float(scores[i]) >= MIN_SCORE
    ]


def search_many(
    query: str, pdf_paths: list[str], top_k: int, chunk_size: int = 200, chunk_overlap: int = 50
) -> dict[str, Any]:
    """Best passages across the papers with vectors; the others are listed in needs_text."""
    results: list[dict[str, Any]] = []
    skipped: list[str] = []
    needs_text: list[str] = []
    searched = 0
    for path in pdf_paths:
        if not os.path.exists(path):
            skipped.append(path)
            continue
        if load_index(path, chunk_size, chunk_overlap) is None:
            needs_text.append(path)
            continue
        searched += 1
        for hit in run_search(query, top_k, path) or []:
            results.append(dict(hit, pdf_path=path))
    results.sort(key=lambda r: -r["score"])
    return {"results": results[:top_k], "searched": searched, "skipped": skipped, "needs_text": needs_text}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    threading.Thread(target=load_model, daemon=True).start()
    yield


app = FastAPI(title="Riemann AI", lifespan=lifespan)


@app.get("/status", dependencies=[Depends(require_token)])
async def status() -> dict[str, str]:
    return {"status": load_status, "error": load_error}


@app.post("/index", dependencies=[Depends(require_token)])
async def index_pdf(req: IndexRequest) -> dict[str, Any]:
    require_model()
    if not os.path.exists(req.pdf_path):
        raise HTTPException(status_code=404, detail="PDF file not found.")
    if req.pages is None:
        count = await asyncio.to_thread(load_index, req.pdf_path, req.chunk_size, req.chunk_overlap)
        if count is None:
            return {"status": "needs_text"}
        return {"status": "success", "chunks_indexed": count}
    try:
        count = await asyncio.to_thread(build_index, req.pdf_path, req.pages, req.chunk_size, req.chunk_overlap)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"status": "success", "chunks_indexed": count}


@app.post("/search", response_model=list[SearchResult], dependencies=[Depends(require_token)])
async def search_pdf(req: SearchRequest) -> list[SearchResult]:
    require_model()
    try:
        results = await asyncio.to_thread(run_search, req.query, req.top_k, req.pdf_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if results is None:
        raise HTTPException(status_code=400, detail="No PDF is currently indexed.")
    return [SearchResult(**r) for r in results]


@app.post("/search_library", dependencies=[Depends(require_token)])
async def search_library(req: LibrarySearchRequest) -> dict[str, Any]:
    require_model()
    return await asyncio.to_thread(search_many, req.query, req.pdf_paths, req.top_k)


@app.post("/tag", dependencies=[Depends(require_token)])
async def generate_tags(req: TagRequest) -> dict[str, list[str]]:
    """Up to three of the labels, best first, whose similarity to the chunk exceeds threshold."""
    require_model()
    labels = tuple(t.strip() for t in (req.tags or DEFAULT_TAGS) if t and t.strip())
    if not labels:
        return {"tags": []}
    try:
        if labels not in tag_embeddings:
            if len(tag_embeddings) >= MAX_TAG_SETS:
                tag_embeddings.clear()
            tag_embeddings[labels] = embedder.encode(list(labels))
        similarities = tag_embeddings[labels] @ embedder.encode([req.text_chunk])[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    scored = sorted(
        ((float(score), labels[i]) for i, score in enumerate(similarities) if score > req.threshold),
        reverse=True,
    )
    return {"tags": [tag for _, tag in scored[:3]]}


def main() -> None:
    import uvicorn

    port = os.environ.get("RIEMANN_AI_PORT", "")
    if not AUTH_TOKEN or not port.isdigit():
        sys.exit(
            "The AI engine is started by Riemann, which sets RIEMANN_AI_PORT and RIEMANN_AI_TOKEN; refusing to run "
            "without them."
        )
    uvicorn.run(app, host=LOOPBACK, port=int(port), log_level="info")


if __name__ == "__main__":
    main()
