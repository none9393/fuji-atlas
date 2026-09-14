#!/usr/bin/env python3
"""Read-only cTrader Open API adapter and deterministic symbol utilities.

The network adapter is deliberately small: credentials are read from the
environment, never persisted, and all failures are surfaced as CTraderError
so the collector can move to its fallback providers.
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HOSTS = {"demo": "demo1.p.ctrader.com", "live": "live1.p.ctrader.com"}
SDK_HOSTS = {"demo": "demo.ctraderapi.com", "live": "live.ctraderapi.com"}
PORT = 5035
PERIODS = {"1day": "D1", "1h": "H1", "1min": "M1"}
ALIASES = {
    "XAUUSD": ("XAUUSD", "XAU/USD", "GOLD", "GOLD.cash", "GOLD.c", "XAUUSD.", "XAUUSDm"),
    "EURUSD": ("EURUSD", "EUR/USD", "EURUSD.", "EURUSDm"),
    "GBPUSD": ("GBPUSD", "GBP/USD", "GBPUSD.", "GBPUSDm"),
    "USDJPY": ("USDJPY", "USD/JPY", "USDJPY.", "USDJPYm"),
    "USDCAD": ("USDCAD", "USD/CAD", "USDCAD.", "USDCADm"),
    "AUDUSD": ("AUDUSD", "AUD/USD", "AUDUSD.", "AUDUSDm"),
    "XAGUSD": ("XAGUSD", "XAG/USD", "SILVER", "SILVER.cash"),
    "WTIUSD": ("WTI", "USOIL", "XTIUSD", "WTIUSD", "CRUDE"),
    "NATGAS": ("NATGAS", "NGAS", "XNGUSD", "NATURALGAS"),
    "DXY": ("DXY", "USDX"),
    "US10Y": ("US10Y", "US10YR", "UST10Y"),
}


class CTraderError(RuntimeError):
    pass

def credential_env(env=None):
    """Expand an optional CTRADER JSON bundle without exposing its values."""
    source = dict(env or os.environ)
    bundle = source.get("CTRADER")
    if bundle and not all(source.get(k) for k in ("CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET", "CTRADER_ACCESS_TOKEN", "CTRADER_ACCOUNT_ID")):
        try:
            parsed = json.loads(bundle)
            if isinstance(parsed, dict):
                for key in ("client_id", "client_secret", "access_token", "refresh_token", "account_id", "environment"):
                    value = parsed.get(key) or parsed.get("CTRADER_" + key.upper())
                    if value is not None:
                        source["CTRADER_" + key.upper()] = str(value)
        except (TypeError, ValueError):
            pass
    return source


def configured(env=None):
    env = credential_env(env)
    return all(env.get(k) for k in ("CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET", "CTRADER_ACCESS_TOKEN", "CTRADER_ACCOUNT_ID"))


def environment(env=None):
    value = credential_env(env).get("CTRADER_ENVIRONMENT", "demo").lower()
    return value if value in HOSTS else "demo"


def host_for(env=None):
    return HOSTS[environment(env)]


def normalize_alias(value):
    return re.sub(r"[\s/._-]+", "", str(value or "")).upper()


def resolve_symbol(symbol, broker_symbols):
    """Return one exact candidate, or metadata describing no/ambiguous match."""
    aliases = {normalize_alias(x) for x in ALIASES.get(symbol, (symbol,))}
    candidates = []
    for item in broker_symbols or ():
        name = item.get("symbolName") if isinstance(item, dict) else getattr(item, "symbolName", "")
        if normalize_alias(name) in aliases:
            candidates.append(item)
    if len(candidates) == 1:
        return {"status": "resolved", "symbol": candidates[0]}
    return {"status": "ambiguous" if candidates else "not_found", "candidates": candidates}


def period_for(interval):
    if interval not in PERIODS:
        raise CTraderError("unsupported timeframe")
    return PERIODS[interval]


def relative_price(raw, digits=None, pip_position=None):
    """cTrader trendbars encode prices as integer relative values."""
    value = float(raw)
    # Open API price is relative to 10^digits; pipPosition is metadata, not a
    # replacement for digits.  Accept an already-decimal fixture for tests.
    if digits is not None and abs(value) >= 10 ** max(int(digits) - 1, 1):
        value /= 10 ** int(digits)
    return value


def validate_ohlc(bar):
    try:
        o, h, l, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
    except (KeyError, TypeError, ValueError):
        return False
    return min(o, h, l, c) >= 0 and h >= max(o, c) and l <= min(o, c) and h >= l


def normalize_trendbars(rows, digits=None, now=None):
    now = now or datetime.now(timezone.utc)
    out = {}
    for row in rows or ():
        stamp = row.get("datetime") or row.get("timestamp")
        if isinstance(stamp, (int, float)):
            dt = datetime.fromtimestamp(stamp / 1000 if stamp > 10**11 else stamp, timezone.utc)
        else:
            dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(timezone.utc)
        if dt > now:
            continue
        base = {"datetime": dt.isoformat(), **{k: relative_price(row.get(k), digits) for k in ("open", "high", "low", "close")}}
        if validate_ohlc(base):
            out[base["datetime"]] = base
    return [{**out[key], "is_closed": True} for key in sorted(out)]


def sanitize_error(exc):
    text = re.sub(r"(access_token|refresh_token|client_secret|client_id|account_id)=[^&\s]+", r"\1=[redacted]", str(exc), flags=re.I)
    return text[:240]


def refresh_access_token(env=None, opener=None):
    env = env or os.environ
    if not env.get("CTRADER_REFRESH_TOKEN"):
        return None, "not_needed"
    data = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": env["CTRADER_REFRESH_TOKEN"], "client_id": env.get("CTRADER_CLIENT_ID", ""), "client_secret": env.get("CTRADER_CLIENT_SECRET", "")}).encode()
    try:
        request = urllib.request.Request("https://openapi.ctrader.com/apps/token", data=data, method="POST")
        with (opener or urllib.request.urlopen)(request, timeout=12) as response:
            token = json.loads(response.read()).get("accessToken")
        return token, "success" if token else "failed"
    except Exception:
        return None, "failed"


class CTraderProvider:
    def __init__(self, env=None, client_factory=None):
        self.env = credential_env(env)
        self.environment = environment(self.env)
        self.host = host_for(self.env)
        self.client_factory = client_factory
        self.authenticated = False
        self.account_id = self.env.get("CTRADER_ACCOUNT_ID")
        self.token_refresh_status = "not_needed"
        self._circuit_broken = False
        self._ready = threading.Event()
        self._symbols_ready = threading.Event()
        self._pending = None
        self._last_failure = ""
        self.symbols = []
        self.symbol_metadata = {}

    def connect(self):
        if not configured(self.env):
            raise CTraderError("not_configured")
        token, status = refresh_access_token(self.env)
        self.token_refresh_status = status
        if token:
            self._token = token
        else:
            self._token = self.env.get("CTRADER_ACCESS_TOKEN")
        if not self._token:
            raise CTraderError("access token unavailable")
        # SDK imports are deferred so installations without cTrader remain
        # fully functional with Twelve/XAUS/Yahoo fallbacks.
        try:
            from ctrader_open_api import Client, TcpProtocol, Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (ProtoOAApplicationAuthReq, ProtoOAGetAccountListByAccessTokenReq, ProtoOAAccountAuthReq, ProtoOASymbolsListReq)
            from twisted.internet import reactor
            # SDK's canonical TLS endpoints are used for the socket; broker host
            # remains available in metadata and diagnostics.
            self.client = (self.client_factory or Client)(SDK_HOSTS[environment(self.env)], PORT, TcpProtocol)
            def fail(failure):
                self._last_failure = sanitize_error(failure)
                self._circuit_broken = True
                self._ready.set(); self._symbols_ready.set()
                return failure
            def symbols_response(response):
                response = Protobuf.extract(response)
                self.symbols = list(getattr(response, "symbol", []))
                self._symbols_ready.set()
            def account_auth(_response):
                self.authenticated = True
                req = ProtoOASymbolsListReq(ctidTraderAccountId=int(self.account_id), includeArchivedSymbols=False)
                self.client.send(req, responseTimeoutInSeconds=8).addCallbacks(symbols_response, fail)
                self._ready.set()
            def account_list(response):
                response = Protobuf.extract(response)
                accounts = list(getattr(response, "ctidTraderAccount", []))
                ids = [getattr(x, "ctidTraderAccountId", x) for x in accounts]
                if not ids:
                    raw_ids = getattr(response, "ctidTraderAccountId", [])
                    # SDK protobuf exposes a repeated field as a list in some
                    # versions and a scalar when only one account is returned.
                    ids = list(raw_ids) if isinstance(raw_ids, (list, tuple)) else ([raw_ids] if raw_ids else [])
                if self.account_id and ids and int(self.account_id) not in [int(x) for x in ids]:
                    self._circuit_broken = True; self._ready.set(); return
                req = ProtoOAAccountAuthReq(ctidTraderAccountId=int(self.account_id), accessToken=self._token)
                self.client.send(req, responseTimeoutInSeconds=10).addCallbacks(account_auth, fail)
            def app_auth(_response):
                req = ProtoOAGetAccountListByAccessTokenReq(accessToken=self._token)
                self.client.send(req, responseTimeoutInSeconds=10).addCallbacks(account_list, fail)
            def connected(_client):
                req = ProtoOAApplicationAuthReq(clientId=self.env["CTRADER_CLIENT_ID"], clientSecret=self.env["CTRADER_CLIENT_SECRET"])
                self.client.send(req, responseTimeoutInSeconds=10).addCallbacks(app_auth, fail)
            def on_message(_client, message):
                """SDK 0.9.x delivers responses through this callback reliably."""
                try:
                    obj = Protobuf.extract(message)
                    name = obj.__class__.__name__
                    if name == "ProtoOAApplicationAuthRes":
                        app_auth(obj)
                    elif name == "ProtoOAGetAccountListByAccessTokenRes":
                        account_list(obj)
                    elif name == "ProtoOAAccountAuthRes":
                        account_auth(obj)
                    elif name == "ProtoOASymbolsListRes":
                        symbols_response(obj)
                except Exception as exc:
                    fail(exc)
            self.client.setConnectedCallback(connected)
            # Deferred responses are the SDK's authoritative correlation path.
            # Do not register a second message callback: doing so re-processes
            # protobuf payloads and can overwrite the discovered symbol list.
            self.client.startService()
            if not reactor.running:
                threading.Thread(target=lambda: reactor.run(installSignalHandlers=False), daemon=True).start()
            if not self._ready.wait(12) or not self.authenticated:
                detail = self._last_failure or "authentication timeout"
                raise CTraderError(detail)
            if not self._symbols_ready.wait(8):
                raise CTraderError("symbol list timeout")
            return True
        except Exception as exc:
            self._circuit_broken = True
            raise CTraderError(sanitize_error(exc)) from exc

    def fetch_bars(self, symbol, interval, count):
        if self._circuit_broken:
            raise CTraderError("circuit_breaker_open")
        if not self.authenticated:
            self.connect()
        match = resolve_symbol(symbol, self.symbols)
        if match["status"] != "resolved":
            names = []
            for candidate in self.symbols[:12]:
                names.append(str(getattr(candidate, "symbolName", "") or getattr(candidate, "name", "")))
            sample = ",".join(x for x in names if x)
            raise CTraderError(f"symbol_{match['status']}:{symbol}:available={sample[:120]}")
        item = match["symbol"]
        if isinstance(item, dict):
            symbol_id = int(item.get("symbolId"))
        else:
            symbol_id = int(getattr(item, "symbolId"))
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAGetTrendbarsReq
            from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod
            period = getattr(ProtoOATrendbarPeriod, period_for(interval))
        except Exception as exc:
            raise CTraderError("trendbar_sdk_unavailable") from exc
        import time
        durations = {"1day": 86400, "1h": 3600, "1min": 60}
        now_ms = int(time.time() * 1000)
        from_ms = now_ms - durations[interval] * max(int(count), 1) * 1000
        done = threading.Event()
        result = {}
        def received(response):
            result["response"] = Protobuf.extract(response); done.set()
        def failed(failure):
            result["error"] = "trendbar request failed"; done.set()
        req = ProtoOAGetTrendbarsReq(
            ctidTraderAccountId=int(self.account_id), symbolId=symbol_id,
            period=period, fromTimestamp=from_ms, toTimestamp=now_ms,
            count=int(count),
        )
        self.client.send(req, responseTimeoutInSeconds=12).addCallbacks(received, failed)
        if not done.wait(14):
            raise CTraderError("trendbar timeout")
        if "error" in result:
            raise CTraderError(result["error"])
        response = result.get("response")
        bars = list(getattr(response, "trendbar", [])) if response else []
        if not bars:
            raise CTraderError("empty trendbar response")
        out = []
        for bar in bars:
            minute = int(getattr(bar, "utcTimestampInMinutes", 0))
            if not minute:
                continue
            low = float(getattr(bar, "low", 0))
            op = low + float(getattr(bar, "deltaOpen", 0))
            cl = low + float(getattr(bar, "deltaClose", 0))
            hi = low + float(getattr(bar, "deltaHigh", 0))
            scale = 100000.0
            row = {"datetime": datetime.fromtimestamp(minute * 60, timezone.utc).isoformat(),
                   "open": op / scale, "high": hi / scale, "low": low / scale, "close": cl / scale}
            if validate_ohlc(row):
                out.append(row)
        normalized = normalize_trendbars(out, now=datetime.now(timezone.utc))
        if not normalized:
            raise CTraderError("invalid trendbar response")
        return normalized


__all__ = ["ALIASES", "CTraderError", "CTraderProvider", "configured", "environment", "host_for", "normalize_alias", "resolve_symbol", "period_for", "relative_price", "normalize_trendbars", "validate_ohlc", "refresh_access_token", "sanitize_error"]
