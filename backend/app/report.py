"""
Case summary generation.

Produces a narrative an investigator can put in a case diary or a briefing:
plain language, every claim followed by the record it rests on, and an explicit
limitations section. The limitations section is not decoration - a report that
does not state what the system could NOT establish invites an investigator to
over-read it.
"""

from __future__ import annotations

from datetime import datetime


def _fmt_money(n):
    return f"Rs {n:,}"


class ReportBuilder:
    def __init__(self, case_graph, analytics, findings, summariser=None):
        self.cg = case_graph
        self.an = analytics
        self.findings = findings
        from .summarise import TemplateSummariser
        self.summariser = summariser or TemplateSummariser()

    def label(self, nid):
        return self.cg.G.nodes[nid].get("label", nid)

    # ------------------------------------------------------------------
    def subject_report(self, node: str) -> dict:
        if node not in self.cg.G:
            return {"error": "unknown node"}
        d = self.cg.G.nodes[node]
        infl = {r["id"]: r for r in self.an.influence()}
        me = infl.get(node)
        comm = self.an.communities()["assignment"].get(node)

        # direct links, strongest first
        links = []
        for nb in self.cg.G.neighbors(node):
            e = self.cg.G[node][nb]
            links.append({
                "node": nb, "label": self.label(nb),
                "type": self.cg.G.nodes[nb].get("type"),
                "relationship": e["types"], "confidence": e["confidence"],
                "observations": e["observations"],
                "independent_sources": sorted({s["source_type"] for s in e["sources"]}),
                "first_seen": e["first_seen"], "last_seen": e["last_seen"],
                "evidence": [s["evidence"] for s in e["sources"][:3] if s["evidence"]],
            })
        links.sort(key=lambda l: (-len(l["independent_sources"]), -l["confidence"]))

        related = [f for f in self.findings if node in f.get("entities", [])]

        lines = []
        lines.append(f"## Subject profile: {d.get('label')}")
        lines.append("")
        if d.get("aliases"):
            lines.append(f"**Recorded name variants:** {', '.join(d['aliases'])}  ")
        if d.get("phones"):
            lines.append(f"**Attributed numbers:** {', '.join(d['phones'])}  ")
        if d.get("accounts"):
            lines.append(f"**Accounts:** {', '.join(d['accounts'])}  ")
        if "prior_cases" in d:
            lines.append(f"**Criminal history:** {d.get('prior_cases')} prior case(s) — "
                         f"{d.get('offence_categories', '-')} ({d.get('active_period','-')})  ")
        else:
            lines.append("**Criminal history:** no entry located in the criminal-history "
                         "database. This means *not found*, not *cleared*.  ")
        lines.append("")

        if me:
            lines.append(f"### Position in the network")
            lines.append(f"Ranked **{me['rank']}** of {len(infl)} by influence "
                         f"(score {me['score']:.3f}), in detected group {comm}. "
                         f"{me['why']}")
            lines.append("")
            lines.append("| Component | Normalised value |")
            lines.append("|---|---|")
            for k, v in me["components"].items():
                lines.append(f"| {k.replace('_',' ')} | {v} |")
            lines.append("")

        if d.get("merge_evidence"):
            lines.append("### Identity resolution")
            lines.append("This subject was assembled from records held under different "
                         "spellings. The merges applied were:")
            for m in d["merge_evidence"][:8]:
                lines.append(f"- {m}")
            lines.append("")

        lines.append("### Established links")
        lines.append("")
        lines.append("| Linked to | Relationship | Sources | Corroboration | Confidence |")
        lines.append("|---|---|---|---|---|")
        for l in links[:15]:
            lines.append(f"| {l['label']} ({l['type']}) | {', '.join(l['relationship'])} | "
                         f"{', '.join(l['independent_sources'])} | "
                         f"{len(l['independent_sources'])} source(s) | {l['confidence']:.2f} |")
        lines.append("")

        if related:
            lines.append("### Flagged patterns involving this subject")
            for f in related:
                lines.append(f"- **{f['title']}** ({f['severity']}) — {f['description']}")
            lines.append("")

        lines.append("### Limitations")
        lines.append("- Links are derived from the records supplied to the system. "
                     "Absence of a link is not evidence of absence of a relationship.")
        lines.append("- Relationships marked `CO_NAMED_IN` mean two people appear in the "
                     "same document. That is association, not participation.")
        lines.append("- Social-media derived material is unverified and is weighted "
                     "accordingly; it should not be relied on without corroboration.")
        lines.append("- This output is an investigative aid. It is not evidence, and no "
                     "coercive action should rest on it without independent verification.")

        narrative = self.summariser.summarise({
            "kind": "subject", "label": d.get("label"),
            "source_count": len(d.get("sources", []) or []),
            "aliases": d.get("aliases", []),
            "prior_cases": d.get("prior_cases"),
            "record_checked": "prior_cases" in d,
            "offences": d.get("offence_categories"),
            "rank": me["rank"] if me else None,
            "population": len(infl),
            "top_driver": (me or {}).get("top_driver"),
            "top_links": [f"{l['label']} ({', '.join(l['relationship']).lower()})"
                          for l in links[:3]],
            "finding_titles": [f["title"] for f in related[:3]],
        })

        return {
            "node": node, "label": d.get("label"), "narrative": narrative,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "markdown": "\n".join(lines),
            "links": links, "findings": related,
            "influence": me, "community": comm,
        }

    # ------------------------------------------------------------------
    def link_report(self, a: str, b: str, cutoff: int = 5) -> dict:
        paths = self.an.paths(a, b, cutoff=cutoff)
        la, lb = self.label(a), self.label(b)
        lines = [f"## Connection analysis: {la} and {lb}", ""]
        if not paths:
            lines.append(f"No connection between {la} and {lb} was found within "
                         f"{cutoff} hops in the records supplied.")
            return {"markdown": "\n".join(lines), "paths": []}

        best = paths[0]
        srcs = ", ".join(best["source_types"])
        lines.append(f"The shortest connection runs through **{best['hops'] - 1} "
                     f"intermediary/intermediaries** and is supported by {srcs} records.")
        lines.append("")
        lines.append("**" + "  →  ".join(best["labels"]) + "**")
        lines.append("")
        lines.append("### How each step is established")
        for i, leg in enumerate(best["legs"], 1):
            lines.append(f"{i}. **{leg['from_label']} → {leg['to_label']}** "
                         f"({', '.join(leg['types'])}, confidence {leg['confidence']:.2f}, "
                         f"{leg['observations']} observation(s))")
            for s in leg["sources"][:2]:
                if s.get("evidence"):
                    lines.append(f"   - `{s['source_type']}:{s['source_id']}` — {s['evidence']}")
        lines.append("")
        if len(paths) > 1:
            lines.append("### Alternative routes")
            for p in paths[1:4]:
                lines.append(f"- {' → '.join(p['labels'])} "
                             f"({p['hops']} hops, {', '.join(p['source_types'])})")
            lines.append("")
        lines.append("### Reading this correctly")
        lines.append("A path shows that information, money or instructions *could* travel "
                     "between these two subjects through the intermediaries named. It does "
                     "not by itself establish that either subject knows the other, and it "
                     "is not proof of a common purpose.")
        narrative = self.summariser.summarise({
            "kind": "path", "labels": best["labels"], "hops": best["hops"],
            "source_types": best["source_types"], "legs": best["legs"],
        })
        return {"markdown": "\n".join(lines), "paths": paths,
                "narrative": narrative,
                "generated_at": datetime.now().isoformat(timespec="seconds")}

    # ------------------------------------------------------------------
    def case_overview(self) -> dict:
        stats = self.cg.stats()
        infl = self.an.influence()
        comm = self.an.communities()
        high = [f for f in self.findings if f["severity"] == "high"]

        lines = ["# Network analysis summary", ""]
        lines.append(f"Generated {datetime.now().strftime('%d %B %Y, %H:%M')} from "
                     f"{stats['source_documents']} source records across "
                     f"{len(stats['edges_by_type'])} relationship types.")
        lines.append("")
        lines.append(f"- **{stats['entities_resolved']} distinct individuals** were resolved "
                     f"from {stats['mentions_extracted']} raw mentions "
                     f"({stats['merge_decisions']} identity merges applied).")
        lines.append(f"- The network contains **{stats['nodes']} entities** and "
                     f"**{stats['edges']} relationships**.")
        lines.append(f"- **{comm['count']} sub-groups** were detected "
                     f"(modularity {comm['modularity']}).")
        lines.append(f"- **{len(high)} high-severity patterns** were flagged.")
        lines.append("")
        lines.append("## Most influential individuals")
        lines.append("")
        lines.append("| Rank | Name | Score | Group | Basis |")
        lines.append("|---|---|---|---|---|")
        for r in infl[:8]:
            lines.append(f"| {r['rank']} | {r['label']} | {r['score']:.3f} | "
                         f"{r['community']} | {r['why']} |")
        lines.append("")
        lines.append("## High-severity findings")
        for f in high:
            lines.append(f"### {f['title']}")
            lines.append(f"*{f['type']} — {f['severity']} severity*")
            lines.append("")
            lines.append(f["description"])
            lines.append("")
            lines.append(f"Rule applied: `{f['basis'].get('rule','-')}`")
            lines.append("")
        lines.append("## Caveats")
        lines.append("All findings above are machine-generated leads for human review. "
                     "Every relationship in this report can be traced to the source record "
                     "that produced it; investigators should verify each before acting.")
        return {"markdown": "\n".join(lines),
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "stats": stats}
