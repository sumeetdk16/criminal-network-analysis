# Architecture

## Pipeline

```
  SOURCES                 EXTRACTION            RESOLUTION           GRAPH
  ─────────────────       ─────────────         ──────────────       ──────────────
  FIR narratives   ─┐                                                
  Surveillance     ─┼──▶  NER                                        
  Social media     ─┘     • regex identifiers ──▶ Entity resolution ──▶ Property graph
                          • gazetteer            • shared identifier   • typed edges
  CDR              ─┐     • heuristic persons    • name similarity     • timestamps
  Transactions     ─┼──▶  (structured parse)     • union-find          • confidence
  Criminal history ─┘                            • merge evidence      • source list
                                                                            │
                                                                            ▼
                                          ANALYTICS ◀───────────────────────┤
                                          • centrality / influence          │
                                          • community detection             │
                                          • path finding                    │
                                          • corroboration scoring           │
                                                                            │
                                          DETECTION ◀───────────────────────┤
                                          • communication bursts            │
                                          • burner handsets                 │
                                          • circular fund flows             │
                                          • structuring                     │
                                          • insulated actors                │
                                          • clean-skin bridges              │
                                                                            │
                                          API (FastAPI) ◀───────────────────┘
                                          permission → audit → answer
                                                    │
                                          CONSOLE (canvas, zero-dep)
```

## Components

| Layer | Module | Responsibility |
|---|---|---|
| Ingestion | `pipeline/ingest.py` | Read eight sources, drive extraction, emit provenance-carrying relations |
| Extraction | `pipeline/extract.py` | `Extractor.extract(text) -> [Mention]`; rule/gazetteer default, spaCy optional; Latin and Devanagari |
| Script handling | `pipeline/translit.py` | Devanagari transliteration, numeral normalisation, consonant-skeleton phonetic keys |
| OCR | `pipeline/ocr.py` | Scanned FIRs via Tesseract, with capability detection and graceful absence |
| Resolution | `pipeline/resolve.py` | Union-find record linkage with auditable merge reasons |
| Graph | `graph/build.py` | NetworkX property graph; phonetic-fallback name resolution |
| Storage | `graph/store.py` | `GraphStore` adapters: Cypher export, JSON, live Neo4j |
| Analytics | `graph/analytics.py` | Centrality, Louvain communities, simple paths, exact Shapley attribution, corroboration |
| Detection | `graph/anomaly.py` | Nine rule-based detectors, each returning its firing basis |
| Summaries | `summarise.py` | Template default, optional LLM with a hallucination guard |
| Export | `pdf_report.py` | Court-ready PDF with evidence table and integrity digest |
| Integration | `integrations/` | Read-only CCTNS and ICJS adapters, authorisation-gated |
| Query | `nlq.py` | Intent parser that always returns its interpretation |
| Reporting | `report.py` | Subject profiles, connection analyses, case overview |
| Security | `auth.py` | Roles, permissions, hash-chained and optionally encrypted audit log |
| API | `main.py` | Every case-data route: `require()` → `audit()` → answer |
| UI | `static/index.html` | Graph explorer, evidence panel, map, pipeline and audit views |

## Cross-script identity resolution

The hardest correctness problem in this system is not the graph — it is
deciding that two records describe one person.

```
  "विक्रम सेठी"  (Hindi FIR)          "Vikram Sethi"  (CDR export)
        │                                     │
        ▼  transliterate                      │
   "vikrama sethe"                            │
        │                                     │
        ▼  consonant skeleton                 ▼  consonant skeleton
      vkrm / sth        ═══ match ═══       vkrm / sth
```

Exact transliteration is unattainable — Hindi deletes the inherent vowel in
ways no simple rule captures, so विक्रम becomes *vikrama* and इमरान becomes
*imaraana*. Rather than fight for the right string, both sides are reduced to a
consonant skeleton: first letter kept, later vowels dropped, `ph`/`f`, `q`/`k`,
`z`/`j` folded, and `y` treated as a glide. Sixteen of sixteen cast members
match their Devanagari spelling under this key, with no collisions between
different people.

The same key powers the phonetic fallback in `CaseGraph.resolve_name`, which is
what lets ICJS's "Sanjay Bhosale" find our "Sanjay Bhosle" — and therefore what
lets the system notice that his handset was in use while he was in custody.

## Why these choices

**NetworkX, not Neo4j, for the prototype.** No database to install means the
demo starts in one command on any laptop. `CaseGraph` exposes a deliberately
narrow surface (`_add_node`, `_add_edge`, `person_subgraph`, `node`), so
repointing it at Neo4j is a contained change. At agency scale a real graph
database is the right answer; at demo scale it is an installation risk.

**Rule-based NER by default.** A demo that depends on a 500 MB model download
succeeding on venue wifi is a demo that fails. `SpacyExtractor` upgrades
automatically when spaCy and a model are present, and `RuleGazetteerExtractor`
is the guaranteed floor. Production would fine-tune IndicNER or IndicBERT on
FIR text; the `Extractor` contract does not change.

**Pure-Python PageRank fallback.** NetworkX delegates PageRank to SciPy.
`analytics.py` ships a power-iteration implementation so a missing SciPy never
breaks the influence ranking.

**Every edge is a list of sources, not a boolean.** The corroboration score —
how many independent source systems attest a link — is only possible because
provenance is stored per observation rather than collapsed at write time.

**Shapley by closed form, not by sampling.** The influence score is additive, so
each component's Shapley value is exactly `w_k · (x_k − E[x_k])`. KernelSHAP
would have to sample coalitions to approximate what an additive model gives for
free; the guarantee an investigator cares about — that the attributions sum to
the subject's score — holds exactly.

**Detectors calibrate against the data, not against constants.** The
insulated-actor rule once used a hand-tuned ratio and silently stopped firing
when the corpus grew by three documents. It now compares components of one
subject's own score, so there is nothing to retune.

**Read-only integration, authorisation-gated.** `integrations/` fetches from
CCTNS and ICJS and never writes back, and refuses to fetch at all without an
authorisation reference recorded in the audit log. A system that cannot say why
it was entitled to a record has no business holding it.

## Production deployment sketch

```
  On-premise / air-gapped agency network
  ┌──────────────────────────────────────────────────────┐
  │  Ingestion workers ──▶ Object store (raw records)     │
  │        │                                              │
  │        ▼                                              │
  │  NER service (GPU, IndicNER) ──▶ Resolution service   │
  │        │                                              │
  │        ▼                                              │
  │  Neo4j cluster  ◀──▶  API (FastAPI, mTLS)             │
  │        │                    │                          │
  │        ▼                    ▼                          │
  │  Analytics jobs        Console (React)                 │
  │                                                        │
  │  SSO/LDAP · HSM-backed encryption · WORM audit store   │
  └──────────────────────────────────────────────────────┘
        │
        └── read-only adapters: CCTNS, ICJS, bank CFT feeds
```

Integration is by adapter, not replacement: the system reads from CCTNS and
ICJS rather than asking agencies to migrate. Nothing in the design requires
outbound internet access, which is what makes an air-gapped deployment possible.
