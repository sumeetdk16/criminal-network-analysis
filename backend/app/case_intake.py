"""
Case intake.

This is the only place new evidence enters the system. Everything the rest of
the app does — extraction, resolution, graph analytics, anomaly detection —
runs on whatever sits in data/raw/. Two ways in are supported:

* **Quick add** — one investigator pastes one new document (an FIR narrative,
  a surveillance log, a social-media tip) through the UI. A record is built
  in the same shape the pipeline already reads and appended to the right
  file. This is the everyday path: new information comes in piece by piece
  as an investigation runs.

* **Bulk upload** — an admin replaces or extends a whole source file at once
  (e.g. a fresh CDR/transaction dump for a new case). This is the "load a new
  case" path.

Both paths end the same way: the caller (in main.py) calls `main.load(force
=True)` afterwards, which reruns the full pipeline — extraction, entity
resolution, graph build, every anomaly detector — against the updated files.
Nothing here talks to the graph directly; intake only ever touches
data/raw/*, so the same rebuild path is exercised whether data arrived by
quick-add, bulk upload, or was sitting there at startup.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ""))
RAW = os.path.join(ROOT, "data", "raw")
SCANS = os.path.join(RAW, "scans")

# kind -> (filename, id_field, required text field, required columns for csv)
JSON_SOURCES = {
    "fir":          ("firs.json", "fir_id", "FIR",
                     ["fir_id", "narrative", "registered_on", "station",
                      "sections", "district"]),
    "surveillance": ("surveillance.json", "report_id", "SUR",
                     ["report_id", "observation", "observed_on", "location",
                      "lat", "lon", "unit"]),
    "social":       ("social_media.json", "post_id", "SM",
                     ["post_id", "text", "posted_on", "platform", "handle",
                      "geo_hint", "reliability"]),
}

CSV_SOURCES = {
    "cdr":               ("cdr.csv",
                          ["call_id", "caller", "callee", "start_time",
                           "duration_sec", "call_type", "cell_tower", "lat",
                           "lon", "imei"]),
    "transactions":      ("transactions.csv",
                          ["txn_id", "from_account", "from_name", "to_account",
                           "to_name", "amount_inr", "mode", "timestamp",
                           "narration"]),
    "criminal_records":  ("criminal_records.csv",
                          ["record_id", "name", "phone", "prior_cases",
                           "offence_categories", "active_period",
                           "police_district"]),
}

ALL_KINDS = sorted({*JSON_SOURCES, *CSV_SOURCES})


class IntakeError(ValueError):
    """A file or field didn't match the shape the pipeline expects."""


def _path(name: str) -> str:
    return os.path.join(RAW, name)


def _read_json(name: str) -> list:
    p = _path(name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _write_json(name: str, rows: list):
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def _next_id(prefix: str, existing: list, id_field: str) -> str:
    """CASE/2026/### style ids, continuing the highest number already in use."""
    nums = []
    for r in existing:
        m = re.search(r"(\d+)$", str(r.get(id_field, "")))
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    sample = existing[0][id_field] if existing else f"{prefix}/2026/0001"
    if "/" in sample:
        head = sample.rsplit("/", 1)[0]
        width = len(sample.rsplit("/", 1)[1])
        return f"{head}/{str(n).zfill(width)}"
    return f"{prefix}{str(n).zfill(4)}"


# --------------------------------------------------------------- quick add

def quick_add(kind: str, fields: dict) -> dict:
    """
    Build one new record in the shape ingest.py expects and append it to the
    right file. Returns the record that was written (so the UI can show the
    generated id back to the investigator).
    """
    if kind not in JSON_SOURCES:
        raise IntakeError(
            f"'{kind}' isn't a document type — use one of {sorted(JSON_SOURCES)}. "
            "(Structured data like call records or transactions goes through "
            "bulk upload instead, since it arrives as a batch.)")
    filename, id_field, prefix, _ = JSON_SOURCES[kind]
    rows = _read_json(filename)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    if kind == "fir":
        text = (fields.get("narrative") or "").strip()
        if not text:
            raise IntakeError("An FIR needs a narrative — that's the text the "
                              "extractor reads names, phones and vehicles out of.")
        rec = {
            "fir_id": _next_id("FIR", rows, id_field),
            "station": fields.get("station") or "Unspecified Station",
            "sections": fields.get("sections") or "",
            "registered_on": fields.get("registered_on") or now_iso,
            "district": fields.get("district") or "",
            "language": fields.get("language") or "en-IN",
            "narrative": text,
        }
    elif kind == "surveillance":
        text = (fields.get("observation") or "").strip()
        if not text:
            raise IntakeError("A surveillance entry needs an observation — the "
                              "free text the extractor reads.")
        rec = {
            "report_id": _next_id("SUR", rows, id_field),
            "observed_on": fields.get("observed_on") or now_iso,
            "location": fields.get("location") or "",
            "lat": float(fields["lat"]) if fields.get("lat") else None,
            "lon": float(fields["lon"]) if fields.get("lon") else None,
            "unit": fields.get("unit") or "Field Unit",
            "observation": text,
        }
    else:  # social
        text = (fields.get("text") or "").strip()
        if not text:
            raise IntakeError("A social-media tip needs the post text.")
        rec = {
            "post_id": _next_id("SM", rows, id_field),
            "platform": fields.get("platform") or "X",
            "handle": fields.get("handle") or "@unknown",
            "posted_on": fields.get("posted_on") or now_iso,
            "geo_hint": fields.get("geo_hint") or "",
            "text": text,
            "reliability": fields.get("reliability") or "unverified",
        }

    rows.append(rec)
    _write_json(filename, rows)
    return rec


# ---------------------------------------------------------------- bulk upload

def _parse_csv(raw: bytes, required: list[str]) -> list[dict]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise IntakeError("That file has no header row.")
    missing = [c for c in required if c not in reader.fieldnames]
    if missing:
        raise IntakeError(
            f"Missing column(s) {missing} — expected header: {required}")
    return list(reader)


def _parse_json_array(raw: bytes, required_field: str) -> list[dict]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise IntakeError(f"Not valid JSON: {e}")
    if not isinstance(data, list):
        raise IntakeError("Expected a JSON array of records.")
    for r in data:
        if required_field not in r:
            raise IntakeError(f"Every record needs a '{required_field}' field.")
    return data


def bulk_upload(kind: str, raw: bytes, mode: str = "append") -> dict:
    """
    Load a whole file for one source. mode='append' merges new records into
    the existing file (deduping on the id column); mode='replace' overwrites
    it — the way to swap in an entirely new case.
    """
    if mode not in ("append", "replace"):
        raise IntakeError("mode must be 'append' or 'replace'")

    if kind in JSON_SOURCES:
        filename, id_field, _, _ = JSON_SOURCES[kind]
        incoming = _parse_json_array(raw, id_field)
        existing = [] if mode == "replace" else _read_json(filename)
        seen = {r[id_field] for r in existing}
        added = 0
        for r in incoming:
            if r[id_field] in seen:
                continue
            existing.append(r)
            seen.add(r[id_field])
            added += 1
        _write_json(filename, existing)
        return {"kind": kind, "file": filename, "mode": mode,
                "records_in_file": len(existing), "records_added": added}

    if kind in CSV_SOURCES:
        filename, required = CSV_SOURCES[kind]
        id_field = required[0]
        incoming = _parse_csv(raw, required)
        existing = [] if mode == "replace" else (
            _read_csv_rows(filename) if os.path.exists(_path(filename)) else [])
        seen = {r[id_field] for r in existing}
        added = 0
        for r in incoming:
            if r[id_field] in seen:
                continue
            existing.append(r)
            seen.add(r[id_field])
            added += 1
        _write_csv(filename, required, existing)
        return {"kind": kind, "file": filename, "mode": mode,
                "records_in_file": len(existing), "records_added": added}

    raise IntakeError(f"Unknown source '{kind}' — use one of {ALL_KINDS}")


def _read_csv_rows(name: str) -> list[dict]:
    with open(_path(name), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_csv(name: str, fieldnames: list[str], rows: list[dict]):
    with open(_path(name), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def clear_all() -> dict:
    """
    Wipe every intake source so the next pipeline run starts from a blank
    case: empty arrays for the JSON sources, header-only CSVs, and the
    scanned-document folder emptied. `scanned_source.json` (OCR ground truth
    for the demo corpus, not an intake source) is left alone.
    """
    cleared = []
    for filename, _, _, _ in JSON_SOURCES.values():
        _write_json(filename, [])
        cleared.append(filename)
    for filename, required in CSV_SOURCES.values():
        _write_csv(filename, required, [])
        cleared.append(filename)
    scans_removed = 0
    if os.path.isdir(SCANS):
        for name in os.listdir(SCANS):
            p = os.path.join(SCANS, name)
            if os.path.isfile(p):
                os.remove(p)
                scans_removed += 1
    return {"files_cleared": cleared, "scans_removed": scans_removed}


def save_scan(filename: str, raw: bytes) -> str:
    """Drop a scanned-document image into data/raw/scans/ for the OCR stage."""
    os.makedirs(SCANS, exist_ok=True)
    safe = re.sub(r"[^\w.\-]", "_", filename) or "scan.png"
    dest = os.path.join(SCANS, safe)
    with open(dest, "wb") as f:
        f.write(raw)
    return safe
