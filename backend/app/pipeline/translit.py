"""
Devanagari handling: transliteration, digit normalisation and phonetic keys.

Why this module exists
----------------------
A large share of FIRs and station diaries in India are written in Devanagari,
and the same person is routinely spelled one way in a Hindi FIR and another in
an English CDR export. Unless "विक्रम सेठी" and "Vikram Sethi" collapse onto one
identity, a multilingual corpus produces two disconnected halves of the same
network - which is exactly the failure the problem statement describes.

Three tools, in increasing tolerance:

1. `deva_to_latin`   - syllable-aware transliteration (ISO-ish, not exact).
2. `normalize_digits`- Devanagari numerals to ASCII, so ९००० parses as 9000.
3. `skeleton`        - a consonant-skeleton phonetic key that absorbs the
   schwa-deletion problem. Hindi drops the inherent 'a' in ways no simple
   transliterator gets right ("इमरान" naively becomes *imaraana*), so instead of
   fighting for an exact Latin string we compare consonant skeletons:
   imaraana -> imrn, imran -> imrn. Same for Sheikh/Shekh, Qureshi/Kureshi.
"""

from __future__ import annotations

import re
import unicodedata

# ------------------------------------------------------------------ tables

CONSONANTS = {
    'क': 'k', 'ख': 'kh', 'ग': 'g', 'घ': 'gh', 'ङ': 'ng',
    'च': 'ch', 'छ': 'chh', 'ज': 'j', 'झ': 'jh', 'ञ': 'n',
    'ट': 't', 'ठ': 'th', 'ड': 'd', 'ढ': 'dh', 'ण': 'n',
    'त': 't', 'थ': 'th', 'द': 'd', 'ध': 'dh', 'न': 'n',
    'प': 'p', 'फ': 'ph', 'ब': 'b', 'भ': 'bh', 'म': 'm',
    'य': 'y', 'र': 'r', 'ल': 'l', 'व': 'v', 'ळ': 'l',
    'श': 'sh', 'ष': 'sh', 'स': 's', 'ह': 'h',
    'क़': 'q', 'ख़': 'kh', 'ग़': 'g', 'ज़': 'z', 'ड़': 'd', 'ढ़': 'dh', 'फ़': 'f',
}

INDEPENDENT_VOWELS = {
    'अ': 'a', 'आ': 'aa', 'इ': 'i', 'ई': 'ee', 'उ': 'u', 'ऊ': 'oo',
    'ऋ': 'ri', 'ए': 'e', 'ऐ': 'ai', 'ओ': 'o', 'औ': 'au',
}

MATRAS = {
    'ा': 'aa', 'ि': 'i', 'ी': 'ee', 'ु': 'u', 'ू': 'oo', 'ृ': 'ri',
    'े': 'e', 'ै': 'ai', 'ो': 'o', 'ौ': 'au',
}

VIRAMA = '्'
ANUSVARA = 'ं'
CHANDRABINDU = 'ँ'
VISARGA = 'ः'
NUKTA = '़'

DIGITS = str.maketrans('०१२३४५६७८९', '0123456789')

DEVANAGARI_RE = re.compile(r'[ऀ-ॿ]')


def has_devanagari(text: str) -> bool:
    return bool(DEVANAGARI_RE.search(text or ''))


def normalize_digits(text: str) -> str:
    """Devanagari numerals to ASCII, so ९००००००००१ parses as a phone number."""
    return (text or '').translate(DIGITS)


# ------------------------------------------------------- transliteration

def deva_to_latin(text: str) -> str:
    """
    Syllable-aware Devanagari to Latin. Not a scholarly scheme - it exists to
    feed `skeleton()` and the gazetteers, and is judged only on whether the
    right records end up merged.
    """
    if not text:
        return ''
    text = unicodedata.normalize('NFC', text)
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        # consonant + optional nukta
        base = ch
        if i + 1 < n and text[i + 1] == NUKTA:
            comb = unicodedata.normalize('NFC', ch + NUKTA)
            base = comb if comb in CONSONANTS else ch
            i += 1
        if base in CONSONANTS:
            out.append(CONSONANTS[base])
            nxt = text[i + 1] if i + 1 < n else ''
            if nxt in MATRAS:
                out.append(MATRAS[nxt])
                i += 2
            elif nxt == VIRAMA:
                i += 2                      # inherent vowel suppressed
            else:
                out.append('a')             # inherent vowel
                i += 1
            continue
        if ch in INDEPENDENT_VOWELS:
            out.append(INDEPENDENT_VOWELS[ch]); i += 1; continue
        if ch in (ANUSVARA, CHANDRABINDU):
            out.append('n'); i += 1; continue
        if ch == VISARGA:
            out.append('h'); i += 1; continue
        if ch in MATRAS:                    # stray matra
            out.append(MATRAS[ch]); i += 1; continue
        out.append(ch.translate(DIGITS)); i += 1
    latin = ''.join(out)
    latin = re.sub(r'([aeiou])\1+', r'\1', latin)     # collapse doubled vowels
    latin = re.sub(r'\ba\b', '', latin)
    return latin.strip()


def transliterate_text(text: str) -> str:
    """Transliterate only the Devanagari runs, leaving Latin/digits intact."""
    return re.sub(r'[ऀ-ॿ]+', lambda m: deva_to_latin(m.group(0)), text or '')


# ------------------------------------------------------------- phonetics

# Cross-script equivalences: the same sound, different conventional spelling.
_PHONETIC = str.maketrans({'q': 'k', 'z': 'j', 'w': 'v', 'x': 'k'})
# 'y' is treated as a glide rather than a consonant in non-initial position:
# नायर transliterates to nayar, the roll spells it Nair - same name.
_VOWELS = set('aeiou')
_TAIL_DROP = _VOWELS | {'y'}


def skeleton(word: str) -> str:
    """
    Consonant skeleton of a name token: first character kept, later vowels
    dropped, doubled consonants collapsed, common cross-script letters folded.

        vikram / vikrama   -> vkrm
        sheikh / shekh     -> shkh
        qureshi / kureshi  -> krsh
        farid / phared     -> frd
        ansari / ansaari   -> ansr
    """
    w = re.sub(r'[^a-z]', '', (word or '').lower())
    # 'ph' and 'f' are the same sound and the two scripts disagree constantly
    # (फरीद transliterates to phared, the CDR spells it Farid)
    w = w.replace('ph', 'f').translate(_PHONETIC)
    if not w:
        return ''
    head, tail = w[0], w[1:]
    tail = ''.join(c for c in tail if c not in _TAIL_DROP)
    out = head + tail
    return re.sub(r'(.)\1+', r'\1', out)


def name_key(name: str) -> tuple:
    """Order-insensitive phonetic key for a full name, for blocking."""
    parts = [skeleton(p) for p in re.split(r'[\s.]+', transliterate_text(name or '')) if p]
    return tuple(sorted(p for p in parts if p))
