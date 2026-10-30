"""Raw landing zone: untouched source responses kept on disk.

Doubles as a cache, so re-running normalisation (after a schema change, say) never
hits the network again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RawStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def path_for(self, source: str, endpoint: str, key: str) -> Path:
        safe = key.replace("/", "_")
        return self.root / source / endpoint / f"{safe}.json"

    def load(self, source: str, endpoint: str, key: str) -> Any | None:
        path = self.path_for(source, endpoint, key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, source: str, endpoint: str, key: str, payload: Any) -> Path:
        path = self.path_for(source, endpoint, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)  # atomic: never leaves a half-written file behind
        return path
