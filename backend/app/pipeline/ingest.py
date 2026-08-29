"""
Multi-source ingestion.

Reads the five fragmented source systems, runs extraction + entity resolution,
and emits a provenance-carrying list of entities and relationships that the
graph layer consumes. Nothing downstream ever sees a fact without knowing
which record it came from.
"""

from __future__ import annotations

import csv
import json
import os
import re
from collections import defaultdict

from .extract import default_extractor, Mention, DEVANAGARI_LOCATIONS
from .resolve import EntityResolver
from .translit import normalize_digits, has_devanagari
from .ocr import capabilities as ocr_capabilities, ocr_folder

ORG_NAMES = {"Meridian Exim Pvt Ltd", "Rathi Infrastructure Ltd", "Sunrise Logistics",
             "Al-Noor Trading Co", "Konkan Marine Services",
             "Sai Traders", "Nova Stationers", "Gokhale Contractors",
             "Prime Facility Services"}

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RAW = os.path.join(ROOT, "data", "raw")


# ------------------------------------------------------------------ loaders

def _json(name):
    with open(os.path.join(RAW, name), encoding="utf-8") as f:
        return json.load(f)


def _csv(name):
    with open(os.path.join(RAW, name), encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ------------------------------------------------- relation cue patterns

RELATION_CUES = [
    (re.compile(r"on instructions of (?:one )?([A-Z][\w.\s]{3,30}?)(?:,|\s+who|\s+is|\.)"),
     "INSTRUCTED_BY", 0.80),
    (re.compile(r"(?:is )?(?:reportedly )?working under ([A-Z][\w.\s]{3,30}?)(?:\.|,)"),
     "REPORTS_TO", 0.80),
    (re.compile(r"handed over to him by ([A-Z][\w.\s]{3,30}?)(?: at|\.|,)"),
     "SUPPLIED_BY", 0.78),
    (re.compile(r"procuring the material from ([A-Z][\w.\s]{3,30}?)(?:\.|,)"),
     "SUPPLIED_BY", 0.78),
    (re.compile(r"involvement of ([A-Z][\w.\s]{3,30}?) in"),
     "ASSOCIATE_OF", 0.65),
    (re.compile(r"director of ([A-Z][\w.\s&-]{3,40}?) is recorded as ([A-Z][\w.\s]{3,30}?)(?:,|\.)"),
     "DIRECTOR_OF", 0.90),
    (re.compile(r"controls collection in .{0,30} after"),
     "CONTROLS_AREA", 0.45),
    # Devanagari cue phrases carrying the same relations
    (re.compile(r"([\u0900-\u097F]+(?:\s[\u0900-\u097F]+){0,2})\s+के कहने पर"),
     "INSTRUCTED_BY", 0.80),
    (re.compile(r"([\u0900-\u097F]+(?:\s[\u0900-\u097F]+){0,2})\s+के लिए काम करता है"),
     "REPORTS_TO", 0.80),
    (re.compile(r"([\u0900-\u097F]+(?:\s[\u0900-\u097F]+){0,2})\s+से माल लिया"),
     "SUPPLIED_BY", 0.78),
]

# --------------------------------------------------- phone ownership cues
#
# Attaching a phone number to the WRONG person is the most damaging error this
# system can make: it fuses two unrelated identities into one node and every
# downstream inference inherits the mistake. So ownership is only asserted from
# an explicit textual cue. Where the text is ambiguous ("Contact 90000... pe
# chalta hai sab") the system declines to assign - precision over recall.
#
# name_group = None means "the first PERSON mentioned in this document".
DEVA = r"[\u0900-\u097F]+(?:\s[\u0900-\u097F]+){0,2}"

PHONE_OWNER_CUES = [
    # Devanagari FIR forms
    (re.compile(rf"({DEVA})\s*\(मो\.?\s*(\d{{10}})\)"), 1, 2, 0.95),
    (re.compile(r"मोबाइल नंबर\s+(\d{10})\s+से किए गए"), None, 1, 0.85),
    (re.compile(r"आरोपी मोबाइल\s+(\d{10})"), None, 1, 0.85),
    (re.compile(r"([A-Z][\w.\s']{2,30}?)\s*\(mob\.?\s*(\d{10})\)"), 1, 2, 0.95),
    (re.compile(r"issued to\s+([A-Z][\w.\s']{2,30}?)\s+at mobile\s+(\d{10})"), 1, 2, 0.93),
    (re.compile(r"Contact number of accused\s+([A-Z][\w.\s']{2,30}?)\s+is\s+(\d{10})"), 1, 2, 0.93),
    (re.compile(r"Mobile number\s+(\d{10})\s+was used to make threatening calls"), None, 1, 0.85),
    (re.compile(r"[Aa]ccused was using mobile\s+(\d{10})"), None, 1, 0.85),
    (re.compile(r"number established as\s+(\d{10})"), None, 1, 0.88),
    (re.compile(r"known number\s+(\d{10})"), None, 1, 0.90),
]


def _ocr_accuracy(fir_id: str, recovered: str) -> float | None:
    """
    Character-level agreement between OCR output and the known original.
    Only possible because this corpus is synthetic; in the field this is what a
    periodic manual sample would measure.
    """
    path = os.path.join(RAW, "scanned_source.json")
    if not os.path.exists(path):
        return None
    import difflib
    truth = next((r["narrative"] for r in json.load(open(path, encoding="utf-8"))
                  if r["fir_id"] == fir_id), None)
    if not truth:
        return None
    norm = lambda s: " ".join(s.split()).lower()
    got, want = norm(recovered), norm(truth)
    # The OCR text also carries the page header and signature block, which are
    # not part of the narrative. Measure how much of the original was RECOVERED
    # rather than how similar the two strings are overall, otherwise correctly
    # reading extra text on the page counts against the score.
    m = difflib.SequenceMatcher(None, want, got)
    recovered_chars = sum(b.size for b in m.get_matching_blocks())
    return round(recovered_chars / max(len(want), 1), 3)


class Ingestor:
    def __init__(self):
        self.extractor = None
        self.resolver = EntityResolver()
        self.raw_relations: list[dict] = []      # keyed on raw name strings
        self.documents: dict[str, dict] = {}     # source_id -> record, for evidence panel
        self.mentions: list[Mention] = []
        self.phone_owner_raw: dict[str, str] = {}
        self.ownership_evidence: list[dict] = []
        self.ocr_status: dict = {}
        self.org_records: dict[str, dict] = {}

    # ---------------------------------------------------------------- util
    def _rel(self, a, b, rtype, source_id, source_type, ts, conf, evidence,
             a_kind="PERSON", b_kind="PERSON", **extra):
        if not a or not b or a.strip().lower() == b.strip().lower():
            return
        self.raw_relations.append({
            "a": a.strip(), "b": b.strip(), "a_kind": a_kind, "b_kind": b_kind,
            "type": rtype, "source_id": source_id, "source_type": source_type,
            "timestamp": ts, "confidence": conf, "evidence": evidence, **extra,
        })

    @staticmethod
    def _snap_to_person(candidate: str, persons) -> str | None:
        """Map a regex-captured name fragment onto an actual PERSON mention."""
        cand = candidate.strip().strip(".,")
        names = [p.text for p in persons]
        if cand in names:
            return cand
        for n in names:
            if n in cand or cand.endswith(n):
                return n
        return None

    @staticmethod
    def _nearest_person(mentions: list[Mention], anchor: Mention, max_gap=140):
        """Attach a PHONE/VEHICLE mention to the closest preceding PERSON."""
        best, best_d = None, 10**9
        for m in mentions:
            if m.type != "PERSON":
                continue
            d = anchor.start - m.end
            if 0 <= d < min(best_d, max_gap):
                best, best_d = m, d
        return best

    # ------------------------------------------------------------ sources
    def load_criminal_records(self):
        for r in _csv("criminal_records.csv"):
            sid = r["record_id"]
            self.documents[sid] = {"source_type": "criminal_record", "record": r}
            self.resolver.observe(
                r["name"], sid, "criminal_record", phones=[r["phone"]],
                attrs={"prior_cases": int(r["prior_cases"]),
                       "offence_categories": r["offence_categories"],
                       "active_period": r["active_period"],
                       "police_district": r["police_district"]})
            if r["phone"]:
                self.phone_owner_raw[r["phone"]] = r["name"]

    def load_text_source(self, records, id_key, text_key, source_type, ts_key=None,
                         loc_key=None):
        for rec in records:
            sid = rec[id_key]
            text = rec[text_key]
            # Devanagari numerals normalised for cue matching; the substitution is
            # 1:1 so character offsets remain valid against the original text.
            cue_text = normalize_digits(text)
            self.documents[sid] = {"source_type": source_type, "record": rec,
                                   "script": "devanagari" if has_devanagari(text)
                                             else "latin"}
            ms = self.extractor.extract(text)
            for m in ms:
                m.source_id, m.source_type = sid, source_type
            self.mentions += ms

            persons = [m for m in ms if m.type == "PERSON"]
            phones = [m for m in ms if m.type == "PHONE"]
            vehicles = [m for m in ms if m.type == "VEHICLE"]
            orgs = [m for m in ms if m.type == "ORG"]
            locs = [m for m in ms if m.type == "LOCATION"]
            ts = rec.get(ts_key) if ts_key else None

            # phone ownership strictly from explicit cues (see PHONE_OWNER_CUES)
            owned = defaultdict(lambda: {"phones": [], "vehicles": []})
            first_person = persons[0].text if persons else None
            for pat, ng, pg, conf in PHONE_OWNER_CUES:
                for m in pat.finditer(cue_text):
                    phone = m.group(pg)
                    owner = m.group(ng).strip() if ng else first_person
                    if not owner:
                        continue
                    # the cue may have grabbed a leading word; snap to a real mention
                    owner = self._snap_to_person(owner, persons) or owner
                    owned[owner]["phones"].append(phone)
                    self.phone_owner_raw.setdefault(phone, owner)
                    self.ownership_evidence.append({
                        "phone": phone, "owner": owner, "source_id": sid,
                        "source_type": source_type, "confidence": conf,
                        "evidence": cue_text[max(0, m.start() - 50):m.end() + 50].replace("\n", " "),
                    })

            # vehicles are only ever a weak co-occurrence, never an identity signal
            for vh in vehicles:
                p = self._nearest_person(ms, vh)
                if p:
                    owned[p.text]["vehicles"].append(vh.text)
                    self._rel(p.text, vh.text, "USED_VEHICLE", sid, source_type, ts,
                              0.55, f"{p.text} associated with {vh.text} in {sid}",
                              b_kind="VEHICLE")

            for p in persons:
                self.resolver.observe(p.text, sid, source_type,
                                      phones=owned[p.text]["phones"])

            # co-mention within one document = weak association
            names = sorted({p.text for p in persons})
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    self._rel(names[i], names[j], "CO_NAMED_IN", sid, source_type, ts,
                              0.55 if source_type == "social_media" else 0.70,
                              f"Both named in {sid}")

            # explicit relation cues
            for pat, rtype, conf in RELATION_CUES:
                for m in pat.finditer(cue_text):
                    groups = [g for g in m.groups() if g]
                    snippet = cue_text[max(0, m.start() - 60):m.end() + 60].replace("\n", " ")
                    if rtype == "DIRECTOR_OF" and len(groups) == 2:
                        self._rel(groups[1], groups[0], rtype, sid, source_type, ts,
                                  conf, snippet, b_kind="ORG")
                    else:
                        if not groups:
                            continue
                        subject = names[0] if names else None
                        target = groups[-1]
                        if subject:
                            self._rel(subject, target, rtype, sid, source_type, ts,
                                      conf, snippet)

            # person -> location / org
            for p in persons:
                for l in locs:
                    if abs(l.start - p.start) < 200:
                        # locations use the canonical Latin label so a Hindi FIR
                        # and an English cell-site record hit the same node
                        loc_label = l.normalized or l.text
                        self._rel(p.text, loc_label, "SEEN_AT", sid, source_type, ts,
                                  0.60, f"{p.text} and {l.text} co-occur in {sid}",
                                  b_kind="LOCATION")
                for o in orgs:
                    if abs(o.start - p.start) < 200:
                        self._rel(p.text, o.text, "LINKED_TO_ORG", sid, source_type, ts,
                                  0.65, f"{p.text} linked to {o.text} in {sid}",
                                  b_kind="ORG")

    def load_scanned_documents(self):
        """
        Paper FIRs, read by OCR. If OCR is unavailable on this machine the
        pipeline continues without them and says so, rather than pretending the
        source does not exist.
        """
        folder = os.path.join(RAW, "scans")
        caps = ocr_capabilities()
        self.ocr_status = {"capabilities": caps, "documents": []}
        if not caps["available"] or not os.path.isdir(folder):
            return []
        results = ocr_folder(folder)
        records = []
        already_digital = set(self.documents)   # same FIR held as clean text
        for r in results:
            if not r.ok or len(r.text) < 60:
                self.ocr_status["documents"].append(
                    {"path": os.path.basename(r.path), "ok": False,
                     "error": r.error or "too little text recovered"})
                continue
            fid = os.path.basename(r.path).rsplit(".", 1)[0].replace("_", "/")
            if fid in already_digital:
                # The same document is already held digitally. Re-reading the
                # scan would create a second, noisier copy of facts we already
                # have and inflate every corroboration count that depends on
                # them, so the scan is recorded and skipped.
                self.ocr_status["documents"].append(
                    {"path": os.path.basename(r.path), "ok": True, "fir_id": fid,
                     "skipped": "already held as digital text",
                     "mean_confidence": r.mean_confidence})
                continue
            records.append({
                "fir_id": fid, "station": "(read from scan)",
                "sections": "(read from scan)",
                "registered_on": None, "district": "",
                "language": r.languages, "medium": "paper - OCR",
                "narrative": r.text,
                "ocr_confidence": r.mean_confidence,
            })
            self.ocr_status["documents"].append(
                {"path": os.path.basename(r.path), "ok": True,
                 "fir_id": fid, "mean_confidence": r.mean_confidence,
                 "characters": len(r.text), "languages": r.languages,
                 # measured against the ground truth, because a confidence
                 # score the engine reports about itself is not an accuracy
                 "text_accuracy": _ocr_accuracy(fid, r.text)})
        return records

    def load_cdr(self):
        rows = _csv("cdr.csv")
        agg = defaultdict(lambda: {"n": 0, "first": None, "last": None,
                                   "secs": 0, "cells": set(), "ids": []})
        imei_map = defaultdict(set)
        for r in rows:
            key = tuple(sorted([r["caller"], r["callee"]]))
            a = agg[key]
            a["n"] += 1
            a["secs"] += int(r["duration_sec"])
            a["cells"].add(r["cell_tower"])
            a["ids"].append(r["call_id"])
            t = r["start_time"]
            a["first"] = min(a["first"], t) if a["first"] else t
            a["last"] = max(a["last"], t) if a["last"] else t
            if r.get("imei"):
                imei_map[r["imei"]].add(r["caller"])
        self.documents["CDR"] = {"source_type": "cdr", "record": {"rows": len(rows)}}
        self.cdr_rows = rows
        self.cdr_agg = agg
        self.imei_map = {k: sorted(v) for k, v in imei_map.items() if len(v) > 1}

    def load_transactions(self):
        self.txn_rows = _csv("transactions.csv")
        self.documents["TXN"] = {"source_type": "transaction",
                                 "record": {"rows": len(self.txn_rows)}}
        for r in self.txn_rows:
            for nm, acct in ((r["from_name"], r["from_account"]),
                             (r["to_name"], r["to_account"])):
                if nm in ORG_NAMES:
                    self.org_records[nm] = {"account": acct}
                else:
                    self.resolver.observe(nm, r["txn_id"], "transaction", accounts=[acct])

    # ------------------------------------------------------------- driver
    def run(self):
        firs = _json("firs.json")
        surv = _json("surveillance.json")
        social = _json("social_media.json")
        crim = _csv("criminal_records.csv")

        person_forms = {r["name"] for r in crim}
        person_forms |= {
            "Vikram Sethi", "V. Sethi", "Vikram Seth", "Sethi Vikram",
            "Rajesh Kumar", "R. Kumar", "Rajesh Kr.", "Rajesh Kumar Singh",
            "Imran Qureshi", "Imran Quraishi", "I. Qureshi",
            "Farid Sheikh", "F. Shaikh", "Fareed Shaikh",
            "Devendra Rathi", "D. Rathi", "Devendra Kumar Rathi",
            "Nadeem Ansari", "N. Ansari", "Sanjay Bhosale", "Sanjay Bhosle",
            "Deepak Chauhan", "D. Chauhan", "Ashok Gaikwad", "Suresh Yadav",
            "Manoj Tiwari", "Ravi Shankar Nair", "Kiran Deshpande",
            "Salim Mirza", "Ganesh Pawar", "Altaf Khan", "Ramesh Jadhav",
            "Sunil More", "Pravin Salunke",
        }
        # Devanagari gazetteer: the same cast as written in a Hindi FIR.
        person_forms |= {
            "विक्रम सेठी", "राजेश कुमार", "इमरान कुरैशी", "संजय भोसले",
            "नदीम अंसारी", "अशोक गायकवाड", "सुरेश यादव", "मनोज तिवारी",
            "फरीद शेख", "देवेंद्र राठी", "सलीम मिर्ज़ा", "गणेश पवार",
            "अल्ताफ खान", "दीपक चौहान", "किरण देशपांडे", "रवि शंकर नायर",
            "प्रवीण सालुंके", "सुनील मोरे",
        }
        self.extractor = default_extractor(person_forms)

        self.load_criminal_records()
        self.load_text_source(firs, "fir_id", "narrative", "fir", ts_key="registered_on")
        scanned = self.load_scanned_documents()
        if scanned:
            self.load_text_source(scanned, "fir_id", "narrative", "fir_scan",
                                  ts_key="registered_on")
        self.load_text_source(surv, "report_id", "observation", "surveillance",
                              ts_key="observed_on")
        self.load_text_source(social, "post_id", "text", "social_media", ts_key="posted_on")
        self.load_transactions()
        self.load_cdr()

        entities, decisions = self.resolver.resolve()
        return {
            "entities": entities,
            "resolution_decisions": decisions,
            "raw_relations": self.raw_relations,
            "documents": self.documents,
            "mentions": self.mentions,
            "phone_owner_raw": self.phone_owner_raw,
            "ownership_evidence": self.ownership_evidence,
            "cdr_rows": self.cdr_rows,
            "cdr_agg": self.cdr_agg,
            "imei_map": self.imei_map,
            "txn_rows": self.txn_rows,
            "org_records": self.org_records,
            "extractor": self.extractor.name,
            "ocr": getattr(self, "ocr_status", {"capabilities": ocr_capabilities(),
                                                "documents": []}),
        }


def ingest_all():
    return Ingestor().run()
