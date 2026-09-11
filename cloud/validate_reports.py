#!/usr/bin/env python3
"""Validate the mandatory text contract of FUJI PDF reports."""
from __future__ import annotations
import sys
import re
from pathlib import Path
from pypdf import PdfReader

REQUIRED = (
    "Yönetici özeti", "Actionable Intelligence", "Piyasa durumu bülteni",
    "Koşullu giriş", "SL", "TP1", "TP2", "Geçmiş gerçekleşme", "Risk notu",
)
DECISIONS = ("YEŞİL · GİR", "SARI · TEMKİNLİ", "KIRMIZI · GİRME")
ACTIONABLE_SCENARIO = re.compile(r"Koşullu giriş\s*\((?:LONG / ALIM|SHORT / SATIM)\)\s*:\s*[0-9]")

def validate(path: Path) -> None:
    text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    missing = [item for item in REQUIRED if item not in text]
    if not any(item in text for item in DECISIONS): missing.append("karar renk etiketi")
    if "ARAŞTIRMA MODU" in text: missing.append("ARAŞTIRMA MODU yasak")
    if not ACTIONABLE_SCENARIO.search(text):
        missing.append("sayısal actionable senaryo")
    if missing: raise AssertionError(f"{path.name}: eksik/geçersiz alanlar: {', '.join(missing)}")

def main(argv=None):
    root = Path((argv or sys.argv[1:] or ["cloud-output"])[0]); files = sorted(root.glob("XAUUSD_*.pdf")) + sorted(root.glob("EURUSD_*.pdf"))
    if not files: print(f"PDF bulunamadı: {root}", file=sys.stderr); return 2
    for path in files:
        validate(path); print(f"{path.name}: OK")
    return 0

if __name__ == "__main__": raise SystemExit(main())
