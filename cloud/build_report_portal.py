#!/usr/bin/env python3
"""Build a timestamped mobile/offline FUJI report archive."""
import hashlib,html,json,shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
def build(source=None,target=None):
    source=Path(source or ROOT/".fuji-published"); target=Path(target or ROOT/"cloud-site"); target.mkdir(parents=True,exist_ok=True); config=json.loads((ROOT/"FUJI_RUNTIME_CONFIG.json").read_text()); limit=config["report_archive_per_symbol"]
    for legacy in (source/"XAUUSD.pdf",source/"EURUSD.pdf"):
        if legacy.exists(): legacy.unlink()
    by_symbol={s:sorted(source.glob(f"{s}_*.pdf"),reverse=True)[:limit] for s in config["symbols"]}; pdfs=[]; aliases=[]
    for paths in by_symbol.values():
        for pdf in paths: shutil.copy2(pdf,target/pdf.name); pdfs.append(pdf.name)
    for symbol,paths in by_symbol.items():
        if paths:
            alias=f"{symbol}-latest.pdf"; shutil.copy2(paths[0],target/alias); aliases.append(alias)
    health_path=ROOT/"cloud-output/health.json"
    if health_path.exists(): shutil.copy2(health_path,target/"health.json"); health=json.loads(health_path.read_text(encoding="utf-8"))
    else: health={"decision":"blocked"}; (target/"health.json").write_text(json.dumps(health),encoding="utf-8")
    # Use a content-derived asset version so every deployment gets a fresh
    # manifest/service-worker/PDF URL even when a CDN still has index.html
    # cached.  The bootstrap below also revalidates the document itself.
    version=hashlib.sha256("|".join(sorted(pdfs)).encode()).hexdigest()[:12]
    def asset(name): return f"{name}?v={version}"
    featured=[]; archive=[]
    for symbol,paths in by_symbol.items():
        if paths:
            latest=paths[0].name; alias=f"{symbol}-latest.pdf"; featured.append(f'<article><h2>{symbol}</h2><p>En güncel rapor</p><a href="{asset(alias)}">Aç</a><a download href="{asset(alias)}">İndir</a></article>')
            archive.append(f'<section><h3>{symbol}</h3><ul>'+''.join(f'<li><a href="{asset(p.name)}">{p.name}</a></li>' for p in paths)+'</ul></section>')
        else: featured.append(f'<article><h2>{symbol}</h2><p>Rapor bekleniyor — Veri sağlığı uygun olduğunda PDF burada yayımlanacak</p></article>')
    stamp=datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%d.%m.%Y %H:%M"); page=f'''<!doctype html><html lang="tr" data-fuji-version="{version}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="robots" content="noindex,nofollow"><meta name="theme-color" content="#071827"><meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"><link rel="manifest" href="{asset('manifest.webmanifest')}"><title>FUJI Raporları</title><style>body{{margin:0;background:#071827;color:#eef6ff;font:16px system-ui}}main{{max-width:760px;margin:auto;padding:24px}}article,section{{background:#102a3d;margin:16px 0;padding:20px;border-radius:16px}}a{{color:#63e4c7}}article a{{display:inline-block;margin:8px 12px 0 0;padding:12px 18px;background:#31c7a5;color:#041713;text-decoration:none;border-radius:10px}}small{{color:#a9c2d2}}@media(max-width:520px){{main{{padding:14px}}article a{{width:calc(50% - 32px);text-align:center}}}}</style></head><body><main><h1>FUJI-ATLAS</h1><p>Veri kararı: <strong>{html.escape(health.get('decision','blocked'))}</strong></p>{''.join(featured)}<h2>Geçmiş raporlar</h2>{''.join(archive) or '<p>Henüz arşivlenmiş rapor yok.</p>'}<small>Son güncelleme: {stamp} Europe/Istanbul</small></main><script>(async()=>{{const query=new URLSearchParams(location.search); if(!query.has('v')){{location.replace('./?v='+Date.now()); return;}} if('serviceWorker' in navigator){{const registration=await navigator.serviceWorker.register('{asset('sw.js')}'); await registration.update();}} try{{const response=await fetch('./index.html?refresh='+Date.now(),{{cache:'no-store'}}); const latest=await response.text(); if(!latest.includes('data-fuji-version="{version}"')){{document.open();document.write(latest);document.close();}}}}catch(_error){{}}}})();</script></body></html>'''; (target/"index.html").write_text(page,encoding="utf-8")
    (target/"manifest.webmanifest").write_text(json.dumps({"name":"FUJI Raporları","short_name":"FUJI","start_url":f"./?v={version}","display":"standalone","background_color":"#071827","theme_color":"#071827"},ensure_ascii=False),encoding="utf-8")
    cache=["./","./index.html",f"./{asset('manifest.webmanifest')}","./health.json",*["./"+asset(p) for p in sorted(pdfs+aliases)]]
    service_worker=f"""const CACHE='fuji-{version}',FILES={json.dumps(cache)};
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(FILES)).then(()=>self.skipWaiting())));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
async function networkFirst(request) {{ try {{ const response=await fetch(request,{{cache:'no-store'}}); const cache=await caches.open(CACHE); cache.put(request,response.clone()); return response; }} catch (error) {{ return (await caches.match(request)) || caches.match('./index.html'); }} }}
self.addEventListener('fetch',event=>{{ const url=new URL(event.request.url); if(event.request.mode==='navigate' || url.pathname.endsWith('/health.json')) event.respondWith(networkFirst(event.request)); else event.respondWith(caches.match(event.request).then(response=>response||fetch(event.request))); }});
"""
    (target/"sw.js").write_text(service_worker,encoding="utf-8")
    return pdfs
if __name__=="__main__": print(json.dumps({"pdfs":build()},ensure_ascii=False))
