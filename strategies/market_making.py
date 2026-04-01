"""
strategies/market_making.py
─────────────────────────────────────────────────────────────────────
Market Making : pose des ordres des deux côtés du bid/ask
sur des marchés crypto avec plus de temps de vie (> 3 min).
Utilise Vertical Priority pour valider l'entrée.
"""

import asyncio
import logging

from core.api import safe_api_call, post_order, redeem_position

log = logging.getLogger("market_making")


class MarketMaker:
    def __init__(self, cfg: dict, dry_run: bool, private_key: str, trade_logger):
        self.cfg = cfg.get("strategies", {}).get("market_making", {})
        self.api_cfg = cfg.get("api", {})
        self.dry_run = dry_run
        self.private_key = private_key
        self.logger = trade_logger

        self.spread = self.cfg.get("spread", 0.02)        # 2% de spread
        self.max_hold_s = self.cfg.get("max_hold_s", 300) # max 5 min
        self.min_ev = self.cfg.get("min_ev", 0.005)

    async def run(self, opportunity: dict) -> dict | None:
        """Lance le market making sur une opportunité."""
        market = opportunity.get("market", {})
        indicators = opportunity.get("indicators", {})
        size = opportunity.get("size", 0.0)

        market_id = market.get("id", "?")
        market_name = market.get("question", market_id)
        current_price = indicators.get("current_price")
        edge = indicators.get("edge", 0.0)

        if current_price is None:
            log.info(f"  ⏭️  {market_id} — prix manquant → skip")
            return None

        log.info(f"📈 Market Making | {market_name[:40]} | prix={current_price:.4f}")

        # Calcule bid/ask avec spread
        half_spread = self.spread / 2
        bid_price = max(current_price - half_spread, 0.01)
        ask_price = min(current_price + half_spread, 0.99)
        size_each = size / 2  # Divise la mise entre bid et ask

        ev = edge - self.spread / 2
        log.info(f"  Bid={bid_price:.4f} Ask={ask_price:.4f} EV={ev:.4f}")

        if ev <= self.min_ev:
            log.info(f"  ⏭️  EV après spread trop faible ({ev:.4f}) → skip")
            self.logger.log_skip(market_id, market_name, "market_making",
                                 f"EV net trop faible: {ev:.4f}")
            return None

        # Place les ordres bid et ask
        if not self.dry_run:
            bid_result = await post_order(
                url=self.api_cfg.get("polymarket_order"),
                order={"marketId": market_id, "side": "YES", "size": size_each, "price": bid_price}
            )
            ask_result = await post_order(
                url=self.api_cfg.get("polymarket_order"),
                order={"marketId": market_id, "side": "NO", "size": size_each, "price": ask_price}
            )
            if not bid_result or not ask_result:
                log.warning(f"  ❌ Ordres refusés → skip")
                return None
        else:
            log.info(f"  🔵 [DRY-RUN] Bid {size_each:.2f} @ {bid_price:.4f} + Ask {size_each:.2f} @ {ask_price:.4f}")

        # Attend et calcule le PnL
        await asyncio.sleep(min(self.max_hold_s, market.get("end_time_remaining_s", 300) - 30))

        # PnL estimé (simplifié pour le dry-run)
        pnl = ev * size

        self.logger.log_trade(
            market_id=market_id,
            market_name=market_name,
            strategy="market_making",
            direction="BID/ASK",
            entry_price=current_price,
            exit_price=current_price + ev,
            size_usdc=size,
            pnl_usdc=pnl,
            edge=edge,
            ev=ev,
            dry_run=self.dry_run
        )

        return {"pnl": pnl, "market_id": market_id}
