"""Modulo di elaborazione e analisi dati sportivi Wattbike (Picco Singolo e Multiplo)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal


@dataclass
class PeakMetrics:
    index: int
    t0: float
    t_peak: float
    t_app: float  # tempo di applicazione (t_peak - t0)
    power_peak: int
    cadence_at_peak: int | None
    cadence_max: int | None
    t_cad_max: float | None = None
    torque_at_peak: float | None = None
    torque_max: float | None = None
    t_torque_max: float | None = None
    power_avg: float | None = None
    duration: float = 0.0
    # Decadenza rispetto al picco massimo della sessione
    decay_power_w: float = 0.0
    decay_power_pct: float = 0.0
    decay_cad_rpm: float = 0.0
    decay_cad_pct: float = 0.0
    decay_app_time_s: float = 0.0


@dataclass
class SinglePeakAnalysis:
    mode: str = "single"
    peak: PeakMetrics | None = None
    rows_count: int = 0
    session_duration: float = 0.0


@dataclass
class MultiPeakAnalysis:
    mode: str = "multi"
    peaks: list[PeakMetrics] = field(default_factory=list)  # ordine cronologico (grafico)
    peaks_by_power: list[PeakMetrics] = field(default_factory=list)  # decrescente per potenza
    peaks_by_cadence: list[PeakMetrics] = field(default_factory=list)  # decrescente per cadenza
    max_power_peak: int = 0
    max_cadence_peak: int = 0
    min_app_time: float = 0.0
    average_decay_power_pct: float = 0.0
    average_decay_cad_pct: float = 0.0
    total_peaks: int = 0
    rows_count: int = 0
    session_duration: float = 0.0


def _compute_torque_if_missing(row: dict[str, Any]) -> None:
    """Popola coppia e velocità angolare se mancanti ma potenza e cadenza sono presenti."""
    tq = row.get("torque_nm")
    w = row.get("angular_velocity_rad_s")
    if tq not in (None, "") and w not in (None, ""):
        return

    pwr = row.get("instantaneous_power_w")
    cad = row.get("cadence_rpm")

    try:
        pwr_val = float(pwr) if pwr is not None else None
    except (ValueError, TypeError):
        pwr_val = None

    try:
        cad_val = float(cad) if cad is not None else None
    except (ValueError, TypeError):
        cad_val = None

    if cad_val is not None:
        if cad_val > 0:
            omega = cad_val * (2.0 * math.pi / 60.0)
            row["angular_velocity_rad_s"] = round(omega, 4)
            if pwr_val is not None and omega > 0:
                row["torque_nm"] = round(pwr_val / omega, 3)
        else:
            row["angular_velocity_rad_s"] = 0.0
            if pwr_val == 0:
                row["torque_nm"] = 0.0
    elif pwr_val == 0:
        row["angular_velocity_rad_s"] = 0.0
        row["torque_nm"] = 0.0


def deduplicate_session_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Rimuove duplicati cross-source ANT+ <-> BLE e rate-limita zeri a riposo.
    Garantisce inoltre il calcolo matematico di coppia per tutte le righe.
    """
    clean: list[dict[str, Any]] = []
    last_ant_key: tuple[Any, ...] | None = None

    for r in rows:
        row = dict(r)
        _compute_torque_if_missing(row)

        src = row.get("source", "ant+")
        # ANT+ dedup su pacchetto raw identico
        if src == "ant+":
            key = (
                row.get("device_id"),
                row.get("page"),
                row.get("event_count"),
                row.get("raw_bytes_hex"),
            )
            if key == last_ant_key and key != (None, None, None, None):
                continue
            last_ant_key = key

        t_curr = row.get("elapsed_s")
        try:
            t_curr_val = float(t_curr) if t_curr is not None else None
        except (ValueError, TypeError):
            t_curr_val = None

        try:
            p_curr = int(row.get("instantaneous_power_w") or 0)
        except (ValueError, TypeError):
            p_curr = 0

        try:
            c_curr = int(row.get("cadence_rpm") or 0)
        except (ValueError, TypeError):
            c_curr = 0

        is_dup = False
        if t_curr_val is not None:
            for prev in reversed(clean[-16:]):
                t_prev = prev.get("elapsed_s")
                try:
                    t_prev_val = float(t_prev) if t_prev is not None else None
                except (ValueError, TypeError):
                    t_prev_val = None
                if t_prev_val is None:
                    continue
                dt = t_curr_val - t_prev_val
                if dt > 0.45:
                    break

                try:
                    p_prev = int(prev.get("instantaneous_power_w") or 0)
                except (ValueError, TypeError):
                    p_prev = 0

                try:
                    c_prev = int(prev.get("cadence_rpm") or 0)
                except (ValueError, TypeError):
                    c_prev = 0

                prev_src = prev.get("source", "ant+")
                if src != prev_src:
                    if p_curr > 0 and p_curr == p_prev:
                        if abs(c_curr - c_prev) <= 2 or c_curr == 0 or c_prev == 0:
                            # Arricchisci campi mancanti nel record precedente
                            if prev.get("torque_nm") is None and row.get("torque_nm") is not None:
                                prev["torque_nm"] = row["torque_nm"]
                                prev["angular_velocity_rad_s"] = row.get("angular_velocity_rad_s")
                            if (prev.get("left_power_w") is None or prev.get("right_power_w") is None) and (
                                row.get("left_power_w") is not None and row.get("right_power_w") is not None
                            ):
                                prev["left_power_w"] = row["left_power_w"]
                                prev["right_power_w"] = row["right_power_w"]
                            is_dup = True
                            break
                    if p_curr == 0 and p_prev == 0 and dt < 0.22:
                        is_dup = True
                        break
                else:
                    if p_curr == 0 and p_prev == 0 and dt < 0.22:
                        is_dup = True
                        break

        if not is_dup:
            clean.append(row)

    return clean


def _extract_metric_int(val: Any) -> int | None:
    try:
        return int(val) if val is not None and str(val).strip() != "" else None
    except (ValueError, TypeError):
        return None


def _extract_metric_float(val: Any) -> float | None:
    try:
        return float(val) if val is not None and str(val).strip() != "" else None
    except (ValueError, TypeError):
        return None


def _analyze_single_sprint_segment(segment: list[dict[str, Any]], index: int = 1) -> PeakMetrics | None:
    if not segment:
        return None

    # Trova il punto di picco di potenza
    max_p = -1
    peak_row: dict[str, Any] | None = None
    peak_idx = -1

    for i, r in enumerate(segment):
        p = _extract_metric_int(r.get("instantaneous_power_w")) or 0
        if p > max_p:
            max_p = p
            peak_row = r
            peak_idx = i

    if peak_row is None or max_p <= 0:
        return None

    t_peak = _extract_metric_float(peak_row.get("elapsed_s")) or 0.0

    # Determina t0: scansiona all'indietro dal picco per trovare l'inizio della spinta (da zero / riposo)
    t0 = _extract_metric_float(segment[0].get("elapsed_s")) or 0.0
    for j in range(peak_idx - 1, -1, -1):
        p_j = _extract_metric_int(segment[j].get("instantaneous_power_w")) or 0
        if p_j <= 10:  # soglia riposo / zero
            # Il punto successivo è l'inizio dell'applicazione
            t0_candidate = _extract_metric_float(segment[j + 1].get("elapsed_s"))
            if t0_candidate is not None:
                t0 = t0_candidate
            break

    t_app = max(0.0, round(t_peak - t0, 3))

    # Cadenza al picco di potenza (all'istante tx)
    cad_at_peak = _extract_metric_int(peak_row.get("cadence_rpm"))

    # Picco di cadenza massima dello sprint
    max_cad = -1
    t_cad_max: float | None = None
    for r in segment:
        c = _extract_metric_int(r.get("cadence_rpm"))
        if c is not None and c > max_cad:
            max_cad = c
            t_cad_max = _extract_metric_float(r.get("elapsed_s"))

    cad_max_val = max_cad if max_cad > 0 else cad_at_peak

    # Torque al picco di potenza e massimo
    torque_at_peak = _extract_metric_float(peak_row.get("torque_nm"))
    max_torque = -1.0
    t_torque_max: float | None = None
    powers: list[int] = []

    for r in segment:
        p = _extract_metric_int(r.get("instantaneous_power_w"))
        if p is not None:
            powers.append(p)
        tq = _extract_metric_float(r.get("torque_nm"))
        if tq is not None and tq > max_torque:
            max_torque = tq
            t_torque_max = _extract_metric_float(r.get("elapsed_s"))

    t_end = _extract_metric_float(segment[-1].get("elapsed_s")) or t_peak
    duration = max(0.0, round(t_end - t0, 3))
    p_avg = round(sum(powers) / len(powers), 1) if powers else float(max_p)

    return PeakMetrics(
        index=index,
        t0=round(t0, 3),
        t_peak=round(t_peak, 3),
        t_app=t_app,
        power_peak=max_p,
        cadence_at_peak=cad_at_peak,
        cadence_max=cad_max_val,
        t_cad_max=round(t_cad_max, 3) if t_cad_max is not None else None,
        torque_at_peak=torque_at_peak,
        torque_max=round(max_torque, 3) if max_torque >= 0 else torque_at_peak,
        t_torque_max=round(t_torque_max, 3) if t_torque_max is not None else None,
        power_avg=p_avg,
        duration=duration,
    )


def analyze_single_peak(rows: list[dict[str, Any]]) -> SinglePeakAnalysis:
    """Analisi modalità Picco Singolo (Time to Peak, cadenza al picco, cadenza max)."""
    clean = deduplicate_session_rows(rows)
    if not clean:
        return SinglePeakAnalysis(peak=None, rows_count=0, session_duration=0.0)

    # Identifica il picco assoluto dell'intera sessione
    peak = _analyze_single_sprint_segment(clean, index=1)
    t_start = _extract_metric_float(clean[0].get("elapsed_s")) or 0.0
    t_end = _extract_metric_float(clean[-1].get("elapsed_s")) or 0.0
    duration = max(0.0, round(t_end - t_start, 3))

    return SinglePeakAnalysis(
        peak=peak,
        rows_count=len(clean),
        session_duration=duration,
    )


def analyze_multi_peak(rows: list[dict[str, Any]]) -> MultiPeakAnalysis:
    """
    Analisi modalità Picco Multiplo:
    - Riconosce i diversi picchi separati da una fila di zeri (recupero)
    - Per ogni picco calcola power peak, cadenza corrispondente, cadenza max, tempo di applicazione
    - Calcola la decadenza rispetto al picco massimo (sia cronologica che ordinata decrescentemente)
    """
    clean = deduplicate_session_rows(rows)
    if not clean:
        return MultiPeakAnalysis()

    # Segmentazione dei picchi tramite la fila di zeri
    segments: list[list[dict[str, Any]]] = []
    cur_seg: list[dict[str, Any]] = []
    consecutive_zeros = 0
    t_last: float | None = None

    for r in clean:
        p = _extract_metric_int(r.get("instantaneous_power_w")) or 0
        t_cur = _extract_metric_float(r.get("elapsed_s"))
        dt_gap = (t_cur - t_last) if (t_cur is not None and t_last is not None) else 0.0
        t_last = t_cur

        is_rest = p <= 10  # zero o quasi-zero (riposo)
        if is_rest:
            consecutive_zeros += 1
            # 2 o più zeri consecutivi oppure gap temporale >= 1.8s segnano la separazione
            if (consecutive_zeros >= 2 or dt_gap >= 1.8) and cur_seg:
                max_seg_p = max((_extract_metric_int(x.get("instantaneous_power_w")) or 0) for x in cur_seg)
                if max_seg_p >= 25:
                    segments.append(cur_seg)
                cur_seg = []
        else:
            consecutive_zeros = 0
            cur_seg.append(r)

    if cur_seg:
        max_seg_p = max((_extract_metric_int(x.get("instantaneous_power_w")) or 0) for x in cur_seg)
        if max_seg_p >= 25:
            segments.append(cur_seg)

    # Se non ci sono state file di zeri ma ci sono dati, prendi l'intera sessione come unico segmento
    if not segments:
        segments = [clean]

    peaks: list[PeakMetrics] = []
    for idx, seg in enumerate(segments, 1):
        pm = _analyze_single_sprint_segment(seg, index=idx)
        if pm is not None:
            pm.index = len(peaks) + 1
            peaks.append(pm)

    if not peaks:
        return MultiPeakAnalysis(rows_count=len(clean))

    # Valori massimi di riferimento della sessione
    max_p = max(p.power_peak for p in peaks)
    max_cad = max((p.cadence_max or 0) for p in peaks)
    min_app = min(p.t_app for p in peaks)

    # Calcolo decadenza per ogni picco
    for p in peaks:
        # Decadenza potenza
        p.decay_power_w = round(float(max_p - p.power_peak), 1)
        p.decay_power_pct = round((float(max_p - p.power_peak) / max_p * 100.0), 1) if max_p > 0 else 0.0

        # Decadenza cadenza
        c_val = p.cadence_max or 0
        p.decay_cad_rpm = round(float(max_cad - c_val), 1)
        p.decay_cad_pct = round((float(max_cad - c_val) / max_cad * 100.0), 1) if max_cad > 0 else 0.0

        # Differenza tempo applicazione rispetto al migliore
        p.decay_app_time_s = round(float(p.t_app - min_app), 3)

    # Classifiche ordinate decrescentemente indifferentemente dall'ordine
    peaks_by_power = sorted(peaks, key=lambda x: x.power_peak, reverse=True)
    peaks_by_cadence = sorted(peaks, key=lambda x: (x.cadence_max or 0), reverse=True)

    avg_decay_p = (
        round(sum(p.decay_power_pct for p in peaks) / len(peaks), 1)
        if peaks
        else 0.0
    )
    avg_decay_c = (
        round(sum(p.decay_cad_pct for p in peaks) / len(peaks), 1)
        if peaks
        else 0.0
    )

    t_start = _extract_metric_float(clean[0].get("elapsed_s")) or 0.0
    t_end = _extract_metric_float(clean[-1].get("elapsed_s")) or 0.0
    duration = max(0.0, round(t_end - t_start, 3))

    return MultiPeakAnalysis(
        peaks=peaks,
        peaks_by_power=peaks_by_power,
        peaks_by_cadence=peaks_by_cadence,
        max_power_peak=max_p,
        max_cadence_peak=max_cad,
        min_app_time=min_app,
        average_decay_power_pct=avg_decay_p,
        average_decay_cad_pct=avg_decay_c,
        total_peaks=len(peaks),
        rows_count=len(clean),
        session_duration=duration,
    )


def analyze_session(
    rows: list[dict[str, Any]],
    mode: Literal["single", "multi"] = "single",
) -> SinglePeakAnalysis | MultiPeakAnalysis:
    """Esegue l'analisi appropriata in base alla modalità selezionata."""
    if mode == "multi":
        return analyze_multi_peak(rows)
    return analyze_single_peak(rows)
