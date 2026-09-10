#!/usr/bin/env python3
"""Incremental, source-coherent FUJI OHLC collector."""
from __future__ import annotations
import argparse, hashlib, json, os, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]; CONFIG_PATH=ROOT/"FUJI_RUNTIME_CONFIG.json"; CACHE_PATH=ROOT/".fuji-cache/market-data.json"; UA="FUJI-ATLAS/2.0"
def load_config(path=CONFIG_PATH):
    raw=Path(path).read_bytes(); return json.loads(raw),hashlib.sha256(raw).hexdigest()
def utcnow(): return datetime.now(timezone.utc)
def parse_time(value): return datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone(timezone.utc)

def request_json(url,attempts=3,timeout=20):
    for attempt in range(attempts):
        try:
            with urlopen(Request(url,headers={"User-Agent":UA,"Accept":"application/json"}),timeout=timeout) as response: return json.load(response)
        except HTTPError as exc:
            if exc.code in (401,403): raise RuntimeError(f"authentication failed ({exc.code})") from exc
            if exc.code!=429 and not 500<=exc.code<600: raise
            if attempt+1==attempts: raise
        except (URLError,TimeoutError):
            if attempt+1==attempts: raise
        time.sleep(min(2**attempt,4))

def normalize_bar(stamp,opening,high,low,close):
    if any(v is None for v in (opening,high,low,close)): return None
    dt=parse_time(stamp) if isinstance(stamp,str) else datetime.fromtimestamp(stamp,timezone.utc)
    return {"datetime":dt.isoformat(),"open":float(opening),"high":float(high),"low":float(low),"close":float(close)}
def bucket_start(dt,interval):
    if interval=="1min": return dt.replace(second=0,microsecond=0)
    if interval=="5min": return dt.replace(minute=dt.minute//5*5,second=0,microsecond=0)
    if interval=="15min": return dt.replace(minute=dt.minute//15*15,second=0,microsecond=0)
    if interval=="1h": return dt.replace(minute=0,second=0,microsecond=0)
    if interval=="4h": return dt.replace(hour=dt.hour//4*4,minute=0,second=0,microsecond=0)
    if interval=="1day": return dt.replace(hour=0,minute=0,second=0,microsecond=0)
    if interval=="1week": return (dt-timedelta(days=dt.weekday())).replace(hour=0,minute=0,second=0,microsecond=0)
    if interval=="1month": return dt.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    raise ValueError(interval)
def next_bucket(start,interval):
    seconds={"1min":60,"5min":300,"15min":900,"1h":3600,"4h":14400,"1day":86400,"1week":604800}
    if interval in seconds: return start+timedelta(seconds=seconds[interval])
    if interval=="1month": return start.replace(year=start.year+(start.month==12),month=1 if start.month==12 else start.month+1)
    raise ValueError(interval)
def is_closed(bar,interval,now=None):
    now=(now or utcnow()).astimezone(timezone.utc); return next_bucket(bucket_start(parse_time(bar["datetime"]),interval),interval)<=now
def annotate(bars,interval,now): return [{**bar,"is_closed":is_closed(bar,interval,now)} for bar in bars]
def resample_closed(bars,interval,now=None):
    now=(now or utcnow()).astimezone(timezone.utc); groups={}
    for bar in sorted((b for b in bars if b.get("is_closed",True)),key=lambda x:x["datetime"]): groups.setdefault(bucket_start(parse_time(bar["datetime"]),interval),[]).append(bar)
    out=[]
    for start,items in sorted(groups.items()):
        if next_bucket(start,interval)>now: continue
        out.append({"datetime":start.isoformat(),"open":items[0]["open"],"high":max(x["high"] for x in items),"low":min(x["low"] for x in items),"close":items[-1]["close"],"is_closed":True})
    return out
def merge_bars(old,new,limit):
    merged={b["datetime"]:b for b in old}; merged.update({b["datetime"]:b for b in new}); return [merged[k] for k in sorted(merged)][-limit:]

def twelve(symbol,interval,key,count):
    q=urlencode({"symbol":"XAU/USD" if symbol=="XAUUSD" else "EUR/USD","interval":interval,"outputsize":count,"timezone":"UTC","apikey":key}); payload=request_json("https://api.twelvedata.com/time_series?"+q)
    if payload.get("status")=="error": raise RuntimeError(payload.get("message","Twelve Data error"))
    bars=[normalize_bar(v.get("datetime"),v.get("open"),v.get("high"),v.get("low"),v.get("close")) for v in payload.get("values",[])]; return [b for b in reversed(bars) if b]
def yahoo(symbol,interval,_count):
    ticker="EURUSD=X" if symbol=="EURUSD" else "GC=F"; api,span={"1day":("1d","2y"),"1h":("1h","730d"),"1min":("1m","7d")}[interval]
    result=(request_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker,safe='')}?range={span}&interval={api}&includePrePost=false").get("chart",{}).get("result") or [None])[0]
    if not result: raise RuntimeError("empty Yahoo chart")
    stamps=result.get("timestamp") or []; q=(result.get("indicators",{}).get("quote") or [{}])[0]; out=[]
    for i,stamp in enumerate(stamps):
        bar=normalize_bar(stamp,*(q.get(k,[None]*len(stamps))[i] for k in ("open","high","low","close")))
        if bar: out.append(bar)
    return out
def xaus(symbol,interval,_count):
    if symbol!="XAUUSD" or interval not in ("1day","1h"): raise RuntimeError("XAUS interval unavailable")
    api,span={"1day":("1d","1y"),"1h":("1h","1y")}[interval]; points=request_json(f"https://xaus.com/api/v1/chart?symbol=xau&range={span}&interval={api}").get("points") or []
    return [b for b in (normalize_bar(p.get("t"),p.get("o"),p.get("h"),p.get("l"),p.get("c")) for p in points) if b]
def metadata(bars,interval,provider,source_type,level,retrieved,mode="live"):
    bars=annotate(bars,interval,retrieved); closed=[b for b in bars if b["is_closed"]]; last_closed=closed[-1] if closed else None; boundary=next_bucket(bucket_start(parse_time(last_closed["datetime"]),interval),interval) if last_closed else None
    return {"status":"ready" if closed else "unavailable","provider":provider,"source_type":source_type,"source_mode":mode,"retrieved_at_utc":retrieved.isoformat(),"last_bar_at_utc":bars[-1]["datetime"] if bars else None,"last_closed_bar_at_utc":last_closed["datetime"] if last_closed else None,"last_bar_closed":bars[-1]["is_closed"] if bars else None,"data_age_seconds":max(0,int((retrieved-boundary).total_seconds())) if boundary else None,"bar_count":len(closed),"total_bar_count":len(bars),"distinct_timestamps":len({b["datetime"] for b in closed}),"fallback_level":level,"values":bars}
def load_cache():
    try: return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception: return {}

def collect_base(symbol,interval,config,cached,now):
    old=(((cached.get("symbols") or {}).get(symbol) or {}).get("intervals") or {}).get(interval); fresh=False
    if old and old.get("retrieved_at_utc"):
        fresh=(now-parse_time(old["retrieved_at_utc"])).total_seconds()<config["refresh_seconds"][interval]
    if fresh: return metadata(old.get("values",[]),interval,old.get("provider"),old.get("source_type"),old.get("fallback_level"),now,"cache_fresh")
    count=config["incremental_outputsize"][interval] if old else config["initial_outputsize"][interval]; key=os.getenv("TWELVEDATA_API_KEY"); plans=[]
    if key: plans.append(("Twelve Data","spot",0,lambda:twelve(symbol,interval,key,count)))
    if symbol=="XAUUSD": plans.append(("XAUS","spot",1,lambda:xaus(symbol,interval,count)))
    plans.append(("Yahoo Finance","spot" if symbol=="EURUSD" else "futures_proxy",2 if symbol=="EURUSD" else 3,lambda:yahoo(symbol,interval,count)))
    error="no provider"
    for provider,source_type,level,loader in plans:
        try:
            new=loader()
            if new:
                prior=old.get("values",[]) if old and old.get("provider")==provider and old.get("source_type")==source_type else []
                return metadata(merge_bars(prior,new,config["retained_bars"][interval]),interval,provider,source_type,level,now,"incremental" if prior else "initial")
        except Exception as exc: error=str(exc)
    if old:
        age=(now-parse_time(old["retrieved_at_utc"])).total_seconds()
        if age<=config["cache_freshness_seconds"][interval]: return metadata(old.get("values",[]),interval,old.get("provider"),old.get("source_type"),old.get("fallback_level"),now,"cache_fallback")
    return {"status":"unavailable","provider":None,"source_type":None,"source_mode":"none","retrieved_at_utc":now.isoformat(),"last_bar_at_utc":None,"last_closed_bar_at_utc":None,"last_bar_closed":None,"data_age_seconds":None,"bar_count":0,"total_bar_count":0,"distinct_timestamps":0,"fallback_level":None,"values":[],"error":error}
def collect_symbol(symbol,config,cached,now):
    base={i:collect_base(symbol,i,config,cached,now) for i in config["base_intervals"]}; intervals=dict(base)
    for source,targets in {"1day":("1week","1month"),"1h":("4h",),"1min":("5min","15min")}.items():
        item=base[source]
        for target in targets:
            intervals[target]=metadata(resample_closed(item.get("values",[]),target,now),target,item.get("provider"),item.get("source_type"),item.get("fallback_level"),now,"derived"); intervals[target]["derived_from"]=source
    return {"intervals":{name:intervals[name] for name in config["required_intervals"]}}
def collect(output=None,now=None):
    now=now or utcnow(); config,digest=load_config(); cached=load_cache(); contract={"schema_version":config["schema_version"],"config_sha256":digest,"generated_at_utc":now.isoformat(),"symbols":{s:collect_symbol(s,config,cached,now) for s in config["symbols"]}}
    CACHE_PATH.parent.mkdir(parents=True,exist_ok=True); CACHE_PATH.write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding="utf-8")
    if output: Path(output).write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding="utf-8")
    return contract
def main():
    p=argparse.ArgumentParser(); p.add_argument("--output",default="/tmp/fuji-market-data.json"); a=p.parse_args(); print(json.dumps(collect(a.output),ensure_ascii=False))
if __name__=="__main__": main()
