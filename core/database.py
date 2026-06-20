import os
import sqlite3
import sys
import time
from core.crypto import _compute_sig, _verify_sig

def _resolve_db_path() -> str:
    """Ancrage PyInstaller — critique pour --onefile --windowed."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "ppe_vault.db")

DB_PATH: str = _resolve_db_path()

_DDL: list[str] = [
    """CREATE TABLE IF NOT EXISTS Morphometrics (
        agent_id    TEXT PRIMARY KEY,
        cin         TEXT NOT NULL,
        full_name   TEXT NOT NULL,
        job_class   TEXT NOT NULL,
        suit_size   TEXT NOT NULL,
        boot_size   INTEGER NOT NULL,
        status      TEXT NOT NULL
            CHECK(status IN ('Active','Suspended','Terminated'))
    )""",

    """CREATE TABLE IF NOT EXISTS Arsenal (
        ppe_code            TEXT PRIMARY KEY,
        category            TEXT NOT NULL,
        description         TEXT NOT NULL,
        lifespan_days       INTEGER NOT NULL,
        unit_cost_centimes  INTEGER NOT NULL
    )""",

    """CREATE TABLE IF NOT EXISTS Vault (
        stock_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ppe_code        TEXT NOT NULL REFERENCES Arsenal(ppe_code),
        lot_number      TEXT NOT NULL,
        qty             INTEGER NOT NULL CHECK(qty >= 0),
        min_threshold   INTEGER NOT NULL DEFAULT 10
    )""",

    """CREATE TABLE IF NOT EXISTS Entropy_Log (
        tx_id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_issued         INTEGER NOT NULL,
        agent_id                 TEXT NOT NULL REFERENCES Morphometrics(agent_id),
        stock_id                 INTEGER NOT NULL REFERENCES Vault(stock_id),
        chantier_location        TEXT NOT NULL,
        expected_death_timestamp INTEGER NOT NULL,
        status                   TEXT NOT NULL
            CHECK(status IN ('Compliant','Degraded','Expired','Scrapped')),
        iso_scrap_reason         TEXT,
        crypto_signature         TEXT NOT NULL
    )""",

    "CREATE INDEX IF NOT EXISTS idx_elog_agent  ON Entropy_Log(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_elog_status ON Entropy_Log(status)",
    "CREATE INDEX IF NOT EXISTS idx_elog_death  ON Entropy_Log(expected_death_timestamp)",
]

_SEED_AGENTS: list[tuple] = [
    ("AGT-001", "AB123456", "Mohammed Alami",    "Peintre Industriel", "L",  42, "Active"),
    ("AGT-002", "CD789012", "Hassan Benali",     "Chef d'Equipe",      "XL", 43, "Active"),
    ("AGT-003", "EF345678", "Youssef Cherkaoui", "Sableur",            "M",  41, "Active"),
    ("AGT-004", "GH901234", "Rachid Darif",      "Peintre Industriel", "L",  44, "Active"),
    ("AGT-005", "IJ567890", "Omar Elhassani",    "Opérateur Cabine",   "XL", 42, "Active"),
    ("AGT-006", "KL123456", "Karim Fassi",       "Peintre Industriel", "M",  40, "Suspended"),
]

_SEED_ARSENAL: list[tuple] = [
    ("PPE-RES-001", "Respiratoire",    "Masque FFP3 Jetable Anti-Poussiere",           1,   8500),
    ("PPE-RES-002", "Respiratoire",    "Demi-Masque Reutilisable + Filtres A2P3",    180,  42000),
    ("PPE-VIS-001", "Oeil",            "Lunettes Anti-Projections Polycarbonate",     365,   9500),
    ("PPE-VIS-002", "Oeil",            "Ecran Facial Complet Anti-Solvant",           730,  28000),
    ("PPE-TEN-001", "Tenue",           "Combinaison Tyvek Type 5/6 Jetable",            3,   5500),
    ("PPE-PIE-001", "Pied",            "Bottines PVC Anti-Derapant Solvant",          365,  35000),
    ("PPE-TET-001", "Tete",            "Casque ABS Classe E 1000V",                  1095,  18000),
    ("PPE-OUI-001", "Ouie",            "Bouchons Oreilles Mousse SNR33",                1,    250),
    ("PPE-CHU-001", "Anti-Chute",      "Harnais Anti-Chute Complet + Longe",          365,  85000),
    ("PPE-HV-001",  "Visibilite",      "Gilet Haute Visibilite Classe 2",             730,   7500),
]

_SEED_VAULT: list[tuple] = [
    ("PPE-RES-001", "LOT-2024-MA-001", 150, 30),
    ("PPE-RES-002", "LOT-2024-MA-002",  20,  5),
    ("PPE-VIS-001", "LOT-2024-VI-001",  50, 10),
    ("PPE-VIS-002", "LOT-2024-VI-002",   9,  3),
    ("PPE-TEN-001", "LOT-2024-TN-001", 200, 50),
    ("PPE-TEN-001", "LOT-2024-TN-002",  80, 50),
    ("PPE-PIE-001", "LOT-2024-PI-001",  25, 10),
    ("PPE-TET-001", "LOT-2024-TT-001",   4,  5),
    ("PPE-OUI-001", "LOT-2024-OU-001", 500, 100),
    ("PPE-CHU-001", "LOT-2024-CH-001",   8,  3),
    ("PPE-HV-001",  "LOT-2024-HV-001",  35, 10),
]

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def initialize_database() -> None:
    conn = _get_conn()
    with conn:
        for stmt in _DDL:
            conn.execute(stmt)
        if conn.execute("SELECT COUNT(*) FROM Morphometrics").fetchone()[0] == 0:
            conn.executemany("INSERT INTO Morphometrics VALUES (?,?,?,?,?,?,?)", _SEED_AGENTS)
        if conn.execute("SELECT COUNT(*) FROM Arsenal").fetchone()[0] == 0:
            conn.executemany("INSERT INTO Arsenal VALUES (?,?,?,?,?)", _SEED_ARSENAL)
        if conn.execute("SELECT COUNT(*) FROM Vault").fetchone()[0] == 0:
            conn.executemany("INSERT INTO Vault (ppe_code, lot_number, qty, min_threshold) VALUES (?,?,?,?)", _SEED_VAULT)
    conn.close()

def _fmt_mad(centimes: int) -> str:
    """Formatage arithmétique pur. Zéro float."""
    return f"{centimes // 100:,}.{centimes % 100:02d} MAD"

def _parse_mad_input(text: str) -> tuple[bool, int]:
    """Parse la saisie MAD en centimes INTEGER."""
    text = text.strip().replace(",", "").replace(" ", "")
    if not text:
        return False, 0
    if "." in text:
        parts = text.split(".")
        if len(parts) != 2:
            return False, 0
        d_str, f_str = parts
        if not d_str.isdigit() or not f_str.isdigit():
            return False, 0
        f_str = (f_str + "0")[:2]
        return True, int(d_str) * 100 + int(f_str)
    else:
        if not text.isdigit():
            return False, 0
        return True, int(text) * 100

def allocate_ppe(agent_id: str, stock_id: int, chantier: str) -> tuple[bool, str]:
    conn = _get_conn()
    try:
        with conn:
            agent = conn.execute("SELECT full_name, status FROM Morphometrics WHERE agent_id=?", (agent_id,)).fetchone()
            if agent is None: return False, f"Agent {agent_id} introuvable."
            if agent["status"] != "Active": return False, f"Agent {agent['full_name']} — statut '{agent['status']}'. Allocation refusee."

            stock = conn.execute("""SELECT v.stock_id, v.qty, v.lot_number, a.description, a.lifespan_days, a.unit_cost_centimes FROM Vault v JOIN Arsenal a ON a.ppe_code = v.ppe_code WHERE v.stock_id = ?""", (stock_id,)).fetchone()
            if stock is None: return False, f"Stock ID {stock_id} introuvable."
            if stock["qty"] <= 0: return False, f"Rupture de stock — LOT {stock['lot_number']}."

            result = conn.execute("UPDATE Vault SET qty = qty - 1 WHERE stock_id = ? AND qty > 0", (stock_id,))
            if result.rowcount != 1: return False, "Deduction stock echouee — concurrence detectee."

            ts_now = int(time.time())
            ts_death = ts_now + stock["lifespan_days"] * 86400

            cursor = conn.execute("""INSERT INTO Entropy_Log (timestamp_issued, agent_id, stock_id, chantier_location, expected_death_timestamp, status, iso_scrap_reason, crypto_signature) VALUES (?,?,?,?,?,'Compliant',NULL,'PENDING')""", (ts_now, agent_id, stock_id, chantier, ts_death))
            tx_id = cursor.lastrowid

            sig = _compute_sig(tx_id, agent_id, stock_id, ts_now, chantier, ts_death)
            conn.execute("UPDATE Entropy_Log SET crypto_signature=? WHERE tx_id=?", (sig, tx_id))

            expiry = time.strftime("%d/%m/%Y", time.localtime(ts_death))
            return True, (f"TX-{tx_id:06d} EMIS\n\nAgent  : {agent['full_name']}\nEPI    : {stock['description']}\nLOT    : {stock['lot_number']}\nSite   : {chantier}\nExpire : {expiry}\nValeur : {_fmt_mad(stock['unit_cost_centimes'])}")
    except sqlite3.Error as exc:
        return False, f"Erreur DB : {exc}"
    finally:
        conn.close()

def scrap_allocation(tx_id: int, reason: str) -> tuple[bool, str]:
    reason = reason.strip()
    if not reason: return False, "Motif non-conformite obligatoire (ISO 9001 PDCA)."
    conn = _get_conn()
    try:
        with conn:
            row = conn.execute("SELECT status FROM Entropy_Log WHERE tx_id=?", (tx_id,)).fetchone()
            if row is None: return False, f"Transaction TX-{tx_id:06d} introuvable."
            if row["status"] == "Scrapped": return False, f"TX-{tx_id:06d} deja cloturee."
            conn.execute("UPDATE Entropy_Log SET status='Scrapped', iso_scrap_reason=? WHERE tx_id=?", (reason, tx_id))
            return True, f"TX-{tx_id:06d} — Non-conformite enregistree.\nMotif : {reason}"
    except sqlite3.Error as exc:
        return False, f"Erreur DB : {exc}"
    finally:
        conn.close()

def run_audit_scanner() -> list[dict]:
    conn = _get_conn()
    anomalies: list[dict] = []
    now = int(time.time())
    try:
        rows = conn.execute("SELECT * FROM Entropy_Log ORDER BY tx_id").fetchall()
        expired_ids: list[int] = []
        for row in rows:
            issues: list[str] = []
            if row["crypto_signature"] != "PENDING":
                if not _verify_sig(row): issues.append("SIGNATURE HMAC INVALIDE — FALSIFICATION DETECTEE")
            if row["status"] in ("Compliant", "Degraded"):
                if now > row["expected_death_timestamp"]:
                    issues.append("EPI EXPIRE EN SERVICE")
                    expired_ids.append(row["tx_id"])
            if issues:
                anomalies.append({"tx_id": row["tx_id"], "agent": row["agent_id"], "status": row["status"], "issues": issues})
        if expired_ids:
            ph = ",".join(["?"] * len(expired_ids))
            with conn:
                conn.execute(f"UPDATE Entropy_Log SET status='Expired' WHERE tx_id IN ({ph})", expired_ids)
    finally:
        conn.close()
    return anomalies

def add_agent(agent_id: str, cin: str, full_name: str, job_class: str, suit_size: str, boot_size: int) -> tuple[bool, str]:
    agent_id = agent_id.strip().upper()
    cin = cin.strip().upper()
    full_name = full_name.strip()
    job_class = job_class.strip()
    suit_size = suit_size.strip().upper()
    if not all([agent_id, cin, full_name, job_class, suit_size]): return False, "Tous les champs texte sont obligatoires."
    if not (36 <= boot_size <= 48): return False, "Pointure invalide (doit etre entre 36 et 48)."
    conn = _get_conn()
    try:
        with conn:
            if conn.execute("SELECT 1 FROM Morphometrics WHERE agent_id=?", (agent_id,)).fetchone(): return False, f"Identifiant {agent_id} deja utilise."
            if conn.execute("SELECT 1 FROM Morphometrics WHERE cin=?", (cin,)).fetchone(): return False, f"CIN {cin} deja enregistre."
            conn.execute("INSERT INTO Morphometrics VALUES (?,?,?,?,?,?,?)", (agent_id, cin, full_name, job_class, suit_size, boot_size, "Active"))
            return True, f"Agent {agent_id} — {full_name} enregistre avec succes."
    except sqlite3.Error as exc:
        return False, f"Erreur DB : {exc}"
    finally:
        conn.close()

def update_agent_status(agent_id: str, new_status: str) -> tuple[bool, str]:
    if new_status not in ("Active", "Suspended", "Terminated"): return False, "Statut invalide."
    conn = _get_conn()
    try:
        with conn:
            row = conn.execute("SELECT full_name, status FROM Morphometrics WHERE agent_id=?", (agent_id,)).fetchone()
            if row is None: return False, f"Agent {agent_id} introuvable."
            if row["status"] == new_status: return False, f"Statut deja '{new_status}' — aucun changement."
            conn.execute("UPDATE Morphometrics SET status=? WHERE agent_id=?", (new_status, agent_id))
            return True, f"{row['full_name']}\nStatut : {row['status']} \u2192 {new_status}"
    except sqlite3.Error as exc:
        return False, f"Erreur DB : {exc}"
    finally:
        conn.close()

def add_ppe_type(ppe_code: str, category: str, description: str, lifespan_days: int, unit_cost_centimes: int) -> tuple[bool, str]:
    ppe_code = ppe_code.strip().upper()
    category = category.strip()
    description = description.strip()
    if not all([ppe_code, category, description]): return False, "Code, categorie et description sont obligatoires."
    if lifespan_days <= 0: return False, "Duree de vie invalide (> 0 jours requis)."
    if unit_cost_centimes <= 0: return False, "Cout invalide (> 0 centimes requis)."
    conn = _get_conn()
    try:
        with conn:
            if conn.execute("SELECT 1 FROM Arsenal WHERE ppe_code=?", (ppe_code,)).fetchone(): return False, f"Code {ppe_code} deja present dans le catalogue."
            conn.execute("INSERT INTO Arsenal VALUES (?,?,?,?,?)", (ppe_code, category, description, lifespan_days, unit_cost_centimes))
            return True, f"EPI {ppe_code} enregistre.\n{description}\nCategorie : {category}   Duree : {lifespan_days}j   Cout : {_fmt_mad(unit_cost_centimes)}"
    except sqlite3.Error as exc:
        return False, f"Erreur DB : {exc}"
    finally:
        conn.close()

def add_stock_lot(ppe_code: str, lot_number: str, qty: int, min_threshold: int) -> tuple[bool, str]:
    lot_number = lot_number.strip().upper()
    if not lot_number: return False, "Numero de lot obligatoire."
    if qty <= 0: return False, "Quantite doit etre > 0."
    if min_threshold < 0: return False, "Seuil d'alerte doit etre >= 0."
    conn = _get_conn()
    try:
        with conn:
            ppe = conn.execute("SELECT description FROM Arsenal WHERE ppe_code=?", (ppe_code,)).fetchone()
            if ppe is None: return False, f"Reference EPI {ppe_code} inexistante dans le catalogue."
            if conn.execute("SELECT 1 FROM Vault WHERE ppe_code=? AND lot_number=?", (ppe_code, lot_number)).fetchone(): return False, f"LOT {lot_number} deja enregistre pour {ppe_code}."
            conn.execute("INSERT INTO Vault (ppe_code, lot_number, qty, min_threshold) VALUES (?,?,?,?)", (ppe_code, lot_number, qty, min_threshold))
            return True, f"Reception OK\n{ppe['description']}\nLOT : {lot_number}   QTY : {qty}   Seuil : {min_threshold}"
    except sqlite3.Error as exc:
        return False, f"Erreur DB : {exc}"
    finally:
        conn.close()

def _get_kpis() -> dict:
    conn = _get_conn()
    try:
        vault_val: int = conn.execute("""SELECT COALESCE(SUM(v.qty * a.unit_cost_centimes), 0) FROM Vault v JOIN Arsenal a ON a.ppe_code = v.ppe_code""").fetchone()[0]
        active: int = conn.execute("SELECT COUNT(*) FROM Entropy_Log WHERE status IN ('Compliant','Degraded')").fetchone()[0]
        expired: int = conn.execute("SELECT COUNT(*) FROM Entropy_Log WHERE status='Expired'").fetchone()[0]
        soon_ts = int(time.time()) + 604_800
        warn: int = conn.execute("""SELECT COUNT(*) FROM Entropy_Log WHERE status IN ('Compliant','Degraded') AND expected_death_timestamp <= ?""", (soon_ts,)).fetchone()[0]
        low: int = conn.execute("SELECT COUNT(*) FROM Vault WHERE qty <= min_threshold").fetchone()[0]
        return {"vault_val_centimes": vault_val, "active_count": active, "expired_count": expired, "warn_count": warn, "low_stock_count": low}
    finally:
        conn.close()

def _get_active_allocs() -> list[sqlite3.Row]:
    conn = _get_conn()
    try:
        return conn.execute("""SELECT e.tx_id, m.full_name AS agent_name, a.description AS ppe_desc, a.category, v.lot_number, e.chantier_location, e.timestamp_issued, e.expected_death_timestamp, e.status, a.unit_cost_centimes FROM Entropy_Log e JOIN Morphometrics m ON m.agent_id = e.agent_id JOIN Vault v ON v.stock_id = e.stock_id JOIN Arsenal a ON a.ppe_code = v.ppe_code WHERE e.status IN ('Compliant','Degraded','Expired') ORDER BY e.expected_death_timestamp ASC""").fetchall()
    finally:
        conn.close()

def _get_active_agents() -> list[sqlite3.Row]:
    conn = _get_conn()
    try:
        return conn.execute("SELECT agent_id, full_name, job_class FROM Morphometrics WHERE status = 'Active' ORDER BY full_name").fetchall()
    finally:
        conn.close()

def _get_available_stocks() -> list[sqlite3.Row]:
    conn = _get_conn()
    try:
        return conn.execute("""SELECT v.stock_id, v.ppe_code, v.lot_number, v.qty, v.min_threshold, a.description, a.category, a.lifespan_days, a.unit_cost_centimes FROM Vault v JOIN Arsenal a ON a.ppe_code = v.ppe_code WHERE v.qty > 0 ORDER BY a.category, a.description""").fetchall()
    finally:
        conn.close()

def _get_all_agents() -> list[sqlite3.Row]:
    conn = _get_conn()
    try:
        return conn.execute("SELECT agent_id, cin, full_name, job_class, suit_size, boot_size, status FROM Morphometrics ORDER BY status, full_name").fetchall()
    finally:
        conn.close()

def _get_all_arsenal() -> list[sqlite3.Row]:
    conn = _get_conn()
    try:
        return conn.execute("SELECT ppe_code, category, description, lifespan_days, unit_cost_centimes FROM Arsenal ORDER BY category, description").fetchall()
    finally:
        conn.close()

def _get_all_vault() -> list[sqlite3.Row]:
    conn = _get_conn()
    try:
        return conn.execute("""SELECT v.stock_id, v.ppe_code, a.description, v.lot_number, v.qty, v.min_threshold FROM Vault v JOIN Arsenal a ON a.ppe_code = v.ppe_code ORDER BY a.category, a.description, v.lot_number""").fetchall()
    finally:
        conn.close()

def _get_single_alloc(tx_id: int):
    conn = _get_conn()
    try:
        return conn.execute("""SELECT e.tx_id, e.timestamp_issued, e.expected_death_timestamp, e.chantier_location, e.status, e.crypto_signature, m.agent_id, m.full_name, m.job_class, m.cin, m.suit_size, m.boot_size, a.ppe_code, a.description AS ppe_desc, a.category, a.lifespan_days, a.unit_cost_centimes, v.lot_number FROM Entropy_Log e JOIN Morphometrics m ON m.agent_id = e.agent_id JOIN Vault v ON v.stock_id = e.stock_id JOIN Arsenal a ON a.ppe_code = v.ppe_code WHERE e.tx_id = ?""", (tx_id,)).fetchone()
    finally:
        conn.close()

def _get_all_scrapped():
    conn = _get_conn()
    try:
        return conn.execute("""SELECT e.tx_id, e.timestamp_issued, e.expected_death_timestamp, e.chantier_location, e.iso_scrap_reason, m.full_name AS agent_name, a.description AS ppe_desc, a.category, v.lot_number, a.unit_cost_centimes FROM Entropy_Log e JOIN Morphometrics m ON m.agent_id = e.agent_id JOIN Vault v ON v.stock_id = e.stock_id JOIN Arsenal a ON a.ppe_code = v.ppe_code WHERE e.status = 'Scrapped' ORDER BY e.tx_id DESC""").fetchall()
    finally:
        conn.close()