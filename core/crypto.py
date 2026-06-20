import hashlib
import hmac
import sqlite3

# Sel cryptographique — NE PAS MODIFIER APRÈS PREMIER DÉPLOIEMENT
SECRET_SALT: bytes = b"ISOFU_EPI_MANAGER_ROGER_FERNANDO_BOUSKOURA"

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