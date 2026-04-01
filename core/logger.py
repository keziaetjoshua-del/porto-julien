"""
core/logger.py
─────────────────────────────────────────────────────────────────────
Logging ultra-clair + trades.csv beau pour les impôts.
Inspiration kirillk_web3 : colonnes claires, lisibles par un comptable.
"""

import csv
import os
import logging
from datetime import datetime

log = logging.getLogger("trade_logger")

# Colonnes du CSV — claires pour les impôts
CSV_COLUMNS = [
    "date",                # YYYY-MM-DD
    "heure",               # HH:MM:SS
    "id_trade",            # Identifiant unique
    "marche",              # Nom du marché Polymarket
    "categorie",           # crypto
    "strategie",           # latency_sniper / market_making
    "direction",           # YES / NO
    "prix_entree",         # Prix d'achat (0.00 à 1.00)
    "prix_sortie",         # Prix de vente
    "taille_usdc",         # Montant misé en USDC
    "gain_perte_usdc",     # PnL réalisé en USDC
    "gain_perte_pct",      # PnL en %
    "raison_skip",         # Raison si trade skippé (vide sinon)
    "ev_estime",           # Expected Value estimée
    "edge",                # Edge calculé
    "kelly_fraction",      # Fraction Kelly utilisée
    "dry_run",             # true si simulation
    "note",                # Note libre
]


class TradeLogger:
    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        self._init_csv()
        log.info(f"📊 TradeLogger initialisé → {csv_path}")

    def _init_csv(self):
        """Crée le CSV avec headers si inexistant."""
        if not os.path.exists(self.csv_path):
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writeheader()
            log.info(f"  📄 Nouveau fichier CSV créé : {self.csv_path}")

    def log_trade(
        self,
        market_id: str,
        market_name: str,
        strategy: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        size_usdc: float,
        pnl_usdc: float,
        edge: float = 0.0,
        ev: float = 0.0,
        kelly_fraction: float = 0.25,
        dry_run: bool = True,
        note: str = ""
    ):
        """Enregistre un trade complet."""
        now = datetime.now()
        pnl_pct = (pnl_usdc / size_usdc * 100) if size_usdc > 0 else 0.0

        row = {
            "date": now.strftime("%Y-%m-%d"),
            "heure": now.strftime("%H:%M:%S"),
            "id_trade": f"{market_id[:8]}_{now.strftime('%H%M%S')}",
            "marche": market_name,
            "categorie": "crypto",
            "strategie": strategy,
            "direction": direction,
            "prix_entree": f"{entry_price:.4f}",
            "prix_sortie": f"{exit_price:.4f}",
            "taille_usdc": f"{size_usdc:.2f}",
            "gain_perte_usdc": f"{pnl_usdc:+.4f}",
            "gain_perte_pct": f"{pnl_pct:+.2f}%",
            "raison_skip": "",
            "ev_estime": f"{ev:.4f}",
            "edge": f"{edge:.4f}",
            "kelly_fraction": f"{kelly_fraction:.2f}",
            "dry_run": str(dry_run).lower(),
            "note": note,
        }

        self._write_row(row)
        self._log_console(row, pnl_usdc)

    def log_skip(
        self,
        market_id: str,
        market_name: str,
        strategy: str,
        reason: str,
        note: str = ""
    ):
        """Enregistre un skip (porte fermée, données manquantes, etc.)."""
        now = datetime.now()
        row = {
            "date": now.strftime("%Y-%m-%d"),
            "heure": now.strftime("%H:%M:%S"),
            "id_trade": f"SKIP_{market_id[:8]}_{now.strftime('%H%M%S')}",
            "marche": market_name,
            "categorie": "crypto",
            "strategie": strategy,
            "direction": "SKIP",
            "prix_entree": "",
            "prix_sortie": "",
            "taille_usdc": "0.00",
            "gain_perte_usdc": "0.00",
            "gain_perte_pct": "0.00%",
            "raison_skip": reason,
            "ev_estime": "",
            "edge": "",
            "kelly_fraction": "",
            "dry_run": "",
            "note": note,
        }
        self._write_row(row)

    def _write_row(self, row: dict):
        """Écrit une ligne dans le CSV."""
        try:
            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writerow(row)
        except Exception as e:
            log.error(f"❌ Impossible d'écrire dans le CSV : {e}")

    def _log_console(self, row: dict, pnl_usdc: float):
        """Affiche le trade de manière lisible dans les logs."""
        emoji = "🟢" if pnl_usdc >= 0 else "🔴"
        log.info(
            f"{emoji} TRADE | {row['marche'][:30]:<30} | "
            f"{row['strategie']:<16} | {row['direction']} | "
            f"entrée={row['prix_entree']} sortie={row['prix_sortie']} | "
            f"PnL={row['gain_perte_usdc']} ({row['gain_perte_pct']}) | "
            f"EV={row['ev_estime']} edge={row['edge']}"
        )
