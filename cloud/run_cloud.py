#!/usr/bin/env python3
"""Rule-based FUJI runner producing immutable timestamped reports."""
from __future__ import annotations
import argparse, hashlib, html, json, math, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"cloud-output"; CONFIG=ROOT/"FUJI_RUNTIME_CONFIG.json"
def digest(): return hashlib.sha256(CONFIG.read_bytes()).hexdigest()
def load_market(path=None):
    target=Path(path or "/tmp/fuji-market-data.json")
    if not path: subprocess.run([sys.executable,str(ROOT/"05_araclar/fuji_market_data_collector.py"),"--output",str(target)],check=True,stdout=subprocess.DEVNULL)
    return target,json.loads(target.read_text(encoding="utf-8"))
def closed_values(item): return [b for b in item.get("values",[]) if b.get("is_closed",True)]
def indicators(values):
    values=values[-50:]; closes=[float(x["close"]) for x in values]
    if len(closes)<20: return None
    alpha=2/21; ema=closes[0]
    for close in closes[1:]: ema=close*alpha+ema*(1-alpha)
    changes=[b-a for a,b in zip(closes[-15:-1],closes[-14:])]; gains=sum(max(x,0) for x in changes)/14; losses=sum(max(-x,0) for x in changes)/14; rsi=100 if losses==0 else 100-(100/(1+gains/losses))
    trs=[]
    for previous,current in zip(values[-15:-1],values[-14:]): trs.append(max(current["high"]-current["low"],abs(current["high"]-previous["close"]),abs(current["low"]-previous["close"])))
    atr=sum(trs)/len(trs) if trs else 0; avg=sum(closes[-20:])/20; direction="yukarı" if closes[-1]>ema else "aşağı" if closes[-1]<ema else "yatay"
    return {"close":closes[-1],"average20":avg,"ema20":ema,"rsi14":rsi,"atr14":atr,"direction":direction,"low":min(closes[-20:]),"high":max(closes[-20:])}
def actionable_levels(symbol,stats):
    """Return conditional, explicitly non-executing levels and gross-move math."""
    if not stats or stats["atr14"]<=0: return None
    sign=1 if stats["direction"]=="yukarı" else -1
    entry=stats["close"]+sign*stats["atr14"]*.10; sl=entry-sign*stats["atr14"]; risk=abs(entry-sl); tp1=entry+sign*risk*1.5; tp2=entry+sign*risk*2.5
    pip_size=.01 if symbol=="XAUUSD" else .0001; pip_value=1.0 if symbol=="XAUUSD" else 10.0
    return {"side":"ALIM" if sign>0 else "SATIM","entry":entry,"sl":sl,"tp1":tp1,"tp2":tp2,"rr1":1.5,"rr2":2.5,"tp1_pips":abs(tp1-entry)/pip_size,"tp2_pips":abs(tp2-entry)/pip_size,"tp1_usd":abs(tp1-entry)/pip_size*pip_value,"tp2_usd":abs(tp2-entry)/pip_size*pip_value,"tp1_pct":abs(tp1-entry)/entry*100,"tp2_pct":abs(tp2-entry)/entry*100}
def knowledge_notes(symbol):
    folder=ROOT/"ogrenme-asistani/veri"; notes=[]
    index=folder/"INDEX.md"
    if index.exists():
        for line in index.read_text(encoding="utf-8",errors="ignore").splitlines():
            if symbol.lower() in line.lower() or any(k in line.lower() for k in ("risk","likidite","timeframe","yapı")): notes.append(line.strip(" -*#"))
            if len(notes)>=5: break
    return [n for n in notes if n] or ["Üst zaman dilimi yönü teyit edilmeden alt zaman dilimi tetikleyicisi tek başına kullanılmaz.","Likidite süpürmesi sonrası kapanış teyidi ve invalidation seviyesi birlikte değerlendirilir."]
def font_names():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    candidates=[("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),("/Library/Fonts/Arial Unicode.ttf","/Library/Fonts/Arial Unicode.ttf")]
    for regular,bold in candidates:
        if Path(regular).exists(): pdfmetrics.registerFont(TTFont("FUJI-Regular",regular)); pdfmetrics.registerFont(TTFont("FUJI-Bold",bold)); return "FUJI-Regular","FUJI-Bold"
    return "Helvetica","Helvetica-Bold"
def page_number(canvas,doc):
    canvas.saveState(); canvas.setFont("Helvetica",8); canvas.drawRightString(doc.pagesize[0]-36,20,f"Sayfa {doc.page}"); canvas.restoreState()
def render_pdf(symbol,market,verification,target,report_id,created):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak,Table,TableStyle
    from reportlab.lib import colors
    regular,bold=font_names(); entries=market["symbols"][symbol]["intervals"]; primary=entries.get("1h",{}); stats=indicators(closed_values(primary)) or indicators(closed_values(entries.get("1day",{}))); latest=(primary.get("values") or [{}])[-1]
    timeframe_stats={interval:indicators(closed_values(entries.get(interval,{}))) for interval in json.loads(CONFIG.read_text())["required_intervals"]}; ready=[name for name,state in verification["symbols"][symbol]["strategies"].items() if state["status"]=="ready"]; blocked=[name for name,state in verification["symbols"][symbol]["strategies"].items() if state["status"]=="blocked"]
    title=ParagraphStyle("title",fontName=bold,fontSize=16,leading=20); body=ParagraphStyle("body",fontName=regular,fontSize=8.5,leading=11); head=ParagraphStyle("head",fontName=bold,fontSize=11,leading=14,spaceBefore=8)
    story=[Paragraph(f"FUJI-ATLAS — {symbol}",title),Paragraph(f"Rapor kimliği: {report_id}<br/>Üretim zamanı: {created.isoformat()}",body),Paragraph("Yönetici özeti",head),Paragraph(f"Güncel fiyat: {latest.get('close','-')} · Son mum: {'kapalı' if latest.get('is_closed') else 'açık'} · Ana yön: {stats.get('direction') if stats else 'hesaplanamadı'} · Hazır stratejiler: {', '.join(ready) or 'yok'} · Blocked: {', '.join(blocked) or 'yok'}",body)]
    story.append(Paragraph("Renk kodlu top-down analiz",head)); top_rows=[["Timeframe","Yön","EMA20","RSI14","ATR14"]]; row_colors=[]
    for row_index,interval in enumerate(json.loads(CONFIG.read_text())["required_intervals"],start=1):
        item=timeframe_stats[interval]
        if item: top_rows.append([interval,item["direction"],f"{item['ema20']:.5f}",f"{item['rsi14']:.1f}",f"{item['atr14']:.5f}"]); row_colors.append((row_index,item["direction"]))
        else: top_rows.append([interval,"veri yetersiz","-","-","-"]); row_colors.append((row_index,"yatay"))
    top=Table(top_rows,repeatRows=1,colWidths=[28*mm,30*mm,35*mm,25*mm,35*mm]); top_style=[("FONT",(0,0),(-1,-1),regular,7),("FONT",(0,0),(-1,0),bold,7),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#dceaf3")),("GRID",(0,0),(-1,-1),.25,colors.grey)]
    for row_index,direction in row_colors: top_style.append(("BACKGROUND",(0,row_index),(-1,row_index),colors.HexColor("#d9f2e6" if direction=="yukarı" else "#f7dada" if direction=="aşağı" else "#fff0c2")))
    top.setStyle(TableStyle(top_style)); story.append(top)
    story.append(Paragraph("Strateji analizleri",head))
    for name,label in (("swing","Swing"),("intraday","Intraday"),("scalping","Scalping")):
        state=verification["symbols"][symbol]["strategies"][name]; story.append(Paragraph(label,head))
        if state["status"]=="blocked": story.append(Paragraph("BLOCKED — fiyat senaryosu üretilmedi. "+"; ".join(state["reasons"]),body))
        elif stats:
            levels=actionable_levels(symbol,stats); story.append(Paragraph(f"Yön {stats['direction']}; EMA20 {stats['ema20']:.5f}, RSI14 {stats['rsi14']:.2f}, ATR14 {stats['atr14']:.5f}.",body))
            if levels:
                story.append(Paragraph(f"Koşullu giriş ({levels['side']}): {levels['entry']:.5f} kapanış teyidi · SL: {levels['sl']:.5f} · TP1: {levels['tp1']:.5f} · TP2: {levels['tp2']:.5f} · R:R: 1:{levels['rr1']:.1f} / 1:{levels['rr2']:.1f}. Invalidation: SL veya teyit sonrası referans aralığına geri kapanış.",body))
                story.append(Paragraph(f"Muhtemel brüt hareket — TP1: {levels['tp1_pips']:.1f} pip, standart lotta yaklaşık ${levels['tp1_usd']:.2f}, %{levels['tp1_pct']:.2f}; TP2: {levels['tp2_pips']:.1f} pip, yaklaşık ${levels['tp2_usd']:.2f}, %{levels['tp2_pct']:.2f}.",body))
    story += [Paragraph("Risk notu",head),Paragraph("Kaldıraç kayıp riskini büyütür. Blocked stratejide işlem senaryosu yoktur; partial veri tam teyit sayılmaz. Bu rapor yatırım tavsiyesi değildir.",body),PageBreak(),Paragraph("Bilgi tabanı uygulama notları",head)]
    for note in knowledge_notes(symbol): story.append(Paragraph("• "+html.escape(note),body))
    story.append(Paragraph("Timeframe veri sözleşmesi",head)); rows=[["TF","Provider","Tür/Mod","Son kapalı mum","Yaş(sn)","Kapalı/Toplam"]]
    for interval in json.loads(CONFIG.read_text())["required_intervals"]:
        item=entries.get(interval,{}); rows.append([interval,item.get("provider") or "-",f"{item.get('source_type') or '-'}/{item.get('source_mode') or '-'}",item.get("last_closed_bar_at_utc") or "-",str(item.get("data_age_seconds")),f"{item.get('bar_count',0)}/{item.get('total_bar_count',0)}"])
    table=Table(rows,repeatRows=1,colWidths=[17*mm,25*mm,30*mm,45*mm,18*mm,24*mm]); table.setStyle(TableStyle([("FONT",(0,0),(-1,-1),regular,6.5),("FONT",(0,0),(-1,0),bold,6.5),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#dceaf3")),("GRID",(0,0),(-1,-1),.25,colors.grey),("VALIGN",(0,0),(-1,-1),"TOP")])) ; story.append(table); story += [Spacer(1,8),Paragraph("Bu rapor yatırım tavsiyesi değildir.",body)]
    SimpleDocTemplate(str(target),pagesize=A4,leftMargin=13*mm,rightMargin=13*mm,topMargin=13*mm,bottomMargin=13*mm).build(story,onFirstPage=page_number,onLaterPages=page_number)
def run(market_data=None,analysis_mode="rules",now=None):
    OUT.mkdir(exist_ok=True); now=now or datetime.now(timezone.utc); path,market=load_market(market_data)
    if market.get("config_sha256")!=digest(): raise RuntimeError("market data/config SHA mismatch")
    verify_path=OUT/"verification.json"; subprocess.run([sys.executable,str(ROOT/"05_araclar/fuji_live_data_verifier.py"),str(path),"--output",str(verify_path)],check=True,stdout=subprocess.DEVNULL); verification=json.loads(verify_path.read_text(encoding="utf-8"))
    if analysis_mode=="openai" and not os.getenv("OPENAI_API_KEY"): raise RuntimeError("OPENAI_API_KEY is required only for openai mode")
    stamp=now.strftime("%Y%m%d_%H%M%S"); files=[]
    for symbol,detail in verification["symbols"].items():
        if any(v["status"]=="ready" for v in detail["strategies"].values()):
            name=f"{symbol}_{stamp}.pdf"; render_pdf(symbol,market,verification,OUT/name,f"{symbol}-{stamp}",now); files.append(name)
    providers={s:{i:{k:v.get(k) for k in ("provider","source_type","source_mode","last_bar_closed","last_bar_at_utc","last_closed_bar_at_utc","data_age_seconds","bar_count","total_bar_count","fallback_level")} for i,v in d["intervals"].items()} for s,d in market["symbols"].items()}
    health={"generated_at_utc":now.isoformat(),"analysis_mode":analysis_mode,"config_sha256":digest(),"decision":verification["decision"],"symbols":verification["symbols"],"providers":providers,"files":files}; (OUT/"health.json").write_text(json.dumps(health,ensure_ascii=False,indent=2),encoding="utf-8")
    return 4 if verification["decision"]=="blocked" else 0
def main():
    p=argparse.ArgumentParser(); p.add_argument("--market-data"); p.add_argument("--analysis-mode",choices=("rules","openai"),default="rules"); a=p.parse_args()
    try: raise SystemExit(run(a.market_data,a.analysis_mode))
    except SystemExit: raise
    except Exception as exc: print(f"FUJI runtime error: {exc}",file=sys.stderr); raise SystemExit(2)
if __name__=="__main__": main()
