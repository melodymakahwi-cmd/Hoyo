"""
Thin async client around Deriv's WebSocket API.
Docs: https://developers.deriv.com/
Endpoint: wss://ws.derivws.com/websockets/v3?app_id=<app_id>
"""
import asyncio
import json
import itertools
import logging
import websockets

log = logging.getLogger("deriv_client")


class DerivClient:
    def __init__(self, app_id: str, api_token: str, account_type: str = "demo"):
        self.app_id = str(app_id).strip()
        self.api_token = str(api_token).strip()
        self.account_type = account_type
        self.ws = None
        self._req_id = itertools.count(1)
        self._pending = {}
        self._subscribers = {}  # msg_type -> list of async callbacks
        self._listener_task = None
        self.account_info = {}

    @property
    def url(self):
        return f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"

    async def connect(self):
        self.ws = await websockets.connect(self.url, ping_interval=20, ping_timeout=10)
        self._listener_task = asyncio.create_task(self._listen())
        auth = await self._send({"authorize": self.api_token})
        if auth.get("error"):
            raise RuntimeError(f"Deriv authorization failed: {auth['error'].get('message')}")
        self.account_info = auth.get("authorize", {})
        log.info("Authorized as %s (%s)", self.account_info.get("loginid"), self.account_info.get("email"))
        return self.account_info

    async def close(self):
        if self._listener_task:
            self._listener_task.cancel()
        if self.ws:
            await self.ws.close()

    async def _listen(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            req_id = msg.get("req_id")
            if req_id is not None and req_id in self._pending:
                fut = self._pending.pop(req_id)
                if not fut.done():
                    fut.set_result(msg)
                continue
            msg_type = msg.get("msg_type")
            for cb in self._subscribers.get(msg_type, []):
                asyncio.create_task(cb(msg))

    async def _send(self, payload: dict, timeout: float = 20.0) -> dict:
        req_id = next(self._req_id)
        payload = {**payload, "req_id": req_id}
        fut = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send(json.dumps(payload))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"Deriv request timed out: {payload}")

    def on(self, msg_type: str, callback):
        """Register an async callback(msg) for a streamed msg_type (balance, transaction, ohlc, tick...)."""
        self._subscribers.setdefault(msg_type, []).append(callback)

    async def subscribe_balance(self):
        return await self._send({"balance": 1, "subscribe": 1})

    async def subscribe_transactions(self):
        return await self._send({"transaction": 1, "subscribe": 1})

    async def subscribe_candles(self, symbol: str, granularity: int):
        return await self._send({
            "ticks_history": symbol,
            "style": "candles",
            "granularity": granularity,
            "count": 200,
            "end": "latest",
            "subscribe": 1,
        })

    async def get_candle_history(self, symbol: str, granularity: int, count: int = 5000, end: str = "latest", start: int = None):
        payload = {
            "ticks_history": symbol,
            "style": "candles",
            "granularity": granularity,
            "count": count,
            "end": end,
        }
        if start is not None:
            payload["start"] = start
        resp = await self._send(payload, timeout=30.0)
        if resp.get("error"):
            raise RuntimeError(resp["error"].get("message"))
        return resp.get("candles", [])

    async def get_active_symbols(self):
        resp = await self._send({"active_symbols": "brief"})
        return resp.get("active_symbols", [])

    async def buy_contract(self, proposal_id: str, price: float):
        """Only used if you opt into semi-automated execution. Not called by the monitor by default."""
        resp = await self._send({"buy": proposal_id, "price": price})
        return resp
