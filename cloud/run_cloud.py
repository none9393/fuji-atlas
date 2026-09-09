#!/usr/bin/env python3
"""FUJI-ATLAS cloud run: verify live data, ask the model, render two PDFs."""
import json, os, subprocess, sys, urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "cloud-output"
OUT.mkdir(exist_ok=True)
stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

contract = subprocess.check_output([
    sys.executable, str(ROOT / "05_araclar/fuji_live_data_verifier.py"), "/tmp/fuji-empty.json"
], text=True)
data = json.loads(contract)
if data.get("decision") != "ready":
    print(json.dumps({"status": "BLOCKED", "contract": data}, ensure_ascii=False))
    raise SystemExit(4)

index = (ROOT / "ogrenme-asistani/veri/INDEX.md").read_text(encoding="utf-8")[:12000]
prompt = f"""FUJI-ATLAS güncel piyasa analiz raporu üret.
Timestamp: {stamp}
Canlı veri sözleşmesi: {json.dumps(data, ensure_ascii=False)}
Bilgi tabanı indeks özeti: {index}
Kurallar: spot ve futures verisini karıştırma; GC=F proxy ise açıkça belirt; kaynak, UTC zaman, veri yaşı ve fallback seviyesini yaz; kritik veri yoksa işlem girişi verme. XAUUSD ve EURUSD için Swing, Intraday, Scalping bölümleri; senaryo, invalidation ve risk notu yaz. Türkçe yaz. Yatırım tavsiyesi olmadığını belirt.
Çıktıyı yalnızca rapor metni olarak üret."""

payload = json.dumps({"model": os.getenv("FUJI_MODEL", "gpt-5"), "store": False, "input": prompt}).encode()
req = urllib.request.Request("https://api.openai.com/v1/responses", data=payload, headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=120) as response:
    answer = json.load(response)
text = answer.get("output_text") or "\n".join(x.get("text", "") for o in answer.get("output", []) for x in o.get("content", []) if x.get("type") == "output_text")
if not text.strip():
    raise RuntimeError("No report text")

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

styles = getSampleStyleSheet(); body = ParagraphStyle("body", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12)
for symbol in ("XAUUSD", "EURUSD"):
    target = OUT / f"{symbol}.pdf"
    doc = SimpleDocTemplate(str(target), pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=15*mm)
    story = [Paragraph(f"{stamp} Europe/Istanbul", styles["Title"]), Paragraph(f"{symbol} — FUJI-ATLAS Bulut Analiz", styles["Heading1"]), Spacer(1, 8)]
    for block in text.split("\n"):
        if block.strip(): story.append(Paragraph(block.replace("&", "&amp;"), body)); story.append(Spacer(1, 3))
    doc.build(story)
print(json.dumps({"status": "READY", "timestamp": stamp, "files": [str(OUT / "XAUUSD.pdf"), str(OUT / "EURUSD.pdf")]}, ensure_ascii=False))
