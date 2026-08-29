"""
Read-only source adapters.

Agencies already run CCTNS, ICJS and a stack of legacy systems, and none of
them are going to be replaced because an analysis tool would prefer a different
schema. So this system integrates by adapter and reads only: it pulls records
in, maps them to the internal model, and never writes back. A read-only posture
also bounds the damage if the analysis system is ever compromised.

Every adapter answers four questions, and the console shows the answers:

    configured()   is it pointed at anything on this machine?
    authorisation  under what legal instrument is this data being read?
    fetch()        the records, already mapped to the internal schema
    provenance     which system, which query, at what time

The `authorisation` field is not decorative. Access to CCTNS and ICJS content
is governed, and a system that cannot say why it was entitled to a record has
no business holding it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


@dataclass
class FetchResult:
    adapter: str
    system: str
    ok: bool
    records: list = field(default_factory=list)
    record_type: str = ""
    fetched_at: str = ""
    query: dict = field(default_factory=dict)
    authorisation: str = ""
    mode: str = ""            # live | mock | unconfigured
    error: str = ""
    field_mapping: dict = field(default_factory=dict)

    def dict(self):
        return asdict(self)


class SourceAdapter:
    key = "base"
    system = "base"
    record_type = ""
    # documented mapping from the external field to ours, so a schema change at
    # the far end shows up here and nowhere else
    FIELD_MAP: dict[str, str] = {}

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = base_url or os.environ.get(f"{self.key.upper()}_URL", "")
        self.api_key = api_key or os.environ.get(f"{self.key.upper()}_KEY", "")

    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def status(self) -> dict:
        return {
            "adapter": self.key, "system": self.system,
            "record_type": self.record_type,
            "configured": self.configured(),
            "mode": "live" if self.configured() else "mock",
            "base_url": self.base_url or None,
            "access": "read-only",
            "field_mapping": self.FIELD_MAP,
        }

    # -- to implement ----------------------------------------------------
    def _fetch_live(self, **query) -> list[dict]:
        raise NotImplementedError

    def _fetch_mock(self, **query) -> list[dict]:
        return []

    def map_record(self, raw: dict) -> dict:
        return {ours: raw.get(theirs) for theirs, ours in self.FIELD_MAP.items()}

    def fetch(self, authorisation: str = "", **query) -> FetchResult:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not authorisation:
            return FetchResult(
                self.key, self.system, False, [], self.record_type, now, query,
                "", "blocked",
                error="No authorisation reference supplied. Records are not "
                      "retrieved without one.",
                field_mapping=self.FIELD_MAP)
        try:
            raw = self._fetch_live(**query) if self.configured() else self._fetch_mock(**query)
            mode = "live" if self.configured() else "mock"
            return FetchResult(self.key, self.system, True,
                               [self.map_record(r) for r in raw],
                               self.record_type, now, query, authorisation, mode,
                               field_mapping=self.FIELD_MAP)
        except Exception as e:
            return FetchResult(self.key, self.system, False, [], self.record_type,
                               now, query, authorisation, "error", str(e),
                               self.FIELD_MAP)
