"""UI nativa tkinter: Start / Stop, countdown, live + grafici."""

from __future__ import annotations

import json
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

class WattbikeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Wattbike ANT+ Logger  v{__version__}")
        self.geometry("780x620")
        self.minsize(700, 560)
        self.configure(bg="#f0f0f0")

        self._row_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._status_queue: queue.Queue[str] = queue.Queue()
        self._session: LiveSession | None = None
        self._recording = False
        self._countdown_job: str | None = None
        self._power_hist: deque[tuple[float, float]] = deque(maxlen=180)
        self._cad_hist: deque[tuple[float, float]] = deque(maxlen=180)
        self._pkt_count = 0
        self._power_sum = 0.0
        self._t0: float | None = None

        self._build_style()
        self._build_ui()
        self._init_plots()
        self.after(100, self._poll_queues)
        self.after(300, self._check_drivers_async)
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
        self.btn_start = ttk.Button(bar, text="Start", style="Start.TButton", command=self._on_start)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_stop = ttk.Button(
            bar, text="Stop", style="Stop.TButton", command=self._on_stop, state=tk.DISABLED
        )
        self.btn_stop.pack(side=tk.LEFT)
        self.lbl_status = ttk.Label(bar, text="Pronto", style="Status.TLabel")
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
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
        except ImportError:
            ttk.Label(
                self.plot_frame,
                text="matplotlib non installato: grafici disabilitati",
                style="Status.TLabel",
            ).pack()
            self.canvas = None
            self.ax_power = None
            self.ax_cad = None
            return

        fig = Figure(figsize=(7.2, 3.2), dpi=100, facecolor="#f0f0f0")
        self.ax_power = fig.add_subplot(211)
        self.ax_cad = fig.add_subplot(212)
        for ax, title, ylab in (
            (self.ax_power, "Potenza", "W"),
            (self.ax_cad, "Cadenza", "rpm"),
        ):
            ax.set_title(title, fontsize=9, loc="left")
            ax.set_ylabel(ylab, fontsize=8)
            ax.tick_params(labelsize=7)
            ax.grid(True, alpha=0.3)
            ax.set_facecolor("#ffffff")
        self.ax_cad.set_xlabel("tempo (s)", fontsize=8)
        fig.tight_layout(pad=1.2)
        self.canvas = FigureCanvasTkAgg(fig, master=self.plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._fig = fig

    def _set_status(self, text: str) -> None:
        self.lbl_status.configure(text=text)

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
        if not self.canvas or not self.ax_power or not self.ax_cad:
            return
        self.ax_power.clear()
        self.ax_cad.clear()
        self.ax_power.set_title("Potenza", fontsize=9, loc="left")
        self.ax_cad.set_title("Cadenza", fontsize=9, loc="left")
        self.ax_power.set_ylabel("W", fontsize=8)
        self.ax_cad.set_ylabel("rpm", fontsize=8)
        self.ax_cad.set_xlabel("tempo (s)", fontsize=8)
        if self._power_hist:
            xs, ys = zip(*self._power_hist)
            self.ax_power.plot(xs, ys, color="#c0392b", linewidth=1.2)
        if self._cad_hist:
            xs, ys = zip(*self._cad_hist)
            self.ax_cad.plot(xs, ys, color="#2980b9", linewidth=1.2)
        for ax in (self.ax_power, self.ax_cad):
            ax.grid(True, alpha=0.3)
            ax.tick_params(labelsize=7)
        self._fig.tight_layout(pad=1.2)
        self.canvas.draw_idle()

    def _on_start(self) -> None:
        if self._recording or self._countdown_job:
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
        self.btn_start.configure(state=tk.NORMAL)
        self.ent_device.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)
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
        self.btn_start.configure(state=tk.NORMAL)
        self.ent_device.configure(state=tk.NORMAL)
        self.lbl_countdown.configure(text="")
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

    def _handle_driver_status(self, st: Any, ensure_drivers: Any, open_zadig_fallback: Any) -> None:
        self._set_status(st.detail)
        if st.ok or st.accessible:
            return
        # Solo avviso soft se manca la stick
        if not st.stick_present and not st.can_auto_install:
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

        self.btn_start.configure(state=tk.DISABLED)
        self._set_status("Installazione driver…")

        def worker() -> None:
            result = ensure_drivers(on_status=lambda s: self._status_queue.put(s))
            self.after(0, lambda: self._after_driver_install(result, open_zadig_fallback))

        threading.Thread(target=worker, daemon=True, name="driver-install").start()

    def _after_driver_install(self, result: Any, open_zadig_fallback: Any) -> None:
        self.btn_start.configure(state=tk.NORMAL)
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
        self.btn_start.configure(state=tk.DISABLED)

        def worker() -> None:
            try:
                apply_update(
                    info,
                    on_status=lambda s: self._status_queue.put(s),
                )
            except SystemExit:
                raise
            except Exception as exc:
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        "Aggiornamento", f"Aggiornamento fallito:\n{exc}"
                    ),
                )
                self.after(0, lambda: self.btn_start.configure(state=tk.NORMAL))

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
