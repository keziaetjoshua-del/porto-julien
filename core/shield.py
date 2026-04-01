"""
core/shield.py
─────────────────────────────────────────────────────────────────────
Bouclier anti-colère : pause 24h si perte journalière > 3%.
État sauvegardé sur disque pour survivre aux redémarrages Railway.
"""

import json
import os
import logging
from datetime import datetime, timedelta

log = logging.getLogger("shield")

STATE_FILE = "/app/data/shield_state.json"  # Volume persistant Railway


class Shield:
    def __init__(self, cfg: dict):
        self.max_daily_loss_pct = cfg.get("max_daily_loss_pct", 0.03)  # 3%
        self.pause_hours = cfg.get("pause_hours", 24)
        self.state = self._load_state()

    def _load_state(self) -> dict:
        """Charge l'état depuis le fichier (survit aux redémarrages)."""
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r") as f:
                    state = json.load(f)
                    log.info(f"🛡️  État bouclier chargé : {state}")
                    return state
        except Exception as e:
            log.warning(f"⚠️  Impossible de charger l'état bouclier : {e}")
        return self._default_state()

    def _default_state(self) -> dict:
        return {
            "daily_pnl": 0.0,
            "daily_start_balance": 0.0,
            "paused_until": None,
            "pause_date": None,
            "date": datetime.now().strftime("%Y-%m-%d")
        }

    def _save_state(self):
        """Sauvegarde l'état sur le volume persistant."""
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            log.error(f"❌ Impossible de sauvegarder l'état bouclier : {e}")

    def _reset_if_new_day(self):
        """Remet les compteurs à zéro chaque nouveau jour."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self.state.get("date") != today:
            log.info("📅 Nouveau jour — réinitialisation du compteur PnL")
            self.state["daily_pnl"] = 0.0
            self.state["date"] = today
            self._save_state()

    def record_pnl(self, pnl: float):
        """Enregistre un PnL (positif ou négatif)."""
        self._reset_if_new_day()
        self.state["daily_pnl"] += pnl
        self._save_state()
        log.info(f"  📈 PnL journalier : {self.state['daily_pnl']:+.4f} USDC")

    def should_pause(self) -> bool:
        """Vérifie si on a atteint la perte max journalière."""
        self._reset_if_new_day()
        if self.state.get("daily_start_balance", 0) <= 0:
            return False
        loss_pct = -self.state["daily_pnl"] / self.state["daily_start_balance"]
        return loss_pct >= self.max_daily_loss_pct

    def activate_pause(self):
        """Active la pause 24h."""
        until = datetime.now() + timedelta(hours=self.pause_hours)
        self.state["paused_until"] = until.isoformat()
        self.state["pause_date"] = datetime.now().isoformat()
        self._save_state()
        log.warning(f"🛡️  SAFE-MODE activé jusqu'à {until.strftime('%Y-%m-%d %H:%M')}")

    def is_paused(self) -> bool:
        """Retourne True si on est en pause."""
        paused_until = self.state.get("paused_until")
        if not paused_until:
            return False
        until = datetime.fromisoformat(paused_until)
        if datetime.now() >= until:
            # Pause terminée
            self.state["paused_until"] = None
            self._save_state()
            log.info("✅ Pause terminée ! Reprise du trading.")
            return False
        return True

    def pause_remaining_minutes(self) -> float:
        """Retourne le nombre de minutes restantes de pause."""
        paused_until = self.state.get("paused_until")
        if not paused_until:
            return 0.0
        until = datetime.fromisoformat(paused_until)
        remaining = (until - datetime.now()).total_seconds() / 60
        return max(0.0, remaining)

    def set_start_balance(self, balance: float):
        """Enregistre la balance de départ du jour (pour calculer le % de perte)."""
        self._reset_if_new_day()
        if self.state.get("daily_start_balance", 0) == 0:
            self.state["daily_start_balance"] = balance
            self._save_state()
