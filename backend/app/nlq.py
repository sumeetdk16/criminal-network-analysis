"""
Natural-language query interface.

An investigator should be able to type "how is Devendra Rathi connected to
Vikram Sethi" rather than learn a graph query language. The parser is
intent-based and deterministic, and it always returns `interpretation` - a
plain restatement of what it understood - so a wrong reading is visible
immediately instead of silently producing a confident wrong answer.

Swapping this for an LLM-backed text-to-query step is a drop-in change: keep
the same `ParsedQuery` contract and keep returning the interpretation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict


@dataclass
class ParsedQuery:
    intent: str
    interpretation: str
    params: dict = field(default_factory=dict)
    confidence: float = 0.0
    unresolved: list[str] = field(default_factory=list)

    def dict(self):
        return asdict(self)


AMOUNT = re.compile(
    r"(?:over|above|more than|greater than|exceeding|>)\s*(?:rs\.?|inr|₹)?\s*"
    r"([\d,.]+)\s*(crore|cr|lakh|lakhs|l|thousand|k)?", re.I)
HOPS = re.compile(r"within\s+(\d+)\s*(?:-|\s)?hops?", re.I)
DAYS = re.compile(r"(?:last|past)\s+(\d+)\s*(day|days|week|weeks|month|months)", re.I)

MULT = {"crore": 10_000_000, "cr": 10_000_000, "lakh": 100_000, "lakhs": 100_000,
        "l": 100_000, "thousand": 1_000, "k": 1_000, None: 1, "": 1}


class QueryParser:
    def __init__(self, case_graph):
        self.cg = case_graph
        self.names = {}
        for n, d in case_graph.G.nodes(data=True):
            label = (d.get("label") or "").strip()
            if not label:
                continue
            self.names[label.lower()] = n
            for a in d.get("aliases", []) or []:
                self.names[a.lower()] = n

    # ------------------------------------------------------------------
    def _find_names(self, text: str) -> list[tuple[str, str]]:
        """
        Return [(matched_text, node_id)], longest match first.

        Falls back to distinctive-token matching so an investigator can type
        "Rathi" or "Sethi" instead of the full canonical spelling the system
        happens to have chosen.
        """
        found, low = [], text.lower()
        for name in sorted(self.names, key=len, reverse=True):
            if len(name) < 4:
                continue
            i = low.find(name)
            if i >= 0 and not any(i < e and i + len(name) > s for s, e, _ in found):
                found.append((i, i + len(name), self.names[name]))

        # token fallback: a distinctive surname that belongs to exactly one node
        token_owner: dict[str, set] = {}
        for name, nid in self.names.items():
            for tok in name.split():
                if len(tok) >= 4:
                    token_owner.setdefault(tok, set()).add(nid)
        for m in re.finditer(r"[A-Za-z]{4,}", text):
            tok = m.group(0).lower()
            owners = token_owner.get(tok)
            if not owners:
                continue
            if len(owners) > 1:
                # a token shared by a person and their company ("Rathi") is not
                # truly ambiguous - an investigator means the person
                people = {o for o in owners
                          if self.cg.G.nodes[o].get("type") == "PERSON"}
                if len(people) != 1:
                    continue
                owners = people
            s0, e0 = m.start(), m.end()
            if any(s0 < e and e0 > s for s, e, _ in found):
                continue
            found.append((s0, e0, next(iter(owners))))

        found.sort()
        # keep the first mention of each distinct entity, in reading order
        seen, out = set(), []
        for s, e, nid in found:
            if nid in seen:
                continue
            seen.add(nid)
            out.append((text[s:e], nid))
        return out

    @staticmethod
    def _amount(text: str):
        m = AMOUNT.search(text)
        if not m:
            return None
        raw = float(m.group(1).replace(",", ""))
        return int(raw * MULT.get((m.group(2) or "").lower(), 1))

    # ------------------------------------------------------------------
    def parse(self, text: str) -> ParsedQuery:
        t = text.strip()
        low = t.lower()
        names = self._find_names(t)
        labels = [self.cg.G.nodes[n].get("label") for _, n in names]
        hops = int(HOPS.search(t).group(1)) if HOPS.search(t) else None
        amount = self._amount(t)

        # 1. connection between two named people
        if len(names) >= 2 and re.search(
                r"connect|link|relation|path|between|associated with|how (is|are)", low):
            a, b = names[0][1], names[1][1]
            return ParsedQuery(
                "path", f"Find how {labels[0]} is connected to {labels[1]}"
                        + (f", within {hops} hops" if hops else ""),
                {"a": a, "b": b, "cutoff": hops or 5}, 0.92)

        # 2. neighbourhood of one person
        if names and re.search(r"connected to|linked to|associates of|around|network of|"
                               r"who (is|are).*(with|near)|neighbou?rhood", low):
            return ParsedQuery(
                "neighbourhood",
                f"Show everyone connected to {labels[0]} within {hops or 2} hops"
                + (f", filtered to transactions over Rs {amount:,}" if amount else ""),
                {"node": names[0][1], "hops": hops or 2, "min_amount": amount}, 0.85)

        # 3. key people
        if re.search(r"key (people|player|individual|influencer)|most important|top "
                     r"(suspect|influencer|people)|kingpin|leader|central", low):
            n = int(re.search(r"top\s+(\d+)", low).group(1)) if re.search(r"top\s+(\d+)", low) else 10
            return ParsedQuery("influencers",
                               f"Rank the {n} most influential members of the network",
                               {"limit": n}, 0.90)

        # 4. suspicious activity
        if re.search(r"suspicious|anomal|unusual|red flag|laundering|pattern", low):
            return ParsedQuery("anomalies",
                               "List detected suspicious patterns, highest severity first",
                               {}, 0.88)

        # 5. financial
        if re.search(r"transaction|payment|money|transfer|fund|paid|hawala", low):
            return ParsedQuery(
                "transactions",
                "List financial transactions"
                + (f" over Rs {amount:,}" if amount else "")
                + (f" involving {labels[0]}" if names else ""),
                {"min_amount": amount or 0,
                 "node": names[0][1] if names else None}, 0.80)

        # 6. calls
        if re.search(r"call|phone|contact|spoke|cdr", low):
            return ParsedQuery(
                "calls", "List call links" + (f" involving {labels[0]}" if names else ""),
                {"node": names[0][1] if names else None}, 0.78)

        # 7. groups
        if re.search(r"group|cluster|gang|cell|communit", low):
            return ParsedQuery("communities",
                               "Show the detected sub-groups within the network", {}, 0.85)

        # 8. one person
        if names:
            return ParsedQuery("entity", f"Show the case profile for {labels[0]}",
                               {"node": names[0][1]}, 0.7)

        return ParsedQuery(
            "unknown",
            "Could not interpret the question. Try naming a person, or ask "
            "\"how is A connected to B\", \"who are the key people\", or "
            "\"show suspicious patterns\".",
            {}, 0.0,
            unresolved=[w for w in re.findall(r"[A-Z][a-z]+", t)])
