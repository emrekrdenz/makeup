#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import html
import math
import re
import sys
import time
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse


APP_ROOT = Path(__file__).resolve().parents[1]
VENDOR = APP_ROOT / "vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

try:
    from curl_cffi import requests
except Exception as exc:  # pragma: no cover - surfaced in app startup.
    requests = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

PRODUCT_HREF_RE = re.compile(r'href="([^"]*/p/BP_\d+[^"]*)"', re.IGNORECASE)
PRODUCT_ID_RE = re.compile(r"/p/BP_(\d+)(?:\D|$)", re.IGNORECASE)
TOTAL_PAGES_RE = re.compile(r'"totalPages"\s*:\s*(\d+)', re.IGNORECASE)
PAGE_SIZE_RE = re.compile(r'"pageSize"\s*:\s*(\d+)', re.IGNORECASE)
TOTAL_RESULTS_RE = re.compile(r'"totalResults"\s*:\s*(\d+)', re.IGNORECASE)
TITLE_META_RE = re.compile(
    r'<meta\s+(?:property|name)="og:title"\s+content="([^"]+)"',
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
GTIN_RE = re.compile(
    r'class="videoly-product-gtin"\s*>\s*([0-9]{8,14})\s*<',
    re.IGNORECASE,
)
EAN_RE = re.compile(r'"ean"\s*:\s*"([0-9]{8,14})"', re.IGNORECASE)
INGREDIENT_RE = re.compile(
    r"(?:Ürün\s*İçerik\s*Bilgisi|Ürün\s*İçeriği|İçerik\s*Maddeleri|İçindekiler|Ingredients|INCI)"
    r"\s*</strong>\s*<p[^>]*>(.*?)</p>",
    re.IGNORECASE | re.DOTALL,
)
INGREDIENT_TEXT_RE = re.compile(
    r"(?:Ürün\s*İçerik\s*Bilgisi|Ürün\s*İçeriği|İçerik\s*Maddeleri|İçindekiler|Ingredients|INCI)\s*[:：]?\s*(.*)",
    re.IGNORECASE | re.DOTALL,
)
STOP_RE = re.compile(
    r"\n\s*\n|(?:İmalatçı Bilgisi|İthalatçı Bilgisi|Uygunluk Bilgisi|"
    r"Uyarı Ve Güvenlik Bilgisi|Uyarı ve Güvenlik Bilgisi|Menşei Bilgisi|"
    r"Kullanım Talimatı|Ürün Açıklaması|Kullanımlar|İptal İade Koşulları)\s*[:：]?",
    re.IGNORECASE,
)
MEDIA_URL_RE = re.compile(
    r"https://media\.watsons\.com\.tr/[^\"'<>\s\\]+?\.(?:jpg|jpeg|png|webp)",
    re.IGNORECASE,
)
REL_MEDIA_RE = re.compile(
    r'(?:"url"\s*:\s*")(/medias/[^"]+?\.(?:jpg|jpeg|png|webp))"',
    re.IGNORECASE,
)
PRODUCT_DESCRIPTION_RE = re.compile(
    r"<e2-product-nested-description\b[^>]*>(.*?)</e2-product-nested-description>",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class ProductTarget:
    url: str
    pages: Tuple[int, ...]
    first_seen: int


def ensure_client() -> None:
    if requests is None:
        raise RuntimeError(
            "Watsons icin curl_cffi gerekli. Komut: python3 -m pip install --target "
            f"{APP_ROOT / 'vendor'} curl_cffi"
        ) from IMPORT_ERROR


def fetch_html(url: str, timeout: int, retries: int, delay: float = 0.0) -> str:
    ensure_client()
    last_error: Optional[BaseException] = None

    for attempt in range(retries + 1):
        if delay > 0:
            time.sleep(delay + random.uniform(0, delay / 2))
        try:
            response = requests.get(
                url,
                impersonate="chrome124",
                timeout=timeout,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = RuntimeError(f"HTTP {response.status_code}")
                time.sleep(1.5 * (attempt + 1))
                continue
            response.raise_for_status()
            return response.text
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def fetch_bytes(url: str, timeout: int, retries: int, delay: float = 0.0) -> bytes:
    ensure_client()
    last_error: Optional[BaseException] = None

    for attempt in range(retries + 1):
        if delay > 0:
            time.sleep(delay + random.uniform(0, delay / 2))
        try:
            response = requests.get(
                url,
                impersonate="chrome124",
                timeout=timeout,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    "Referer": "https://www.watsons.com.tr/",
                },
            )
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = RuntimeError(f"HTTP {response.status_code}")
                time.sleep(1.5 * (attempt + 1))
                continue
            response.raise_for_status()
            return response.content
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"Could not download {url}: {last_error}")


def decode_escaped_source(source: str) -> str:
    source = html.unescape(source)
    replacements = {
        r"\u002f": "/",
        r"\u003c": "<",
        r"\u003e": ">",
        r"\u0026": "&",
        r"\u0027": "'",
        r"\/": "/",
        r"\"": '"',
        r"\n": "\n",
        r"\r": "\r",
        r"\t": "\t",
    }
    for old, new in replacements.items():
        source = source.replace(old, new)
    return source


def html_to_text(source: str) -> str:
    source = decode_escaped_source(source)
    source = re.sub(r"<br\s*/?>", "\n", source, flags=re.IGNORECASE)
    source = re.sub(r"</p\s*>", "\n", source, flags=re.IGNORECASE)
    source = re.sub(r"<[^>]+>", " ", source)
    source = html.unescape(source)
    source = re.sub(r"[ \t\f\v]+", " ", source)
    source = re.sub(r" *\n *", "\n", source)
    source = re.sub(r"\n{3,}", "\n\n", source)
    return source.strip()


def compact_text(value: str) -> str:
    value = html_to_text(value)
    value = value.strip(" \n\t\"'.,;:-")
    value = re.sub(r"\s+", " ", value)
    value = value.replace(" ,", ",").replace(" .", ".")
    return value.strip()


def compact_multiline(value: str) -> str:
    lines = []
    for raw_line in value.splitlines():
        line = raw_line.strip(" \n\t\"'")
        line = re.sub(r"\s+", " ", line)
        line = line.replace(" ,", ",").replace(" .", ".")
        if line:
            lines.append(line)
    return "\n".join(lines)


def clean_title(value: str) -> str:
    value = compact_text(value)
    value = re.sub(r"\s+\d+\s*\|\s*Watsons\s*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*\|\s*Watsons\s*$", "", value, flags=re.IGNORECASE)
    return value


def extract_product_id(url: str) -> str:
    match = PRODUCT_ID_RE.search(url)
    return match.group(1) if match else ""


def category_page_url(category_url: str, page_index: int) -> str:
    parsed = urlparse(category_url)
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key.lower() != "currentpage"]
    if page_index > 0:
        query.append(("currentPage", str(page_index)))
    return urlunparse(parsed._replace(query=urlencode(query)))


def extract_max_page(source: str, link_count: int) -> int:
    match = TOTAL_PAGES_RE.search(source)
    if match:
        return max(1, int(match.group(1)))

    total_match = TOTAL_RESULTS_RE.search(source)
    size_match = PAGE_SIZE_RE.search(source)
    if total_match and size_match:
        page_size = max(1, int(size_match.group(1)))
        return max(1, math.ceil(int(total_match.group(1)) / page_size))

    return 1 if link_count else 0


def extract_product_links(source: str, base_url: str) -> List[str]:
    links: List[str] = []
    seen: Set[str] = set()
    decoded = decode_escaped_source(source)

    for raw_href in PRODUCT_HREF_RE.findall(decoded):
        absolute_url = urljoin(base_url, html.unescape(raw_href))
        parsed = urlparse(absolute_url)
        clean_url = urlunparse(parsed._replace(query="", fragment=""))
        if clean_url not in seen:
            seen.add(clean_url)
            links.append(clean_url)

    return links


def collect_product_targets(
    category_url: str,
    timeout: int,
    retries: int,
    page_delay: float,
    page_limit: Optional[int],
    keep_duplicates: bool,
    log=None,
) -> Tuple[List[ProductTarget], int, int]:
    first_source = fetch_html(category_page_url(category_url, 0), timeout, retries, page_delay)
    first_links = extract_product_links(first_source, category_url)
    max_page = extract_max_page(first_source, len(first_links))
    if page_limit:
        max_page = min(max_page, page_limit)

    occurrences: List[Tuple[str, int, int]] = []
    pages_by_url: Dict[str, Set[int]] = {}
    first_seen_by_url: Dict[str, int] = {}

    for page_index in range(max_page):
        if page_index == 0:
            links = first_links
        else:
            source = fetch_html(category_page_url(category_url, page_index), timeout, retries, page_delay)
            links = extract_product_links(source, category_url)

        display_page = page_index + 1
        if log:
            log(f"{category_url} | sayfa {display_page}/{max_page}: {len(links)} urun linki")

        for position, url in enumerate(links, start=1):
            occurrences.append((url, display_page, position))
            pages_by_url.setdefault(url, set()).add(display_page)
            first_seen_by_url.setdefault(url, len(first_seen_by_url) + 1)

    if keep_duplicates:
        targets = [
            ProductTarget(url=url, pages=(page,), first_seen=index)
            for index, (url, page, _position) in enumerate(occurrences, start=1)
        ]
    else:
        targets = [
            ProductTarget(url=url, pages=tuple(sorted(pages)), first_seen=first_seen_by_url[url])
            for url, pages in pages_by_url.items()
        ]
        targets.sort(key=lambda item: item.first_seen)

    return targets, len(occurrences), max_page


def extract_title(source: str) -> str:
    match = TITLE_META_RE.search(source) or TITLE_RE.search(source)
    return clean_title(match.group(1)) if match else ""


def extract_barcode(source: str) -> str:
    decoded = decode_escaped_source(source)
    match = GTIN_RE.search(decoded)
    if match:
        return match.group(1)

    product_id = re.search(r"productDetails_BP_(\d+)", decoded)
    if product_id:
        idx = decoded.find(f'"BP_{product_id.group(1)}"')
        if idx != -1:
            local = decoded[idx : idx + 50000]
            match = EAN_RE.search(local)
            if match:
                return match.group(1)

    match = EAN_RE.search(decoded)
    return match.group(1) if match else ""


def extract_ingredients(source: str) -> str:
    decoded = decode_escaped_source(source)
    candidates: List[str] = []

    for match in INGREDIENT_RE.finditer(decoded):
        candidate = compact_text(match.group(1))
        if candidate:
            candidates.append(candidate)

    if not candidates:
        text = html_to_text(decoded)
        for match in INGREDIENT_TEXT_RE.finditer(text):
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


def normalize_product_description(value: str) -> str:
    value = decode_escaped_source(value)
    value = re.sub(r"<li\b[^>]*>", "\n• ", value, flags=re.IGNORECASE)
    value = re.sub(r"</li\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</h[1-6]\s*>", "\n", value, flags=re.IGNORECASE)
    value = html_to_text(value)
    value = re.sub(r"\n\s*\n\s*", "\n\n", value)
    return compact_multiline(value)


def extract_product_description(source: str) -> str:
    decoded = decode_escaped_source(source)
    match = PRODUCT_DESCRIPTION_RE.search(decoded)
    if match:
        return normalize_product_description(match.group(1))

    text = html_to_text(decoded)
    match = re.search(
        r"Ürün Açıklaması\s+Ürün Açıklaması\s+(.*?)(?:\s+Kullanımlar\s+|\s+İçerik Maddeleri\s+|\s+İptal İade Koşulları\s+|\s+Ödeme Yöntemi\s+|\s+Teslimat Seçenekleri\s+|\s+Ürün Detay Bilgisi\s+)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return compact_multiline(match.group(1))

    return ""


def extract_image_urls(source: str, product_id: str) -> List[str]:
    decoded = decode_escaped_source(source)
    urls: List[str] = []
    seen: Set[str] = set()

    for raw_url in MEDIA_URL_RE.findall(decoded):
        if product_id and product_id not in raw_url:
            continue
        if product_id and "prd-" not in raw_url:
            continue
        if raw_url not in seen:
            seen.add(raw_url)
            urls.append(raw_url)

    for raw_url in REL_MEDIA_RE.findall(decoded):
        url = urljoin("https://media.watsons.com.tr", raw_url)
        if product_id and product_id not in url:
            continue
        if product_id and "prd-" not in url:
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)

    high_res = [url for url in urls if "1200x1200" in url]
    return high_res or urls


def safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return value[:160] or "image"


def download_product_images(
    image_urls: Sequence[str],
    product_id: str,
    image_root: Path,
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
            data = fetch_bytes(image_url, timeout, retries, image_delay)
            output_path.write_bytes(data)

        saved_files.append(str(output_path))

    return saved_files


def parse_product(
    target: ProductTarget,
    category_url: str,
    timeout: int,
    retries: int,
    product_delay: float,
    download_images: bool,
    image_root: Optional[Path],
    image_delay: float,
    max_images_per_product: int,
) -> Dict[str, str]:
    source = fetch_html(target.url, timeout, retries, product_delay)
    product_id = extract_product_id(target.url)
    image_urls = extract_image_urls(source, product_id)
    image_urls = image_urls[:1]

    image_files: List[str] = []
    if download_images and image_root:
        image_files = download_product_images(
            image_urls=image_urls,
            product_id=product_id,
            image_root=image_root,
            timeout=timeout,
            retries=retries,
            image_delay=image_delay,
        )

    ingredients = extract_ingredients(source)
    product_description = extract_product_description(source)

    return {
        "source_category": category_url,
        "category_pages": ",".join(str(page) for page in target.pages),
        "product_id": product_id,
        "title": extract_title(source),
        "barcode": extract_barcode(source),
        "ingredients_found": "yes" if ingredients else "no",
        "ingredients": ingredients,
        "product_details": product_description,
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
        "image_count",
        "image_urls",
        "image_files",
        "url",
    ]
    with open(output_path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
