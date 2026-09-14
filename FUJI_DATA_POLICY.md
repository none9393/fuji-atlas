# FUJI veri güvenliği ve kaynak politikası

## Kaynak seçimi

Her sembol ve temel timeframe için ilk yeterli kaynak seçilir:

1. Twelve Data (`TWELVEDATA_API_KEY` yalnız ortamdan okunur)
2. XAUUSD için XAUS spot
3. EURUSD için Yahoo `EURUSD=X` spot
4. XAUUSD için son çare Yahoo `GC=F` futures proxy

Farklı sağlayıcı fiyatları karşılaştırılmaz. Spot ve futures verileri aynı timeframe
kaydında birleştirilmez. `GC=F`, XAUUSD spot scalping üretmek için kullanılamaz.

## Bütünlük

Collector, verifier ve runner aynı `FUJI_RUNTIME_CONFIG.json` SHA-256 özetini
doğrular. Mum sayısı, benzersiz timestamp, veri yaşı ve piyasa kapanışı strateji
bazında değerlendirilir. Eksik 1min veri swing/intraday raporunu gereksiz yere
engellemez. Blocked strateji için fiyat senaryosu üretilmez.

Last-known-good cache yalnız timeframe'e özel freshness sınırı içindeyse kullanılır.
Yeni geçerli PDF yoksa yayımlanmış son PDF silinmez.

Base 1day, 1h ve 1min geçmişleri timestamp üzerinden incremental birleştirilir.
Açık mum güncellik metadata'sında korunur; kapalı mum sayısına, indikatörlere veya
türetilmiş timeframe hesabına girmez. Haftalık ve aylık veri yaşı mum açılışından
değil kapanış sınırından hesaplanır. Raporlar timestamp'li ve değişmezdir; legacy
sabit adlı PDF'ler yayımlanmaz.

Actionable tablolar yalnız doğrulanmış stratejiler için koşullu teyit, SL, TP1,
TP2 ve risk/getiri oranı gösterebilir. Pip, standart-lot USD ve yüzde hareket
değerleri brüt matematiksel tahmindir; spread, komisyon ve slippage içermez ve
kesin getiri olarak sunulamaz.

Actionable fiyat seviyeleri yalnız doğrulanmış piyasa verisiyle değil, kapanmış
empirical sonuçlarla da kapılanır. Açık/bekleyen sonuçlar istatistiğe katılmaz.
Örneklem, kazanma oranı veya profit factor eşiği sağlanmadığında teknik okuma
gösterilebilir fakat giriş, SL, TP ve R:R araştırma modu dışında yayımlanamaz.

## Çalışma ve teslim

Piyasa haberleri için ücretsiz, anahtarsız çok kaynak katmanı kullanılır: Federal Reserve, ECB ve U.S. EIA resmi RSS; GDELT allowlist içindeki güvenilir yayınların yalnız metadata alanları; erişilebildiğinde Reuters Markets ek başlıkları; mevcut Forex Factory/Fair Economy takvimi. Makale gövdeleri veya description alanları saklanmaz. Haberler son 36 saatle sınırlanır, URL duplicate temizlenir ve tek başına LONG/SHORT, giriş, SL veya TP üretmez. Canlı kaynakların tamamı başarısızsa en fazla altı saatlik birleşik cache kullanılır; daha eski cache `unavailable` olur. Durum ve uyarılar `health.json.market_news` alanındadır.

Varsayılan analiz kurala dayalıdır; OpenAI zorunlu değildir. Telegram ve e-posta
kullanılmaz. GitHub Actions telefon veya laptop açık olmasa bile bulutta çalışır.
Pages portalı ilk çevrimiçi ziyaretten sonra mevcut raporları offline açabilir ve
public olabilir.

Secret'lar yalnız ortam değişkeni veya GitHub Actions Secrets üzerinden okunur.
cTrader Open API salt-okuma birincil fiyat sağlayıcısıdır; `ctrader-open-api==0.9.2`
ile çalışır. Kimlik bilgileri yoksa Twelve Data/XAUS/Yahoo fallback zinciri
kullanılır. “Piyasa gündemi” haber, makro ve doğrulanmış kapalı günlük fiyat
hareketlerinden oluşturulur; veri yoksa boş bırakılmak yerine bu durum açıkça
raporlanır. Secret, account ID ve tokenlar metadata/PDF/health çıktısına yazılmaz.
Sohbette açığa çıkan secret'lar yenilenmelidir; log, PDF veya Git'e yazılmaz.
