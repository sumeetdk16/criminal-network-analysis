"""
Network analytics: who matters, who clusters with whom, and how two people
are connected.

Every score returned here carries a `why` breakdown. An unexplained ranking is
useless to an investigator and inadmissible as a basis for action, so the
influence score is a transparent weighted sum of named components rather than
an opaque model output.
"""

from __future__ import annotations

import math
from collections import defaultdict

import networkx as nx


# Population-average baseline plus per-feature Shapley attributions are computed
# alongside the score, so "why is this person ranked third" has a numeric answer
# and not just a sentence.
INFLUENCE_WEIGHTS = {
    "betweenness": 0.30,   # controls the flow of information/goods between groups
    "pagerank": 0.25,      # is pointed at by other important people
    "degree": 0.15,        # breadth of direct contacts
    "eigenvector": 0.15,   # connected to other well-connected people
    "prior_record": 0.10,  # documented criminal history
    "bridging": 0.05,      # sits between otherwise separate communities
}


def _pagerank_pure(G, alpha=0.85, iters=100, tol=1.0e-8, weight="weight"):
    """
    Power-iteration PageRank with no SciPy/NumPy dependency.

    NetworkX delegates pagerank() to SciPy, which is a heavy install. The demo
    must run on a bare Python, so this is used whenever SciPy is unavailable.
    """
    nodes = list(G)
    n = len(nodes)
    if n == 0:
        return {}
    x = {v: 1.0 / n for v in nodes}
    out_w = {v: sum(G[v][u].get(weight, 1.0) for u in G[v]) for v in nodes}
    dangling = [v for v in nodes if out_w[v] == 0]
    for _ in range(iters):
        xlast, x = x, dict.fromkeys(nodes, 0.0)
        danglesum = alpha * sum(xlast[v] for v in dangling)
        for v in nodes:
            if out_w[v] == 0:
                continue
            share = alpha * xlast[v] / out_w[v]
            for u in G[v]:
                x[u] += share * G[v][u].get(weight, 1.0)
        for v in nodes:
            x[v] += danglesum / n + (1.0 - alpha) / n
        if sum(abs(x[v] - xlast[v]) for v in nodes) < n * tol:
            break
    return x


def _eigenvector_pure(G, iters=200, tol=1.0e-8, weight="weight"):
    nodes = list(G)
    if not nodes:
        return {}
    x = {v: 1.0 for v in nodes}
    for _ in range(iters):
        xlast, x = x, dict.fromkeys(nodes, 0.0)
        for v in nodes:
            for u in G[v]:
                x[u] += xlast[v] * G[v][u].get(weight, 1.0)
        norm = math.sqrt(sum(val * val for val in x.values())) or 1.0
        x = {k: v / norm for k, v in x.items()}
        if sum(abs(x[v] - xlast[v]) for v in nodes) < tol:
            break
    return x


def _normalise(d: dict) -> dict:
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    if math.isclose(hi, lo):
        return {k: 0.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


class NetworkAnalytics:
    def __init__(self, case_graph):
        self.cg = case_graph
        self.P = case_graph.person_subgraph()
        self._communities = None
        self._scores = None

    # ------------------------------------------------------------ centrality
    def centralities(self) -> dict:
        P = self.P
        if P.number_of_nodes() == 0:
            return {}
        deg = dict(P.degree(weight="weight"))
        btw = nx.betweenness_centrality(P, weight=None, normalized=True)
        try:
            pr = nx.pagerank(P, weight="weight")
        except (ImportError, ModuleNotFoundError):
            pr = _pagerank_pure(P)
        try:
            eig = nx.eigenvector_centrality(P, max_iter=1000, weight="weight")
        except Exception:
            eig = _eigenvector_pure(P)
        return {"degree": deg, "betweenness": btw, "pagerank": pr, "eigenvector": eig}

    # ----------------------------------------------------------- communities
    def communities(self) -> dict:
        if self._communities is not None:
            return self._communities
        P = self.P

        # Community algorithms break ties by iterating over sets of node ids,
        # and Python randomises string hashing per process. Relabelling to
        # integers - whose hash is not randomised - in a fixed order removes
        # that source of run-to-run variation before the algorithm even starts.
        order = sorted(P.nodes())
        to_int = {n: i for i, n in enumerate(order)}
        to_id = {i: n for n, i in to_int.items()}
        Q = nx.relabel_nodes(P, to_int, copy=True)

        # Community detection must give the same answer twice.
        #
        # Louvain scores marginally higher here (0.504 vs 0.5037) but is
        # stochastic, and NetworkX's implementation keeps some dependence on
        # hash ordering even with a fixed seed and integer node ids - across
        # processes it returned partitions that scored identically yet grouped
        # people differently, which changed how many subjects were reported as
        # cross-group brokers. Greedy modularity (Clauset-Newman-Moore) is
        # agglomerative and deterministic, and lands within 0.0003 modularity of
        # Louvain on this network. An investigator who reruns an analysis and
        # gets different groups will, correctly, stop trusting the tool, so the
        # reproducible algorithm wins the trade.
        parts_raw = nx.community.greedy_modularity_communities(Q, weight="weight")
        parts_int = sorted((sorted(c) for c in parts_raw), key=lambda c: c[0])
        parts = [{to_id[i] for i in c} for c in parts_int]

        mapping = {}
        for i, comm in enumerate(parts):
            for n in comm:
                mapping[n] = i
        self._communities = {
            "assignment": mapping,
            "groups": [sorted(c) for c in parts],
            "count": len(parts),
            "algorithm": "greedy modularity (Clauset-Newman-Moore), deterministic",
            "modularity": round(nx.community.modularity(P, parts, weight="weight"), 3)
            if len(parts) > 1 else 0.0,
        }
        return self._communities

    # ------------------------------------------------------------- influence
    @staticmethod
    def shapley(parts: dict, means: dict) -> dict:
        """
        Exact Shapley attribution for the influence score.

        The score is additive: f(x) = sum_k w_k * x_k. For an additive model the
        Shapley value has a closed form - phi_k = w_k * (x_k - E[x_k]) - so this
        is computed exactly rather than sampled the way KernelSHAP would have to
        for an opaque model. The guarantee that matters to an investigator holds
        either way: the attributions sum exactly to the gap between this
        subject's score and the population average.

        A positive value means the component pushed this person UP the ranking
        relative to everyone else; negative means it held them down.
        """
        return {k: round(INFLUENCE_WEIGHTS[k] * (v - means.get(k, 0.0)), 4)
                for k, v in parts.items()}

    def influence(self) -> list[dict]:
        if self._scores is not None:
            return self._scores
        c = self.centralities()
        if not c:
            return []
        comm = self.communities()["assignment"]
        norm = {k: _normalise(v) for k, v in c.items()}

        bridging = {}
        for n in self.P:
            neigh_comms = {comm.get(m) for m in self.P.neighbors(n)}
            neigh_comms.discard(comm.get(n))
            bridging[n] = len(neigh_comms)
        bridging = _normalise(bridging)

        prior = {}
        for n in self.P:
            d = self.cg.G.nodes[n]
            prior[n] = float(d.get("prior_cases", 0) or 0)
        prior = _normalise(prior)

        all_parts = {}
        for n in self.P:
            all_parts[n] = {
                "betweenness": norm["betweenness"].get(n, 0),
                "pagerank": norm["pagerank"].get(n, 0),
                "degree": norm["degree"].get(n, 0),
                "eigenvector": norm["eigenvector"].get(n, 0),
                "prior_record": prior.get(n, 0),
                "bridging": bridging.get(n, 0),
            }
        m = max(len(all_parts), 1)
        means = {k: sum(p[k] for p in all_parts.values()) / m
                 for k in INFLUENCE_WEIGHTS}
        baseline = sum(INFLUENCE_WEIGHTS[k] * v for k, v in means.items())

        out = []
        for n in self.P:
            d = self.cg.G.nodes[n]
            parts = all_parts[n]
            score = sum(INFLUENCE_WEIGHTS[k] * v for k, v in parts.items())
            top = sorted(parts.items(), key=lambda kv: -INFLUENCE_WEIGHTS[kv[0]] * kv[1])[:2]
            attribution = self.shapley(parts, means)
            drivers = sorted(attribution.items(), key=lambda kv: -kv[1])
            out.append({
                "id": n, "label": d.get("label"), "type": d.get("type"),
                "score": round(score, 4),
                "community": comm.get(n),
                "components": {k: round(v, 3) for k, v in parts.items()},
                "raw": {k: round(c[k].get(n, 0), 4) for k in c},
                "attribution": attribution,
                "baseline": round(baseline, 4),
                "top_driver": drivers[0][0] if drivers else None,
                "top_detractor": drivers[-1][0] if drivers else None,
                "why": self._explain(d, parts, top),
            })
        out.sort(key=lambda r: (-r["score"], r["label"] or r["id"]))
        for i, r in enumerate(out, 1):
            r["rank"] = i
        self._scores = out
        return out

    @staticmethod
    def _explain(node, parts, top) -> str:
        names = {
            "betweenness": "sits on the shortest routes between other members",
            "pagerank": "is repeatedly referenced by other significant members",
            "degree": "has an unusually broad set of direct contacts",
            "eigenvector": "is closely tied to other well-connected members",
            "prior_record": "has a documented prior criminal record",
            "bridging": "connects otherwise separate groups",
        }
        bits = [names[k] for k, v in top if v > 0.05]
        if not bits:
            bits = ["has limited connectivity in the current data"]
        extra = ""
        if node.get("prior_cases"):
            extra = f" Prior cases on record: {node['prior_cases']} ({node.get('offence_categories','-')})."
        return f"{node.get('label')} " + " and ".join(bits) + "." + extra

    # ------------------------------------------------------------- linkage
    def paths(self, a: str, b: str, cutoff: int = 5, max_paths: int = 6) -> list[dict]:
        if a not in self.P or b not in self.P:
            return []
        results = []
        try:
            gen = nx.all_simple_paths(self.P, a, b, cutoff=cutoff)
            for path in gen:
                legs = []
                strength = 1.0
                for x, y in zip(path, path[1:]):
                    e = self.cg.G[x][y]
                    legs.append({
                        "from": x, "from_label": self.cg.G.nodes[x].get("label"),
                        "to": y, "to_label": self.cg.G.nodes[y].get("label"),
                        "types": e["types"], "confidence": e["confidence"],
                        "observations": e["observations"],
                        "sources": e["sources"][:6],
                    })
                    strength *= e["confidence"]
                results.append({
                    "path": path,
                    "labels": [self.cg.G.nodes[n].get("label") for n in path],
                    "hops": len(path) - 1,
                    "path_confidence": round(strength, 3),
                    "source_types": sorted({s["source_type"] for l in legs for s in l["sources"]}),
                    "legs": legs,
                })
                if len(results) >= 60:
                    break
        except nx.NetworkXNoPath:
            return []
        results.sort(key=lambda r: (r["hops"], -r["path_confidence"]))
        return results[:max_paths]

    def neighbourhood(self, node: str, hops: int = 2) -> dict:
        if node not in self.cg.G:
            return {"nodes": [], "edges": []}
        nodes = nx.single_source_shortest_path_length(self.cg.G, node, cutoff=hops)
        sub = self.cg.G.subgraph(nodes).copy()
        return {"center": node, "hops": hops,
                "nodes": sorted(sub.nodes()), "distance": nodes}

    # --------------------------------------------------------- corroboration
    def corroboration(self) -> list[dict]:
        """
        How many INDEPENDENT source systems support each link. A connection
        attested by CDR + financial + surveillance is far stronger evidence than
        one attested by a single social-media post, and the UI should say so.
        """
        out = []
        for a, b, d in self.cg.G.edges(data=True):
            srcs = {s["source_type"] for s in d["sources"]}
            out.append({
                "a": a, "b": b,
                "a_label": self.cg.G.nodes[a].get("label"),
                "b_label": self.cg.G.nodes[b].get("label"),
                "types": d["types"],
                "independent_sources": sorted(srcs),
                "corroboration_level": len(srcs),
                "confidence": d["confidence"],
                "observations": d["observations"],
            })
        out.sort(key=lambda r: (-r["corroboration_level"], -r["confidence"]))
        return out
