import os
import sys
import time
import subprocess
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
)
from core.database import (
    _get_single_alloc, _get_active_allocs, _get_kpis, 
    _get_all_scrapped, _get_all_vault, _fmt_mad
)

_PDF_NAV  = colors.HexColor("#003366")
_PDF_SLT  = colors.HexColor("#E8EFF8")
_PDF_GRN  = colors.HexColor("#005500")
_PDF_RED  = colors.HexColor("#AA0000")
_PDF_ORA  = colors.HexColor("#995500")
_PDF_GRY  = colors.HexColor("#F4F4F4")
_PDF_MID  = colors.HexColor("#888888")

def _resolve_reports_dir() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "rapports_ppe")
    os.makedirs(d, exist_ok=True)
    return d

def _open_pdf(path: str) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.call(["open", path])
        else:
            subprocess.call(["xdg-open", path])
    except Exception:
        pass

def _pdf_styles() -> dict:
    base = getSampleStyleSheet()
    def P(name, **kw):
        return ParagraphStyle(name, parent=base["Normal"], **kw)
    return {
        "title":    P("ppv_title",  fontName="Helvetica-Bold",  fontSize=16, textColor=_PDF_NAV, spaceAfter=4),
        "subtitle": P("ppv_sub",    fontName="Helvetica",        fontSize=9, textColor=colors.HexColor("#555555"), spaceAfter=2),
        "hdr":      P("ppv_hdr",    fontName="Helvetica-Bold",   fontSize=10, textColor=_PDF_NAV, spaceBefore=10, spaceAfter=4),
        "body":     P("ppv_body",   fontName="Helvetica",        fontSize=8, textColor=colors.black),
        "small":    P("ppv_small",  fontName="Helvetica",        fontSize=7, textColor=_PDF_MID),
        "th":       P("ppv_th",     fontName="Helvetica-Bold",   fontSize=8, textColor=colors.white),
        "td":       P("ppv_td",     fontName="Helvetica",        fontSize=8, textColor=colors.black),
        "td_grn":   P("ppv_tdg",    fontName="Helvetica-Bold",   fontSize=8, textColor=_PDF_GRN),
        "td_red":   P("ppv_tdr",    fontName="Helvetica-Bold",   fontSize=8, textColor=_PDF_RED),
        "td_ora":   P("ppv_tdo",    fontName="Helvetica-Bold",   fontSize=8, textColor=_PDF_ORA),
        "mono":     P("ppv_mono",   fontName="Courier",           fontSize=7, textColor=_PDF_MID),
    }

def _common_header_footer(canvas_obj, doc) -> None:
    canvas_obj.saveState()
    W, H = A4
    canvas_obj.setFillColor(_PDF_NAV)
    canvas_obj.rect(0, H - 28*mm, W, 28*mm, fill=1, stroke=0)
    canvas_obj.setFillColor(colors.white)
    canvas_obj.setFont("Helvetica-Bold", 11)
    canvas_obj.drawString(15*mm, H - 13*mm, "EPI MANAGER — ISOFU")
    canvas_obj.setFont("Helvetica", 8)
    canvas_obj.drawString(15*mm, H - 21*mm, "Architecture : Roger Fernando  \xb7  Loi 65-99 / ISO 9001  \xb7  Maroc")
    ts = time.strftime("Genere le : %d/%m/%Y  %H:%M:%S")
    canvas_obj.drawRightString(W - 15*mm, H - 13*mm, ts)
    canvas_obj.drawRightString(W - 15*mm, H - 21*mm, f"Page {doc.page}")
    canvas_obj.setFillColor(_PDF_GRY)
    canvas_obj.rect(0, 0, W, 12*mm, fill=1, stroke=0)
    canvas_obj.setFillColor(_PDF_MID)
    canvas_obj.setFont("Helvetica", 6.5)
    canvas_obj.drawString(15*mm, 4*mm, "Document confidentiel  \u2014  Usage interne uniquement  \u2014  Toute reproduction non autorisee est interdite.")
    canvas_obj.restoreState()

def pdf_bon_allocation(tx_id: int) -> tuple[bool, str]:
    row = _get_single_alloc(tx_id)
    if row is None: return False, f"Transaction TX-{tx_id:06d} introuvable."
    rdir = _resolve_reports_dir()
    fname = f"BON_ALLOC_TX{tx_id:06d}_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
    path = os.path.join(rdir, fname)

    from reportlab.pdfgen import canvas as rl_canvas
    c = rl_canvas.Canvas(path, pagesize=A4)
    W, H = A4

    class _FakeDoc:
        page = 1
    _common_header_footer(c, _FakeDoc())

    y = H - 38*mm
    c.setFillColor(_PDF_NAV)
    c.setFont("Helvetica-Bold", 26)
    c.drawString(15*mm, y, f"TX-{tx_id:06d}")
    c.setFont("Helvetica", 9)
    c.setFillColor(_PDF_MID)
    c.drawString(15*mm, y - 7*mm, "BON D'ATTRIBUTION EPI  \u2014  Loi 65-99 Art. 24")
    y -= 18*mm
    c.setStrokeColor(_PDF_NAV)
    c.setLineWidth(1.2)
    c.line(15*mm, y, W - 15*mm, y)
    y -= 9*mm

    c.setFillColor(_PDF_NAV)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(15*mm, y, "AGENT")
    y -= 5*mm
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(15*mm, y, row["full_name"])
    c.setFont("Helvetica", 9)
    c.drawString(85*mm, y, f"ID : {row['agent_id']}")
    c.drawRightString(W - 15*mm, y, f"CIN : {row['cin']}")
    y -= 6*mm
    c.setFont("Helvetica", 9)
    c.setFillColor(_PDF_MID)
    c.drawString(15*mm, y, row["job_class"])
    y -= 13*mm

    c.setFillColor(_PDF_NAV)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(15*mm, y, "EQUIPEMENT DE PROTECTION INDIVIDUELLE")
    y -= 5*mm
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(15*mm, y, row["ppe_desc"])
    y -= 6*mm
    c.setFont("Helvetica", 9)
    c.setFillColor(_PDF_MID)
    c.drawString(15*mm, y, f"[{row['category']}]    Code : {row['ppe_code']}    LOT : {row['lot_number']}")
    y -= 13*mm

    emis_s  = time.strftime("%d/%m/%Y  %H:%M", time.localtime(row["timestamp_issued"]))
    death_s = time.strftime("%d/%m/%Y", time.localtime(row["expected_death_timestamp"]))
    S = _pdf_styles()
    data = [
        ["DATE D'EMISSION", "DATE D'EXPIRATION", "DUREE DE VIE", "VALEUR UNITAIRE"],
        [emis_s, death_s, f"{row['lifespan_days']} jour(s)", _fmt_mad(row["unit_cost_centimes"])],
    ]
    tbl = Table(data, colWidths=[52*mm, 50*mm, 36*mm, 40*mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), _PDF_NAV),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 8),
        ("FONTNAME",      (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 1), (-1, 1), 10),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [_PDF_SLT]),
        ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    tbl.wrapOn(c, W - 30*mm, 40*mm)
    tbl.drawOn(c, 15*mm, y - 24*mm)
    y -= 38*mm

    c.setFillColor(_PDF_NAV)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(15*mm, y, "SITE / CHANTIER D'AFFECTATION")
    y -= 5*mm
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(15*mm, y, row["chantier_location"])
    y -= 14*mm

    c.setStrokeColor(colors.HexColor("#DDDDDD"))
    c.setLineWidth(0.5)
    c.line(15*mm, y, W - 15*mm, y)
    y -= 6*mm
    c.setFillColor(_PDF_MID)
    c.setFont("Courier", 6.5)
    c.drawString(15*mm, y, f"HMAC-SHA256 : {row['crypto_signature']}")
    y -= 18*mm

    for x_off, label in [(15*mm, "Signature Agent"), (105*mm, "Visa Responsable HSE")]:
        c.setStrokeColor(colors.black)
        c.setLineWidth(0.8)
        c.line(x_off, y - 18*mm, x_off + 75*mm, y - 18*mm)
        c.setFont("Helvetica", 8)
        c.setFillColor(_PDF_MID)
        c.drawString(x_off, y - 23*mm, label)

    c.setFillColor(colors.HexColor("#CC0000"))
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(W / 2, 18*mm, "Ce bon est un document reglementaire. Conservation obligatoire 5 ans (Loi 65-99 / ISO 9001 Clause 7.5.3).")
    c.save()
    return True, path

def pdf_etat_allocations() -> tuple[bool, str]:
    allocs = _get_active_allocs()
    kpis   = _get_kpis()
    if not allocs: return False, "Aucune allocation active a exporter."
    rdir = _resolve_reports_dir()
    fname = f"ETAT_ALLOC_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
    path = os.path.join(rdir, fname)
    S = _pdf_styles()
    now = int(time.time())
    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=34*mm, bottomMargin=18*mm, leftMargin=15*mm, rightMargin=15*mm)
    story = []
    story.append(Paragraph("ETAT DES ALLOCATIONS EPI", S["title"]))
    story.append(Paragraph(f"Genere le {time.strftime('%d/%m/%Y a %H:%M:%S')}  \u2014  {len(allocs)} enregistrement(s)", S["subtitle"]))
    story.append(Spacer(1, 4*mm))

    kpi_data = [
        ["VALEUR TOTALE STOCK", "ALLOCATIONS ACTIVES", "VIOLATIONS ISO", "STOCK CRITIQUE"],
        [_fmt_mad(kpis["vault_val_centimes"]), str(kpis["active_count"]), str(kpis["expired_count"]), str(kpis["low_stock_count"])],
    ]
    kt = Table(kpi_data, colWidths=[50*mm, 42*mm, 38*mm, 38*mm])
    kt.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), _PDF_NAV),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 7.5),
        ("FONTNAME",      (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 1), (-1, 1), 10),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [_PDF_SLT]),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#AAAAAA")),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(kt)
    story.append(Spacer(1, 5*mm))
    story.append(HRFlowable(width="100%", thickness=1, color=_PDF_NAV))
    story.append(Spacer(1, 4*mm))

    cols_w = [18*mm, 40*mm, 52*mm, 22*mm, 22*mm, 20*mm]
    header = [Paragraph(f"<b>{h}</b>", S["th"]) for h in ["TX-ID", "AGENT", "EPI", "EXPIRE", "JOURS REST.", "STATUT"]]
    rows = [header]
    style_cmds = [
        ("BACKGROUND",   (0, 0), (-1, 0), _PDF_NAV),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",         (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("LEFTPADDING",  (1, 0), (2, -1), 3),
    ]
    for i, row in enumerate(allocs, 1):
        days = (row["expected_death_timestamp"] - now) // 86400
        days_s = str(days) if days > 0 else "EXPIRE"
        exp_s = time.strftime("%d/%m/%Y", time.localtime(row["expected_death_timestamp"]))
        st = row["status"]
        st_s = S["td_red"] if st == "Expired" else (S["td_ora"] if st == "Degraded" else S["td_grn"])
        rows.append([
            Paragraph(f"TX-{row['tx_id']:06d}", S["td"]),
            Paragraph(row["agent_name"][:26], S["td"]),
            Paragraph(row["ppe_desc"][:40], S["td"]),
            Paragraph(exp_s, S["td"]),
            Paragraph(days_s, st_s if days <= 0 else S["td"]),
            Paragraph(st.upper(), st_s),
        ])
        style_cmds.append(("BACKGROUND", (0, i), (-1, i), _PDF_GRY if i % 2 == 0 else colors.white))
    tbl = Table(rows, colWidths=cols_w, repeatRows=1)
    tbl.setStyle(TableStyle(style_cmds))
    story.append(tbl)
    doc.build(story, onFirstPage=_common_header_footer, onLaterPages=_common_header_footer)
    return True, path

def pdf_journal_nc() -> tuple[bool, str]:
    rows = _get_all_scrapped()
    if not rows: return False, "Aucune non-conformite enregistree."
    rdir = _resolve_reports_dir()
    fname = f"JOURNAL_NC_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
    path = os.path.join(rdir, fname)
    S = _pdf_styles()
    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=34*mm, bottomMargin=18*mm, leftMargin=15*mm, rightMargin=15*mm)
    story = []
    story.append(Paragraph("JOURNAL DES NON-CONFORMITES EPI", S["title"]))
    story.append(Paragraph(f"ISO 9001:2015 Clause 10.2  \u2014  PDCA  \u2014  {len(rows)} enregistrement(s)  \u2014  {time.strftime('%d/%m/%Y %H:%M')}", S["subtitle"]))
    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="100%", thickness=1.2, color=_PDF_RED))
    story.append(Spacer(1, 4*mm))

    cols_w = [18*mm, 38*mm, 48*mm, 22*mm, 28*mm, 30*mm]
    header = [Paragraph(f"<b>{h}</b>", S["th"]) for h in ["TX-ID", "AGENT", "EPI", "EMIS", "LOT N\xb0", "MOTIF NC"]]
    tbl_rows = [header]
    style_cmds = [
        ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#AA0000")),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",         (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("LEFTPADDING",  (1, 0), (5, -1), 3),
    ]
    for i, row in enumerate(rows, 1):
        emis_s = time.strftime("%d/%m/%Y", time.localtime(row["timestamp_issued"]))
        tbl_rows.append([
            Paragraph(f"TX-{row['tx_id']:06d}", S["td"]),
            Paragraph(row["agent_name"][:26], S["td"]),
            Paragraph(row["ppe_desc"][:36], S["td"]),
            Paragraph(emis_s, S["td"]),
            Paragraph(row["lot_number"][:14], S["td"]),
            Paragraph(str(row["iso_scrap_reason"] or "\u2014")[:28], S["td_red"]),
        ])
        style_cmds.append(("BACKGROUND", (0, i), (-1, i), _PDF_GRY if i % 2 == 0 else colors.white))
    tbl = Table(tbl_rows, colWidths=cols_w, repeatRows=1)
    tbl.setStyle(TableStyle(style_cmds))
    story.append(tbl)
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("Ce journal constitue la preuve documentaire des actions correctives conformement a l'ISO 9001:2015 Art. 10.2.2. Conservation : 5 ans.", S["small"]))
    doc.build(story, onFirstPage=_common_header_footer, onLaterPages=_common_header_footer)
    return True, path

def pdf_inventaire_vault() -> tuple[bool, str]:
    rows = _get_all_vault()
    if not rows: return False, "Vault vide."
    kpis = _get_kpis()
    rdir = _resolve_reports_dir()
    fname = f"INVENTAIRE_VAULT_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
    path = os.path.join(rdir, fname)
    S = _pdf_styles()
    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=34*mm, bottomMargin=18*mm, leftMargin=15*mm, rightMargin=15*mm)
    story = []
    story.append(Paragraph("INVENTAIRE DU VAULT EPI", S["title"]))
    story.append(Paragraph(f"Arrete au {time.strftime('%d/%m/%Y  %H:%M:%S')}  \u2014  Valeur totale : {_fmt_mad(kpis['vault_val_centimes'])}", S["subtitle"]))
    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="100%", thickness=1, color=_PDF_NAV))
    story.append(Spacer(1, 4*mm))

    from collections import defaultdict
    groups: dict = defaultdict(list)
    for r in rows:
        groups[r["ppe_code"]].append(r)

    cols_w = [22*mm, 80*mm, 30*mm, 16*mm, 16*mm, 20*mm]
    header = [Paragraph(f"<b>{h}</b>", S["th"]) for h in ["CODE", "DESCRIPTION / LOT", "LOT N\xb0", "QTY", "SEUIL", "ETAT"]]
    all_rows = [header]
    style_cmds = [
        ("BACKGROUND",   (0, 0), (-1, 0), _PDF_NAV),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",         (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("LEFTPADDING",  (1, 0), (1, -1), 3),
    ]
    row_idx = 1
    for code, lots in groups.items():
        all_rows.append([
            Paragraph(f"<b>{code}</b>", S["td"]),
            Paragraph(f"<b>{lots[0]['description'][:54]}</b>", S["td"]),
            Paragraph("", S["td"]),
            Paragraph(f"<b>{sum(l['qty'] for l in lots)}</b>", S["td"]),
            Paragraph("", S["td"]),
            Paragraph("", S["td"]),
        ])
        style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#D6E4F0")))
        row_idx += 1
        for lot in lots:
            qty, seuil = lot["qty"], lot["min_threshold"]
            etat, st = (("RUPTURE", S["td_red"]) if qty == 0 else ("ALERTE",  S["td_ora"]) if qty <= seuil else ("OK",      S["td_grn"]))
            all_rows.append([
                Paragraph("", S["td"]),
                Paragraph(f"   {lot['lot_number']}", S["td"]),
                Paragraph(lot["lot_number"], S["td"]),
                Paragraph(str(qty), S["td"]),
                Paragraph(str(seuil), S["td"]),
                Paragraph(etat, st),
            ])
            style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), _PDF_GRY if row_idx % 2 == 0 else colors.white))
            row_idx += 1

    tbl = Table(all_rows, colWidths=cols_w, repeatRows=1)
    tbl.setStyle(TableStyle(style_cmds))
    story.append(tbl)
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph(f"Valeur totale : {_fmt_mad(kpis['vault_val_centimes'])}  \u2014  Lots en alerte : {kpis['low_stock_count']}", S["hdr"]))
    doc.build(story, onFirstPage=_common_header_footer, onLaterPages=_common_header_footer)
    return True, path