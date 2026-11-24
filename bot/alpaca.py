"""Cliente mínimo de la API REST de Alpaca (paper trading + noticias)."""
import requests

from .config import DATA_BASE_URL, PAPER_BASE_URL


def norm(symbol: str) -> str:
    """Alpaca escribe cripto como 'BTC/USD' en órdenes y 'BTCUSD' en posiciones y noticias."""
    return symbol.replace("/", "").upper()


class Alpaca:
    def __init__(self, key: str, secret: str):
        self.http = requests.Session()
        self.http.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        self._crypto: dict[str, dict] | None = None

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
    def crypto_assets(self) -> dict[str, dict]:
        if self._crypto is None:
            assets = self._req("GET", f"{PAPER_BASE_URL}/v2/assets",
                               params={"status": "active", "asset_class": "crypto"})
            self._crypto = {norm(a["symbol"]): a for a in assets if a.get("tradable")}
        return self._crypto

    def tradable_asset(self, symbol: str) -> dict | None:
        """Devuelve el activo si se puede operar con montos fraccionados, o None."""
        sym = norm(symbol)
        crypto = self.crypto_assets()
        if sym in crypto:
            return crypto[sym]
        try:
            asset = self._req("GET", f"{PAPER_BASE_URL}/v2/assets/{sym}")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise
        if asset.get("tradable") and asset.get("fractionable") and asset.get("class") == "us_equity":
            return asset
        return None

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

    # --- Noticias ---------------------------------------------------------
    def news(self, start_iso: str, max_items: int = 100) -> list[dict]:
        """Noticias (acciones y cripto) publicadas desde start_iso, de la más vieja a la más nueva."""
        items: list[dict] = []
        params = {"start": start_iso, "limit": 50, "sort": "asc", "include_content": "false"}
        while len(items) < max_items:
            page = self._req("GET", f"{DATA_BASE_URL}/v1beta1/news", params=params)
            items.extend(page.get("news", []))
            token = page.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        return items[:max_items]
