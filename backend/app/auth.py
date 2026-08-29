"""
Role-based access control and a tamper-evident audit trail.

In a law-enforcement system the audit log is not a nice-to-have. Who asked
which question about whom, and when, is itself evidence - of proper conduct or
of misuse. Two properties follow from that:

* **It must be tamper-evident.** Every entry carries the hash of the entry
  before it, so removing or editing any line breaks the chain from that point
  onward and `verify_chain()` reports exactly where. Deleting the tail is still
  possible on a plain filesystem, which is why a deployment writes this to WORM
  storage - but silent alteration is not.

* **It may contain sensitive material.** Query text can name a subject under
  investigation. When `CNAS_AUDIT_KEY` is set, the payload of each entry is
  encrypted with AES-256-GCM at rest. The chain hashes the ciphertext, so
  integrity can be verified by someone who cannot read the contents.

The demo ships with static tokens so it runs with no identity provider. A
deployment replaces `USERS` with the agency directory (LDAP / SSO) without
touching the permission model.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
from datetime import datetime, timezone

from fastapi import Header, HTTPException

ROLES = {
    # role         permissions
    #   data:submit  — file one new document (FIR / surveillance / tip) as it
    #                  comes in; any working investigator does this.
    #   data:ingest  — bulk-load or replace a whole source file, i.e. stand up
    #                  a new case's dataset in one go; kept to admin, the way
    #                  only records/IT stands up a new case file in practice.
    "investigator": {"graph:read", "entity:read", "evidence:read", "query:run",
                     "report:generate", "data:submit"},
    "admin":        {"graph:read", "entity:read", "evidence:read", "query:run",
                     "report:generate", "audit:read", "analytics:tune",
                     "data:submit", "data:ingest", "user:manage"},
}

# Demo credentials only. Replace with the agency identity provider.
USERS = {
    "demo-investigator": {"name": "PSI A. Kulkarni", "role": "investigator",
                          "unit": "Anti Narcotics Cell", "badge": "ANC-2291"},
    "demo-admin":        {"name": "System Administrator", "role": "admin",
                          "unit": "IT", "badge": "IT-001"},
}

AUDIT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "audit_log.jsonl")
GENESIS = "0" * 64
_lock = threading.Lock()


# ------------------------------------------------------------- encryption

def _key() -> bytes | None:
    """32-byte key from CNAS_AUDIT_KEY (base64 or raw). Absent = plaintext."""
    raw = os.environ.get("CNAS_AUDIT_KEY", "").strip()
    if not raw:
        return None
    try:
        k = base64.b64decode(raw, validate=True)
    except Exception:
        k = raw.encode()
    if len(k) not in (16, 24, 32):
        k = hashlib.sha256(k).digest()
    return k


def encryption_status() -> dict:
    k = _key()
    try:
        import cryptography  # noqa: F401
        lib = True
    except Exception:
        lib = False
    return {
        "at_rest_encryption": bool(k) and lib,
        "algorithm": "AES-256-GCM" if bool(k) and lib else None,
        "key_source": "CNAS_AUDIT_KEY environment variable" if k else None,
        "library_available": lib,
        "hash_chain": "SHA-256; each entry links the hash of the previous one",
        "note": ("Set CNAS_AUDIT_KEY to encrypt audit payloads at rest. "
                 "Integrity verification does not require the key."
                 if not (bool(k) and lib) else
                 "Payloads are encrypted at rest; the hash chain covers the "
                 "ciphertext so integrity is verifiable without the key."),
    }


def _encrypt(payload: dict) -> tuple[str, bool]:
    k = _key()
    if not k:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True), False
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True), False
    nonce = os.urandom(12)
    ct = AESGCM(k).encrypt(nonce, json.dumps(payload, ensure_ascii=False,
                                             sort_keys=True).encode(), None)
    return base64.b64encode(nonce + ct).decode(), True


def _decrypt(blob: str, encrypted: bool) -> dict:
    if not encrypted:
        try:
            return json.loads(blob)
        except Exception:
            return {"unreadable": True}
    k = _key()
    if not k:
        return {"encrypted": True, "readable": False,
                "reason": "CNAS_AUDIT_KEY not set in this process"}
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw = base64.b64decode(blob)
        return json.loads(AESGCM(k).decrypt(raw[:12], raw[12:], None).decode())
    except Exception as e:
        return {"encrypted": True, "readable": False, "reason": str(e)}


# ------------------------------------------------------------- principals

class Principal:
    def __init__(self, token: str, info: dict):
        self.token = token
        self.name = info["name"]
        self.role = info["role"]
        self.unit = info["unit"]
        self.badge = info["badge"]
        self.permissions = ROLES.get(info["role"], set())

    def can(self, perm: str) -> bool:
        return perm in self.permissions

    def dict(self):
        return {"name": self.name, "role": self.role, "unit": self.unit,
                "badge": self.badge, "permissions": sorted(self.permissions)}


def current_user(x_auth_token: str = Header(default="demo-investigator")) -> Principal:
    info = USERS.get(x_auth_token)
    if not info:
        raise HTTPException(status_code=401, detail="Unknown or missing access token")
    return Principal(x_auth_token, info)


def require(principal: Principal, permission: str):
    if not principal.can(permission):
        audit(principal, "ACCESS_DENIED", {"permission": permission})
        raise HTTPException(
            status_code=403,
            detail=f"Role '{principal.role}' is not permitted to {permission}")


# ------------------------------------------------------------- audit log

def _entry_hash(entry: dict) -> str:
    material = json.dumps({k: entry[k] for k in
                           ("seq", "timestamp", "user", "badge", "role", "unit",
                            "action", "payload", "encrypted", "prev_hash")},
                          ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()


def _tail() -> tuple[int, str]:
    if not os.path.exists(AUDIT_PATH):
        return 0, GENESIS
    last = None
    with open(AUDIT_PATH, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                last = line
    if not last:
        return 0, GENESIS
    try:
        rec = json.loads(last)
        return rec.get("seq", 0), rec.get("hash", GENESIS)
    except Exception:
        return 0, GENESIS


def audit(principal: Principal, action: str, detail: dict | None = None):
    payload, encrypted = _encrypt(detail or {})
    with _lock:
        seq, prev = _tail()
        entry = {
            "seq": seq + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "user": principal.name, "badge": principal.badge,
            "role": principal.role, "unit": principal.unit,
            "action": action, "payload": payload, "encrypted": encrypted,
            "prev_hash": prev,
        }
        entry["hash"] = _entry_hash(entry)
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _read_raw() -> list[dict]:
    if not os.path.exists(AUDIT_PATH):
        return []
    out = []
    with open(AUDIT_PATH, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except Exception:
                    out.append({"corrupt": True, "raw": line[:200]})
    return out


def read_audit(limit: int = 200) -> list[dict]:
    rows = _read_raw()[-limit:]
    out = []
    for r in rows:
        if r.get("corrupt"):
            out.append(r)
            continue
        out.append({**{k: r[k] for k in ("seq", "timestamp", "user", "badge",
                                         "role", "unit", "action")},
                    "detail": _decrypt(r["payload"], r.get("encrypted", False)),
                    "hash": r["hash"][:16], "encrypted": r.get("encrypted", False)})
    return out[::-1]


def verify_chain() -> dict:
    """
    Walk the whole log and confirm each entry hashes to what the next one
    claims. Reports the first break, so an integrity failure names a specific
    entry rather than being a yes/no.
    """
    rows = _read_raw()
    prev = GENESIS
    for i, r in enumerate(rows):
        if r.get("corrupt"):
            return {"intact": False, "entries": len(rows),
                    "broken_at": i + 1, "reason": "unparseable line"}
        if r.get("prev_hash") != prev:
            return {"intact": False, "entries": len(rows), "broken_at": r.get("seq"),
                    "reason": "previous-hash mismatch: an earlier entry was "
                              "altered or removed"}
        if _entry_hash(r) != r.get("hash"):
            return {"intact": False, "entries": len(rows), "broken_at": r.get("seq"),
                    "reason": "entry hash mismatch: this entry was altered"}
        prev = r["hash"]
    return {"intact": True, "entries": len(rows),
            "head_hash": prev[:16] if rows else None,
            "encryption": encryption_status()}
