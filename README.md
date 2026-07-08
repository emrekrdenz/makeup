# URL Ürün Toplayıcı

Gratis ve Watsons kategori URL'lerinden ürün, barkod, içerik ve görsel bilgisi toplamak için lokal web uygulaması.

## Kurulum

```bash
cd /Users/testinium/Desktop/watsons/temiz_proje
./install.sh
```

## Çalıştırma

```bash
cd /Users/testinium/Desktop/watsons/temiz_proje
./start.sh
```

Sonra tarayıcıda aç:

```text
http://127.0.0.1:8765
```

Farklı port gerekirse:

```bash
./start.sh 8766
```

## Desteklenen URL örnekleri

- Watsons: `https://www.watsons.com.tr/makyaj/c/100`
- Gratis: `https://www.gratis.com/sac-bakim/sac-kremleri-c-50302`

## Çıktılar

Her çalışma `outputs/runs/` altında ayrı bir klasör üretir.

- `products.csv`: her ürün için içerik, barkod ve tek ana görsel eşleştirmesi
- `products.xlsx`: ürünler ve ilk görsel önizlemesi
- `images.zip`: indirilen görseller
- `job.log`: çalışma logu

## Klasör yapısı

```text
temiz_proje/
  app.py
  requirements.txt
  install.sh
  start.sh
  outputs/
    gratis_ingredients_scraper.py
    watsons_scraper.py
    runs/
```

Görseller tek klasöre kaydedilir: `outputs/runs/.../images/`.
CSV'de her ürün satırında doğrudan `image_file` ve `image_url` kolonları bulunur.
`image_file` değeri `images/dosya.jpg` formatındadır.
