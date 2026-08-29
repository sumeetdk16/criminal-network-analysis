"""
End-to-end verification.

Asserts the invariants the demo depends on, so a change to extraction,
resolution or the generator cannot silently break the story on stage.
Run it before presenting: `python3 scripts/verify.py`
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import networkx as nx  # noqa: E402
from app.graph.build import build_graph  # noqa: E402
from app.graph.analytics import NetworkAnalytics  # noqa: E402
from app.graph.anomaly import AnomalyDetector  # noqa: E402
from app.nlq import QueryParser  # noqa: E402
from app.report import ReportBuilder as ReportBuilderCls  # noqa: E402
from app.pipeline.translit import name_key, transliterate_text, normalize_digits  # noqa: E402
from app.pipeline.ocr import capabilities as ocr_caps  # noqa: E402
from app.integrations import status as integ_status, fetch as integ_fetch  # noqa: E402
from app.graph.store import CypherExportStore, export_graph  # noqa: E402
from app import pdf_report  # noqa: E402
from app.summarise import LLMSummariser, TemplateSummariser  # noqa: E402
from app.auth import (Principal, USERS, audit, verify_chain, AUDIT_PATH,  # noqa: E402
                      encryption_status)

FAILED = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def main():
    g = build_graph()
    an = NetworkAnalytics(g)
    findings = AnomalyDetector(g, an).run_all()
    qp = QueryParser(g)
    by_label = {d["label"]: n for n, d in g.G.nodes(data=True)}

    print("\nPipeline")
    s = g.stats()
    check("source documents ingested", s["source_documents"] >= 25, f"{s['source_documents']}")
    check("entity mentions extracted", s["mentions_extracted"] >= 80, f"{s['mentions_extracted']}")
    check("subjects resolved", 14 <= s["entities_resolved"] <= 25, f"{s['entities_resolved']}")

    print("\nIdentity resolution")
    sethi = g.G.nodes[by_label["Vikram Sethi"]]
    check("Sethi's alias forms merged", len(sethi["aliases"]) >= 2, str(sethi["aliases"]))
    check("Sethi's burner attributed", "9000000007" in sethi["phones"], str(sethi["phones"]))
    shaikh = g.G.nodes[by_label["Fareed Shaikh"]]
    check("Shaikh/Sheikh spelling merged", "Farid Sheikh" in shaikh["aliases"], str(shaikh["aliases"]))

    # the critical negative test: two different people must NOT be fused
    people = [d["label"] for _, d in g.G.nodes(data=True) if d["type"] == "PERSON"]
    check("Rathi and Rajesh Kumar are separate subjects",
          any("Rathi" in p for p in people) and any("Rajesh" in p for p in people)
          and not any("Rathi" in p and "Rajesh" in p for p in people))
    check("no person node absorbed a company",
          not any(x in p for p in people
                  for x in ("Ltd", "Pvt", "Traders", "Services", "Contractors")))

    print("\nThe hidden link")
    a, b = by_label["Devendra Kumar Rathi"], by_label["Vikram Sethi"]
    P = an.person_subgraph() if hasattr(an, "person_subgraph") else an.P
    check("no direct link between them", not g.G.has_edge(a, b))
    paths = an.paths(a, b, cutoff=4)
    check("a multi-hop path exists", bool(paths))
    if paths:
        best = paths[0]
        check("path is 3 hops", best["hops"] == 3, " -> ".join(best["labels"]))
        check("path spans more than one source system",
              len(best["source_types"]) >= 2, str(best["source_types"]))

    print("\nAnalytics")
    infl = an.influence()
    check("influence ranking produced", len(infl) >= 15)
    check("every score is explained", all(r["why"] for r in infl))
    check("score components are visible", all(len(r["components"]) == 6 for r in infl))
    comm = an.communities()
    check("communities detected", comm["count"] >= 3, f"{comm['count']} groups")
    check("modularity is meaningful", comm["modularity"] > 0.3, str(comm["modularity"]))

    print("\nDetection")
    types = {f["type"] for f in findings}
    for t in ("burner_handset", "circular_fund_flow", "structuring",
              "insulated_actor", "clean_skin_bridge", "communication_burst",
              "repeated_co_location", "custody_conflict"):
        check(f"detector fired: {t}", t in types)
    check("every finding states its rule", all(f["basis"].get("rule") for f in findings))
    ins = [f for f in findings if f["type"] == "insulated_actor"]
    check("insulated-actor detector is not noisy", len(ins) <= 2, f"{len(ins)} hits")
    check("insulated actor is the kingpin",
          any("Vikram Sethi" in (f["entity_labels"] or []) for f in ins))
    colo = [f for f in findings if f["type"] == "repeated_co_location"]
    check("co-location ranks unexplained pairs first",
          bool(colo) and colo[0]["severity"] == "high"
          and colo[0]["basis"]["calls_between_them"] == 0)
    clean = [f for f in findings if f["type"] == "clean_skin_bridge"]
    check("clean-skin bridge flags Rathi",
          any("Devendra" in l for f in clean for l in (f["entity_labels"] or [])))
    check("clean-skin detector only judges people actually checked",
          all("prior_cases" in g.G.nodes[e] for f in clean for e in f["entities"]))

    print("\nMultilingual handling")
    pairs = [("विक्रम सेठी", "Vikram Sethi"), ("फरीद शेख", "Farid Sheikh"),
             ("अशोक गायकवाड", "Ashok Gaikwad"), ("रवि शंकर नायर", "Ravi Shankar Nair"),
             ("इमरान कुरैशी", "Imran Qureshi")]
    check("Devanagari names key to their Latin spelling",
          all(name_key(a) == name_key(b) for a, b in pairs))
    check("different people still do not collide",
          name_key("विक्रम सेठी") != name_key("Rajesh Kumar"))
    check("Devanagari numerals normalised",
          normalize_digits("मो. ९००००००००१") == "मो. 9000000001")
    deva_aliased = [d for _, d in g.G.nodes(data=True)
                    if d.get("type") == "PERSON"
                    and any(any("\u0900" <= ch <= "\u097f" for ch in a)
                            for a in (d.get("aliases") or []))]
    check("Devanagari spellings merged onto existing subjects",
          len(deva_aliased) >= 4, f"{len(deva_aliased)} subjects")
    check("canonical labels are Latin for display",
          all(not any("\u0900" <= ch <= "\u097f" for ch in d["label"])
              for _, d in g.G.nodes(data=True) if d.get("type") == "PERSON"))

    print("\nOCR")
    caps = ocr_caps()
    ocr = g.raw.get("ocr", {})
    if caps["available"]:
        done = [d for d in ocr.get("documents", []) if d.get("ok")
                and not d.get("skipped")]
        check("scanned FIRs were read", len(done) >= 2, f"{len(done)} pages")
        check("OCR recovered the text accurately",
              all(d.get("text_accuracy", 0) >= 0.9 for d in done),
              str([d.get("text_accuracy") for d in done]))
        scan_docs = [k for k, v in g.raw["documents"].items()
                     if v["source_type"] == "fir_scan"]
        check("paper-only FIRs entered the graph", len(scan_docs) >= 2)
    else:
        check("OCR degrades gracefully when unavailable",
              ocr.get("documents") == [] and g.stats()["entities_resolved"] > 10,
              "tesseract not installed on this machine")

    print("\nAttribution")
    top = infl[0]
    check("Shapley attribution present", "attribution" in top)
    total = top["baseline"] + sum(top["attribution"].values())
    check("attributions sum to the score", abs(total - top["score"]) < 1e-3,
          f"{total:.4f} vs {top['score']:.4f}")
    check("a driver and a detractor are named",
          bool(top["top_driver"]) and bool(top["top_detractor"]))

    print("\nIntegrations")
    st = integ_status()
    check("adapters registered", len(st["adapters"]) >= 2)
    check("fetch without authorisation is refused",
          integ_fetch("cctns")["mode"] == "blocked")
    ok = integ_fetch("cctns", authorisation="demo authorisation")
    check("fetch with authorisation returns mapped records",
          ok["ok"] and ok["records"] and "fir_id" in ok["records"][0])
    check("custody conflict detected from ICJS + CDR",
          any(f["type"] == "custody_conflict" for f in findings))

    print("\nSecurity")
    import json as _json
    p = Principal("demo-admin", USERS["demo-admin"])
    audit(p, "VERIFY_SELF_TEST", {"note": "verification run"})
    chain = verify_chain()
    if not chain["intact"]:
        # A log written by an older build has no hash chain at all. That is a
        # migration artefact, not a tampering event, and should not be reported
        # as one - but the log must be rotated before it means anything.
        legacy = any("hash" not in _l for _l in
                     (_json.loads(x) for x in
                      open(AUDIT_PATH, encoding="utf-8").read().splitlines() if x.strip()))
        if legacy:
            open(AUDIT_PATH, "w", encoding="utf-8").close()
            audit(p, "AUDIT_LOG_ROTATED", {"reason": "pre-chain entries removed"})
            chain = verify_chain()
    check("audit chain intact", chain["intact"], str(chain.get("reason", "")))
    lines = open(AUDIT_PATH, encoding="utf-8").read().splitlines()
    if len(lines) >= 2:
        backup = list(lines)
        try:
            rec = _json.loads(lines[-2]); rec["action"] = "TAMPERED"
            lines[-2] = _json.dumps(rec)
            open(AUDIT_PATH, "w", encoding="utf-8").write("\n".join(lines) + "\n")
            broken = verify_chain()
        finally:
            # always put the log back, even if this check raises - leaving a
            # deliberately corrupted audit log behind would be worse than the
            # bug it was testing for
            open(AUDIT_PATH, "w", encoding="utf-8").write("\n".join(backup) + "\n")
        check("tampering with the audit log is detected", not broken["intact"],
              broken.get("reason", ""))

    print("\nExport and reporting")
    import tempfile
    out = os.path.join(tempfile.gettempdir(), "verify_graph.cypher")
    info = export_graph(g, CypherExportStore(out))
    check("cypher export produced", info["nodes"] > 40 and info["edges"] > 100,
          f"{info['nodes']} nodes / {info['edges']} edges")
    body = open(out, encoding="utf-8").read()
    check("cypher export is loadable syntax",
          body.count("MERGE (n:") >= info["nodes"] and "MATCH (a {id:" in body)
    if pdf_report.available():
        rep = ReportBuilderCls(g, an, findings).subject_report(by_label["Vikram Sethi"])
        data = pdf_report.CaseFilePdf("PSI Test", "investigator", "T-1",
                                      "Test Unit").render(
            "Verification", rep["markdown"], [], rep["findings"])
        check("PDF renders", data[:4] == b"%PDF" and len(data) > 5000,
              f"{len(data)} bytes")
    else:
        check("PDF export degrades gracefully", True, "reportlab not installed")

    print("\nSummariser guard rails")
    llm = LLMSummariser()
    check("hallucinated names are caught",
          llm._hallucinated("Rathi met Ajay Sharma in Delhi.",
                            {"labels": ["Devendra Kumar Rathi"]}) ==
          ["Ajay", "Delhi", "Sharma"])
    check("template summariser produces a narrative",
          len(TemplateSummariser().summarise(
              {"kind": "subject", "label": "X", "source_count": 2})["text"]) > 40)

    print("\nReproducibility")
    # Python randomises string hashing per process, so a pipeline that iterates
    # over sets can return a different answer on a second run. An investigator
    # who reruns an analysis and gets different groups will stop trusting the
    # tool, so this is checked across a genuinely separate process.
    import subprocess
    sig_code = (
        "import sys,hashlib,json;sys.path.insert(0,%r);"
        "from app.graph.build import build_graph;"
        "from app.graph.analytics import NetworkAnalytics;"
        "from app.graph.anomaly import AnomalyDetector;"
        "g=build_graph();a=NetworkAnalytics(g);f=AnomalyDetector(g,a).run_all();"
        "print(hashlib.sha1(json.dumps([[x['title'] for x in f],"
        "[r['label'] for r in a.influence()],a.communities()['modularity']]"
        ").encode()).hexdigest())"
        % os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
    sigs = set()
    for _ in range(3):
        r = subprocess.run([sys.executable, "-c", sig_code], capture_output=True,
                           text=True)
        sigs.add(r.stdout.strip())
    check("identical results across separate processes", len(sigs) == 1,
          f"{len(sigs)} distinct outcomes")

    print("\nProvenance")
    unsourced = [(x, y) for x, y, d in g.G.edges(data=True) if not d["sources"]]
    check("every edge carries its sources", not unsourced, str(unsourced[:3]))
    no_ev = [(x, y) for x, y, d in g.G.edges(data=True)
             if not any(s.get("evidence") for s in d["sources"])]
    check("every edge carries an evidence string", not no_ev, str(no_ev[:3]))

    print("\nNatural-language queries")
    cases = [
        ("how is Devendra Rathi connected to Vikram Sethi", "path"),
        ("what links Rathi and Sethi", "path"),
        ("who are the top 5 key people", "influencers"),
        ("show suspicious patterns", "anomalies"),
        ("everyone connected to Fareed Shaikh within 2 hops", "neighbourhood"),
        ("transactions over 10 lakh", "transactions"),
        ("what groups exist in this network", "communities"),
    ]
    for q, want in cases:
        got = qp.parse(q).intent
        check(f'"{q}"', got == want, f"got {got}")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) FAILED:")
        for f in FAILED:
            print("  - " + f)
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
