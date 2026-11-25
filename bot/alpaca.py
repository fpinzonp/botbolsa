"""Cliente mínimo de la API REST de Alpaca (paper trading + noticias)."""
import time

import requests

from .config import DATA_BASE_URL, PAPER_BASE_URL


def norm(symbol: str) -> str:
    """Alpaca escribe cripto como 'BTC/USD' en órdenes y 'BTCUSD' en posiciones y noticias."""
    return symbol.replace("/", "").upper()


class Alpaca:
    def __init__(self, key: str, secret: str):
        self.http = requests.Session()
        self.http.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        self._assets: dict[str, dict] | None = None

    def _req(self, method: str, url: str, **kwargs):
        resp = self.http.request(method, url, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else None

    # --- Cuenta ---------------------------------------------------------
    def account(self) -> dict:
        return self._req("GET", f"{PAPER_BASE_URL}/v2/account")

    def positions(self) -> list[dict]:
        return self._req("GET", f"{PAPER_BASE_URL}/v2/positions")

    def clock(self) -> dict:
        return self._req("GET", f"{PAPER_BASE_URL}/v2/clock")

    # --- Activos ----------------------------------------------------------
    def tradable_asset(self, symbol: str) -> dict | None:
        """Devuelve el activo si se puede operar con montos fraccionados, o None."""
        if self._assets is None:
            self._assets = {}
            for cls in ("us_equity", "crypto"):
                assets = self._req("GET", f"{PAPER_BASE_URL}/v2/assets",
                                   params={"status": "active", "asset_class": cls})
                for a in assets:
                    if a.get("tradable") and (cls == "crypto" or a.get("fractionable")):
                        self._assets[norm(a["symbol"])] = a
        return self._assets.get(norm(symbol))

    # --- Órdenes ----------------------------------------------------------
    def buy_notional(self, asset: dict, notional: float) -> dict:
        is_crypto = asset.get("class") == "crypto"
        return self._req("POST", f"{PAPER_BASE_URL}/v2/orders", json={
            "symbol": asset["symbol"],
            "notional": f"{notional:.2f}",
            "side": "buy",
            "type": "market",
            "time_in_force": "gtc" if is_crypto else "day",
        })

    def close_position(self, symbol: str) -> dict:
        return self._req("DELETE", f"{PAPER_BASE_URL}/v2/positions/{norm(symbol)}")

    def open_orders(self) -> list[dict]:
        return self._req("GET", f"{PAPER_BASE_URL}/v2/orders", params={"status": "open"})

    def wait_for_fills(self, timeout_s: float = 20, poll_s: float = 2) -> bool:
        """Espera a que no queden órdenes pendientes (las de mercado se llenan en segundos).

        Devuelve False si al vencer el plazo aún hay órdenes abiertas.
        """
        deadline = time.monotonic() + timeout_s
        while self.open_orders():
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll_s)
        return True

    # --- Precios ----------------------------------------------------------
    def latest_stock_prices(self, symbols: list[str]) -> dict[str, float]:
        """Último precio negociado de cada acción (feed IEX, el del plan gratis).

        Si la consulta falla devuelve {}: sin precio no se aplica el filtro de precio.
        """
        if not symbols:
            return {}
        try:
            data = self._req("GET", f"{DATA_BASE_URL}/v2/stocks/trades/latest",
                             params={"symbols": ",".join(sorted(set(symbols))), "feed": "iex"})
        except requests.RequestException:
            return {}
        return {norm(sym): float(t["p"]) for sym, t in (data.get("trades") or {}).items() if t.get("p")}

    # --- Noticias ---------------------------------------------------------
    def news(self, start_iso: str, max_items: int) -> list[dict]:
        """Noticias publicadas desde start_iso, de la más nueva a la más vieja.

        El feed trae miles de noticias por hora; si hay más de max_items se pierden las
        más viejas, que de todos modos ya no sirven para operar.
        """
        items: list[dict] = []
        params = {"start": start_iso, "limit": 50, "sort": "desc", "include_content": "false"}
        while len(items) < max_items:
            page = self._req("GET", f"{DATA_BASE_URL}/v1beta1/news", params=params)
            items.extend(page.get("news", []))
            token = page.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        return items[:max_items]
