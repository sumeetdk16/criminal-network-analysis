# Safeguards, privacy and evidentiary handling

A system that maps relationships between named people can cause real harm if it
is wrong, if it is misread, or if it is misused. This document states what the
prototype already does, and what a deployment would have to add.

## What the prototype implements

**Role-based access control.** Four roles with distinct permission sets
(`auth.py`). An investigator cannot read the audit log. A viewer sees the graph
with phone numbers and accounts redacted and cannot open evidence at all. Every
denial is itself audited.

**Tamper-evident audit log.** Written *before* the answer is returned, on every
route that touches case data: who asked, what they asked, which subject, when.
Each entry carries the SHA-256 hash of the entry before it, so altering any line
breaks every hash after it and `/api/audit/verify` names the broken entry.
Setting `CNAS_AUDIT_KEY` encrypts each payload at rest with AES-256-GCM; the
chain covers the ciphertext, so an auditor can verify integrity without being
able to read the contents. Truncating the tail is still possible on a plain
filesystem, which is why a deployment writes this to WORM storage — but silent
alteration is not.

**Full evidentiary trail.** No claim in the interface is unsourced. Every edge
resolves to the records that produced it, and the Evidence panel quotes the
underlying FIR or surveillance narrative verbatim. Every identity merge records
why it was made and with what score.

**Explainable scoring.** The influence score is a weighted sum of six named
components, all displayed. Every anomaly finding reports the rule that fired
and the numbers behind it. There is no component an investigator cannot inspect
and disagree with.

**Stated uncertainty.** Reports carry a mandatory limitations section.
`CO_NAMED_IN` is described as association rather than participation. Social-media
material is weighted lower and labelled unverified. "No criminal record found"
is reported as *not found*, never as *cleared*.

**Precision-first identity resolution.** A wrong merge is worse than a missed
one, so phone attribution requires an explicit textual cue and ambiguous
mentions stay unattributed.

**Authorisation-gated external access.** The CCTNS and ICJS adapters refuse to
fetch anything without an authorisation reference, which is then recorded in the
audit log alongside the query. Access is read-only in both directions of the
design: the system never writes to a source of record.

**Court-ready export with an integrity digest.** PDF case files carry a
classification banner, the generating officer and time on every page, and a
SHA-256 digest of the content, so a printed copy can be matched against the
audit entry recorded when it was generated.

**Guard-railed generation.** Where an LLM is configured to write summaries, it
receives only structured facts already derived from source records — never raw
case text — and its output is rejected if it names any entity absent from those
facts. Machine-drafted text is labelled in the interface.

## What a deployment must add

**Legal basis.** Access to CDRs, financial records and surveillance product is
governed by the Indian Telegraph Act, the IT Act, the PMLA and the BNSS. The
system must record the authorisation under which each dataset was obtained and
refuse to ingest data without one.

**DPDP Act 2023.** Purpose limitation per case, retention limits with automatic
expiry, data minimisation at ingestion, and the ability to erase a subject's
data when a case closes without a charge.

**Chain of custody.** Cryptographic hashing of every ingested record at
receipt, WORM storage for the audit log, and export bundles that carry hashes
so a court can verify that what is presented is what was collected. Section 63
BSA (electronic-evidence certification) applies to any output offered in
evidence.

**Human-in-the-loop by design.** Nothing in this system should trigger an
automated action. Findings are leads for a human to verify. No arrest,
surveillance authorisation or coercive step should rest on a machine-generated
link alone, and the interface should make that impossible to forget.

**Bias and disparate impact review.** Graph centrality reflects who is
*recorded*, not who is *active*. Communities that are policed more heavily
generate more records and therefore denser graphs. Periodic review of whose
networks the system surfaces, by an independent body, is a requirement rather
than a courtesy.

**Deployment posture.** Air-gapped or on-premise, mTLS between services,
HSM-backed encryption at rest, SSO/LDAP identity, and no outbound internet
dependency anywhere in the stack. The prototype already has no outbound
dependency, which is what makes this possible.

**Integration, not replacement.** Read-only adapters to CCTNS and ICJS rather
than a parallel database. Agencies should not have to migrate to benefit, and a
read-only posture limits the blast radius of a compromise.

## Misuse the design deliberately resists

| Risk | Mitigation in the design |
|---|---|
| Silent identity confusion | Explicit-cue-only phone attribution; every merge auditable |
| "The computer says he's guilty" | No verdict output; influence and findings are leads with visible reasoning |
| Unsourced assertions | Provenance stored per observation; the UI cannot show a link without its sources |
| Fishing expeditions | Every query audited with the subject named; supervisors can review |
| Treating absence as innocence | `record_checked` distinguishes *not found* from *no record* everywhere |
| Over-trusting weak signals | Confidence and corroboration surfaced separately, both filterable |
| Retroactive editing of the audit trail | SHA-256 hash chain; any alteration is detected and located |
| A model inventing a name or a link | LLM sees only derived facts; output rejected if it names anyone else |
| Unlawful access to external systems | Adapters refuse to fetch without an authorisation reference |
| Attributing a call to the wrong person | ICJS custody dates cross-checked against handset activity |
| Mis-reading a Hindi scan into a wrong name | Devanagari OCR skipped entirely without the language pack |
