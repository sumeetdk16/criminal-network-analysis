"""Registry of read-only source adapters."""

from .base import SourceAdapter, FetchResult          # noqa: F401
from .cctns import CctnsAdapter
from .icjs import IcjsAdapter

ADAPTERS = {a.key: a for a in (CctnsAdapter(), IcjsAdapter())}


def status() -> dict:
    return {
        "posture": "read-only; this system never writes to a source of record",
        "authorisation_required": True,
        "adapters": [a.status() for a in ADAPTERS.values()],
        "note": ("Adapters run against local fixtures until the corresponding "
                 "URL and key are configured, so the integration contract is "
                 "testable without agency credentials."),
    }


def fetch(key: str, authorisation: str = "", **query) -> dict:
    a = ADAPTERS.get(key)
    if not a:
        return {"ok": False, "error": f"no adapter named '{key}'"}
    return a.fetch(authorisation=authorisation, **query).dict()
