"""
Graph construction.

Turns resolved entities + provenance-carrying relations into a NetworkX
MultiGraph-flavoured property graph. Every edge stores:

    type, weight, confidence, first_seen, last_seen, sources[], evidence[]

`sources` is the non-negotiable part: an investigator must always be able to
ask "why do you say these two people are connected?" and get back the exact
records that produced the edge.

NetworkX is used so the prototype runs with no database to install. The
`GraphStore` API is intentionally narrow (add_node / add_edge / query) so the
same code can be pointed at Neo4j for a production deployment.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

import networkx as nx

from ..pipeline.ingest import ingest_all
from ..pipeline.translit import name_key

LOCATION_COORDS = {
    "Ghatkopar": (19.0860, 72.9080), "Kurla": (19.0726, 72.8790),
    "Dongri": (18.9600, 72.8370), "Chembur": (19.0620, 72.8990),
    "Fort": (18.9350, 72.8350), "Zaveri Bazaar": (18.9520, 72.8320),
    "Byculla": (18.9760, 72.8330), "Worli": (19.0170, 72.8170),
    "Nagpada": (18.9660, 72.8290), "JNPT Port": (18.9490, 72.9510),
    "Bhiwandi Godown": (19.2960, 73.0630), "Bhiwandi": (19.2960, 73.0630),
}

EDGE_BASE_WEIGHT = {
    "CALLED": 1.0, "TRANSACTED_WITH": 1.0, "SHARES_HANDSET": 1.4,
    "INSTRUCTED_BY": 1.3, "REPORTS_TO": 1.3, "SUPPLIED_BY": 1.2,
    "DIRECTOR_OF": 1.2, "CO_NAMED_IN": 0.6, "ASSOCIATE_OF": 0.7,
    "SEEN_AT": 0.4, "LINKED_TO_ORG": 0.8, "USED_VEHICLE": 0.4,
    "CONTROLS_AREA": 0.5,
}

ORG_NAMES = {"Meridian Exim Pvt Ltd", "Rathi Infrastructure Ltd", "Sunrise Logistics",
             "Al-Noor Trading Co", "Konkan Marine Services",
             "Sai Traders", "Nova Stationers", "Gokhale Contractors",
             "Prime Facility Services"}


def _org_id(name: str) -> str:
    """Stable, collision-resistant id for an organisation node."""
    return "O-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]


class CaseGraph:
    def __init__(self):
        self.G = nx.Graph()
        self.raw = None
        self.alias_index: dict[str, str] = {}   # lowercase name form -> node id
        self.phone_index: dict[str, str] = {}   # phone -> node id
        self.account_index: dict[str, str] = {}
        self.entities: dict[str, object] = {}
        self._phonetic_index: dict | None = None

    # ------------------------------------------------------------- helpers
    def _add_node(self, nid, **attrs):
        if self.G.has_node(nid):
            self.G.nodes[nid].update({k: v for k, v in attrs.items() if v})
        else:
            self.G.add_node(nid, **attrs)
        return nid

    def _add_edge(self, a, b, etype, source_id, source_type, ts=None,
                  confidence=0.6, evidence="", weight=None, **extra):
        if a is None or b is None or a == b:
            return
        w = weight if weight is not None else EDGE_BASE_WEIGHT.get(etype, 0.5)
        if self.G.has_edge(a, b):
            e = self.G[a][b]
            if etype not in e["types"]:
                e["types"].append(etype)
            e["weight"] += w
            e["confidence"] = max(e["confidence"], confidence)
            e["sources"].append({"source_id": source_id, "source_type": source_type,
                                 "type": etype, "timestamp": ts, "evidence": evidence,
                                 "confidence": confidence, **extra})
            if ts:
                e["first_seen"] = min(e["first_seen"] or ts, ts)
                e["last_seen"] = max(e["last_seen"] or ts, ts)
            e["observations"] += 1
        else:
            self.G.add_edge(a, b, types=[etype], weight=w, confidence=confidence,
                            first_seen=ts, last_seen=ts, observations=1,
                            sources=[{"source_id": source_id, "source_type": source_type,
                                      "type": etype, "timestamp": ts,
                                      "evidence": evidence, "confidence": confidence,
                                      **extra}])

    def resolve_name(self, name: str) -> str | None:
        """
        Exact spelling first, then a phonetic fallback. External systems spell
        the same person differently from our canonical label ("Sanjay Bhosale"
        in ICJS, "Sanjay Bhosle" here), and an integration that silently returns
        nothing is worse than no integration at all.
        """
        if not name:
            return None
        hit = self.alias_index.get(name.strip().lower())
        if hit:
            return hit
        if self._phonetic_index is None:
            self._phonetic_index = {}
            for form, nid in self.alias_index.items():
                self._phonetic_index.setdefault(name_key(form), set()).add(nid)
        owners = self._phonetic_index.get(name_key(name), set())
        return next(iter(owners)) if len(owners) == 1 else None

    # --------------------------------------------------------------- build
    def build(self):
        self.raw = ingest_all()

        # 1. people ------------------------------------------------------
        for e in self.raw["entities"]:
            self._add_node(e.id, label=e.canonical_name, type="PERSON",
                           aliases=e.aliases, phones=e.phones, accounts=e.accounts,
                           sources=e.sources, merge_evidence=e.merge_evidence,
                           **{k: v for k, v in e.attrs.items()})
            self.entities[e.id] = e
            for form in [e.canonical_name] + list(e.aliases):
                self.alias_index[form.strip().lower()] = e.id
            self._phonetic_index = None
            for ph in e.phones:
                self.phone_index[ph] = e.id
            for ac in e.accounts:
                self.account_index[ac] = e.id

        # 2. organisations ----------------------------------------------
        # Node ids are derived deterministically. Python randomises str hashing
        # per process, so an id built from hash() changes between runs and can
        # collide - which silently merged two companies into one node on some
        # runs and made the whole analysis non-reproducible. An investigation
        # tool that returns a different answer on a second run is not usable,
        # and neither is one whose exported ids move.
        #
        # ORG_NAMES is a name directory for resolving mentions ("Meridian Exim
        # Pvt Ltd" in an FIR) to a stable id - it is not itself evidence, so a
        # company only becomes a node once something in the case actually
        # references it (a transaction account or a text mention), same as
        # locations and vehicles below. A blank case must show zero orgs.
        self._org_meta = {}
        for name in sorted(set(ORG_NAMES) | set(self.raw["org_records"])):
            nid = _org_id(name)
            rec = self.raw["org_records"].get(name, {})
            self._org_meta[nid] = {"label": name, "account": rec.get("account")}
            self.alias_index[name.lower()] = nid
            if rec.get("account"):
                self.account_index[rec["account"]] = nid

        # 3. call detail records ----------------------------------------
        for (a_ph, b_ph), agg in self.raw["cdr_agg"].items():
            a, b = self.phone_index.get(a_ph), self.phone_index.get(b_ph)
            if not a or not b:
                continue     # unattributed subscriber pairs stay out of the person graph
            self._add_edge(a, b, "CALLED", "CDR", "cdr", agg["last"],
                           confidence=0.92,
                           evidence=(f"{agg['n']} contacts between {a_ph} and {b_ph} "
                                     f"between {agg['first'][:10]} and {agg['last'][:10]}, "
                                     f"cell sites: {', '.join(sorted(agg['cells']))}"),
                           weight=1.0 + agg["n"] / 25.0,
                           call_count=agg["n"], total_seconds=agg["secs"],
                           cells=sorted(agg["cells"]))
            for cell in agg["cells"]:
                for p in (a, b):
                    self._link_location(p, cell, "CDR", "cdr", agg["last"], 0.7,
                                        f"Handset active on {cell} cell site")

        # 4. shared handsets (IMEI) --------------------------------------
        for imei, phones in self.raw["imei_map"].items():
            ids = {self.phone_index.get(p) for p in phones} - {None}
            ids = sorted(ids)
            if len(ids) > 1:
                for i in range(len(ids)):
                    for j in range(i + 1, len(ids)):
                        self._add_edge(ids[i], ids[j], "SHARES_HANDSET", "CDR", "cdr",
                                       None, 0.95,
                                       f"Numbers {', '.join(phones)} seen on one handset "
                                       f"(IMEI {imei})")

        # 5. financial transactions --------------------------------------
        pair = defaultdict(lambda: {"amt": 0, "n": 0, "first": None, "last": None,
                                    "modes": set(), "ids": []})
        for r in self.raw["txn_rows"]:
            a = self.account_index.get(r["from_account"]) or self.resolve_name(r["from_name"])
            b = self.account_index.get(r["to_account"]) or self.resolve_name(r["to_name"])
            if not a or not b or a == b:
                continue
            self._use_org(a)
            self._use_org(b)
            k = tuple(sorted([a, b]))
            p = pair[k]
            p["amt"] += int(r["amount_inr"])
            p["n"] += 1
            p["modes"].add(r["mode"])
            p["ids"].append(r["txn_id"])
            t = r["timestamp"]
            p["first"] = min(p["first"], t) if p["first"] else t
            p["last"] = max(p["last"], t) if p["last"] else t
        for (a, b), p in pair.items():
            self._add_edge(a, b, "TRANSACTED_WITH", "TXN", "transaction", p["last"],
                           confidence=0.95,
                           evidence=(f"{p['n']} transactions totalling "
                                     f"Rs {p['amt']:,} ({', '.join(sorted(p['modes']))}) "
                                     f"between {p['first'][:10]} and {p['last'][:10]}"),
                           weight=1.0 + min(p["amt"] / 5_000_000, 3.0),
                           total_amount=p["amt"], txn_count=p["n"],
                           txn_ids=p["ids"][:20])

        # 6. text-derived relations --------------------------------------
        for rel in self.raw["raw_relations"]:
            a = self.resolve_name(rel["a"])
            if rel["b_kind"] == "LOCATION":
                if a:
                    self._link_location(a, rel["b"], rel["source_id"], rel["source_type"],
                                        rel["timestamp"], rel["confidence"], rel["evidence"])
                continue
            if rel["b_kind"] == "VEHICLE":
                if a:
                    v = self._add_node(f"V-{rel['b'].replace(' ', '')}", label=rel["b"],
                                       type="VEHICLE")
                    self._add_edge(a, v, "USED_VEHICLE", rel["source_id"],
                                   rel["source_type"], rel["timestamp"],
                                   rel["confidence"], rel["evidence"])
                continue
            b = self.resolve_name(rel["b"])
            if a and b:
                self._use_org(a)
                self._use_org(b)
                self._add_edge(a, b, rel["type"], rel["source_id"], rel["source_type"],
                               rel["timestamp"], rel["confidence"], rel["evidence"])

        # 7. case / FIR nodes --------------------------------------------
        for sid, doc in self.raw["documents"].items():
            if doc["source_type"] != "fir":
                continue
            rec = doc["record"]
            self._add_node(f"F-{sid}", label=sid, type="CASE", station=rec["station"],
                           sections=rec["sections"], registered_on=rec["registered_on"])
        for rel in self.raw["raw_relations"]:
            if rel["source_type"] != "fir":
                continue
            for nm in (rel["a"], rel["b"]):
                nid = self.resolve_name(nm)
                if nid:
                    self._add_edge(nid, f"F-{rel['source_id']}", "NAMED_IN",
                                   rel["source_id"], "fir", rel["timestamp"], 0.85,
                                   f"Named in {rel['source_id']}")
        return self

    def _use_org(self, nid):
        meta = self._org_meta.get(nid)
        if meta:
            self._add_node(nid, label=meta["label"], type="ORG", account=meta["account"])

    def _link_location(self, node, loc_name, source_id, source_type, ts, conf, ev):
        lid = f"L-{loc_name.replace(' ', '_')}"
        lat, lon = LOCATION_COORDS.get(loc_name, (None, None))
        self._add_node(lid, label=loc_name, type="LOCATION", lat=lat, lon=lon)
        self._add_edge(node, lid, "SEEN_AT", source_id, source_type, ts, conf, ev)

    # ---------------------------------------------------------- accessors
    def person_subgraph(self) -> nx.Graph:
        keep = [n for n, d in self.G.nodes(data=True) if d["type"] in ("PERSON", "ORG")]
        return self.G.subgraph(keep).copy()

    def node(self, nid):
        d = dict(self.G.nodes[nid])
        d["id"] = nid
        return d

    def stats(self):
        by_type = defaultdict(int)
        for _, d in self.G.nodes(data=True):
            by_type[d["type"]] += 1
        edge_types = defaultdict(int)
        for _, _, d in self.G.edges(data=True):
            for t in d["types"]:
                edge_types[t] += 1
        return {"nodes": self.G.number_of_nodes(), "edges": self.G.number_of_edges(),
                "nodes_by_type": dict(by_type), "edges_by_type": dict(edge_types),
                "extractor": self.raw["extractor"],
                "source_documents": len(self.raw["documents"]),
                "mentions_extracted": len(self.raw["mentions"]),
                "entities_resolved": len(self.raw["entities"]),
                "merge_decisions": len(self.raw["resolution_decisions"])}


def build_graph() -> CaseGraph:
    return CaseGraph().build()
