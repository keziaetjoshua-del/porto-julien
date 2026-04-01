"""
strategies/latency_sniper.py
─────────────────────────────────────────────────────────────────────
Latency Sniper : prend position en fin de vie du contrat (< 2-3 min).
Idées intégrées :
  - @sopersone : filtre momentum >0.5% en 60s + sortie à 65-70% ou 15s avant fin
  - Vertical Priority pour l'entrée
  - Kelly stricte pour le sizing
"""

import asyncio
import logging
from datetime import datetime

from core.api import safe_api_call, post_order, redeem_position

log = logging.getLogger("latency_sniper")


class LatencySniper:
    def __init__(self, cfg: dict, dry_run: bool, private_key: str, trade_logger):
        self.cfg = cfg.get("strategies", {}).get("latency_sniper", {})
        self.api_cfg = cfg.get("api", {})
        self.dry_run = dry_run
        self.private_key = private_key
        self.logger = trade_logger

        # Paramètres clés
        self.momentum_threshold = self.cfg.get("momentum_threshold_pct", 0.005)  # 0.5%
        self.exit_prob_threshold = self.cfg.get("exit_prob_threshold", 0.67)      # 65-70%
        self.exit_time_before_end_s = self.cfg.get("exit_time_before_end_s", 15) # 15s avant fin
        self.end_time_threshold_s = self.cfg.get("end_time_threshold_s", 180)    # < 3 min

    async def run(self, opportunity: dict) -> dict | None:
        """
        Lance le Latency Sniper sur une opportunité.
        Retourne un dict avec le PnL ou None si skip.
        """
        market = opportunity.get("market", {})
        indicators = opportunity.get("indicators", {})
        size = opportunity.get("size", 0.0)

        market_id = market.get("id", "?")
        market_name = market.get("question", market_id)
        end_in_s = market.get("end_time_remaining_s", 999)

        log.info(f"🎯 Latency Sniper | {market_name[:40]} | fin dans {end_in_s}s")

        # ── Filtre momentum @sopersone ─────────────────────────────────────────
        momentum_ok = await self._check_momentum(market_id, indicators)
        if not momentum_ok:
            log.info(f"  ⏭️  Momentum <0.5% en 60s → skip")
            self.logger.log_skip(market_id, market_name, "latency_sniper",
                                 "momentum < 0.5% en 60s")
            return None

        # ── Détermine la direction ────────────────────────────────────────────
        current_price = indicators.get("current_price")
        if current_price is None:
            log.info(f"  ⏭️  Prix manquant → skip")
            return None

        direction = "YES" if current_price < 0.5 else "NO"
        entry_price = current_price

        # ── Calcule l'EV ─────────────────────────────────────────────────────
        edge = indicators.get("edge", 0.0)
        ev = self._calc_ev(entry_price, edge, direction)
        log.info(f"  EV={ev:.4f} | edge={edge:.4f} | direction={direction} | taille={size:.2f} USDC")

        if ev <= self.cfg.get("min_ev", 0.01):
            log.info(f"  ⏭️  EV trop faible ({ev:.4f}) → skip")
            self.logger.log_skip(market_id, market_name, "latency_sniper",
                                 f"EV trop faible: {ev:.4f}")
            return None

        # ── Passe l'ordre ─────────────────────────────────────────────────────
        if not self.dry_run:
            order_result = await post_order(
                url=self.api_cfg.get("polymarket_order"),
                order={
                    "marketId": market_id,
                    "side": direction,
                    "size": size,
                    "price": entry_price,
                }
            )
            if not order_result:
                log.warning(f"  ❌ Ordre refusé → skip")
                return None
        else:
            log.info(f"  🔵 [DRY-RUN] Ordre simulé : {direction} {size:.2f} USDC @ {entry_price:.4f}")

        # ── Monitoring et sortie ──────────────────────────────────────────────
        exit_price, reason = await self._monitor_and_exit(
            market_id=market_id,
            end_in_s=end_in_s,
            entry_price=entry_price,
            direction=direction
        )

        pnl = self._calc_pnl(entry_price, exit_price, size, direction)

        # ── Log du trade ─────────────────────────────────────────────────────
        self.logger.log_trade(
            market_id=market_id,
            market_name=market_name,
            strategy="latency_sniper",
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_price,
            size_usdc=size,
            pnl_usdc=pnl,
            edge=edge,
            ev=ev,
            kelly_fraction=self.cfg.get("kelly_fraction", 0.25),
            dry_run=self.dry_run,
            note=f"sortie:{reason}"
        )

        # ── Redeem automatique ────────────────────────────────────────────────
        if not self.dry_run and pnl > 0:
            await redeem_position(
                url=self.api_cfg.get("polymarket_redeem"),
                market_id=market_id
            )

        return {"pnl": pnl, "market_id": market_id}

    async def _check_momentum(self, market_id: str, indicators: dict) -> bool:
        """
        @sopersone : filtre momentum >0.5% en 60s.
        Compare le prix actuel avec le prix d'il y a ~60s.
        """
        price_now = indicators.get("current_price")
        if price_now is None:
            return False

        # Récupère le prix d'il y a ~60s via l'historique
        history = await safe_api_call(
            url=f"{self.api_cfg.get('polymarket_history', '')}/{market_id}?interval=60s",
            timeout=15
        )
        if not history:
            # Si pas d'historique dispo, on accepte quand même (pas de crash)
            log.debug("  Pas d'historique momentum — porte ouverte par défaut")
            return True

        price_60s_ago = None
        try:
            prices = history.get("prices") or history
            if isinstance(prices, list) and len(prices) >= 2:
                price_60s_ago = prices[0] if isinstance(prices[0], float) else prices[0].get("price")
        except Exception:
            pass

        if price_60s_ago is None or price_60s_ago == 0:
            return True  # Pas de données → on passe

        momentum = abs(price_now - price_60s_ago) / price_60s_ago
        log.info(f"  📈 Momentum 60s : {momentum:.4f} ({momentum*100:.2f}%) — seuil={self.momentum_threshold*100:.1f}%")
        return momentum >= self.momentum_threshold

    async def _monitor_and_exit(
        self,
        market_id: str,
        end_in_s: int,
        entry_price: float,
        direction: str
    ) -> tuple[float, str]:
        """
        Surveille le marché et sort :
        - À 65-70% de probabilité (@sopersone)
        - OU 15s avant la fin du contrat
        - OU après max_hold_s secondes
        """
        max_hold_s = min(end_in_s - self.exit_time_before_end_s, 
                         self.cfg.get("max_hold_s", 120))
        poll_interval_s = self.cfg.get("poll_interval_s", 3)
        elapsed = 0
        exit_price = entry_price
        reason = "timeout"

        while elapsed < max_hold_s:
            await asyncio.sleep(poll_interval_s)
            elapsed += poll_interval_s

            # Récupère le prix actuel
            price_data = await safe_api_call(
                url=f"{self.api_cfg.get('polymarket_price', '')}/{market_id}",
                timeout=10
            )

            if not price_data:
                continue

            current_price = price_data.get("price") if isinstance(price_data, dict) else None
            if current_price is None:
                continue

            exit_price = current_price
            prob = current_price if direction == "YES" else (1 - current_price)

            # Sortie si prob ≥ seuil (65-70%)
            if prob >= self.exit_prob_threshold:
                reason = f"prob_{prob:.2f}"
                log.info(f"  ✅ Sortie à {prob:.2f} (seuil {self.exit_prob_threshold}) — {reason}")
                break

            # Sortie si < 15s avant fin
            remaining = end_in_s - elapsed
            if remaining <= self.exit_time_before_end_s:
                reason = f"fin_contrat_{remaining:.0f}s"
                log.info(f"  ⏰ Sortie urgente — {remaining:.0f}s avant fin")
                break

        return exit_price, reason

    def _calc_ev(self, entry_price: float, edge: float, direction: str) -> float:
        """Expected Value estimé."""
        win_prob = (1 - entry_price + edge) if direction == "YES" else (entry_price + edge)
        win_prob = min(max(win_prob, 0), 1)
        ev = win_prob * (1 - entry_price) - (1 - win_prob) * entry_price
        return ev

    def _calc_pnl(self, entry: float, exit_p: float, size: float, direction: str) -> float:
        """Calcule le PnL simple."""
        if direction == "YES":
            return (exit_p - entry) * size
        else:
            return (entry - exit_p) * size
