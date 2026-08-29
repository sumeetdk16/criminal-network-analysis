"""
Chart-ready aggregates for the overview dashboard.

The API returns numbers, not pixels. Each block below states what its data's
*job* is - magnitude, identity, change over time - because that is what decides
the chart form at the other end, and getting it wrong is how dashboards end up
with eight colours telling one story.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

SOURCE_LABELS = {
    "cdr": "Call records",
    "transaction": "Financial",
    "fir": "FIRs",
    "fir_scan": "Scanned FIRs",
    "surveillance": "Surveillance",
    "social_media": "Social media",
    "criminal_record": "Criminal history",
}

FINDING_LABELS = {
    "repeated_co_location": "Repeated co-location",
    "cross_community_broker": "Cross-group broker",
    "clean_skin_bridge": "Clean-skin bridge",
    "communication_burst": "Call-volume burst",
    "burner_handset": "Burner handset",
    "circular_fund_flow": "Circular fund flow",
    "structuring": "Structuring",
    "insulated_actor": "Insulated actor",
    "custody_conflict": "Custody conflict",
}


def _week(ts: str) -> str:
    d = datetime.fromisoformat(ts[:19])
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def build(case_graph, analytics, findings) -> dict:
    G = case_graph.G
    raw = case_graph.raw
    stats = case_graph.stats()

    # -- headline figures. The job is a single number each, so these are stat
    #    tiles, not charts. -------------------------------------------------
    high = [f for f in findings if f["severity"] == "high"]
    comm = analytics.communities()
    tiles = [
        {"key": "subjects", "label": "Subjects resolved", "value": stats["entities_resolved"],
         "hint": f"from {stats['mentions_extracted']} mentions across "
                 f"{stats['source_documents']} records"},
        {"key": "links", "label": "Relationships", "value": stats["edges"],
         "hint": f"across {stats['nodes']} entities"},
        {"key": "flags", "label": "High-severity flags", "value": len(high),
         "hint": f"of {len(findings)} findings", "status": "critical" if high else "good"},
        {"key": "groups", "label": "Sub-groups detected", "value": comm["count"],
         "hint": f"modularity {comm['modularity']}"},
    ]

    # -- severity. Two levels only: a two-bar chart would be worse than a
    #    labelled status row, so this ships as counts with status roles. -----
    sev_counts = Counter(f["severity"] for f in findings)
    severity = [{"key": k, "label": k.title(), "count": sev_counts.get(k, 0),
                 "status": {"high": "critical", "medium": "warning",
                            "low": "good"}[k]}
                for k in ("high", "medium", "low") if sev_counts.get(k)]

    # -- findings by type. Magnitude across nominal categories -> one hue,
    #    horizontal bars, direct labels. ------------------------------------
    type_counts = Counter(f["type"] for f in findings)
    by_type = sorted(
        ({"key": k, "label": FINDING_LABELS.get(k, k.replace("_", " ").title()),
          "value": v,
          "high": sum(1 for f in findings if f["type"] == k and f["severity"] == "high")}
         for k, v in type_counts.items()),
        key=lambda r: -r["value"])

    # -- where the evidence comes from. Magnitude, one hue. ------------------
    src_edges = Counter()
    src_obs = Counter()
    for _, _, d in G.edges(data=True):
        for st in {s["source_type"] for s in d["sources"]}:
            src_edges[st] += 1
        for s in d["sources"]:
            src_obs[s["source_type"]] += 1
    by_source = sorted(
        ({"key": k, "label": SOURCE_LABELS.get(k, k), "value": v,
          "observations": src_obs[k]} for k, v in src_edges.items()),
        key=lambda r: -r["value"])

    # -- corroboration. Ordered categories -> ordinal ramp. ------------------
    corr = Counter(len({s["source_type"] for s in d["sources"]})
                   for _, _, d in G.edges(data=True))
    corroboration = [{"key": str(n), "label": f"{n} source" + ("" if n == 1 else "s"),
                      "value": corr.get(n, 0)}
                     for n in sorted(corr)]

    # -- activity over time. Change over time, two series -> one line chart
    #    with a legend; never two y-axes. Both series are event counts, so
    #    they share one scale honestly. -------------------------------------
    calls = Counter(_week(r["start_time"]) for r in raw["cdr_rows"]
                    if case_graph.phone_index.get(r["caller"])
                    or case_graph.phone_index.get(r["callee"]))
    txns = Counter(_week(r["timestamp"]) for r in raw["txn_rows"])
    weeks = sorted(set(calls) | set(txns))
    activity = {
        "weeks": weeks,
        "series": [
            {"key": "calls", "label": "Attributed calls",
             "points": [calls.get(w, 0) for w in weeks]},
            {"key": "transactions", "label": "Transactions",
             "points": [txns.get(w, 0) for w in weeks]},
        ],
    }

    # -- top subjects by influence. Magnitude, one hue, direct labels. -------
    infl = [r for r in analytics.influence() if r["type"] == "PERSON"][:8]
    top_subjects = [{"id": r["id"], "label": r["label"], "value": round(r["score"], 3),
                     "rank": r["rank"], "community": r["community"],
                     "driver": r.get("top_driver"),
                     "prior_cases": G.nodes[r["id"]].get("prior_cases")}
                    for r in infl]

    # -- what the pipeline did, as an ordered funnel. ------------------------
    ocr = raw.get("ocr", {})
    ocr_docs = [d for d in ocr.get("documents", []) if d.get("ok") and not d.get("skipped")]
    pipeline = [
        {"label": "Source records read", "value": stats["source_documents"]},
        {"label": "Entity mentions extracted", "value": stats["mentions_extracted"]},
        {"label": "Identity merges applied", "value": stats["merge_decisions"]},
        {"label": "Distinct subjects", "value": stats["entities_resolved"]},
    ]

    deva = [n for n, d in G.nodes(data=True)
            if d.get("type") == "PERSON"
            and any(any("ऀ" <= ch <= "ॿ" for ch in a)
                    for a in (d.get("aliases") or []))]

    return {
        "tiles": tiles,
        "severity": severity,
        "findings_by_type": by_type,
        "by_source": by_source,
        "corroboration": corroboration,
        "activity": activity,
        "top_subjects": top_subjects,
        "pipeline": pipeline,
        "highlights": {
            "cross_script_subjects": len(deva),
            "scanned_pages_read": len(ocr_docs),
            "ocr_recovery": (round(sum(d.get("text_accuracy") or 0 for d in ocr_docs)
                                   / len(ocr_docs), 3) if ocr_docs else None),
            "undated_edges": sum(1 for _, _, d in G.edges(data=True)
                                 if not d.get("first_seen")),
        },
        "top_findings": [
            {"id": f["id"], "type": f["type"], "severity": f["severity"],
             "title": f["title"], "entities": f.get("entities", []),
             "entity_labels": f.get("entity_labels", [])}
            for f in findings[:6]
        ],
    }
