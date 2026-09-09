#!/usr/bin/env python3
"""FUJI-VERIFY: compact, source-aware live OHLC contract for FUJI runs."""

import json
import sys
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen

REQUIRED = ("1month", "1week", "1day", "4h", "1h", "15min", "5min", "1min")
MIN_BARS = {"1month": 12, "1week": 26, "1day": 60, "4h": 80, "1h": 100, "15min": 100, "5min": 100, "1min": 100}
TWELVE_SYMBOLS = {"XAUUSD": "XAU/USD", "EURUSD": "EUR/USD"}
YAHOO_INTERVALS = {
    "1month": ("1mo", "10y"),
    "1week": ("1wk", "10y"),
    "1day": ("1d", "10y"),
    "1h": ("1h", "730d"),
    "15min": ("15m", "60d"),
    "5min": ("5m", "60d"),
    "1min": ("1m", "7d"),
}
XAUS_INTERVALS = {
    "1month": ("1mo", "10y"), "1week": ("1wk", "10y"), "1day": ("1d", "1y"),
    "4h": ("4h", "1y"), "1h": ("1h", "1y"),
}
# Yahoo'nun bazı ayrıntılı sahte Chrome imzalarına uyguladığı geçici sınırı
# tetiklememek için sade, tarayıcı-uyumlu imza kullanılır.
UA = "Mozilla/5.0"


def iso_time(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def compact_bar(value):
    return {key: value.get(key) for key in ("datetime", "open", "high", "low", "close")}


def twelve_interval(entry):
    if not isinstance(entry, dict) or entry.get("status") != "live" or not entry.get("values"):
        return None
    values = entry["values"]
    value = values[0]
    return {
        "status": "live",
        "provider": "Twelve Data",
        "url": "https://api.twelvedata.com/time_series",
        "retrieved_at_utc": None,
        "bar_count": len(values),
        "distinct_timestamps": len({v.get("datetime") for v in values}),
        "bar": compact_bar(value),
    }


def yahoo_bars(interval, data_cache, ticker="EURUSD=X"):
    if interval in data_cache:
        return data_cache[interval]
    yahoo_interval, date_range = YAHOO_INTERVALS[interval]
    symbol = quote(ticker, safe="")
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?range={date_range}&interval={yahoo_interval}&includePrePost=false"
    )
    try:
        request = Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
        result = payload.get("chart", {}).get("result")
        if not result:
            raise RuntimeError(payload.get("chart", {}).get("error", {}).get("description", "empty chart result"))
        chart = result[0]
        timestamps = chart.get("timestamp") or []
        quote_data = (chart.get("indicators", {}).get("quote") or [{}])[0]
        bars = []
        for index, stamp in enumerate(timestamps):
            values = {field: (quote_data.get(field) or [None] * len(timestamps))[index] for field in ("open", "high", "low", "close")}
            if all(value is not None for value in values.values()):
                bars.append({"datetime": iso_time(stamp), **values})
        if not bars:
            raise RuntimeError("no complete OHLC bars")
        data_cache[interval] = {"url": url, "bars": bars, "retrieved_at_utc": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        data_cache[interval] = {"error": str(exc), "url": url}
    return data_cache[interval]


def yahoo_interval(interval, data_cache, ticker="EURUSD=X"):
    if interval == "4h":
        hourly = yahoo_bars("1h", data_cache, ticker)
        bars = hourly.get("bars", [])
        if len(bars) < 4:
            return {"status": "unavailable", "provider": "Yahoo Finance chart", "url": hourly.get("url"), "error": hourly.get("error", "fewer than four 1h bars")}
        last_four = bars[-4:]
        return {
            "status": "live",
            "provider": "Yahoo Finance chart",
            "url": hourly["url"],
            "retrieved_at_utc": hourly["retrieved_at_utc"],
            "derived_from_1h": True,
            "bar_count": len(bars),
            "distinct_timestamps": len({v.get("datetime") for v in bars}),
            "bar": {
                "datetime": last_four[0]["datetime"],
                "open": last_four[0]["open"],
                "high": max(item["high"] for item in last_four),
                "low": min(item["low"] for item in last_four),
                "close": last_four[-1]["close"],
            },
        }
    result = yahoo_bars(interval, data_cache, ticker)
    if "error" in result:
        return {"status": "unavailable", "provider": "Yahoo Finance chart", "url": result["url"], "error": result["error"]}
    return {
        "status": "live",
        "provider": "Yahoo Finance chart",
        "url": result["url"],
        "retrieved_at_utc": result["retrieved_at_utc"],
        "bar_count": len(result["bars"]),
        "distinct_timestamps": len({v.get("datetime") for v in result["bars"]}),
        "bar": result["bars"][-1],
    }


def xaus_interval(interval, data_cache):
    """No-key XAU/USD spot OHLC backup; intraday gaps fall through to GC=F."""
    if interval not in XAUS_INTERVALS:
        return {"status": "unavailable", "provider": "XAUS spot", "error": "interval unavailable"}
    if interval in data_cache:
        result = data_cache[interval]
    else:
        api_interval, date_range = XAUS_INTERVALS[interval]
        url = f"https://xaus.com/api/v1/chart?symbol=xau&range={date_range}&interval={api_interval}"
        try:
            request = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urlopen(request, timeout=20) as response:
                payload = json.load(response)
            points = payload.get("points") or []
            bars = [{"datetime": iso_time(p["t"]), "open": p["o"], "high": p["h"], "low": p["l"], "close": p["c"]} for p in points if all(k in p for k in ("t", "o", "h", "l", "c"))]
            result = {"url": url, "bars": bars, "retrieved_at_utc": datetime.now(timezone.utc).isoformat()}
        except Exception as exc:
            result = {"url": url, "error": str(exc)}
        data_cache[interval] = result
    if "error" in result or not result.get("bars"):
        return {"status": "unavailable", "provider": "XAUS XAU/USD spot", "url": result.get("url"), "error": result.get("error", "empty")}
    return {"status": "live", "provider": "XAUS XAU/USD spot", "url": result["url"], "retrieved_at_utc": result["retrieved_at_utc"], "bar_count": len(result["bars"]), "distinct_timestamps": len({b["datetime"] for b in result["bars"]}), "bar": result["bars"][-1]}


def read_twelve(path):
    try:
        with open(path, encoding="utf-8") as source:
            return json.load(source)
    except Exception as exc:
        return {"read_error": str(exc)}


def build_symbol(symbol, twelve, force_yahoo_eurusd=False):
    twelve_symbol = (twelve.get("symbols") or {}).get(TWELVE_SYMBOLS[symbol], {})
    source_intervals = twelve_symbol.get("intervals") or {}
    collected = {}
    for interval in REQUIRED:
        item = None if (symbol == "EURUSD" and force_yahoo_eurusd) else twelve_interval(source_intervals.get(interval))
        if item:
            item["retrieved_at_utc"] = twelve.get("retrieved_at_utc")
            collected[interval] = item

    if symbol == "EURUSD" and len(collected) != len(REQUIRED):
        cache = {}
        for interval in REQUIRED:
            if interval not in collected:
                collected[interval] = yahoo_interval(interval, cache, "EURUSD=X")

    # Ücretsiz Twelve Data anahtarı yoksa XAU/USD için son çare yedek:
    # Yahoo GC=F altın vadeli işlem serisidir; spot XAU/USD ile aynı feed değildir.
    # Bu nedenle her kayıtta proxy olarak açıkça etiketlenir ve rapor bunu belirtir.
    if symbol == "XAUUSD" and len(collected) != len(REQUIRED):
        cache = {}
        for interval in REQUIRED:
            if interval not in collected:
                item = xaus_interval(interval, cache)
                if item.get("status") != "live":
                    item = yahoo_interval(interval, cache, "GC=F")
                if item.get("status") == "live":
                    if item.get("provider") == "Yahoo Finance chart":
                        item["provider"] = "Yahoo Finance GC=F (XAU/USD proxy; spot değil)"
                collected[interval] = item

    missing = []
    quality_failures = []
    for interval in REQUIRED:
        item = collected.get(interval, {})
        if item.get("status") != "live":
            missing.append(interval)
            continue
        count = int(item.get("bar_count", 0))
        distinct = int(item.get("distinct_timestamps", 0))
        if count < MIN_BARS[interval] or distinct < MIN_BARS[interval]:
            quality_failures.append({"interval": interval, "required_bars": MIN_BARS[interval], "bar_count": count, "distinct_timestamps": distinct})
    return {"status": "ready" if not missing and not quality_failures else "blocked", "intervals": collected, "missing": missing, "quality_failures": quality_failures}


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: fuji_live_data_verifier.py TWELVE_DATA_JSON [--force-yahoo-eurusd]")
    twelve = read_twelve(sys.argv[1])
    force_yahoo = "--force-yahoo-eurusd" in sys.argv[2:]
    symbols = {
        "XAUUSD": build_symbol("XAUUSD", twelve),
        "EURUSD": build_symbol("EURUSD", twelve, force_yahoo),
    }
    missing = [f"{symbol}:{interval}" for symbol, detail in symbols.items() for interval in detail["missing"]]
    quality_failures = [{"symbol": symbol, **failure} for symbol, detail in symbols.items() for failure in detail.get("quality_failures", [])]
    contract = {
        "agent": "FUJI-VERIFY",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": "ready" if not missing and not quality_failures else "blocked",
        "symbols": symbols,
        "missing": missing,
        "quality_failures": quality_failures,
        "rule": "No main FUJI agent, PDF, or journal update when decision is blocked; one latest bar is never sufficient for top-down analysis.",
    }
    print(json.dumps(contract, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
