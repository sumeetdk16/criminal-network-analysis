"""
Narrative summarisation of graph findings.

Two backends behind one interface:

* `TemplateSummariser` (default) - deterministic, offline, and incapable of
  inventing a fact that is not in the structured input. Every sentence is
  assembled from values that came out of the graph.
* `LLMSummariser` - an optional OpenAI-compatible chat endpoint, enabled by
  environment variables. It reads better; it can also hallucinate, which in
  this domain is a serious failure mode.

The guard rails matter more than the prose:

1. The LLM is given ONLY structured facts already derived from source records,
   never raw case text, and is instructed to add nothing.
2. Its output is checked - every name it mentions must appear in the facts it
   was given. If it names anyone else, the output is discarded and the template
   version is used instead.
3. Which backend produced the text is always reported, and LLM output is
   labelled as machine-drafted in the interface.

Enable with:
    export CNAS_LLM_ENDPOINT=https://api.openai.com/v1/chat/completions
    export CNAS_LLM_KEY=sk-...
    export CNAS_LLM_MODEL=gpt-4o-mini
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

SYSTEM_PROMPT = (
    "You write short factual briefing notes for a police investigator. "
    "You will be given structured facts extracted from case records. "
    "Rules you must follow exactly:\n"
    "1. Use ONLY the facts given. Never add a name, number, place, date or "
    "inference that is not present in them.\n"
    "2. Never state or imply guilt. Describe connections and what supports "
    "them, nothing more.\n"
    "3. Plain English, under 130 words, no headings, no bullet points.\n"
    "4. If the facts are thin, say so rather than filling the gap."
)


class Summariser:
    name = "base"
    def summarise(self, facts: dict) -> dict: raise NotImplementedError


class TemplateSummariser(Summariser):
    name = "template"

    def summarise(self, facts: dict) -> dict:
        kind = facts.get("kind")
        if kind == "path":
            return {"text": self._path(facts), "backend": self.name,
                    "machine_drafted": False}
        if kind == "subject":
            return {"text": self._subject(facts), "backend": self.name,
                    "machine_drafted": False}
        return {"text": "", "backend": self.name, "machine_drafted": False}

    @staticmethod
    def _path(f) -> str:
        chain = " then ".join(f["labels"][1:-1]) or "no intermediary"
        srcs = ", ".join(f.get("source_types", []))
        legs = f.get("legs", [])
        strongest = max(legs, key=lambda l: l.get("observations", 0), default=None)
        s = (f"{f['labels'][0]} and {f['labels'][-1]} are not directly connected in "
             f"the records held. They are linked through {chain}, over "
             f"{f['hops']} steps, on the strength of {srcs} material.")
        if strongest:
            s += (f" The best-supported step is {strongest['from_label']} to "
                  f"{strongest['to_label']}, seen {strongest['observations']} "
                  f"time(s).")
        s += (" A path of this kind shows a route by which contact could pass; "
              "it is a lead to verify, not a finding of association.")
        return s

    @staticmethod
    def _subject(f) -> str:
        bits = [f"{f['label']} appears in {f['source_count']} source record(s)"]
        if f.get("aliases"):
            bits.append(f"under {len(f['aliases']) + 1} different spellings")
        s = ", ".join(bits) + "."
        if f.get("prior_cases"):
            s += (f" There are {f['prior_cases']} prior case(s) on record "
                  f"({f.get('offences', '-')}).")
        elif f.get("record_checked"):
            s += " No prior case was found on a criminal-history check."
        else:
            s += " No criminal-history entry was located, which means not checked."
        if f.get("rank"):
            s += (f" The subject ranks {f['rank']} of {f['population']} by network "
                  f"influence, driven mainly by {f.get('top_driver', 'connectivity')}.")
        if f.get("top_links"):
            s += " Strongest links: " + ", ".join(f["top_links"][:3]) + "."
        if f.get("finding_titles"):
            s += " Flagged patterns: " + "; ".join(f["finding_titles"][:3]) + "."
        return s


class LLMSummariser(Summariser):
    name = "llm"

    def __init__(self, fallback: Summariser | None = None):
        self.endpoint = os.environ.get("CNAS_LLM_ENDPOINT", "").strip()
        self.key = os.environ.get("CNAS_LLM_KEY", "").strip()
        self.model = os.environ.get("CNAS_LLM_MODEL", "gpt-4o-mini").strip()
        self.timeout = float(os.environ.get("CNAS_LLM_TIMEOUT", "20"))
        self.fallback = fallback or TemplateSummariser()

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.key)

    # -- guard rail ------------------------------------------------------
    @staticmethod
    def _allowed_names(facts: dict) -> set[str]:
        names = set()
        def walk(v):
            if isinstance(v, str):
                names.update(re.findall(r"[A-Z][a-zA-Z]{2,}", v))
            elif isinstance(v, dict):
                [walk(x) for x in v.values()]
            elif isinstance(v, (list, tuple)):
                [walk(x) for x in v]
        walk(facts)
        return names

    def _hallucinated(self, text: str, facts: dict) -> list[str]:
        allowed = self._allowed_names(facts) | {
            "The", "This", "They", "There", "It", "A", "An", "No", "Both",
            "Flagged", "Strongest", "Their", "He", "She", "In", "On", "At"}
        seen = set(re.findall(r"[A-Z][a-zA-Z]{2,}", text))
        return sorted(seen - allowed)

    # -- main ------------------------------------------------------------
    def summarise(self, facts: dict) -> dict:
        base = self.fallback.summarise(facts)
        if not self.configured:
            base["llm_status"] = "not configured"
            return base
        try:
            body = json.dumps({
                "model": self.model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content":
                        "Structured facts:\n" + json.dumps(facts, ensure_ascii=False,
                                                           indent=1)},
                ],
            }).encode()
            req = urllib.request.Request(
                self.endpoint, data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.key}"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
            text = data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            base["llm_status"] = f"call failed, using template: {e}"
            return base

        bad = self._hallucinated(text, facts)
        if bad:
            base["llm_status"] = ("rejected: output named entities absent from the "
                                  f"source facts ({', '.join(bad[:5])})")
            base["rejected_draft"] = text
            return base
        return {"text": text, "backend": self.name, "machine_drafted": True,
                "model": self.model, "llm_status": "ok",
                "guard": "every named entity verified against the source facts"}


def default_summariser() -> Summariser:
    llm = LLMSummariser()
    return llm if llm.configured else TemplateSummariser()


def status() -> dict:
    llm = LLMSummariser()
    return {"active": "llm" if llm.configured else "template",
            "llm_configured": llm.configured,
            "model": llm.model if llm.configured else None,
            "guard_rails": [
                "the model sees only structured facts derived from source records",
                "output is rejected if it names an entity absent from those facts",
                "machine-drafted text is labelled as such in the interface",
            ]}
