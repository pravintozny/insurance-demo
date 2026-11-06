"""Append-only, hash-chained audit trail."""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    timestamp: str
    actor: str
    role: str
    action: str
    entity_type: str
    entity_id: str
    details: Dict[str, Any]
    prev_hash: str
    hash: str


def _digest(prev_hash, seq, timestamp, actor, role, action, etype, eid, details) -> str:
    payload = json.dumps(
        [prev_hash, seq, timestamp, actor, role, action, etype, eid, details],
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class AuditLog:
    """Every state change is recorded here. Entries are never edited; each one
    embeds the hash of its predecessor so tampering is detectable via verify()."""

    GENESIS = "0" * 64

    def __init__(self, clock):
        self._clock = clock
        self._entries: List[AuditEntry] = []

    def record(self, actor, action: str, entity_type: str, entity_id: str,
               details: Optional[dict] = None) -> AuditEntry:
        details = dict(details or {})
        prev = self._entries[-1].hash if self._entries else self.GENESIS
        seq = len(self._entries) + 1
        ts = self._clock().isoformat()
        name = getattr(actor, "name", str(actor))
        role = getattr(getattr(actor, "role", None), "value", "SYSTEM")
        h = _digest(prev, seq, ts, name, role, action, entity_type, entity_id, details)
        entry = AuditEntry(seq, ts, name, role, action, entity_type, entity_id, details, prev, h)
        self._entries.append(entry)
        return entry

    def entries(self) -> List[AuditEntry]:
        return list(self._entries)

    def for_entity(self, entity_type: str, entity_id: str) -> List[AuditEntry]:
        return [e for e in self._entries
                if e.entity_type == entity_type and e.entity_id == entity_id]

    def verify(self) -> bool:
        prev = self.GENESIS
        for e in self._entries:
            if e.prev_hash != prev:
                return False
            if e.hash != _digest(prev, e.seq, e.timestamp, e.actor, e.role,
                                 e.action, e.entity_type, e.entity_id, e.details):
                return False
            prev = e.hash
        return True
