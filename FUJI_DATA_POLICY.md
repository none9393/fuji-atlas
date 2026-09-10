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

## Çalışma ve teslim

Varsayılan analiz kurala dayalıdır; OpenAI zorunlu değildir. Telegram ve e-posta
kullanılmaz. GitHub Actions telefon veya laptop açık olmasa bile bulutta çalışır.
Pages portalı ilk çevrimiçi ziyaretten sonra mevcut raporları offline açabilir ve
public olabilir.

Secret'lar yalnız ortam değişkeni veya GitHub Actions Secrets üzerinden okunur.
Sohbette açığa çıkan secret'lar yenilenmelidir; log, PDF veya Git'e yazılmaz.
