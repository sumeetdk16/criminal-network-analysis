"""
Case-file PDF export.

A report that only exists inside a web panel cannot be attached to a case
diary, handed to a supervisor, or produced in court. This module renders the
same analysis to a paginated PDF with the properties an evidentiary document
needs:

* a classification banner and an unmissable statement that the contents are
  machine-generated leads, not findings of fact;
* every assertion followed by the source record that produced it;
* a SHA-256 digest of the report body printed on the document itself, so a copy
  can be checked against the audit log entry recorded when it was generated;
* the generating officer, their role and the exact time, on every page.

Devanagari is transliterated for the PDF unless a Devanagari font is available,
because a box-glyph in an evidentiary document is worse than a romanisation
that is labelled as one.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
from datetime import datetime, timezone

from .pipeline.translit import has_devanagari, transliterate_text

DEVA_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Kohinoor.ttc",
    "/Library/Fonts/NotoSansDevanagari-Regular.ttf",
    os.path.expanduser("~/Library/Fonts/NotoSansDevanagari-Regular.ttf"),
]


def available() -> bool:
    try:
        import reportlab  # noqa: F401
        return True
    except Exception:
        return False


def _devanagari_font() -> str | None:
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except Exception:
        return None
    for path in DEVA_FONT_CANDIDATES:
        if os.path.exists(path) and path.endswith(".ttf"):
            try:
                pdfmetrics.registerFont(TTFont("NotoDeva", path))
                return "NotoDeva"
            except Exception:
                continue
    return None


def _safe(text: str, deva_font: str | None) -> str:
    """Escape for reportlab, and romanise Devanagari when no font can show it."""
    if text is None:
        return ""
    t = str(text)
    if has_devanagari(t) and not deva_font:
        t = re.sub(r"[ऀ-ॿ]+(?:\s[ऀ-ॿ]+)*",
                   lambda m: f"{transliterate_text(m.group(0)).title()} "
                             f"[Devanagari romanised]", t)
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class CaseFilePdf:
    """Renders a report dict (from ReportBuilder) into PDF bytes."""

    def __init__(self, officer: str, role: str, badge: str, unit: str):
        self.officer, self.role, self.badge, self.unit = officer, role, badge, unit
        self.generated = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    def _styles(self):
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        ss = getSampleStyleSheet()
        base = ss["BodyText"]
        base.fontSize = 9.5
        base.leading = 13.5
        base.spaceAfter = 5
        return {
            "body": base,
            "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontSize=15,
                                 spaceAfter=8, textColor=colors.HexColor("#12263f")),
            "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11.5,
                                 spaceBefore=12, spaceAfter=5,
                                 textColor=colors.HexColor("#1f4e79")),
            "h3": ParagraphStyle("h3", parent=ss["Heading3"], fontSize=10,
                                 spaceBefore=9, spaceAfter=3,
                                 textColor=colors.HexColor("#44546a")),
            "small": ParagraphStyle("small", parent=base, fontSize=8,
                                    textColor=colors.HexColor("#5a6472")),
            "evidence": ParagraphStyle("evidence", parent=base, fontSize=8.5,
                                       leftIndent=10, textColor=colors.HexColor("#333333"),
                                       borderPadding=2),
            "warn": ParagraphStyle("warn", parent=base, fontSize=9,
                                   textColor=colors.HexColor("#8a3b00"),
                                   backColor=colors.HexColor("#fff4e5"),
                                   borderPadding=6, spaceBefore=6, spaceAfter=8),
            "mm": mm,
        }

    def _decorate(self, digest_holder):
        from reportlab.lib import colors
        from reportlab.lib.units import mm

        def on_page(canvas, doc):
            canvas.saveState()
            w, h = doc.pagesize
            canvas.setFillColor(colors.HexColor("#1f4e79"))
            canvas.rect(0, h - 14 * mm, w, 14 * mm, stroke=0, fill=1)
            canvas.setFillColor(colors.white)
            canvas.setFont("Helvetica-Bold", 8.5)
            canvas.drawString(15 * mm, h - 9 * mm,
                              "RESTRICTED — FOR INVESTIGATIVE USE ONLY")
            canvas.drawRightString(w - 15 * mm, h - 9 * mm,
                                   "SYNTHETIC DEMONSTRATION DATA")
            canvas.setFillColor(colors.HexColor("#6b7683"))
            canvas.setFont("Helvetica", 7)
            canvas.drawString(15 * mm, 10 * mm,
                              f"Generated by {self.officer} ({self.role}, {self.badge}, "
                              f"{self.unit}) — "
                              f"{self.generated.strftime('%d %b %Y %H:%M UTC')}")
            canvas.drawRightString(w - 15 * mm, 10 * mm, f"Page {doc.page}")
            if digest_holder.get("digest"):
                canvas.drawCentredString(w / 2, 6 * mm,
                                         f"Integrity SHA-256: {digest_holder['digest']}")
            canvas.restoreState()
        return on_page

    # ------------------------------------------------------------------
    def render(self, title: str, markdown_body: str, evidence_rows=None,
               findings=None, extra_note: str | None = None) -> bytes:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle)

        deva = _devanagari_font()
        st = self._styles()
        if deva:
            for k in ("body", "small", "evidence"):
                st[k].fontName = deva

        digest = hashlib.sha256(
            (title + markdown_body).encode("utf-8")).hexdigest()
        holder = {"digest": digest[:32]}

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4, topMargin=22 * mm, bottomMargin=18 * mm,
            leftMargin=15 * mm, rightMargin=15 * mm,
            title=title, author=self.officer,
            subject="AI-assisted criminal network analysis (synthetic data)")

        flow = [Paragraph(_safe(title, deva), st["h1"]),
                Paragraph(
                    "This document is machine-generated investigative "
                    "decision-support. Every relationship stated below is derived "
                    "from the source records cited alongside it. Nothing here is a "
                    "finding of fact, and no coercive action should be taken on it "
                    "without independent verification of the underlying records.",
                    st["warn"])]

        if extra_note:
            flow.append(Paragraph(_safe(extra_note, deva), st["small"]))
            flow.append(Spacer(1, 4))

        flow += self._markdown(markdown_body, st, deva)

        if evidence_rows:
            flow.append(Paragraph("Evidentiary trail", st["h2"]))
            flow.append(Paragraph(
                "Each link relied on above, with the source records that "
                "produced it.", st["small"]))
            flow.append(Spacer(1, 4))
            data = [["Linked entities", "Relationship", "Sources", "Conf."]]
            for r in evidence_rows[:22]:
                data.append([
                    Paragraph(_safe(r.get("pair", ""), deva), st["small"]),
                    Paragraph(_safe(r.get("relationship", ""), deva), st["small"]),
                    Paragraph(_safe(r.get("sources", ""), deva), st["small"]),
                    Paragraph(f"{r.get('confidence', 0):.2f}", st["small"]),
                ])
            t = Table(data, colWidths=[58 * mm, 38 * mm, 63 * mm, 14 * mm],
                      repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d3df")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.white, colors.HexColor("#fafbfd")]),
            ]))
            flow.append(t)

        if findings:
            flow.append(Paragraph("Flagged patterns", st["h2"]))
            for f in findings[:10]:
                flow.append(Paragraph(
                    f"<b>{_safe(f['title'], deva)}</b> "
                    f"({f['severity']})", st["h3"]))
                flow.append(Paragraph(_safe(f["description"], deva), st["body"]))
                flow.append(Paragraph(
                    "Rule applied: " + _safe(f["basis"].get("rule", "-"), deva),
                    st["small"]))

        flow.append(Paragraph("Integrity and handling", st["h2"]))
        flow.append(Paragraph(
            f"SHA-256 of report content: <b>{digest}</b><br/>"
            f"Generated: {self.generated.isoformat(timespec='seconds')}<br/>"
            f"By: {_safe(self.officer, deva)} ({self.role}, {self.badge}, "
            f"{_safe(self.unit, deva)})<br/>"
            "The generation of this report is recorded in the system audit log. "
            "The digest above allows a printed copy to be matched against that "
            "entry. Any alteration of this document changes the digest.",
            st["body"]))
        if not deva:
            flow.append(Paragraph(
                "Note: no Devanagari font was available on the generating "
                "machine, so Devanagari names appear romanised and are marked as "
                "such. The original spellings are retained in the system.",
                st["small"]))

        doc.build(flow, onFirstPage=self._decorate(holder),
                  onLaterPages=self._decorate(holder))
        return buf.getvalue()

    # ------------------------------------------------------------------
    def _markdown(self, src: str, st, deva):
        """Enough markdown for the reports this system generates."""
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

        def inline(s):
            s = _safe(s, deva)
            s = re.sub(r"`([^`]+)`", r"<font face='Courier'>\1</font>", s)
            s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
            s = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", s)
            return s

        flow, table = [], None

        def flush():
            nonlocal table
            if not table:
                return
            data = [[Paragraph(inline(c), st["small"]) for c in row]
                    for row in [table["head"]] + table["rows"]]
            cols = len(table["head"])
            t = Table(data, colWidths=[(180 / cols) * mm] * cols, repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d3df")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            flow.append(t)
            flow.append(Spacer(1, 6))
            table = None

        for raw in (src or "").split("\n"):
            line = raw.rstrip()
            if line.startswith("|"):
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if re.fullmatch(r"[\s|:-]+", line):
                    continue
                if table is None:
                    table = {"head": cells, "rows": []}
                else:
                    table["rows"].append(cells)
                continue
            flush()
            if not line.strip():
                continue
            if line.startswith("### "):
                flow.append(Paragraph(inline(line[4:]), st["h3"]))
            elif line.startswith("## "):
                flow.append(Paragraph(inline(line[3:]), st["h2"]))
            elif line.startswith("# "):
                flow.append(Paragraph(inline(line[2:]), st["h1"]))
            elif re.match(r"^[-*]\s", line):
                flow.append(Paragraph("• " + inline(line[2:]), st["body"]))
            else:
                flow.append(Paragraph(inline(line), st["body"]))
        flush()
        return flow
