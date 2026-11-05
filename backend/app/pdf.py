"""Render a recorded session's forensic dossier as a printable A4 PDF.

Why this exists: the officer UI shows a SOP/FIR checklist that an officer would
otherwise retype into a case file. This turns the archive into something a
station can hand to an investigating officer, with the evidence hashes and the
Responsible-AI caveat printed on the document itself.

Only ``reportlab`` is required. A Unicode TTF is used when the platform has
one; otherwise the built-in Helvetica is used and non-Latin text is
transliterated so the document never contains missing glyphs.
"""

from __future__ import annotations

import io
from typing import Any, Dict, Iterable, List
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_FONT_CANDIDATES = (
    ("CERS", "C:/Windows/Fonts/segoeui.ttf"),
    ("CERS-Bold", "C:/Windows/Fonts/segoeuib.ttf"),
    ("CERS", "C:/Windows/Fonts/arial.ttf"),
    ("CERS-Bold", "C:/Windows/Fonts/arialbd.ttf"),
    ("CERS", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ("CERS-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
)

BODY_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"
_UNICODE_FONTS = False


def _register_fonts() -> None:
    global BODY_FONT, BOLD_FONT, _UNICODE_FONTS
    try:
        import os

        for name, path in _FONT_CANDIDATES:
            if os.path.isfile(path):
                pdfmetrics.registerFont(TTFont(name, path))
        if "CERS" in pdfmetrics.getRegisteredFontNames():
            BODY_FONT = "CERS"
            BOLD_FONT = "CERS-Bold" if "CERS-Bold" in pdfmetrics.getRegisteredFontNames() else "CERS"
            _UNICODE_FONTS = True
    except Exception:  # pragma: no cover - font availability only
        BODY_FONT, BOLD_FONT, _UNICODE_FONTS = "Helvetica", "Helvetica-Bold", False


_register_fonts()

# Emoji and other symbols that core fonts cannot render.
_STRIP_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
    (0xFE00, 0xFE0F),
    (0x1F1E6, 0x1F1FF),
)
_REPLACEMENTS = {
    "₹": "Rs.",
    "—": "-",
    "–": "-",
    "’": "'",
    "‘": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    "•": "-",
}


def _safe(value: Any) -> str:
    """Make arbitrary LLM/caller text safe for a PDF paragraph."""
    text = "" if value is None else str(value)
    for source, target in _REPLACEMENTS.items():
        text = text.replace(source, target)
    if not _UNICODE_FONTS:
        cleaned = []
        for char in text:
            code = ord(char)
            if any(low <= code <= high for low, high in _STRIP_RANGES):
                continue
            cleaned.append(char if code < 256 else "?")
        text = "".join(cleaned)
    else:
        text = "".join(
            char
            for char in text
            if not any(low <= ord(char) <= high for low, high in _STRIP_RANGES)
        )
    return escape(text)


def _styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    common = {"fontName": BODY_FONT, "alignment": TA_LEFT}
    return {
        "title": ParagraphStyle(
            "CersTitle",
            parent=base["Title"],
            fontName=BOLD_FONT,
            fontSize=17,
            leading=21,
            spaceAfter=2,
            textColor=colors.HexColor("#0B2545"),
        ),
        "subtitle": ParagraphStyle(
            "CersSub", parent=base["Normal"], fontSize=9, leading=12,
            textColor=colors.HexColor("#555555"), **common,
        ),
        "h2": ParagraphStyle(
            "CersH2", parent=base["Heading2"], fontName=BOLD_FONT,
            fontSize=10.5, leading=13, spaceBefore=9, spaceAfter=3,
            textColor=colors.HexColor("#0B2545"),
        ),
        "body": ParagraphStyle(
            "CersBody", parent=base["Normal"], fontSize=9, leading=12.5, **common
        ),
        "small": ParagraphStyle(
            "CersSmall", parent=base["Normal"], fontSize=7.8, leading=10,
            textColor=colors.HexColor("#444444"), **common,
        ),
        "cell": ParagraphStyle(
            "CersCell", parent=base["Normal"], fontSize=8.4, leading=11, **common
        ),
    }


def _kv_table(rows: Iterable[tuple], styles: Dict[str, ParagraphStyle]) -> Table:
    data = [
        [Paragraph(f"<b>{_safe(label)}</b>", styles["cell"]), Paragraph(_safe(value), styles["cell"])]
        for label, value in rows
    ]
    table = Table(data, colWidths=[46 * mm, 120 * mm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F4F7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _bullets(items: List[str], styles: Dict[str, ParagraphStyle], numbered: bool = False) -> List[Any]:
    flow: List[Any] = []
    if not items:
        flow.append(Paragraph("<i>None recorded.</i>", styles["small"]))
        return flow
    for index, item in enumerate(items, start=1):
        marker = f"{index}." if numbered else "-"
        flow.append(Paragraph(f"{marker} {_safe(item)}", styles["body"]))
    return flow


def _emotion_rows(emotions: Dict[str, Any]) -> List[tuple]:
    labels = (
        ("fear", "Fear"),
        ("distress", "Distress"),
        ("anger", "Anger"),
        ("nervousness", "Jitter / tremor"),
        ("neutral", "Calm / neutral"),
    )
    rows = []
    for key, label in labels:
        raw = emotions.get(key)
        if isinstance(raw, (int, float)):
            pct = round(raw * 100) if raw <= 1 else round(raw)
            rows.append((label, f"{min(100, max(0, int(pct)))}%"))
    source = emotions.get("source", "unknown")
    source_label = {
        "aws_ser": "measured (AWS SER)",
        "hf_ser": "measured (Hugging Face SER)",
        "acoustic": "ESTIMATED from prosody (no SER model reachable)",
    }.get(str(source), str(source))
    rows.append(("Source", source_label))
    if isinstance(emotions.get("vocal_distress_score"), (int, float)):
        score = emotions["vocal_distress_score"]
        pct = round(score * 100) if score <= 1 else round(score)
        rows.append(("Acoustic distress index", f"{min(100, max(0, int(pct)))}%"))
    return rows


def build_case_packet(recording: Dict[str, Any]) -> bytes:
    """Return a PDF byte string for one archived recording."""
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="CERS Forensic Case Packet",
        author="CERS Dispatch Gateway",
    )

    forensics = recording.get("forensic_analysis") or {}
    triage = recording.get("triage") or {}
    verdict = forensics.get("triage_verdict") or {}
    caller = forensics.get("caller_dossier") or {}
    suspect = forensics.get("suspect_profile") or {}
    finance = forensics.get("financial_impact") or {}
    vector = forensics.get("attack_vector") or {}
    psych = forensics.get("psychological_evaluation") or {}
    emotions = forensics.get("emotions") or triage.get("emotions") or {}

    story: List[Any] = [
        Paragraph("CERS FORENSIC CASE PACKET", styles["title"]),
        Paragraph(
            f"Cyber Emergency Response System &middot; generated from archived session "
            f"<b>{_safe(recording.get('id', recording.get('meta_name', '')))}</b>",
            styles["subtitle"],
        ),
        Spacer(1, 6),
    ]

    story.append(Paragraph("1. Session record", styles["h2"]))
    story.append(
        _kv_table(
            [
                ("Call reference", recording.get("call_id") or "not retained"),
                ("Helpline / gateway", recording.get("dialed_number") or "-"),
                ("Caller number", recording.get("caller_number") or "not captured"),
                ("Handling officer", recording.get("officer_id") or "unassigned"),
                ("Received", _fmt_time(recording.get("timestamp"))),
                ("Duration", f"{recording.get('duration') or 0} s"),
                ("Triage priority", f"{verdict.get('priority_score', triage.get('priority_score', '-'))}/100"),
                ("Incident category", verdict.get("category") or triage.get("incident_category") or "-"),
                ("Disposition", recording.get("disposition") or "not recorded"),
            ],
            styles,
        )
    )

    if not forensics:
        story.append(Spacer(1, 6))
        story.append(
            Paragraph(
                "No AI forensic audit has been run for this recording. "
                "Run the audit in the officer console before exporting a packet.",
                styles["small"],
            )
        )

    if caller or suspect or finance or vector:
        story.append(Paragraph("2. Victim / caller dossier", styles["h2"]))
        story.append(
            _kv_table(
                [
                    ("Full name", caller.get("name", "Not Disclosed")),
                    ("Contact", caller.get("contact", "Not Disclosed")),
                    ("Location", caller.get("location", "Not Disclosed")),
                    ("Language", caller.get("language_spoken", "Not detected")),
                ],
                styles,
            )
        )
        story.append(Paragraph("3. Suspect &amp; impersonation traces", styles["h2"]))
        story.append(
            _kv_table(
                [
                    ("Claimed identity", suspect.get("claimed_identity", "Unknown")),
                    ("Impersonated agency", suspect.get("impersonated_agency", "Unknown")),
                    ("Phone numbers", _join(suspect.get("phone_numbers"))),
                    ("Digital handles", _join(suspect.get("digital_handles"))),
                ],
                styles,
            )
        )
        story.append(Paragraph("4. Financial impact", styles["h2"]))
        story.append(
            _kv_table(
                [
                    ("Amount lost", finance.get("amount_lost", "-")),
                    ("Amount demanded", finance.get("amount_demanded", "-")),
                    ("Destination UPI VPAs", _join(finance.get("destination_upi_ids"))),
                    ("Destination accounts", _join(finance.get("destination_bank_accounts"))),
                    ("Victim bank", finance.get("victim_bank", "Unknown")),
                ],
                styles,
            )
        )
        story.append(Paragraph("5. Attack vector", styles["h2"]))
        story.append(
            _kv_table(
                [
                    ("Scam classification", vector.get("scam_type", "-")),
                    ("Coercion technique", vector.get("coercion_technique", "-")),
                    ("Malicious tools / APKs", _join(vector.get("malicious_tools"))),
                ],
                styles,
            )
        )

    if emotions:
        story.append(Paragraph("6. Emotion spectrum", styles["h2"]))
        story.append(_kv_table(_emotion_rows(emotions), styles))
        if psych:
            story.append(Paragraph("7. Psychological assessment", styles["h2"]))
            story.append(
                _kv_table(
                    [
                        ("Dominant state", psych.get("dominant_state", "Undetermined")),
                        ("Vulnerability", psych.get("vulnerability_level", "Unknown")),
                        ("Summary", psych.get("summary", "-")),
                    ],
                    styles,
                )
            )

    facts = forensics.get("key_facts_timeline") or []
    if facts:
        story.append(Paragraph("8. Key facts / chronology", styles["h2"]))
        story.extend(_bullets(list(facts), styles))

    sops = forensics.get("actionable_investigation_sop") or []
    if sops:
        story.append(Paragraph("9. Actionable investigation SOP", styles["h2"]))
        story.extend(_bullets(list(sops), styles, numbered=True))

    transcript = triage.get("transcript") or recording.get("notes") or ""
    if transcript:
        story.append(Paragraph("10. Caller statement (automated transcript)", styles["h2"]))
        story.append(Paragraph(_safe(transcript[:6000]), styles["body"]))

    notes = recording.get("notes") or ""
    if notes:
        story.append(Paragraph("11. Officer dispatch log", styles["h2"]))
        story.append(Paragraph(_safe(notes[:6000]), styles["body"]))

    story.append(Paragraph("12. Evidence integrity", styles["h2"]))
    story.append(_evidence_table(recording, styles))

    story.append(Spacer(1, 8))
    story.append(
        Paragraph(
            "<b>Responsible AI Advisory.</b> Emotion measurements and AI-extracted "
            "entities are indicative investigative signals for triage assistance only. "
            "They are not admissible proof of legal guilt, intent, or culpability, and "
            "must be corroborated by conventional investigation. Where the emotion "
            "source is marked ESTIMATED, no speech-emotion model was reachable and the "
            "values are a prosody heuristic.",
            styles["small"],
        )
    )

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def _join(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value if str(item).strip()]
        return ", ".join(items) if items else "None logged"
    return str(value) if value else "None logged"


def _fmt_time(timestamp: Any) -> str:
    try:
        import datetime

        return datetime.datetime.fromtimestamp(float(timestamp)).strftime("%d %b %Y, %H:%M:%S")
    except Exception:
        return "unknown"


def _evidence_table(recording: Dict[str, Any], styles: Dict[str, ParagraphStyle]) -> Table:
    hashes = recording.get("sha256") or {}
    tracks = recording.get("tracks") or {}
    if not tracks and recording.get("filename"):
        tracks = {"mixed": recording["filename"]}
    rows = [["Track", "File", "SHA-256"]]
    for name, filename in tracks.items():
        digest = hashes.get(name) if isinstance(hashes, dict) else None
        rows.append([name, str(filename), str(digest or "not recorded")])
    if len(rows) == 1:
        rows.append(["mixed", recording.get("filename", "unknown"), "not recorded"])

    chain = recording.get("chain") or {}
    rows.append(["chain", "previous record hash", str(chain.get("prev_hash") or "genesis")])
    rows.append(["chain", "this record hash", str(chain.get("hash") or "not recorded")])

    table = Table(rows, colWidths=[18 * mm, 62 * mm, 86 * mm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF3")),
                ("FONTNAME", (0, 0), (-1, 0), BOLD_FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 7.2),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _footer(canvas, doc) -> None:  # pragma: no cover - drawing only
    canvas.saveState()
    canvas.setFont(BODY_FONT, 7)
    canvas.setFillColor(colors.HexColor("#777777"))
    canvas.drawString(
        18 * mm,
        10 * mm,
        "CERS - confidential; contains personal data. Handle per station evidence policy.",
    )
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()
