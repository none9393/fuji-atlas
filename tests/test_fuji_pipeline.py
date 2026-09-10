import hashlib,importlib.util,json,os,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
collector=module("collector","05_araclar/fuji_market_data_collector.py"); verifier=module("verifier","05_araclar/fuji_live_data_verifier.py"); runner=module("runner","cloud/run_cloud.py"); portal=module("portal","cloud/build_report_portal.py")
CONFIG=json.loads((ROOT/"FUJI_RUNTIME_CONFIG.json").read_text()); SHA=hashlib.sha256((ROOT/"FUJI_RUNTIME_CONFIG.json").read_bytes()).hexdigest()
def bars(count,interval="1h",source="spot",now=None):
    now=now or datetime.now(timezone.utc).replace(second=30,microsecond=0); seconds={"1month":2678400,"1week":604800,"1day":86400,"4h":14400,"1h":3600,"15min":900,"5min":300,"1min":60}[interval]; start=now-timedelta(seconds=seconds*(count+1)); values=[]
    for i in range(count):
        stamp=start+timedelta(seconds=seconds*i); values.append({"datetime":stamp.isoformat(),"open":100+i,"high":102+i,"low":99+i,"close":101+i,"is_closed":True})
    return {"status":"ready","provider":"fixture","source_type":source,"source_mode":"fixture","retrieved_at_utc":now.isoformat(),"last_bar_at_utc":values[-1]["datetime"],"last_closed_bar_at_utc":values[-1]["datetime"],"last_bar_closed":True,"data_age_seconds":60,"bar_count":count,"total_bar_count":count,"distinct_timestamps":count,"fallback_level":0,"values":values}
def contract(missing=None,xau_source="spot",now=None):
    missing=missing or set(); symbols={}
    for symbol in CONFIG["symbols"]:
        intervals={}
        for interval in CONFIG["required_intervals"]:
            minimum=CONFIG["minimum_bars"][interval]; item=bars(max(minimum,25),interval,xau_source if symbol=="XAUUSD" else "spot",now)
            if interval in missing: item={**item,"status":"unavailable","bar_count":0,"distinct_timestamps":0,"values":[]}
            intervals[interval]=item
        symbols[symbol]={"intervals":intervals}
    return {"config_sha256":SHA,"symbols":symbols}

class PipelineTests(unittest.TestCase):
    def test_open_one_minute_is_retained_not_counted(self):
        now=datetime(2026,1,1,10,0,30,tzinfo=timezone.utc); values=[{"datetime":"2026-01-01T09:59:00+00:00","open":1,"high":2,"low":.5,"close":1.5},{"datetime":"2026-01-01T10:00:00+00:00","open":1.5,"high":2,"low":1,"close":1.8}]
        item=collector.metadata(values,"1min","fixture","spot",0,now); self.assertEqual(item["total_bar_count"],2); self.assertEqual(item["bar_count"],1); self.assertFalse(item["last_bar_closed"]); self.assertFalse(item["values"][-1]["is_closed"])
    def test_only_closed_utc_buckets_are_resampled(self):
        base=datetime(2026,1,1,10,0,tzinfo=timezone.utc); values=[{"datetime":(base+timedelta(minutes=i)).isoformat(),"open":i,"high":i+2,"low":i-1,"close":i+1,"is_closed":i<7} for i in range(8)]
        out=collector.resample_closed(values,"5min",base+timedelta(minutes=8)); self.assertEqual(len(out),1); self.assertEqual(out[0]["open"],0); self.assertEqual(out[0]["close"],5)
    def test_month_age_is_measured_from_close_boundary(self):
        now=datetime(2026,9,10,tzinfo=timezone.utc); item=collector.metadata([{"datetime":"2026-08-01T00:00:00+00:00","open":1,"high":2,"low":.5,"close":1.5}],"1month","fixture","spot",0,now); self.assertEqual(item["data_age_seconds"],9*86400)
    def test_fresh_base_cache_avoids_http(self):
        now=datetime(2026,1,1,10,0,30,tzinfo=timezone.utc); item=collector.metadata([{"datetime":"2026-01-01T09:58:00+00:00","open":1,"high":2,"low":.5,"close":1.5}],"1min","fixture","spot",0,now); cached={"symbols":{"EURUSD":{"intervals":{"1min":item}}}}
        with patch.object(collector,"twelve",side_effect=AssertionError("HTTP called")),patch.object(collector,"yahoo",side_effect=AssertionError("HTTP called")): result=collector.collect_base("EURUSD","1min",CONFIG,cached,now+timedelta(seconds=30))
        self.assertEqual(result["source_mode"],"cache_fresh")
    def test_incremental_merge_uses_timestamp(self):
        old=[{"datetime":"2026-01-01T00:00:00+00:00","close":1}]; new=[{"datetime":"2026-01-01T00:00:00+00:00","close":2},{"datetime":"2026-01-01T00:01:00+00:00","close":3}]; merged=collector.merge_bars(old,new,10); self.assertEqual(len(merged),2); self.assertEqual(merged[0]["close"],2)
    def test_missing_one_minute_blocks_only_scalping(self):
        result=verifier.verify(contract({"1min"})); self.assertEqual(result["symbols"]["EURUSD"]["strategies"]["swing"]["status"],"ready"); self.assertEqual(result["symbols"]["EURUSD"]["strategies"]["intraday"]["status"],"ready"); self.assertEqual(result["symbols"]["EURUSD"]["strategies"]["scalping"]["status"],"blocked")
    def test_futures_proxy_cannot_enable_xau_scalping(self): self.assertEqual(verifier.verify(contract(xau_source="futures_proxy"))["symbols"]["XAUUSD"]["strategies"]["scalping"]["status"],"blocked")
    def test_spot_ready_without_provider_comparison(self): self.assertEqual(verifier.verify(contract())["decision"],"ready")
    def test_config_sha_is_shared(self): self.assertEqual(collector.load_config()[1],verifier.load_config()[1]); self.assertEqual(runner.digest(),SHA)
    def test_actionable_levels_include_entry_sl_targets_and_rr(self):
        stats={"close":100.0,"atr14":2.0,"direction":"yukarı"}; levels=runner.actionable_levels("XAUUSD",stats)
        self.assertEqual(levels["side"],"ALIM"); self.assertLess(levels["sl"],levels["entry"]); self.assertGreater(levels["tp1"],levels["entry"]); self.assertGreater(levels["tp2"],levels["tp1"]); self.assertEqual(levels["rr1"],1.5); self.assertEqual(levels["rr2"],2.5); self.assertGreater(levels["tp2_usd"],levels["tp1_usd"]); self.assertGreater(levels["tp2_pct"],levels["tp1_pct"])
    def test_empirical_gate_blocks_small_or_weak_samples(self):
        weak=[{"symbol":"EURUSD","strategy":"swing","status":"closed","pnl_r":1 if i<20 else -1} for i in range(29)]
        result=runner.empirical_gate(weak,"EURUSD","swing",CONFIG); self.assertFalse(result["passed"]); self.assertEqual(result["sample_size"],29)
    def test_empirical_gate_opens_only_after_thresholds(self):
        strong=[{"symbol":"EURUSD","strategy":"swing","status":"closed","pnl_r":1.5 if i<18 else -1} for i in range(30)]
        result=runner.empirical_gate(strong,"EURUSD","swing",CONFIG); self.assertTrue(result["passed"]); self.assertEqual(result["sample_size"],30); self.assertGreaterEqual(result["win_rate"],CONFIG["empirical_outcome_gate"]["minimum_win_rate"]); self.assertGreaterEqual(result["profit_factor"],CONFIG["empirical_outcome_gate"]["minimum_profit_factor"])
    def test_timestamped_rule_reports_without_openai(self):
        with tempfile.TemporaryDirectory() as td:
            data=Path(td)/"data.json"; data.write_text(json.dumps(contract())); old=runner.OUT; runner.OUT=Path(td)/"out"; when=datetime(2026,1,2,3,4,5,tzinfo=timezone.utc)
            try:
                with patch.dict(os.environ,{},clear=True): self.assertEqual(runner.run(str(data),"rules",when),0)
                self.assertTrue((runner.OUT/"XAUUSD_20260102_030405.pdf").exists()); self.assertTrue((runner.OUT/"EURUSD_20260102_030405.pdf").exists())
            finally: runner.OUT=old
    def test_portal_lists_latest_history_and_offline_cache(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/"source"; target=Path(td)/"site"; source.mkdir()
            for name in ("XAUUSD_20260101_000000.pdf","XAUUSD_20260102_000000.pdf","EURUSD_20260102_000000.pdf"): (source/name).write_bytes(b"pdf")
            portal.build(source,target); page=(target/"index.html").read_text(); sw=(target/"sw.js").read_text(); self.assertIn("Geçmiş raporlar",page); self.assertIn("XAUUSD_20260102_000000.pdf",page); self.assertIn("EURUSD_20260102_000000.pdf",sw); self.assertIn('name="viewport"',page); self.assertIn("serviceWorker.register",page)
    def test_blocked_portal_without_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/"source"; target=Path(td)/"site"; source.mkdir(); portal.build(source,target); self.assertIn("Rapor bekleniyor",(target/"index.html").read_text())
if __name__=="__main__": unittest.main()
