# FUJI-ATLAS Cloud Runtime

FUJI, GitHub Actions üzerinde laptop veya telefon açık olmadan çalışır. Varsayılan
`rules` modu OpenAI anahtarı istemez. Piyasa verisi ilk yeterli kaynaktan alınır;
farklı sağlayıcı fiyatları karşılaştırılmaz veya aynı timeframe içinde birleştirilmez.

Birincil broker fiyat sağlayıcısı salt-okuma cTrader Open API'dir (`ctrader-open-api==0.9.2`). `CTRADER_*` secret'ları yoksa sistem güvenli biçimde fallback'e geçer. Gerekli diğer runtime secret'ı `TWELVEDATA_API_KEY` değeridir. Anahtar yoksa sistem
tanımlı spot/proxy fallback'lerini dener; eksik veriyi uydurmaz. Telegram ve e-posta
kullanılmaz.

Çalıştırma:

```sh
python3 -m pip install -r cloud/requirements.txt
python3 cloud/run_cloud.py
python3 cloud/build_report_portal.py
```

Runner `cloud-output/health.json` dosyasını her durumda üretir. Tüm stratejiler
blocked ise exit code 4 döner; workflow bunu veri güvenliği kararı olarak kabul edip
son geçerli PDF'leri korur ve sağlık portalını yayımlar.

Haber katmanı tek yayın kuruluşuna bağlı değildir: Federal Reserve, ECB ve U.S. EIA resmi RSS akışları; GDELT ise yalnız allowlist içindeki yayınların başlık/URL/tarih metadata keşif indeksidir. Reuters Markets erişilebildiğinde opsiyonel ek kaynaktır. API anahtarı gerekmez; makale gövdeleri kopyalanmaz. Başlıklar son 36 saat filtresi ve deterministik sembol/finans terimi eşleşmesiyle kullanılır; haber tek başına işlem yönü üretmez. Kaynak hataları `health.json` içindeki `market_news.warnings` alanında gösterilir ve birleşik cache en fazla altı saat kullanılır.

Base OHLC serileri timeframe yenileme süresine göre incremental güncellenir. Açık
1min mum güncellik için saklanır fakat indikatörlere ve kapalı mum resampling'ine
katılmaz. Her çalışma sabit adın üzerine yazmak yerine
`XAUUSD_YYYYMMDD_HHMMSS.pdf` ve diğer sembol raporları ile `PIYASA_BULTENI_YYYYMMDD_HHMMSS.pdf` üretir. Portal en
güncel raporu öne çıkarır ve sembol başına son 200 raporu “Geçmiş raporlar”
bölümünde tutar.

Her PDF yönetici özeti ve renk kodlu top-down tabloyla başlar. Yalnız hazır
stratejiler için kapanış teyidine bağlı giriş, SL, TP1, TP2, R:R ve muhtemel brüt
pip/standart-lot USD/yüzde hareketi gösterilir. Bu seviyeler emir veya getiri
vaadi değildir; blocked strateji için seviye üretilmez.

Actionable seviyeler ayrıca empirical outcome gate arkasındadır. Her
sembol/strateji için `.fuji-cache/actionable-outcomes.json` içindeki yalnız kapanmış
ve sayısal `pnl_r` sonucu bulunan kayıtlar kullanılır. Minimum örneklem, kazanma
oranı ve profit factor eşikleri `FUJI_RUNTIME_CONFIG.json` içindedir. Eşikler
sağlanmazsa rapor araştırma modunda kalır ve giriş/SL/TP/R:R yayımlamaz.

Portal public GitHub Pages üzerinde olabilir. İlk çevrimiçi ziyaretten sonra mevcut
raporlar service worker cache'i sayesinde offline açılabilir. Sohbette veya loglarda
açığa çıkan tüm secret'lar yenilenmelidir.
