"""
core/kelly.py
─────────────────────────────────────────────────────────────────────
Kelly très stricte et conservatrice.
Inspiration @mnilax : petites mises quand l'edge est moyen.
Trader de 19k → 1M avec 70% win-rate sur 5-min crypto.

Règle d'or : max 0,5% de la tirelire par trade.
"""

import logging

log = logging.getLogger("kelly")


def kelly_size(
    edge: float,
    balance: float,
    win_rate: float = 0.60,
    odds: float = 1.0,
    max_pct: float = 0.005,  # 0,5% max par trade
    kelly_fraction: float = 0.25  # Kelly conservateur (1/4 Kelly)
) -> float:
    """
    Calcule la taille de position Kelly stricte.

    Formule Kelly complète :
        f = (bp - q) / b
        où b = odds, p = win_rate, q = 1 - p

    On utilise 1/4 Kelly (très conservateur) + plafond à 0,5%.

    Returns :
        Montant en USDC à miser (jamais plus de max_pct * balance)
    """
    if balance <= 0:
        return 0.0

    if edge <= 0 or win_rate <= 0 or odds <= 0:
        log.debug("  Kelly : edge ou odds invalide → mise nulle")
        return 0.0

    # Kelly complet
    p = min(max(win_rate, 0.01), 0.99)
    q = 1 - p
    b = odds

    kelly_full = (b * p - q) / b

    if kelly_full <= 0:
        log.debug(f"  Kelly négatif ({kelly_full:.4f}) → pas de trade")
        return 0.0

    # Kelly conservateur (1/4 Kelly)
    kelly_conservative = kelly_full * kelly_fraction

    # Ajustement selon l'edge estimé
    edge_factor = min(edge / 0.10, 1.0)  # edge max théorique = 10%
    adjusted_kelly = kelly_conservative * edge_factor

    # Plafond absolu : max_pct de la balance
    max_size = balance * max_pct
    size = min(adjusted_kelly * balance, max_size)

    # Taille minimum
    min_size = max(balance * 0.001, 1.0)  # min 0,1% ou 1 USDC
    if size < min_size:
        log.debug(f"  Kelly trop petit ({size:.2f} USDC) → skip")
        return 0.0

    log.info(
        f"  💰 Kelly : edge={edge:.3f} | kelly_full={kelly_full:.3f} | "
        f"kelly_cons={kelly_conservative:.3f} | taille={size:.2f} USDC "
        f"({size/balance*100:.3f}% balance)"
    )

    return round(size, 2)


def kelly_multi_bin(
    opportunities: list,
    total_balance: float,
    max_pct_per_trade: float = 0.005,
    max_total_exposure: float = 0.06  # 6% max total exposé simultanément
) -> list:
    """
    Multi-bin Kelly : répartit la mise sur plusieurs marchés simultanés.
    Diversification sur 8-12 marchés pour réduire le risque.

    Returns :
        Liste des opportunités avec leur taille de mise ajustée.
    """
    if not opportunities or total_balance <= 0:
        return []

    # Trie par edge décroissant
    sorted_opps = sorted(opportunities, key=lambda x: x.get("edge", 0), reverse=True)

    total_allocated = 0.0
    max_total = total_balance * max_total_exposure
    result = []

    for opp in sorted_opps:
        edge = opp.get("edge", 0)
        size = kelly_size(
            edge=edge,
            balance=total_balance,
            max_pct=max_pct_per_trade
        )

        # Vérifie qu'on ne dépasse pas l'exposition totale
        if total_allocated + size > max_total:
            remaining = max_total - total_allocated
            if remaining < 1.0:
                log.info(f"  📊 Multi-bin : exposition max atteinte ({total_allocated:.2f}/{max_total:.2f} USDC)")
                break
            size = remaining

        if size > 0:
            opp_copy = dict(opp)
            opp_copy["size"] = size
            result.append(opp_copy)
            total_allocated += size

    log.info(f"  📊 Multi-bin : {len(result)} positions | total exposé : {total_allocated:.2f} USDC ({total_allocated/total_balance*100:.2f}%)")
    return result
