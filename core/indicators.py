"""
core/indicators.py
─────────────────────────────────────────────────────────────────────
Vertical Priority : les indicateurs sont calculés dans l'ordre des portes.
Si une porte est fermée, on arrête tout de suite (économie CPU).

Ordre :
  1. ATR / Volatilité (porte 1 — la plus importante)
  2. Structure prix : VWAP + EMA (porte 2)
  3. Volume : OBV (porte 3)
  4. RSI, MACD, Stochastic (porte 4 — seulement si tout est ouvert)
"""

import logging
import numpy as np

log = logging.getLogger("indicators")


def _safe_list(data, key) -> list | None:
    """Extrait une liste d'un dict, retourne None si manquant/invalide."""
    val = data.get(key) if isinstance(data, dict) else None
    if not isinstance(val, list) or len(val) < 5:
        return None
    return val


def compute_indicators(price_data: dict, cfg: dict) -> dict | None:
    """
    Calcule tous les indicateurs et renvoie un dict avec les résultats des portes.
    Retourne None si les données de base sont invalides.

    Protection systématique contre NoneType partout (leçon Tirelire Météo).
    """

    # ── Extraction des données brutes ────────────────────────────────────────
    prices = _safe_list(price_data, "prices")
    volumes = _safe_list(price_data, "volumes")
    highs = _safe_list(price_data, "highs")
    lows = _safe_list(price_data, "lows")

    if prices is None:
        log.debug("  Données prix manquantes")
        return None

    # ── Porte 1 : ATR / Volatilité ───────────────────────────────────────────
    atr_ok = False
    atr_value = 0.0
    try:
        if highs and lows and len(highs) == len(lows) == len(prices):
            atr_value = _calc_atr(highs, lows, prices, period=cfg.get("atr_period", 14))
            atr_min = cfg.get("atr_min", 0.005)
            atr_max = cfg.get("atr_max", 0.08)
            atr_ok = atr_min <= atr_value <= atr_max
        else:
            # Fallback : volatilité simple sur les prix
            pct_changes = [abs(prices[i] - prices[i-1]) / prices[i-1]
                           for i in range(1, len(prices)) if prices[i-1] > 0]
            if pct_changes:
                atr_value = np.mean(pct_changes[-14:])
                atr_ok = cfg.get("atr_min", 0.005) <= atr_value <= cfg.get("atr_max", 0.08)
    except Exception as e:
        log.debug(f"  ATR erreur : {e}")

    result = {
        "atr_ok": atr_ok,
        "atr_value": atr_value,
        "price_structure_ok": False,
        "volume_ok": False,
        "momentum_ok": False,
        "edge": 0.0,
        "current_price": prices[-1] if prices else None,
    }

    if not atr_ok:
        return result  # Porte 1 fermée → on s'arrête là

    # ── Porte 2 : Structure prix VWAP + EMA ──────────────────────────────────
    try:
        ema_fast = _calc_ema(prices, period=cfg.get("ema_fast", 9))
        ema_slow = _calc_ema(prices, period=cfg.get("ema_slow", 21))

        vwap = None
        if volumes and len(volumes) == len(prices):
            vwap = _calc_vwap(prices, volumes)

        current = prices[-1]
        price_structure_ok = False

        if ema_fast and ema_slow:
            ema_bullish = ema_fast[-1] > ema_slow[-1]
            ema_bearish = ema_fast[-1] < ema_slow[-1]
            if vwap:
                price_structure_ok = (current > vwap and ema_bullish) or \
                                     (current < vwap and ema_bearish)
            else:
                price_structure_ok = ema_bullish or ema_bearish

        result["price_structure_ok"] = price_structure_ok
        result["vwap"] = vwap
        result["ema_fast"] = ema_fast[-1] if ema_fast else None
        result["ema_slow"] = ema_slow[-1] if ema_slow else None

    except Exception as e:
        log.debug(f"  VWAP/EMA erreur : {e}")

    if not result["price_structure_ok"]:
        return result  # Porte 2 fermée

    # ── Porte 3 : Volume OBV ─────────────────────────────────────────────────
    try:
        volume_ok = False
        if volumes and len(volumes) >= 5:
            obv = _calc_obv(prices, volumes)
            if obv:
                # OBV en hausse = confirmation du mouvement
                obv_trend = obv[-1] > obv[-3] if len(obv) >= 3 else False
                avg_volume = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
                volume_ok = volumes[-1] > avg_volume * cfg.get("volume_multiplier", 1.2) and obv_trend
        result["volume_ok"] = volume_ok
    except Exception as e:
        log.debug(f"  OBV erreur : {e}")

    if not result["volume_ok"]:
        return result  # Porte 3 fermée

    # ── Porte 4 : RSI + MACD + Stochastic ────────────────────────────────────
    try:
        rsi = _calc_rsi(prices, period=cfg.get("rsi_period", 14))
        macd_line, signal_line = _calc_macd(
            prices,
            fast=cfg.get("macd_fast", 12),
            slow=cfg.get("macd_slow", 26),
            signal=cfg.get("macd_signal", 9)
        )
        stoch_k = _calc_stochastic(prices, highs, lows, period=cfg.get("stoch_period", 14))

        momentum_ok = False
        edge = 0.0

        if rsi is not None and macd_line and signal_line:
            rsi_ok = cfg.get("rsi_min", 40) < rsi < cfg.get("rsi_max", 75)
            macd_ok = macd_line[-1] > signal_line[-1]  # croisement haussier

            stoch_ok = True
            if stoch_k is not None:
                stoch_ok = 20 < stoch_k < 80  # pas en zone extrême

            momentum_ok = rsi_ok and macd_ok and stoch_ok

            if momentum_ok:
                # Edge estimé : combinaison simple des signaux
                edge = min(
                    (rsi - 50) / 100 +
                    abs(macd_line[-1] - signal_line[-1]) * 10,
                    cfg.get("max_edge", 0.15)
                )
                edge = max(edge, 0.01)  # edge minimum positif

        result["momentum_ok"] = momentum_ok
        result["rsi"] = rsi
        result["macd"] = macd_line[-1] if macd_line else None
        result["edge"] = edge

    except Exception as e:
        log.debug(f"  RSI/MACD erreur : {e}")

    return result


# ─── Fonctions de calcul ───────────────────────────────────────────────────────

def _calc_atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """Average True Range (mesure la volatilité)."""
    if len(highs) < period + 1:
        return 0.0
    true_ranges = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        true_ranges.append(tr)
    return float(np.mean(true_ranges[-period:]))


def _calc_ema(prices: list, period: int) -> list | None:
    """Exponential Moving Average."""
    if not prices or len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = [float(np.mean(prices[:period]))]
    for p in prices[period:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return ema


def _calc_vwap(prices: list, volumes: list) -> float | None:
    """Volume Weighted Average Price."""
    if not prices or not volumes or len(prices) != len(volumes):
        return None
    total_vol = sum(volumes)
    if total_vol == 0:
        return None
    return sum(p * v for p, v in zip(prices, volumes)) / total_vol


def _calc_obv(prices: list, volumes: list) -> list | None:
    """On Balance Volume."""
    if not prices or not volumes or len(prices) != len(volumes):
        return None
    obv = [0]
    for i in range(1, len(prices)):
        if prices[i] > prices[i-1]:
            obv.append(obv[-1] + volumes[i])
        elif prices[i] < prices[i-1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return obv


def _calc_rsi(prices: list, period: int = 14) -> float | None:
    """Relative Strength Index."""
    if not prices or len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_macd(prices: list, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD : Moving Average Convergence Divergence."""
    ema_fast = _calc_ema(prices, fast)
    ema_slow = _calc_ema(prices, slow)
    if not ema_fast or not ema_slow:
        return None, None
    min_len = min(len(ema_fast), len(ema_slow))
    macd_line = [ema_fast[-min_len+i] - ema_slow[-min_len+i] for i in range(min_len)]
    signal_line = _calc_ema(macd_line, signal)
    return macd_line, signal_line


def _calc_stochastic(prices: list, highs: list, lows: list, period: int = 14) -> float | None:
    """Stochastic oscillator %K."""
    if not prices or not highs or not lows or len(prices) < period:
        return None
    recent_high = max(highs[-period:])
    recent_low = min(lows[-period:])
    if recent_high == recent_low:
        return 50.0
    return (prices[-1] - recent_low) / (recent_high - recent_low) * 100
