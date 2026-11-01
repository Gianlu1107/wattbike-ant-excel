"""Scrittura Excel / CSV dei pacchetti ANT+ grezzi e foglio Analisi Sportiva."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .analysis import MultiPeakAnalysis, SinglePeakAnalysis, analyze_session, deduplicate_session_rows

COLUMNS: Sequence[str] = (
    "timestamp_iso",
    "elapsed_s",
    "source",
    "device_id",
    "device_type",
    "device_type_name",
    "page",
    "page_name",
    "event_count",
    "instantaneous_power_w",
    "average_power_w",
    "cadence_rpm",
    "left_power_w",
    "right_power_w",
    "torque_nm",
    "angular_velocity_rad_s",
    "raw_bytes_hex",
)


def _row_values(row: dict[str, Any]) -> list[Any]:
    return [row.get(col) for col in COLUMNS]


def _build_analysis_sheet(
    wb: Workbook,
    rows: list[dict[str, Any]],
    mode: str = "single",
) -> None:
    """Crea e popola il foglio 'analisi' con i risultati sportivi dei picchi."""
    analysis = analyze_session(rows, mode="multi" if mode == "multi" else "single")
    ws = wb.create_sheet("analisi", 0)  # primo foglio visibile

    title_font = Font(name="Segoe UI", size=14, bold=True, color="1F497D")
    sec_font = Font(name="Segoe UI", size=11, bold=True, color="000000")
    tbl_hdr_font = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
    tbl_hdr_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
    tbl_hdr2_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
    bold_font = Font(name="Segoe UI", size=10, bold=True)
    regular_font = Font(name="Segoe UI", size=10)
    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")
    right_align = Alignment(horizontal="right", vertical="center")

    if isinstance(analysis, SinglePeakAnalysis):
        ws.append(["REPORT ANALISI SPRINT — PICCO SINGOLO (TTP)"])
        ws["A1"].font = title_font
        ws.append([])

        pk = analysis.peak
        headers = ["Parametro", "Valore", "Unità", "Descrizione"]
        ws.append(headers)
        row_idx = ws.max_row
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.font = tbl_hdr_font
            cell.fill = tbl_hdr_fill
            cell.alignment = center_align

        metrics_data = [
            ("Potenza di Picco (P_peak)", pk.power_peak if pk else 0, "W", "Massima potenza istantanea"),
            ("Tempo al Picco (tx)", pk.t_peak if pk else 0.0, "s", "Istante temporale del picco"),
            ("Cadenza al Picco", pk.cadence_at_peak if pk else "—", "rpm", "Cadenza corrispondente all'istante tx"),
            ("Picco Cadenza Massima", pk.cadence_max if pk else "—", "rpm", "Massima cadenza registrata"),
            ("Inizio Spinta (t0)", pk.t0 if pk else 0.0, "s", "Istante inizio applicazione da riposo"),
            ("Tempo di Applicazione (t_app)", pk.t_app if pk else 0.0, "s", "Intervallo tx - t0 (Time to Peak)"),
            ("Coppia al Picco", pk.torque_at_peak if pk else "—", "Nm", "Coppia calcolata al picco (P / ω)"),
            ("Coppia Massima", pk.torque_max if pk else "—", "Nm", "Picco massimo di coppia dello sprint"),
            ("Potenza Media Sprint", pk.power_avg if pk else "—", "W", "Potenza media durante la spinta"),
            ("Durata Applicazione", pk.duration if pk else 0.0, "s", "Durata dello sforzo"),
        ]

        for p_name, val, unit, desc in metrics_data:
            ws.append([p_name, val, unit, desc])
            r = ws.max_row
            ws.cell(row=r, column=1).font = bold_font
            ws.cell(row=r, column=2).font = bold_font
            ws.cell(row=r, column=2).alignment = right_align
            ws.cell(row=r, column=3).alignment = center_align
            ws.cell(row=r, column=4).font = regular_font

    elif isinstance(analysis, MultiPeakAnalysis):
        ws.append(["REPORT ANALISI REPEATED SPRINTS — PICCHI MULTIPLI & DECADENZA"])
        ws["A1"].font = title_font
        ws.append([])

        # Statistiche di riepilogo
        ws.append(["RIEPILOGO SESSIONE MULTI-PICCO"])
        ws[f"A{ws.max_row}"].font = sec_font

        summary_rows = [
            ("Picchi totali rilevati (separati da zero):", analysis.total_peaks),
            ("Potenza Picco Massima Assoluta (P_peak_max):", f"{analysis.max_power_peak} W"),
            ("Cadenza Massima Assoluta:", f"{analysis.max_cadence_peak} rpm"),
            ("Miglior Tempo di Applicazione (min t_app):", f"{analysis.min_app_time:.3f} s"),
            ("Decadenza Media Potenza:", f"{analysis.average_decay_power_pct:.1f} %"),
            ("Decadenza Media Cadenza:", f"{analysis.average_decay_cad_pct:.1f} %"),
        ]
        for label, val in summary_rows:
            ws.append([label, val])
            r = ws.max_row
            ws.cell(row=r, column=1).font = bold_font
            ws.cell(row=r, column=2).font = regular_font

        ws.append([])
        ws.append(["1. SEQUENZA CRONOLOGICA PICCHI (ORDINE GRAFICO)"])
        ws[f"A{ws.max_row}"].font = sec_font

        tbl1_cols = [
            "Sprint #",
            "t0 (s)",
            "tx Picco (s)",
            "t_app (s)",
            "Potenza Picco (W)",
            "Cadenza @ Picco (rpm)",
            "Cadenza Max (rpm)",
            "Coppia @ Picco (Nm)",
            "Decadenza Potenza (W)",
            "Decadenza Potenza (%)",
            "Decadenza Cadenza (rpm)",
            "Decadenza Cadenza (%)",
        ]
        ws.append(tbl1_cols)
        h_row = ws.max_row
        for col_idx in range(1, len(tbl1_cols) + 1):
            cell = ws.cell(row=h_row, column=col_idx)
            cell.font = tbl_hdr_font
            cell.fill = tbl_hdr_fill
            cell.alignment = center_align

        for pk in analysis.peaks:
            dec_p_w = "— (Base)" if pk.index == 1 else pk.decay_power_w
            dec_p_pct = "—" if pk.index == 1 else f"{pk.decay_power_pct:.1f}%"
            dec_c_rpm = "—" if pk.index == 1 else pk.decay_cad_rpm
            dec_c_pct = "—" if pk.index == 1 else f"{pk.decay_cad_pct:.1f}%"
            ws.append([
                f"Picco {pk.index}",
                pk.t0,
                pk.t_peak,
                pk.t_app,
                pk.power_peak,
                pk.cadence_at_peak or "—",
                pk.cadence_max or "—",
                pk.torque_at_peak if pk.torque_at_peak is not None else "—",
                dec_p_w,
                dec_p_pct,
                dec_c_rpm,
                dec_c_pct,
            ])
            r = ws.max_row
            for c in range(1, len(tbl1_cols) + 1):
                ws.cell(row=r, column=c).alignment = center_align

        ws.append([])
        ws.append(["2. CLASSIFICA DECRESCENTE POTENZA (RANKING DECADENZA)"])
        ws[f"A{ws.max_row}"].font = sec_font

        tbl2_cols = [
            "Rank",
            "Sprint Orig.",
            "Potenza Picco (W)",
            "Decadenza vs Sprint #1 (W)",
            "Decadenza (%)",
            "Cadenza @ Picco (rpm)",
            "Tempo Appl. (s)",
        ]
        ws.append(tbl2_cols)
        h_row2 = ws.max_row
        for col_idx in range(1, len(tbl2_cols) + 1):
            cell = ws.cell(row=h_row2, column=col_idx)
            cell.font = tbl_hdr_font
            cell.fill = tbl_hdr2_fill
            cell.alignment = center_align

        for rank, pk in enumerate(analysis.peaks_by_power, 1):
            ws.append([
                rank,
                f"Picco {pk.index}",
                pk.power_peak,
                pk.decay_power_w,
                f"{pk.decay_power_pct:.1f}%",
                pk.cadence_at_peak or "—",
                pk.t_app,
            ])
            r = ws.max_row
            for c in range(1, len(tbl2_cols) + 1):
                ws.cell(row=r, column=c).alignment = center_align

        ws.append([])
        ws.append(["3. CLASSIFICA DECRESCENTE CADENZA"])
        ws[f"A{ws.max_row}"].font = sec_font

        tbl3_cols = [
            "Rank",
            "Sprint Orig.",
            "Cadenza Max (rpm)",
            "Decadenza vs Max (rpm)",
            "Decadenza Cad (%)",
            "Potenza Picco (W)",
            "Tempo Appl. (s)",
        ]
        ws.append(tbl3_cols)
        h_row3 = ws.max_row
        for col_idx in range(1, len(tbl3_cols) + 1):
            cell = ws.cell(row=h_row3, column=col_idx)
            cell.font = tbl_hdr_font
            cell.fill = tbl_hdr2_fill
            cell.alignment = center_align

        for rank, pk in enumerate(analysis.peaks_by_cadence, 1):
            ws.append([
                rank,
                f"Picco {pk.index}",
                pk.cadence_max or "—",
                pk.decay_cad_rpm,
                f"{pk.decay_cad_pct:.1f}%",
                pk.power_peak,
                pk.t_app,
            ])
            r = ws.max_row
            for c in range(1, len(tbl3_cols) + 1):
                ws.cell(row=r, column=c).alignment = center_align

    # Auto-adjust column widths
    for col in ws.columns:
        letter = col[0].column_letter
        max_len = 0
        for cell in col:
            val_str = str(cell.value or "")
            if len(val_str) > max_len and len(val_str) < 60:
                max_len = len(val_str)
        ws.column_dimensions[letter].width = max(max_len + 3, 14)


def write_xlsx(
    path: Path,
    rows: Iterable[dict[str, Any]],
    meta: dict[str, Any] | None = None,
    mode: str = "single",
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Assicura deduplicazione e calcolo torque per l'export
    clean_rows = deduplicate_session_rows(rows)

    wb = Workbook()
    if wb.active is not None:
        wb.remove(wb.active)

    # Foglio 1: Analisi Sportiva (Picco Singolo o Multiplo)
    _build_analysis_sheet(wb, clean_rows, mode=mode)

    # Foglio 2: Raw data
    ws = wb.create_sheet("raw")
    header_font = Font(name="Segoe UI", size=10, bold=True)
    ws.append(list(COLUMNS))
    for cell in ws[1]:
        cell.font = header_font

    count = 0
    for row in clean_rows:
        ws.append(_row_values(row))
        count += 1

    for col in ws.columns:
        letter = col[0].column_letter
        width = max(len(str(c.value)) if c.value is not None else 0 for c in col[:50])
        ws.column_dimensions[letter].width = min(max(width + 2, 12), 42)

    # Foglio 3: Metadata
    meta_ws = wb.create_sheet("meta")
    meta_ws.append(["chiave", "valore"])
    meta_ws["A1"].font = header_font
    meta_ws["B1"].font = header_font
    payload = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "rows": count,
        "mode_selected": mode,
        "note": "Dati ANT+ e/o Bluetooth LE (CPS) con watt, coppia e analisi picchi.",
        **(meta or {}),
    }
    for key, value in payload.items():
        meta_ws.append([key, value])

    wb.save(path)
    return path


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> Path:
    import csv

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean_rows = deduplicate_session_rows(rows)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNS), extrasaction="ignore")
        writer.writeheader()
        for row in clean_rows:
            writer.writerow({col: row.get(col) for col in COLUMNS})
    return path
