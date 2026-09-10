#!/usr/bin/env python3
"""Config-driven FUJI market-data verifier."""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; CONFIG_PATH=ROOT/"FUJI_RUNTIME_CONFIG.json"
def load_config(path=CONFIG_PATH):
    raw=Path(path).read_bytes(); return json.loads(raw),hashlib.sha256(raw).hexdigest()
def parse_time(value): return datetime.fromisoformat(value.replace("Z","+00:00")).astimezone(timezone.utc)
def market_closed(now):
    return now.weekday()==5 or (now.weekday()==6 and now.hour<22) or (now.weekday()==4 and now.hour>=22)
def verify(contract,now=None,config_path=CONFIG_PATH):
    now=now or datetime.now(timezone.utc); config,digest=load_config(config_path); mismatch=contract.get("config_sha256")!=digest; symbols={}; overall=[]
    for symbol in config["symbols"]:
        entries=(((contract.get("symbols") or {}).get(symbol) or {}).get("intervals") or {}); strategies={}
        for name,gate in config["strategies"].items():
            reasons=[]; warnings=[]
            for interval in gate["required"]:
                item=entries.get(interval) or {}; minimum=config["minimum_bars"][interval]
                if item.get("status")!="ready": reasons.append(f"{interval}: veri yok")
                elif item.get("bar_count",0)<minimum: reasons.append(f"{interval}: mum sayısı {item.get('bar_count',0)}/{minimum}")
                elif item.get("distinct_timestamps",0)<minimum: reasons.append(f"{interval}: benzersiz timestamp yetersiz")
                elif item.get("data_age_seconds") is None or (item["data_age_seconds"]>config["verification_max_age_seconds"][interval] and not market_closed(now)): reasons.append(f"{interval}: son mum eski")
                if symbol=="XAUUSD" and name=="scalping" and interval in ("15min","5min","1min") and item.get("source_type")!="spot": reasons.append(f"{interval}: spot veri gerekli")
            for interval in gate.get("optional",[]):
                if (entries.get(interval) or {}).get("status")!="ready": warnings.append(f"{interval}: opsiyonel veri yok")
            if mismatch: reasons.append("runtime config SHA uyuşmuyor")
            strategies[name]={"status":"blocked" if reasons else "ready","reasons":sorted(set(reasons)),"warnings":warnings}
        states=[v["status"] for v in strategies.values()]; decision="ready" if all(x=="ready" for x in states) else "blocked" if all(x=="blocked" for x in states) else "partial"
        symbols[symbol]={"decision":decision,"strategies":strategies}; overall.append(decision)
    decision="ready" if all(x=="ready" for x in overall) else "blocked" if all(x=="blocked" for x in overall) else "partial"
    return {"schema_version":config["schema_version"],"config_sha256":digest,"market_data_config_sha256":contract.get("config_sha256"),"generated_at_utc":now.isoformat(),"decision":decision,"symbols":symbols}
def main():
    p=argparse.ArgumentParser(); p.add_argument("market_data"); p.add_argument("--output"); a=p.parse_args(); result=verify(json.loads(Path(a.market_data).read_text(encoding="utf-8"))); text=json.dumps(result,ensure_ascii=False,indent=2)
    if a.output: Path(a.output).write_text(text,encoding="utf-8")
    print(text)
if __name__=="__main__": main()
