"""
Entity extraction (NER) over unstructured investigation text.

Design note
-----------
The extractor is deliberately split into a *pluggable strategy* interface.
The default strategy is rule + gazetteer based so the prototype runs offline
with zero model downloads on any machine. A production deployment swaps in a
transformer NER model (spaCy / IndicNER / a fine-tuned IndicBERT) by
implementing the same `Extractor.extract(text) -> list[Mention]` contract -
nothing downstream changes.

Every mention carries the character span and a confidence, so the UI can
highlight exactly which words in which document produced a graph node.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
from typing import Iterable

from .translit import (has_devanagari, normalize_digits, transliterate_text,
                       skeleton)


# ---------------------------------------------------------------- data model

@dataclass
class Mention:
    text: str
    type: str            # PERSON | PHONE | VEHICLE | LOCATION | ORG | ACCOUNT | MONEY | DATE
    start: int
    end: int
    confidence: float
    source_id: str = ""      # e.g. FIR/2026/0101
    source_type: str = ""    # fir | surveillance | social_media | ...
    context: str = ""        # surrounding sentence, for the evidence panel
    script: str = "latin"    # latin | devanagari
    normalized: str = ""     # transliterated form, what resolution actually matches on

    def __post_init__(self):
        # A name that wraps across a line in the source is still one name.
        # Left un-normalised, the newline travels all the way into the merge
        # table and breaks the rendering of every row after it.
        self.text = " ".join((self.text or "").split())
        if not self.normalized:
            self.normalized = (transliterate_text(self.text).title()
                               if self.script == "devanagari" else self.text)

    def dict(self):
        return asdict(self)


# ---------------------------------------------------------------- gazetteers

KNOWN_ORGS = [
    "Meridian Exim Pvt Ltd", "Rathi Infrastructure Ltd", "Sunrise Logistics",
    "Al-Noor Trading Co", "Konkan Marine Services",
]

KNOWN_LOCATIONS = [
    "Ghatkopar", "Kurla", "Dongri", "Chembur", "Fort", "Zaveri Bazaar",
    "Byculla", "Worli", "Nagpada", "JNPT Port", "Bhiwandi Godown", "Bhiwandi",
]

# Devanagari location gazetteer, mapped back to the canonical Latin label so a
# Hindi FIR and an English CDR resolve to the same location node.
DEVANAGARI_LOCATIONS = {
    "घाटकोपर": "Ghatkopar", "कुर्ला": "Kurla", "डोंगरी": "Dongri",
    "चेंबूर": "Chembur", "भायखला": "Byculla", "वरली": "Worli",
    "नागपाडा": "Nagpada", "भिवंडी गोदाम": "Bhiwandi Godown",
    "भिवंडी": "Bhiwandi", "झवेरी बाजार": "Zaveri Bazaar",
    "जेएनपीटी बंदरगाह": "JNPT Port", "फोर्ट": "Fort",
}

# Surname / name-particle lexicon used to validate heuristic PERSON candidates.
NAME_LEXICON = {
    "sethi", "seth", "kumar", "kr", "singh", "qureshi", "quraishi", "bhosale",
    "bhosle", "salunke", "ansari", "gaikwad", "yadav", "tiwari", "nair",
    "chauhan", "sheikh", "shaikh", "rathi", "deshpande", "mirza", "pawar",
    "khan", "jadhav", "more", "shankar", "devendra", "vikram", "rajesh",
    "imran", "sanjay", "pravin", "nadeem", "ashok", "suresh", "manoj", "ravi",
    "deepak", "farid", "fareed", "kiran", "salim", "ganesh", "altaf", "ramesh",
    "sunil", "vinod", "prakash",
}

# Words that look like names (capitalised) but are not.
STOP_TITLES = {
    "The", "On", "During", "Two", "Both", "Complainant", "Accused", "Subject",
    "Recovered", "Further", "Notice", "Documents", "Cash", "Mobile", "Contact",
    "Static", "Container", "Meeting", "Sources", "Names", "Funds", "Police",
    "Station", "Act", "Arms", "Sole", "Registrar", "Companies", "Anti",
    "Narcotics", "Cell", "Exited", "Met", "Seen", "Director",
}


NAME_SKELETONS = set()   # populated below, used to validate Devanagari candidates


# ------------------------------------------------------------------ patterns

NAME_SKELETONS.update(skeleton(t) for t in NAME_LEXICON)

PATTERNS = {
    "PHONE":   re.compile(r"\b([6-9]\d{9})\b"),
    "VEHICLE": re.compile(r"\b(MH\s?\d{2}\s?[A-Z]{1,2}\s?\d{3,4})\b"),
    "ACCOUNT": re.compile(r"\b(AC[A-Z]{3}\d{4})\b"),
    "MONEY":   re.compile(r"(?:Rs\.?|INR|₹)\s?([\d,]+(?:\.\d+)?)\s*(crore|lakh|cr|l)?", re.I),
    "DATE":    re.compile(r"\b(\d{2}/\d{2}/\d{4})\b"),
    "IMEI":    re.compile(r"\b(\d{15})\b"),
}

# A capitalised run of 2-3 tokens, allowing initials like "V. Sethi".
PERSON_CANDIDATE = re.compile(
    r"\b((?:[A-Z]\.[ \t]?|[A-Z][a-z]{2,}[ \t]){1,2}[A-Z][a-z]{2,})\b"
)

# Devanagari has no capitalisation to lean on, so a candidate is any run of two
# or three Devanagari words, validated against the phonetic skeletons of the
# name lexicon. Function words are excluded outright.
DEVA_CANDIDATE = re.compile(
    r"([\u0900-\u097F]{2,}(?:[ \t][\u0900-\u097F]{2,}){1,2})")

DEVA_STOPWORDS = {
    "दिनांक", "समय", "गुप्त", "सूचना", "आधार", "छापेमारी", "वाहन", "क्रमांक",
    "आरोपी", "हिरासत", "तलाशी", "दौरान", "ग्राम", "प्रतिबंधित", "पदार्थ",
    "बरामद", "पूछताछ", "बताया", "कहने", "मंगाई", "आगे", "जांच", "जारी",
    "फरियादी", "बयान", "अनुसार", "स्थित", "उसकी", "दुकान", "उसके", "साथी",
    "हफ्ता", "वसूली", "लेकर", "धमकी", "सफेद", "रंग", "गाड़ी", "पूर्व",
    "दर्ज", "अपराधों", "शामिल", "रहा", "काम", "करता", "भरे", "मोबाइल",
    "नंबर", "किए", "गए", "नाकाबंदी", "नामक", "व्यक्ति", "कब्जे", "देशी",
    "पिस्तौल", "मोटरसाइकिल", "सवार", "उसने", "माल", "लिया", "उसे", "केवल",
    "पहुंचाने", "पुलिस", "स्टेशन", "अधिनियम", "धारा", "एक", "तथा", "बजे",
    "द्वारा", "पास", "में", "से", "को", "का", "की", "के", "है", "था", "थी",
}


def _context(text: str, start: int, end: int, window: int = 90) -> str:
    a = max(0, start - window)
    b = min(len(text), end + window)
    return ("..." if a > 0 else "") + text[a:b].replace("\n", " ") + ("..." if b < len(text) else "")


# ------------------------------------------------------------------ strategy

class Extractor:
    """Base contract. Swap the implementation, keep the pipeline."""
    name = "base"

    def extract(self, text: str) -> list[Mention]:
        raise NotImplementedError


class RuleGazetteerExtractor(Extractor):
    """Offline, deterministic, zero-dependency extractor."""

    name = "rule+gazetteer"

    def __init__(self, extra_person_forms: Iterable[str] = ()):
        forms = set(extra_person_forms)
        # Longest first so "Rajesh Kumar Singh" wins over "Rajesh Kumar", with the
        # string itself as the tiebreaker. Sorting by length alone leaves equal
        # -length names in set order, which Python randomises per process - and a
        # tool that gives a different answer on a second run is not usable.
        order = lambda f: (-len(f), f)
        self.person_gazetteer = sorted(
            {f for f in forms if not has_devanagari(f)}, key=order)
        self.devanagari_persons = sorted(
            {f for f in forms if has_devanagari(f)}, key=order)

    # -- helpers ---------------------------------------------------------
    def _regex_mentions(self, text: str) -> list[Mention]:
        out: list[Mention] = []
        for typ, pat in PATTERNS.items():
            for m in pat.finditer(text):
                out.append(Mention(
                    text=m.group(1).strip(), type=typ, start=m.start(1), end=m.end(1),
                    confidence=0.97, context=_context(text, m.start(1), m.end(1)),
                ))
        return out

    def _gazetteer_mentions(self, text: str, terms: list[str], typ: str,
                            conf: float) -> list[Mention]:
        out: list[Mention] = []
        for term in terms:
            for m in re.finditer(re.escape(term), text):
                out.append(Mention(
                    text=term, type=typ, start=m.start(), end=m.end(),
                    confidence=conf, context=_context(text, m.start(), m.end()),
                ))
        return out

    def _devanagari_mentions(self, text: str) -> list[Mention]:
        out: list[Mention] = []
        for deva, latin in DEVANAGARI_LOCATIONS.items():
            for m in re.finditer(re.escape(deva), text):
                out.append(Mention(text=deva, type="LOCATION", start=m.start(),
                                   end=m.end(), confidence=0.92,
                                   context=_context(text, m.start(), m.end()),
                                   script="devanagari", normalized=latin))
        for deva in self.devanagari_persons:
            for m in re.finditer(re.escape(deva), text):
                out.append(Mention(text=deva, type="PERSON", start=m.start(),
                                   end=m.end(), confidence=0.93,
                                   context=_context(text, m.start(), m.end()),
                                   script="devanagari"))
        # heuristic: unseen Devanagari names validated by phonetic skeleton
        for m in DEVA_CANDIDATE.finditer(text):
            cand = m.group(1).strip()
            toks = cand.split()
            if any(t in DEVA_STOPWORDS for t in toks):
                continue
            skels = [skeleton(t) for t in transliterate_text(cand).split()]
            if not any(s_ in NAME_SKELETONS for s_ in skels):
                continue
            out.append(Mention(text=cand, type="PERSON", start=m.start(1),
                               end=m.end(1), confidence=0.70,
                               context=_context(text, m.start(1), m.end(1)),
                               script="devanagari"))
        return out

    def _heuristic_persons(self, text: str) -> list[Mention]:
        out: list[Mention] = []
        for m in PERSON_CANDIDATE.finditer(text):
            cand = m.group(1).strip()
            tokens = cand.split()
            if tokens[0].rstrip(".") in STOP_TITLES:
                continue
            lowered = [t.lower().strip(".") for t in tokens]
            if not any(t in NAME_LEXICON for t in lowered):
                continue
            out.append(Mention(
                text=cand, type="PERSON", start=m.start(1), end=m.end(1),
                confidence=0.72, context=_context(text, m.start(1), m.end(1)),
            ))
        return out

    # -- main ------------------------------------------------------------
    def extract(self, text: str) -> list[Mention]:
        mentions: list[Mention] = []
        # Devanagari numerals are a 1:1 character substitution, so offsets in the
        # normalised string still index the original text correctly.
        mentions += self._regex_mentions(normalize_digits(text))
        mentions += self._gazetteer_mentions(text, KNOWN_ORGS, "ORG", 0.95)
        mentions += self._gazetteer_mentions(text, KNOWN_LOCATIONS, "LOCATION", 0.90)
        mentions += self._gazetteer_mentions(text, self.person_gazetteer, "PERSON", 0.93)
        mentions += self._heuristic_persons(text)
        if has_devanagari(text):
            mentions += self._devanagari_mentions(text)
        return _dedupe_spans(mentions)


class SpacyExtractor(Extractor):
    """
    Optional upgrade path. Used automatically if spaCy and a model are present;
    falls back to the rule extractor otherwise. Kept thin on purpose - the
    hackathon demo must never depend on a model download succeeding.
    """
    name = "spacy"

    def __init__(self, model: str = "en_core_web_sm", fallback: Extractor | None = None):
        self.fallback = fallback or RuleGazetteerExtractor()
        try:
            import spacy  # noqa
            self.nlp = spacy.load(model)
            self.ok = True
        except Exception:
            self.nlp = None
            self.ok = False

    _MAP = {"PERSON": "PERSON", "GPE": "LOCATION", "LOC": "LOCATION",
            "ORG": "ORG", "FAC": "LOCATION", "MONEY": "MONEY", "DATE": "DATE"}

    def extract(self, text: str) -> list[Mention]:
        base = self.fallback.extract(text)
        if not self.ok:
            return base
        doc = self.nlp(text)
        for ent in doc.ents:
            typ = self._MAP.get(ent.label_)
            if not typ:
                continue
            base.append(Mention(text=ent.text, type=typ, start=ent.start_char,
                                end=ent.end_char, confidence=0.85,
                                context=_context(text, ent.start_char, ent.end_char)))
        return _dedupe_spans(base)


# Types that outrank a heuristic PERSON guess when spans overlap. Without this,
# "Rathi Infrastructure Ltd" is happily read as a human being called Rathi.
_PRECEDENCE = {"ORG": 3, "LOCATION": 2, "PERSON": 1}


def _dedupe_spans(mentions: list[Mention]) -> list[Mention]:
    """Resolve overlapping spans: same type keeps the best, different types
    defer to precedence (ORG > LOCATION > PERSON)."""
    mentions.sort(key=lambda m: (m.start, -(m.end - m.start), -m.confidence))
    kept: list[Mention] = []
    for m in mentions:
        drop = False
        for k in list(kept):
            overlap = not (m.end <= k.start or m.start >= k.end)
            if not overlap:
                continue
            if k.type == m.type:
                drop = True
                break
            pk, pm = _PRECEDENCE.get(k.type, 0), _PRECEDENCE.get(m.type, 0)
            if pk and pm:
                if pm > pk:
                    kept.remove(k)
                else:
                    drop = True
                    break
        if not drop:
            kept.append(m)
    return kept


def default_extractor(person_forms: Iterable[str] = ()) -> Extractor:
    ext = SpacyExtractor(fallback=RuleGazetteerExtractor(person_forms))
    return ext if ext.ok else RuleGazetteerExtractor(person_forms)
