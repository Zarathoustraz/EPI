#!/usr/bin/env python3
# =============================================================================
#  EPI MANAGER  ·  ISOFU  ·  Architecte : Roger Fernando
#  Gestion EPI — Sites de Revêtement Industriel
#  main.py — Fichier monolithique · v3.0
#
#  ARCHITECTURE:
#    §1  Imports & Constantes système
#    §2  DDL & Données de référence
#    §3  Couche base de données
#    §4  Moteur cryptographique (HMAC-SHA256)
#    §5  Logique métier
#    §6  Requêtes & helpers GUI
#    §7  Interface graphique (PPEVaultApp)
#    §8  Point d'entrée
#
#  INVARIANTS:
#    · Tous montants en centimes INTEGER — aucun float dans la couche données
#    · _fmt_mad() appelé UNIQUEMENT dans la couche GUI
#    · Signatures HMAC couvrent tx_id + agent_id + stock_id + ts + chantier + ts_death
#    · Insert deux temps : PENDING → signature réelle (atomique WAL)
# =============================================================================

# =============================================================================
#  §1 — IMPORTS & CONSTANTES SYSTÈME
# =============================================================================

import hashlib
import hmac
import os
import sqlite3
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk


def _resolve_db_path() -> str:
    """
    Ancrage PyInstaller — critique pour --onefile --windowed.

    Mode frozen  : sys.executable pointe l'exe dans dist/.
                   La DB est creee/lue a cote de l'exe.
                   os.path.abspath(__file__) pointerait _MEIPASS (volatile).
    Mode script  : __file__ colocalise la DB avec main.py.

    NE JAMAIS utiliser __file__ seul pour le chemin DB en production.
    """
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "ppe_vault.db")


DB_PATH: str = _resolve_db_path()

# Sel cryptographique — NE PAS MODIFIER APRÈS PREMIER DÉPLOIEMENT
SECRET_SALT: bytes = b"LOI6599_ISO9001_PPE_MAROC_v3_INVIOLABLE_BOUSKOURA"

# Palette couleurs GitHub Dark
C: dict[str, str] = {
    "bg0":    "#0D1117",
    "bg1":    "#161B22",
    "bg2":    "#1C2128",
    "bg3":    "#21262D",
    "blue":   "#58A6FF",
    "green":  "#3FB950",
    "orange": "#D29922",
    "red":    "#F85149",
    "purple": "#BC8CFF",
    "t0":     "#E6EDF3",
    "t1":     "#8B949E",
    "border": "#30363D",
}

# =============================================================================
#  §2 — DDL & DONNÉES DE RÉFÉRENCE
# =============================================================================

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

# 6 agents (1 Suspended pour tester le blocage d'allocation)
_SEED_AGENTS: list[tuple] = [
    ("AGT-001", "AB123456", "Mohammed Alami",    "Peintre Industriel", "L",  42, "Active"),
    ("AGT-002", "CD789012", "Hassan Benali",     "Chef d'Equipe",      "XL", 43, "Active"),
    ("AGT-003", "EF345678", "Youssef Cherkaoui", "Sableur",            "M",  41, "Active"),
    ("AGT-004", "GH901234", "Rachid Darif",      "Peintre Industriel", "L",  44, "Active"),
    ("AGT-005", "IJ567890", "Omar Elhassani",    "Opérateur Cabine",   "XL", 42, "Active"),
    ("AGT-006", "KL123456", "Karim Fassi",       "Peintre Industriel", "M",  40, "Suspended"),
]

# 10 références EPI — couvrent tous les risques d'un site de revêtement
_SEED_ARSENAL: list[tuple] = [
    # (ppe_code, category, description, lifespan_days, unit_cost_centimes)
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

# 11 lots en stock — deux lots Tyvek pour démontrer la traçabilité ISO
_SEED_VAULT: list[tuple] = [
    # (ppe_code, lot_number, qty, min_threshold)
    ("PPE-RES-001", "LOT-2024-MA-001", 150, 30),
    ("PPE-RES-002", "LOT-2024-MA-002",  20,  5),
    ("PPE-VIS-001", "LOT-2024-VI-001",  50, 10),
    ("PPE-VIS-002", "LOT-2024-VI-002",   9,  3),
    ("PPE-TEN-001", "LOT-2024-TN-001", 200, 50),
    ("PPE-TEN-001", "LOT-2024-TN-002",  80, 50),   # Second lot — même référence
    ("PPE-PIE-001", "LOT-2024-PI-001",  25, 10),
    ("PPE-TET-001", "LOT-2024-TT-001",   4,  5),   # Sous seuil → stock critique
    ("PPE-OUI-001", "LOT-2024-OU-001", 500, 100),
    ("PPE-CHU-001", "LOT-2024-CH-001",   8,  3),
    ("PPE-HV-001",  "LOT-2024-HV-001",  35, 10),
]

# =============================================================================
#  §3 — COUCHE BASE DE DONNÉES
# =============================================================================

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize_database() -> None:
    """Idempotent — crée les tables et insère les données de référence si vides."""
    conn = _get_conn()
    with conn:
        for stmt in _DDL:
            conn.execute(stmt)
        if conn.execute("SELECT COUNT(*) FROM Morphometrics").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO Morphometrics VALUES (?,?,?,?,?,?,?)",
                _SEED_AGENTS,
            )
        if conn.execute("SELECT COUNT(*) FROM Arsenal").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO Arsenal VALUES (?,?,?,?,?)",
                _SEED_ARSENAL,
            )
        if conn.execute("SELECT COUNT(*) FROM Vault").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO Vault (ppe_code, lot_number, qty, min_threshold) VALUES (?,?,?,?)",
                _SEED_VAULT,
            )
    conn.close()

# =============================================================================
#  §4 — MOTEUR CRYPTOGRAPHIQUE (HMAC-SHA256)
# =============================================================================

def _canonical(
    tx_id: int, agent_id: str, stock_id: int,
    ts_issued: int, chantier: str, ts_death: int,
) -> bytes:
    """Forme canonique NUL-délimitée couvrant tous les champs critiques."""
    return (
        f"{tx_id}\x00{agent_id}\x00{stock_id}\x00"
        f"{ts_issued}\x00{chantier}\x00{ts_death}"
    ).encode("utf-8")


def _compute_sig(
    tx_id: int, agent_id: str, stock_id: int,
    ts_issued: int, chantier: str, ts_death: int,
) -> str:
    msg = _canonical(tx_id, agent_id, stock_id, ts_issued, chantier, ts_death)
    return hmac.new(SECRET_SALT, msg, hashlib.sha256).hexdigest()


def _verify_sig(row: sqlite3.Row) -> bool:
    """Vérification à temps constant — résistant aux attaques timing."""
    expected = _compute_sig(
        row["tx_id"], row["agent_id"], row["stock_id"],
        row["timestamp_issued"], row["chantier_location"],
        row["expected_death_timestamp"],
    )
    return hmac.compare_digest(expected, row["crypto_signature"])

# =============================================================================
#  §5 — LOGIQUE MÉTIER
# =============================================================================

def allocate_ppe(agent_id: str, stock_id: int, chantier: str) -> tuple[bool, str]:
    """
    Protocole d'allocation atomique.
    1. Valide agent (Active) + stock (qty > 0)
    2. Déduction atomique qty = qty - 1 avec guard rowcount
    3. Insert PENDING → compute HMAC avec tx_id réel → UPDATE signature
    Tout dans un seul WITH CONN (WAL, FK ON).
    """
    conn = _get_conn()
    try:
        with conn:
            agent = conn.execute(
                "SELECT full_name, status FROM Morphometrics WHERE agent_id=?",
                (agent_id,),
            ).fetchone()
            if agent is None:
                return False, f"Agent {agent_id} introuvable."
            if agent["status"] != "Active":
                return False, (
                    f"Agent {agent['full_name']} — statut '{agent['status']}'."
                    " Allocation refusee."
                )

            stock = conn.execute(
                """SELECT v.stock_id, v.qty, v.lot_number,
                          a.description, a.lifespan_days, a.unit_cost_centimes
                   FROM Vault v
                   JOIN Arsenal a ON a.ppe_code = v.ppe_code
                   WHERE v.stock_id = ?""",
                (stock_id,),
            ).fetchone()
            if stock is None:
                return False, f"Stock ID {stock_id} introuvable."
            if stock["qty"] <= 0:
                return False, f"Rupture de stock — LOT {stock['lot_number']}."

            result = conn.execute(
                "UPDATE Vault SET qty = qty - 1 WHERE stock_id = ? AND qty > 0",
                (stock_id,),
            )
            if result.rowcount != 1:
                return False, "Deduction stock echouee — concurrence detectee."

            ts_now   = int(time.time())
            ts_death = ts_now + stock["lifespan_days"] * 86400

            cursor = conn.execute(
                """INSERT INTO Entropy_Log
                   (timestamp_issued, agent_id, stock_id, chantier_location,
                    expected_death_timestamp, status, iso_scrap_reason,
                    crypto_signature)
                   VALUES (?,?,?,?,?,'Compliant',NULL,'PENDING')""",
                (ts_now, agent_id, stock_id, chantier, ts_death),
            )
            tx_id = cursor.lastrowid

            sig = _compute_sig(
                tx_id, agent_id, stock_id, ts_now, chantier, ts_death
            )
            conn.execute(
                "UPDATE Entropy_Log SET crypto_signature=? WHERE tx_id=?",
                (sig, tx_id),
            )

            expiry = time.strftime("%d/%m/%Y", time.localtime(ts_death))
            return True, (
                f"TX-{tx_id:06d} EMIS\n\n"
                f"Agent  : {agent['full_name']}\n"
                f"EPI    : {stock['description']}\n"
                f"LOT    : {stock['lot_number']}\n"
                f"Site   : {chantier}\n"
                f"Expire : {expiry}\n"
                f"Valeur : {_fmt_mad(stock['unit_cost_centimes'])}"
            )
    except sqlite3.Error as exc:
        return False, f"Erreur DB : {exc}"
    finally:
        conn.close()


def scrap_allocation(tx_id: int, reason: str) -> tuple[bool, str]:
    """
    Guillotine ISO 9001 — clôture une allocation avec motif PDCA obligatoire.
    Refuse si déjà Scrapped.
    """
    reason = reason.strip()
    if not reason:
        return False, "Motif non-conformite obligatoire (ISO 9001 PDCA)."

    conn = _get_conn()
    try:
        with conn:
            row = conn.execute(
                "SELECT status FROM Entropy_Log WHERE tx_id=?",
                (tx_id,),
            ).fetchone()
            if row is None:
                return False, f"Transaction TX-{tx_id:06d} introuvable."
            if row["status"] == "Scrapped":
                return False, f"TX-{tx_id:06d} deja cloturee."

            conn.execute(
                """UPDATE Entropy_Log
                   SET status='Scrapped', iso_scrap_reason=?
                   WHERE tx_id=?""",
                (reason, tx_id),
            )
            return True, (
                f"TX-{tx_id:06d} — Non-conformite enregistree.\n"
                f"Motif : {reason}"
            )
    except sqlite3.Error as exc:
        return False, f"Erreur DB : {exc}"
    finally:
        conn.close()


def run_audit_scanner() -> list[dict]:
    """
    Scanner d'intégrité HMAC + auto-expiration.

    Pour chaque enregistrement Entropy_Log :
      · Recompute HMAC → flag TAMPERED si mismatch
      · Si Compliant/Degraded et now > expected_death → batch UPDATE 'Expired'

    Retourne liste des anomalies [{tx_id, agent, status, issues}].
    """
    conn = _get_conn()
    anomalies: list[dict] = []
    now = int(time.time())

    try:
        rows = conn.execute(
            "SELECT * FROM Entropy_Log ORDER BY tx_id"
        ).fetchall()

        expired_ids: list[int] = []

        for row in rows:
            issues: list[str] = []

            if row["crypto_signature"] != "PENDING":
                if not _verify_sig(row):
                    issues.append(
                        "SIGNATURE HMAC INVALIDE — FALSIFICATION DETECTEE"
                    )

            if row["status"] in ("Compliant", "Degraded"):
                if now > row["expected_death_timestamp"]:
                    issues.append("EPI EXPIRE EN SERVICE")
                    expired_ids.append(row["tx_id"])

            if issues:
                anomalies.append({
                    "tx_id":  row["tx_id"],
                    "agent":  row["agent_id"],
                    "status": row["status"],
                    "issues": issues,
                })

        if expired_ids:
            ph = ",".join(["?"] * len(expired_ids))
            with conn:
                conn.execute(
                    f"UPDATE Entropy_Log SET status='Expired' WHERE tx_id IN ({ph})",
                    expired_ids,
                )
    finally:
        conn.close()

    return anomalies


def add_agent(
    agent_id: str, cin: str, full_name: str,
    job_class: str, suit_size: str, boot_size: int,
) -> tuple[bool, str]:
    """Insère un nouvel agent avec statut Active par défaut."""
    agent_id  = agent_id.strip().upper()
    cin       = cin.strip().upper()
    full_name = full_name.strip()
    job_class = job_class.strip()
    suit_size = suit_size.strip().upper()

    if not all([agent_id, cin, full_name, job_class, suit_size]):
        return False, "Tous les champs texte sont obligatoires."
    if not (36 <= boot_size <= 48):
        return False, "Pointure invalide (doit etre entre 36 et 48)."

    conn = _get_conn()
    try:
        with conn:
            if conn.execute(
                "SELECT 1 FROM Morphometrics WHERE agent_id=?", (agent_id,)
            ).fetchone():
                return False, f"Identifiant {agent_id} deja utilise."
            if conn.execute(
                "SELECT 1 FROM Morphometrics WHERE cin=?", (cin,)
            ).fetchone():
                return False, f"CIN {cin} deja enregistre."
            conn.execute(
                "INSERT INTO Morphometrics VALUES (?,?,?,?,?,?,?)",
                (agent_id, cin, full_name, job_class, suit_size, boot_size, "Active"),
            )
            return True, f"Agent {agent_id} — {full_name} enregistre avec succes."
    except sqlite3.Error as exc:
        return False, f"Erreur DB : {exc}"
    finally:
        conn.close()


def update_agent_status(agent_id: str, new_status: str) -> tuple[bool, str]:
    """Change le statut d'un agent (Active / Suspended / Terminated)."""
    if new_status not in ("Active", "Suspended", "Terminated"):
        return False, "Statut invalide."
    conn = _get_conn()
    try:
        with conn:
            row = conn.execute(
                "SELECT full_name, status FROM Morphometrics WHERE agent_id=?",
                (agent_id,),
            ).fetchone()
            if row is None:
                return False, f"Agent {agent_id} introuvable."
            if row["status"] == new_status:
                return False, f"Statut deja '{new_status}' — aucun changement."
            conn.execute(
                "UPDATE Morphometrics SET status=? WHERE agent_id=?",
                (new_status, agent_id),
            )
            return True, (
                f"{row['full_name']}\n"
                f"Statut : {row['status']} \u2192 {new_status}"
            )
    except sqlite3.Error as exc:
        return False, f"Erreur DB : {exc}"
    finally:
        conn.close()


def add_ppe_type(
    ppe_code: str, category: str, description: str,
    lifespan_days: int, unit_cost_centimes: int,
) -> tuple[bool, str]:
    """Ajoute une nouvelle référence EPI au catalogue Arsenal."""
    ppe_code    = ppe_code.strip().upper()
    category    = category.strip()
    description = description.strip()

    if not all([ppe_code, category, description]):
        return False, "Code, categorie et description sont obligatoires."
    if lifespan_days <= 0:
        return False, "Duree de vie invalide (> 0 jours requis)."
    if unit_cost_centimes <= 0:
        return False, "Cout invalide (> 0 centimes requis)."

    conn = _get_conn()
    try:
        with conn:
            if conn.execute(
                "SELECT 1 FROM Arsenal WHERE ppe_code=?", (ppe_code,)
            ).fetchone():
                return False, f"Code {ppe_code} deja present dans le catalogue."
            conn.execute(
                "INSERT INTO Arsenal VALUES (?,?,?,?,?)",
                (ppe_code, category, description, lifespan_days, unit_cost_centimes),
            )
            return True, (
                f"EPI {ppe_code} enregistre.\n"
                f"{description}\n"
                f"Categorie : {category}   "
                f"Duree : {lifespan_days}j   "
                f"Cout : {_fmt_mad(unit_cost_centimes)}"
            )
    except sqlite3.Error as exc:
        return False, f"Erreur DB : {exc}"
    finally:
        conn.close()


def add_stock_lot(
    ppe_code: str, lot_number: str, qty: int, min_threshold: int,
) -> tuple[bool, str]:
    """Réception stock — ajoute un nouveau lot au Vault."""
    lot_number = lot_number.strip().upper()
    if not lot_number:
        return False, "Numero de lot obligatoire."
    if qty <= 0:
        return False, "Quantite doit etre > 0."
    if min_threshold < 0:
        return False, "Seuil d'alerte doit etre >= 0."

    conn = _get_conn()
    try:
        with conn:
            ppe = conn.execute(
                "SELECT description FROM Arsenal WHERE ppe_code=?", (ppe_code,)
            ).fetchone()
            if ppe is None:
                return False, f"Reference EPI {ppe_code} inexistante dans le catalogue."
            if conn.execute(
                "SELECT 1 FROM Vault WHERE ppe_code=? AND lot_number=?",
                (ppe_code, lot_number),
            ).fetchone():
                return False, f"LOT {lot_number} deja enregistre pour {ppe_code}."
            conn.execute(
                "INSERT INTO Vault (ppe_code, lot_number, qty, min_threshold) VALUES (?,?,?,?)",
                (ppe_code, lot_number, qty, min_threshold),
            )
            return True, (
                f"Reception OK\n{ppe['description']}\n"
                f"LOT : {lot_number}   QTY : {qty}   Seuil : {min_threshold}"
            )
    except sqlite3.Error as exc:
        return False, f"Erreur DB : {exc}"
    finally:
        conn.close()

# =============================================================================
#  §6 — REQUÊTES & HELPERS GUI
# =============================================================================

def _fmt_mad(centimes: int) -> str:
    """COUCHE GUI UNIQUEMENT. Arithmetique entiere pure. Zero float."""
    return f"{centimes // 100:,}.{centimes % 100:02d} MAD"


def _parse_mad_input(text: str) -> tuple[bool, int]:
    """
    Parse saisie utilisateur MAD → centimes INTEGER. Float-free.
    '85'      → (True, 8500)
    '85.50'   → (True, 8550)
    '85.5'    → (True, 8550)
    '1 420'   → (True, 142000)
    Retourne (ok, centimes).
    """
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
        f_str = (f_str + "0")[:2]   # "5" → "50", "507" → "50"
        return True, int(d_str) * 100 + int(f_str)
    else:
        if not text.isdigit():
            return False, 0
        return True, int(text) * 100


def _get_kpis() -> dict:
    conn = _get_conn()
    try:
        vault_val: int = conn.execute(
            """SELECT COALESCE(SUM(v.qty * a.unit_cost_centimes), 0)
               FROM Vault v
               JOIN Arsenal a ON a.ppe_code = v.ppe_code"""
        ).fetchone()[0]

        active: int = conn.execute(
            "SELECT COUNT(*) FROM Entropy_Log"
            " WHERE status IN ('Compliant','Degraded')"
        ).fetchone()[0]

        expired: int = conn.execute(
            "SELECT COUNT(*) FROM Entropy_Log WHERE status='Expired'"
        ).fetchone()[0]

        soon_ts = int(time.time()) + 604_800  # 7 jours en secondes
        warn: int = conn.execute(
            """SELECT COUNT(*) FROM Entropy_Log
               WHERE status IN ('Compliant','Degraded')
               AND expected_death_timestamp <= ?""",
            (soon_ts,),
        ).fetchone()[0]

        low: int = conn.execute(
            "SELECT COUNT(*) FROM Vault WHERE qty <= min_threshold"
        ).fetchone()[0]

        return {
            "vault_val_centimes": vault_val,
            "active_count":       active,
            "expired_count":      expired,
            "warn_count":         warn,
            "low_stock_count":    low,
        }
    finally:
        conn.close()


def _get_active_allocs() -> list[sqlite3.Row]:
    conn = _get_conn()
    try:
        return conn.execute(
            """SELECT
                   e.tx_id,
                   m.full_name                AS agent_name,
                   a.description              AS ppe_desc,
                   a.category,
                   v.lot_number,
                   e.chantier_location,
                   e.timestamp_issued,
                   e.expected_death_timestamp,
                   e.status,
                   a.unit_cost_centimes
               FROM Entropy_Log e
               JOIN Morphometrics m ON m.agent_id = e.agent_id
               JOIN Vault v         ON v.stock_id  = e.stock_id
               JOIN Arsenal a       ON a.ppe_code  = v.ppe_code
               WHERE e.status IN ('Compliant','Degraded','Expired')
               ORDER BY e.expected_death_timestamp ASC"""
        ).fetchall()
    finally:
        conn.close()


def _get_active_agents() -> list[sqlite3.Row]:
    conn = _get_conn()
    try:
        return conn.execute(
            """SELECT agent_id, full_name, job_class
               FROM Morphometrics
               WHERE status = 'Active'
               ORDER BY full_name"""
        ).fetchall()
    finally:
        conn.close()


def _get_available_stocks() -> list[sqlite3.Row]:
    conn = _get_conn()
    try:
        return conn.execute(
            """SELECT
                   v.stock_id, v.ppe_code, v.lot_number,
                   v.qty, v.min_threshold,
                   a.description, a.category, a.lifespan_days,
                   a.unit_cost_centimes
               FROM Vault v
               JOIN Arsenal a ON a.ppe_code = v.ppe_code
               WHERE v.qty > 0
               ORDER BY a.category, a.description"""
        ).fetchall()
    finally:
        conn.close()


def _get_all_agents() -> list[sqlite3.Row]:
    conn = _get_conn()
    try:
        return conn.execute(
            """SELECT agent_id, cin, full_name, job_class, suit_size, boot_size, status
               FROM Morphometrics
               ORDER BY status, full_name"""
        ).fetchall()
    finally:
        conn.close()


def _get_all_arsenal() -> list[sqlite3.Row]:
    conn = _get_conn()
    try:
        return conn.execute(
            """SELECT ppe_code, category, description, lifespan_days, unit_cost_centimes
               FROM Arsenal
               ORDER BY category, description"""
        ).fetchall()
    finally:
        conn.close()


def _get_all_vault() -> list[sqlite3.Row]:
    conn = _get_conn()
    try:
        return conn.execute(
            """SELECT v.stock_id, v.ppe_code, a.description, v.lot_number,
                      v.qty, v.min_threshold
               FROM Vault v
               JOIN Arsenal a ON a.ppe_code = v.ppe_code
               ORDER BY a.category, a.description, v.lot_number"""
        ).fetchall()
    finally:
        conn.close()


def _get_single_alloc(tx_id: int):
    conn = _get_conn()
    try:
        return conn.execute(
            """SELECT e.tx_id, e.timestamp_issued, e.expected_death_timestamp,
                      e.chantier_location, e.status, e.crypto_signature,
                      m.agent_id, m.full_name, m.job_class, m.cin,
                      m.suit_size, m.boot_size,
                      a.ppe_code, a.description   AS ppe_desc,
                      a.category, a.lifespan_days,
                      a.unit_cost_centimes,
                      v.lot_number
               FROM Entropy_Log e
               JOIN Morphometrics m ON m.agent_id = e.agent_id
               JOIN Vault v         ON v.stock_id  = e.stock_id
               JOIN Arsenal a       ON a.ppe_code  = v.ppe_code
               WHERE e.tx_id = ?""",
            (tx_id,),
        ).fetchone()
    finally:
        conn.close()


def _get_all_scrapped():
    conn = _get_conn()
    try:
        return conn.execute(
            """SELECT e.tx_id, e.timestamp_issued, e.expected_death_timestamp,
                      e.chantier_location, e.iso_scrap_reason,
                      m.full_name AS agent_name,
                      a.description AS ppe_desc, a.category,
                      v.lot_number, a.unit_cost_centimes
               FROM Entropy_Log e
               JOIN Morphometrics m ON m.agent_id = e.agent_id
               JOIN Vault v         ON v.stock_id  = e.stock_id
               JOIN Arsenal a       ON a.ppe_code  = v.ppe_code
               WHERE e.status = 'Scrapped'
               ORDER BY e.tx_id DESC"""
        ).fetchall()
    finally:
        conn.close()


# =============================================================================
#  §6.5 — MOTEUR PDF  (reportlab — Loi 65-99 / ISO 9001)
# =============================================================================

import subprocess
from reportlab.lib              import colors
from reportlab.lib.pagesizes    import A4
from reportlab.lib.styles       import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units        import cm, mm
from reportlab.platypus         import (
    HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
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
        base = os.path.dirname(os.path.abspath(__file__))
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
        "title":    P("ppv_title",  fontName="Helvetica-Bold",  fontSize=16,
                      textColor=_PDF_NAV, spaceAfter=4),
        "subtitle": P("ppv_sub",    fontName="Helvetica",        fontSize=9,
                      textColor=colors.HexColor("#555555"), spaceAfter=2),
        "hdr":      P("ppv_hdr",    fontName="Helvetica-Bold",   fontSize=10,
                      textColor=_PDF_NAV, spaceBefore=10, spaceAfter=4),
        "body":     P("ppv_body",   fontName="Helvetica",        fontSize=8,
                      textColor=colors.black),
        "small":    P("ppv_small",  fontName="Helvetica",        fontSize=7,
                      textColor=_PDF_MID),
        "th":       P("ppv_th",     fontName="Helvetica-Bold",   fontSize=8,
                      textColor=colors.white),
        "td":       P("ppv_td",     fontName="Helvetica",        fontSize=8,
                      textColor=colors.black),
        "td_grn":   P("ppv_tdg",    fontName="Helvetica-Bold",   fontSize=8,
                      textColor=_PDF_GRN),
        "td_red":   P("ppv_tdr",    fontName="Helvetica-Bold",   fontSize=8,
                      textColor=_PDF_RED),
        "td_ora":   P("ppv_tdo",    fontName="Helvetica-Bold",   fontSize=8,
                      textColor=_PDF_ORA),
        "mono":     P("ppv_mono",   fontName="Courier",           fontSize=7,
                      textColor=_PDF_MID),
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
    canvas_obj.drawString(15*mm, H - 21*mm,
        "Architecture : Roger Fernando  \xb7  Loi 65-99 / ISO 9001  \xb7  Maroc")
    ts = time.strftime("Genere le : %d/%m/%Y  %H:%M:%S")
    canvas_obj.drawRightString(W - 15*mm, H - 13*mm, ts)
    canvas_obj.drawRightString(W - 15*mm, H - 21*mm, f"Page {doc.page}")
    canvas_obj.setFillColor(_PDF_GRY)
    canvas_obj.rect(0, 0, W, 12*mm, fill=1, stroke=0)
    canvas_obj.setFillColor(_PDF_MID)
    canvas_obj.setFont("Helvetica", 6.5)
    canvas_obj.drawString(15*mm, 4*mm,
        "Document confidentiel  \u2014  Usage interne uniquement  \u2014  "
        "Toute reproduction non autorisee est interdite.")
    canvas_obj.restoreState()


def pdf_bon_allocation(tx_id: int) -> tuple[bool, str]:
    row = _get_single_alloc(tx_id)
    if row is None:
        return False, f"Transaction TX-{tx_id:06d} introuvable."
    rdir  = _resolve_reports_dir()
    fname = f"BON_ALLOC_TX{tx_id:06d}_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
    path  = os.path.join(rdir, fname)

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

    # Agent
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

    # EPI
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
    c.drawString(15*mm, y,
        f"[{row['category']}]    Code : {row['ppe_code']}    LOT : {row['lot_number']}")
    y -= 13*mm

    # Tableau dates
    emis_s  = time.strftime("%d/%m/%Y  %H:%M", time.localtime(row["timestamp_issued"]))
    death_s = time.strftime("%d/%m/%Y", time.localtime(row["expected_death_timestamp"]))
    S = _pdf_styles()
    data = [
        ["DATE D'EMISSION", "DATE D'EXPIRATION", "DUREE DE VIE", "VALEUR UNITAIRE"],
        [emis_s, death_s, f"{row['lifespan_days']} jour(s)",
         _fmt_mad(row["unit_cost_centimes"])],
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

    # Chantier
    c.setFillColor(_PDF_NAV)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(15*mm, y, "SITE / CHANTIER D'AFFECTATION")
    y -= 5*mm
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(15*mm, y, row["chantier_location"])
    y -= 14*mm

    # HMAC
    c.setStrokeColor(colors.HexColor("#DDDDDD"))
    c.setLineWidth(0.5)
    c.line(15*mm, y, W - 15*mm, y)
    y -= 6*mm
    c.setFillColor(_PDF_MID)
    c.setFont("Courier", 6.5)
    c.drawString(15*mm, y, f"HMAC-SHA256 : {row['crypto_signature']}")
    y -= 18*mm

    # Signatures
    for x_off, label in [(15*mm, "Signature Agent"), (105*mm, "Visa Responsable HSE")]:
        c.setStrokeColor(colors.black)
        c.setLineWidth(0.8)
        c.line(x_off, y - 18*mm, x_off + 75*mm, y - 18*mm)
        c.setFont("Helvetica", 8)
        c.setFillColor(_PDF_MID)
        c.drawString(x_off, y - 23*mm, label)

    # Mention légale
    c.setFillColor(colors.HexColor("#CC0000"))
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(W / 2, 18*mm,
        "Ce bon est un document reglementaire. "
        "Conservation obligatoire 5 ans (Loi 65-99 / ISO 9001 Clause 7.5.3).")
    c.save()
    return True, path


def pdf_etat_allocations() -> tuple[bool, str]:
    allocs = _get_active_allocs()
    kpis   = _get_kpis()
    if not allocs:
        return False, "Aucune allocation active a exporter."
    rdir  = _resolve_reports_dir()
    fname = f"ETAT_ALLOC_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
    path  = os.path.join(rdir, fname)
    S     = _pdf_styles()
    now   = int(time.time())
    doc   = SimpleDocTemplate(path, pagesize=A4,
                               topMargin=34*mm, bottomMargin=18*mm,
                               leftMargin=15*mm, rightMargin=15*mm)
    story = []
    story.append(Paragraph("ETAT DES ALLOCATIONS EPI", S["title"]))
    story.append(Paragraph(
        f"Genere le {time.strftime('%d/%m/%Y a %H:%M:%S')}  "
        f"\u2014  {len(allocs)} enregistrement(s)", S["subtitle"]))
    story.append(Spacer(1, 4*mm))

    kpi_data = [
        ["VALEUR TOTALE STOCK", "ALLOCATIONS ACTIVES", "VIOLATIONS ISO", "STOCK CRITIQUE"],
        [_fmt_mad(kpis["vault_val_centimes"]), str(kpis["active_count"]),
         str(kpis["expired_count"]), str(kpis["low_stock_count"])],
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
    header = [Paragraph(f"<b>{h}</b>", S["th"]) for h in [
        "TX-ID", "AGENT", "EPI", "EXPIRE", "JOURS REST.", "STATUT"]]
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
        days  = (row["expected_death_timestamp"] - now) // 86400
        days_s = str(days) if days > 0 else "EXPIRE"
        exp_s  = time.strftime("%d/%m/%Y", time.localtime(row["expected_death_timestamp"]))
        st     = row["status"]
        st_s   = S["td_red"] if st == "Expired" else (S["td_ora"] if st == "Degraded" else S["td_grn"])
        rows.append([
            Paragraph(f"TX-{row['tx_id']:06d}", S["td"]),
            Paragraph(row["agent_name"][:26], S["td"]),
            Paragraph(row["ppe_desc"][:40], S["td"]),
            Paragraph(exp_s, S["td"]),
            Paragraph(days_s, st_s if days <= 0 else S["td"]),
            Paragraph(st.upper(), st_s),
        ])
        style_cmds.append(("BACKGROUND", (0, i), (-1, i),
                           _PDF_GRY if i % 2 == 0 else colors.white))
    tbl = Table(rows, colWidths=cols_w, repeatRows=1)
    tbl.setStyle(TableStyle(style_cmds))
    story.append(tbl)
    doc.build(story, onFirstPage=_common_header_footer, onLaterPages=_common_header_footer)
    return True, path


def pdf_journal_nc() -> tuple[bool, str]:
    rows = _get_all_scrapped()
    if not rows:
        return False, "Aucune non-conformite enregistree."
    rdir  = _resolve_reports_dir()
    fname = f"JOURNAL_NC_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
    path  = os.path.join(rdir, fname)
    S   = _pdf_styles()
    doc = SimpleDocTemplate(path, pagesize=A4,
                             topMargin=34*mm, bottomMargin=18*mm,
                             leftMargin=15*mm, rightMargin=15*mm)
    story = []
    story.append(Paragraph("JOURNAL DES NON-CONFORMITES EPI", S["title"]))
    story.append(Paragraph(
        f"ISO 9001:2015 Clause 10.2  \u2014  PDCA  \u2014  "
        f"{len(rows)} enregistrement(s)  \u2014  {time.strftime('%d/%m/%Y %H:%M')}",
        S["subtitle"]))
    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="100%", thickness=1.2, color=_PDF_RED))
    story.append(Spacer(1, 4*mm))

    cols_w = [18*mm, 38*mm, 48*mm, 22*mm, 28*mm, 30*mm]
    header = [Paragraph(f"<b>{h}</b>", S["th"]) for h in [
        "TX-ID", "AGENT", "EPI", "EMIS", "LOT N\xb0", "MOTIF NC"]]
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
        style_cmds.append(("BACKGROUND", (0, i), (-1, i),
                           _PDF_GRY if i % 2 == 0 else colors.white))
    tbl = Table(tbl_rows, colWidths=cols_w, repeatRows=1)
    tbl.setStyle(TableStyle(style_cmds))
    story.append(tbl)
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph(
        "Ce journal constitue la preuve documentaire des actions correctives "
        "conformement a l'ISO 9001:2015 Art. 10.2.2. Conservation : 5 ans.",
        S["small"]))
    doc.build(story, onFirstPage=_common_header_footer, onLaterPages=_common_header_footer)
    return True, path


def pdf_inventaire_vault() -> tuple[bool, str]:
    rows = _get_all_vault()
    if not rows:
        return False, "Vault vide."
    kpis  = _get_kpis()
    rdir  = _resolve_reports_dir()
    fname = f"INVENTAIRE_VAULT_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
    path  = os.path.join(rdir, fname)
    S   = _pdf_styles()
    doc = SimpleDocTemplate(path, pagesize=A4,
                             topMargin=34*mm, bottomMargin=18*mm,
                             leftMargin=15*mm, rightMargin=15*mm)
    story = []
    story.append(Paragraph("INVENTAIRE DU VAULT EPI", S["title"]))
    story.append(Paragraph(
        f"Arrete au {time.strftime('%d/%m/%Y  %H:%M:%S')}  "
        f"\u2014  Valeur totale : {_fmt_mad(kpis['vault_val_centimes'])}",
        S["subtitle"]))
    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="100%", thickness=1, color=_PDF_NAV))
    story.append(Spacer(1, 4*mm))

    from collections import defaultdict
    groups: dict = defaultdict(list)
    for r in rows:
        groups[r["ppe_code"]].append(r)

    cols_w = [22*mm, 80*mm, 30*mm, 16*mm, 16*mm, 20*mm]
    header = [Paragraph(f"<b>{h}</b>", S["th"]) for h in [
        "CODE", "DESCRIPTION / LOT", "LOT N\xb0", "QTY", "SEUIL", "ETAT"]]
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
        style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx),
                           colors.HexColor("#D6E4F0")))
        row_idx += 1
        for lot in lots:
            qty, seuil = lot["qty"], lot["min_threshold"]
            etat, st = (("RUPTURE", S["td_red"]) if qty == 0 else
                        ("ALERTE",  S["td_ora"]) if qty <= seuil else
                        ("OK",      S["td_grn"]))
            all_rows.append([
                Paragraph("", S["td"]),
                Paragraph(f"   {lot['lot_number']}", S["td"]),
                Paragraph(lot["lot_number"], S["td"]),
                Paragraph(str(qty), S["td"]),
                Paragraph(str(seuil), S["td"]),
                Paragraph(etat, st),
            ])
            style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx),
                               _PDF_GRY if row_idx % 2 == 0 else colors.white))
            row_idx += 1

    tbl = Table(all_rows, colWidths=cols_w, repeatRows=1)
    tbl.setStyle(TableStyle(style_cmds))
    story.append(tbl)
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph(
        f"Valeur totale : {_fmt_mad(kpis['vault_val_centimes'])}  "
        f"\u2014  Lots en alerte : {kpis['low_stock_count']}",
        S["hdr"]))
    doc.build(story, onFirstPage=_common_header_footer, onLaterPages=_common_header_footer)
    return True, path


# =============================================================================
#  §7 — INTERFACE GRAPHIQUE
# =============================================================================

class PPEVaultApp(tk.Tk):
    """
    Application principale — GUI anti-chaos conforme Loi 65-99 / ISO 9001.
    Trois onglets : Tableau de bord · Allocation Terminal · Non-conformités.
    """

    # ─────────────────────────────────────────────────────────────────────────
    #  INIT
    # ─────────────────────────────────────────────────────────────────────────

    def __init__(self) -> None:
        super().__init__()
        self.title("EPI MANAGER  ·  ISOFU  ·  Architecte : Roger Fernando")
        self.geometry("1300x820")
        self.minsize(1080, 700)
        self.configure(bg=C["bg0"])

        # Dark combobox dropdown (widget-level override, must precede style)
        self.option_add("*TCombobox*Listbox.background",       C["bg2"])
        self.option_add("*TCombobox*Listbox.foreground",       C["t0"])
        self.option_add("*TCombobox*Listbox.selectBackground", C["blue"])
        self.option_add("*TCombobox*Listbox.selectForeground", C["bg0"])
        self.option_add("*TCombobox*Listbox.font",             "Consolas 10")

        self._build_style()
        self._build_header()
        self._build_notebook()
        self._build_statusbar()

        # Runtime state
        self._agent_map: dict[str, str]  = {}   # dropdown_label → agent_id
        self._stock_map: dict[str, dict] = {}   # dropdown_label → stock dict
        self._nc_tx_id:  int | None      = None

        self.after(400, self._refresh_all)
        self.after(60_000, self._sched_refresh)

    # ─────────────────────────────────────────────────────────────────────────
    #  STYLE
    # ─────────────────────────────────────────────────────────────────────────

    def _build_style(self) -> None:
        s = ttk.Style(self)
        s.theme_use("clam")

        s.configure(".",
            background=C["bg0"], foreground=C["t0"],
            font=("Segoe UI", 10), borderwidth=0, relief="flat",
        )
        s.configure("TFrame",    background=C["bg0"])
        s.configure("TLabel",    background=C["bg0"], foreground=C["t0"])
        s.configure("TSeparator", background=C["border"])

        s.configure("TNotebook",
            background=C["bg0"], borderwidth=0, tabmargins=[0, 0, 0, 0],
        )
        s.configure("TNotebook.Tab",
            background=C["bg1"], foreground=C["t1"],
            padding=[24, 9], font=("Segoe UI", 10, "bold"),
        )
        s.map("TNotebook.Tab",
            background=[("selected", C["bg2"]), ("active", C["bg2"])],
            foreground=[("selected", C["blue"]),  ("active", C["t0"])],
        )

        s.configure("Treeview",
            background=C["bg1"], foreground=C["t0"],
            fieldbackground=C["bg1"], rowheight=32,
            font=("Consolas", 9), borderwidth=0,
        )
        s.configure("Treeview.Heading",
            background=C["bg3"], foreground=C["t1"],
            font=("Segoe UI", 9, "bold"), relief="flat",
        )
        s.map("Treeview",
            background=[("selected", C["blue"])],
            foreground=[("selected", C["bg0"])],
        )
        s.map("Treeview.Heading",
            background=[("active", C["border"])],
        )

        s.configure("TScrollbar",
            background=C["bg3"], troughcolor=C["bg1"],
            borderwidth=0, arrowcolor=C["t1"], gripcount=0,
        )

        s.configure("TCombobox",
            background=C["bg2"], foreground=C["t0"],
            fieldbackground=C["bg2"],
            selectbackground=C["bg2"], selectforeground=C["t0"],
            arrowcolor=C["blue"], bordercolor=C["border"],
            lightcolor=C["bg2"], darkcolor=C["bg2"],
        )
        s.map("TCombobox",
            fieldbackground=[("readonly", C["bg2"])],
            foreground=[("readonly", C["t0"])],
            selectforeground=[("readonly", C["t0"])],
            selectbackground=[("readonly", C["bg2"])],
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  HEADER
    # ─────────────────────────────────────────────────────────────────────────

    def _build_header(self) -> None:
        hdr = tk.Frame(self, bg=C["bg1"], height=58)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)

        left = tk.Frame(hdr, bg=C["bg1"])
        left.pack(side="left", padx=20)
        tk.Label(
            left, text="\U0001f6e1  EPI MANAGER",
            bg=C["bg1"], fg=C["blue"],
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            left, text="   ISOFU \u00b7 Loi 65-99 \u00b7 Architecte : Roger Fernando",
            bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 9),
        ).pack(side="left")

        right = tk.Frame(hdr, bg=C["bg1"])
        right.pack(side="right", padx=20)

        tk.Button(
            right, text="\U0001f50d SCAN INTEGRITE DB",
            bg=C["orange"], fg=C["bg0"],
            font=("Segoe UI", 9, "bold"),
            relief="flat", cursor="hand2", padx=14, pady=6,
            command=self._run_audit,
        ).pack(side="right", padx=(6, 0))

        tk.Button(
            right, text="\u21ba ACTUALISER",
            bg=C["bg3"], fg=C["t0"],
            font=("Segoe UI", 9),
            relief="flat", cursor="hand2", padx=14, pady=6,
            command=self._refresh_all,
        ).pack(side="right", padx=(6, 0))

        self._clock_var = tk.StringVar()
        tk.Label(
            right, textvariable=self._clock_var,
            bg=C["bg1"], fg=C["t1"], font=("Consolas", 9),
        ).pack(side="right", padx=(0, 14))

        self._tick()
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", side="top")

    def _tick(self) -> None:
        self._clock_var.set(time.strftime("  %a %d/%m/%Y   %H:%M:%S"))
        self.after(1000, self._tick)

    # ─────────────────────────────────────────────────────────────────────────
    #  STATUS BAR
    # ─────────────────────────────────────────────────────────────────────────

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self, bg=C["bg3"], height=26)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self._status_var = tk.StringVar(value="Systeme pret.")
        tk.Label(
            bar, textvariable=self._status_var,
            bg=C["bg3"], fg=C["t1"],
            font=("Consolas", 8), anchor="w", padx=12,
        ).pack(fill="x", expand=True)

    def _set_status(self, msg: str) -> None:
        self._status_var.set(f"[{time.strftime('%H:%M:%S')}]  {msg}")

    # ─────────────────────────────────────────────────────────────────────────
    #  NOTEBOOK
    # ─────────────────────────────────────────────────────────────────────────

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

    # =========================================================================
    #  TAB 1 — TABLEAU DE BORD
    # =========================================================================

    def _build_dashboard(self) -> None:
        root = self._tab_dash

        # ── KPI strip ──────────────────────────────────────────────────────
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
            tk.Label(
                inner, text=f"{icon}  {label}",
                bg=C["bg1"], fg=C["t1"],
                font=("Segoe UI", 8, "bold"),
            ).pack(anchor="w")
            tk.Label(
                inner, textvariable=self._kpi_vars[key],
                bg=C["bg1"], fg=color,
                font=("Segoe UI", 16, "bold"),
            ).pack(anchor="w", pady=(4, 0))

        # ── Section header ─────────────────────────────────────────────────
        tk.Frame(root, bg=C["border"], height=1).pack(
            fill="x", padx=20, pady=(0, 6)
        )
        sec_hdr = tk.Frame(root, bg=C["bg0"])
        sec_hdr.pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(
            sec_hdr,
            text="  ALLOCATIONS ACTIVES / EXPIRATIONS CRITIQUES",
            bg=C["bg0"], fg=C["t1"],
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left")
        tk.Button(
            sec_hdr, text="\U0001f4c4  ETAT ALLOCATIONS PDF",
            bg=C["blue"], fg=C["bg0"],
            font=("Segoe UI", 8, "bold"),
            relief="flat", cursor="hand2", padx=10, pady=4,
            command=self._pdf_etat_alloc,
        ).pack(side="right")

        # ── Treeview ───────────────────────────────────────────────────────
        tf = tk.Frame(root, bg=C["bg0"])
        tf.pack(fill="both", expand=True, padx=20, pady=(4, 14))

        cols = ("tx_id", "agent", "ppe", "lot", "chantier",
                "emis", "expire", "jours", "statut")
        self._dash_tree = ttk.Treeview(
            tf, columns=cols, show="headings", selectmode="browse"
        )

        _col_cfg = [
            ("tx_id",    "TX-ID",        72, "center"),
            ("agent",    "AGENT",        170, "w"),
            ("ppe",      "EPI",          235, "w"),
            ("lot",      "LOT N\u00b0",  118, "center"),
            ("chantier", "CHANTIER",     108, "w"),
            ("emis",     "\u00c9MIS",     90, "center"),
            ("expire",   "EXPIRE",        90, "center"),
            ("jours",    "JOURS REST.",   82, "center"),
            ("statut",   "STATUT",       105, "center"),
        ]
        for col, hdr_txt, w, anch in _col_cfg:
            self._dash_tree.heading(col, text=hdr_txt)
            self._dash_tree.column(col, width=w, minwidth=50, anchor=anch)

        self._dash_tree.tag_configure("Compliant", foreground=C["green"])
        self._dash_tree.tag_configure("Expired",   foreground=C["red"])
        self._dash_tree.tag_configure("Degraded",  foreground=C["orange"])

        vsb = ttk.Scrollbar(tf, orient="vertical",   command=self._dash_tree.yview)
        hsb = ttk.Scrollbar(tf, orient="horizontal", command=self._dash_tree.xview)
        self._dash_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._dash_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

    def _refresh_dashboard(self) -> None:
        run_audit_scanner()   # auto-expire avant affichage

        kpis = _get_kpis()
        self._kpi_vars["vault_val"].set(_fmt_mad(kpis["vault_val_centimes"]))
        self._kpi_vars["active"].set(str(kpis["active_count"]))
        self._kpi_vars["expired"].set(str(kpis["expired_count"]))
        self._kpi_vars["warn"].set(str(kpis["warn_count"]))
        self._kpi_vars["low_stock"].set(str(kpis["low_stock_count"]))

        self._dash_tree.delete(*self._dash_tree.get_children())
        now = int(time.time())
        for row in _get_active_allocs():
            emis   = time.strftime("%d/%m/%Y", time.localtime(row["timestamp_issued"]))
            expire = time.strftime(
                "%d/%m/%Y", time.localtime(row["expected_death_timestamp"])
            )
            days   = (row["expected_death_timestamp"] - now) // 86400
            days_s = str(days) if days > 0 else "EXPIRE"
            status = row["status"]
            self._dash_tree.insert("", "end", tags=(status,), values=(
                f"TX-{row['tx_id']:06d}",
                row["agent_name"],
                row["ppe_desc"],
                row["lot_number"],
                row["chantier_location"],
                emis, expire, days_s, status.upper(),
            ))

        n = len(self._dash_tree.get_children())
        self._set_status(f"Dashboard actualise \u2014 {n} allocation(s) active(s).")

    # =========================================================================
    #  TAB 2 — ALLOCATION TERMINAL
    # =========================================================================

    def _build_alloc_tab(self) -> None:
        root = self._tab_alloc

        outer = tk.Frame(root, bg=C["bg0"])
        outer.pack(fill="both", expand=True)

        card = tk.Frame(outer, bg=C["bg1"], padx=48, pady=40)
        card.pack(expand=True)

        # Title
        tk.Label(
            card, text="\U0001f4e6  ALLOCATION TERMINAL",
            bg=C["bg1"], fg=C["blue"],
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w")
        tk.Label(
            card,
            text="Tous les champs sont obligatoires."
                 " L'action decremente le stock immediatement.",
            bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(2, 18))
        tk.Frame(card, bg=C["border"], height=1).pack(fill="x", pady=(0, 22))

        # ── Agent ──────────────────────────────────────────────────────────
        tk.Label(
            card, text="AGENT  (actifs uniquement)",
            bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        self._agent_var = tk.StringVar()
        self._agent_combo = ttk.Combobox(
            card, textvariable=self._agent_var,
            state="readonly", width=64, font=("Consolas", 10),
        )
        self._agent_combo.pack(anchor="w", pady=(4, 18), ipady=6)

        # ── PPE + Lot ──────────────────────────────────────────────────────
        tk.Label(
            card, text="EPI + LOT  (stock disponible uniquement)",
            bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        self._stock_var = tk.StringVar()
        self._stock_combo = ttk.Combobox(
            card, textvariable=self._stock_var,
            state="readonly", width=64, font=("Consolas", 10),
        )
        self._stock_combo.pack(anchor="w", pady=(4, 0), ipady=6)
        self._stock_combo.bind("<<ComboboxSelected>>", self._on_stock_selected)

        self._stock_detail_var = tk.StringVar(value="")
        tk.Label(
            card, textvariable=self._stock_detail_var,
            bg=C["bg1"], fg=C["t1"], font=("Consolas", 8),
        ).pack(anchor="w", pady=(3, 18))

        # ── Chantier ───────────────────────────────────────────────────────
        tk.Label(
            card, text="CHANTIER / SITE  (min 3 caracteres)",
            bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        self._chantier_var = tk.StringVar()
        tk.Entry(
            card, textvariable=self._chantier_var,
            bg=C["bg2"], fg=C["t0"],
            insertbackground=C["blue"],
            font=("Consolas", 11), relief="flat", bd=1, width=64,
        ).pack(anchor="w", pady=(4, 22), ipady=8)

        # ── Error label ────────────────────────────────────────────────────
        self._alloc_err_var = tk.StringVar(value="")
        tk.Label(
            card, textvariable=self._alloc_err_var,
            bg=C["bg1"], fg=C["red"], font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(0, 10))

        # ── Issue button ───────────────────────────────────────────────────
        self._alloc_btn = tk.Button(
            card,
            text="\u2705  EMETTRE L'EQUIPEMENT",
            bg=C["green"], fg=C["bg0"],
            font=("Segoe UI", 14, "bold"),
            relief="flat", cursor="hand2",
            padx=20, pady=20, width=44,
            command=self._do_allocate,
        )
        self._alloc_btn.pack(fill="x", pady=(0, 6))
        tk.Label(
            card,
            text="Action irreversible \u2014 decremente le stock a l'emission.",
            bg=C["bg1"], fg=C["t1"], font=("Segoe UI", 7, "italic"),
        ).pack()

    def _refresh_alloc_tab(self) -> None:
        # ── Agents ─────────────────────────────────────────────────────────
        self._agent_map = {}
        labels_a: list[str] = []
        for a in _get_active_agents():
            lbl = (
                f"{a['agent_id']}  \u2500  "
                f"{a['full_name']}  ({a['job_class']})"
            )
            self._agent_map[lbl] = a["agent_id"]
            labels_a.append(lbl)
        self._agent_combo["values"] = labels_a
        if labels_a:
            self._agent_combo.current(0)

        # ── Stocks ─────────────────────────────────────────────────────────
        self._stock_map = {}
        labels_s: list[str] = []
        for s in _get_available_stocks():
            cost = _fmt_mad(s["unit_cost_centimes"])
            lbl = (
                f"[{s['category'][:11]:<11}]  "
                f"{s['description'][:30]:<30}  "
                f"LOT:{s['lot_number']:<18}  "
                f"QTY:{s['qty']:<5} {cost}"
            )
            self._stock_map[lbl] = {
                "stock_id":           s["stock_id"],
                "description":        s["description"],
                "lifespan_days":      s["lifespan_days"],
                "unit_cost_centimes": s["unit_cost_centimes"],
                "qty":                s["qty"],
                "lot_number":         s["lot_number"],
            }
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
            self._stock_detail_var.set(
                f"  Duree de vie : {s['lifespan_days']} jour(s)   \u00b7   "
                f"Cout unitaire : {_fmt_mad(s['unit_cost_centimes'])}   \u00b7   "
                f"Stock restant : {s['qty']}"
            )
        else:
            self._stock_detail_var.set("")

    def _do_allocate(self) -> None:
        self._alloc_btn.config(state="disabled",
                               text="\u23f3  TRAITEMENT EN COURS\u2026")
        self.update()
        try:
            agent_lbl = self._agent_var.get().strip()
            stock_lbl = self._stock_var.get().strip()
            chantier  = self._chantier_var.get().strip()

            if not agent_lbl or agent_lbl not in self._agent_map:
                self._alloc_err_var.set(
                    "\u274c Selectionnez un agent valide."
                )
                return
            if not stock_lbl or stock_lbl not in self._stock_map:
                self._alloc_err_var.set(
                    "\u274c Selectionnez un EPI valide."
                )
                return
            if len(chantier) < 3:
                self._alloc_err_var.set(
                    "\u274c Chantier invalide (min 3 caracteres)."
                )
                return

            ok, msg = allocate_ppe(
                self._agent_map[agent_lbl],
                self._stock_map[stock_lbl]["stock_id"],
                chantier,
            )
            if ok:
                self._alloc_err_var.set("")
                # Extraire tx_id du message "TX-XXXXXX EMIS"
                import re as _re
                m_tx = _re.search(r'TX-(\d+)', msg)
                tx_num = int(m_tx.group(1)) if m_tx else None
                messagebox.showinfo(
                    "\u2705 ALLOCATION REUSSIE", msg, parent=self
                )
                self._chantier_var.set("")
                self._refresh_all()
                # Offre automatique du bon PDF
                if tx_num is not None:
                    if messagebox.askyesno(
                        "\U0001f4c4 BON D'ATTRIBUTION",
                        f"Generer et ouvrir le bon d'attribution PDF\n"
                        f"pour TX-{tx_num:06d} ?",
                        parent=self,
                    ):
                        ok_p, res_p = pdf_bon_allocation(tx_num)
                        if ok_p:
                            _open_pdf(res_p)
                            self._set_status(
                                f"Bon PDF genere \u2014 {res_p}"
                            )
                        else:
                            messagebox.showerror("Erreur PDF", res_p, parent=self)
            else:
                self._alloc_err_var.set(f"\u274c {msg}")
                self._set_status(f"Allocation refusee : {msg}")
        finally:
            self._alloc_btn.config(
                state="normal",
                text="\u2705  EMETTRE L'EQUIPEMENT",
            )

    # =========================================================================
    #  TAB 3 — NON-CONFORMITÉS ISO 9001
    # =========================================================================

    def _build_nc_tab(self) -> None:
        root = self._tab_nc

        tk.Label(
            root,
            text="  ALLOCATIONS ACTIVES \u2014 "
                 "SELECTIONNEZ PUIS TERMINEZ EN NON-CONFORMITE",
            bg=C["bg0"], fg=C["t1"], font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", padx=20, pady=(14, 4))

        # ── Treeview ───────────────────────────────────────────────────────
        tf = tk.Frame(root, bg=C["bg0"])
        tf.pack(fill="both", expand=True, padx=20)

        cols = ("tx_id", "agent", "ppe", "lot", "chantier",
                "emis", "expire", "statut")
        self._nc_tree = ttk.Treeview(
            tf, columns=cols, show="headings", selectmode="browse"
        )

        _col_cfg = [
            ("tx_id",    "TX-ID",        72, "center"),
            ("agent",    "AGENT",        170, "w"),
            ("ppe",      "EPI",          235, "w"),
            ("lot",      "LOT N\u00b0",  118, "center"),
            ("chantier", "CHANTIER",     108, "w"),
            ("emis",     "\u00c9MIS",     90, "center"),
            ("expire",   "EXPIRE",        90, "center"),
            ("statut",   "STATUT",       105, "center"),
        ]
        for col, hdr_txt, w, anch in _col_cfg:
            self._nc_tree.heading(col, text=hdr_txt)
            self._nc_tree.column(col, width=w, minwidth=50, anchor=anch)

        self._nc_tree.tag_configure("Compliant", foreground=C["green"])
        self._nc_tree.tag_configure("Expired",   foreground=C["red"])
        self._nc_tree.tag_configure("Degraded",  foreground=C["orange"])
        self._nc_tree.bind("<<TreeviewSelect>>", self._on_nc_select)

        vsb = ttk.Scrollbar(tf, orient="vertical",   command=self._nc_tree.yview)
        hsb = ttk.Scrollbar(tf, orient="horizontal", command=self._nc_tree.xview)
        self._nc_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._nc_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

        # ── Control panel ──────────────────────────────────────────────────
        ctrl = tk.Frame(root, bg=C["bg2"], padx=20, pady=16)
        ctrl.pack(fill="x", padx=20, pady=(10, 16))

        # Selected TX display
        tx_row = tk.Frame(ctrl, bg=C["bg2"])
        tx_row.pack(fill="x", pady=(0, 10))
        tk.Label(
            tx_row, text="SELECTION :",
            bg=C["bg2"], fg=C["t1"], font=("Segoe UI", 8, "bold"),
        ).pack(side="left")
        self._nc_sel_var = tk.StringVar(value="(aucune)")
        tk.Label(
            tx_row, textvariable=self._nc_sel_var,
            bg=C["bg2"], fg=C["blue"], font=("Consolas", 10, "bold"),
        ).pack(side="left", padx=(10, 0))

        # Reason combobox
        reason_row = tk.Frame(ctrl, bg=C["bg2"])
        reason_row.pack(fill="x", pady=(0, 14))
        tk.Label(
            reason_row, text="MOTIF NON-CONFORMITE :",
            bg=C["bg2"], fg=C["t1"], font=("Segoe UI", 8, "bold"),
        ).pack(side="left")

        self._nc_reason_var = tk.StringVar()
        nc_cb = ttk.Combobox(
            reason_row, textvariable=self._nc_reason_var,
            values=[
                "Dechirure Mecanique",
                "Saturation Solvant",
                "Perte / Vol",
                "Defaut Fabricant",
                "Rupture Structure",
                "Brulure / Projection",
                "Duree de Vie Atteinte",
                "Degradation Visuelle",
                "Non-Conformite Reception Lot",
                "Autre (voir rapport)",
            ],
            state="readonly", width=38, font=("Consolas", 10),
        )
        nc_cb.pack(side="left", padx=(10, 0), ipady=4)
        nc_cb.current(0)

        # Bouton Journal PDF + Terminate
        pdf_nc_row = tk.Frame(ctrl, bg=C["bg2"])
        pdf_nc_row.pack(fill="x", pady=(0, 8))
        tk.Button(
            pdf_nc_row, text="\U0001f4c4  JOURNAL NC PDF",
            bg=C["purple"], fg=C["bg0"],
            font=("Segoe UI", 9, "bold"),
            relief="flat", cursor="hand2", padx=12, pady=10,
            command=self._pdf_journal_nc,
        ).pack(side="right")

        # Terminate button
        tk.Button(
            ctrl,
            text="\U0001f534  TERMINER & ENREGISTRER LA NON-CONFORMITE",
            bg=C["red"], fg="#FFFFFF",
            font=("Segoe UI", 11, "bold"),
            relief="flat", cursor="hand2", padx=16, pady=13,
            command=self._do_scrap,
        ).pack(fill="x")

    def _refresh_nc_tab(self) -> None:
        self._nc_tree.delete(*self._nc_tree.get_children())
        self._nc_tx_id = None
        self._nc_sel_var.set("(aucune)")

        for row in _get_active_allocs():
            emis   = time.strftime(
                "%d/%m/%Y", time.localtime(row["timestamp_issued"])
            )
            expire = time.strftime(
                "%d/%m/%Y", time.localtime(row["expected_death_timestamp"])
            )
            status = row["status"]
            self._nc_tree.insert(
                "", "end",
                iid=str(row["tx_id"]),
                tags=(status,),
                values=(
                    f"TX-{row['tx_id']:06d}",
                    row["agent_name"], row["ppe_desc"],
                    row["lot_number"], row["chantier_location"],
                    emis, expire, status.upper(),
                ),
            )

    def _on_nc_select(self, _event) -> None:
        sel = self._nc_tree.selection()
        if not sel:
            self._nc_tx_id = None
            self._nc_sel_var.set("(aucune)")
            return
        try:
            self._nc_tx_id = int(sel[0])
            vals = self._nc_tree.item(sel[0], "values")
            self._nc_sel_var.set(
                f"{vals[0]}  \u2500  {vals[1]}  \u2500  {vals[2][:42]}"
            )
        except (ValueError, IndexError):
            self._nc_tx_id = None
            self._nc_sel_var.set("(erreur selection)")

    def _do_scrap(self) -> None:
        if self._nc_tx_id is None:
            messagebox.showwarning(
                "Selection requise",
                "Selectionnez une allocation dans la liste.",
                parent=self,
            )
            return

        reason = self._nc_reason_var.get().strip()
        if not reason:
            messagebox.showwarning(
                "Motif requis",
                "Selectionnez un motif de non-conformite.",
                parent=self,
            )
            return

        vals    = self._nc_tree.item(str(self._nc_tx_id), "values")
        tx_disp = vals[0] if vals else f"TX-{self._nc_tx_id:06d}"

        if not messagebox.askyesno(
            "\u26a0 CONFIRMATION NON-CONFORMITE",
            f"Terminer l'allocation {tx_disp} ?\n\n"
            f"Motif : {reason}\n\n"
            "Cette action est irreversible et sera tracee\n"
            "dans le registre ISO 9001 / PDCA.",
            icon="warning", parent=self,
        ):
            return

        ok, msg = scrap_allocation(self._nc_tx_id, reason)
        if ok:
            messagebox.showinfo(
                "\u2705 NON-CONFORMITE ENREGISTREE", msg, parent=self
            )
            self._nc_tx_id = None
            self._nc_sel_var.set("(aucune)")
            self._refresh_all()
        else:
            messagebox.showerror("\u274c ERREUR", msg, parent=self)
            self._set_status(f"Erreur scrap : {msg}")

    # =========================================================================
    #  AUDIT SCANNER
    # =========================================================================

    def _run_audit(self) -> None:
        self._set_status("Scan integrite HMAC en cours\u2026")
        self.update()

        anomalies = run_audit_scanner()
        self._refresh_all()

        if not anomalies:
            messagebox.showinfo(
                "\u2705 BASE INTEGRE",
                "Scan HMAC termine.\n\n"
                "Toutes les signatures cryptographiques sont valides.\n"
                "Aucune falsification ni expiration non traitee detectee.",
                parent=self,
            )
            self._set_status("Scan HMAC \u2014 base integre, aucune anomalie.")
        else:
            lines = [f"\u26a0  {len(anomalies)} ANOMALIE(S) DETECTEE(S)\n"]
            for a in anomalies:
                lines.append(f"  TX-{a['tx_id']:06d}  [{a['status']}]")
                for issue in a["issues"]:
                    lines.append(f"    \u26d4 {issue}")
                lines.append("")
            messagebox.showwarning(
                "\u26d4 ANOMALIES DETECTEES",
                "\n".join(lines),
                parent=self,
            )
            self._set_status(
                f"Scan HMAC \u2014 {len(anomalies)} anomalie(s). Action requise."
            )

    # =========================================================================
    #  MASTER REFRESH
    # =========================================================================


    # =========================================================================
    #  ACTIONS PDF — Délégation au moteur §6.5
    # =========================================================================

    def _pdf_etat_alloc(self) -> None:
        self._set_status("Generation PDF etat allocations\u2026")
        self.update()
        ok, res = pdf_etat_allocations()
        if ok:
            self._set_status(f"PDF genere \u2014 {res}")
            if messagebox.askyesno(
                "\U0001f4c4 PDF pret",
                f"Etat des allocations genere.\n\nOuvrir le fichier ?\n{res}",
                parent=self,
            ):
                _open_pdf(res)
        else:
            messagebox.showwarning("PDF", res, parent=self)
            self._set_status(f"PDF annule : {res}")

    def _pdf_journal_nc(self) -> None:
        self._set_status("Generation PDF journal NC\u2026")
        self.update()
        ok, res = pdf_journal_nc()
        if ok:
            self._set_status(f"PDF genere \u2014 {res}")
            if messagebox.askyesno(
                "\U0001f4c4 PDF pret",
                f"Journal non-conformites genere.\n\nOuvrir le fichier ?\n{res}",
                parent=self,
            ):
                _open_pdf(res)
        else:
            messagebox.showwarning("PDF", res, parent=self)
            self._set_status(f"PDF annule : {res}")

    def _pdf_inventaire(self) -> None:
        self._set_status("Generation PDF inventaire\u2026")
        self.update()
        ok, res = pdf_inventaire_vault()
        if ok:
            self._set_status(f"PDF genere \u2014 {res}")
            if messagebox.askyesno(
                "\U0001f4c4 PDF pret",
                f"Inventaire Vault genere.\n\nOuvrir le fichier ?\n{res}",
                parent=self,
            ):
                _open_pdf(res)
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

    # =========================================================================
    #  TAB 4 — CONFIGURATION
    # =========================================================================

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

    # ── Sub-tab: Agents ───────────────────────────────────────────────────────

    def _build_cfg_agents(self) -> None:
        root = self._tab_cfg_agents

        hdr = tk.Frame(root, bg=C["bg0"])
        hdr.pack(fill="x", padx=20, pady=(14, 6))
        tk.Label(hdr, text="\U0001f464  GESTION DES AGENTS",
                 bg=C["bg0"], fg=C["blue"],
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        btns = tk.Frame(hdr, bg=C["bg0"])
        btns.pack(side="right")
        tk.Button(btns, text="\u270f  MODIFIER STATUT",
                  bg=C["orange"], fg=C["bg0"],
                  font=("Segoe UI", 9, "bold"),
                  relief="flat", cursor="hand2", padx=12, pady=6,
                  command=self._open_status_wizard,
                  ).pack(side="right", padx=(6, 0))
        tk.Button(btns, text="\uff0b  NOUVEL AGENT",
                  bg=C["green"], fg=C["bg0"],
                  font=("Segoe UI", 9, "bold"),
                  relief="flat", cursor="hand2", padx=12, pady=6,
                  command=self._open_agent_wizard,
                  ).pack(side="right")

        tk.Frame(root, bg=C["border"], height=1).pack(fill="x", padx=20, pady=(0, 6))

        tf = tk.Frame(root, bg=C["bg0"])
        tf.pack(fill="both", expand=True, padx=20, pady=(0, 14))

        cols = ("agent_id", "cin", "full_name", "job_class", "suit", "boot", "status")
        self._cfg_agents_tree = ttk.Treeview(tf, columns=cols,
                                              show="headings", selectmode="browse")
        for col, h, w, anch in [
            ("agent_id",  "ID AGENT",    90, "center"),
            ("cin",       "CIN",        110, "center"),
            ("full_name", "NOM COMPLET",200, "w"),
            ("job_class", "POSTE",      165, "w"),
            ("suit",      "TAILLE",      75, "center"),
            ("boot",      "POINTURE",    80, "center"),
            ("status",    "STATUT",     105, "center"),
        ]:
            self._cfg_agents_tree.heading(col, text=h)
            self._cfg_agents_tree.column(col, width=w, minwidth=40, anchor=anch)

        self._cfg_agents_tree.tag_configure("Active",     foreground=C["green"])
        self._cfg_agents_tree.tag_configure("Suspended",  foreground=C["orange"])
        self._cfg_agents_tree.tag_configure("Terminated", foreground=C["red"])

        vsb = ttk.Scrollbar(tf, orient="vertical",
                             command=self._cfg_agents_tree.yview)
        self._cfg_agents_tree.configure(yscrollcommand=vsb.set)
        self._cfg_agents_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

    # ── Sub-tab: Arsenal ──────────────────────────────────────────────────────

    def _build_cfg_arsenal(self) -> None:
        root = self._tab_cfg_arsenal

        hdr = tk.Frame(root, bg=C["bg0"])
        hdr.pack(fill="x", padx=20, pady=(14, 6))
        tk.Label(hdr, text="\U0001f5c2  CATALOGUE ARSENAL EPI",
                 bg=C["bg0"], fg=C["blue"],
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Button(hdr, text="\uff0b  NOUVEL EPI",
                  bg=C["green"], fg=C["bg0"],
                  font=("Segoe UI", 9, "bold"),
                  relief="flat", cursor="hand2", padx=12, pady=6,
                  command=self._open_arsenal_wizard,
                  ).pack(side="right")

        tk.Frame(root, bg=C["border"], height=1).pack(fill="x", padx=20, pady=(0, 6))

        tf = tk.Frame(root, bg=C["bg0"])
        tf.pack(fill="both", expand=True, padx=20, pady=(0, 14))

        cols = ("code", "cat", "desc", "life", "cost")
        self._cfg_arsenal_tree = ttk.Treeview(tf, columns=cols,
                                               show="headings", selectmode="browse")
        for col, h, w, anch in [
            ("code", "CODE EPI",     120, "center"),
            ("cat",  "CATEGORIE",    120, "w"),
            ("desc", "DESCRIPTION",  300, "w"),
            ("life", "DUREE (j)",     80, "center"),
            ("cost", "COUT UNIT.",   115, "center"),
        ]:
            self._cfg_arsenal_tree.heading(col, text=h)
            self._cfg_arsenal_tree.column(col, width=w, minwidth=40, anchor=anch)

        vsb = ttk.Scrollbar(tf, orient="vertical",
                             command=self._cfg_arsenal_tree.yview)
        self._cfg_arsenal_tree.configure(yscrollcommand=vsb.set)
        self._cfg_arsenal_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

    # ── Sub-tab: Stock ────────────────────────────────────────────────────────

    def _build_cfg_stock(self) -> None:
        root = self._tab_cfg_stock

        hdr = tk.Frame(root, bg=C["bg0"])
        hdr.pack(fill="x", padx=20, pady=(14, 6))
        tk.Label(hdr, text="\U0001f4e6  INVENTAIRE STOCK / LOTS",
                 bg=C["bg0"], fg=C["blue"],
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Button(hdr, text="\uff0b  NOUVELLE RECEPTION",
                  bg=C["blue"], fg=C["bg0"],
                  font=("Segoe UI", 9, "bold"),
                  relief="flat", cursor="hand2", padx=12, pady=6,
                  command=self._open_stock_wizard,
                  ).pack(side="right")
        tk.Button(hdr, text="\U0001f4c4  INVENTAIRE PDF",
                  bg=C["purple"], fg=C["bg0"],
                  font=("Segoe UI", 9, "bold"),
                  relief="flat", cursor="hand2", padx=12, pady=6,
                  command=self._pdf_inventaire,
                  ).pack(side="right", padx=(0, 6))

        tk.Frame(root, bg=C["border"], height=1).pack(fill="x", padx=20, pady=(0, 6))

        tf = tk.Frame(root, bg=C["bg0"])
        tf.pack(fill="both", expand=True, padx=20, pady=(0, 14))

        cols = ("stock_id", "code", "desc", "lot", "qty", "seuil", "etat")
        self._cfg_stock_tree = ttk.Treeview(tf, columns=cols,
                                             show="headings", selectmode="browse")
        for col, h, w, anch in [
            ("stock_id", "ID",          55, "center"),
            ("code",     "CODE EPI",   110, "center"),
            ("desc",     "EPI",        250, "w"),
            ("lot",      "LOT N\u00b0",145, "center"),
            ("qty",      "QTY",         65, "center"),
            ("seuil",    "SEUIL",       65, "center"),
            ("etat",     "ETAT",        90, "center"),
        ]:
            self._cfg_stock_tree.heading(col, text=h)
            self._cfg_stock_tree.column(col, width=w, minwidth=40, anchor=anch)

        self._cfg_stock_tree.tag_configure("OK",      foreground=C["green"])
        self._cfg_stock_tree.tag_configure("LOW",     foreground=C["orange"])
        self._cfg_stock_tree.tag_configure("RUPTURE", foreground=C["red"])

        vsb = ttk.Scrollbar(tf, orient="vertical",
                             command=self._cfg_stock_tree.yview)
        self._cfg_stock_tree.configure(yscrollcommand=vsb.set)
        self._cfg_stock_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

    # ── Refresh config ────────────────────────────────────────────────────────

    def _refresh_config_tab(self) -> None:
        self._refresh_cfg_agents()
        self._refresh_cfg_arsenal()
        self._refresh_cfg_stock()

    def _refresh_cfg_agents(self) -> None:
        self._cfg_agents_tree.delete(*self._cfg_agents_tree.get_children())
        for a in _get_all_agents():
            self._cfg_agents_tree.insert("", "end",
                                          iid=a["agent_id"],
                                          tags=(a["status"],), values=(
                a["agent_id"], a["cin"], a["full_name"],
                a["job_class"], a["suit_size"], a["boot_size"], a["status"],
            ))

    def _refresh_cfg_arsenal(self) -> None:
        self._cfg_arsenal_tree.delete(*self._cfg_arsenal_tree.get_children())
        for r in _get_all_arsenal():
            self._cfg_arsenal_tree.insert("", "end", values=(
                r["ppe_code"], r["category"], r["description"],
                r["lifespan_days"], _fmt_mad(r["unit_cost_centimes"]),
            ))

    def _refresh_cfg_stock(self) -> None:
        self._cfg_stock_tree.delete(*self._cfg_stock_tree.get_children())
        for r in _get_all_vault():
            qty, seuil = r["qty"], r["min_threshold"]
            if qty == 0:
                tag, etat = "RUPTURE", "RUPTURE"
            elif qty <= seuil:
                tag, etat = "LOW",     "ALERTE"
            else:
                tag, etat = "OK",      "OK"
            self._cfg_stock_tree.insert("", "end", tags=(tag,), values=(
                r["stock_id"], r["ppe_code"], r["description"],
                r["lot_number"], qty, seuil, etat,
            ))

    # =========================================================================
    #  WIZARDS DE CONFIGURATION
    # =========================================================================

    @staticmethod
    def _make_modal(parent: tk.Misc, title: str,
                    width: int, height: int) -> tk.Toplevel:
        """Crée et centre une Toplevel modale au thème sombre."""
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
    def _wz_entry(parent: tk.Widget, var: tk.StringVar,
                  width: int = 38) -> tk.Entry:
        """Entry dark pour wizards."""
        return tk.Entry(parent, textvariable=var,
                        bg=C["bg2"], fg=C["t0"],
                        insertbackground=C["blue"],
                        font=("Consolas", 11), relief="flat", bd=1, width=width)

    @staticmethod
    def _wz_label(parent: tk.Widget, text: str) -> None:
        """Label champ pour wizards."""
        tk.Label(parent, text=text, bg=C["bg1"], fg=C["t1"],
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")

    @staticmethod
    def _wz_btn_submit(parent: tk.Widget, text: str,
                       color: str, cmd) -> tk.Button:
        return tk.Button(parent, text=text,
                         bg=color, fg=C["bg0"],
                         font=("Segoe UI", 12, "bold"),
                         relief="flat", cursor="hand2",
                         pady=14, command=cmd)

    # ── Wizard: Nouvel Agent ──────────────────────────────────────────────────

    def _open_agent_wizard(self) -> None:
        win = self._make_modal(self, "Nouvel Agent", 530, 640)

        tk.Label(win, text="\U0001f464  NOUVEL AGENT",
                 bg=C["bg1"], fg=C["blue"],
                 font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=32, pady=(24, 2))
        tk.Label(win, text="Tous les champs sont obligatoires.",
                 bg=C["bg1"], fg=C["t1"],
                 font=("Segoe UI", 8)).pack(anchor="w", padx=32)
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x", padx=32, pady=(12, 20))

        form = tk.Frame(win, bg=C["bg1"])
        form.pack(fill="x", padx=32)

        v_id   = tk.StringVar(value="AGT-")
        v_cin  = tk.StringVar()
        v_name = tk.StringVar()
        v_job  = tk.StringVar(value="Peintre Industriel")
        v_suit = tk.StringVar(value="L")
        v_boot = tk.StringVar(value="42")

        for lbl, var in [
            ("IDENTIFIANT  (ex: AGT-007)", v_id),
            ("CIN  (ex: AB123456)",        v_cin),
            ("NOM COMPLET",                v_name),
        ]:
            self._wz_label(form, lbl)
            self._wz_entry(form, var).pack(anchor="w", pady=(3, 12), ipady=6)

        self._wz_label(form, "CLASSE / POSTE")
        ttk.Combobox(form, textvariable=v_job,
                     values=["Peintre Industriel", "Chef d'Equipe", "Sableur",
                             "Operateur Cabine", "Technicien Maintenance",
                             "Agent Securite", "Magasinier", "Autre"],
                     font=("Consolas", 10), width=36,
                     ).pack(anchor="w", pady=(3, 12), ipady=5)

        # Taille + pointure côte à côte
        row2 = tk.Frame(form, bg=C["bg1"])
        row2.pack(anchor="w", fill="x", pady=(0, 12))

        lf = tk.Frame(row2, bg=C["bg1"])
        lf.pack(side="left", padx=(0, 24))
        self._wz_label(lf, "TAILLE COMBINAISON")
        ttk.Combobox(lf, textvariable=v_suit,
                     values=["XS","S","M","L","XL","XXL"],
                     state="readonly", font=("Consolas", 11), width=8,
                     ).pack(anchor="w", pady=(3, 0), ipady=5)

        rf = tk.Frame(row2, bg=C["bg1"])
        rf.pack(side="left")
        self._wz_label(rf, "POINTURE  (36-48)")
        self._wz_entry(rf, v_boot, width=8).pack(anchor="w", pady=(3, 0), ipady=6)

        err_var = tk.StringVar()
        tk.Label(win, textvariable=err_var, bg=C["bg1"], fg=C["red"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=32, pady=(8, 4))

        def _submit():
            try:
                boot = int(v_boot.get().strip())
            except ValueError:
                err_var.set("\u274c Pointure doit etre un entier.")
                return
            ok, msg = add_agent(
                v_id.get(), v_cin.get(), v_name.get(),
                v_job.get(), v_suit.get(), boot,
            )
            if ok:
                messagebox.showinfo("\u2705 Agent enregistre", msg, parent=win)
                win.destroy()
                self._refresh_all()
            else:
                err_var.set(f"\u274c {msg}")

        self._wz_btn_submit(win, "\u2705  ENREGISTRER L'AGENT",
                            C["green"], _submit
                            ).pack(fill="x", padx=32, pady=(0, 6))
        tk.Button(win, text="ANNULER", bg=C["bg3"], fg=C["t1"],
                  font=("Segoe UI", 9), relief="flat", cursor="hand2",
                  pady=8, command=win.destroy).pack(fill="x", padx=32)

    # ── Wizard: Modifier Statut ───────────────────────────────────────────────

    def _open_status_wizard(self) -> None:
        sel = self._cfg_agents_tree.selection()
        if not sel:
            messagebox.showwarning("Selection requise",
                                   "Selectionnez un agent dans la liste.",
                                   parent=self)
            return

        vals     = self._cfg_agents_tree.item(sel[0], "values")
        agent_id = vals[0]     # col 0
        cur_stat = vals[6]     # col 6

        win = self._make_modal(self, f"Statut — {agent_id}", 400, 320)

        tk.Label(win, text="\u270f  MODIFIER STATUT",
                 bg=C["bg1"], fg=C["orange"],
                 font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=32, pady=(24, 2))
        tk.Label(win, text=f"Agent : {vals[2]}",
                 bg=C["bg1"], fg=C["t0"],
                 font=("Consolas", 10)).pack(anchor="w", padx=32, pady=(0, 2))
        tk.Label(win, text=f"Statut actuel : {cur_stat}",
                 bg=C["bg1"], fg=C["t1"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=32)
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x", padx=32, pady=(14, 20))

        form = tk.Frame(win, bg=C["bg1"])
        form.pack(fill="x", padx=32)
        self._wz_label(form, "NOUVEAU STATUT")
        v_status = tk.StringVar(value=cur_stat)
        ttk.Combobox(form, textvariable=v_status,
                     values=["Active", "Suspended", "Terminated"],
                     state="readonly", font=("Consolas", 11), width=20,
                     ).pack(anchor="w", pady=(3, 0), ipady=5)

        err_var = tk.StringVar()
        tk.Label(win, textvariable=err_var, bg=C["bg1"], fg=C["red"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=32, pady=(10, 4))

        def _submit():
            ok, msg = update_agent_status(agent_id, v_status.get())
            if ok:
                messagebox.showinfo("\u2705 Statut modifie", msg, parent=win)
                win.destroy()
                self._refresh_all()
            else:
                err_var.set(f"\u274c {msg}")

        self._wz_btn_submit(win, "\u2705  CONFIRMER",
                            C["orange"], _submit
                            ).pack(fill="x", padx=32, pady=(0, 6))
        tk.Button(win, text="ANNULER", bg=C["bg3"], fg=C["t1"],
                  font=("Segoe UI", 9), relief="flat", cursor="hand2",
                  pady=8, command=win.destroy).pack(fill="x", padx=32)

    # ── Wizard: Nouvel EPI ────────────────────────────────────────────────────

    def _open_arsenal_wizard(self) -> None:
        win = self._make_modal(self, "Nouvel EPI — Catalogue Arsenal", 530, 590)

        tk.Label(win, text="\U0001f5c2  NOUVEL EPI AU CATALOGUE",
                 bg=C["bg1"], fg=C["blue"],
                 font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=32, pady=(24, 2))
        tk.Label(win,
                 text="Cout saisi en MAD (ex: 85.50) \u2014 converti en centimes sans float.",
                 bg=C["bg1"], fg=C["t1"],
                 font=("Segoe UI", 8)).pack(anchor="w", padx=32)
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
        ttk.Combobox(form, textvariable=v_cat,
                     values=["Respiratoire","Oeil","Tete","Tenue","Pied",
                             "Main","Ouie","Anti-Chute","Visibilite","Autre"],
                     font=("Consolas", 10), width=36,
                     ).pack(anchor="w", pady=(3, 12), ipady=5)

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
        tk.Label(win, textvariable=err_var, bg=C["bg1"], fg=C["red"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=32, pady=(10, 4))

        def _submit():
            try:
                life = int(v_life.get().strip())
            except ValueError:
                err_var.set("\u274c Duree de vie invalide (entier requis).")
                return
            ok_c, centimes = _parse_mad_input(v_cost.get())
            if not ok_c:
                err_var.set("\u274c Cout invalide (ex: 85.50).")
                return
            ok, msg = add_ppe_type(
                v_code.get(), v_cat.get(), v_desc.get(), life, centimes,
            )
            if ok:
                messagebox.showinfo("\u2705 EPI enregistre", msg, parent=win)
                win.destroy()
                self._refresh_all()
            else:
                err_var.set(f"\u274c {msg}")

        self._wz_btn_submit(win, "\u2705  AJOUTER AU CATALOGUE",
                            C["green"], _submit
                            ).pack(fill="x", padx=32, pady=(0, 6))
        tk.Button(win, text="ANNULER", bg=C["bg3"], fg=C["t1"],
                  font=("Segoe UI", 9), relief="flat", cursor="hand2",
                  pady=8, command=win.destroy).pack(fill="x", padx=32)

    # ── Wizard: Réception Stock ───────────────────────────────────────────────

    def _open_stock_wizard(self) -> None:
        arsenal = _get_all_arsenal()
        if not arsenal:
            messagebox.showwarning(
                "Catalogue vide",
                "Aucune reference EPI dans le catalogue.\n"
                "Ajoutez d'abord un EPI via l'onglet Arsenal EPI.",
                parent=self,
            )
            return

        ppe_labels  = [f"{r['ppe_code']}  \u2014  {r['description']}"
                       for r in arsenal]
        code_map    = {lbl: r["ppe_code"] for lbl, r in zip(ppe_labels, arsenal)}

        win = self._make_modal(self, "Nouvelle Reception Stock", 530, 490)

        tk.Label(win, text="\U0001f4e6  RECEPTION STOCK",
                 bg=C["bg1"], fg=C["blue"],
                 font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=32, pady=(24, 2))
        tk.Label(win, text="Enregistrement d'un nouveau lot recu en magasin.",
                 bg=C["bg1"], fg=C["t1"],
                 font=("Segoe UI", 8)).pack(anchor="w", padx=32)
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x", padx=32, pady=(12, 20))

        form = tk.Frame(win, bg=C["bg1"])
        form.pack(fill="x", padx=32)

        v_ppe   = tk.StringVar(value=ppe_labels[0])
        v_lot   = tk.StringVar()
        v_qty   = tk.StringVar(value="100")
        v_seuil = tk.StringVar(value="10")

        self._wz_label(form, "REFERENCE EPI")
        ttk.Combobox(form, textvariable=v_ppe,
                     values=ppe_labels, state="readonly",
                     font=("Consolas", 10), width=52,
                     ).pack(anchor="w", pady=(3, 12), ipady=5)

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
        tk.Label(win, textvariable=err_var, bg=C["bg1"], fg=C["red"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=32, pady=(12, 4))

        def _submit():
            lbl = v_ppe.get()
            if lbl not in code_map:
                err_var.set("\u274c Selectionnez une reference EPI.")
                return
            try:
                qty   = int(v_qty.get().strip())
                seuil = int(v_seuil.get().strip())
            except ValueError:
                err_var.set("\u274c Quantite et seuil doivent etre des entiers.")
                return
            ok, msg = add_stock_lot(code_map[lbl], v_lot.get(), qty, seuil)
            if ok:
                messagebox.showinfo("\u2705 Reception enregistree", msg, parent=win)
                win.destroy()
                self._refresh_all()
            else:
                err_var.set(f"\u274c {msg}")

        self._wz_btn_submit(win, "\u2705  CONFIRMER LA RECEPTION",
                            C["blue"], _submit
                            ).pack(fill="x", padx=32, pady=(0, 6))
        tk.Button(win, text="ANNULER", bg=C["bg3"], fg=C["t1"],
                  font=("Segoe UI", 9), relief="flat", cursor="hand2",
                  pady=8, command=win.destroy).pack(fill="x", padx=32)


# =============================================================================
#  §8 — POINT D'ENTRÉE
# =============================================================================

def main() -> None:
    initialize_database()
    app = PPEVaultApp()
    app.mainloop()


if __name__ == "__main__":
    main()