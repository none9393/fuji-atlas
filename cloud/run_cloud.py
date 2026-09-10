#!/usr/bin/env python3
"""Rule-first FUJI cloud runner."""
from __future__ import annotations
import argparse, hashlib, html, json, os, subprocess, sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"cloud-output"; CONFIG=ROOT/"FUJI_RUNTIME_CONFIG.json"

def digest(): return hashlib.sha256(CONFIG.read_bytes()).hexdigest()
def load_market(path=None):
    target=Path(path or "/tmp/fuji-market-data.json")
    if not path: subprocess.run([sys.executable,str(ROOT/"05_araclar/fuji_market_data_collector.py"),"--output",str(target)],check=True,stdout=subprocess.DEVNULL)
    return target,json.loads(target.read_text(encoding="utf-8"))
def knowledge(symbol):
    folder=ROOT/"ogrenme-asistani/veri"; hits=[]
    for path in sorted(folder.glob("*.json")):
        if symbol.lower() in path.name.lower() or (symbol=="EURUSD" and "eur" in path.name.lower()): hits.append(path.name)
        if len(hits)>=5: break
    return hits or (["INDEX.md"] if (folder/"INDEX.md").exists() else [])
def metrics(values):
    closes=[float(x["close"]) for x in values[-20:]]
    if not closes: return None
    average=sum(closes)/len(closes); direction="yukarı" if closes[-1]>average else "aşağı" if closes[-1]<average else "yatay"
    return {"close":closes[-1],"average20":average,"direction":direction,"low":min(closes),"high":max(closes)}
def font_names():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    regular="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"; bold="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if Path(regular).exists(): pdfmetrics.registerFont(TTFont("DejaVu",regular)); pdfmetrics.registerFont(TTFont("DejaVu-Bold",bold)); return "DejaVu","DejaVu-Bold"
    return "Helvetica","Helvetica-Bold"
def render_pdf(symbol,market,verification,target):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer
    regular,bold=font_names(); entries=market["symbols"][symbol]["intervals"]; summary=metrics(entries.get("1h",{}).get("values",[])) or metrics(entries.get("1day",{}).get("values",[])); stamp=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    title=ParagraphStyle("title",fontName=bold,fontSize=16,leading=20); body=ParagraphStyle("body",fontName=regular,fontSize=9,leading=12); head=ParagraphStyle("head",fontName=bold,fontSize=11,leading=14,spaceBefore=7)
    story=[Paragraph(f"FUJI-ATLAS — {symbol}",title),Paragraph(stamp,body),Spacer(1,4)]
    if summary: story += [Paragraph("Doğrulanmış piyasa özeti",head),Paragraph(f"Son kapanış: {summary['close']:.5f} · Son 20 ortalaması: {summary['average20']:.5f} · Yön: {summary['direction']} · Referans aralığı: {summary['low']:.5f}–{summary['high']:.5f}",body)]
    for name,label in (("swing","Swing"),("intraday","Intraday"),("scalping","Scalping")):
        state=verification["symbols"][symbol]["strategies"][name]; story.append(Paragraph(label,head))
        if state["status"]=="blocked": story.append(Paragraph("BLOCKED — fiyat senaryosu üretilmedi. "+"; ".join(state["reasons"]),body))
        else: story.append(Paragraph(f"Kurala dayalı görünüm {summary['direction'] if summary else 'nötr'}. Referans aralığı dışındaki kapanış yön teyidi; aralık içine dönüş geçersizleşme kabul edilir. Risk işlem öncesinde bağımsız belirlenmelidir.",body))
    story.append(Paragraph("Veri kaynakları",head))
    for interval in json.loads(CONFIG.read_text())["required_intervals"]:
        item=entries.get(interval,{})
        story.append(Paragraph(html.escape(f"{interval}: {item.get('provider') or 'yok'} | {item.get('source_type') or '-'} | veri zamanı {item.get('last_bar_at_utc') or '-'} | yaş {item.get('data_age_seconds')} sn | fallback {item.get('fallback_level')}"),body))
    story.append(Paragraph("Bilgi tabanı: "+", ".join(knowledge(symbol)),body)); story.append(Spacer(1,6)); story.append(Paragraph("Bu rapor yatırım tavsiyesi değildir.",body))
    SimpleDocTemplate(str(target),pagesize=A4,leftMargin=15*mm,rightMargin=15*mm,topMargin=14*mm,bottomMargin=14*mm).build(story)
def run(market_data=None,analysis_mode="rules"):
    OUT.mkdir(exist_ok=True); path,market=load_market(market_data)
    if market.get("config_sha256")!=digest(): raise RuntimeError("market data/config SHA mismatch")
    verify_path=OUT/"verification.json"; subprocess.run([sys.executable,str(ROOT/"05_araclar/fuji_live_data_verifier.py"),str(path),"--output",str(verify_path)],check=True,stdout=subprocess.DEVNULL); verification=json.loads(verify_path.read_text(encoding="utf-8"))
    if analysis_mode=="openai" and not os.getenv("OPENAI_API_KEY"): raise RuntimeError("OPENAI_API_KEY is required only for openai mode")
    files=[]
    for symbol,detail in verification["symbols"].items():
        if any(v["status"]=="ready" for v in detail["strategies"].values()):
            target=OUT/f"{symbol}.pdf"; render_pdf(symbol,market,verification,target); files.append(target.name)
    health={"generated_at_utc":datetime.utcnow().isoformat()+"Z","analysis_mode":analysis_mode,"config_sha256":digest(),"decision":verification["decision"],"symbols":verification["symbols"],"providers":{s:{i:v.get("provider") for i,v in d["intervals"].items()} for s,d in market["symbols"].items()},"files":files}
    (OUT/"health.json").write_text(json.dumps(health,ensure_ascii=False,indent=2),encoding="utf-8")
    return 4 if verification["decision"]=="blocked" else 0
def main():
    p=argparse.ArgumentParser(); p.add_argument("--market-data"); p.add_argument("--analysis-mode",choices=("rules","openai"),default="rules"); a=p.parse_args()
    try: raise SystemExit(run(a.market_data,a.analysis_mode))
    except SystemExit: raise
    except Exception as exc: print(f"FUJI runtime error: {exc}",file=sys.stderr); raise SystemExit(2)
if __name__=="__main__": main()
