"""
Suspicious pattern detection.

Each detector returns a finding with an explicit `basis` - the rule that fired,
the numbers behind it, and the source records. Nothing here is a black box:
an investigator can disagree with a finding and see exactly why it was raised.

Detectors are deliberately conservative. A false positive costs an
investigator hours; in this domain it can cost a person their liberty.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime

import networkx as nx

CTR_THRESHOLD = 50_000          # cash transaction reporting threshold (INR)
STRUCTURING_BAND = 0.85         # deposits at >=85% of the threshold are "just under"


def _day(ts: str) -> str:
    return ts[:10]


def _minutes_between(t1: str, t2: str) -> float:
    return abs((datetime.fromisoformat(t2) - datetime.fromisoformat(t1)).total_seconds()) / 60


class AnomalyDetector:
    def __init__(self, case_graph, analytics):
        self.cg = case_graph
        self.an = analytics
        self.raw = case_graph.raw

    # ------------------------------------------------------------------ 1
    def communication_bursts(self, z=2.5, min_calls=6):
        by_day = defaultdict(int)
        by_day_pairs = defaultdict(list)
        for r in self.raw["cdr_rows"]:
            a = self.cg.phone_index.get(r["caller"])
            b = self.cg.phone_index.get(r["callee"])
            if not a or not b:
                continue
            d = _day(r["start_time"])
            by_day[d] += 1
            by_day_pairs[d].append((a, b, r["cell_tower"]))
        if len(by_day) < 5:
            return []
        counts = list(by_day.values())
        mu, sd = statistics.mean(counts), (statistics.pstdev(counts) or 1)
        out = []
        for d, c in sorted(by_day.items()):
            score = (c - mu) / sd
            if score >= z and c >= min_calls:
                people = defaultdict(int)
                cells = defaultdict(int)
                for a, b, cell in by_day_pairs[d]:
                    people[a] += 1
                    people[b] += 1
                    cells[cell] += 1
                top = sorted(people.items(), key=lambda kv: -kv[1])[:5]
                out.append({
                    "type": "communication_burst",
                    "severity": "high" if score >= 3.5 else "medium",
                    "title": f"Abnormal call volume on {d}",
                    "description": (f"{c} attributed calls on {d} against a daily mean of "
                                    f"{mu:.1f} (z = {score:.1f}). Dominant cell site: "
                                    f"{max(cells, key=cells.get)}."),
                    "entities": [n for n, _ in top],
                    "entity_labels": [self.cg.G.nodes[n].get("label") for n, _ in top],
                    "basis": {"rule": "daily call count z-score >= 2.5",
                              "observed": c, "mean": round(mu, 1),
                              "z_score": round(score, 2)},
                    "sources": ["CDR"], "date": d,
                })
        return out

    # ------------------------------------------------------------------ 2
    def burner_handsets(self, max_contacts=3, max_active_days=25):
        spans = defaultdict(lambda: {"first": None, "last": None,
                                     "contacts": set(), "calls": 0})
        for r in self.raw["cdr_rows"]:
            for me, other in ((r["caller"], r["callee"]), (r["callee"], r["caller"])):
                s = spans[me]
                s["contacts"].add(other)
                s["calls"] += 1
                t = r["start_time"]
                s["first"] = min(s["first"], t) if s["first"] else t
                s["last"] = max(s["last"], t) if s["last"] else t
        out = []
        for phone, s in spans.items():
            owner = self.cg.phone_index.get(phone)
            if not owner:
                continue
            days = (datetime.fromisoformat(s["last"]) - datetime.fromisoformat(s["first"])).days
            if len(s["contacts"]) <= max_contacts and days <= max_active_days and s["calls"] >= 3:
                other_numbers = [p for p in self.cg.G.nodes[owner].get("phones", [])
                                 if p != phone]
                out.append({
                    "type": "burner_handset",
                    "severity": "high" if other_numbers else "medium",
                    "title": f"Probable burner handset {phone}",
                    "description": (f"{phone} contacted only {len(s['contacts'])} number(s) "
                                    f"over a {days}-day window and then fell silent"
                                    + (f". The same subject also operates {', '.join(other_numbers)}."
                                       if other_numbers else ".")),
                    "entities": [owner],
                    "entity_labels": [self.cg.G.nodes[owner].get("label")],
                    "basis": {"rule": f"<= {max_contacts} unique contacts and "
                                      f"<= {max_active_days} active days",
                              "unique_contacts": len(s["contacts"]),
                              "active_days": days, "calls": s["calls"]},
                    "sources": ["CDR"],
                })
        return out

    # ------------------------------------------------------------------ 3
    def circular_funds(self, min_amount=1_000_000):
        D = nx.DiGraph()
        for r in self.raw["txn_rows"]:
            a = self.cg.account_index.get(r["from_account"]) or \
                self.cg.resolve_name(r["from_name"])
            b = self.cg.account_index.get(r["to_account"]) or \
                self.cg.resolve_name(r["to_name"])
            if not a or not b or a == b:
                continue
            if D.has_edge(a, b):
                D[a][b]["amount"] += int(r["amount_inr"])
                D[a][b]["txns"].append(r["txn_id"])
            else:
                D.add_edge(a, b, amount=int(r["amount_inr"]), txns=[r["txn_id"]])
        out = []
        seen = set()
        for cycle in nx.simple_cycles(D, length_bound=5):
            if len(cycle) < 3:
                continue
            key = tuple(sorted(cycle))
            if key in seen:
                continue
            legs, total = [], 0
            ok = True
            for x, y in zip(cycle, cycle[1:] + cycle[:1]):
                if not D.has_edge(x, y):
                    ok = False
                    break
                amt = D[x][y]["amount"]
                total += amt
                legs.append({"from": self.cg.G.nodes[x].get("label"),
                             "to": self.cg.G.nodes[y].get("label"),
                             "amount_inr": amt, "txn_ids": D[x][y]["txns"][:8]})
            if not ok or total < min_amount:
                continue
            seen.add(key)
            out.append({
                "type": "circular_fund_flow",
                "severity": "high",
                "title": "Funds returning to origin through intermediaries",
                "description": (f"Rs {total:,} moved through a closed loop of "
                                f"{len(cycle)} parties, returning to the originator. "
                                f"This round-tripping pattern is characteristic of "
                                f"layering."),
                "entities": list(cycle),
                "entity_labels": [self.cg.G.nodes[n].get("label") for n in cycle],
                "basis": {"rule": "directed cycle in the payment graph, total >= Rs 10 lakh",
                          "cycle_length": len(cycle), "total_inr": total, "legs": legs},
                "sources": ["TXN"],
                "_sort": (-total, "|".join(sorted(cycle))),
            })
        # nx.simple_cycles yields cycles in an order that depends on hash
        # ordering, so ranking must be explicit or the report changes between
        # runs. Largest flows first, ties broken canonically.
        out.sort(key=lambda f: f.pop("_sort"))
        return out[:3]

    # ------------------------------------------------------------------ 4
    def structuring(self, min_count=5):
        groups = defaultdict(list)
        for r in self.raw["txn_rows"]:
            amt = int(r["amount_inr"])
            if r["mode"] != "CASH":
                continue
            if CTR_THRESHOLD * STRUCTURING_BAND <= amt < CTR_THRESHOLD:
                groups[(r["from_name"], r["to_name"])].append(r)
        out = []
        for (src, dst), rows in groups.items():
            if len(rows) < min_count:
                continue
            a = self.cg.resolve_name(src)
            b = self.cg.resolve_name(dst)
            total = sum(int(r["amount_inr"]) for r in rows)
            out.append({
                "type": "structuring",
                "severity": "high",
                "title": f"Cash deposits kept just below the reporting threshold",
                "description": (f"{len(rows)} cash transactions from {src} to {dst} "
                                f"totalling Rs {total:,}, every one of them between "
                                f"Rs {int(CTR_THRESHOLD*STRUCTURING_BAND):,} and "
                                f"Rs {CTR_THRESHOLD:,}. Splitting a sum to stay under the "
                                f"reporting threshold is a recognised laundering method."),
                "entities": [x for x in (a, b) if x],
                "entity_labels": [src, dst],
                "basis": {"rule": f">= {min_count} cash transactions in "
                                  f"[{int(CTR_THRESHOLD*STRUCTURING_BAND)}, {CTR_THRESHOLD})",
                          "count": len(rows), "total_inr": total,
                          "txn_ids": [r["txn_id"] for r in rows][:20]},
                "sources": ["TXN"],
            })
        return out

    # ------------------------------------------------------------------ 5
    def insulated_actors(self):
        """
        Command roles that are influential *despite* having few direct contacts.

        Earlier versions of this detector used a hand-tuned ratio of two-hop
        reach to degree. That broke the moment the corpus grew: adding a handful
        of documents changed one subject's degree and the detector went silent,
        which is the worst possible failure for a rule an investigator is asked
        to trust.

        It now reads the Shapley attribution of the influence score directly.
        A subject whose ranking is driven by betweenness or eigenvector
        centrality, while contact count is the weakest of the six contributions
        to their score, is by definition someone whose importance does not come
        from knowing a lot of people - which is what "insulated behind
        intermediaries" actually means. The comparison is between components of
        one score, so there is no constant to recalibrate when the data grows.
        """
        P = self.an.P
        infl = {r["id"]: r for r in self.an.influence()}
        people = [n for n in P if self.cg.G.nodes[n].get("type") == "PERSON"]
        if len(people) < 6:
            return []
        top_n = max(5, len(people) // 4)

        out = []
        for n in people:
            r = infl.get(n)
            if not r or r["rank"] > top_n:
                continue
            attribution = r.get("attribution", {})
            if r.get("top_driver") not in ("betweenness", "eigenvector"):
                continue
            # "Weakest contributor" as a strict single minimum is brittle: two
            # components can sit within noise of each other and swap which one
            # is technically last, silently turning the detector off. Degree
            # only needs to be among the bottom two of the six to mean "this
            # ranking isn't built on contact volume."
            weakest_two = {k for k, _ in
                          sorted(attribution.items(), key=lambda kv: kv[1])[:2]}
            if "degree" not in weakest_two:
                continue
            d = self.cg.G.nodes[n]
            handsets = len(d.get("phones", []) or [])
            priors = int(d.get("prior_cases", 0) or 0)
            if handsets < 2 and priors < 3:
                continue
            reach = len(nx.single_source_shortest_path_length(P, n, cutoff=2)) - 1
            deg = P.degree(n)
            out.append({
                "type": "insulated_actor",
                "severity": "high",
                "title": f"{d.get('label')} appears insulated behind intermediaries",
                "description": (
                    f"{d.get('label')} ranks {r['rank']} of {len(infl)} by influence, "
                    f"but that ranking is not built on contact volume: with "
                    f"{deg} direct contacts they reach {reach} people in two hops, "
                    f"and of the six scoring components their contact count "
                    f"contributes among the least ({attribution.get('degree', 0):+.3f}) "
                    f"while their position between other members contributes the "
                    f"most ({attribution.get(r['top_driver'], 0):+.3f}). "
                    f"They operate {handsets} attributed handset(s) and carry "
                    f"{priors} prior case(s). Ranking by contact count alone would "
                    f"place this subject well down the list."),
                "entities": [n], "entity_labels": [d.get("label")],
                "basis": {"rule": "influence rank in the top quartile, with the score "
                                  "driven by betweenness/eigenvector and held back by "
                                  "degree, plus multiple handsets or >= 3 prior cases",
                          "influence_rank": r["rank"], "direct_contacts": deg,
                          "two_hop_reach": reach,
                          "attribution": attribution,
                          "top_driver": r.get("top_driver"),
                          "top_detractor": r.get("top_detractor"),
                          "attributed_handsets": handsets, "prior_cases": priors},
                "sources": ["CDR", "FIR", "SUR"],
            })
        return out

    # ------------------------------------------------------------------ 6
    def clean_skin_bridges(self):
        """
        A person with no criminal record who nonetheless sits adjacent to a
        high-risk cluster. Historically the hardest connection for an
        investigator to notice, because nothing about the individual triggers a
        database hit.
        """
        P = self.an.P
        infl = {r["id"]: r for r in self.an.influence()}
        comm = self.an.communities()["assignment"]
        risky_comm = defaultdict(float)
        for n, d in self.cg.G.nodes(data=True):
            if d.get("type") == "PERSON" and n in comm:
                risky_comm[comm[n]] += float(d.get("prior_cases", 0) or 0)

        out = []
        for n, d in self.cg.G.nodes(data=True):
            if d.get("type") != "PERSON" or n not in P:
                continue
            # "No record" is only meaningful for someone who was actually checked
            # against the criminal-history database. Absence of an entry means
            # unknown, not clean, and must never be reported as clean.
            if "prior_cases" not in d:
                continue
            if int(d.get("prior_cases", 0) or 0) > 0:
                continue
            flagged = []
            for m in P.neighbors(n):
                md = self.cg.G.nodes[m]
                if md.get("type") == "PERSON" and int(md.get("prior_cases", 0) or 0) >= 2:
                    flagged.append(md.get("label"))
            reach = nx.single_source_shortest_path_length(P, n, cutoff=3)
            reached_flagged = [self.cg.G.nodes[m].get("label") for m in reach
                               if self.cg.G.nodes[m].get("type") == "PERSON"
                               and int(self.cg.G.nodes[m].get("prior_cases", 0) or 0) >= 3]
            if not reached_flagged:
                continue
            worst = max((int(self.cg.G.nodes[m].get("prior_cases", 0) or 0)
                         for m in reach
                         if self.cg.G.nodes[m].get("type") == "PERSON"), default=0)
            out.append({
                "type": "clean_skin_bridge",
                "severity": "high" if (flagged or worst >= 4) else "medium",
                "title": f"{d.get('label')} has no record but is linked to flagged subjects",
                "description": (f"{d.get('label')} carries no prior criminal record, so no "
                                f"database check would flag them. Within three hops they "
                                f"reach {', '.join(sorted(set(reached_flagged)))}"
                                + (f", and are in direct contact with {', '.join(flagged)}."
                                   if flagged else ".")),
                "entities": [n], "entity_labels": [d.get("label")],
                "basis": {"rule": "no prior record, but reaches a subject with >= 3 prior "
                                  "cases within 3 hops",
                          "direct_flagged_contacts": flagged,
                          "reached_within_3_hops": sorted(set(reached_flagged)),
                          "influence_rank": infl.get(n, {}).get("rank")},
                "sources": ["CDR", "TXN", "SUR"],
            })
        return out

    # ------------------------------------------------------------------ 7
    def co_location(self, window_minutes=90, min_events=3):
        """
        Two subjects whose handsets are repeatedly active on the same cell site
        within a short window. Sharing a neighbourhood is not suspicious; being
        in the same place at the same time, repeatedly, across different places,
        is the pattern a surveillance team would be tasked to confirm.

        Pairs that already have a heavy call relationship are reported at lower
        severity - people who speak daily being in the same place is expected,
        and flagging it buries the pairs that never call each other but keep
        turning up together.
        """
        events = []
        for r in self.raw["cdr_rows"]:
            for phone in (r["caller"], r["callee"]):
                node = self.cg.phone_index.get(phone)
                if node:
                    events.append((r["start_time"], r["cell_tower"], node))
        events.sort()

        from collections import defaultdict
        pairs = defaultdict(lambda: {"n": 0, "cells": set(), "times": []})
        for i, (t1, cell1, a) in enumerate(events):
            for t2, cell2, b in events[i + 1:]:
                if _minutes_between(t1, t2) > window_minutes:
                    break
                if a == b or cell1 != cell2:
                    continue
                key = tuple(sorted([a, b]))
                p = pairs[key]
                p["n"] += 1
                p["cells"].add(cell1)
                if len(p["times"]) < 6:
                    p["times"].append(t1[:16].replace("T", " "))

        out = []
        for (a, b), p in pairs.items():
            if p["n"] < min_events or len(p["cells"]) < 2:
                continue
            e = self.cg.G[a][b] if self.cg.G.has_edge(a, b) else None
            call_count = 0
            if e:
                call_count = max((s.get("call_count", 0) for s in e["sources"]),
                                 default=0)
            expected = call_count >= 10
            out.append({
                "type": "repeated_co_location",
                "severity": "medium" if expected else "high",
                "title": (f"{self.cg.G.nodes[a].get('label')} and "
                          f"{self.cg.G.nodes[b].get('label')} repeatedly present together"),
                "description": (
                    f"Handsets active on the same cell site within "
                    f"{window_minutes} minutes on {p['n']} occasions across "
                    f"{len(p['cells'])} different locations "
                    f"({', '.join(sorted(p['cells']))})."
                    + (" The two are also in frequent phone contact, so joint "
                       "presence is expected rather than notable."
                       if expected else
                       " They are not in frequent phone contact, which makes "
                       "repeated joint presence worth verifying.")),
                "entities": [a, b],
                "entity_labels": [self.cg.G.nodes[a].get("label"),
                                  self.cg.G.nodes[b].get("label")],
                "basis": {"rule": f">= {min_events} same-cell events within "
                                  f"{window_minutes} minutes, across >= 2 cell sites",
                          "events": p["n"], "cell_sites": sorted(p["cells"]),
                          "sample_times": p["times"],
                          "calls_between_them": call_count},
                "sources": ["CDR"],
            })
        # Rank by how INFORMATIVE the finding is, not by raw event count.
        # Two lieutenants who speak daily also travelling together tells an
        # investigator nothing; two subjects who never call each other and keep
        # turning up in the same place is the lead worth working.
        out.sort(key=lambda f: (f["severity"] != "high", -f["basis"]["events"],
                                f["title"]))
        return out[:6]

    # ------------------------------------------------------------------ 8
    def custody_conflicts(self):
        """
        A handset active while its registered user is in judicial custody.

        This is the concrete payoff of the ICJS integration. Custody dates live
        in the criminal-justice system, call records live with the operator, and
        neither system can see the contradiction on its own. When they are put
        side by side, continued activity on a number whose user is inside means
        the handset is being operated by somebody else - which changes who every
        call on that number should be attributed to.
        """
        from datetime import datetime as _dt
        from ..integrations import fetch as fetch_source

        res = fetch_source("icjs", authorisation="demo: internal consistency check")
        if not res.get("ok"):
            return []
        out = []
        for rec in res["records"]:
            if rec.get("custody_status") != "Judicial custody" or not rec.get("custody_from"):
                continue
            node = self.cg.resolve_name(rec.get("name", ""))
            if not node:
                continue
            phones = self.cg.G.nodes[node].get("phones", []) or []
            start = rec["custody_from"]
            end = rec.get("custody_to")
            active = []
            for r in self.raw["cdr_rows"]:
                if r["caller"] not in phones and r["callee"] not in phones:
                    continue
                d = r["start_time"][:10]
                if d >= start and (not end or d <= end):
                    active.append(r)
            if len(active) < 3:
                continue
            last = max(r["start_time"] for r in active)[:10]
            cells = sorted({r["cell_tower"] for r in active})
            out.append({
                "type": "custody_conflict",
                "severity": "high",
                "title": (f"{self.cg.G.nodes[node].get('label')}'s handset was in use "
                          f"during judicial custody"),
                "description": (
                    f"ICJS records {rec['name']} in judicial custody from {start} "
                    f"in case {rec.get('case_id')} ({rec.get('court')}). The number "
                    f"{', '.join(phones)} attributed to this subject nevertheless "
                    f"shows {len(active)} call events up to {last}, on cell sites "
                    f"{', '.join(cells)}. Either the attribution is wrong or the "
                    f"handset is being operated by another person - and every call "
                    f"on it during this period should be re-attributed before it is "
                    f"relied on."),
                "entities": [node],
                "entity_labels": [self.cg.G.nodes[node].get("label")],
                "basis": {"rule": "CDR activity on an attributed handset inside a "
                                  "period of judicial custody recorded in ICJS",
                          "custody_from": start, "custody_to": end,
                          "case": rec.get("case_id"), "court": rec.get("court"),
                          "events_during_custody": len(active),
                          "last_event": last, "cell_sites": cells,
                          "phones": phones},
                "sources": ["ICJS", "CDR"],
            })
        return out

    # ------------------------------------------------------------------ 9
    def cross_community_brokers(self):
        P = self.an.P
        comm = self.an.communities()["assignment"]
        out = []
        for n in P:
            d = self.cg.G.nodes[n]
            if d.get("type") != "PERSON":
                continue
            groups = {comm.get(m) for m in P.neighbors(n)}
            groups.discard(comm.get(n))
            groups.discard(None)
            if len(groups) >= 2:
                out.append({
                    "type": "cross_community_broker",
                    "severity": "medium",
                    "title": f"{d.get('label')} bridges {len(groups) + 1} separate groups",
                    "description": (f"{d.get('label')} is the point of contact between "
                                    f"{len(groups) + 1} otherwise weakly connected clusters. "
                                    f"Removing this link would fragment the network."),
                    "entities": [n], "entity_labels": [d.get("label")],
                    "basis": {"rule": "neighbours span >= 2 foreign communities",
                              "own_community": comm.get(n),
                              "bridged_communities": sorted(groups)},
                    "sources": ["CDR", "TXN", "FIR"],
                })
        return out

    # ------------------------------------------------------------------
    def run_all(self):
        findings = (self.communication_bursts() + self.burner_handsets()
                    + self.circular_funds() + self.structuring()
                    + self.insulated_actors() + self.clean_skin_bridges()
                    + self.co_location() + self.custody_conflicts()
                    + self.cross_community_brokers())
        order = {"high": 0, "medium": 1, "low": 2}
        findings.sort(key=lambda f: (order.get(f["severity"], 3), f["type"],
                                     f["title"]))
        for i, f in enumerate(findings, 1):
            f["id"] = f"A{i:03d}"
        return findings
