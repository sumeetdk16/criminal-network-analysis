# Data model

## Node types

| Type | Id prefix | Key attributes |
|---|---|---|
| `PERSON` | `P0001` | `label` (canonical name), `aliases[]`, `phones[]`, `accounts[]`, `prior_cases`, `offence_categories`, `active_period`, `police_district`, `sources[]`, `merge_evidence[]` |
| `ORG` | `O1234` | `label`, `account` |
| `LOCATION` | `L-Kurla` | `label`, `lat`, `lon` |
| `VEHICLE` | `V-MH01AB1234` | `label` |
| `CASE` | `F-FIR/2026/0101` | `station`, `sections`, `registered_on` |

`record_checked` is derived, not stored: a person carries `prior_cases` only if
they were actually found in the criminal-history database. Its absence means
*not checked*, never *no record*.

## Edge attributes

Every edge, regardless of type, carries:

| Field | Meaning |
|---|---|
| `types[]` | one or more relationship types on the same pair |
| `weight` | accumulated strength, drives layout and centrality |
| `confidence` | highest confidence among contributing observations |
| `first_seen` / `last_seen` | temporal window |
| `observations` | how many separate observations produced it |
| `sources[]` | **the evidentiary trail** — one entry per observation |

Each entry in `sources[]` holds `source_id`, `source_type`, `type`,
`timestamp`, `confidence` and an `evidence` string, plus type-specific extras
(`call_count`, `total_seconds`, `cells[]` for CDR; `total_amount`, `txn_count`,
`txn_ids[]` for transactions).

## Relationship types

| Type | Derived from | Base weight | Typical confidence |
|---|---|---|---|
| `CALLED` | CDR aggregation | 1.0 + n/25 | 0.92 |
| `TRANSACTED_WITH` | transaction aggregation | 1.0 + amount factor | 0.95 |
| `SHARES_HANDSET` | shared IMEI | 1.4 | 0.95 |
| `DIRECTOR_OF` | FIR / registry text | 1.2 | 0.90 |
| `INSTRUCTED_BY` | FIR cue phrase | 1.3 | 0.80 |
| `REPORTS_TO` | FIR cue phrase | 1.3 | 0.80 |
| `SUPPLIED_BY` | FIR cue phrase | 1.2 | 0.78 |
| `ASSOCIATE_OF` | FIR cue phrase | 0.7 | 0.65 |
| `LINKED_TO_ORG` | text co-occurrence | 0.8 | 0.65 |
| `CO_NAMED_IN` | same document | 0.6 | 0.70 (0.55 social media) |
| `NAMED_IN` | person → FIR | — | 0.85 |
| `SEEN_AT` | cell site / text co-occurrence | 0.4 | 0.60–0.70 |
| `USED_VEHICLE` | text proximity | 0.4 | 0.55 |

Devanagari mentions carry `script: "devanagari"` and a `normalized` field
holding the transliteration; locations additionally map to their canonical Latin
label, so a Hindi FIR and an English cell-site record land on the same node.

`CO_NAMED_IN` deserves care: it means two people appear in the same document.
That is association, not participation, and the reports say so explicitly.

## Confidence and corroboration

Two separate ideas, deliberately not merged into one number:

- **Confidence** is how reliable a single observation is. A CDR record is 0.92;
  a social-media co-mention is 0.55.
- **Corroboration** is how many *independent source systems* attest the same
  link. A connection seen in CDR *and* transactions *and* surveillance is
  qualitatively stronger evidence than three sightings in one system.

The console exposes both as separate filters, because an investigator asking
"what do I actually have on this link?" needs both answers.

## Source records

| File | Records | Structure |
|---|---|---|
| `firs.json` | 11 | free-text narratives; 8 English, 3 wholly in Devanagari (one with Devanagari numerals) |
| `raw/scans/*.png` | 2 | paper-only FIRs, reaching the pipeline through OCR alone |
| `scanned_source.json` | 2 | ground truth for the scans, used only to measure OCR recovery |
| `cdr.csv` | ~530 | caller, callee, time, duration, cell tower, lat/lon, IMEI |
| `transactions.csv` | ~130 | from/to account and name, amount, mode, narration |
| `criminal_records.csv` | 12 | name, phone, prior cases, offence categories |
| `surveillance.json` | 5 | free-text observations with location and time |
| `social_media.json` | 5 | free-text posts with handle and reliability rating |
| CCTNS adapter | 2 | live or fixture FIR records, authorisation-gated |
| ICJS adapter | 2 | live or fixture custody and case-progression records |
