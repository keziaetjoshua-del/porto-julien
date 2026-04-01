"""
core/api.py
─────────────────────────────────────────────────────────────────────
Leçon dure de Tirelire Météo : les APIs tombent. Toujours.
safe_api_call() gère ça proprement : retries, backoff, fallbacks, timeouts.
"""

import asyncio
import logging
import aiohttp

log = logging.getLogger("api")

# Endpoints de fallback Polymarket (si le principal tombe)
POLYMARKET_FALLBACKS = [
    "https://clob.polymarket.com",
    "https://gamma-api.polymarket.com",
]


async def safe_api_call(
    url: str,
    method: str = "GET",
    payload: dict = None,
    timeout: int = 25,
    max_retries: int = 3,
    fallback_urls: list = None
) -> dict | list | None:
    """
    Appel API sécurisé avec :
    - Retries intelligents (max 3-4 tentatives)
    - Backoff exponentiel : 1s → 3s → 8s
    - Timeout configurable (défaut 25s)
    - Gestion propre des erreurs (ReadTimeout, 404, JSON invalide, etc.)
    - Fallback automatique sur d'autres URLs si disponibles

    Retourne None si toutes les tentatives échouent (pas de crash).
    """
    urls_to_try = [url]
    if fallback_urls:
        urls_to_try += fallback_urls

    backoff_delays = [1, 3, 8]  # secondes entre chaque retry

    for url_attempt in urls_to_try:
        for attempt in range(max_retries):
            try:
                connector = aiohttp.TCPConnector(limit=10)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.request(
                        method,
                        url_attempt,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=timeout)
                    ) as resp:

                        # 404 → pas la peine de retry
                        if resp.status == 404:
                            log.warning(f"⚠️  404 sur {url_attempt} — skip sans retry")
                            break

                        # Autres codes d'erreur HTTP
                        if resp.status >= 400:
                            log.warning(f"⚠️  HTTP {resp.status} sur {url_attempt}")
                            raise aiohttp.ClientResponseError(
                                resp.request_info, resp.history, status=resp.status
                            )

                        # Essaie de parser le JSON
                        try:
                            data = await resp.json(content_type=None)
                        except Exception:
                            log.warning(f"⚠️  JSON invalide sur {url_attempt}")
                            raise ValueError("JSON invalide")

                        # Protection contre None ou réponse vide
                        if data is None:
                            log.warning(f"⚠️  Réponse None sur {url_attempt}")
                            return None

                        return data  # ✅ Succès

            except asyncio.TimeoutError:
                log.warning(f"⏱️  Timeout ({timeout}s) sur {url_attempt} — tentative {attempt+1}/{max_retries}")

            except aiohttp.ClientConnectionError as e:
                log.warning(f"🔌 ConnectionError sur {url_attempt} : {e} — tentative {attempt+1}/{max_retries}")

            except aiohttp.ClientResponseError as e:
                log.warning(f"📡 HTTP {e.status} sur {url_attempt} — tentative {attempt+1}/{max_retries}")

            except ValueError:
                pass  # JSON invalide, déjà loggé

            except Exception as e:
                log.error(f"❌ Erreur inattendue sur {url_attempt} : {type(e).__name__}: {e}")

            # Backoff exponentiel avant le prochain essai
            if attempt < max_retries - 1:
                delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                log.info(f"   ↳ Attente {delay}s avant retry...")
                await asyncio.sleep(delay)

    log.error(f"❌ Toutes les tentatives échouées pour {url}")
    return None


async def post_order(url: str, order: dict, timeout: int = 20) -> dict | None:
    """Envoie un ordre avec les mêmes protections."""
    return await safe_api_call(url=url, method="POST", payload=order, timeout=timeout)


async def redeem_position(url: str, market_id: str, timeout: int = 20) -> dict | None:
    """
    Redeem automatique des gains avec retry.
    Leçon Tirelire Météo : le redeem peut rater → on réessaie.
    """
    payload = {"marketId": market_id}
    result = await safe_api_call(url=url, method="POST", payload=payload, timeout=timeout)
    if result:
        log.info(f"✅ Redeem réussi pour {market_id}")
    else:
        log.warning(f"⚠️  Redeem échoué pour {market_id} — sera retenté au prochain cycle")
    return result
