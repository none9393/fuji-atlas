#!/usr/bin/env python3
"""Rule-based FUJI runner with cached-OHLC walk-forward evidence."""
from __future__ import annotations
import argparse, hashlib, html, json, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"cloud-output"; CONFIG=ROOT/"FUJI_RUNTIME_CONFIG.json"; ISTANBUL=ZoneInfo("Europe/Istanbul")
STRATEGY_PLAN={"swing":("1day",20),"intraday":("1h",12),"scalping":("5min",18)}

def digest(): return hashlib.sha256(CONFIG.read_bytes()).hexdigest()
def load_market(path=None):
    target=Path(path or "/tmp/fuji-market-data.json")
    if not path: subprocess.run([sys.executable,str(ROOT/"05_araclar/fuji_market_data_collector.py"),"--output",str(target)],check=True,stdout=subprocess.DEVNULL)
    return target,json.loads(target.read_text(encoding="utf-8"))
def closed_values(item): return [bar for bar in item.get("values",[]) if bar.get("is_closed",True)]
def indicators(values):
    values=values[-50:]; closes=[float(x["close"]) for x in values]
    if len(closes)<20: return None
    alpha=2/21; ema=closes[0]
    for close in closes[1:]: ema=close*alpha+ema*(1-alpha)
    changes=[b-a for a,b in zip(closes[-15:-1],closes[-14:])]; gains=sum(max(x,0) for x in changes)/14; losses=sum(max(-x,0) for x in changes)/14; rsi=100 if losses==0 else 100-(100/(1+gains/losses))
    trs=[max(c["high"]-c["low"],abs(c["high"]-p["close"]),abs(c["low"]-p["close"])) for p,c in zip(values[-15:-1],values[-14:])]; atr=sum(trs)/len(trs) if trs else 0; direction="yukarı" if closes[-1]>ema else "aşağı" if closes[-1]<ema else "yatay"
    return {"close":closes[-1],"average20":sum(closes[-20:])/20,"ema20":ema,"rsi14":rsi,"atr14":atr,"direction":direction,"low":min(closes[-20:]),"high":max(closes[-20:])}
def actionable_levels(symbol,stats):
    if not stats or stats["atr14"]<=0: return None
    sign=1 if stats["direction"]!="aşağı" else -1; entry=stats["close"]+sign*stats["atr14"]*.1; sl=entry-sign*stats["atr14"]; risk=abs(entry-sl); tp1=entry+sign*risk*1.5; tp2=entry+sign*risk*2.5; pip=.01 if symbol=="XAUUSD" else .0001; value=1 if symbol=="XAUUSD" else 10
    return {"side":"LONG" if sign>0 else "SHORT","side_tr":"ALIM" if sign>0 else "SATIM","sign":sign,"entry":entry,"sl":sl,"tp1":tp1,"tp2":tp2,"rr1":1.5,"rr2":2.5,"tp1_pips":abs(tp1-entry)/pip,"tp2_pips":abs(tp2-entry)/pip,"tp1_usd":abs(tp1-entry)/pip*value,"tp2_usd":abs(tp2-entry)/pip*value,"tp1_pct":abs(tp1-entry)/entry*100,"tp2_pct":abs(tp2-entry)/entry*100}
def market_conditions(entries):
    frames=("1day","4h","1h","15min","5min"); frame_stats={f:indicators(closed_values(entries.get(f,{}))) for f in frames}; dirs={f:s.get("direction") for f,s in frame_stats.items() if s}
    conflicts=len(set(dirs.values()))>1
    one=closed_values(entries.get("1h",{})); atr=frame_stats.get("1h",{}).get("atr14") if frame_stats.get("1h") else None; expansion=sum(1 for b in one[-30:] if atr and float(b.get("high",0))-float(b.get("low",0))>=2*atr)
    gaps=sum(1 for prev,b in zip(one[-30:-1],one[-29:]) if atr and abs(float(b.get("open",0))-float(prev.get("close",0)))>=1.5*atr)
    rsi_flags=[f+" RSI aşırı alım" for f,s in frame_stats.items() if s and s["rsi14"]>=70]+[f+" RSI aşırı satım" for f,s in frame_stats.items() if s and s["rsi14"]<=30]
    return {"directions":dirs,"conflict":conflicts,"rsi_flags":rsi_flags,"expansion":expansion,"gaps":gaps}
def market_brief(entries,stats):
    conditions=market_conditions(entries); dirs=list(conditions["directions"].values()); up=dirs.count("yukarı"); down=dirs.count("aşağı"); available=len(dirs)
    regime="yükseliş eğilimli" if up>down else "düşüş eğilimli" if down>up else "karışık/yatay"
    location="orta" if not stats else ("alt" if stats["close"]<=stats["low"]+(stats["high"]-stats["low"])/3 else "üst" if stats["close"]>=stats["low"]+2*(stats["high"]-stats["low"])/3 else "orta")
    vol=f"1H ATR14 {stats['atr14']:.5f}" if stats else "ATR14 hesaplanamadı"; momentum=f"RSI14 {stats['rsi14']:.1f}" if stats else "RSI14 hesaplanamadı"; warning="Yönler karışık; teyitsiz kırılım kovalanmamalı." if conditions["conflict"] else "Timeframe yönleri aynı eğilimi destekliyor."
    return f"{available} kullanılabilir timeframe bulundu; {up} yukarı, {down} aşağı eğimli. Rejim {regime}. Güncel fiyat 20 mum referans aralığının {location} bölümünde. Volatilite {vol}; momentum {momentum}. {warning}"
def load_outcomes(config):
    try:
        payload=json.loads((ROOT/config.get("empirical_outcome_gate",{}).get("outcomes_path",".fuji-cache/actionable-outcomes.json")).read_text(encoding="utf-8")); return payload.get("outcomes",[]) if isinstance(payload,dict) else payload
    except (FileNotFoundError,json.JSONDecodeError): return []
def walk_forward(values,symbol,strategy):
    interval,horizon=STRATEGY_PLAN[strategy]; results=[]
    for anchor in range(20,max(20,len(values)-horizon)):
        stats=indicators(values[:anchor+1]); levels=actionable_levels(symbol,stats)
        if not levels: continue
        triggered=False; outcome=None
        for bar in values[anchor+1:anchor+horizon+1]:
            high,low=float(bar["high"]),float(bar["low"])
            if not triggered: triggered=high>=levels["entry"] if levels["sign"]>0 else low<=levels["entry"]
            if not triggered: continue
            tp=high>=levels["tp1"] if levels["sign"]>0 else low<=levels["tp1"]; sl=low<=levels["sl"] if levels["sign"]>0 else high>=levels["sl"]
            if tp and sl: outcome="loss"; break
            if sl: outcome="loss"; break
            if tp: outcome="win"; break
        if outcome: results.append(outcome)
    sample=len(results); wins=results.count("win"); losses=results.count("loss"); rate=wins/sample if sample else 0.0; pf=wins/losses if losses else (float("inf") if wins else 0.0)
    # Empirical weakness alone does not hide a structurally readable setup.
    # Red is reserved for verifier-blocked/unreadable structures; otherwise
    # uncertain or sub-65% walk-forward evidence remains actionable yellow.
    if sample>=30 and rate>=.65: decision,color,status="YEŞİL · GİR","green","green"
    else: decision,color,status="SARI · TEMKİNLİ","yellow","yellow"
    return {"status":status,"decision":decision,"color":color,"sample_size":sample,"wins":wins,"losses":losses,"win_rate":rate,"profit_factor":pf,"interval":interval,"horizon":horizon,"reason":"giriş tetiklenmiş kapalı mumların walk-forward sonucu" if sample else "giriş tetiklenmiş sonuç yok"}
def empirical_gate(outcomes,symbol,strategy,config):
    """Compatibility metrics for callers of the old API; never used as a gate."""
    policy=config.get("empirical_outcome_gate",{}); rows=[r for r in outcomes if r.get("symbol")==symbol and r.get("strategy")==strategy and r.get("status")=="closed" and isinstance(r.get("pnl_r"),(int,float))]; wins=sum(1 for r in rows if r["pnl_r"]>0); gross_win=sum(max(r["pnl_r"],0) for r in rows); gross_loss=sum(max(-r["pnl_r"],0) for r in rows); rate=wins/len(rows) if rows else 0.0; pf=gross_win/gross_loss if gross_loss else (float("inf") if gross_win else 0.0); reasons=[]
    if len(rows)<policy.get("minimum_closed_outcomes",30): reasons.append(f"kapalı sonuç örneklemi {len(rows)}/{policy.get('minimum_closed_outcomes',30)}")
    if rate<policy.get("minimum_win_rate",.45): reasons.append(f"kazanma oranı %{rate*100:.1f}")
    if pf<policy.get("minimum_profit_factor",1.1): reasons.append(f"profit factor {pf:.2f}")
    return {"passed":not reasons,"sample_size":len(rows),"win_rate":rate,"profit_factor":pf,"reason":"; ".join(reasons) if reasons else "legacy metrics only; walk-forward is authoritative"}
def knowledge_notes(symbol):
    index=ROOT/"ogrenme-asistani/veri/INDEX.md"; notes=[]
    if index.exists():
        for line in index.read_text(encoding="utf-8",errors="ignore").splitlines():
            if symbol.lower() in line.lower() or any(k in line.lower() for k in ("risk","likidite","timeframe","yapı")): notes.append(line.strip(" -*#"))
            if len(notes)>=5: break
    return [n for n in notes if n] or ["Üst zaman dilimi yönü teyit edilmeden alt zaman dilimi tetikleyicisi tek başına kullanılmaz.","Likidite süpürmesi sonrası kapanış teyidi ve invalidation seviyesi birlikte değerlendirilir."]
def font_names():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for regular,bold in (("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),("/Library/Fonts/Arial Unicode.ttf","/Library/Fonts/Arial Unicode.ttf")):
        if Path(regular).exists(): pdfmetrics.registerFont(TTFont("FUJI-Regular",regular)); pdfmetrics.registerFont(TTFont("FUJI-Bold",bold)); return "FUJI-Regular","FUJI-Bold"
    return "Helvetica","Helvetica-Bold"
def page_number(canvas,doc): canvas.saveState(); canvas.setFont("Helvetica",8); canvas.drawRightString(doc.pagesize[0]-36,20,f"Sayfa {doc.page}"); canvas.restoreState()
def render_pdf(symbol,market,verification,target,report_id,created,evidence,config):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate,Paragraph,PageBreak,Table,TableStyle,KeepTogether
    regular,bold=font_names(); entries=market["symbols"][symbol]["intervals"]; primary=entries.get("1h",{}); stats=indicators(closed_values(primary)) or indicators(closed_values(entries.get("1day",{}))); latest=(primary.get("values") or [{}])[-1]; conditions=market_conditions(entries); title=ParagraphStyle("title",fontName=bold,fontSize=16,leading=20); body=ParagraphStyle("body",fontName=regular,fontSize=8.5,leading=11); head=ParagraphStyle("head",fontName=bold,fontSize=11,leading=14,spaceBefore=8)
    cond_text="; ".join(conditions["rsi_flags"]) or "RSI aşırı alım/satım sinyali yok"; conflict="var" if conditions["conflict"] else "yok"; macro="Makro takvim/haber akışı bağlı değildir; rapor doğrulanmamış haber veya makro neden üretmez."
    story=[Paragraph(f"FUJI Piyasa Brifingi — {symbol}",title),Paragraph(f"Rapor: {report_id} · {created.isoformat()}",body),Paragraph("1 · Karar ekranı",head),Paragraph("Yönetici özeti · Actionable Intelligence",head),Paragraph("Karar renkleri: YEŞİL · GİR / SARI · TEMKİNLİ / KIRMIZI · GİRME",body),Paragraph(f"Güncel fiyat: {latest.get('close','-')} · Son mum: {'kapalı' if latest.get('is_closed',True) else 'açık'} · Ana yön: {stats.get('direction') if stats else 'hesaplanamadı'}",body),Paragraph("2 · Genel piyasa durumu",head),Paragraph("Piyasa durumu bülteni",head),Paragraph(market_brief(entries,stats),body),Paragraph("Açık pozisyon desteği",head),Paragraph("Kullanıcı pozisyon bilgisi verilmedi; rapor yeni pozisyon seviyelerini ve piyasa koşullarını bağımsız değerlendirir.",body),Paragraph("Aktif koşullar ve dikkat noktaları",head),Paragraph(f"Timeframe yön çatışması: {conflict}. {cond_text}. 1H ≥2 ATR mum: {conditions['expansion']}; ≥1.5 ATR boşluk: {conditions['gaps']}. {macro}",body),Paragraph("3 · Fırsat planları",head),Paragraph("Karar tablos",head)]
    P=lambda value: Paragraph(str(value),body)
    summary=[[P("Fırsat"),P("Karar / yön"),P("Tetik"),P("Risk"),P("Hedefler"),P("Kanıt / süre")]]
    for strategy,label in (("swing","Çok günlük fırsat: 2–20 gün"),("intraday","Seans fırsatı: 2–12 saat"),("scalping","Yakın fırsat: 30–90 dakika")):
        state=verification["symbols"][symbol]["strategies"][strategy]; ev=evidence[strategy]; local=indicators(closed_values(entries.get(ev["interval"],{}))) or stats; levels=actionable_levels(symbol,local) if ev["status"]!="red" and state["status"]!="blocked" else None; direction=f"{levels['side']} / {levels['side_tr']}" if levels else (("LONG / ALIM" if local and local.get("direction")=="yukarı" else "SHORT / SATIM") if local and local.get("direction") in ("yukarı","aşağı") else "-"); summary.append([P(label),P(f"{ev['decision']}<br/>{direction}"),P(f"Kapanış teyidi: {levels['entry']:.5f}" if levels else "Kapanış teyidi yok"),P(f"SL {levels['sl']:.5f}" if levels else "KIRMIZI · GİRME"),P(f"TP1 {levels['tp1']:.5f}<br/>TP2 {levels['tp2']:.5f}" if levels else "Hedef yok"),P(f"%{ev['win_rate']*100:.1f}, n={ev['sample_size']}<br/>{label.split(': ',1)[-1]}")])
    table=Table(summary,repeatRows=1,colWidths=[30*mm,31*mm,31*mm,25*mm,30*mm,30*mm],hAlign="LEFT"); styles=[("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),("FONT",(0,0),(-1,-1),regular,6.2),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#dceaf3")),("GRID",(0,0),(-1,-1),.25,colors.grey)]
    for row,strategy in enumerate(("swing","intraday","scalping"),1): styles.append(("BACKGROUND",(0,row),(-1,row),colors.HexColor({"green":"#d9f2e6","yellow":"#fff0c2","red":"#f7dada"}[evidence[strategy]["status"] if verification["symbols"][symbol]["strategies"][strategy]["status"]!="blocked" else "red"])))
    table.setStyle(TableStyle(styles)); story.append(table)
    decisions="; ".join(f"{label}: {evidence[strategy]['decision']}" for strategy,label in (("swing","Çok günlük fırsat"),("intraday","Seans fırsatı"),("scalping","Yakın fırsat")))
    story.append(Paragraph(f"Kısacası: {decisions}. Bu raporun makro veri beslemesi yok; makro bağlam uydurulmadan yalnızca OHLC, EMA20, RSI14, ATR14 ve gerçekleşmiş walk-forward sonuçları kullanılmıştır.",body))
    story.append(Paragraph("Fırsat ayrıntıları",head))
    for strategy,label in (("swing","Çok günlük fırsat: 2–20 gün"),("intraday","Seans fırsatı: 2–12 saat"),("scalping","Yakın fırsat: 30–90 dakika")):
        state=verification["symbols"][symbol]["strategies"][strategy]; ev=evidence[strategy]; local=indicators(closed_values(entries.get(ev["interval"],{}))) or stats; levels=actionable_levels(symbol,local) if ev["status"]!="red" and state["status"]!="blocked" else None; story.append(Paragraph(label,head))
        if state["status"]=="blocked": story.append(Paragraph("KIRMIZI · GİRME — BLOCKED; "+"; ".join(state["reasons"]),body)); continue
        if local: story.append(Paragraph(f"Yön: {local['direction']} · EMA20: {local['ema20']:.5f} · RSI14: {local['rsi14']:.2f} · ATR14: {local['atr14']:.5f}",body))
        story.append(Paragraph(f"Karar: {ev['decision']} · Geçmiş gerçekleşme: %{ev['win_rate']*100:.1f} · örneklem: {ev['sample_size']} · kazanç/kayıp: {ev['wins']}/{ev['losses']} · profit factor: {ev['profit_factor']:.2f} · {ev['interval']} / {ev['horizon']} ileri mum",body))
        trigger=f"{ev['interval']} kapanışında EMA20 yönü ve mevcut mum aralığı teyit edilmeden giriş tetiklenmez."
        if levels: story.append(Paragraph(f"Tetikleyici: {trigger}",body)); story.append(Paragraph(f"Koşullu giriş ({levels['side']} / {levels['side_tr']}): {levels['entry']:.5f} · SL: {levels['sl']:.5f} · TP1: {levels['tp1']:.5f} · TP2: {levels['tp2']:.5f} · R:R: 1:{levels['rr1']:.1f} / 1:{levels['rr2']:.1f}",body)); story.append(Paragraph(f"Pip/brüt hareket: TP1 {levels['tp1_pips']:.1f} pip / ${levels['tp1_usd']:.2f} / %{levels['tp1_pct']:.2f}; TP2 {levels['tp2_pips']:.1f} pip / ${levels['tp2_usd']:.2f} / %{levels['tp2_pct']:.2f}. Geçersizleşme: SL veya teyit sonrası referans aralığına geri kapanış.",body))
        else: story.append(Paragraph(f"Tetikleyici: {trigger} Geçersizleşme: gerçekleşme oranı yeterli örnekte %45 altında kaldığı için strateji kırmızı; giriş/SL/TP yayımlanmadı.",body))
    story += [Paragraph("Risk notu",head),Paragraph("Kaldıraç kayıp riskini büyütür. Spread, komisyon ve slippage hariçtir. Bu rapor yatırım tavsiyesi değildir.",body),PageBreak(),Paragraph("Bilgi tabanı uygulama notları",head)]
    for note in knowledge_notes(symbol): story.append(Paragraph("• "+html.escape(note),body))
    story.append(Paragraph("Timeframe veri sözleşmesi",head)); rows=[["TF","Provider","Tür/Mod","Son kapalı mum","Yaş(sn)","Kapalı/Toplam"]]
    for interval in config["required_intervals"]:
        item=entries.get(interval,{}); rows.append([interval,item.get("provider") or "-",f"{item.get('source_type') or '-'}/{item.get('source_mode') or '-'}",item.get("last_closed_bar_at_utc") or "-",str(item.get("data_age_seconds")),f"{item.get('bar_count',0)}/{item.get('total_bar_count',0)}"])
    data_table=Table(rows,repeatRows=1,colWidths=[17*mm,25*mm,30*mm,45*mm,18*mm,24*mm]); data_table.setStyle(TableStyle([("FONT",(0,0),(-1,-1),regular,6.5),("FONT",(0,0),(-1,0),bold,6.5),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#dceaf3")),("GRID",(0,0),(-1,-1),.25,colors.grey)])); story.append(data_table); story.append(Paragraph("Bu rapor yatırım tavsiyesi değildir.",body)); SimpleDocTemplate(str(target),pagesize=A4,leftMargin=13*mm,rightMargin=13*mm,topMargin=13*mm,bottomMargin=13*mm).build(story,onFirstPage=page_number,onLaterPages=page_number)
def run(market_data=None,analysis_mode="rules",now=None):
    OUT.mkdir(exist_ok=True); now=now or datetime.now(ISTANBUL); path,market=load_market(market_data); config=json.loads(CONFIG.read_text(encoding="utf-8"));
    if market.get("config_sha256")!=digest(): raise RuntimeError("market data/config SHA mismatch")
    verify_path=OUT/"verification.json"; subprocess.run([sys.executable,str(ROOT/"05_araclar/fuji_live_data_verifier.py"),str(path),"--output",str(verify_path)],check=True,stdout=subprocess.DEVNULL); verification=json.loads(verify_path.read_text(encoding="utf-8"));
    if analysis_mode=="openai" and not os.getenv("OPENAI_API_KEY"): raise RuntimeError("OPENAI_API_KEY is required only for openai mode")
    evidence={symbol:{strategy:walk_forward(closed_values(market["symbols"][symbol]["intervals"].get(interval,{})),symbol,strategy) for strategy,(interval,_) in STRATEGY_PLAN.items()} for symbol in config["symbols"]}; stamp=now.astimezone(ISTANBUL).strftime("%Y%m%d_%H%M%S"); files=[]
    for symbol,detail in verification["symbols"].items():
        if any(value["status"]=="ready" for value in detail["strategies"].values()): name=f"{symbol}_{stamp}.pdf"; render_pdf(symbol,market,verification,OUT/name,f"{symbol}-{stamp}",now.astimezone(ISTANBUL),evidence[symbol],config); files.append(name)
    providers={s:{i:{k:v.get(k) for k in ("provider","source_type","source_mode","last_bar_closed","last_bar_at_utc","last_closed_bar_at_utc","data_age_seconds","bar_count","total_bar_count","fallback_level")} for i,v in d["intervals"].items()} for s,d in market["symbols"].items()}; health={"generated_at_utc":now.astimezone(timezone.utc).isoformat(),"generated_at_local":now.astimezone(ISTANBUL).isoformat(),"analysis_mode":analysis_mode,"config_sha256":digest(),"decision":verification["decision"],"symbols":verification["symbols"],"empirical_outcome_gates":evidence,"walk_forward":evidence,"providers":providers,"files":files}; (OUT/"health.json").write_text(json.dumps(health,ensure_ascii=False,indent=2),encoding="utf-8"); return 4 if verification["decision"]=="blocked" else 0
def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--market-data"); parser.add_argument("--analysis-mode",choices=("rules","openai"),default="rules"); args=parser.parse_args()
    try: raise SystemExit(run(args.market_data,args.analysis_mode))
    except SystemExit: raise
    except Exception as exc: print(f"FUJI runtime error: {exc}",file=sys.stderr); raise SystemExit(2)
if __name__=="__main__": main()
