"""Small JSON store with workspace-bound reads and cache support.

This is intentionally a local implementation of the Core persistence boundary.
The service only talks to this interface, so a remote transactional store can
replace it without changing domain or MCP contracts.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from .errors import NotFoundError
from .models import BrandProfile, Record, ResearchEvidence, record_from_dict, website_key


class LocalStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "topic-planner-store.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"records": {}, "cache": {}, "websites": {}})

    def _read(self) -> dict:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Topic Planner local storage cannot be read") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), dict):
            raise RuntimeError("Topic Planner local storage has an invalid shape")
        payload.setdefault("cache", {})
        payload.setdefault("websites", {})
        return payload

    def _write(self, payload: dict) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(self.path)

    def save(self, record: Record) -> Record:
        record.validate()
        payload = self._read()
        payload["records"][record.id] = record.to_dict()
        if isinstance(record, BrandProfile):
            # Keep one durable brand context per normalized website host; drop
            # any stale keys this record previously owned so a rename cannot
            # leave a website pointing at two profiles.
            index = payload["websites"]
            for key in [item for item, value in index.items() if value == record.id]:
                index.pop(key, None)
            key = website_key(record.website)
            if key:
                index[f"{record.workspace_id}:{key}"] = record.id
        self._write(payload)
        return record

    def get(self, record_id: str, *, workspace_id: str, object_type: str | None = None) -> Record:
        payload = self._read()
        raw = payload["records"].get(record_id)
        if not raw:
            raise NotFoundError("object not found")
        record = record_from_dict(raw)
        if record.workspace_id != workspace_id:
            raise NotFoundError("object not found")
        if object_type and record.object_type != object_type:
            raise NotFoundError("object not found")
        return record

    def list(
        self, *, workspace_id: str, object_type: str, lineage_contains: str | None = None
    ) -> list[Record]:
        result: list[Record] = []
        for raw in self._read()["records"].values():
            record = record_from_dict(raw)
            if record.workspace_id != workspace_id or record.object_type != object_type:
                continue
            if lineage_contains and lineage_contains not in record.lineage:
                continue
            result.append(record)
        return sorted(result, key=lambda item: item.created_at)

    def find_brand_by_website(self, *, workspace_id: str, website: str) -> BrandProfile | None:
        """Return the brand context remembered for this normalized website host."""

        key = website_key(website)
        if not key:
            return None
        payload = self._read()
        record_id = payload["websites"].get(f"{workspace_id}:{key}")
        if not record_id:
            return None
        record = self.get(record_id, workspace_id=workspace_id, object_type="brand_profile")
        return record if isinstance(record, BrandProfile) else None

    def get_cached_evidence(self, *, workspace_id: str, cache_key: str) -> ResearchEvidence | None:
        payload = self._read()
        record_id = payload["cache"].get(f"{workspace_id}:{cache_key}")
        if not record_id:
            return None
        record = self.get(record_id, workspace_id=workspace_id, object_type="research_evidence")
        return record if isinstance(record, ResearchEvidence) else None

    def cache_evidence(self, evidence: ResearchEvidence) -> None:
        payload = self._read()
        payload["records"][evidence.id] = evidence.to_dict()
        payload["cache"][f"{evidence.workspace_id}:{evidence.cache_key}"] = evidence.id
        self._write(payload)

    def all_for_run(self, *, workspace_id: str, research_run_id: str, object_type: str) -> Iterable[Record]:
        return self.list(workspace_id=workspace_id, object_type=object_type, lineage_contains=research_run_id)
