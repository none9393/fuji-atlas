# FUJI veri güvenliği ve kaynak politikası

Bu politika yerel ve bulut çalıştırmalarında aynıdır.

## Kaynak önceliği

1. Resmî kurumlar: Fed/FRED, BLS, ECB/Eurostat, TCMB/TÜİK ve CME.
2. Kimlik doğrulamalı piyasa API'leri: Twelve Data; XAU/USD için MetalpriceAPI yedek.
3. Spot yedek: XAUS API.
4. EURUSD yedek: Yahoo Finance `EURUSD=X`.
5. Yahoo `GC=F` yalnızca XAU/USD spot bulunamadığında proxy'dir; spot gibi gösterilemez.
6. TradingView yalnızca görsel doğrulama içindir; sayısal OHLC'nin otomatik karar kaynağı değildir.
7. Exa/Firecrawl haber ve belge keşfi içindir; bulunan bilgi orijinal kaynakta doğrulanır.
8. Context7 yalnızca teknik dokümantasyon içindir; piyasa verisi değildir.

## Güvenlik ve bütünlük

- Anahtarlar yalnızca ortam değişkeni, GitHub Actions Secrets veya Secret Manager'da tutulur.
- API anahtarları URL yerine mümkün olduğunda header ile gönderilir; log, PDF ve Git'e yazılmaz.
- Her kaynakta `provider`, `retrieved_at_utc`, `status`, `data_age_seconds`, `bar_count` ve `fallback_level` kaydedilir.
- Spot ve futures verisi birleştirilmez. Kaynaklar çelişirse en güncel veri korunur ve fark raporlanır.
- Kritik timeframe eksikse sonuç `BLOCKED` olur; eski chart, tahmin veya tek mumla rapor üretilmez.
- Başarılı koşuda en az iki bağımsız fiyat katmanı karşılaştırılır; anlamlı sapmada işlem senaryosu kapatılır.
- Sohbette veya log'da açığa çıkan anahtarlar iptal edilip yenilenir.
