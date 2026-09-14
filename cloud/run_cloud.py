#!/usr/bin/env python3
"""Rule-based FUJI runner with cached-OHLC walk-forward evidence."""
from __future__ import annotations
import argparse, hashlib, html, json, os, re, subprocess, sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"cloud-output"; CONFIG=ROOT/"FUJI_RUNTIME_CONFIG.json"; ISTANBUL=ZoneInfo("Europe/Istanbul")
STRATEGY_PLAN={"swing":("1day",20),"intraday":("1h",12),"scalping":("5min",18)}

PUBLIC_NEWS_FEEDS=(
    ("Federal Reserve","https://www.federalreserve.gov/feeds/press_all.xml"),
    ("ECB","https://www.ecb.europa.eu/rss/press.html"),
    ("U.S. EIA","https://www.eia.gov/rss/todayinenergy.xml"),
)
TRUSTED_GDELT_DOMAINS={"reuters.com","apnews.com","bbc.com","cnbc.com","ft.com","wsj.com","bloomberg.com","marketwatch.com"}
GDELT_ENDPOINT="https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_THEMES=("gold","silver","dollar","euro","sterling","yen","Federal Reserve","ECB","Treasury","yields","oil","OPEC","natural gas","inflation","employment","central bank")
NEWS_TERMS=("market","rate","rates","inflation","economy","economic","growth","jobs","payroll","currency","dollar","euro","sterling","pound","yen","gold","silver","oil","crude","opec","natural gas","lng","treasury","bond","yield","central bank","fed","ecb","boj","boe","boc","rba","tariff","trade")
SYMBOL_NEWS_TERMS={
    "XAUUSD":("gold","bullion","precious metals","safe haven","fed","inflation","dollar","yields"),
    "EURUSD":("euro","ecb","eurozone","germany","france","dollar"),
    "GBPUSD":("sterling","pound","bank of england","boe","uk","britain"),
    "USDJPY":("yen","boj","bank of japan","intervention","japan","treasury yields"),
    "USDCAD":("canada","canadian dollar","loonie","boc","bank of canada","oil"),
    "AUDUSD":("australia","australian dollar","aussie","rba","china","risk sentiment"),
    "XAGUSD":("silver","precious metals","industrial metals","dollar","yields"),
    "WTIUSD":("oil","crude","wti","brent","opec","refinery","inventories","supply"),
    "NATGAS":("natural gas","lng","storage","weather","pipeline","supply"),
    "DXY":("dollar","dollar index","fed","inflation","payroll","jobs","rates"),
    "US10Y":("treasury","bonds","yields","auction","fed","inflation","rates"),
}
_WORD_RE=re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)?",re.I)

def _clean_url(url):
    try:
        parts=urlsplit(url.strip()); query=[(k,v) for k,v in parse_qsl(parts.query) if not k.lower().startswith(("utm_","fbclid","gclid"))]
        return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),""))
    except Exception: return url.strip()

def parse_news_datetime(value):
    if not value: return None
    value=str(value).strip()
    try:
        if re.fullmatch(r"\d{8}T\d{6}Z",value): return datetime.strptime(value,"%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        parsed=datetime.fromisoformat(value.replace("Z","+00:00")); return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            parsed=parsedate_to_datetime(value); return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError,ValueError): return None

def _text(node): return "" if node is None else " ".join("".join(node.itertext()).split())
def parse_public_feed(payload, source, now=None):
    now=now or datetime.now(timezone.utc); root=ET.fromstring(payload); out=[]
    for item in root.findall(".//item")+root.findall(".//{*}entry"):
        children={child.tag.rsplit("}",1)[-1]:child for child in list(item)}; title=_text(children.get("title")); link_node=children.get("link"); link=(link_node.get("href") if link_node is not None else None) or _text(link_node)
        date_node=next((children.get(tag) for tag in ("pubDate","date","updated","published") if children.get(tag) is not None),None)
        published=parse_news_datetime(_text(date_node));
        if title and link and published: out.append({"title":title,"url":_clean_url(link),"published_at_utc":published.astimezone(timezone.utc).isoformat(),"source":source})
    return out

def _domain(url):
    host=urlsplit(url).netloc.lower().split(":",1)[0]; return host[4:] if host.startswith("www.") else host
def _whole_word(text, phrase):
    words=[w.lower() for w in _WORD_RE.findall(text.lower())]; target=phrase.lower().split(); n=len(target)
    return any(words[i:i+n]==target for i in range(len(words)-n+1))
def related_symbols(title):
    text=title.lower(); return [s for s,terms in SYMBOL_NEWS_TERMS.items() if any(_whole_word(text,t) for t in terms)]
def is_relevant_news(article, now=None):
    now=now or datetime.now(timezone.utc); title=article.get("title",""); published=parse_news_datetime(article.get("published_at_utc"));
    if not published or (now-published).total_seconds()>36*3600 or (published-now).total_seconds()>300: return False
    return bool(related_symbols(title)) and any(_whole_word(title,t) for t in NEWS_TERMS)

def _fetch(url, timeout=12):
    request=Request(url,headers={"User-Agent":"FUJI-ATLAS/1.0"})
    with urlopen(request,timeout=timeout) as response: return response.read()

def _gdelt_articles(now):
    query=" OR ".join('"'+x+'"' for x in GDELT_THEMES); url=GDELT_ENDPOINT+"?"+urlencode({"query":query,"mode":"ArtList","maxrecords":250,"format":"json","timespan":"36h","sort":"HybridRel"}); payload=json.loads(_fetch(url)); out=[]
    for row in payload.get("articles",[]):
        link=_clean_url(row.get("url", "")); domain=_domain(link)
        if domain not in TRUSTED_GDELT_DOMAINS: continue
        published=parse_news_datetime(row.get("seendate"));
        if row.get("title") and link and published: out.append({"title":row["title"],"url":link,"domain":domain,"published_at_utc":published.astimezone(timezone.utc).isoformat(),"source":domain})
    return out

def load_public_market_news(now=None, cache_path=None):
    now=now or datetime.now(timezone.utc); cache_path=Path(cache_path or ROOT/".fuji-cache/public-market-news.json"); cache_path.parent.mkdir(parents=True,exist_ok=True); articles=[]; warnings=[]; sources=[]
    for source,url in PUBLIC_NEWS_FEEDS:
        try: articles.extend(parse_public_feed(_fetch(url),source,now)); sources.append(source)
        except Exception as exc: warnings.append(f"{source}: {type(exc).__name__}")
    try: gdelt=_gdelt_articles(now); articles.extend(gdelt); sources.append("GDELT")
    except Exception as exc: warnings.append(f"GDELT: {type(exc).__name__}")
    unique={a["url"]:a for a in articles if is_relevant_news(a,now)}; articles=sorted(unique.values(),key=lambda a:a["published_at_utc"],reverse=True)
    status="live" if sources else "unavailable"
    if not sources and cache_path.exists():
        try:
            cached=json.loads(cache_path.read_text(encoding="utf-8")); retrieved=parse_news_datetime(cached.get("retrieved_at_utc"));
            if retrieved and (now-retrieved).total_seconds()<=21600: cached["status"]="cache"; cached.setdefault("warnings",[]).extend(warnings); return cached
        except (OSError,json.JSONDecodeError): pass
    result={"status":status,"source":" + ".join(sources) if sources else "","source_url":"https://www.gdeltproject.org/","retrieved_at_utc":now.astimezone(timezone.utc).isoformat(),"articles":articles,"warnings":warnings}
    cache_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8"); return result

def market_agenda(news, macro, market, verification, evidence, config):
    items=[]
    for article in news.get("articles",[])[:4]:
        items.append(f"Haber gündemi: {article.get('source','Kaynak')} — {article.get('title','Başlık yok')} ({', '.join(related_symbols(article.get('title',''))) or 'piyasa'})")
    for event in (macro.get("events") or [])[:3]:
        items.append(f"Makro gündemi: {event.get('time') or event.get('datetime') or 'zaman belirtilmedi'} · {event.get('title') or event.get('name') or 'olay'}" if isinstance(event,dict) else f"Makro gündemi: {event}")
    for symbol in config.get("symbols",[]):
        values=closed_values(((market.get("symbols",{}).get(symbol) or {}).get("intervals") or {}).get("1day",{}))
        if len(values)<2: continue
        delta=float(values[-1]["close"])-float(values[-2]["close"]); stats=indicators(values); atr=stats.get("atr14") if stats else None; ratio=(abs(delta)/atr) if atr else 0.0
        decisions=[(evidence.get(symbol,{}).get(k,{}) or {}).get("decision") for k in ("swing","intraday","scalping")]; strongest=next((d for d in decisions if d),"SARI · TEMKİNLİ")
        provider=((market.get("symbols",{}).get(symbol) or {}).get("intervals") or {}).get("1day",{}).get("status","unavailable")
        items.append(f"Fiyat gündemi: {symbol} son günlük kapanışta {delta:+.5f} değişti; hareket {ratio:.2f} ATR, günlük yön {stats.get('direction','belirsiz') if stats else 'belirsiz'}, veri kararı {provider}, en güçlü fırsat {strongest}.")
        if len(items)>=10: break
    return items or ["Yeni haber veya makro başlığı yok; doğrulanmış fiyat verisinde ayrıca raporlanabilir günlük hareket oluşmadı."]

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
    sign=1 if stats["direction"]!="aşağı" else -1; entry=stats["close"]+sign*stats["atr14"]*.1; sl=entry-sign*stats["atr14"]; risk=abs(entry-sl); tp1=entry+sign*risk*1.5; tp2=entry+sign*risk*2.5; pip=.01 if symbol in ("XAUUSD","USDJPY") else .0001; value=10 if symbol in ("EURUSD","GBPUSD","AUDUSD") else None
    return {"side":"LONG" if sign>0 else "SHORT","side_tr":"ALIM" if sign>0 else "SATIM","sign":sign,"entry":entry,"sl":sl,"tp1":tp1,"tp2":tp2,"rr1":1.5,"rr2":2.5,"tp1_pips":abs(tp1-entry)/pip,"tp2_pips":abs(tp2-entry)/pip,"tp1_usd":abs(tp1-entry)/pip*value if value else None,"tp2_usd":abs(tp2-entry)/pip*value if value else None,"tp1_pct":abs(tp1-entry)/entry*100,"tp2_pct":abs(tp2-entry)/entry*100}
def cross_market_context(market,symbol):
    def returns(item):
        vals=closed_values(item); by={str(v.get("datetime",""))[:10]:float(v["close"]) for v in vals}; keys=sorted(by); return {k:(by[k]/by[keys[i-1]]-1) for i,k in enumerate(keys) if i and by[keys[i-1]]}
    base=returns(market.get("symbols",{}).get(symbol,{}).get("intervals",{}).get("1day",{})); dxy=returns(market.get("symbols",{}).get("DXY",{}).get("intervals",{}).get("1day",{})); common=sorted(set(base)&set(dxy))[-120:]
    if len(common)<20: return {"correlation":None,"sample":len(common),"relation":"yetersiz ortak örneklem","text":"Yayımlanmadı; en az 20 ortak gözlem gerekir."}
    x=[base[k] for k in common]; y=[dxy[k] for k in common]; mx=sum(x)/len(x); my=sum(y)/len(y); den=((sum((a-mx)**2 for a in x)*sum((b-my)**2 for b in y))**.5); corr=sum((a-mx)*(b-my) for a,b in zip(x,y))/den if den else 0
    return {"correlation":corr,"sample":len(common),"relation":"ölçülmüş korelasyon; nedensellik/garanti değildir","text":f"Ölçülmüş korelasyon {corr:.3f}; {len(common)} ortak gözlem."}
def dxy_conflict(symbol,cross,config):
    relation=(config.get("instrument_profiles",{}).get(symbol) or {}).get("dxy_relation")
    corr=cross.get("correlation")
    if corr is None or relation not in ("inverse","same"): return False
    return (relation=="inverse" and corr>0) or (relation=="same" and corr<0)
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
def render_pdf(symbol,market,verification,target,report_id,created,evidence,config,news=None):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate,Paragraph,PageBreak,Table,TableStyle,KeepTogether
    regular,bold=font_names(); entries=market["symbols"][symbol]["intervals"]; primary=entries.get("1h",{}); stats=indicators(closed_values(primary)) or indicators(closed_values(entries.get("1day",{}))); latest=(primary.get("values") or [{}])[-1]; conditions=market_conditions(entries); cross=cross_market_context(market,symbol); title=ParagraphStyle("title",fontName=bold,fontSize=16,leading=20); body=ParagraphStyle("body",fontName=regular,fontSize=8.5,leading=11); head=ParagraphStyle("head",fontName=bold,fontSize=11,leading=14,spaceBefore=8)
    cond_text="; ".join(conditions["rsi_flags"]) or "RSI aşırı alım/satım sinyali yok"; conflict="var" if conditions["conflict"] else "yok"; corr_text=f"{cross['correlation']:.3f} ({cross['sample']} ortak günlük gözlem)" if cross.get('correlation') is not None else f"yayımlanmadı ({cross['sample']} ortak gözlem; en az 20 gerekli)"
    brief=market_brief(entries,stats); related=(news or {}).get("articles",[]) if news else []; related=[a for a in related if symbol in related_symbols(a.get("title",""))][:3]
    news_lines="Son 36 saatte bu sembolle eşleşen doğrulanmış yeni piyasa başlığı bulunmadı."
    if related: news_lines="<br/>".join(f"{html.escape(a.get('source',''))} · {html.escape(a['title'])} · {parse_news_datetime(a['published_at_utc']).astimezone(ISTANBUL).strftime('%d.%m %H:%M')} · <link href=\"{html.escape(a['url'],quote=True)}\" color=\"#a00000\">Bağlantı</link>" for a in related)
    story=[Paragraph(f"FUJI Piyasa Brifingi — {symbol}",title),Paragraph(f"Rapor: {report_id} · {created.isoformat()}",body),Paragraph("Yönetici özeti",head),Paragraph(f"Actionable Intelligence: {brief} En güçlü izlenen fırsat, kapanış teyidi bekleyen yapı yönüdür; DXY korelasyonu {corr_text}, US10Y yalnız bağlamdır. Teknik koşullar: {cond_text}; timeframe çatışması {conflict}.",body),Paragraph("Genel piyasa görünümü",head),Paragraph(brief,body),Paragraph("Karar ekranı",head),Paragraph("Karar ekranında kapanış teyitli tetik, risk ve hedefler aşağıdaki fırsat planlarında ayrıntılanır.",body),Paragraph("Makro ve çapraz piyasa değerlendirmesi",head),Paragraph(f"DXY korelasyonu: {corr_text} — {cross['relation']}. Beklenen ilişki bağlam teyididir; nedensellik/garanti değildir. US10Y bağlamı sinyal üretmez. Haber bağlamı: {news_lines}",body),Paragraph("Aktif koşullar ve dikkat noktaları",head),Paragraph(f"Timeframe yön çatışması: {conflict}. {cond_text}. 1H ≥2 ATR mum: {conditions['expansion']}; ≥1.5 ATR boşluk: {conditions['gaps']}.",body),Paragraph("Fırsat planları",head),Paragraph("Karar tablos",head)]
    P=lambda value: Paragraph(str(value),body)
    summary=[[P("Fırsat"),P("Karar / yön"),P("Tetik"),P("Risk"),P("Hedefler"),P("Kanıt / süre")]]
    for strategy,label in (("swing","Çok günlük fırsat: 2–20 gün"),("intraday","Seans fırsatı: 2–12 saat"),("scalping","Yakın fırsat: 30–90 dakika")):
        state=verification["symbols"][symbol]["strategies"][strategy]; ev=evidence[strategy]; local=indicators(closed_values(entries.get(ev["interval"],{}))) or stats; levels=actionable_levels(symbol,local) if ev["status"]!="red" and state["status"]!="blocked" else None; direction=f"{levels['side']} / {levels['side_tr']}" if levels else (("LONG / ALIM" if local and local.get("direction")=="yukarı" else "SHORT / SATIM") if local and local.get("direction") in ("yukarı","aşağı") else "-"); summary.append([P(label),P(f"{ev['decision']}<br/>{direction}"),P(f"Kapanış teyidi: {levels['entry']:.5f}" if levels else "Kapanış teyidi yok"),P(f"SL {levels['sl']:.5f}" if levels else "KIRMIZI · GİRME"),P(f"TP1 {levels['tp1']:.5f}<br/>TP2 {levels['tp2']:.5f}" if levels else "Hedef yok"),P(f"%{ev['win_rate']*100:.1f}, n={ev['sample_size']}<br/>{label.split(': ',1)[-1]}")])
    table=Table(summary,repeatRows=1,colWidths=[30*mm,31*mm,31*mm,25*mm,30*mm,30*mm],hAlign="LEFT"); styles=[("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),("FONT",(0,0),(-1,-1),regular,6.2),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#dceaf3")),("GRID",(0,0),(-1,-1),.25,colors.grey)]
    for row,strategy in enumerate(("swing","intraday","scalping"),1): styles.append(("BACKGROUND",(0,row),(-1,row),colors.HexColor({"green":"#d9f2e6","yellow":"#fff0c2","red":"#f7dada"}[evidence[strategy]["status"] if verification["symbols"][symbol]["strategies"][strategy]["status"]!="blocked" else "red"])))
    table.setStyle(TableStyle(styles)); story.append(table)
    decisions="; ".join(f"{label}: {evidence[strategy]['decision']}" for strategy,label in (("swing","Çok günlük fırsat"),("intraday","Seans fırsatı"),("scalping","Yakın fırsat")))
    story.append(Paragraph("Fırsat planları",head))
    for strategy,label in (("swing","Çok günlük fırsat: 2–20 gün"),("intraday","Seans fırsatı: 2–12 saat"),("scalping","Yakın fırsat: 30–90 dakika")):
        state=verification["symbols"][symbol]["strategies"][strategy]; ev=evidence[strategy]; local=indicators(closed_values(entries.get(ev["interval"],{}))) or stats; levels=actionable_levels(symbol,local) if ev["status"]!="red" and state["status"]!="blocked" else None
        detail=[[P("Fırsat / süre"),P(f"{label} · {ev['decision']}")],[P("Yapı"),P(f"Yön: {(levels or {}).get('side','-')} / {(levels or {}).get('side_tr','-')} · EMA20: {local['ema20']:.5f} · RSI14: {local['rsi14']:.2f} · ATR14: {local['atr14']:.5f}" if local else "Yapı hesaplanamadı")],[P("Geçmiş kanıt"),P(f"Geçmiş gerçekleşme: %{ev['win_rate']*100:.1f} · örneklem: {ev['sample_size']} · olumlu/olumsuz: {ev['wins']}/{ev['losses']} · {ev['interval']} / {ev['horizon']} ileri mum")]]
        if state["status"]=="blocked": detail.append([P("Tetik ve seviyeler"),P("KIRMIZI · GİRME — BLOCKED; seviyeler yayımlanmadı. "+"; ".join(state["reasons"]))])
        elif levels:
            detail.append([P("Tetik ve seviyeler"),P(f"Koşullu giriş ({levels['side']} / {levels['side_tr']}): {levels['entry']:.5f}<br/>SL: {levels['sl']:.5f} · TP1: {levels['tp1']:.5f} · TP2: {levels['tp2']:.5f} · R:R 1:{levels['rr1']:.1f}/1:{levels['rr2']:.1f}")]); detail.append([P("Potansiyel hareket"),P(f"TP1 {levels['tp1_pips']:.1f} pip / %{levels['tp1_pct']:.2f}; TP2 {levels['tp2_pips']:.1f} pip / %{levels['tp2_pct']:.2f}. Spread, komisyon ve slippage hariç; invalidation SL veya teyit sonrası geri kapanıştır.")])
        story.append(KeepTogether([Paragraph(label,head),Table(detail,colWidths=[40*mm,125*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.25,colors.grey),('BACKGROUND',(0,0),(0,-1),colors.HexColor('#eef3f7')),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]))]))
        trigger=f"{ev['interval']} kapanışında EMA20 yönü ve mevcut mum aralığı teyit edilmeden giriş tetiklenmez."
        if levels:
            usd_text=f" / yaklaşık ${levels['tp1_usd']:.2f}" if levels.get('tp1_usd') is not None else " / USD hesaplanmadı (contract-size doğrulanmadı)"; usd_text2=f" / yaklaşık ${levels['tp2_usd']:.2f}" if levels.get('tp2_usd') is not None else " / USD hesaplanmadı (contract-size doğrulanmadı)"
            story.append(Paragraph(f"Tetikleyici: {trigger}",body)); story.append(Paragraph(f"Koşullu giriş ({levels['side']} / {levels['side_tr']}): {levels['entry']:.5f} · SL: {levels['sl']:.5f} · TP1: {levels['tp1']:.5f} · TP2: {levels['tp2']:.5f} · R:R: 1:{levels['rr1']:.1f} / 1:{levels['rr2']:.1f}",body)); story.append(Paragraph(f"Pip/fiyat hareketi: TP1 {levels['tp1_pips']:.1f} pip{usd_text} / %{levels['tp1_pct']:.2f}; TP2 {levels['tp2_pips']:.1f} pip{usd_text2} / %{levels['tp2_pct']:.2f}. Geçersizleşme: SL veya teyit sonrası referans aralığına geri kapanış.",body))
        else: story.append(Paragraph(f"Tetikleyici: {trigger} Geçersizleşme: gerçekleşme oranı yeterli örnekte %45 altında kaldığı için strateji kırmızı; giriş/SL/TP yayımlanmadı.",body))
    story += [Paragraph("Genel risk çerçevesi",head),Paragraph("Kaldıraç kayıp riskini büyütür. Spread, komisyon ve slippage hariçtir. Bu rapor yatırım tavsiyesi değildir.",body),PageBreak(),Paragraph("Bilgi tabanı uygulama notları",head)]
    for note in knowledge_notes(symbol): story.append(Paragraph("• "+html.escape(note),body))
    story.append(Paragraph("Timeframe veri sözleşmesi",head)); rows=[["TF","Provider","Tür/Mod","Son kapalı mum","Yaş(sn)","Kapalı/Toplam"]]
    for interval in config["required_intervals"]:
        item=entries.get(interval,{}); rows.append([interval,item.get("provider") or "-",f"{item.get('source_type') or '-'}/{item.get('source_mode') or '-'}",item.get("last_closed_bar_at_utc") or "-",str(item.get("data_age_seconds")),f"{item.get('bar_count',0)}/{item.get('total_bar_count',0)}"])
    data_table=Table(rows,repeatRows=1,colWidths=[17*mm,25*mm,30*mm,45*mm,18*mm,24*mm]); data_table.setStyle(TableStyle([("FONT",(0,0),(-1,-1),regular,6.5),("FONT",(0,0),(-1,0),bold,6.5),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#dceaf3")),("GRID",(0,0),(-1,-1),.25,colors.grey)])); story.append(data_table); story.append(Paragraph("Karar renkleri",head)); legend=Table([[P("YEŞİL · GİR"),P("SARI · TEMKİNLİ"),P("KIRMIZI · GİRME")]],colWidths=[55*mm]*3); legend.setStyle(TableStyle([("BACKGROUND",(0,0),(0,0),colors.HexColor('#d9f2e6')),("BACKGROUND",(1,0),(1,0),colors.HexColor('#fff0c2')),("BACKGROUND",(2,0),(2,0),colors.HexColor('#f7dada')),('GRID',(0,0),(-1,-1),.25,colors.grey),('VALIGN',(0,0),(-1,-1),'TOP')])); story.append(legend); story.append(Paragraph("Kaynaklar: 107_2_Pariteler Arası Korelasyon Mantığı; 106_1_DXY US Dollar Index Nedir; 108_3_HTF Yapı Analizi ve DXY Kullanımı; 021_3 Önemli Stratejik Kurallar.",body)); story.append(Paragraph("Bu rapor yatırım tavsiyesi değildir.",body)); SimpleDocTemplate(str(target),pagesize=A4,leftMargin=13*mm,rightMargin=13*mm,topMargin=13*mm,bottomMargin=13*mm).build(story,onFirstPage=page_number,onLaterPages=page_number)
def render_market_bulletin(news, macro, agenda, target, created, config):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle
    regular,bold=font_names(); body=ParagraphStyle("bulletin_body",fontName=regular,fontSize=8.5,leading=11); head=ParagraphStyle("bulletin_head",fontName=bold,fontSize=12,leading=15,spaceBefore=8); title=ParagraphStyle("bulletin_title",fontName=bold,fontSize=17,leading=21)
    story=[Paragraph("FUJI Güncel Piyasa Bülteni",title),Paragraph(f"Gerçek üretim zamanı: {created.astimezone(ISTANBUL).isoformat()}",body),Paragraph(f"Haber kaynak durumu: {news.get('status','unavailable')} · {html.escape(news.get('source','') or 'Kaynak yok')}",body),Paragraph(f"Makro takvim durumu: {macro.get('status','unavailable')}",body),Paragraph("Yönetici özeti",head)]
    story.append(Paragraph(f"Son 36 saatte {len(news.get('articles',[]))} doğrulanmış piyasa başlığı sembol eşlemesiyle tarandı. Başlıklar yalnız bağlam sağlar; tek başına işlem yönü oluşturmaz.",body)); story.append(Paragraph("Piyasa gündemi",head)); story.extend(Paragraph("• "+html.escape(item),body) for item in agenda); story.append(Paragraph("Sembol panoraması",head)); story.append(Paragraph(", ".join(config["symbols"]),body)); story.append(Paragraph("Etki yaratan güncel gelişmeler",head))
    rows=[[Paragraph("Zaman / kaynak",body),Paragraph("Güncel başlık",body),Paragraph("Bağlanan piyasalar",body)]]
    for a in news.get("articles",[])[:30]:
        local=parse_news_datetime(a["published_at_utc"]).astimezone(ISTANBUL).strftime("%d.%m %H:%M"); link=f'<link href="{html.escape(a["url"],quote=True)}" color="#a00000">{html.escape(a["title"])}</link>'; rows.append([Paragraph(f"{local} · {html.escape(a.get('source',''))}",body),Paragraph(link,body),Paragraph(", ".join(related_symbols(a["title"])),body)])
    if len(rows)==1: rows.append([Paragraph("—",body),Paragraph("Son 36 saatte eşleşen doğrulanmış yeni başlık bulunmadı.",body),Paragraph("—",body)])
    table=Table(rows,repeatRows=1,colWidths=[36*mm,96*mm,38*mm]); table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#dceaf3")),("GRID",(0,0),(-1,-1),.25,colors.grey),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4)])); story.append(table); story.append(Paragraph("Makro saatleri",head)); story.append(Paragraph("Forex Factory/Fair Economy takvimi: "+str(macro.get("status","unavailable")),body)); story.append(Paragraph("Kaynaklar ve yöntem",head)); story.append(Paragraph("Federal Reserve · ECB · U.S. EIA · GDELT · erişilebildiğinde Reuters Markets · Forex Factory/Fair Economy. Resmî RSS birincil olgu katmanıdır; GDELT yalnız güvenilir yayın metadata/keşif indeksidir. Makale gövdeleri kopyalanmamıştır; son 36 saat filtresi, URL duplicate temizliği ve sembol/finans terimi eşleşmesi uygulanır.",body)); SimpleDocTemplate(str(target),pagesize=A4,leftMargin=13*mm,rightMargin=13*mm,topMargin=13*mm,bottomMargin=13*mm).build(story,onFirstPage=page_number,onLaterPages=page_number)

def run(market_data=None,analysis_mode="rules",now=None):
    OUT.mkdir(exist_ok=True); now=now or datetime.now(ISTANBUL); path,market=load_market(market_data); config=json.loads(CONFIG.read_text(encoding="utf-8")); news=load_public_market_news(now.astimezone(timezone.utc)) if market_data is None else {"status":"unavailable","source":"fixture run","source_url":"","retrieved_at_utc":now.astimezone(timezone.utc).isoformat(),"articles":[],"warnings":["network disabled for fixture input"]}
    if market.get("config_sha256")!=digest(): raise RuntimeError("market data/config SHA mismatch")
    verify_path=OUT/"verification.json"; subprocess.run([sys.executable,str(ROOT/"05_araclar/fuji_live_data_verifier.py"),str(path),"--output",str(verify_path)],check=True,stdout=subprocess.DEVNULL); verification=json.loads(verify_path.read_text(encoding="utf-8"));
    if analysis_mode=="openai" and not os.getenv("OPENAI_API_KEY"): raise RuntimeError("OPENAI_API_KEY is required only for openai mode")
    evidence={symbol:{strategy:walk_forward(closed_values(market["symbols"][symbol]["intervals"].get(interval,{})),symbol,strategy) for strategy,(interval,_) in STRATEGY_PLAN.items()} for symbol in config["symbols"]}
    for symbol in config["symbols"]:
        cross=cross_market_context(market,symbol)
        if dxy_conflict(symbol,cross,config):
            for ev in evidence[symbol].values():
                if ev["status"]=="green": ev.update(status="yellow",color="yellow",decision="SARI · TEMKİNLİ",reason="DXY korelasyon yönü fırsatı temkine indirdi")
    stamp=now.astimezone(ISTANBUL).strftime("%Y%m%d_%H%M%S"); files=[]
    for symbol,detail in verification["symbols"].items():
        if any(value["status"]=="ready" for value in detail["strategies"].values()): name=f"{symbol}_{stamp}.pdf"; render_pdf(symbol,market,verification,OUT/name,f"{symbol}-{stamp}",now.astimezone(ISTANBUL),evidence[symbol],config,news); files.append(name)
    macro={"status":"unavailable","source":"Forex Factory/Fair Economy cache","events":[]}
    agenda=market_agenda(news,macro,market,verification,evidence,config); bulletin_name=f"PIYASA_BULTENI_{stamp}.pdf"; render_market_bulletin(news,macro,agenda,OUT/bulletin_name,now,config); files.append(bulletin_name)
    providers={s:{i:{k:v.get(k) for k in ("provider","source_type","source_mode","last_bar_closed","last_bar_at_utc","last_closed_bar_at_utc","data_age_seconds","bar_count","total_bar_count","fallback_level")} for i,v in d["intervals"].items()} for s,d in market["symbols"].items()}; intermarket={s:cross_market_context(market,s) for s in config["symbols"]}; health={"generated_at_utc":now.astimezone(timezone.utc).isoformat(),"generated_at_local":now.astimezone(ISTANBUL).isoformat(),"analysis_mode":analysis_mode,"config_sha256":digest(),"decision":verification["decision"],"symbols":verification["symbols"],"empirical_outcome_gates":evidence,"walk_forward":evidence,"providers":providers,"intermarket_context":intermarket,"macro":macro,"market_news":news,"market_agenda":agenda,"files":files}; (OUT/"health.json").write_text(json.dumps(health,ensure_ascii=False,indent=2),encoding="utf-8"); return 4 if verification["decision"]=="blocked" else 0
def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--market-data"); parser.add_argument("--analysis-mode",choices=("rules","openai"),default="rules"); args=parser.parse_args()
    try: raise SystemExit(run(args.market_data,args.analysis_mode))
    except SystemExit: raise
    except Exception as exc: print(f"FUJI runtime error: {exc}",file=sys.stderr); raise SystemExit(2)
if __name__=="__main__": main()
