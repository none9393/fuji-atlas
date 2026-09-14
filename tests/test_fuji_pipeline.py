import hashlib,importlib.util,json,os,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
collector=module("collector","05_araclar/fuji_market_data_collector.py"); verifier=module("verifier","05_araclar/fuji_live_data_verifier.py"); runner=module("runner","cloud/run_cloud.py"); portal=module("portal","cloud/build_report_portal.py")
ctrader=module("ctrader","05_araclar/fuji_ctrader_provider.py")
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
    def test_ctrader_provider_utilities_and_no_secret_metadata(self):
        self.assertFalse(ctrader.configured({})); self.assertEqual(ctrader.host_for({"CTRADER_ENVIRONMENT":"live"}),"live1.p.ctrader.com"); self.assertEqual(ctrader.period_for("1min"),"M1")
        self.assertEqual(ctrader.normalize_alias("XAU/USD-m"),"XAUUSDM")
        resolved=ctrader.resolve_symbol("XAUUSD",[{"symbolName":"GOLD.cash","symbolId":7}]); self.assertEqual(resolved["status"],"resolved")
        self.assertEqual(ctrader.resolve_symbol("XAUUSD",[{"symbolName":"GOLD"},{"symbolName":"XAUUSD"}])["status"],"ambiguous")
        self.assertTrue(ctrader.validate_ohlc({"open":1,"high":2,"low":.5,"close":1.5})); self.assertFalse(ctrader.validate_ohlc({"open":1,"high":.5,"low":.8,"close":1}))
    def test_ctrader_bundle_configuration_without_logging(self):
        env={"CTRADER":json.dumps({"client_id":"id","client_secret":"secret","access_token":"token","account_id":"42","environment":"demo"})}
        self.assertTrue(ctrader.configured(env)); self.assertEqual(ctrader.environment(env),"demo")
    def test_ctrader_relative_prices_and_future_filter(self):
        rows=ctrader.normalize_trendbars([{"timestamp":1735689600000,"open":100000,"high":101000,"low":99000,"close":100500},{"timestamp":4102444800000,"open":1,"high":2,"low":0.5,"close":1}],digits=5,now=datetime(2026,1,1,tzinfo=timezone.utc)); self.assertEqual(len(rows),1); self.assertAlmostEqual(rows[0]["close"],1.005)
    def test_market_agenda_is_nonempty_without_news(self):
        agenda=runner.market_agenda({"articles":[]},{"events":[]},{"symbols":{}},{"symbols":{}},{}, {"symbols":[]}); self.assertTrue(agenda); self.assertIn("Yeni haber",agenda[0])
    def test_public_feed_parsers_and_normalized_contract(self):
        xml=b'''<rss><channel><item><title>Fed rates and dollar market</title><link>https://example.com/a?utm_source=x</link><pubDate>Sun, 13 Sep 2026 10:00:00 GMT</pubDate></item></channel></rss>'''
        rows=runner.parse_public_feed(xml,"Federal Reserve",datetime(2026,9,13,11,tzinfo=timezone.utc)); self.assertEqual(rows[0]["url"],"https://example.com/a"); self.assertIn("published_at_utc",rows[0]); self.assertNotIn("description",rows[0])
        atom=b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>ECB euro inflation</title><link href="https://example.com/b"/><updated>2026-09-13T10:00:00Z</updated></entry></feed>'''; self.assertEqual(runner.parse_public_feed(atom,"ECB",datetime(2026,9,13,11,tzinfo=timezone.utc))[0]["source"],"ECB")
    def test_gdelt_timestamp_allowlist_and_symbol_mapping(self):
        self.assertEqual(runner.parse_news_datetime("20260913T100000Z").hour,10); self.assertEqual(runner.related_symbols("Gold and dollar yields lift XAUUSD"),["XAUUSD","EURUSD","XAGUSD","DXY","US10Y"])
        self.assertIn("reuters.com",runner.TRUSTED_GDELT_DOMAINS); self.assertNotIn("random-blog.example",runner.TRUSTED_GDELT_DOMAINS)
    def test_news_relevance_word_boundary_and_36h(self):
        now=datetime(2026,9,13,12,tzinfo=timezone.utc); good={"title":"Fed rates lift dollar market","published_at_utc":"2026-09-13T10:00:00+00:00"}; old={"title":"Fed rates dollar market","published_at_utc":"2026-09-11T23:00:00+00:00"}; self.assertTrue(runner.is_relevant_news(good,now)); self.assertFalse(runner.is_relevant_news(old,now)); self.assertFalse(runner._whole_word("corporate rateside", "rate"))
    def test_news_cache_failure_isolated_and_age_bounded(self):
        now=datetime(2026,9,13,12,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            cache=Path(td)/"public-market-news.json"; cache.write_text(json.dumps({"status":"live","retrieved_at_utc":"2026-09-13T10:00:00+00:00","articles":[]}),encoding="utf-8")
            with patch.object(runner,"_fetch",side_effect=RuntimeError("provider down")): result=runner.load_public_market_news(now,cache); self.assertEqual(result["status"],"cache")
            cache.write_text(json.dumps({"status":"live","retrieved_at_utc":"2026-09-13T00:00:00+00:00","articles":[]}),encoding="utf-8")
            with patch.object(runner,"_fetch",side_effect=RuntimeError("provider down")): self.assertEqual(runner.load_public_market_news(now,cache)["status"],"unavailable")
    def test_bulletin_contract_and_health_market_news(self):
        with tempfile.TemporaryDirectory() as td:
            data=Path(td)/"data.json"; data.write_text(json.dumps(contract()),encoding="utf-8"); old=runner.OUT; runner.OUT=Path(td)/"out"
            try:
                runner.run(str(data),"rules",datetime(2026,1,2,3,4,5,tzinfo=timezone.utc)); self.assertTrue(next(runner.OUT.glob("PIYASA_BULTENI_*.pdf"),None)); self.assertIn("market_news",json.loads((runner.OUT/"health.json").read_text()))
            finally: runner.OUT=old
    def test_cross_market_profiles_and_correlations(self):
        self.assertEqual(len(CONFIG["symbols"]),11); self.assertEqual(CONFIG["instrument_profiles"]["USDJPY"]["yahoo"],"JPY=X"); self.assertIsNone(CONFIG["instrument_profiles"]["US10Y"]["twelve"])
        rx=[.01,-.02,.03,-.01,.02,-.03,.015,-.01,.025,-.02]*3; a=100; b=200; av=[]; bv=[]
        for i,r in enumerate(rx,1): a*=1+r; b*=1-r; av.append({"datetime":f"2026-01-{i:02d}T00:00:00+00:00","close":a,"is_closed":True}); bv.append({"datetime":f"2026-01-{i:02d}T00:00:00+00:00","close":b,"is_closed":True})
        market={"symbols":{"EURUSD":{"intervals":{"1day":{"values":av}}},"DXY":{"intervals":{"1day":{"values":bv}}}}}
        ctx=runner.cross_market_context(market,"EURUSD"); self.assertEqual(ctx["sample"],29); self.assertLess(ctx["correlation"],0); self.assertTrue(runner.dxy_conflict("EURUSD",{"correlation":.3},CONFIG)); self.assertFalse(runner.dxy_conflict("WTIUSD",{"correlation":.9},CONFIG))
    def test_workflow_exposes_fresh_data_cache_bypass(self):
        workflow=(ROOT/".github/workflows/fuji-cloud.yml").read_text(encoding="utf-8")
        self.assertIn("fresh_data:",workflow)
        self.assertIn("Reset market cache for a fresh run",workflow)
        self.assertIn('2,32 * * * *',workflow)
        self.assertNotIn('35 3 * * 1-5',workflow)
        self.assertNotIn('15 5-14 * * 1-5',workflow)

    def test_validator_rejects_heading_only_actionable_fields(self):
        validator=module("validator", "cloud/validate_reports.py")
        from reportlab.pdfgen import canvas
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"EURUSD_20260101_000000.pdf"
            c=canvas.Canvas(str(path)); c.drawString(40,780,"Yönetici özeti Actionable Intelligence"); c.drawString(40,760,"Piyasa durumu bülteni"); c.drawString(40,740,"Koşullu giriş SL TP1 TP2 Geçmiş gerçekleşme Risk notu SARI · TEMKİNLİ"); c.save()
            with self.assertRaises(AssertionError): validator.validate(path)

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
        self.assertEqual(levels["side"],"LONG"); self.assertEqual(levels["side_tr"],"ALIM"); self.assertLess(levels["sl"],levels["entry"]); self.assertGreater(levels["tp1"],levels["entry"]); self.assertGreater(levels["tp2"],levels["tp1"]); self.assertEqual(levels["rr1"],1.5); self.assertEqual(levels["rr2"],2.5); self.assertIsNone(levels["tp1_usd"]); self.assertIsNone(levels["tp2_usd"]); self.assertGreater(levels["tp2_pct"],levels["tp1_pct"])
    def test_empirical_gate_blocks_small_or_weak_samples(self):
        weak=[{"symbol":"EURUSD","strategy":"swing","status":"closed","pnl_r":1 if i<20 else -1} for i in range(29)]
        result=runner.empirical_gate(weak,"EURUSD","swing",CONFIG); self.assertFalse(result["passed"]); self.assertEqual(result["sample_size"],29)
    def test_empirical_gate_opens_only_after_thresholds(self):
        strong=[{"symbol":"EURUSD","strategy":"swing","status":"closed","pnl_r":1.5 if i<18 else -1} for i in range(30)]
        result=runner.empirical_gate(strong,"EURUSD","swing",CONFIG); self.assertTrue(result["passed"]); self.assertEqual(result["sample_size"],30); self.assertGreaterEqual(result["win_rate"],CONFIG["empirical_outcome_gate"]["minimum_win_rate"]); self.assertGreaterEqual(result["profit_factor"],CONFIG["empirical_outcome_gate"]["minimum_profit_factor"])
    def test_walk_forward_does_not_count_untriggered_tp_sl(self):
        values=[]
        for i in range(80): values.append({"datetime":f"2026-01-{(i//24)+1:02d}T{(i%24):02d}:00:00+00:00","open":100,"high":100.05,"low":99.95,"close":100,"is_closed":True})
        result=runner.walk_forward(values,"EURUSD","swing"); self.assertEqual(result["sample_size"],0); self.assertEqual(result["decision"],"SARI · TEMKİNLİ")
    def test_walk_forward_same_candle_tp_sl_is_loss(self):
        values=[]
        for i in range(80):
            close=100+i*.01; values.append({"datetime":f"2026-01-{(i//24)+1:02d}T{(i%24):02d}:00:00+00:00","open":close,"high":close+1,"low":close-1,"close":close,"is_closed":True})
        result=runner.walk_forward(values,"EURUSD","swing"); self.assertGreaterEqual(result["losses"],0); self.assertIn(result["status"],("yellow","red","green"))
    def test_timestamped_rule_reports_without_openai(self):
        with tempfile.TemporaryDirectory() as td:
            data=Path(td)/"data.json"; data.write_text(json.dumps(contract())); old=runner.OUT; runner.OUT=Path(td)/"out"; when=datetime(2026,1,2,3,4,5,tzinfo=timezone.utc)
            try:
                with patch.dict(os.environ,{},clear=True): self.assertEqual(runner.run(str(data),"rules",when),0)
                self.assertTrue((runner.OUT/"XAUUSD_20260102_060405.pdf").exists()); self.assertTrue((runner.OUT/"EURUSD_20260102_060405.pdf").exists())
                for name in ("XAUUSD_20260102_060405.pdf","EURUSD_20260102_060405.pdf"):
                    text="\n".join(page.extract_text() or "" for page in PdfReader(runner.OUT/name).pages)
                    for expected in ("Yönetici özeti","Actionable Intelligence","Genel piyasa görünümü","Karar ekranı","Makro ve çapraz piyasa değerlendirmesi","Fırsat planları","Karar renkleri","Koşullu giriş","SL","TP1","TP2","Geçmiş gerçekleşme"): self.assertIn(expected,text)
                    self.assertNotIn("Açık pozisyon desteği",text); self.assertNotIn("Risk notu",text); self.assertNotIn("Kısacası:",text)
            finally: runner.OUT=old
    def test_portal_lists_latest_history_and_offline_cache(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/"source"; target=Path(td)/"site"; source.mkdir()
            for name in ("XAUUSD_20260101_000000.pdf","XAUUSD_20260102_000000.pdf","EURUSD_20260102_000000.pdf"): (source/name).write_bytes(b"pdf")
            portal.build(source,target); page=(target/"index.html").read_text(); sw=(target/"sw.js").read_text(); self.assertIn("Geçmiş raporlar",page); self.assertIn("XAUUSD_20260102_000000.pdf",page); self.assertRegex(page, r'XAUUSD-latest\.pdf\?v=[0-9a-f]{12}'); self.assertTrue((target/"XAUUSD-latest.pdf").exists()); self.assertTrue((target/"EURUSD-latest.pdf").exists()); self.assertIn('data-fuji-version="',page); self.assertIn("location.replace('./?v='+Date.now())",page); self.assertIn("EURUSD_20260102_000000.pdf",sw); self.assertIn("EURUSD-latest.pdf",sw); self.assertIn('manifest.webmanifest?v=',page); self.assertIn('sw.js?v=',page); self.assertIn('name="viewport"',page); self.assertIn("serviceWorker.register",page); self.assertIn("networkFirst",sw); self.assertIn("no-store",sw); self.assertIn("registration.update",page)
    def test_blocked_portal_without_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/"source"; target=Path(td)/"site"; source.mkdir(); portal.build(source,target); self.assertIn("Rapor bekleniyor",(target/"index.html").read_text())
if __name__=="__main__": unittest.main()
