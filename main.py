"""
PolyHybrid-v4 « Tirelire Crypto »
Bot de trading Polymarket — Marchés crypto 5/15-min
100% personnel, conçu pour Railway Hobby
"""

import asyncio
import os
import yaml
import logging
from datetime import datetime

from core.api import safe_api_call
from core.indicators import compute_indicators
from core.kelly import kelly_size
from core.shield import Shield
from core.logger import TradeLogger
from strategies.latency_sniper import LatencySniper
from strategies.market_making import MarketMaker

# ─── Chargement de la config ───────────────────────────────────────────────────

def load_config():
    with open("config.yaml", "r") as f:
        return yaml.safe_load(f)

# ─── Logging basique ───────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("main")

# ─── Boucle principale ─────────────────────────────────────────────────────────

async def main():
    cfg = load_config()
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    private_key = os.getenv("PRIVATE_KEY")  # Jamais en dur dans le code !

    if not private_key and not dry_run:
        log.error("❌ PRIVATE_KEY manquant ! Lance en DRY_RUN=true d'abord.")
        return

    log.info(f"🚀 PolyHybrid-v4 démarré | DRY_RUN={dry_run}")

    shield = Shield(cfg["shield"])
    trade_logger = TradeLogger(cfg["paths"]["trades_csv"])
    sniper = LatencySniper(cfg, dry_run, private_key, trade_logger)
    maker = MarketMaker(cfg, dry_run, private_key, trade_logger)

    loop_count = 0

    while True:
        loop_count += 1
        log.info(f"── Cycle #{loop_count} ──────────────────────")

        # 🛡️ Bouclier : vérifie si on est en pause
        if shield.is_paused():
            remaining = shield.pause_remaining_minutes()
            log.warning(f"🛡️  Safe-mode actif. Pause encore {remaining:.0f} min.")
            await asyncio.sleep(cfg["loop"]["sleep_on_pause_s"])
            continue

        try:
            # 1. Récupère les marchés crypto 5/15-min disponibles
            markets = await safe_api_call(
                url=cfg["api"]["polymarket_markets"],
                timeout=25
            )
            if not markets:
                log.warning("⚠️  Aucun marché récupéré. Skip ce cycle.")
                await asyncio.sleep(cfg["loop"]["sleep_between_cycles_s"])
                continue

            # 2. Filtre : seulement marchés crypto 5/15-min
            crypto_markets = [
                m for m in markets
                if m.get("category") == "crypto"
                and m.get("end_time_remaining_s", 999) < 900  # < 15 min
            ]
            log.info(f"📋 {len(crypto_markets)} marchés crypto 5/15-min trouvés")

            if not crypto_markets:
                log.info("😴 Aucun marché éligible. Dors.")
                await asyncio.sleep(cfg["loop"]["sleep_between_cycles_s"])
                continue

            # 3. Limite à 8-12 marchés max (multi-bin)
            markets_to_trade = crypto_markets[:cfg["trading"]["max_markets"]]

            # 4. Pour chaque marché : Vertical Priority + stratégies
            opportunities = []
            for market in markets_to_trade:
                market_id = market.get("id", "?")

                # Récupère données de prix / orderbook
                price_data = await safe_api_call(
                    url=f"{cfg['api']['polymarket_price']}/{market_id}",
                    timeout=20
                )
                if not price_data:
                    log.info(f"  ⏭️  {market_id} — données manquantes, skip")
                    continue

                # Calcule les indicateurs (Vertical Priority)
                indicators = compute_indicators(price_data, cfg["indicators"])
                if indicators is None:
                    log.info(f"  ⏭️  {market_id} — indicateurs invalides, skip")
                    continue

                # 🚪 Porte 1 : ATR / Volatilité (obligatoire en premier)
                if not indicators["atr_ok"]:
                    log.info(f"  🚪1 {market_id} — ATR hors zone, skip")
                    continue

                # 🚪 Porte 2 : Structure prix VWAP + EMA
                if not indicators["price_structure_ok"]:
                    log.info(f"  🚪2 {market_id} — structure prix KO, skip")
                    continue

                # 🚪 Porte 3 : Volume OBV
                if not indicators["volume_ok"]:
                    log.info(f"  🚪3 {market_id} — volume insuffisant, skip")
                    continue

                # 🚪 Porte 4 : RSI / MACD / Stochastic (seulement si tout ouvert)
                if not indicators["momentum_ok"]:
                    log.info(f"  🚪4 {market_id} — momentum KO, skip")
                    continue

                log.info(f"  ✅ {market_id} — toutes portes ouvertes !")

                # Kelly size
                balance = float(os.getenv("BANKROLL", cfg["trading"]["default_bankroll"]))
                size = kelly_size(
                    edge=indicators["edge"],
                    balance=balance,
                    max_pct=cfg["trading"]["max_position_pct"]
                )

                opportunities.append({
                    "market": market,
                    "indicators": indicators,
                    "size": size
                })

            # 5. Lance les stratégies sur les opportunités trouvées
            tasks = []
            for opp in opportunities:
                end_time = opp["market"].get("end_time_remaining_s", 999)
                if end_time < cfg["strategies"]["latency_sniper"]["end_time_threshold_s"]:
                    tasks.append(sniper.run(opp))
                else:
                    tasks.append(maker.run(opp))

            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        log.error(f"❌ Erreur stratégie : {r}")
                    elif r and r.get("pnl") is not None:
                        shield.record_pnl(r["pnl"])

            # Vérifie le bouclier après les trades
            if shield.should_pause():
                log.warning("🛡️  Perte journalière >3% ! Passage en safe-mode 24h.")
                shield.activate_pause()

        except Exception as e:
            log.error(f"💥 Erreur critique cycle #{loop_count} : {e}", exc_info=True)

        sleep_s = cfg["loop"]["sleep_between_cycles_s"]
        log.info(f"💤 Dors {sleep_s}s avant prochain cycle...")
        await asyncio.sleep(sleep_s)


if __name__ == "__main__":
    asyncio.run(main())
