# Demo script — 8 minutes

Read this before presenting. The demo has one job: show connections a human
reading the same files would almost certainly miss, and then show exactly why
the system believes each one.

**Setup:** `python3 scripts/verify.py` (all 63 checks must pass), server
running, browser at <http://127.0.0.1:8000>, role set to *Investigator*, theme
set to suit the room (press <kbd>T</kbd>). Nothing else open.

**Navigation:** <kbd>⌘K</kbd> searches everything — subjects (including their
Devanagari spellings), findings, views and the example questions. <kbd>1</kbd>–<kbd>6</kbd>
switch views, <kbd>/</kbd> focuses the question box, <kbd>?</kbd> lists the rest.
Use the palette rather than hunting for nodes with the mouse.

---

## 0:00 — The problem, in the data (50s)

> "An agency holds these files on one case. Eight sources. Three are free
> text — FIRs, surveillance notes, social media. Three of the FIRs are written
> wholly in Hindi. Two more exist only as scanned paper. Three are structured —
> call records, transactions, criminal history. And two are live feeds from
> CCTNS and ICJS.
>
> Nothing links them. The same man is 'Vikram Sethi' in an English record,
> 'विक्रम सेठी' in a Hindi FIR, 'V. Sethi' in a surveillance note, and a phone
> number in the CDR."

Show `data/raw/` and open one Devanagari FIR. Do not dwell.

---

## 0:50 — What the system built (60s)

You land on **Overview**. Do not click anything yet.

> "From 138 raw mentions across 37 records it resolved 17 distinct people —
> seven of them assembled across two scripts — into 137 relationships.
> Fourteen high-severity patterns are flagged.
>
> Look at *Where the evidence comes from*: every source system contributes,
> including twelve links that exist only because two paper FIRs were read by
> OCR. And *Independent corroboration* — a hundred and six links rest on a
> single source, one rests on four. That distinction is on screen because a
> link seen in three systems is qualitatively stronger evidence than three
> sightings in one, and an investigator needs to know which they have."

Hit **table** on any panel.

> "Every chart has a table behind it. No number in this system is reachable
> only by hovering."

---

## 1:50 — The headline finding (90s)

Press <kbd>⌘K</kbd>, type `rathi`, hit enter — or click the example query
**"how is Devendra Rathi connected to Vikram Sethi"** in the Network view.

The graph collapses to four nodes. The report panel fills.

> "Devendra Rathi is a builder with no criminal record. Vikram Sethi has four
> prior cases. No file connects them — not one.
>
> The system found a three-hop path, and it can only see it because it did
> four things first. It merged 'F. Shaikh' in one document with 'Farid Sheikh'
> in another. It read a surveillance note attributing a second, unlisted
> handset to Sethi. It aggregated 530 call records into weighted links. And one
> leg of this path — Deshpande to Shaikh — is corroborated by a FIR that exists
> only on paper, which the system read by OCR. Look at the source labels:
> `cdr`, `surveillance`, `fir_scan`. Three independent systems, one of them a
> scanned page.
>
> Every step cites the record it came from. If the record is wrong, the link is
> wrong — and you can see the record."

Scroll to *Reading this correctly*.

> "It also says what this does **not** mean. A path is not proof of a common
> purpose. That sentence is in every connection report it generates."

Click **Connection analysis (PDF)**.

> "And that's the same analysis as a court-ready PDF — classification banner,
> evidence table, the officer who ran it, and a SHA-256 digest on the page so a
> printed copy can be matched against the audit entry."

---

## 3:20 — Cross-script identity (60s)

Press <kbd>⌘K</kbd> and type `sethi` — note that the palette matches his
Devanagari spelling too. Open him, then look at *Identity resolution*.

> "Four spellings collapsed into one subject, including the Devanagari one. The
> transliteration is never exact — Hindi drops the inherent vowel, so विक्रम
> comes out as *vikrama* — so instead of chasing the right string the system
> compares consonant skeletons. *vikrama* and *vikram* both reduce to `vkrm`.
> Sixteen of sixteen names in this case match their Hindi spelling that way,
> with no two different people colliding."

Scroll to *Why this ranking*.

> "And this is why he's ranked third. Six components, Shapley attribution
> against the network average. Because the score is additive the Shapley values
> have a closed form, so these are exact, not sampled — and they sum precisely
> to his score. Note the shape: betweenness is his largest contribution,
> contact count his smallest."

---

## 4:10 — Suspicious patterns (100s)

Click **Findings**.

Walk four, not twenty:

1. **Vikram Sethi appears insulated behind intermediaries** — "Five contacts,
   reaches eighteen people in two hops, and of the six scoring components his
   contact count contributes least while his position between others
   contributes most. That's the shape of a command role. An earlier version of
   this detector used a hand-tuned ratio and went silent when we added three
   documents — it now reads the attribution itself, so there's no constant to
   retune."

2. **Sethi and Deepak Chauhan repeatedly present together** — "Six occasions,
   two cell sites, and **zero calls between them**. They meet in person and
   never speak on the phone. Findings where the pair *do* call each other are
   ranked below these, because two lieutenants who talk daily also travelling
   together tells you nothing."

3. **Sanjay Bhosle's handset was in use during judicial custody** — "ICJS says
   he's been inside since 20 May. His number shows 27 call events after that,
   up to 10 June. Neither system can see this alone. Either the attribution is
   wrong or somebody else has the handset — and every call on it in that period
   has to be re-attributed before anyone relies on it."

4. **Cash deposits just below the reporting threshold** — "Fourteen deposits,
   every one between ₹42,500 and ₹50,000. Structuring. The rule that fired is
   printed under the finding."

---

## 5:50 — Evidence, OCR and identity (60s)

Click any person, then a row in **Established links**.

> "Why do you say these two are connected? Source system, record id, date, and
> the FIR text quoted verbatim."

Press <kbd>4</kbd> for **Sources**.

> "And why do you say these two records are the same person? Every merge, with
> its reason and score. Phone attribution only happens when the text says so
> explicitly — ambiguous mentions are left unattributed on purpose. An earlier
> version guessed by proximity and silently fused two unrelated suspects."

Press <kbd>5</kbd> for **System**.

> "OCR read two paper-only FIRs at 95% engine confidence and recovered 100% of
> the text — that's measured against ground truth, not the engine's opinion of
> itself. Devanagari OCR needs the Hindi language pack; without it scanned Hindi
> pages are skipped rather than mis-read, because a garbled name is worse than a
> missing one. Same principle everywhere on this page: every capability has a
> stated fallback."

---

## 6:50 — Time (30s)

Press <kbd>2</kbd> for **Network**. Drag the timeline slider, then press **replay**.

> "Every edge is timestamped, so you can see the network as it stood on any
> date, or watch it form. Undated links — a directorship, say — stay visible
> rather than being implied to have appeared at some moment."

---

## 7:20 — Governance (60s)

Press <kbd>6</kbd> for the **Audit** log. It refuses.

> "Investigators can't read the audit log. Switch to admin —"

Change the role to **Administrator**, click Audit log again.

> "— and there's every query I just ran, logged before the answer came back.
> Note the integrity line: each entry carries the hash of the one before it, so
> altering any line breaks every hash after it, and the check names the broken
> entry. Set one environment variable and the payloads are encrypted at rest
> with AES-256-GCM — and the chain covers the ciphertext, so an auditor can
> verify integrity without being able to read the contents."

---

## 8:20 — Close (20s)

> "It runs offline on one command with no database and no model download.
> `scripts/export_graph.py` writes the whole graph as Cypher for Neo4j, and the
> extractor swaps to IndicNER behind an interface that's already there. And it
> never asserts anything it can't show you the record for."

---

## Questions to expect

**"Is this real data?"** No. Everything is synthetic, generated by a script in
the repo. Phone numbers are in a reserved fictional block.

**"Is the Hindi handling real or a lookup table?"** Both, and honestly so.
There is a gazetteer, but the matching is a real syllable-aware transliterator
plus a phonetic key, and it works on names never seen before — the Devanagari
FIR from the CCTNS fixture names "गणेश पवार", who is matched by skeleton.
Production would put a trained IndicNER model behind the same interface.

**"Do you use an LLM?"** Optionally, and it is not trusted. It sees only
structured facts already derived from source records, never raw case text, and
its output is discarded if it names any entity absent from those facts. With no
LLM configured, summaries are template-generated and cannot invent anything.

**"What if the NER is wrong?"** Then a node is wrong, which is why every
mention keeps its character span and every merge keeps its reason — you can
find it and correct it.

**"Could this get someone arrested wrongly?"** Not on its own, by design. No
verdict output, findings are leads with visible reasoning, every report carries
limitations, nothing automated acts on a finding, and the custody detector
exists precisely to catch a wrong attribution before anyone relies on it. See
`docs/COMPLIANCE.md`.

**"Does it give the same answer twice?"** Yes, and that took work. Python
randomises string hashing per process, and Louvain is stochastic, so early
builds returned slightly different groupings on different runs. Node ids are now
derived deterministically, the graph is relabelled to integers before community
detection, and Louvain is run across sixteen seeds with the best-scoring
partition kept. `scripts/verify.py` asserts identical output across separate
processes.

**"How does it scale?"** Analytics are the bottleneck — betweenness is O(VE).
At agency scale you move to Neo4j with GDS and compute centrality as a batch
job. `graph/store.py` is the only module that knows the storage engine.

**"Why no React?"** Because it needs to work when the venue wifi doesn't —
no CDN, no build step, no npm. The force-directed layout, the canvas renderer
and the SVG charts are all hand-written. All state comes from the REST API, so
the view layer is a swap, not a rewrite.

**"How did you choose the colours?"** They were validated, not eyeballed. The
categorical slots, the ordinal ramp and the status roles were each run through
a colour-vision-deficiency validator against the exact light and dark surfaces
the app renders on. Node type additionally carries **shape**, because five
types exceed the number of hues that stay separable under CVD when every pair
can appear side by side — so identity never rests on colour alone.
