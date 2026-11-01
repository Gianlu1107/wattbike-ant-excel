"""Interfaccia grafica Tkinter per registratore Wattbike ANT+ / BLE con Analisi Sportiva."""

from __future__ import annotations

import csv
from collections import deque
from datetime import datetime
import json
import math
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from . import __version__
from .analysis import (
    MultiPeakAnalysis,
    PeakMetrics,
    SinglePeakAnalysis,
    analyze_session,
    deduplicate_session_rows,
)
from .excel_export import write_csv, write_xlsx
from .live_session import LiveSession
from .prefs import (
    get_device_id_pref,
    get_test_mode_pref,
    set_device_id_pref,
    set_test_mode_pref,
)
from .recorder import timing_stats
from .updater import apply_update, check_latest_release, frozen_executable

_UI_FONT = ("Segoe UI", 10) if sys.platform.startswith("win") else ("Helvetica", 10)
_UI_FONT_BOLD = (_UI_FONT[0], 10, "bold")
_UI_FONT_BIG = (_UI_FONT[0], 26, "bold")
_UI_FONT_METRIC = (_UI_FONT[0], 19, "bold")
_UI_FONT_SMALL = (_UI_FONT[0], 9)
_UI_FONT_AXIS = (_UI_FONT[0], 8)

# Palette stile Windows classico / utilità tecnica
_WIN_BG = "#f0f0f0"
_PANEL_BG = "#ffffff"
_CHART_BG = "#fafafa"
_CHART_PLOT = "#ffffff"
_CHART_GRID = "#e6e6e6"
_CHART_AXIS = "#555555"
_CHART_BORDER = "#a0a0a0"
_POWER_COLOR = "#c0392b"
_CAD_COLOR = "#2471a3"


class WattbikeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Wattbike ANT+ / BLE Analyzer & Logger — v{__version__}")
        self.geometry("900x730")
        self.minsize(800, 620)
        self.configure(bg=_WIN_BG)

        self._row_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._status_queue: queue.Queue[str] = queue.Queue()
        self._session: LiveSession | None = None
        self._recording = False
        self._countdown_job: str | None = None
        self._power_hist: deque[tuple[float, float]] = deque(maxlen=360)
        self._cad_hist: deque[tuple[float, float]] = deque(maxlen=360)
        self._peak_markers: list[tuple[float, float, str]] = []
        self._last_rows: list[dict[str, Any]] = []
        self._last_analysis: SinglePeakAnalysis | MultiPeakAnalysis | None = None

        self._pkt_count = 0
        self._power_sum = 0.0
        self._t0: float | None = None
        self._stick_ready = False
        self._ble_ready = False
        self._busy = False
        self._driver_fix_attempted = False
        self._last_stick_present = False
        self._driver_logging = False
        self._ble_poll_tick = 0

        self.var_test_mode = tk.StringVar(value=get_test_mode_pref("single"))
        self.var_multi_sort = tk.StringVar(value="cronologico")

        self._build_style()
        self._build_menu()
        self._build_ui()
        self._init_plots()

        self.after(100, self._poll_queues)
        self.after(150, self._bootstrap_drivers_async)
        self.after(1000, self._poll_stick_presence)
        self.after(2500, self._check_updates_async)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("winnative" if self.tk.call("tk", "windowingsystem") == "win32" else "clam")
        except tk.TclError:
            style.theme_use("clam")

        style.configure("TFrame", background=_WIN_BG)
        style.configure("TLabel", background=_WIN_BG, font=_UI_FONT)
        style.configure("Title.TLabel", font=(_UI_FONT[0], 12, "bold"), foreground="#1a252f")
        style.configure("Big.TLabel", font=_UI_FONT_BIG, foreground="#2c3e50")
        style.configure("Metric.TLabel", font=_UI_FONT_METRIC, foreground="#111111")
        style.configure("MetricCap.TLabel", font=_UI_FONT_SMALL, foreground="#555555")
        style.configure("Status.TLabel", font=_UI_FONT_SMALL, foreground="#333333")
        style.configure("Start.TButton", font=_UI_FONT_BOLD, padding=(14, 6))
        style.configure("Stop.TButton", font=_UI_FONT_BOLD, padding=(14, 6))
        style.configure("Toolbar.TButton", font=_UI_FONT_SMALL, padding=(8, 4))
        style.configure("CardVal.TLabel", font=(_UI_FONT[0], 15, "bold"), foreground="#2c3e50")
        style.configure("CardSub.TLabel", font=_UI_FONT_SMALL, foreground="#7f8c8d")
        style.configure("Header.TLabel", font=(_UI_FONT[0], 11, "bold"), foreground="#2c3e50")

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        # File
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Apri sessione CSV/Excel…", accelerator="Ctrl+O", command=self._on_open_file)
        file_menu.add_command(label="Salva analisi Excel (.xlsx)…", accelerator="Ctrl+S", command=self._on_export_excel)
        file_menu.add_separator()
        file_menu.add_command(label="Esci", command=self._on_close)

        # Modalità Test
        test_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Modalità Test", menu=test_menu)
        test_menu.add_radiobutton(
            label="Picco Singolo (TTP / Sprint Max)",
            value="single",
            variable=self.var_test_mode,
            command=self._on_change_test_mode,
        )
        test_menu.add_radiobutton(
            label="Picco Multiplo (Repeated Sprints / Decadenza)",
            value="multi",
            variable=self.var_test_mode,
            command=self._on_change_test_mode,
        )

        # Strumenti
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Strumenti", menu=tools_menu)
        tools_menu.add_command(label="Setup / Ripristino Driver ANT+…", command=self._on_repair_drivers)

        # Aiuto
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Aiuto", menu=help_menu)
        help_menu.add_command(label="Informazioni su Wattbike Logger", command=self._on_about)

        # Scorciatoie da tastiera
        self.bind("<Control-o>", lambda _e: self._on_open_file())
        self.bind("<Control-s>", lambda _e: self._on_export_excel())

    def _build_ui(self) -> None:
        # 1. Barra superiore (Header + Toolbar)
        top_bar = ttk.Frame(self, padding=(10, 8, 10, 4))
        top_bar.pack(fill=tk.X)

        self.btn_start = ttk.Button(
            top_bar, text="▶ Start", style="Start.TButton", command=self._on_start, state=tk.DISABLED
        )
        self.btn_start.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_stop = ttk.Button(
            top_bar, text="■ Stop", style="Stop.TButton", command=self._on_stop, state=tk.DISABLED
        )
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Separator(top_bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)

        self.btn_open = ttk.Button(
            top_bar, text="📂 Apri CSV/Excel…", style="Toolbar.TButton", command=self._on_open_file
        )
        self.btn_open.pack(side=tk.LEFT, padx=(4, 6))

        self.btn_export = ttk.Button(
            top_bar, text="💾 Salva Excel…", style="Toolbar.TButton", command=self._on_export_excel
        )
        self.btn_export.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_drivers = ttk.Button(
            top_bar, text="Driver ANT+", style="Toolbar.TButton", command=self._on_repair_drivers
        )
        self.btn_drivers.pack(side=tk.RIGHT)

        ttk.Label(top_bar, text=f"Wattbike Logger v{__version__}", style="Title.TLabel").pack(side=tk.RIGHT, padx=14)

        # 2. Riquadro opzioni (Modalità test, Device ID, Bluetooth)
        opts_box = ttk.LabelFrame(self, text=" Configurazione Sessione & Radio ", padding=(8, 4))
        opts_box.pack(fill=tk.X, padx=10, pady=(2, 6))

        # Scelta modalità prima dello start
        ttk.Label(opts_box, text="Modalità:", font=_UI_FONT_BOLD).pack(side=tk.LEFT, padx=(4, 6))
        self.rb_single = ttk.Radiobutton(
            opts_box,
            text="Picco Singolo (TTP)",
            value="single",
            variable=self.var_test_mode,
            command=self._on_change_test_mode,
        )
        self.rb_single.pack(side=tk.LEFT, padx=(0, 8))

        self.rb_multi = ttk.Radiobutton(
            opts_box,
            text="Picco Multiplo (Decadenza)",
            value="multi",
            variable=self.var_test_mode,
            command=self._on_change_test_mode,
        )
        self.rb_multi.pack(side=tk.LEFT, padx=(0, 16))

        ttk.Separator(opts_box, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Label(opts_box, text="Device ID:").pack(side=tk.LEFT, padx=(4, 4))
        self.var_device = tk.StringVar(value=str(get_device_id_pref(0)))
        self.ent_device = ttk.Entry(opts_box, textvariable=self.var_device, width=8)
        self.ent_device.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(opts_box, text="(0 = auto)").pack(side=tk.LEFT)

        self.var_ble = tk.BooleanVar(value=True)
        self.chk_ble = ttk.Checkbutton(
            opts_box,
            text="Anche Bluetooth LE (CPS)",
            variable=self.var_ble,
            command=self._update_start_enabled,
        )
        self.chk_ble.pack(side=tk.LEFT, padx=(16, 4))

        self.lbl_detected = ttk.Label(opts_box, text="", style="Status.TLabel")
        self.lbl_detected.pack(side=tk.RIGHT, padx=8)

        # 3. Riquadro metriche live (6 riquadri beveled in stile Windows XP/2000)
        metrics_wrap = ttk.Frame(self, padding=(10, 0))
        metrics_wrap.pack(fill=tk.X)

        self.lbl_power = self._metric_box(metrics_wrap, "POTENZA", "— W", 0)
        self.lbl_cadence = self._metric_box(metrics_wrap, "CADENZA", "— rpm", 1)
        self.lbl_torque = self._metric_box(metrics_wrap, "COPPIA", "— Nm", 2)
        self.lbl_elapsed = self._metric_box(metrics_wrap, "TEMPO", "00:00", 3)
        self.lbl_avg = self._metric_box(metrics_wrap, "MEDIA W", "—", 4)
        self.lbl_packets = self._metric_box(metrics_wrap, "PACCHETTI", "0", 5)

        # 4. Notebook a schede in stile Windows classico (Tab 1: Monitor Live, Tab 2: Report Analisi)
        self.notebook = ttk.Notebook(self, padding=4)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 4))

        # --- Tab 1: Monitor Live & Grafici ---
        self.tab_live = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self.tab_live, text=" 📊 Monitor Live & Grafici ")

        self.lbl_countdown = ttk.Label(self.tab_live, text="", style="Big.TLabel", anchor=tk.CENTER)
        self.lbl_countdown.pack(fill=tk.X, pady=(0, 2))

        self.plot_frame = ttk.Frame(self.tab_live)
        self.plot_frame.pack(fill=tk.BOTH, expand=True)

        # Log driver (nascosto di default)
        self.driver_log_frame = ttk.LabelFrame(self.tab_live, text="Installazione driver ANT+", padding=6)
        log_wrap = ttk.Frame(self.driver_log_frame)
        log_wrap.pack(fill=tk.BOTH, expand=True)
        self.txt_driver_log = tk.Text(
            log_wrap,
            height=6,
            wrap=tk.WORD,
            font=_UI_FONT_SMALL,
            bg=_PANEL_BG,
            fg="#222222",
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=_CHART_BORDER,
            state=tk.DISABLED,
        )
        scroll = ttk.Scrollbar(log_wrap, command=self.txt_driver_log.yview)
        self.txt_driver_log.configure(yscrollcommand=scroll.set)
        self.txt_driver_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._driver_log_visible = False

        # 5. Barra di stato Windows classica inferiore a 3 scomparti incassati
        status_bar = ttk.Frame(self, relief=tk.SUNKEN, borderwidth=1, padding=(4, 2))
        status_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=2, pady=(2, 2))

        self.lbl_status = ttk.Label(status_bar, text="Preparazione radio ANT+ / BLE…", style="Status.TLabel")
        self.lbl_status.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.lbl_radio_status = ttk.Label(status_bar, text="Radio: in attesa", style="Status.TLabel", width=26)
        self.lbl_radio_status.pack(side=tk.LEFT, padx=6)

        self.lbl_stats_status = ttk.Label(status_bar, text="Modalità: Picco Singolo", style="Status.TLabel", width=28)
        self.lbl_stats_status.pack(side=tk.RIGHT, padx=4)

        # --- Tab 2: Report Analisi Risultati ---
        self.tab_report = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.tab_report, text=" 📈 Report Analisi Risultati ")
        self._build_report_tab(self.tab_report)

    def _metric_box(self, parent: ttk.Frame, caption: str, value: str, col: int) -> ttk.Label:
        box = ttk.Frame(parent, padding=(6, 4), relief=tk.GROOVE, borderwidth=2)
        box.grid(row=0, column=col, sticky="nsew", padx=3, pady=2)
        parent.columnconfigure(col, weight=1)
        ttk.Label(box, text=caption, style="MetricCap.TLabel").pack(anchor=tk.W)
        lbl = ttk.Label(box, text=value, style="Metric.TLabel")
        lbl.pack(anchor=tk.W)
        return lbl

    def _build_report_tab(self, parent: ttk.Frame) -> None:
        """Costruisce i controlli della vista report (Picco Singolo e Multiplo)."""
        # Barra titolo report
        top_rep = ttk.Frame(parent)
        top_rep.pack(fill=tk.X, pady=(0, 6))

        self.lbl_report_title = ttk.Label(
            top_rep, text="Nessun dato analizzato. Registra una sessione o apri un file CSV.", style="Header.TLabel"
        )
        self.lbl_report_title.pack(side=tk.LEFT)

        btn_box = ttk.Frame(top_rep)
        btn_box.pack(side=tk.RIGHT)
        ttk.Button(btn_box, text="💾 Esporta Excel", command=self._on_export_excel, style="Toolbar.TButton").pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_box, text="Esporta CSV", command=self._on_export_csv, style="Toolbar.TButton").pack(
            side=tk.LEFT, padx=4
        )

        # Contenitore per i due layout alternativi (Single e Multi)
        self.frame_single_report = ttk.Frame(parent)
        self.frame_multi_report = ttk.Frame(parent)

        # --- LAYOUT PICCO SINGOLO ---
        cards_frame = ttk.LabelFrame(self.frame_single_report, text=" Parametri Chiave Sprint — Picco Singolo ", padding=10)
        cards_frame.pack(fill=tk.X, pady=(0, 10))

        # Griglia 4x2 di card parametriche
        self.card_p_peak = self._report_card(cards_frame, "POTENZA DI PICCO (P_peak)", "—", "Massimo istantaneo", 0, 0)
        self.card_tx = self._report_card(cards_frame, "TEMPO AL PICCO (tx)", "—", "Istante temporale del picco", 0, 1)
        self.card_t_app = self._report_card(cards_frame, "TEMPO DI APPLICAZIONE (t_app)", "—", "Time to Peak (tx - t0)", 0, 2)
        self.card_t0 = self._report_card(cards_frame, "INIZIO SPINTA (t0)", "—", "Inizio applicazione da zero", 0, 3)

        self.card_cad_peak = self._report_card(cards_frame, "CADENZA AL PICCO", "—", "Cadenza corrispondente a tx", 1, 0)
        self.card_cad_max = self._report_card(cards_frame, "CADENZA MASSIMA", "—", "Picco di cadenza assoluto", 1, 1)
        self.card_torque_peak = self._report_card(cards_frame, "COPPIA AL PICCO", "—", "Coppia a tx: P / ω", 1, 2)
        self.card_torque_max = self._report_card(cards_frame, "COPPIA MASSIMA", "—", "Picco massimo di coppia", 1, 3)

        details_box = ttk.LabelFrame(self.frame_single_report, text=" Dettagli Sessione ", padding=8)
        details_box.pack(fill=tk.X)
        self.lbl_single_details = ttk.Label(details_box, text="In attesa di registrazione…", font=_UI_FONT)
        self.lbl_single_details.pack(anchor=tk.W)

        # --- LAYOUT PICCO MULTIPLO ---
        multi_summary = ttk.LabelFrame(self.frame_multi_report, text=" Riepilogo Sprints & Decadenza ", padding=8)
        multi_summary.pack(fill=tk.X, pady=(0, 6))

        self.lbl_m_peaks_count = self._report_card(multi_summary, "PICCHI TOTALI", "0", "Separati da zeri", 0, 0)
        self.lbl_m_max_p = self._report_card(multi_summary, "PICCO POTENZA MAX", "—", "Miglior sprint", 0, 1)
        self.lbl_m_max_cad = self._report_card(multi_summary, "CADENZA MAX", "—", "Massimo assoluto", 0, 2)
        self.lbl_m_best_app = self._report_card(multi_summary, "MIGLIOR T_APP", "—", "Minimo tempo applicazione", 0, 3)
        self.lbl_m_avg_decay_p = self._report_card(multi_summary, "DECADENZA MEDIA POTENZA", "—", "Fatigue Index medio W", 0, 4)
        self.lbl_m_avg_decay_c = self._report_card(multi_summary, "DECADENZA MEDIA CADENZA", "—", "Fatigue Index cadenza", 0, 5)

        # Selettore ordinamento tabella
        sort_bar = ttk.Frame(self.frame_multi_report)
        sort_bar.pack(fill=tk.X, pady=(4, 4))
        ttk.Label(sort_bar, text="Visualizzazione Tabella:", font=_UI_FONT_BOLD).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Radiobutton(
            sort_bar,
            text="Ordine Cronologico (Timeline Grafico)",
            value="cronologico",
            variable=self.var_multi_sort,
            command=self._refresh_multi_table,
        ).pack(side=tk.LEFT, padx=6)

        ttk.Radiobutton(
            sort_bar,
            text="Classifica Decrescente Potenza (Ranking Decadenza)",
            value="potenza",
            variable=self.var_multi_sort,
            command=self._refresh_multi_table,
        ).pack(side=tk.LEFT, padx=6)

        ttk.Radiobutton(
            sort_bar,
            text="Classifica Decrescente Cadenza",
            value="cadenza",
            variable=self.var_multi_sort,
            command=self._refresh_multi_table,
        ).pack(side=tk.LEFT, padx=6)

        # Tabella Treeview con scrollbar in classico stile Windows
        tbl_frame = ttk.Frame(self.frame_multi_report)
        tbl_frame.pack(fill=tk.BOTH, expand=True)

        cols = (
            "index",
            "t0",
            "tx",
            "t_app",
            "p_peak",
            "cad_peak",
            "cad_max",
            "torque_peak",
            "decay_p_w",
            "decay_p_pct",
            "decay_c_rpm",
            "decay_c_pct",
        )
        self.tree_peaks = ttk.Treeview(tbl_frame, columns=cols, show="headings", height=9)

        col_defs = [
            ("index", "Sprint", 70, "center"),
            ("t0", "t0 (s)", 75, "center"),
            ("tx", "tx Picco (s)", 85, "center"),
            ("t_app", "t_app (s)", 80, "center"),
            ("p_peak", "Picco Potenza (W)", 115, "center"),
            ("cad_peak", "Cad @ Picco (rpm)", 115, "center"),
            ("cad_max", "Cad Max (rpm)", 95, "center"),
            ("torque_peak", "Coppia @ Picco (Nm)", 125, "center"),
            ("decay_p_w", "Decadenza W", 95, "center"),
            ("decay_p_pct", "Decadenza Potenza %", 130, "center"),
            ("decay_c_rpm", "Decadenza Cad (rpm)", 125, "center"),
            ("decay_c_pct", "Decadenza Cad %", 115, "center"),
        ]
        for cid, text, width, anchor in col_defs:
            self.tree_peaks.heading(cid, text=text)
            self.tree_peaks.column(cid, width=width, anchor=anchor)

        sb_y = ttk.Scrollbar(tbl_frame, orient=tk.VERTICAL, command=self.tree_peaks.yview)
        sb_x = ttk.Scrollbar(tbl_frame, orient=tk.HORIZONTAL, command=self.tree_peaks.xview)
        self.tree_peaks.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)

        self.tree_peaks.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        sb_x.pack(side=tk.BOTTOM, fill=tk.X)

        # Mostra frame iniziale
        self._on_change_test_mode()

    def _report_card(
        self, parent: ttk.Frame, title: str, value: str, subtitle: str, row: int, col: int
    ) -> tuple[ttk.Label, ttk.Label]:
        card = ttk.Frame(parent, padding=(6, 4), relief=tk.GROOVE, borderwidth=1)
        card.grid(row=row, column=col, sticky="nsew", padx=3, pady=3)
        parent.columnconfigure(col, weight=1)

        ttk.Label(card, text=title, style="MetricCap.TLabel").pack(anchor=tk.W)
        lbl_val = ttk.Label(card, text=value, style="CardVal.TLabel")
        lbl_val.pack(anchor=tk.W, pady=(2, 1))
        lbl_sub = ttk.Label(card, text=subtitle, style="CardSub.TLabel")
        lbl_sub.pack(anchor=tk.W)
        return lbl_val, lbl_sub

    def _on_change_test_mode(self) -> None:
        """Cambio modalità Picco Singolo / Multiplo."""
        mode = self.var_test_mode.get()
        set_test_mode_pref(mode)
        mode_text = "Picco Singolo (TTP)" if mode == "single" else "Picco Multiplo (Decadenza)"
        if hasattr(self, "lbl_stats_status"):
            self.lbl_stats_status.configure(text=f"Modalità: {mode_text}")

        if mode == "single":
            self.frame_multi_report.pack_forget()
            self.frame_single_report.pack(fill=tk.BOTH, expand=True)
        else:
            self.frame_single_report.pack_forget()
            self.frame_multi_report.pack(fill=tk.BOTH, expand=True)

        # Ricalcola l'analisi se abbiamo righe già caricate
        if self._last_rows:
            self._update_analysis(self._last_rows)

    def _init_plots(self) -> None:
        wrap = ttk.Frame(self.plot_frame)
        wrap.pack(fill=tk.BOTH, expand=True)
        self.canvas_power = self._make_chart(
            wrap, "Potenza Istantanea & Picchi", "W", _POWER_COLOR, y_default=(0.0, 400.0), show_xlabel=False
        )
        self.canvas_power._show_peaks = True  # type: ignore[attr-defined]

        self.canvas_cad = self._make_chart(
            wrap, "Cadenza Istantanea", "rpm", _CAD_COLOR, y_default=(0.0, 120.0), show_xlabel=True
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
        frame.pack(fill=tk.BOTH, expand=True, pady=2)
        ttk.Label(frame, text=title, style="MetricCap.TLabel").pack(anchor=tk.W)
        canvas = tk.Canvas(
            frame,
            height=140,
            bg=_CHART_BG,
            highlightthickness=1,
            highlightbackground=_CHART_BORDER,
        )
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas._line_color = color  # type: ignore[attr-defined]
        canvas._ylabel = ylabel  # type: ignore[attr-defined]
        canvas._y_default = y_default  # type: ignore[attr-defined]
        canvas._show_xlabel = show_xlabel  # type: ignore[attr-defined]
        canvas._show_peaks = False  # type: ignore[attr-defined]
        canvas.bind("<Configure>", lambda _e: self._redraw_plots())
        return canvas

    @staticmethod
    def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
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
        bottom = 26 if getattr(canvas, "_show_xlabel", False) else 20
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
            ymin = min(ymin, y_def[0])
            ymax = max(ymax, y_def[1] * 0.25, ymin + 1)
            pad_y = (ymax - ymin) * 0.1
            ymin = max(0.0, ymin - pad_y) if ymin >= 0 else ymin - pad_y
            ymax = ymax + pad_y
        else:
            xmin, xmax = 0.0, 60.0
            ymin, ymax = y_def

        # Sfondo rettangolo
        canvas.create_rectangle(x0, y0, x1, y1, fill=_CHART_PLOT, outline=_CHART_BORDER)

        y_ticks = self._nice_ticks(ymin, ymax, 5)
        x_ticks = self._nice_ticks(xmin, xmax, 6)

        def sx(x: float) -> float:
            span = xmax - xmin if xmax > xmin else 1.0
            return x0 + (x - xmin) / span * plot_w

        def sy(y: float) -> float:
            span = ymax - ymin if ymax > ymin else 1.0
            return y1 - (y - ymin) / span * plot_h

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

        canvas.create_line(x0, y0, x0, y1, fill=_CHART_AXIS, width=1)
        canvas.create_line(x0, y1, x1, y1, fill=_CHART_AXIS, width=1)

        canvas.create_text(12, (y0 + y1) / 2, text=ylabel, angle=90, fill=_CHART_AXIS, font=_UI_FONT_SMALL)
        if getattr(canvas, "_show_xlabel", False):
            canvas.create_text((x0 + x1) / 2, h - 3, text="tempo (s)", anchor=tk.S, fill=_CHART_AXIS, font=_UI_FONT_SMALL)

        if len(series) < 2:
            canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="in attesa di dati…", fill="#999999", font=_UI_FONT_SMALL)
            return

        pts: list[float] = []
        for x, y in series:
            pts.extend([sx(x), sy(y)])
        poly = [sx(series[0][0]), y1]
        for x, y in series:
            poly.extend([sx(x), sy(y)])
        poly.extend([sx(series[-1][0]), y1])
        try:
            canvas.create_polygon(*poly, fill=color, outline="", stipple="gray50")
        except tk.TclError:
            pass
        canvas.create_line(*pts, fill=color, width=2.0, smooth=True)

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

        # Evidenziazione visiva dei punti di picco rilevati dall'analisi
        if getattr(canvas, "_show_peaks", False) and self._peak_markers:
            for p_time, p_val, p_lbl in self._peak_markers:
                if xmin <= p_time <= xmax:
                    cx = sx(p_time)
                    cy = sy(p_val)
                    canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill="#f39c12", outline="#b03a2e", width=2)
                    canvas.create_text(
                        cx,
                        max(cy - 12, y0 + 6),
                        text=p_lbl,
                        fill="#b03a2e",
                        font=(_UI_FONT[0], 9, "bold"),
                        anchor=tk.S,
                    )

    def _set_status(self, text: str) -> None:
        self.lbl_status.configure(text=text)

    def _set_status_and_driver_log(self, text: str) -> None:
        self._set_status(text)
        self._append_driver_log(text)

    def _show_driver_log(self, *, clear: bool = False) -> None:
        if clear:
            self.txt_driver_log.configure(state=tk.NORMAL)
            self.txt_driver_log.delete("1.0", tk.END)
            self.txt_driver_log.configure(state=tk.DISABLED)
        if not self._driver_log_visible:
            self.driver_log_frame.pack(fill=tk.X, padx=4, pady=4)
            self._driver_log_visible = True

    def _hide_driver_log(self, *, delay_ms: int = 0) -> None:
        def _do() -> None:
            if self._driver_log_visible:
                self.driver_log_frame.pack_forget()
                self._driver_log_visible = False

        if delay_ms > 0:
            self.after(delay_ms, _do)
        else:
            _do()

    def _append_driver_log(self, text: str) -> None:
        if not text:
            return
        self.txt_driver_log.configure(state=tk.NORMAL)
        self.txt_driver_log.insert(tk.END, text.rstrip() + "\n")
        self.txt_driver_log.see(tk.END)
        self.txt_driver_log.configure(state=tk.DISABLED)

    def _update_start_enabled(self) -> None:
        can = self._stick_ready or (bool(self.var_ble.get()) and self._ble_ready)
        self.btn_start.configure(state=tk.NORMAL if (can and not self._busy and not self._recording) else tk.DISABLED)

    def _poll_queues(self) -> None:
        try:
            while True:
                msg = self._status_queue.get_nowait()
                self._set_status(msg)
                if self._driver_logging:
                    self._append_driver_log(msg)
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
        tq = row.get("torque_nm")
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
        if tq is not None and str(tq).strip() != "":
            self.lbl_torque.configure(text=f"{float(tq):.1f} Nm")
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
        if self._recording or self._busy:
            return
        raw_dev = self.var_device.get().strip()
        try:
            device_id = int(raw_dev) if raw_dev else 0
        except ValueError:
            messagebox.showerror("Device ID", "Il Device ID deve essere un numero intero (0 = qualsiasi).")
            return

        set_device_id_pref(device_id)
        self._busy = True
        self.btn_start.configure(state=tk.DISABLED)
        self.btn_stop.configure(state=tk.DISABLED)
        self.ent_device.configure(state=tk.DISABLED)

        self._power_hist.clear()
        self._cad_hist.clear()
        self._peak_markers.clear()
        self._pkt_count = 0
        self._power_sum = 0.0
        self._t0 = None
        self._redraw_plots()

        self.lbl_power.configure(text="— W")
        self.lbl_cadence.configure(text="— rpm")
        self.lbl_torque.configure(text="— Nm")
        self.lbl_elapsed.configure(text="00:00")
        self.lbl_avg.configure(text="—")
        self.lbl_packets.configure(text="0")
        self.lbl_detected.configure(text="")
        self.lbl_countdown.configure(text="3")
        self._set_status("Countdown… preparazione radio")

        # Assicurati di mostrare la scheda monitor durante il live
        self.notebook.select(self.tab_live)

        def on_device(dev_id: int, type_name: str) -> None:
            self.after(0, lambda: self._on_device_found(dev_id, type_name))

        self._session = LiveSession(
            device_id=device_id,
            mode="scan",
            on_row=lambda r: self._row_queue.put(r),
            on_status=lambda s: self._status_queue.put(s),
            on_device=on_device,
            use_ble=bool(self.var_ble.get()),
        )

        def prep() -> None:
            try:
                assert self._session is not None
                self._session.open_stick()
                self._status_queue.put("Radio OK — countdown")
            except Exception as exc:
                self._status_queue.put(f"Errore ANT+/BLE: {exc}")
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
        self._busy = False
        self._update_start_enabled()
        messagebox.showerror(
            "Radio ANT+ / Bluetooth",
            f"Impossibile aprire la chiavetta o il Bluetooth:\n{err}\n\n"
            "Controlla che la chiavetta sia inserita, chiudi altre app e riprova.",
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
            if self._session and self._session.last_error:
                self._abort_start(self._session.last_error)
                return
            self.lbl_countdown.configure(text="…")
            self._countdown_job = self.after(300, self._begin_recording)
            return
        self._session.begin_capture()
        self._recording = True
        self._busy = False
        self.btn_stop.configure(state=tk.NORMAL)
        self.lbl_countdown.configure(text="")

    def _on_stop(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self.btn_stop.configure(state=tk.DISABLED)
        self.lbl_countdown.configure(text="Salvataggio…")
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

        clean = deduplicate_session_rows(rows)
        self._last_rows = clean

        # Elabora e mostra immediatamente l'analisi
        self._update_analysis(clean)
        # Passa automaticamente alla scheda Report Analisi
        self.notebook.select(self.tab_report)

        # Proponi salvataggio file
        self._prompt_save_session(clean)

    def _prompt_save_session(self, rows: list[dict[str, Any]]) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode = self.var_test_mode.get()
        prefix = "wattbike_sprint_single" if mode == "single" else "wattbike_sprint_multi"
        path = filedialog.asksaveasfilename(
            title="Salva sessione con Analisi",
            defaultextension=".xlsx",
            initialfile=f"{prefix}_{stamp}.xlsx",
            filetypes=[
                ("Excel con Analisi (*.xlsx)", "*.xlsx"),
                ("CSV (*.csv)", "*.csv"),
                ("Tutti i file", "*.*"),
            ],
        )
        if not path:
            self._set_status(f"Non salvato su disco — {len(rows)} righe caricate in memoria")
            return

        out = Path(path)
        stats = timing_stats(rows)
        meta = {
            "mode": f"gui/live_{mode}",
            "found_device_id": rows[-1].get("device_id") if rows else None,
            "rows": len(rows),
            "app_version": __version__,
            "timing_stats_json": json.dumps(stats, ensure_ascii=False),
            "power_hz": (stats.get("standard_power") or {}).get("hz"),
        }

        try:
            if out.suffix.lower() == ".csv":
                write_csv(out, rows)
                msg = f"Salvato CSV:\n{out.resolve()}"
            else:
                if out.suffix.lower() != ".xlsx":
                    out = out.with_suffix(".xlsx")
                write_xlsx(out, rows, meta=meta, mode=mode)
                csv_path = out.with_suffix(".csv")
                if messagebox.askyesno("CSV", "Vuoi salvare anche il file CSV accanto al file Excel?"):
                    write_csv(csv_path, rows)
                    msg = f"Salvati:\n{out.resolve()}\n{csv_path.resolve()}"
                else:
                    msg = f"Salvato Excel con Analisi:\n{out.resolve()}"

            self._set_status(f"Salvate {len(rows)} righe in {out.name}")
            messagebox.showinfo("Salvataggio", msg)
        except Exception as exc:
            messagebox.showerror("Errore salvataggio", f"Impossibile salvare il file:\n{exc}")

    def _update_analysis(self, rows: list[dict[str, Any]]) -> None:
        """Esegue l'analisi sportiva e popola l'interfaccia (scheda Report e grafici)."""
        clean = deduplicate_session_rows(rows)
        self._last_rows = clean
        mode = self.var_test_mode.get()
        analysis = analyze_session(clean, mode=mode)
        self._last_analysis = analysis

        # Popola marker dei picchi sui grafici
        self._peak_markers.clear()

        if isinstance(analysis, SinglePeakAnalysis):
            pk = analysis.peak
            self.lbl_report_title.configure(
                text=f"Analisi Picco Singolo (TTP) — {analysis.rows_count} righe, durata {analysis.session_duration:.1f} s"
            )
            if pk:
                self.card_p_peak[0].configure(text=f"{pk.power_peak} W")
                self.card_tx[0].configure(text=f"{pk.t_peak:.3f} s")
                self.card_t_app[0].configure(text=f"{pk.t_app:.3f} s")
                self.card_t0[0].configure(text=f"{pk.t0:.3f} s")
                self.card_cad_peak[0].configure(text=f"{pk.cadence_at_peak or '—'} rpm")
                self.card_cad_max[0].configure(text=f"{pk.cadence_max or '—'} rpm")
                self.card_torque_peak[0].configure(text=f"{pk.torque_at_peak or '—'} Nm")
                self.card_torque_max[0].configure(text=f"{pk.torque_max or '—'} Nm")

                self.lbl_single_details.configure(
                    text=f"Potenza media sprint: {pk.power_avg or '—'} W  |  "
                    f"Durata sforzo: {pk.duration:.2f} s  |  "
                    f"Istante cadenza max: {pk.t_cad_max or '—'} s"
                )
                self._peak_markers.append((pk.t_peak, float(pk.power_peak), f"{pk.power_peak}W @ {pk.t_peak:.1f}s"))
            else:
                self.lbl_single_details.configure(text="Nessun picco significativo rilevato.")

        elif isinstance(analysis, MultiPeakAnalysis):
            self.lbl_report_title.configure(
                text=f"Analisi Repeated Sprints (Picchi Multipli) — {analysis.total_peaks} picchi rilevati"
            )
            self.lbl_m_peaks_count[0].configure(text=str(analysis.total_peaks))
            self.lbl_m_max_p[0].configure(text=f"{analysis.max_power_peak} W")
            self.lbl_m_max_cad[0].configure(text=f"{analysis.max_cadence_peak} rpm")
            self.lbl_m_best_app[0].configure(text=f"{analysis.min_app_time:.3f} s")
            self.lbl_m_avg_decay_p[0].configure(text=f"{analysis.average_decay_power_pct:.1f}%")
            self.lbl_m_avg_decay_c[0].configure(text=f"{analysis.average_decay_cad_pct:.1f}%")

            for pk in analysis.peaks:
                self._peak_markers.append((pk.t_peak, float(pk.power_peak), f"#{pk.index}: {pk.power_peak}W"))

            self._refresh_multi_table()

        self._redraw_plots()

    def _refresh_multi_table(self) -> None:
        """Aggiorna le righe del Treeview in base all'ordinamento selezionato."""
        if not isinstance(self._last_analysis, MultiPeakAnalysis):
            return

        for item in self.tree_peaks.get_children():
            self.tree_peaks.delete(item)

        sort_by = self.var_multi_sort.get()
        if sort_by == "potenza":
            peaks = self._last_analysis.peaks_by_power
        elif sort_by == "cadenza":
            peaks = self._last_analysis.peaks_by_cadence
        else:
            peaks = self._last_analysis.peaks

        for pk in peaks:
            self.tree_peaks.insert(
                "",
                tk.END,
                values=(
                    f"Picco {pk.index}",
                    f"{pk.t0:.3f}",
                    f"{pk.t_peak:.3f}",
                    f"{pk.t_app:.3f}",
                    f"{pk.power_peak} W",
                    f"{pk.cadence_at_peak or '—'} rpm",
                    f"{pk.cadence_max or '—'} rpm",
                    f"{pk.torque_at_peak or '—'} Nm",
                    f"{pk.decay_power_w:.1f} W",
                    f"{pk.decay_power_pct:.1f}%",
                    f"{pk.decay_cad_rpm:.1f} rpm",
                    f"{pk.decay_cad_pct:.1f}%",
                ),
            )

    def _on_open_file(self) -> None:
        """Carica un file CSV o Excel precedentemente registrato per visualizzarne l'analisi."""
        path = filedialog.askopenfilename(
            title="Apri file dati Wattbike",
            filetypes=[
                ("File dati (*.csv; *.xlsx)", "*.csv;*.xlsx"),
                ("CSV (*.csv)", "*.csv"),
                ("Excel (*.xlsx)", "*.xlsx"),
                ("Tutti i file", "*.*"),
            ],
        )
        if not path:
            return

        file_path = Path(path)
        rows: list[dict[str, Any]] = []

        try:
            if file_path.suffix.lower() == ".csv":
                with file_path.open("r", encoding="utf-8", errors="replace") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        # Assicura tipi e compatibilità
                        if r.get("elapsed_s") is not None and str(r.get("elapsed_s")).strip() != "":
                            rows.append(r)
            else:
                import openpyxl

                wb = openpyxl.load_workbook(file_path, data_only=True)
                sheet_name = "raw" if "raw" in wb.sheetnames else wb.sheetnames[0]
                ws = wb[sheet_name]
                header = [str(c.value) for c in ws[1]]
                for row_cells in ws.iter_rows(min_row=2, values_only=True):
                    r_dict = {header[i]: row_cells[i] for i in range(min(len(header), len(row_cells)))}
                    if r_dict.get("elapsed_s") is not None:
                        rows.append(r_dict)

            if not rows:
                messagebox.showwarning("File vuoto", "Nessun dato valido trovato nel file.")
                return

            clean = deduplicate_session_rows(rows)
            self._last_rows = clean

            # Popola la cronologia dei grafici
            self._power_hist.clear()
            self._cad_hist.clear()
            self._pkt_count = len(clean)

            p_sum = 0.0
            for r in clean:
                t = float(r.get("elapsed_s") or 0.0)
                p = r.get("instantaneous_power_w")
                c = r.get("cadence_rpm")
                if p is not None and str(p).strip() != "":
                    p_val = float(p)
                    self._power_hist.append((t, p_val))
                    p_sum += p_val
                if c is not None and str(c).strip() != "":
                    self._cad_hist.append((t, float(c)))

            self._power_sum = p_sum
            if clean:
                last_r = clean[-1]
                self.lbl_power.configure(text=f"{last_r.get('instantaneous_power_w', '—')} W")
                self.lbl_cadence.configure(text=f"{last_r.get('cadence_rpm', '—')} rpm")
                self.lbl_torque.configure(text=f"{last_r.get('torque_nm', '—')} Nm")
                dur = float(last_r.get("elapsed_s") or 0)
                self.lbl_elapsed.configure(text=f"{int(dur)//60:02d}:{int(dur)%60:02d}")
                avg_p = p_sum / len(clean) if clean else 0
                self.lbl_avg.configure(text=f"{avg_p:.0f}")
                self.lbl_packets.configure(text=str(len(clean)))

            self._update_analysis(clean)
            self.notebook.select(self.tab_report)
            self._set_status(f"Aperto {file_path.name} ({len(clean)} righe deduplicate)")

        except Exception as exc:
            messagebox.showerror("Errore apertura", f"Impossibile leggere il file:\n{exc}")

    def _on_export_excel(self) -> None:
        if not self._last_rows:
            messagebox.showinfo("Esporta", "Nessuna sessione in memoria da salvare.")
            return
        self._prompt_save_session(self._last_rows)

    def _on_export_csv(self) -> None:
        if not self._last_rows:
            messagebox.showinfo("Esporta", "Nessuna sessione in memoria da salvare.")
            return
        path = filedialog.asksaveasfilename(
            title="Salva CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        write_csv(Path(path), self._last_rows)
        messagebox.showinfo("Salvataggio", f"CSV salvato:\n{path}")

    def _on_about(self) -> None:
        messagebox.showinfo(
            "Informazioni",
            f"Wattbike ANT+ / BLE Analyzer & Logger\n"
            f"Versione: {__version__}\n\n"
            "Analisi sportiva di picco singolo (Time to Peak) e picco multiplo con decadenza.\n"
            "Supporto ANT+ e Bluetooth LE CPS con calcolo matematico della coppia.",
        )

    def _on_device_found(self, device_id: int, type_name: str) -> None:
        self.lbl_detected.configure(text=f"Rilevato: #{device_id} ({type_name})")
        self.lbl_radio_status.configure(text=f"Device: #{device_id}")

    def _apply_driver_status(self, st: Any, *, announce: bool = True) -> None:
        present = bool(getattr(st, "stick_present", False))
        accessible = bool(getattr(st, "stick_accessible", False) or st.accessible)
        self._stick_ready = present and accessible
        self._update_start_enabled()

        if self._stick_ready:
            self.lbl_radio_status.configure(text="ANT+: OK (pronto)")
        elif self._ble_ready:
            self.lbl_radio_status.configure(text="BLE: Connesso")
        else:
            self.lbl_radio_status.configure(text="Radio: in attesa")

        if announce:
            if self._ble_ready and not self._stick_ready:
                self._set_status("Bluetooth Wattbike rilevato (CPS) — pronto.")
            elif self._ble_ready and self._stick_ready:
                self._set_status("ANT+ e Bluetooth pronti.")
            elif self._stick_ready:
                self._set_status("Chiavetta ANT+ pronta — premi Start.")
            elif present and not accessible:
                self._set_status(st.detail or "Driver ANT non configurati")
            else:
                self._set_status("In attesa chiavetta ANT+ o Bluetooth Wattbike…")

    def _bootstrap_drivers_async(self) -> None:
        def worker() -> None:
            from .drivers import diagnose_stick, ensure_windows_driver, open_zadig_async

            st = diagnose_stick()
            self.after(0, lambda: self._after_bootstrap(st, open_zadig_async))

        threading.Thread(target=worker, daemon=True, name="ant-boot").start()

    def _after_bootstrap(self, st: Any, open_zadig_fallback: Any) -> None:
        self._last_stick_present = bool(st.stick_present)
        self._apply_driver_status(st, announce=True)
        if st.stick_present and not (st.ok or st.accessible) and st.can_auto_install:
            self._run_driver_repair(open_zadig_fallback, silent=True)

    def _on_repair_drivers(self) -> None:
        from .drivers import open_zadig_async

        self._run_driver_repair(open_zadig_async, silent=False)

    def _run_driver_repair(self, open_zadig_fallback: Any, *, silent: bool) -> None:
        self._show_driver_log(clear=True)
        self._driver_logging = True
        self._busy = True
        self._update_start_enabled()
        self._set_status_and_driver_log("Configurazione driver in corso…")

        def worker() -> None:
            from .drivers import ensure_windows_driver

            res = ensure_windows_driver(
                on_status=lambda s: self._status_queue.put(s),
                on_output=lambda line: self._status_queue.put(line),
            )
            self.after(0, lambda: self._after_driver_install(res, open_zadig_fallback, silent=silent))

        threading.Thread(target=worker, daemon=True, name="driver-repair").start()

    def _poll_stick_presence(self) -> None:
        if not self._busy and not self._recording:
            def worker() -> None:
                from .drivers import diagnose_stick

                st = diagnose_stick()

                def apply() -> None:
                    curr_present = bool(st.stick_present)
                    if curr_present != self._last_stick_present:
                        self._last_stick_present = curr_present
                        if (
                            curr_present
                            and not (st.ok or st.accessible)
                            and st.can_auto_install
                            and not self._driver_fix_attempted
                        ):
                            self._driver_fix_attempted = True
                            from .drivers import open_zadig_async

                            self._run_driver_repair(open_zadig_async, silent=True)
                            return
                    self._apply_driver_status(st, announce=False)

                self.after(0, apply)

            threading.Thread(target=worker, daemon=True, name="ant-poll").start()

            # Poll Bluetooth periodico
            self._ble_poll_tick += 1
            if self._ble_poll_tick % 8 == 1:
                def ble_worker() -> None:
                    try:
                        from .ble_wattbike import bleak_available, discover_wattbikes_sync

                        if not bleak_available():
                            return
                        found = discover_wattbikes_sync(timeout_s=1.2)

                        def apply_ble() -> None:
                            self._ble_ready = bool(found)
                            self._update_start_enabled()
                            if self._ble_ready and not self._stick_ready:
                                self.lbl_radio_status.configure(text="BLE: Connesso")

                        self.after(0, apply_ble)
                    except Exception:
                        pass

                threading.Thread(target=ble_worker, daemon=True, name="ble-poll").start()

        self.after(1000, self._poll_stick_presence)

    def _after_driver_install(self, result: Any, open_zadig_fallback: Any, *, silent: bool = False) -> None:
        self._busy = False
        self._apply_driver_status(result)
        self._append_driver_log(result.detail)
        if result.stick_present and (result.ok or result.accessible):
            self._set_status("Chiavetta ANT+ pronta.")
            self._hide_driver_log(delay_ms=3000)
            if not silent:
                messagebox.showinfo("Driver", "Setup completato.\n" + result.detail)
            return
        if result.backend_ready and not result.stick_present:
            self._set_status("Driver pronti. Collega la chiavetta USB ANT+.")
            self._hide_driver_log(delay_ms=5000)
            return
        self._set_status(result.detail)

    def _check_updates_async(self) -> None:
        def worker() -> None:
            info = check_latest_release()
            if info:
                self.after(0, lambda: self._prompt_update(info))

        threading.Thread(target=worker, daemon=True, name="update-check").start()

    def _prompt_update(self, info: Any) -> None:
        if frozen_executable() is None:
            return
        if not messagebox.askyesno(
            "Aggiornamento",
            f"È disponibile la versione {info.version} (ora hai {__version__}).\n\nScaricare e installare ora?",
        ):
            return
        self._set_status("Download aggiornamento…")
        self._busy = True
        self._update_start_enabled()

        def worker() -> None:
            try:
                apply_update(info, on_status=lambda s: self._status_queue.put(s))
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
