# FUJI-ATLAS Cloud Runtime

FUJI, GitHub Actions üzerinde laptop veya telefon açık olmadan çalışır. Varsayılan
`rules` modu OpenAI anahtarı istemez. Piyasa verisi ilk yeterli kaynaktan alınır;
farklı sağlayıcı fiyatları karşılaştırılmaz veya aynı timeframe içinde birleştirilmez.

Gerekli tek runtime secret'ı `TWELVEDATA_API_KEY` değeridir. Anahtar yoksa sistem
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

Base OHLC serileri timeframe yenileme süresine göre incremental güncellenir. Açık
1min mum güncellik için saklanır fakat indikatörlere ve kapalı mum resampling'ine
katılmaz. Her çalışma sabit adın üzerine yazmak yerine
`XAUUSD_YYYYMMDD_HHMMSS.pdf` ve `EURUSD_YYYYMMDD_HHMMSS.pdf` üretir. Portal en
güncel raporu öne çıkarır ve sembol başına son 200 raporu “Geçmiş raporlar”
bölümünde tutar.

Portal public GitHub Pages üzerinde olabilir. İlk çevrimiçi ziyaretten sonra mevcut
raporlar service worker cache'i sayesinde offline açılabilir. Sohbette veya loglarda
açığa çıkan tüm secret'lar yenilenmelidir.
