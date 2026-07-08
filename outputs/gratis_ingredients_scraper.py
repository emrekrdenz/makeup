#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import html
import json
import random
import re
import ssl
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


DEFAULT_CATEGORY_URL = "https://www.gratis.com/sac-bakim/sac-kremleri-c-50302"
DEFAULT_OUTPUT = "gratis_sac_kremleri_icerikler.csv"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

PRODUCT_HREF_RE = re.compile(r'href="([^"]*?-p-\d+[^"]*)"', re.IGNORECASE)
PRODUCT_ID_RE = re.compile(r"-p-(\d+)(?:\D|$)", re.IGNORECASE)
PAGE_RE = re.compile(r"[?&]page=(\d+)", re.IGNORECASE)
TITLE_META_RE = re.compile(
    r'<meta\s+(?:property|name)="og:title"\s+content="([^"]+)"',
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
BARCODE_RE = re.compile(r"Ürün\s+Barkodu\s*([0-9]{8,14})", re.IGNORECASE)
EAN_RE = re.compile(
    r'"key"\s*:\s*"eanUpc".{0,500}?"value"\s*:\s*"([0-9]{8,14})"',
    re.IGNORECASE | re.DOTALL,
)
IMAGE_URL_RE = re.compile(
    r"https://api\.gratis\.retter\.io/[^\"'<>\s\\]+?\.(?:jpg|jpeg|png|webp)",
    re.IGNORECASE,
)
JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
INGREDIENT_LABEL_RE = re.compile(
    r"(?:Ürün\s*İçeriği|Urun\s*Icerigi|İçindekiler|Icindekiler|Ingredients|INCI)\s*[:：]\s*(.*)",
    re.IGNORECASE | re.DOTALL,
)
STOP_RE = re.compile(
    r"\n\s*\n|(?:Uyarı ve Güvenlik Bilgileri|Kullanım(?: Şekli)?|Bakım Önerisi|"
    r"Menşei|Saklama Koşulları|Ürün Barkodu|Ek Özellikler|Saç Tipi|Etki|Hacim|"
    r"Cinsiyet|Yaş Grubu|Ambalaj Tipi)\s*[:：]",
    re.IGNORECASE,
)


@dataclass
class ProductTarget:
    url: str
    pages: Tuple[int, ...]
    first_seen: int


def build_ssl_context(insecure: bool) -> ssl.SSLContext:
    if insecure:
        return ssl._create_unverified_context()
    return ssl.create_default_context()


def fetch_html(
    url: str,
    context: ssl.SSLContext,
    timeout: int,
    retries: int,
    delay: float = 0.0,
) -> str:
    last_error: Optional[BaseException] = None
    for attempt in range(retries + 1):
        if delay > 0:
            time.sleep(delay + random.uniform(0, delay / 2))

        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
                },
            )
            with urlopen(request, timeout=timeout, context=context) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except HTTPError as exc:
            last_error = exc
            if exc.code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except (URLError, TimeoutError, ssl.SSLError) as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def fetch_bytes(
    url: str,
    context: ssl.SSLContext,
    timeout: int,
    retries: int,
    delay: float = 0.0,
) -> bytes:
    last_error: Optional[BaseException] = None
    for attempt in range(retries + 1):
        if delay > 0:
            time.sleep(delay + random.uniform(0, delay / 2))

        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    "Referer": "https://www.gratis.com/",
                },
            )
            with urlopen(request, timeout=timeout, context=context) as response:
                return response.read()
        except HTTPError as exc:
            last_error = exc
            if exc.code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except (URLError, TimeoutError, ssl.SSLError) as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"Could not download {url}: {last_error}")


def decode_escaped_source(source: str) -> str:
    source = html.unescape(source)
    replacements = {
        r"\u003c": "<",
        r"\u003e": ">",
        r"\u0026": "&",
        r"\u0027": "'",
        r"\u002F": "/",
        r"\/": "/",
        r"\"": '"',
    }
    for old, new in replacements.items():
        source = source.replace(old, new)
    return source


def decode_page_text(source: str) -> str:
    source = decode_escaped_source(source)
    replacements = {
        r"\n": "\n",
        r"\r": "\r",
        r"\t": "\t",
    }
    for old, new in replacements.items():
        source = source.replace(old, new)

    source = re.sub(r"<br\s*/?>", "\n", source, flags=re.IGNORECASE)
    source = re.sub(r"</p\s*>", "\n", source, flags=re.IGNORECASE)
    source = re.sub(r"<[^>]+>", " ", source)
    source = html.unescape(source)
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    source = re.sub(r"[ \t\f\v]+", " ", source)
    source = re.sub(r" *\n *", "\n", source)
    source = re.sub(r"\n{3,}", "\n\n", source)
    return source


def compact_text(value: str) -> str:
    value = value.strip(" \n\t\"'.,;:-")
    value = re.sub(r"\s+", " ", value)
    value = value.replace(" ,", ",").replace(" .", ".")
    return value.strip()


def compact_multiline(value: str) -> str:
    lines = [compact_detail_line(line) for line in value.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def compact_detail_line(value: str) -> str:
    value = value.strip(" \n\t\"'")
    value = re.sub(r"\s+", " ", value)
    value = value.replace(" ,", ",").replace(" .", ".")
    return value.strip()


def clean_title(value: str) -> str:
    value = compact_text(html.unescape(value))
    value = re.sub(r"\s+-\s+Gratis\s*$", "", value, flags=re.IGNORECASE)
    return value


def extract_product_id(url: str) -> str:
    match = PRODUCT_ID_RE.search(url)
    return match.group(1) if match else ""


def category_page_url(category_url: str, page: int) -> str:
    parsed = urlparse(category_url)
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key.lower() != "page"]
    if page > 1:
        query.append(("page", str(page)))
    return urlunparse(parsed._replace(query=urlencode(query)))


def extract_max_page(source: str) -> int:
    pages = [int(match) for match in PAGE_RE.findall(source)]
    return max(pages) if pages else 1


def extract_product_links(source: str, base_url: str) -> List[str]:
    links: List[str] = []
    seen: Set[str] = set()

    for raw_href in PRODUCT_HREF_RE.findall(source):
        href = html.unescape(raw_href)
        if "/CALL/Image/" in href:
            continue

        absolute_url = urljoin(base_url, href)
        parsed = urlparse(absolute_url)
        clean_url = urlunparse(parsed._replace(query="", fragment=""))

        if clean_url not in seen:
            seen.add(clean_url)
            links.append(clean_url)

    return links


def collect_product_targets(
    category_url: str,
    context: ssl.SSLContext,
    timeout: int,
    retries: int,
    page_delay: float,
    page_limit: Optional[int],
    keep_duplicates: bool,
) -> Tuple[List[ProductTarget], int, int]:
    first_source = fetch_html(category_page_url(category_url, 1), context, timeout, retries, page_delay)
    max_page = extract_max_page(first_source)
    if page_limit:
        max_page = min(max_page, page_limit)

    occurrences: List[Tuple[str, int, int]] = []
    pages_by_url: Dict[str, Set[int]] = {}
    first_seen_by_url: Dict[str, int] = {}

    for page in range(1, max_page + 1):
        source = first_source if page == 1 else fetch_html(
            category_page_url(category_url, page), context, timeout, retries, page_delay
        )
        links = extract_product_links(source, category_url)
        print(f"Kategori sayfasi {page}/{max_page}: {len(links)} urun linki")

        for position, url in enumerate(links, start=1):
            occurrences.append((url, page, position))
            pages_by_url.setdefault(url, set()).add(page)
            first_seen_by_url.setdefault(url, len(first_seen_by_url) + 1)

    if keep_duplicates:
        targets = [
            ProductTarget(url=url, pages=(page,), first_seen=index)
            for index, (url, page, _position) in enumerate(occurrences, start=1)
        ]
    else:
        targets = [
            ProductTarget(
                url=url,
                pages=tuple(sorted(pages)),
                first_seen=first_seen_by_url[url],
            )
            for url, pages in pages_by_url.items()
        ]
        targets.sort(key=lambda item: item.first_seen)

    return targets, len(occurrences), max_page


def extract_title(source: str) -> str:
    match = TITLE_META_RE.search(source) or TITLE_RE.search(source)
    return clean_title(match.group(1)) if match else ""


def extract_barcode(source: str, text: str) -> str:
    match = BARCODE_RE.search(text)
    if match:
        return match.group(1)

    decoded = decode_page_text(source)
    match = EAN_RE.search(decoded)
    return match.group(1) if match else ""


def extract_ingredients(text: str) -> str:
    candidates: List[str] = []

    for match in INGREDIENT_LABEL_RE.finditer(text):
        tail = match.group(1)
        stop = STOP_RE.search(tail)
        candidate = tail[: stop.start()] if stop else tail
        candidate = compact_text(candidate)
        if candidate:
            candidates.append(candidate)

    if not candidates:
        return ""

    def score(candidate: str) -> Tuple[int, int]:
        ingredient_like = int("," in candidate or ";" in candidate or "Aqua" in candidate)
        return ingredient_like, len(candidate)

    return max(candidates, key=score)


def walk_json(value: object) -> Iterable[object]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def type_matches(value: object, expected_type: str) -> bool:
    if isinstance(value, str):
        return value.lower() == expected_type.lower()
    if isinstance(value, list):
        return any(type_matches(item, expected_type) for item in value)
    return False


def normalize_heading(value: str) -> str:
    value = compact_text(value).casefold().replace("\u0307", "").replace("ı", "i")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return value


def normalize_product_details(value: str) -> str:
    text = decode_page_text(value)
    text = re.sub(r"\s*•\s*", "\n• ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    text = re.sub(r"(?<!\n)(Kullanım Önerileri|Uyarılar)\s*(?=\n|$)", r"\n\n\1", text, flags=re.IGNORECASE)
    return compact_multiline(text)


def extract_product_details(source: str) -> str:
    for match in JSON_LD_RE.finditer(source):
        raw_json = html.unescape(match.group(1)).strip()
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            continue

        for item in walk_json(data):
            if not isinstance(item, dict):
                continue
            if not type_matches(item.get("@type"), "Product"):
                continue
            description = item.get("description")
            if isinstance(description, str) and description.strip():
                return normalize_product_details(description)

    return ""


def detail_section_key(line: str) -> Optional[str]:
    normalized = normalize_heading(line)

    if normalized in {"urun ozellikleri", "ozellikler"}:
        return "product_features"
    if normalized in {"kullanim", "kullanim onerileri"}:
        return "usage_recommendations"
    if normalized in {"uyarilar", "uyari"}:
        return "warnings"
    if "kimler icin uygundur" in normalized:
        return "suitable_for"
    if "hangi sac tipleri icin uygundur" in normalized:
        return "suitable_hair_types"
    if "etken maddeleri nelerdir" in normalized:
        return "active_ingredients"
    if normalized.startswith("urun icerigi"):
        return "ingredients"
    return None


def split_product_details(details: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {
        "product_features": [],
        "usage_recommendations": [],
        "warnings": [],
        "suitable_for": [],
        "suitable_hair_types": [],
        "active_ingredients": [],
        "ingredients": [],
        "preamble": [],
    }
    current_key = "preamble"

    for raw_line in details.splitlines():
        line = compact_detail_line(raw_line)
        if not line:
            continue

        inline_heading = re.match(r"^(Ürün İçeriği|Urun Icerigi|Uyarılar|Uyarilar|Kullanım Önerileri|Kullanim Onerileri)\s*[:：]\s*(.*)$", line, re.IGNORECASE)
        if inline_heading:
            current_key = detail_section_key(inline_heading.group(1)) or current_key
            rest = compact_detail_line(inline_heading.group(2))
            if rest:
                sections.setdefault(current_key, []).append(rest)
            continue

        heading_key = detail_section_key(line)
        if heading_key:
            current_key = heading_key
            continue

        sections.setdefault(current_key, []).append(line)

    if not sections["product_features"] and sections["preamble"]:
        sections["product_features"] = sections["preamble"]

    if (
        len(sections["product_features"]) > 1
        and not sections["product_features"][0].startswith("•")
        and any(line.startswith("•") for line in sections["product_features"][1:])
    ):
        sections["product_features"] = sections["product_features"][1:]

    return {
        key: compact_multiline("\n".join(value))
        for key, value in sections.items()
        if key != "preamble"
    }


def extract_image_urls(source: str, product_id: str) -> List[str]:
    decoded = decode_escaped_source(source)
    urls: List[str] = []
    seen: Set[str] = set()

    for raw_url in IMAGE_URL_RE.findall(decoded):
        url = raw_url.strip()
        path_name = Path(urlparse(url).path).name
        is_product_image = f"/CALL/Image/getImage/{product_id}-" in url
        is_rich_content = product_id in path_name and "richcontent" in path_name.lower()

        if product_id and not (is_product_image or is_rich_content):
            continue

        if url not in seen:
            seen.add(url)
            urls.append(url)

    return urls


def safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return value[:160] or "image"


def download_product_images(
    image_urls: Sequence[str],
    product_id: str,
    image_root: Path,
    context: ssl.SSLContext,
    timeout: int,
    retries: int,
    image_delay: float,
) -> List[str]:
    if not image_urls:
        return []

    image_root.mkdir(parents=True, exist_ok=True)
    saved_files: List[str] = []

    for index, image_url in enumerate(image_urls, start=1):
        basename = safe_filename(Path(urlparse(image_url).path).name)
        product_prefix = safe_filename(product_id or "unknown")
        output_path = image_root / f"{product_prefix}_{index:02d}_{basename}"

        if not output_path.exists():
            data = fetch_bytes(image_url, context, timeout, retries, image_delay)
            output_path.write_bytes(data)

        saved_files.append(str(output_path))

    return saved_files


def parse_product(
    target: ProductTarget,
    category_url: str,
    context: ssl.SSLContext,
    timeout: int,
    retries: int,
    product_delay: float,
    download_images: bool,
    image_root: Optional[Path],
    image_delay: float,
    max_images_per_product: int,
) -> Dict[str, str]:
    source = fetch_html(target.url, context, timeout, retries, product_delay)
    text = decode_page_text(source)
    ingredients = extract_ingredients(text)
    product_details = extract_product_details(source)
    product_detail_sections = split_product_details(product_details)
    product_id = extract_product_id(target.url)
    image_urls = extract_image_urls(source, product_id)
    image_urls = image_urls[:1]

    image_files: List[str] = []
    if download_images and image_root:
        image_files = download_product_images(
            image_urls=image_urls,
            product_id=product_id,
            image_root=image_root,
            context=context,
            timeout=timeout,
            retries=retries,
            image_delay=image_delay,
        )

    return {
        "source_category": category_url,
        "category_pages": ",".join(str(page) for page in target.pages),
        "product_id": product_id,
        "title": extract_title(source),
        "barcode": extract_barcode(source, text),
        "ingredients_found": "yes" if ingredients else "no",
        "ingredients": ingredients,
        "product_details": product_details,
        "product_features": product_detail_sections.get("product_features", ""),
        "usage_recommendations": product_detail_sections.get("usage_recommendations", ""),
        "warnings": product_detail_sections.get("warnings", ""),
        "suitable_for": product_detail_sections.get("suitable_for", ""),
        "suitable_hair_types": product_detail_sections.get("suitable_hair_types", ""),
        "active_ingredients": product_detail_sections.get("active_ingredients", ""),
        "image_count": str(len(image_urls)),
        "image_urls": " | ".join(image_urls),
        "image_files": " | ".join(image_files),
        "url": target.url,
    }


def write_csv(rows: Sequence[Dict[str, str]], output_path: str) -> None:
    fieldnames = [
        "source_category",
        "category_pages",
        "product_id",
        "title",
        "barcode",
        "ingredients_found",
        "ingredients",
        "product_details",
        "product_features",
        "usage_recommendations",
        "warnings",
        "suitable_for",
        "suitable_hair_types",
        "active_ingredients",
        "image_count",
        "image_urls",
        "image_files",
        "url",
    ]
    with open(output_path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gratis kategori sayfasindaki urunlerin barkod ve icerik bilgilerini CSV'ye aktarir."
    )
    parser.add_argument("--category-url", default=DEFAULT_CATEGORY_URL)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4, help="Ayni anda kac urun sayfasi indirilsin.")
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--page-delay", type=float, default=0.25)
    parser.add_argument("--product-delay", type=float, default=0.15)
    parser.add_argument("--image-delay", type=float, default=0.05)
    parser.add_argument("--limit", type=int, default=0, help="Test icin ilk N urunu isle.")
    parser.add_argument("--pages", type=int, default=0, help="Test icin ilk N kategori sayfasini tara.")
    parser.add_argument("--keep-duplicates", action="store_true", help="Ayni urun birden cok sayfada cikarsa tekrar yaz.")
    parser.add_argument("--download-images", action="store_true", help="Urun gorsellerini de indir.")
    parser.add_argument("--image-dir", default="", help="Gorsellerin kaydedilecegi klasor.")
    parser.add_argument("--max-images-per-product", type=int, default=1, help="Web uygulamasinda yalnizca ana gorsel kullanilir.")
    parser.add_argument("--insecure", action="store_true", help="Yerel sertifika hatasi varsa HTTPS dogrulamasini kapat.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    context = build_ssl_context(args.insecure)

    try:
        targets, occurrence_count, max_page = collect_product_targets(
            category_url=args.category_url,
            context=context,
            timeout=args.timeout,
            retries=args.retries,
            page_delay=args.page_delay,
            page_limit=args.pages or None,
            keep_duplicates=args.keep_duplicates,
        )
    except ssl.SSLError as exc:
        print(f"SSL hatasi: {exc}", file=sys.stderr)
        print("Bu makinede sertifika sorunu varsa komutu --insecure ile tekrar deneyin.", file=sys.stderr)
        return 2

    if args.limit:
        targets = targets[: args.limit]

    print(
        f"Toplam kart: {occurrence_count}, islenecek urun: {len(targets)}, "
        f"kategori sayfasi: {max_page}"
    )

    rows: List[Dict[str, str]] = []
    errors: List[Tuple[str, str]] = []
    image_root: Optional[Path] = None

    if args.download_images:
        image_root = Path(args.image_dir) if args.image_dir else Path(args.output).resolve().parent / "gratis_product_images"
        image_root.mkdir(parents=True, exist_ok=True)
        print(f"Gorseller indirilecek klasor: {image_root}")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {
            executor.submit(
                parse_product,
                target,
                args.category_url,
                context,
                args.timeout,
                args.retries,
                args.product_delay,
                args.download_images,
                image_root,
                args.image_delay,
                args.max_images_per_product,
            ): target
            for target in targets
        }

        for index, future in enumerate(as_completed(future_map), start=1):
            target = future_map[future]
            try:
                rows.append(future.result())
                print(f"[{index}/{len(targets)}] OK {target.url}")
            except Exception as exc:  # Keep the batch running and report failed products.
                errors.append((target.url, str(exc)))
                print(f"[{index}/{len(targets)}] HATA {target.url}: {exc}", file=sys.stderr)

    order_by_url = {target.url: index for index, target in enumerate(targets)}
    rows.sort(key=lambda row: order_by_url.get(row["url"], 10**9))
    write_csv(rows, args.output)

    found_count = sum(1 for row in rows if row["ingredients_found"] == "yes")
    print(f"CSV yazildi: {args.output}")
    print(f"Icerik bulunan urun: {found_count}/{len(rows)}")

    if errors:
        print(f"Hata alinan urun: {len(errors)}", file=sys.stderr)
        for url, message in errors[:10]:
            print(f"- {url}: {message}", file=sys.stderr)

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
