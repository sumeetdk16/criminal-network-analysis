"""
OCR ingestion for scanned FIRs and station-diary pages.

A great deal of the record an investigator actually holds is paper: a scanned
FIR, a photographed diary page, a fax of a seizure memo. None of it is
machine-readable until it goes through OCR, so the pipeline treats a scan as a
first-class source rather than assuming clean digital text.

Capability detection is deliberate. Tesseract and its language packs may or may
not be installed on any given machine, and a demo must not die because they are
absent. `capabilities()` reports exactly what is available, `ocr_document()`
degrades gracefully, and the console surfaces the result either way.

Installing Devanagari support:
    macOS    brew install tesseract tesseract-lang
    Debian   apt-get install tesseract-ocr tesseract-ocr-hin
    Python   pip install pytesseract pillow
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, asdict


@dataclass
class OcrResult:
    path: str
    text: str
    mean_confidence: float
    languages: str
    engine: str
    ok: bool
    error: str = ""

    def dict(self):
        return asdict(self)


def capabilities() -> dict:
    """What OCR support actually exists on this machine, right now."""
    binary = shutil.which("tesseract")
    langs: list[str] = []
    version = ""
    if binary:
        try:
            langs = [l.strip() for l in subprocess.run(
                [binary, "--list-langs"], capture_output=True, text=True, timeout=20
            ).stdout.splitlines()[1:] if l.strip()]
            version = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=20
            ).stdout.splitlines()[0]
        except Exception:
            pass
    try:
        import pytesseract  # noqa: F401
        has_py = True
    except Exception:
        has_py = False
    try:
        import PIL  # noqa: F401
        has_pil = True
    except Exception:
        has_pil = False
    return {
        "tesseract": bool(binary), "tesseract_version": version,
        "languages": langs, "hindi_available": "hin" in langs,
        "pytesseract": has_py, "pillow": has_pil,
        "available": bool(binary) and has_pil,
        "note": ("Devanagari OCR needs the 'hin' language pack; without it "
                 "scanned Hindi pages are skipped rather than mis-read.")
        if "hin" not in langs else "",
    }


def preferred_languages() -> str:
    caps = capabilities()
    return "eng+hin" if caps["hindi_available"] else "eng"


def ocr_document(path: str, languages: str | None = None) -> OcrResult:
    caps = capabilities()
    langs = languages or preferred_languages()
    if not caps["available"]:
        return OcrResult(path, "", 0.0, langs, "none", False,
                         "Tesseract or Pillow not installed on this machine")
    try:
        from PIL import Image
        import pytesseract
        img = Image.open(path)
        # modest preprocessing: greyscale and upscale small scans, which is where
        # most of the accuracy on a phone photo of a page comes from
        img = img.convert("L")
        if min(img.size) < 1200:
            img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
        text = pytesseract.image_to_string(img, lang=langs)
        data = pytesseract.image_to_data(img, lang=langs,
                                         output_type=pytesseract.Output.DICT)
        confs = [int(c) for c in data.get("conf", []) if str(c).lstrip("-").isdigit()
                 and int(c) >= 0]
        mean = round(sum(confs) / len(confs) / 100, 3) if confs else 0.0
        return OcrResult(path, text.strip(), mean, langs, "tesseract", True)
    except Exception as e:                                 # pragma: no cover
        return OcrResult(path, "", 0.0, langs, "tesseract", False, str(e))


def ocr_folder(folder: str) -> list[OcrResult]:
    if not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff")):
            out.append(ocr_document(os.path.join(folder, name)))
    return out
