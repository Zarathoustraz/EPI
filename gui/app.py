import time
import tkinter as tk
from tkinter import messagebox, ttk
import re

from core.database import (
    allocate_ppe, scrap_allocation, run_audit_scanner, add_agent,
    update_agent_status, add_ppe_type, add_stock_lot, _get_kpis,
    _get_active_allocs, _get_active_agents, _get_available_stocks,
    _get_all_agents, _get_all_arsenal, _get_all_vault,
    _fmt_mad, _parse_mad_input
)
from utils.pdf_engine import (
    pdf_bon_allocation, pdf_etat_allocations, pdf_journal_nc,
    pdf_inventaire_vault, _open_pdf
)

C: dict[str, str] = {
    "bg0":    "#0D1117", "bg1":    "#161B22", "bg2":    "#1C2128", "bg3":    "#21262D",
    "blue":   "#58A6FF", "green":  "#3FB950", "orange": "#D29922", "red":    "#F85149",
    "purple": "#BC8CFF", "t0":     "#E6EDF3", "t1":     "#8B949E", "border": "#30363D",
}

class PPEVaultApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("EPI MANAGER  ·  ISOFU  ·  Architecte : Roger Fernando")
        self.geometry("1300x820")
        self.minsize(1080, 700)
        self.configure(bg=C["bg0"])

        self.option_add("*TCombobox*Listbox.background",       C["bg2"])
        self.option_add("*TCombobox*Listbox.foreground",       C["t0"])
        self.option_add("*TCombobox*Listbox.selectBackground", C["blue"])
        self.option_add("*TCombobox*Listbox.selectForeground", C["bg0"])
        self.option_add("*TCombobox*Listbox.font",             "Consolas 10")

        self._build_style()
        self._build_header()
        self._build_notebook()
        self._build_statusbar()

        self._agent_map: dict[str, str]  = {}
        self._stock_map: dict[str, dict] = {}
        self._nc_tx_id:  int | None      = None

        self.after(400, self._refresh_all)
        self.after(60_000, self._sched_refresh)

    def _build_style(self) -> None:
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=C["bg0"], foreground=C["t0"], font=("Segoe UI", 10), borderwidth=0, relief="flat")
        s.configure("TFrame", background=C["bg0"])
        s.configure("TLabel", background=C["bg0"], foreground=C["t0"])
        s.configure("TSeparator", background=C["border"])
        s.configure("TNotebook", background=C["bg0"], borderwidth=0, tabmargins=[0, 0, 0, 0])
        s.configure("TNotebook.Tab", background=C["bg1"], foreground=C["t1"], padding=[24, 9], font=("Segoe UI", 10, "bold"))
        s.map("TNotebook.Tab", background=[("selected", C["bg2"]), ("active", C["bg2"])], foreground=[("selected", C["blue"]),  ("active", C["t0"])])
        s.configure("Treeview", background=C["bg1"], foreground=C["t0"], fieldbackground=C["bg1"], rowheight=32, font=("Consolas", 9), borderwidth=0)
        s.configure("Treeview.Heading", background=C["bg3"], foreground=C["t1"], font=("Segoe UI", 9, "bold"), relief="flat")
        s.map("Treeview", background=[("selected", C["blue"])], foreground=[("selected", C["bg0"])])
        s.map("Treeview.Heading", background=[("active", C["border"])])
        s.configure("TScrollbar", background=C["bg3"], troughcolor=C["bg1"], borderwidth=0, arrowcolor=C["t1"], gripcount=0)
        s.configure("TCombobox", background=C["bg2"], foreground=C["t0"], fieldbackground=C["bg2"], selectbackground=C["bg2"], selectforeground=C["t0"], arrowcolor=C["blue"], bordercolor=C["border"], lightcolor=C["bg2"], darkcolor=C["bg2"])
        s.map("TCombobox", fieldbackground=[("readonly", C["bg2"])], foreground=[("readonly", C["t0"])], selectforeground=[("readonly", C["t0"])], selectbackground=[("readonly", C["bg2"])])

    def _build_header(self) -> None:
        hdr = tk.Frame(self, bg=C["bg1"], height=58)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)

        left = tk.Frame(hdr, bg=C["bg1"])
        left.pack(side="left", padx=20)
        tk.Label(left, text="\U0001f6e1  EPI MANAGER", bg=C["bg1"], fg=C["blue"], font=("Segoe UI", 15, "bold")).pack(side="left")
        tk.Label(left, text="   ISOFU \u00b7 Loi 65-99 \u00b7 Architecte : Roger Fernando", bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 9)).pack(side="left")

        right = tk.Frame(hdr, bg=C["bg1"])
        right.pack(side="right", padx=20)

        tk.Button(right, text="\U0001f50d SCAN INTEGRITE DB", bg=C["orange"], fg=C["bg0"], font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2", padx=14, pady=6, command=self._run_audit).pack(side="right", padx=(6, 0))
        tk.Button(right, text="\u21ba ACTUALISER", bg=C["bg3"], fg=C["t0"], font=("Segoe UI", 9), relief="flat", cursor="hand2", padx=14, pady=6, command=self._refresh_all).pack(side="right", padx=(6, 0))

        self._clock_var = tk.StringVar()
        tk.Label(right, textvariable=self._clock_var, bg=C["bg1"], fg=C["t1"], font=("Consolas", 9)).pack(side="right", padx=(0, 14))
        self._tick()
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", side="top")

    def _tick(self) -> None:
        self._clock_var.set(time.strftime("  %a %d/%m/%Y   %H:%M:%S"))
        self.after(1000, self._tick)

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self, bg=C["bg3"], height=26)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self._status_var = tk.StringVar(value="Systeme pret.")
        tk.Label(bar, textvariable=self._status_var, bg=C["bg3"], fg=C["t1"], font=("Consolas", 8), anchor="w", padx=12).pack(fill="x", expand=True)

    def _set_status(self, msg: str) -> None:
        self._status_var.set(f"[{time.strftime('%H:%M:%S')}]  {msg}")

    def _build_notebook(self) -> None:
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill="both", expand=True)

        self._tab_dash  = ttk.Frame(self._nb)
        self._tab_alloc = ttk.Frame(self._nb)
        self._tab_nc    = ttk.Frame(self._nb)
        self._tab_cfg   = ttk.Frame(self._nb)

        self._nb.add(self._tab_dash,  text="  \U0001f4ca  TABLEAU DE BORD  ")
        self._nb.add(self._tab_alloc, text="  \U0001f4e6  ALLOCATION TERMINAL  ")
        self._nb.add(self._tab_nc,    text="  \U0001f534  NON-CONFORMITES ISO 9001  ")
        self._nb.add(self._tab_cfg,   text="  \u2699  CONFIGURATION  ")

        self._build_dashboard()
        self._build_alloc_tab()
        self._build_nc_tab()
        self._build_config_tab()

    def _build_dashboard(self) -> None:
        root = self._tab_dash
        kpi_strip = tk.Frame(root, bg=C["bg0"])
        kpi_strip.pack(fill="x", padx=20, pady=16)

        kpi_defs = [
            ("\U0001f4b0", "VALEUR STOCK TOTAL",  "vault_val",  C["green"]),
            ("\U0001f4cb", "ALLOCATIONS ACTIVES", "active",     C["blue"]),
            ("\U0001f6a8", "VIOLATIONS ISO",       "expired",    C["red"]),
            ("\u26a0",     "EXPIRE  7 JOURS",     "warn",       C["orange"]),
            ("\U0001f4c9", "STOCK CRITIQUE",       "low_stock",  C["purple"]),
        ]
        self._kpi_vars: dict[str, tk.StringVar] = {}

        for i, (icon, label, key, color) in enumerate(kpi_defs):
            kpi_strip.columnconfigure(i, weight=1)
            self._kpi_vars[key] = tk.StringVar(value="\u2014")
            card = tk.Frame(kpi_strip, bg=C["bg1"])
            card.grid(row=0, column=i, padx=5, sticky="ew")
            tk.Frame(card, bg=color, height=3).pack(fill="x")
            inner = tk.Frame(card, bg=C["bg1"], padx=16, pady=12)
            inner.pack(fill="x")
            tk.Label(inner, text=f"{icon}  {label}", bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 8, "bold")).pack(anchor="w")
            tk.Label(inner, textvariable=self._kpi_vars[key], bg=C["bg1"], fg=color, font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(4, 0))

        tk.Frame(root, bg=C["border"], height=1).pack(fill="x", padx=20, pady=(0, 6))
        sec_hdr = tk.Frame(root, bg=C["bg0"])
        sec_hdr.pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(sec_hdr, text="  ALLOCATIONS ACTIVES / EXPIRATIONS CRITIQUES", bg=C["bg0"], fg=C["t1"], font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Button(sec_hdr, text="\U0001f4c4  ETAT ALLOCATIONS PDF", bg=C["blue"], fg=C["bg0"], font=("Segoe UI", 8, "bold"), relief="flat", cursor="hand2", padx=10, pady=4, command=self._pdf_etat_alloc).pack(side="right")

        tf = tk.Frame(root, bg=C["bg0"])
        tf.pack(fill="both", expand=True, padx=20, pady=(4, 14))
        cols = ("tx_id", "agent", "ppe", "lot", "chantier", "emis", "expire", "jours", "statut")
        self._dash_tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse")

        _col_cfg = [
            ("tx_id",    "TX-ID",        72, "center"), ("agent",    "AGENT",        170, "w"),
            ("ppe",      "EPI",          235, "w"), ("lot",      "LOT N\u00b0",  118, "center"),
            ("chantier", "CHANTIER",     108, "w"), ("emis",     "\u00c9MIS",     90, "center"),
            ("expire",   "EXPIRE",        90, "center"), ("jours",    "JOURS REST.",   82, "center"),
            ("statut",   "STATUT",       105, "center"),
        ]
        for col, hdr_txt, w, anch in _col_cfg:
            self._dash_tree.heading(col, text=hdr_txt)
            self._dash_tree.column(col, width=w, minwidth=50, anchor=anch)

        self._dash_tree.tag_configure("Compliant", foreground=C["green"])
        self._dash_tree.tag_configure("Expired",   foreground=C["red"])
        self._dash_tree.tag_configure("Degraded",  foreground=C["orange"])

        vsb = ttk.Scrollbar(tf, orient="vertical", command=self._dash_tree.yview)
        hsb = ttk.Scrollbar(tf, orient="horizontal", command=self._dash_tree.xview)
        self._dash_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._dash_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

    def _refresh_dashboard(self) -> None:
        run_audit_scanner()
        kpis = _get_kpis()
        self._kpi_vars["vault_val"].set(_fmt_mad(kpis["vault_val_centimes"]))
        self._kpi_vars["active"].set(str(kpis["active_count"]))
        self._kpi_vars["expired"].set(str(kpis["expired_count"]))
        self._kpi_vars["warn"].set(str(kpis["warn_count"]))
        self._kpi_vars["low_stock"].set(str(kpis["low_stock_count"]))

        self._dash_tree.delete(*self._dash_tree.get_children())
        now = int(time.time())
        for row in _get_active_allocs():
            emis = time.strftime("%d/%m/%Y", time.localtime(row["timestamp_issued"]))
            expire = time.strftime("%d/%m/%Y", time.localtime(row["expected_death_timestamp"]))
            days = (row["expected_death_timestamp"] - now) // 86400
            days_s = str(days) if days > 0 else "EXPIRE"
            status = row["status"]
            self._dash_tree.insert("", "end", tags=(status,), values=(f"TX-{row['tx_id']:06d}", row["agent_name"], row["ppe_desc"], row["lot_number"], row["chantier_location"], emis, expire, days_s, status.upper()))
        n = len(self._dash_tree.get_children())
        self._set_status(f"Dashboard actualise \u2014 {n} allocation(s) active(s).")

    def _build_alloc_tab(self) -> None:
        root = self._tab_alloc
        outer = tk.Frame(root, bg=C["bg0"])
        outer.pack(fill="both", expand=True)
        card = tk.Frame(outer, bg=C["bg1"], padx=48, pady=40)
        card.pack(expand=True)

        tk.Label(card, text="\U0001f4e6  ALLOCATION TERMINAL", bg=C["bg1"], fg=C["blue"], font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(card, text="Tous les champs sont obligatoires. L'action decremente le stock immediatement.", bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 18))
        tk.Frame(card, bg=C["border"], height=1).pack(fill="x", pady=(0, 22))

        tk.Label(card, text="AGENT  (actifs uniquement)", bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self._agent_var = tk.StringVar()
        self._agent_combo = ttk.Combobox(card, textvariable=self._agent_var, state="readonly", width=64, font=("Consolas", 10))
        self._agent_combo.pack(anchor="w", pady=(4, 18), ipady=6)

        tk.Label(card, text="EPI + LOT  (stock disponible uniquement)", bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self._stock_var = tk.StringVar()
        self._stock_combo = ttk.Combobox(card, textvariable=self._stock_var, state="readonly", width=64, font=("Consolas", 10))
        self._stock_combo.pack(anchor="w", pady=(4, 0), ipady=6)
        self._stock_combo.bind("<<ComboboxSelected>>", self._on_stock_selected)

        self._stock_detail_var = tk.StringVar(value="")
        tk.Label(card, textvariable=self._stock_detail_var, bg=C["bg1"], fg=C["t1"], font=("Consolas", 8)).pack(anchor="w", pady=(3, 18))

        tk.Label(card, text="CHANTIER / SITE  (min 3 caracteres)", bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self._chantier_var = tk.StringVar()
        tk.Entry(card, textvariable=self._chantier_var, bg=C["bg2"], fg=C["t0"], insertbackground=C["blue"], font=("Consolas", 11), relief="flat", bd=1, width=64).pack(anchor="w", pady=(4, 22), ipady=8)

        self._alloc_err_var = tk.StringVar(value="")
        tk.Label(card, textvariable=self._alloc_err_var, bg=C["bg1"], fg=C["red"], font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 10))

        self._alloc_btn = tk.Button(card, text="\u2705  EMETTRE L'EQUIPEMENT", bg=C["green"], fg=C["bg0"], font=("Segoe UI", 14, "bold"), relief="flat", cursor="hand2", padx=20, pady=20, width=44, command=self._do_allocate)
        self._alloc_btn.pack(fill="x", pady=(0, 6))
        tk.Label(card, text="Action irreversible \u2014 decremente le stock a l'emission.", bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 7, "italic")).pack()

    def _refresh_alloc_tab(self) -> None:
        self._agent_map = {}
        labels_a = []
        for a in _get_active_agents():
            lbl = f"{a['agent_id']}  \u2500  {a['full_name']}  ({a['job_class']})"
            self._agent_map[lbl] = a["agent_id"]
            labels_a.append(lbl)
        self._agent_combo["values"] = labels_a
        if labels_a: self._agent_combo.current(0)

        self._stock_map = {}
        labels_s = []
        for s in _get_available_stocks():
            cost = _fmt_mad(s["unit_cost_centimes"])
            lbl = f"[{s['category'][:11]:<11}]  {s['description'][:30]:<30}  LOT:{s['lot_number']:<18}  QTY:{s['qty']:<5} {cost}"
            self._stock_map[lbl] = {"stock_id": s["stock_id"], "description": s["description"], "lifespan_days": s["lifespan_days"], "unit_cost_centimes": s["unit_cost_centimes"], "qty": s["qty"], "lot_number": s["lot_number"]}
            labels_s.append(lbl)
        self._stock_combo["values"] = labels_s
        if labels_s:
            self._stock_combo.current(0)
            self._on_stock_selected(None)
        else:
            self._stock_detail_var.set("")
        self._alloc_err_var.set("")

    def _on_stock_selected(self, _event) -> None:
        lbl = self._stock_var.get()
        if lbl in self._stock_map:
            s = self._stock_map[lbl]
            self._stock_detail_var.set(f"  Duree de vie : {s['lifespan_days']} jour(s)   \u00b7   Cout unitaire : {_fmt_mad(s['unit_cost_centimes'])}   \u00b7   Stock restant : {s['qty']}")
        else:
            self._stock_detail_var.set("")

    def _do_allocate(self) -> None:
        self._alloc_btn.config(state="disabled", text="\u23f3  TRAITEMENT EN COURS\u2026")
        self.update()
        try:
            agent_lbl = self._agent_var.get().strip()
            stock_lbl = self._stock_var.get().strip()
            chantier  = self._chantier_var.get().strip()

            if not agent_lbl or agent_lbl not in self._agent_map: return self._alloc_err_var.set("\u274c Selectionnez un agent valide.")
            if not stock_lbl or stock_lbl not in self._stock_map: return self._alloc_err_var.set("\u274c Selectionnez un EPI valide.")
            if len(chantier) < 3: return self._alloc_err_var.set("\u274c Chantier invalide (min 3 caracteres).")

            ok, msg = allocate_ppe(self._agent_map[agent_lbl], self._stock_map[stock_lbl]["stock_id"], chantier)
            if ok:
                self._alloc_err_var.set("")
                m_tx = re.search(r'TX-(\d+)', msg)
                tx_num = int(m_tx.group(1)) if m_tx else None
                messagebox.showinfo("\u2705 ALLOCATION REUSSIE", msg, parent=self)
                self._chantier_var.set("")
                self._refresh_all()
                if tx_num is not None and messagebox.askyesno("\U0001f4c4 BON D'ATTRIBUTION", f"Generer et ouvrir le bon d'attribution PDF\npour TX-{tx_num:06d} ?", parent=self):
                    ok_p, res_p = pdf_bon_allocation(tx_num)
                    if ok_p:
                        _open_pdf(res_p)
                        self._set_status(f"Bon PDF genere \u2014 {res_p}")
                    else:
                        messagebox.showerror("Erreur PDF", res_p, parent=self)
            else:
                self._alloc_err_var.set(f"\u274c {msg}")
                self._set_status(f"Allocation refusee : {msg}")
        finally:
            self._alloc_btn.config(state="normal", text="\u2705  EMETTRE L'EQUIPEMENT")

    def _build_nc_tab(self) -> None:
        root = self._tab_nc
        tk.Label(root, text="  ALLOCATIONS ACTIVES \u2014 SELECTIONNEZ PUIS TERMINEZ EN NON-CONFORMITE", bg=C["bg0"], fg=C["t1"], font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=20, pady=(14, 4))
        tf = tk.Frame(root, bg=C["bg0"])
        tf.pack(fill="both", expand=True, padx=20)
        cols = ("tx_id", "agent", "ppe", "lot", "chantier", "emis", "expire", "statut")
        self._nc_tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse")
        _col_cfg = [
            ("tx_id", "TX-ID", 72, "center"), ("agent", "AGENT", 170, "w"),
            ("ppe", "EPI", 235, "w"), ("lot", "LOT N\u00b0", 118, "center"),
            ("chantier", "CHANTIER", 108, "w"), ("emis", "\u00c9MIS", 90, "center"),
            ("expire", "EXPIRE", 90, "center"), ("statut", "STATUT", 105, "center"),
        ]
        for col, hdr_txt, w, anch in _col_cfg:
            self._nc_tree.heading(col, text=hdr_txt)
            self._nc_tree.column(col, width=w, minwidth=50, anchor=anch)
        self._nc_tree.tag_configure("Compliant", foreground=C["green"])
        self._nc_tree.tag_configure("Expired", foreground=C["red"])
        self._nc_tree.tag_configure("Degraded", foreground=C["orange"])
        self._nc_tree.bind("<<TreeviewSelect>>", self._on_nc_select)

        vsb = ttk.Scrollbar(tf, orient="vertical", command=self._nc_tree.yview)
        hsb = ttk.Scrollbar(tf, orient="horizontal", command=self._nc_tree.xview)
        self._nc_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._nc_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

        ctrl = tk.Frame(root, bg=C["bg2"], padx=20, pady=16)
        ctrl.pack(fill="x", padx=20, pady=(10, 16))
        tx_row = tk.Frame(ctrl, bg=C["bg2"])
        tx_row.pack(fill="x", pady=(0, 10))
        tk.Label(tx_row, text="SELECTION :", bg=C["bg2"], fg=C["t1"], font=("Segoe UI", 8, "bold")).pack(side="left")
        self._nc_sel_var = tk.StringVar(value="(aucune)")
        tk.Label(tx_row, textvariable=self._nc_sel_var, bg=C["bg2"], fg=C["blue"], font=("Consolas", 10, "bold")).pack(side="left", padx=(10, 0))

        reason_row = tk.Frame(ctrl, bg=C["bg2"])
        reason_row.pack(fill="x", pady=(0, 14))
        tk.Label(reason_row, text="MOTIF NON-CONFORMITE :", bg=C["bg2"], fg=C["t1"], font=("Segoe UI", 8, "bold")).pack(side="left")
        self._nc_reason_var = tk.StringVar()
        nc_cb = ttk.Combobox(reason_row, textvariable=self._nc_reason_var, values=["Dechirure Mecanique", "Saturation Solvant", "Perte / Vol", "Defaut Fabricant", "Rupture Structure", "Brulure / Projection", "Duree de Vie Atteinte", "Degradation Visuelle", "Non-Conformite Reception Lot", "Autre (voir rapport)"], state="readonly", width=38, font=("Consolas", 10))
        nc_cb.pack(side="left", padx=(10, 0), ipady=4)
        nc_cb.current(0)

        pdf_nc_row = tk.Frame(ctrl, bg=C["bg2"])
        pdf_nc_row.pack(fill="x", pady=(0, 8))
        tk.Button(pdf_nc_row, text="\U0001f4c4  JOURNAL NC PDF", bg=C["purple"], fg=C["bg0"], font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2", padx=12, pady=10, command=self._pdf_journal_nc).pack(side="right")
        tk.Button(ctrl, text="\U0001f534  TERMINER & ENREGISTRER LA NON-CONFORMITE", bg=C["red"], fg="#FFFFFF", font=("Segoe UI", 11, "bold"), relief="flat", cursor="hand2", padx=16, pady=13, command=self._do_scrap).pack(fill="x")

    def _refresh_nc_tab(self) -> None:
        self._nc_tree.delete(*self._nc_tree.get_children())
        self._nc_tx_id = None
        self._nc_sel_var.set("(aucune)")
        for row in _get_active_allocs():
            emis = time.strftime("%d/%m/%Y", time.localtime(row["timestamp_issued"]))
            expire = time.strftime("%d/%m/%Y", time.localtime(row["expected_death_timestamp"]))
            status = row["status"]
            self._nc_tree.insert("", "end", iid=str(row["tx_id"]), tags=(status,), values=(f"TX-{row['tx_id']:06d}", row["agent_name"], row["ppe_desc"], row["lot_number"], row["chantier_location"], emis, expire, status.upper()))

    def _on_nc_select(self, _event) -> None:
        sel = self._nc_tree.selection()
        if not sel:
            self._nc_tx_id = None
            self._nc_sel_var.set("(aucune)")
            return
        try:
            self._nc_tx_id = int(sel[0])
            vals = self._nc_tree.item(sel[0], "values")
            self._nc_sel_var.set(f"{vals[0]}  \u2500  {vals[1]}  \u2500  {vals[2][:42]}")
        except (ValueError, IndexError):
            self._nc_tx_id = None
            self._nc_sel_var.set("(erreur selection)")

    def _do_scrap(self) -> None:
        if self._nc_tx_id is None: return messagebox.showwarning("Selection requise", "Selectionnez une allocation dans la liste.", parent=self)
        reason = self._nc_reason_var.get().strip()
        if not reason: return messagebox.showwarning("Motif requis", "Selectionnez un motif de non-conformite.", parent=self)
        vals = self._nc_tree.item(str(self._nc_tx_id), "values")
        tx_disp = vals[0] if vals else f"TX-{self._nc_tx_id:06d}"
        if not messagebox.askyesno("\u26a0 CONFIRMATION NON-CONFORMITE", f"Terminer l'allocation {tx_disp} ?\n\nMotif : {reason}\n\nCette action est irreversible et sera tracee\ndans le registre ISO 9001 / PDCA.", icon="warning", parent=self): return
        ok, msg = scrap_allocation(self._nc_tx_id, reason)
        if ok:
            messagebox.showinfo("\u2705 NON-CONFORMITE ENREGISTREE", msg, parent=self)
            self._nc_tx_id = None
            self._nc_sel_var.set("(aucune)")
            self._refresh_all()
        else:
            messagebox.showerror("\u274c ERREUR", msg, parent=self)
            self._set_status(f"Erreur scrap : {msg}")

    def _run_audit(self) -> None:
        self._set_status("Scan integrite HMAC en cours\u2026")
        self.update()
        anomalies = run_audit_scanner()
        self._refresh_all()
        if not anomalies:
            messagebox.showinfo("\u2705 BASE INTEGRE", "Scan HMAC termine.\n\nToutes les signatures cryptographiques sont valides.\nAucune falsification ni expiration non traitee detectee.", parent=self)
            self._set_status("Scan HMAC \u2014 base integre, aucune anomalie.")
        else:
            lines = [f"\u26a0  {len(anomalies)} ANOMALIE(S) DETECTEE(S)\n"]
            for a in anomalies:
                lines.append(f"  TX-{a['tx_id']:06d}  [{a['status']}]")
                for issue in a["issues"]: lines.append(f"    \u26d4 {issue}")
                lines.append("")
            messagebox.showwarning("\u26d4 ANOMALIES DETECTEES", "\n".join(lines), parent=self)
            self._set_status(f"Scan HMAC \u2014 {len(anomalies)} anomalie(s). Action requise.")

    def _pdf_etat_alloc(self) -> None:
        self._set_status("Generation PDF etat allocations\u2026")
        self.update()
        ok, res = pdf_etat_allocations()
        if ok:
            self._set_status(f"PDF genere \u2014 {res}")
            if messagebox.askyesno("\U0001f4c4 PDF pret", f"Etat des allocations genere.\n\nOuvrir le fichier ?\n{res}", parent=self): _open_pdf(res)
        else:
            messagebox.showwarning("PDF", res, parent=self)
            self._set_status(f"PDF annule : {res}")

    def _pdf_journal_nc(self) -> None:
        self._set_status("Generation PDF journal NC\u2026")
        self.update()
        ok, res = pdf_journal_nc()
        if ok:
            self._set_status(f"PDF genere \u2014 {res}")
            if messagebox.askyesno("\U0001f4c4 PDF pret", f"Journal non-conformites genere.\n\nOuvrir le fichier ?\n{res}", parent=self): _open_pdf(res)
        else:
            messagebox.showwarning("PDF", res, parent=self)
            self._set_status(f"PDF annule : {res}")

    def _pdf_inventaire(self) -> None:
        self._set_status("Generation PDF inventaire\u2026")
        self.update()
        ok, res = pdf_inventaire_vault()
        if ok:
            self._set_status(f"PDF genere \u2014 {res}")
            if messagebox.askyesno("\U0001f4c4 PDF pret", f"Inventaire Vault genere.\n\nOuvrir le fichier ?\n{res}", parent=self): _open_pdf(res)
        else:
            messagebox.showwarning("PDF", res, parent=self)
            self._set_status(f"PDF annule : {res}")

    def _refresh_all(self) -> None:
        self._refresh_dashboard()
        self._refresh_alloc_tab()
        self._refresh_nc_tab()
        self._refresh_config_tab()

    def _sched_refresh(self) -> None:
        self._refresh_all()
        self.after(60_000, self._sched_refresh)

    def _build_config_tab(self) -> None:
        root = self._tab_cfg
        cfg_nb = ttk.Notebook(root)
        cfg_nb.pack(fill="both", expand=True)
        self._tab_cfg_agents  = ttk.Frame(cfg_nb)
        self._tab_cfg_arsenal = ttk.Frame(cfg_nb)
        self._tab_cfg_stock   = ttk.Frame(cfg_nb)
        cfg_nb.add(self._tab_cfg_agents,  text="  \U0001f464  AGENTS  ")
        cfg_nb.add(self._tab_cfg_arsenal, text="  \U0001f5c2  ARSENAL EPI  ")
        cfg_nb.add(self._tab_cfg_stock,   text="  \U0001f4e6  STOCK / LOTS  ")
        self._build_cfg_agents()
        self._build_cfg_arsenal()
        self._build_cfg_stock()

    def _build_cfg_agents(self) -> None:
        root = self._tab_cfg_agents
        hdr = tk.Frame(root, bg=C["bg0"])
        hdr.pack(fill="x", padx=20, pady=(14, 6))
        tk.Label(hdr, text="\U0001f464  GESTION DES AGENTS", bg=C["bg0"], fg=C["blue"], font=("Segoe UI", 11, "bold")).pack(side="left")
        btns = tk.Frame(hdr, bg=C["bg0"])
        btns.pack(side="right")
        tk.Button(btns, text="\u270f  MODIFIER STATUT", bg=C["orange"], fg=C["bg0"], font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2", padx=12, pady=6, command=self._open_status_wizard).pack(side="right", padx=(6, 0))
        tk.Button(btns, text="\uff0b  NOUVEL AGENT", bg=C["green"], fg=C["bg0"], font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2", padx=12, pady=6, command=self._open_agent_wizard).pack(side="right")
        tk.Frame(root, bg=C["border"], height=1).pack(fill="x", padx=20, pady=(0, 6))
        tf = tk.Frame(root, bg=C["bg0"])
        tf.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        cols = ("agent_id", "cin", "full_name", "job_class", "suit", "boot", "status")
        self._cfg_agents_tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse")
        for col, h, w, anch in [("agent_id", "ID AGENT", 90, "center"), ("cin", "CIN", 110, "center"), ("full_name", "NOM COMPLET", 200, "w"), ("job_class", "POSTE", 165, "w"), ("suit", "TAILLE", 75, "center"), ("boot", "POINTURE", 80, "center"), ("status", "STATUT", 105, "center")]:
            self._cfg_agents_tree.heading(col, text=h)
            self._cfg_agents_tree.column(col, width=w, minwidth=40, anchor=anch)
        self._cfg_agents_tree.tag_configure("Active", foreground=C["green"])
        self._cfg_agents_tree.tag_configure("Suspended", foreground=C["orange"])
        self._cfg_agents_tree.tag_configure("Terminated", foreground=C["red"])
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self._cfg_agents_tree.yview)
        self._cfg_agents_tree.configure(yscrollcommand=vsb.set)
        self._cfg_agents_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

    def _build_cfg_arsenal(self) -> None:
        root = self._tab_cfg_arsenal
        hdr = tk.Frame(root, bg=C["bg0"])
        hdr.pack(fill="x", padx=20, pady=(14, 6))
        tk.Label(hdr, text="\U0001f5c2  CATALOGUE ARSENAL EPI", bg=C["bg0"], fg=C["blue"], font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Button(hdr, text="\uff0b  NOUVEL EPI", bg=C["green"], fg=C["bg0"], font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2", padx=12, pady=6, command=self._open_arsenal_wizard).pack(side="right")
        tk.Frame(root, bg=C["border"], height=1).pack(fill="x", padx=20, pady=(0, 6))
        tf = tk.Frame(root, bg=C["bg0"])
        tf.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        cols = ("code", "cat", "desc", "life", "cost")
        self._cfg_arsenal_tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse")
        for col, h, w, anch in [("code", "CODE EPI", 120, "center"), ("cat", "CATEGORIE", 120, "w"), ("desc", "DESCRIPTION", 300, "w"), ("life", "DUREE (j)", 80, "center"), ("cost", "COUT UNIT.", 115, "center")]:
            self._cfg_arsenal_tree.heading(col, text=h)
            self._cfg_arsenal_tree.column(col, width=w, minwidth=40, anchor=anch)
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self._cfg_arsenal_tree.yview)
        self._cfg_arsenal_tree.configure(yscrollcommand=vsb.set)
        self._cfg_arsenal_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

    def _build_cfg_stock(self) -> None:
        root = self._tab_cfg_stock
        hdr = tk.Frame(root, bg=C["bg0"])
        hdr.pack(fill="x", padx=20, pady=(14, 6))
        tk.Label(hdr, text="\U0001f4e6  INVENTAIRE STOCK / LOTS", bg=C["bg0"], fg=C["blue"], font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Button(hdr, text="\uff0b  NOUVELLE RECEPTION", bg=C["blue"], fg=C["bg0"], font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2", padx=12, pady=6, command=self._open_stock_wizard).pack(side="right")
        tk.Button(hdr, text="\U0001f4c4  INVENTAIRE PDF", bg=C["purple"], fg=C["bg0"], font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2", padx=12, pady=6, command=self._pdf_inventaire).pack(side="right", padx=(0, 6))
        tk.Frame(root, bg=C["border"], height=1).pack(fill="x", padx=20, pady=(0, 6))
        tf = tk.Frame(root, bg=C["bg0"])
        tf.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        cols = ("stock_id", "code", "desc", "lot", "qty", "seuil", "etat")
        self._cfg_stock_tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse")
        for col, h, w, anch in [("stock_id", "ID", 55, "center"), ("code", "CODE EPI", 110, "center"), ("desc", "EPI", 250, "w"), ("lot", "LOT N\u00b0", 145, "center"), ("qty", "QTY", 65, "center"), ("seuil", "SEUIL", 65, "center"), ("etat", "ETAT", 90, "center")]:
            self._cfg_stock_tree.heading(col, text=h)
            self._cfg_stock_tree.column(col, width=w, minwidth=40, anchor=anch)
        self._cfg_stock_tree.tag_configure("OK", foreground=C["green"])
        self._cfg_stock_tree.tag_configure("LOW", foreground=C["orange"])
        self._cfg_stock_tree.tag_configure("RUPTURE", foreground=C["red"])
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self._cfg_stock_tree.yview)
        self._cfg_stock_tree.configure(yscrollcommand=vsb.set)
        self._cfg_stock_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

    def _refresh_config_tab(self) -> None:
        self._refresh_cfg_agents()
        self._refresh_cfg_arsenal()
        self._refresh_cfg_stock()

    def _refresh_cfg_agents(self) -> None:
        self._cfg_agents_tree.delete(*self._cfg_agents_tree.get_children())
        for a in _get_all_agents():
            self._cfg_agents_tree.insert("", "end", iid=a["agent_id"], tags=(a["status"],), values=(a["agent_id"], a["cin"], a["full_name"], a["job_class"], a["suit_size"], a["boot_size"], a["status"]))

    def _refresh_cfg_arsenal(self) -> None:
        self._cfg_arsenal_tree.delete(*self._cfg_arsenal_tree.get_children())
        for r in _get_all_arsenal():
            self._cfg_arsenal_tree.insert("", "end", values=(r["ppe_code"], r["category"], r["description"], r["lifespan_days"], _fmt_mad(r["unit_cost_centimes"])))

    def _refresh_cfg_stock(self) -> None:
        self._cfg_stock_tree.delete(*self._cfg_stock_tree.get_children())
        for r in _get_all_vault():
            qty, seuil = r["qty"], r["min_threshold"]
            if qty == 0: tag, etat = "RUPTURE", "RUPTURE"
            elif qty <= seuil: tag, etat = "LOW", "ALERTE"
            else: tag, etat = "OK", "OK"
            self._cfg_stock_tree.insert("", "end", tags=(tag,), values=(r["stock_id"], r["ppe_code"], r["description"], r["lot_number"], qty, seuil, etat))

    @staticmethod
    def _make_modal(parent: tk.Misc, title: str, width: int, height: int) -> tk.Toplevel:
        win = tk.Toplevel(parent)
        win.title(title)
        win.configure(bg=C["bg1"])
        win.resizable(False, False)
        win.grab_set()
        win.focus_set()
        parent.update_idletasks()
        px = parent.winfo_x() + (parent.winfo_width()  - width)  // 2
        py = parent.winfo_y() + (parent.winfo_height() - height) // 2
        win.geometry(f"{width}x{height}+{px}+{py}")
        return win

    @staticmethod
    def _wz_entry(parent: tk.Widget, var: tk.StringVar, width: int = 38) -> tk.Entry:
        return tk.Entry(parent, textvariable=var, bg=C["bg2"], fg=C["t0"], insertbackground=C["blue"], font=("Consolas", 11), relief="flat", bd=1, width=width)

    @staticmethod
    def _wz_label(parent: tk.Widget, text: str) -> None:
        tk.Label(parent, text=text, bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 8, "bold")).pack(anchor="w")

    @staticmethod
    def _wz_btn_submit(parent: tk.Widget, text: str, color: str, cmd) -> tk.Button:
        return tk.Button(parent, text=text, bg=color, fg=C["bg0"], font=("Segoe UI", 12, "bold"), relief="flat", cursor="hand2", pady=14, command=cmd)

    def _open_agent_wizard(self) -> None:
        win = self._make_modal(self, "Nouvel Agent", 530, 640)
        tk.Label(win, text="\U0001f464  NOUVEL AGENT", bg=C["bg1"], fg=C["blue"], font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=32, pady=(24, 2))
        tk.Label(win, text="Tous les champs sont obligatoires.", bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 8)).pack(anchor="w", padx=32)
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x", padx=32, pady=(12, 20))

        form = tk.Frame(win, bg=C["bg1"])
        form.pack(fill="x", padx=32)
        v_id   = tk.StringVar(value="AGT-")
        v_cin  = tk.StringVar()
        v_name = tk.StringVar()
        v_job  = tk.StringVar(value="Peintre Industriel")
        v_suit = tk.StringVar(value="L")
        v_boot = tk.StringVar(value="42")

        for lbl, var in [("IDENTIFIANT  (ex: AGT-007)", v_id), ("CIN  (ex: AB123456)", v_cin), ("NOM COMPLET", v_name)]:
            self._wz_label(form, lbl)
            self._wz_entry(form, var).pack(anchor="w", pady=(3, 12), ipady=6)

        self._wz_label(form, "CLASSE / POSTE")
        ttk.Combobox(form, textvariable=v_job, values=["Peintre Industriel", "Chef d'Equipe", "Sableur", "Operateur Cabine", "Technicien Maintenance", "Agent Securite", "Magasinier", "Autre"], font=("Consolas", 10), width=36).pack(anchor="w", pady=(3, 12), ipady=5)

        row2 = tk.Frame(form, bg=C["bg1"])
        row2.pack(anchor="w", fill="x", pady=(0, 12))
        lf = tk.Frame(row2, bg=C["bg1"])
        lf.pack(side="left", padx=(0, 24))
        self._wz_label(lf, "TAILLE COMBINAISON")
        ttk.Combobox(lf, textvariable=v_suit, values=["XS","S","M","L","XL","XXL"], state="readonly", font=("Consolas", 11), width=8).pack(anchor="w", pady=(3, 0), ipady=5)
        rf = tk.Frame(row2, bg=C["bg1"])
        rf.pack(side="left")
        self._wz_label(rf, "POINTURE  (36-48)")
        self._wz_entry(rf, v_boot, width=8).pack(anchor="w", pady=(3, 0), ipady=6)

        err_var = tk.StringVar()
        tk.Label(win, textvariable=err_var, bg=C["bg1"], fg=C["red"], font=("Segoe UI", 9)).pack(anchor="w", padx=32, pady=(8, 4))

        def _submit():
            try: boot = int(v_boot.get().strip())
            except ValueError: return err_var.set("\u274c Pointure doit etre un entier.")
            ok, msg = add_agent(v_id.get(), v_cin.get(), v_name.get(), v_job.get(), v_suit.get(), boot)
            if ok:
                messagebox.showinfo("\u2705 Agent enregistre", msg, parent=win)
                win.destroy()
                self._refresh_all()
            else: err_var.set(f"\u274c {msg}")

        self._wz_btn_submit(win, "\u2705  ENREGISTRER L'AGENT", C["green"], _submit).pack(fill="x", padx=32, pady=(0, 6))
        tk.Button(win, text="ANNULER", bg=C["bg3"], fg=C["t1"], font=("Segoe UI", 9), relief="flat", cursor="hand2", pady=8, command=win.destroy).pack(fill="x", padx=32)

    def _open_status_wizard(self) -> None:
        sel = self._cfg_agents_tree.selection()
        if not sel: return messagebox.showwarning("Selection requise", "Selectionnez un agent dans la liste.", parent=self)
        vals     = self._cfg_agents_tree.item(sel[0], "values")
        agent_id = vals[0]
        cur_stat = vals[6]

        win = self._make_modal(self, f"Statut — {agent_id}", 400, 320)
        tk.Label(win, text="\u270f  MODIFIER STATUT", bg=C["bg1"], fg=C["orange"], font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=32, pady=(24, 2))
        tk.Label(win, text=f"Agent : {vals[2]}", bg=C["bg1"], fg=C["t0"], font=("Consolas", 10)).pack(anchor="w", padx=32, pady=(0, 2))
        tk.Label(win, text=f"Statut actuel : {cur_stat}", bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 9)).pack(anchor="w", padx=32)
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x", padx=32, pady=(14, 20))

        form = tk.Frame(win, bg=C["bg1"])
        form.pack(fill="x", padx=32)
        self._wz_label(form, "NOUVEAU STATUT")
        v_status = tk.StringVar(value=cur_stat)
        ttk.Combobox(form, textvariable=v_status, values=["Active", "Suspended", "Terminated"], state="readonly", font=("Consolas", 11), width=20).pack(anchor="w", pady=(3, 0), ipady=5)

        err_var = tk.StringVar()
        tk.Label(win, textvariable=err_var, bg=C["bg1"], fg=C["red"], font=("Segoe UI", 9)).pack(anchor="w", padx=32, pady=(10, 4))

        def _submit():
            ok, msg = update_agent_status(agent_id, v_status.get())
            if ok:
                messagebox.showinfo("\u2705 Statut modifie", msg, parent=win)
                win.destroy()
                self._refresh_all()
            else: err_var.set(f"\u274c {msg}")

        self._wz_btn_submit(win, "\u2705  CONFIRMER", C["orange"], _submit).pack(fill="x", padx=32, pady=(0, 6))
        tk.Button(win, text="ANNULER", bg=C["bg3"], fg=C["t1"], font=("Segoe UI", 9), relief="flat", cursor="hand2", pady=8, command=win.destroy).pack(fill="x", padx=32)

    def _open_arsenal_wizard(self) -> None:
        win = self._make_modal(self, "Nouvel EPI — Catalogue Arsenal", 530, 590)
        tk.Label(win, text="\U0001f5c2  NOUVEL EPI AU CATALOGUE", bg=C["bg1"], fg=C["blue"], font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=32, pady=(24, 2))
        tk.Label(win, text="Cout saisi en MAD (ex: 85.50) \u2014 converti en centimes sans float.", bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 8)).pack(anchor="w", padx=32)
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x", padx=32, pady=(12, 20))

        form = tk.Frame(win, bg=C["bg1"])
        form.pack(fill="x", padx=32)
        v_code = tk.StringVar(value="PPE-")
        v_cat  = tk.StringVar(value="Respiratoire")
        v_desc = tk.StringVar()
        v_life = tk.StringVar(value="365")
        v_cost = tk.StringVar()

        self._wz_label(form, "CODE EPI  (ex: PPE-RES-003)")
        self._wz_entry(form, v_code).pack(anchor="w", pady=(3, 12), ipady=6)
        self._wz_label(form, "CATEGORIE")
        ttk.Combobox(form, textvariable=v_cat, values=["Respiratoire","Oeil","Tete","Tenue","Pied","Main","Ouie","Anti-Chute","Visibilite","Autre"], font=("Consolas", 10), width=36).pack(anchor="w", pady=(3, 12), ipady=5)
        self._wz_label(form, "DESCRIPTION COMPLETE")
        self._wz_entry(form, v_desc).pack(anchor="w", pady=(3, 12), ipady=6)

        row2 = tk.Frame(form, bg=C["bg1"])
        row2.pack(anchor="w", fill="x")
        lf = tk.Frame(row2, bg=C["bg1"])
        lf.pack(side="left", padx=(0, 24))
        self._wz_label(lf, "DUREE DE VIE (jours)")
        self._wz_entry(lf, v_life, width=12).pack(anchor="w", pady=(3, 0), ipady=6)
        rf = tk.Frame(row2, bg=C["bg1"])
        rf.pack(side="left")
        self._wz_label(rf, "COUT UNITAIRE (MAD)")
        self._wz_entry(rf, v_cost, width=18).pack(anchor="w", pady=(3, 0), ipady=6)

        err_var = tk.StringVar()
        tk.Label(win, textvariable=err_var, bg=C["bg1"], fg=C["red"], font=("Segoe UI", 9)).pack(anchor="w", padx=32, pady=(10, 4))

        def _submit():
            try: life = int(v_life.get().strip())
            except ValueError: return err_var.set("\u274c Duree de vie invalide (entier requis).")
            ok_c, centimes = _parse_mad_input(v_cost.get())
            if not ok_c: return err_var.set("\u274c Cout invalide (ex: 85.50).")
            ok, msg = add_ppe_type(v_code.get(), v_cat.get(), v_desc.get(), life, centimes)
            if ok:
                messagebox.showinfo("\u2705 EPI enregistre", msg, parent=win)
                win.destroy()
                self._refresh_all()
            else: err_var.set(f"\u274c {msg}")

        self._wz_btn_submit(win, "\u2705  AJOUTER AU CATALOGUE", C["green"], _submit).pack(fill="x", padx=32, pady=(0, 6))
        tk.Button(win, text="ANNULER", bg=C["bg3"], fg=C["t1"], font=("Segoe UI", 9), relief="flat", cursor="hand2", pady=8, command=win.destroy).pack(fill="x", padx=32)

    def _open_stock_wizard(self) -> None:
        arsenal = _get_all_arsenal()
        if not arsenal: return messagebox.showwarning("Catalogue vide", "Aucune reference EPI dans le catalogue.\nAjoutez d'abord un EPI via l'onglet Arsenal EPI.", parent=self)
        ppe_labels  = [f"{r['ppe_code']}  \u2014  {r['description']}" for r in arsenal]
        code_map    = {lbl: r["ppe_code"] for lbl, r in zip(ppe_labels, arsenal)}

        win = self._make_modal(self, "Nouvelle Reception Stock", 530, 490)
        tk.Label(win, text="\U0001f4e6  RECEPTION STOCK", bg=C["bg1"], fg=C["blue"], font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=32, pady=(24, 2))
        tk.Label(win, text="Enregistrement d'un nouveau lot recu en magasin.", bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 8)).pack(anchor="w", padx=32)
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x", padx=32, pady=(12, 20))

        form = tk.Frame(win, bg=C["bg1"])
        form.pack(fill="x", padx=32)
        v_ppe   = tk.StringVar(value=ppe_labels[0])
        v_lot   = tk.StringVar()
        v_qty   = tk.StringVar(value="100")
        v_seuil = tk.StringVar(value="10")

        self._wz_label(form, "REFERENCE EPI")
        ttk.Combobox(form, textvariable=v_ppe, values=ppe_labels, state="readonly", font=("Consolas", 10), width=52).pack(anchor="w", pady=(3, 12), ipady=5)
        self._wz_label(form, "NUMERO DE LOT  (ex: LOT-2025-MA-003)")
        self._wz_entry(form, v_lot).pack(anchor="w", pady=(3, 12), ipady=6)

        row2 = tk.Frame(form, bg=C["bg1"])
        row2.pack(anchor="w", fill="x")
        lf = tk.Frame(row2, bg=C["bg1"])
        lf.pack(side="left", padx=(0, 24))
        self._wz_label(lf, "QUANTITE RECUE")
        self._wz_entry(lf, v_qty, width=12).pack(anchor="w", pady=(3, 0), ipady=6)
        rf = tk.Frame(row2, bg=C["bg1"])
        rf.pack(side="left")
        self._wz_label(rf, "SEUIL D'ALERTE")
        self._wz_entry(rf, v_seuil, width=12).pack(anchor="w", pady=(3, 0), ipady=6)

        err_var = tk.StringVar()
        tk.Label(win, textvariable=err_var, bg=C["bg1"], fg=C["red"], font=("Segoe UI", 9)).pack(anchor="w", padx=32, pady=(12, 4))

        def _submit():
            lbl = v_ppe.get()
            if lbl not in code_map: return err_var.set("\u274c Selectionnez une reference EPI.")
            try:
                qty   = int(v_qty.get().strip())
                seuil = int(v_seuil.get().strip())
            except ValueError: return err_var.set("\u274c Quantite et seuil doivent etre des entiers.")
            ok, msg = add_stock_lot(code_map[lbl], v_lot.get(), qty, seuil)
            if ok:
                messagebox.showinfo("\u2705 Reception enregistree", msg, parent=win)
                win.destroy()
                self._refresh_all()
            else: err_var.set(f"\u274c {msg}")

        self._wz_btn_submit(win, "\u2705  CONFIRMER LA RECEPTION", C["blue"], _submit).pack(fill="x", padx=32, pady=(0, 6))
        tk.Button(win, text="ANNULER", bg=C["bg3"], fg=C["t1"], font=("Segoe UI", 9), relief="flat", cursor="hand2", pady=8, command=win.destroy).pack(fill="x", padx=32)