"""
Entity resolution / record linkage.

The single biggest reason criminal-network graphs are useless in practice is
that the same human being appears as "Rajesh Kumar", "R. Kumar" and
"Rajesh Kr." across three systems, producing three disconnected nodes. This
module collapses mentions into canonical identities using:

  1. deterministic identifier matching  - a shared phone / account / IMEI is
     near-proof of identity and is scored highest;
  2. name normalisation + a phonetic variant map for common Indian
     transliteration splits (Sheikh/Shaikh, Bhosale/Bhosle, Qureshi/Quraishi);
  3. initial-compatible matching        - "V. Sethi" unifies with
     "Vikram Sethi" but not with "Vijay Sethi" unless an identifier agrees.

Every merge decision records *why* it happened, so an investigator can audit
the identity itself, not just the link.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .translit import skeleton, transliterate_text, has_devanagari


VARIANTS = {
    "shaikh": "sheikh", "fareed": "farid", "bhosle": "bhosale",
    "quraishi": "qureshi", "seth": "sethi", "kr": "kumar",
    "chouhan": "chauhan", "jhadav": "jadhav", "yadhav": "yadav",
}

NOISE_TOKENS = {"mr", "mrs", "shri", "smt", "alias", "urf", "one", "accused", "subject"}


def norm_token(t: str) -> str:
    t = re.sub(r"[^a-z]", "", t.lower())
    return VARIANTS.get(t, t)


def name_tokens(name: str) -> list[str]:
    # Devanagari names are transliterated first, so a Hindi FIR and an English
    # CDR export are compared in one space rather than never meeting.
    name = transliterate_text(name or "")
    raw = [p for p in re.split(r"[\s.]+", name.strip()) if p]
    out = []
    for p in raw:
        n = norm_token(p)
        if not n or n in NOISE_TOKENS:
            continue
        # keep single letters as initials
        out.append(n if len(p.rstrip(".")) > 1 else n[:1])
    return out


def _is_initial(tok: str) -> bool:
    return len(tok) == 1


def _full_tokens(name: str) -> list[str]:
    return [t for t in name_tokens(name) if len(t) > 1]


def _same_sound(a: str, b: str) -> bool:
    """
    Token equality that survives a script change. Exact match first; otherwise
    compare consonant skeletons, which is what lets "Gayakavada" (transliterated
    from गायकवाड) meet "Gaikwad" without also letting unrelated names collide.
    """
    if a == b:
        return True
    if _is_initial(a) or _is_initial(b):
        return False
    sa, sb = skeleton(a), skeleton(b)
    return bool(sa) and sa == sb


def name_similarity(a: str, b: str) -> float:
    """
    0..1 similarity that is tolerant of initials ("V. Sethi"), token order
    ("Sethi Vikram") and added middle names ("Devendra Kumar Rathi"), while
    still refusing to merge two different people who share one given name.
    """
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0

    full_a = {skeleton(t) for t in ta if len(t) >= 4}
    full_b = {skeleton(t) for t in tb if len(t) >= 4}
    # require at least one substantive token in common - a shared initial alone
    # is never enough to claim two records describe the same human being
    if not (full_a & full_b):
        return 0.0

    matched, used = 0, set()
    for x in ta:
        for j, y in enumerate(tb):
            if j in used:
                continue
            if _same_sound(x, y):
                matched += 1
                used.add(j)
                break
            if (_is_initial(x) and y.startswith(x)) or (_is_initial(y) and x.startswith(y)):
                matched += 1
                used.add(j)
                break

    shorter, longer = min(len(ta), len(tb)), max(len(ta), len(tb))
    shorter_full = full_a if len(ta) <= len(tb) else full_b

    # Subset rule ("Devendra Rathi" inside "Devendra Kumar Rathi") is only safe
    # when the shorter name carries at least two spelled-out tokens. Otherwise
    # an initial can wander onto the wrong surname: "R. Kumar" would otherwise
    # match "Devendra Kumar Rathi" through R~Rathi, fusing two different people.
    if matched == shorter and shorter >= 2 and len(shorter_full) >= 2:
        return 1.0 if longer - shorter <= 1 else 0.85
    return matched / longer


# --------------------------------------------------------------------------

class UnionFind:
    def __init__(self):
        self.p: dict[str, str] = {}

    def add(self, x):
        self.p.setdefault(x, x)

    def find(self, x):
        self.add(x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra
        return ra


@dataclass
class ResolvedEntity:
    id: str
    type: str
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    vehicles: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    merge_evidence: list[str] = field(default_factory=list)
    attrs: dict = field(default_factory=dict)


class EntityResolver:
    """
    Feed observations in with `observe(...)`, then call `resolve()`.

    An observation is a name (possibly an alias form) plus whatever
    identifiers appeared alongside it in the same record, plus the source
    reference the pair came from.
    """

    NAME_MATCH_THRESHOLD = 0.75

    def __init__(self):
        self.obs: list[dict] = []

    def observe(self, name: str, source_id: str, source_type: str,
                phones=(), accounts=(), vehicles=(), attrs=None):
        if not name or not name_tokens(name):
            return
        self.obs.append({
            "name": name.strip(), "source_id": source_id, "source_type": source_type,
            "phones": [p for p in phones if p], "accounts": [a for a in accounts if a],
            "vehicles": [v for v in vehicles if v], "attrs": attrs or {},
        })

    # ------------------------------------------------------------------
    def resolve(self) -> tuple[list[ResolvedEntity], list[dict]]:
        uf = UnionFind()
        keys = [f"obs{i}" for i in range(len(self.obs))]
        for k in keys:
            uf.add(k)

        decisions: list[dict] = []

        seen_decisions: set = set()

        def record(a, b, why, score):
            uf.union(a, b)
            na, nb = self.obs[int(a[3:])]["name"], self.obs[int(b[3:])]["name"]
            # The same pair of spellings can be joined by many observations of
            # the same identifier. That is one decision, not twenty - repeating
            # it buries the merges an investigator actually needs to audit.
            key = (na, nb, why)
            if key in seen_decisions:
                return
            seen_decisions.add(key)
            decisions.append({"key_a": a, "key_b": b, "a": na, "b": nb,
                              "reason": why, "score": round(score, 2)})

        # 1) deterministic: shared identifier
        by_ident: dict[str, list[str]] = {}
        for k, o in zip(keys, self.obs):
            for ident in o["phones"] + o["accounts"]:
                by_ident.setdefault(ident, []).append(k)
        for ident, group in by_ident.items():
            for other in group[1:]:
                record(group[0], other, f"shared identifier {ident}", 1.0)

        # 2) probabilistic: name similarity, blocked on every substantive token
        #    (order-insensitive, so "Sethi Vikram" still meets "Vikram Sethi")
        blocks: dict[str, list[str]] = {}
        for k, o in zip(keys, self.obs):
            # block on phonetic skeletons, not spellings, so cross-script pairs
            # actually land in the same candidate bucket
            for t in {skeleton(t) for t in name_tokens(o["name"]) if len(t) >= 4}:
                blocks.setdefault(t, []).append(k)

        for _, group in sorted(blocks.items()):
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    if uf.find(a) == uf.find(b):
                        continue
                    s = name_similarity(self.obs[int(a[3:])]["name"],
                                        self.obs[int(b[3:])]["name"])
                    if s >= self.NAME_MATCH_THRESHOLD:
                        record(a, b, f"name match {self.obs[int(a[3:])]['name']} ~ "
                                     f"{self.obs[int(b[3:])]['name']} (score {s:.2f})", s)

        # 3) build canonical entities
        clusters: dict[str, list[int]] = {}
        for idx, k in enumerate(keys):
            clusters.setdefault(uf.find(k), []).append(idx)

        entities: list[ResolvedEntity] = []
        for n, (root, idxs) in enumerate(sorted(clusters.items()), start=1):
            names, phones, accounts, vehicles, sources, attrs = [], [], [], [], [], {}
            for i in idxs:
                o = self.obs[i]
                names.append(o["name"])
                phones += o["phones"]
                accounts += o["accounts"]
                vehicles += o["vehicles"]
                sources.append(f"{o['source_type']}:{o['source_id']}")
                attrs.update({k2: v for k2, v in o["attrs"].items() if v not in (None, "", "-")})
            # canonical form = the longest, most complete spelling seen
            # Canonical label: the most complete name seen, preferring a Latin
            # spelling on ties. A Devanagari-only subject is transliterated for
            # display, and every original spelling survives in `aliases`.
            best = max(len(_full_tokens(x)) for x in names)
            candidates = [x for x in names if len(_full_tokens(x)) == best]
            latin = [x for x in candidates if not has_devanagari(x)]
            if latin:
                canonical = max(latin, key=len)
            else:
                canonical = transliterate_text(max(candidates, key=len)).title()
            entities.append(ResolvedEntity(
                id=f"P{n:04d}", type="PERSON", canonical_name=canonical,
                aliases=sorted(set(names) - {canonical}),
                phones=sorted(set(phones)), accounts=sorted(set(accounts)),
                vehicles=sorted(set(vehicles)), sources=sorted(set(sources)),
                merge_evidence=sorted({d["reason"] for d in decisions
                                       if uf.find(d["key_a"]) == root}),
                attrs=attrs,
            ))
        # Cross-spelling merges first: "Farid Sheikh ~ Fareed Shaikh" is the
        # decision worth auditing; "Vikram Sethi ~ Vikram Sethi, same phone" is
        # bookkeeping. Both are kept, but the interesting ones lead.
        decisions.sort(key=lambda d: (d["a"] == d["b"], d["a"]))
        return entities, decisions
