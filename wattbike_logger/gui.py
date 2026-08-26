"""UI nativa tkinter: Start / Stop, countdown, live + grafici."""

from __future__ import annotations

import json
import math
import queue
import sys
import threading
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from . import __version__
from .excel_export import write_csv, write_xlsx
from .live_session import LiveSession
from .recorder import timing_stats
from .updater import apply_update, check_latest_release, frozen_executable
# drivers imported lazily on startup

_UI_FONT = ("Segoe UI", 10) if sys.platform.startswith("win") else ("Helvetica", 10)
_UI_FONT_BOLD = (_UI_FONT[0], 11, "bold")
_UI_FONT_BIG = (_UI_FONT[0], 28, "bold")
_UI_FONT_METRIC = (_UI_FONT[0], 20, "bold")
_UI_FONT_SMALL = (_UI_FONT[0], 9)
_UI_FONT_AXIS = (_UI_FONT[0], 8)

# Colori grafici
_CHART_BG = "#fafafa"
_CHART_PLOT = "#ffffff"
_CHART_GRID = "#e6e6e6"
_CHART_AXIS = "#666666"
_CHART_BORDER = "#bdbdbd"
_POWER_COLOR = "#c0392b"
_CAD_COLOR = "#2471a3"


class WattbikeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Wattbike ANT+ Logger  v{__version__}")
        self.geometry("820x680")
        self.minsize(720, 600)
        self.configure(bg="#f0f0f0")

        self._row_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._status_queue: queue.Queue[str] = queue.Queue()
        self._session: LiveSession | None = None
        self._recording = False
        self._countdown_job: str | None = None
        self._power_hist: deque[tuple[float, float]] = deque(maxlen=240)
        self._cad_hist: deque[tuple[float, float]] = deque(maxlen=240)
        self._pkt_count = 0
        self._power_sum = 0.0
        self._t0: float | None = None
        self._stick_ready = False
        self._busy = False  # countdown / driver install / update

        self._build_style()
        self._build_ui()
        self._init_plots()
        self.after(100, self._poll_queues)
        self.after(200, self._check_drivers_async)
        self.after(2500, self._poll_stick_presence)
        self.after(800, self._check_updates_async)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("winnative" if self.tk.call("tk", "windowingsystem") == "win32" else "clam")
        except tk.TclError:
            style.theme_use("clam")
        style.configure("TFrame", background="#f0f0f0")
        style.configure("TLabel", background="#f0f0f0", font=_UI_FONT)
        style.configure("Title.TLabel", font=_UI_FONT_BOLD)
        style.configure("Big.TLabel", font=_UI_FONT_BIG)
        style.configure("Metric.TLabel", font=_UI_FONT_METRIC)
        style.configure("MetricCap.TLabel", font=_UI_FONT_SMALL)
        style.configure("Status.TLabel", font=_UI_FONT_SMALL, foreground="#333333")
        style.configure("Start.TButton", font=_UI_FONT_BOLD, padding=(16, 8))
        style.configure("Stop.TButton", font=_UI_FONT_BOLD, padding=(16, 8))
    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Wattbike ANT+ → Excel", style="Title.TLabel").pack(side=tk.LEFT)
        self.lbl_version = ttk.Label(top, text=f"v{__version__}", style="Status.TLabel")
        self.lbl_version.pack(side=tk.RIGHT)

        bar = ttk.Frame(self, padding=(10, 0))
        bar.pack(fill=tk.X)
        # Disabilitato finché non c'è una chiavetta ANT rilevata
        self.btn_start = ttk.Button(
            bar, text="Start", style="Start.TButton", command=self._on_start, state=tk.DISABLED
        )
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_stop = ttk.Button(
            bar, text="Stop", style="Stop.TButton", command=self._on_stop, state=tk.DISABLED
        )
        self.btn_stop.pack(side=tk.LEFT)
        self.lbl_status = ttk.Label(bar, text="Ricerca chiavetta ANT…", style="Status.TLabel")
        self.lbl_status.pack(side=tk.LEFT, padx=16)

        opts = ttk.Frame(self, padding=(10, 6))
        opts.pack(fill=tk.X)
        ttk.Label(opts, text="Device ID:").pack(side=tk.LEFT)
        self.var_device = tk.StringVar(value="54434")
        self.ent_device = ttk.Entry(opts, textvariable=self.var_device, width=10)
        self.ent_device.pack(side=tk.LEFT, padx=6)
        ttk.Label(opts, text="(0 = qualsiasi)").pack(side=tk.LEFT)

        self.lbl_countdown = ttk.Label(self, text="", style="Big.TLabel", anchor=tk.CENTER)
        self.lbl_countdown.pack(fill=tk.X, pady=8)

        metrics = ttk.Frame(self, padding=10)
        metrics.pack(fill=tk.X)
        self.lbl_power = self._metric_box(metrics, "Potenza", "— W", 0)
        self.lbl_cadence = self._metric_box(metrics, "Cadenza", "— rpm", 1)
        self.lbl_elapsed = self._metric_box(metrics, "Tempo", "00:00", 2)
        self.lbl_avg = self._metric_box(metrics, "Media W", "—", 3)
        self.lbl_packets = self._metric_box(metrics, "Pacchetti", "0", 4)

        charts = ttk.Frame(self, padding=(10, 0, 10, 10))
        charts.pack(fill=tk.BOTH, expand=True)
        self.plot_frame = charts

    def _metric_box(self, parent: ttk.Frame, caption: str, value: str, col: int) -> ttk.Label:
        box = ttk.Frame(parent, padding=8, relief=tk.GROOVE)
        box.grid(row=0, column=col, sticky="nsew", padx=4)
        parent.columnconfigure(col, weight=1)
        ttk.Label(box, text=caption, style="MetricCap.TLabel").pack(anchor=tk.W)
        lbl = ttk.Label(box, text=value, style="Metric.TLabel")
        lbl.pack(anchor=tk.W)
        return lbl

    def _init_plots(self) -> None:
        wrap = ttk.Frame(self.plot_frame)
        wrap.pack(fill=tk.BOTH, expand=True)
        self.canvas_power = self._make_chart(
            wrap, "Potenza", "W", _POWER_COLOR, y_default=(0.0, 400.0), show_xlabel=False
        )
        self.canvas_cad = self._make_chart(
            wrap, "Cadenza", "rpm", _CAD_COLOR, y_default=(0.0, 120.0), show_xlabel=True
        )
        self._redraw_plots()

    def _make_chart(
        self,
        parent: ttk.Frame,
        title: str,
        ylabel: str,
        color: str,
        *,
        y_default: tuple[float, float],
        show_xlabel: bool,
    ) -> tk.Canvas:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True, pady=3)
        ttk.Label(frame, text=title, style="MetricCap.TLabel").pack(anchor=tk.W)
        canvas = tk.Canvas(
            frame,
            height=150,
            bg=_CHART_BG,
            highlightthickness=1,
            highlightbackground=_CHART_BORDER,
        )
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas._line_color = color  # type: ignore[attr-defined]
        canvas._ylabel = ylabel  # type: ignore[attr-defined]
        canvas._y_default = y_default  # type: ignore[attr-defined]
        canvas._show_xlabel = show_xlabel  # type: ignore[attr-defined]
        canvas.bind("<Configure>", lambda _e: self._redraw_plots())
        return canvas

    @staticmethod
    def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
        """Tick equispaziati “tondi” sull’intervallo [lo, hi]."""
        if hi <= lo:
            hi = lo + 1.0
        span = hi - lo
        raw = span / max(n - 1, 1)
        exp = math.floor(math.log10(raw)) if raw > 0 else 0
        mag = 10.0**exp
        step = mag
        for candidate in (1.0, 2.0, 2.5, 5.0, 10.0):
            if candidate * mag >= raw * 0.9:
                step = candidate * mag
                break
        start = math.floor(lo / step) * step
        ticks: list[float] = []
        v = start
        for _ in range(n + 8):
            if v >= lo - step * 1e-6 and v <= hi + step * 1e-6:
                ticks.append(round(v, 8))
            v += step
            if v > hi + step:
                break
        return ticks or [lo, hi]

    def _draw_series(self, canvas: tk.Canvas, series: deque[tuple[float, float]]) -> None:
        canvas.delete("all")
        w = max(int(canvas.winfo_width()), 40)
        h = max(int(canvas.winfo_height()), 40)
        left, right, top = 48, 14, 10
        bottom = 28 if getattr(canvas, "_show_xlabel", False) else 22
        plot_w = max(w - left - right, 10)
        plot_h = max(h - top - bottom, 10)
        x0, y0 = left, top
        x1, y1 = left + plot_w, top + plot_h

        y_def = getattr(canvas, "_y_default", (0.0, 1.0))
        color = getattr(canvas, "_line_color", "#333333")
        ylabel = getattr(canvas, "_ylabel", "")

        if len(series) >= 1:
            xs = [p[0] for p in series]
            ys = [p[1] for p in series]
            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)
            if xmax - xmin < 5:
                xmax = xmin + 5
            # scala Y: almeno il default utile, con margine
            ymin = min(ymin, y_def[0])
            ymax = max(ymax, y_def[1] * 0.25, ymin + 1)
            pad_y = (ymax - ymin) * 0.08
            ymin = max(0.0, ymin - pad_y) if ymin >= 0 else ymin - pad_y
            ymax = ymax + pad_y
        else:
            xmin, xmax = 0.0, 60.0
            ymin, ymax = y_def

        # sfondo area plot
        canvas.create_rectangle(x0, y0, x1, y1, fill=_CHART_PLOT, outline=_CHART_BORDER)

        y_ticks = self._nice_ticks(ymin, ymax, 5)
        x_ticks = self._nice_ticks(xmin, xmax, 6)

        def sx(x: float) -> float:
            return x0 + (x - xmin) / (xmax - xmin) * plot_w

        def sy(y: float) -> float:
            return y1 - (y - ymin) / (ymax - ymin) * plot_h

        for yt in y_ticks:
            py = sy(yt)
            if py < y0 - 1 or py > y1 + 1:
                continue
            canvas.create_line(x0, py, x1, py, fill=_CHART_GRID, width=1)
            label = f"{yt:.0f}" if abs(yt) >= 10 or abs(yt - round(yt)) < 1e-6 else f"{yt:.1f}"
            canvas.create_text(x0 - 6, py, text=label, anchor=tk.E, fill=_CHART_AXIS, font=_UI_FONT_AXIS)

        for xt in x_ticks:
            px = sx(xt)
            if px < x0 - 1 or px > x1 + 1:
                continue
            canvas.create_line(px, y0, px, y1, fill=_CHART_GRID, width=1)
            label = f"{xt:.0f}" if abs(xt) >= 10 or abs(xt - round(xt)) < 1e-6 else f"{xt:.1f}"
            canvas.create_text(px, y1 + 4, text=label, anchor=tk.N, fill=_CHART_AXIS, font=_UI_FONT_AXIS)

        # assi
        canvas.create_line(x0, y0, x0, y1, fill=_CHART_AXIS, width=1)
        canvas.create_line(x0, y1, x1, y1, fill=_CHART_AXIS, width=1)

        # etichetta Y
        canvas.create_text(12, (y0 + y1) / 2, text=ylabel, angle=90, fill=_CHART_AXIS, font=_UI_FONT_SMALL)
        if getattr(canvas, "_show_xlabel", False):
            canvas.create_text((x0 + x1) / 2, h - 4, text="tempo (s)", anchor=tk.S, fill=_CHART_AXIS, font=_UI_FONT_SMALL)

        if len(series) < 2:
            canvas.create_text(
                (x0 + x1) / 2,
                (y0 + y1) / 2,
                text="in attesa di dati…",
                fill="#aaaaaa",
                font=_UI_FONT_SMALL,
            )
            return

        pts: list[float] = []
        for x, y in series:
            pts.extend([sx(x), sy(y)])
        # area sotto la curva
        poly = [sx(series[0][0]), y1]
        for x, y in series:
            poly.extend([sx(x), sy(y)])
        poly.extend([sx(series[-1][0]), y1])
        try:
            canvas.create_polygon(*poly, fill=color, outline="", stipple="gray50")
        except tk.TclError:
            pass
        canvas.create_line(*pts, fill=color, width=2.5, smooth=True)
        # punto ultimo valore
        lx, ly = sx(series[-1][0]), sy(series[-1][1])
        canvas.create_oval(lx - 3, ly - 3, lx + 3, ly + 3, fill=color, outline="")
        canvas.create_text(
            min(lx + 8, x1 - 4),
            max(ly - 8, y0 + 4),
            text=f"{series[-1][1]:.0f}",
            anchor=tk.W,
            fill=color,
            font=_UI_FONT_SMALL,
        )

    def _set_status(self, text: str) -> None:
        self.lbl_status.configure(text=text)

    def _update_start_enabled(self) -> None:
        if self._recording or self._countdown_job or self._busy:
            self.btn_start.configure(state=tk.DISABLED)
            return
        self.btn_start.configure(state=tk.NORMAL if self._stick_ready else tk.DISABLED)

    def _poll_queues(self) -> None:
        try:
            while True:
                self._set_status(self._status_queue.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                row = self._row_queue.get_nowait()
                self._on_live_row(row)
        except queue.Empty:
            pass
        self.after(80, self._poll_queues)

    def _on_live_row(self, row: dict[str, Any]) -> None:
        pwr = row.get("instantaneous_power_w")
        cad = row.get("cadence_rpm")
        elapsed = float(row.get("elapsed_s") or 0)
        if self._t0 is None:
            self._t0 = elapsed
        self._pkt_count += 1
        if pwr is not None:
            self._power_sum += float(pwr)
            self.lbl_power.configure(text=f"{int(pwr)} W")
            self._power_hist.append((elapsed, float(pwr)))
        if cad is not None:
            self.lbl_cadence.configure(text=f"{int(cad)} rpm")
            self._cad_hist.append((elapsed, float(cad)))
        mins = int(elapsed) // 60
        secs = int(elapsed) % 60
        self.lbl_elapsed.configure(text=f"{mins:02d}:{secs:02d}")
        avg = self._power_sum / self._pkt_count if self._pkt_count else 0
        self.lbl_avg.configure(text=f"{avg:.0f}")
        self.lbl_packets.configure(text=str(self._pkt_count))
        if self._pkt_count % 2 == 0:
            self._redraw_plots()

    def _redraw_plots(self) -> None:
        if getattr(self, "canvas_power", None) is not None:
            self._draw_series(self.canvas_power, self._power_hist)
        if getattr(self, "canvas_cad", None) is not None:
            self._draw_series(self.canvas_cad, self._cad_hist)

    def _on_start(self) -> None:
        if self._recording or self._countdown_job or self._busy:
            return
        if not self._stick_ready:
            messagebox.showwarning(
                "Chiavetta ANT+",
                "Nessuna chiavetta ANT+ collegata.\n"
                "Inserisci la stick USB e riprova.",
            )
            self._check_drivers_async()
            return

        # Verifica immediata prima del countdown
        from .drivers import diagnose

        st = diagnose()
        self._stick_ready = bool(st.stick_present)
        if not self._stick_ready:
            self._set_status(st.detail)
            self._update_start_enabled()
            messagebox.showwarning(
                "Chiavetta ANT+",
                "Nessuna chiavetta ANT+ rilevata.\n"
                "Collegala e riprova.",
            )
            return

        try:
            device_id = int(self.var_device.get().strip() or "0")
        except ValueError:
            messagebox.showerror("Device ID", "Inserisci un numero valido (es. 54434).")
            return

        self.btn_start.configure(state=tk.DISABLED)
        self.ent_device.configure(state=tk.DISABLED)
        self._pkt_count = 0
        self._power_sum = 0.0
        self._t0 = None
        self._power_hist.clear()
        self._cad_hist.clear()
        self._redraw_plots()
        self.lbl_power.configure(text="— W")
        self.lbl_cadence.configure(text="— rpm")
        self.lbl_elapsed.configure(text="00:00")
        self.lbl_avg.configure(text="—")
        self.lbl_packets.configure(text="0")
        self.lbl_countdown.configure(text="3")
        self._set_status("Countdown… preparazione chiavetta")

        self._session = LiveSession(
            device_id=device_id,
            mode="scan",
            on_row=lambda r: self._row_queue.put(r),
            on_status=lambda s: self._status_queue.put(s),
        )

        def prep() -> None:
            try:
                assert self._session is not None
                self._session.open_stick()
                self._status_queue.put("Chiavetta OK — countdown")
            except Exception as exc:
                self._status_queue.put(f"Errore ANT+: {exc}")
                self.after(0, lambda: self._abort_start(str(exc)))

        threading.Thread(target=prep, daemon=True, name="ant-prep").start()
        self._countdown_left = 3
        self._tick_countdown()

    def _abort_start(self, err: str) -> None:
        if self._countdown_job:
            self.after_cancel(self._countdown_job)
            self._countdown_job = None
        self.lbl_countdown.configure(text="")
        self.ent_device.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)
        self._update_start_enabled()
        messagebox.showerror(
            "Chiavetta ANT+",
            f"Impossibile aprire la chiavetta:\n{err}\n\n"
            "Controlla che sia inserita, ristaccala, chiudi Zwift e riprova.",
        )

    def _tick_countdown(self) -> None:
        if self._countdown_left > 0:
            self.lbl_countdown.configure(text=str(self._countdown_left))
            self._countdown_left -= 1
            self._countdown_job = self.after(1000, self._tick_countdown)
            return
        self.lbl_countdown.configure(text="VIA!")
        self._countdown_job = self.after(600, self._begin_recording)

    def _begin_recording(self) -> None:
        self._countdown_job = None
        if self._session is None or not self._session.is_ready:
            # Stick ancora in apertura: aspetta un attimo
            if self._session and self._session.last_error:
                self._abort_start(self._session.last_error)
                return
            self.lbl_countdown.configure(text="…")
            self._countdown_job = self.after(300, self._begin_recording)
            return
        self._session.begin_capture()
        self._recording = True
        self.btn_stop.configure(state=tk.NORMAL)
        self.lbl_countdown.configure(text="")
        self._set_status("Registrazione in corso — pedala!")

    def _on_stop(self) -> None:
        if not self._recording and self._session is None:
            return
        self.btn_stop.configure(state=tk.DISABLED)
        self._set_status("Arresto…")
        self._recording = False

        session = self._session
        self._session = None

        def worker() -> None:
            rows: list[dict[str, Any]] = []
            if session is not None:
                try:
                    rows = session.stop()
                except Exception as exc:
                    self._status_queue.put(f"Stop con errore: {exc}")
            self.after(0, lambda: self._after_stop(rows))

        threading.Thread(target=worker, daemon=True, name="ant-stop").start()

    def _after_stop(self, rows: list[dict[str, Any]]) -> None:
        self.ent_device.configure(state=tk.NORMAL)
        self.lbl_countdown.configure(text="")
        self._update_start_enabled()
        if not rows:
            self._set_status("Nessun dato registrato")
            messagebox.showinfo("Sessione", "Nessun pacchetto ricevuto: file non creato.")
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            title="Salva sessione",
            defaultextension=".xlsx",
            initialfile=f"wattbike_{stamp}.xlsx",
            filetypes=[
                ("Excel", "*.xlsx"),
                ("CSV", "*.csv"),
                ("Excel + CSV", "*.xlsx"),
                ("Tutti i file", "*.*"),
            ],
        )
        if not path:
            self._set_status(f"Annullato — {len(rows)} righe non salvate")
            if messagebox.askyesno(
                "Salvataggio",
                f"Hai {len(rows)} righe non salvate. Vuoi davvero scartarle?",
            ):
                return
            path = filedialog.asksaveasfilename(
                title="Salva sessione",
                defaultextension=".xlsx",
                initialfile=f"wattbike_{stamp}.xlsx",
                filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv")],
            )
            if not path:
                return

        out = Path(path)
        stats = timing_stats(rows)
        meta = {
            "mode": "gui/live",
            "found_device_id": None,
            "rows": len(rows),
            "app_version": __version__,
            "timing_stats_json": json.dumps(stats, ensure_ascii=False),
            "power_hz": (stats.get("standard_power") or {}).get("hz"),
            "power_median_dt_s": (stats.get("standard_power") or {}).get("median_dt_s"),
        }
        # recupera device id dall'ultima riga se presente
        if rows:
            meta["found_device_id"] = rows[-1].get("device_id")
        try:
            if out.suffix.lower() == ".csv":
                write_csv(out, rows)
                msg = f"Salvato CSV:\n{out.resolve()}"
            else:
                if out.suffix.lower() != ".xlsx":
                    out = out.with_suffix(".xlsx")
                write_xlsx(out, rows, meta=meta)
                csv_path = out.with_suffix(".csv")
                if messagebox.askyesno("CSV", "Vuoi salvare anche il file CSV accanto all'Excel?"):
                    write_csv(csv_path, rows)
                    msg = f"Salvati:\n{out.resolve()}\n{csv_path.resolve()}"
                else:
                    msg = f"Salvato Excel:\n{out.resolve()}"
            self._set_status(f"Salvate {len(rows)} righe")
            messagebox.showinfo("Salvataggio", msg)
        except Exception as exc:
            messagebox.showerror("Salvataggio", f"Errore scrittura file:\n{exc}")
            self._set_status("Errore salvataggio")

    def _check_drivers_async(self) -> None:
        def worker() -> None:
            from .drivers import diagnose, ensure_drivers, open_zadig_fallback

            st = diagnose()
            self.after(0, lambda: self._handle_driver_status(st, ensure_drivers, open_zadig_fallback))

        threading.Thread(target=worker, daemon=True, name="driver-check").start()

    def _poll_stick_presence(self) -> None:
        """Controlla periodicamente se la chiavetta è stata collegata/scollegata."""
        if not self._recording and not self._countdown_job and not self._busy:

            def worker() -> None:
                from .drivers import diagnose

                st = diagnose()
                ready = bool(st.stick_present)
                detail = st.detail

                def apply() -> None:
                    prev = self._stick_ready
                    self._stick_ready = ready
                    self._update_start_enabled()
                    if not self._recording and not self._countdown_job:
                        if ready and not prev:
                            self._set_status(detail or "Chiavetta ANT rilevata")
                        elif not ready:
                            self._set_status(detail or "Nessuna chiavetta ANT collegata.")

                self.after(0, apply)

            threading.Thread(target=worker, daemon=True, name="stick-poll").start()
        self.after(2500, self._poll_stick_presence)

    def _handle_driver_status(self, st: Any, ensure_drivers: Any, open_zadig_fallback: Any) -> None:
        self._stick_ready = bool(st.stick_present)
        self._set_status(st.detail)
        self._update_start_enabled()
        if st.ok or st.accessible:
            return
        # Solo avviso soft se manca la stick (Start resta disabilitato)
        if not st.stick_present and not st.can_auto_install:
            return
        if not st.stick_present:
            return
        if not st.can_auto_install:
            messagebox.showwarning("Chiavetta ANT+", st.detail)
            return
        if not messagebox.askyesno(
            "Setup driver ANT+",
            f"{st.detail}\n\nVuoi che provo ad installare automaticamente "
            f"quanto serve su questo sistema?",
        ):
            return

        self._busy = True
        self._update_start_enabled()
        self._set_status("Installazione driver…")

        def worker() -> None:
            result = ensure_drivers(on_status=lambda s: self._status_queue.put(s))
            self.after(0, lambda: self._after_driver_install(result, open_zadig_fallback))

        threading.Thread(target=worker, daemon=True, name="driver-install").start()

    def _after_driver_install(self, result: Any, open_zadig_fallback: Any) -> None:
        self._busy = False
        self._stick_ready = bool(getattr(result, "stick_present", False) or result.ok or result.accessible)
        self._update_start_enabled()
        self._set_status(result.detail)
        if result.ok or result.accessible:
            messagebox.showinfo("Driver", "Setup completato.\n" + result.detail)
            return
        if sys.platform.startswith("win"):
            if messagebox.askyesno(
                "Driver",
                "Installazione automatica non sufficiente.\n"
                "Aprire Zadig per installare libusb-win32 manualmente?\n"
                "(Options → List All Devices → stick ANT → libusb-win32)",
            ):
                open_zadig_fallback(on_status=lambda s: self._status_queue.put(s))
        else:
            messagebox.showwarning("Driver", result.detail)

    def _check_updates_async(self) -> None:
        def worker() -> None:
            info = check_latest_release()
            if info:
                self.after(0, lambda: self._prompt_update(info))

        threading.Thread(target=worker, daemon=True, name="update-check").start()

    def _prompt_update(self, info: Any) -> None:
        if frozen_executable() is None:
            # In sviluppo: solo avviso
            self._set_status(f"Aggiornamento disponibile: v{info.version} (solo in exe)")
            return
        if not messagebox.askyesno(
            "Aggiornamento",
            f"È disponibile la versione {info.version} (ora hai {__version__}).\n\n"
            f"{info.name}\n\nScaricare e installare ora?",
        ):
            return
        self._set_status("Download aggiornamento…")
        self._busy = True
        self._update_start_enabled()

        def worker() -> None:
            try:
                apply_update(
                    info,
                    on_status=lambda s: self._status_queue.put(s),
                )
            except SystemExit:
                raise
            except Exception as exc:
                def fail() -> None:
                    messagebox.showerror("Aggiornamento", f"Aggiornamento fallito:\n{exc}")
                    self._busy = False
                    self._update_start_enabled()

                self.after(0, fail)
        threading.Thread(target=worker, daemon=True, name="update-apply").start()

    def _on_close(self) -> None:
        if self._recording:
            if not messagebox.askyesno("Esci", "Registrazione in corso. Fermare e uscire?"):
                return
            if self._session:
                try:
                    self._session.stop()
                except Exception:
                    pass
        self.destroy()


def run_gui() -> int:
    app = WattbikeApp()
    app.mainloop()
    return 0
