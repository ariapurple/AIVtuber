from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
import httpx

from ailive2d.config import AppConfig, ROOT


@dataclass
class MemoryHit:
    text: str
    kind: str
    author: str
    distance: float | None


class MemoryStore:
    """Local long-term memory: Ollama embedding + Chroma vector DB.

    Flow each chat turn:
    1) embed the new user message
    2) query nearest past notes / chats
    3) stuff those hits into the LLM prompt
    4) after the reply, embed+store the exchange
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def apply_config(self, config) -> None:
        """Hot-swap memory settings (collection path changes need restart)."""
        self._config = config
        self._client_http = httpx.AsyncClient(timeout=config.llm.timeout_sec)
        path = Path(config.memory.persist_dir)
        if not path.is_absolute():
            path = ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._chroma = chromadb.PersistentClient(path=str(path))
        self._collection = self._chroma.get_or_create_collection(
            name=config.memory.collection,
            metadata={"hnsw:space": "cosine"},
        )

    async def close(self) -> None:
        await self._client_http.aclose()

    @property
    def enabled(self) -> bool:
        return bool(self._config.memory.enabled)

    def count(self) -> int:
        return int(self._collection.count())

    async def embed(self, text: str) -> list[float]:
        response = await self._client_http.post(
            f"{self._config.llm.base_url}/api/embeddings",
            json={"model": self._config.memory.embed_model, "prompt": text},
        )
        response.raise_for_status()
        embedding = response.json().get("embedding") or []
        if not embedding:
            raise RuntimeError("Ollama returned empty embedding")
        return [float(x) for x in embedding]

    async def add(
        self,
        text: str,
        *,
        kind: str = "chat",
        author: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        cleaned = text.strip()
        if not cleaned:
            return ""
        emb = await self.embed(cleaned)
        doc_id = str(uuid.uuid4())
        meta: dict[str, Any] = {
            "kind": kind,
            "author": author or "",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if metadata:
            for key, value in metadata.items():
                meta[str(key)] = str(value)
        self._collection.add(
            ids=[doc_id],
            documents=[cleaned],
            embeddings=[emb],
            metadatas=[meta],
        )
        return doc_id

    async def recall(
        self,
        query: str,
        *,
        n: int | None = None,
        author: str | None = None,
    ) -> list[MemoryHit]:
        if not self.enabled:
            return []
        cleaned = query.strip()
        if not cleaned:
            return []
        top_k = max(1, int(n or self._config.memory.top_k))
        if self.count() == 0:
            return []
        emb = await self.embed(cleaned)
        kwargs: dict[str, Any] = {
            "query_embeddings": [emb],
            "n_results": min(top_k, self.count()),
            "include": ["documents", "metadatas", "distances"],
        }
        if author:
            kwargs["where"] = {"author": author}
        try:
            result = self._collection.query(**kwargs)
        except Exception:
            # Fallback without metadata filter if chroma rejects where-clause.
            kwargs.pop("where", None)
            result = self._collection.query(**kwargs)
        hits: list[MemoryHit] = []
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        for doc, meta, dist in zip(docs, metas, dists, strict=False):
            if not doc:
                continue
            meta = meta or {}
            hits.append(
                MemoryHit(
                    text=str(doc),
                    kind=str(meta.get("kind") or ""),
                    author=str(meta.get("author") or ""),
                    distance=float(dist) if dist is not None else None,
                )
            )
        return hits

    def format_for_prompt(self, hits: list[MemoryHit]) -> str:
        if not hits:
            return ""
        lines = []
        for hit in hits:
            prefix = hit.kind or "memory"
            who = f"/{hit.author}" if hit.author else ""
            lines.append(f"- [{prefix}{who}] {hit.text}")
        return "\n".join(lines)

    async def ensure_seed_facts(self, facts: list[str]) -> int:
        """Insert starter persona facts once (skip if collection already has data)."""
        if self.count() > 0:
            return 0
        added = 0
        for fact in facts:
            if fact.strip():
                await self.add(fact.strip(), kind="fact", author="system")
                added += 1
        return added

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "enabled": self.enabled,
            "persist_dir": str(self._path),
            "collection": self._config.memory.collection,
            "embed_model": self._config.memory.embed_model,
            "top_k": self._config.memory.top_k,
            "count": self.count(),
        }
