import hashlib, importlib.util, json, os, tempfile, unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
collector=module("collector","05_araclar/fuji_market_data_collector.py"); verifier=module("verifier","05_araclar/fuji_live_data_verifier.py"); runner=module("runner","cloud/run_cloud.py"); portal=module("portal","cloud/build_report_portal.py")
CONFIG=json.loads((ROOT/"FUJI_RUNTIME_CONFIG.json").read_text()); SHA=hashlib.sha256((ROOT/"FUJI_RUNTIME_CONFIG.json").read_bytes()).hexdigest()
def bars(count,interval="1h",source="spot"):
    now=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0); delta=timedelta(hours=1)
    values=[{"datetime":(now-delta*(count-i+1)).isoformat(),"open":100+i,"high":102+i,"low":99+i,"close":101+i} for i in range(count)]
    return {"status":"ready","provider":"fixture","source_type":source,"source_mode":"fixture","retrieved_at_utc":now.isoformat(),"last_bar_at_utc":values[-1]["datetime"],"data_age_seconds":60,"bar_count":count,"distinct_timestamps":count,"fallback_level":0,"values":values}
def contract(missing=None,xau_source="spot"):
    missing=missing or set(); symbols={}
    for symbol in CONFIG["symbols"]:
        intervals={}
        for interval in CONFIG["required_intervals"]:
            minimum=CONFIG["minimum_bars"][interval]; item=bars(minimum,interval,xau_source if symbol=="XAUUSD" else "spot")
            if interval in missing: item={**item,"status":"unavailable","bar_count":0,"distinct_timestamps":0,"values":[]}
            intervals[interval]=item
        symbols[symbol]={"intervals":intervals}
    return {"config_sha256":SHA,"symbols":symbols}
class PipelineTests(unittest.TestCase):
    def test_only_closed_utc_buckets_are_resampled(self):
        base=datetime(2026,1,1,10,0,tzinfo=timezone.utc); values=[{"datetime":(base+timedelta(minutes=i)).isoformat(),"open":i,"high":i+2,"low":i-1,"close":i+1} for i in range(8)]
        out=collector.resample_closed(values,"5min",base+timedelta(minutes=8)); self.assertEqual(len(out),1); self.assertEqual(out[0]["open"],0); self.assertEqual(out[0]["high"],6); self.assertEqual(out[0]["close"],5)
    def test_missing_one_minute_blocks_only_scalping(self):
        result=verifier.verify(contract({"1min"})); self.assertEqual(result["symbols"]["EURUSD"]["strategies"]["swing"]["status"],"ready"); self.assertEqual(result["symbols"]["EURUSD"]["strategies"]["intraday"]["status"],"ready"); self.assertEqual(result["symbols"]["EURUSD"]["strategies"]["scalping"]["status"],"blocked")
    def test_futures_proxy_cannot_enable_xau_scalping(self): self.assertEqual(verifier.verify(contract(xau_source="futures_proxy"))["symbols"]["XAUUSD"]["strategies"]["scalping"]["status"],"blocked")
    def test_spot_ready_without_provider_comparison(self): self.assertEqual(verifier.verify(contract())["decision"],"ready")
    def test_config_sha_is_shared(self): self.assertEqual(collector.load_config()[1],verifier.load_config()[1]); self.assertEqual(runner.digest(),SHA)
    def test_rule_report_without_openai(self):
        with tempfile.TemporaryDirectory() as td:
            data=Path(td)/"data.json"; data.write_text(json.dumps(contract())); old=runner.OUT; runner.OUT=Path(td)/"out"
            try:
                with patch.dict(os.environ,{},clear=True): self.assertEqual(runner.run(str(data),"rules"),0)
                self.assertTrue((runner.OUT/"XAUUSD.pdf").exists()); self.assertTrue((runner.OUT/"health.json").exists())
            finally: runner.OUT=old
    def test_portal_copies_pdfs_and_is_mobile_offline(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/"source"; target=Path(td)/"site"; source.mkdir(); (source/"XAUUSD.pdf").write_bytes(b"pdf"); portal.build(source,target); page=(target/"index.html").read_text(); sw=(target/"sw.js").read_text(); self.assertIn('name="viewport"',page); self.assertIn("serviceWorker.register",page); self.assertIn("XAUUSD.pdf",sw); self.assertTrue((target/"manifest.webmanifest").exists())
    def test_blocked_portal_without_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/"source"; target=Path(td)/"site"; source.mkdir(); portal.build(source,target); self.assertIn("Rapor bekleniyor",(target/"index.html").read_text())
if __name__=="__main__": unittest.main()
