from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
import re
import sys
import threading
import time
from collections import defaultdict
from math import ceil
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

FONT_SOURCE_URL = "https://api.fontsource.org/v1/fonts?type=google"
FONT_SOURCE_OTHER_URL = "https://api.fontsource.org/v1/fonts?type=other"
FONTSOURCE_CDN_CSS = "https://cdn.jsdelivr.net/fontsource/css"
GOOGLE_FONTS_TREE_URL = "https://api.github.com/repos/google/fonts/git/trees/main?recursive=1"
DAFONT_ROOT = "https://www.dafont.com"
DAFONT_THEMES = f"{DAFONT_ROOT}/themes.php"
CACHE_SECONDS = 12 * 60 * 60
DAFONT_DELAY_SECONDS = 0.65
FONT_LIBRARY_ROOT = "https://fontlibrary.org"
FONT_SHARE_ROOT = "https://www.fontshare.com"
SUPPLEMENTAL_DELAY_SECONDS = 0.45
SUPPLEMENTAL_LIMIT = 100

LICENSES = {
    "ofl": "https://openfontlicense.org/",
    "apache": "https://www.apache.org/licenses/LICENSE-2.0",
    "ufl": "https://ubuntu.com/legal/font-licence",
}
LICENSE_LABELS = {
    "ofl": "SIL Open Font License 1.1",
    "apache": "Apache License 2.0",
    "ufl": "Ubuntu Font Licence 1.0",
}
LICENSE_CATEGORIES = (
    "No Credit Required",
    "Credit Required Commercial",
    "Credit Required must Modify",
    "Credit Required Share Alike",
)


def license_classification(label: str = "", license_key: str | None = None) -> tuple[list[str], str]:
    """Classify only known licenses that permit free commercial use."""
    key = (license_key or "").casefold()
    normalized = re.sub(r"[^a-z0-9]+", "", label.casefold())
    categories: list[str] = []
    public_domain = any(token in normalized for token in ("publicdomain", "cc0", "creativecommonszer"))
    noncommercial = any(token in normalized for token in ("noncommercial", "bync", "ncsa", "ncnd"))
    if noncommercial:
        return [], "Excluded: this license restricts commercial use."

    is_ofl = key == "ofl" or "ofl" in normalized or "openfontlicense" in normalized
    is_itf_ffl = "itffreefontlicense" in normalized or normalized in {"itfffl", "ffl"}
    cc_by_sa = "ccbysa" in normalized or "creativecommonsattributionsharealike" in normalized
    cc_by = ("ccby" in normalized or "creativecommonsattribution" in normalized) and not cc_by_sa
    is_apache = key == "apache" or "apache" in normalized
    is_mit = normalized in {"mit", "mitlicense", "mitx11", "mitx11license"}
    is_gpl = "gpl" in normalized or normalized.startswith("gnugeneralpubliclicense")
    is_ufl = key == "ufl" or "ufl" in normalized or "ubuntufontlicence" in normalized
    other_permissive = any(token in normalized for token in ("bitstreamvera", "arphic", "mplus", "gust", "aladdin"))

    if public_domain:
        return ["No Credit Required"], "Public domain or CC0 terms; commercial use is allowed without attribution."
    if is_ofl or is_itf_ffl:
        categories.append("No Credit Required")
        if is_ofl:
            categories.append("Credit Required Share Alike")
        summary = "No creator acknowledgment is required for ordinary use. Commercial use is allowed; redistribution requires notices, and modified font software must remain under OFL."
        if is_itf_ffl:
            summary = "Free for personal and commercial use; creator credit is optional. Follow the family license for redistribution."
        return categories, summary
    if cc_by or cc_by_sa:
        categories.extend(("Credit Required Commercial", "Credit Required must Modify"))
        if cc_by_sa:
            categories.append("Credit Required Share Alike")
        summary = "Commercial use is allowed with attribution; identify changes when modifying."
        if cc_by_sa:
            summary += " Adaptations must use the same license."
    elif is_apache:
        categories.extend(("Credit Required Commercial", "Credit Required must Modify"))
        summary = "Commercial use is allowed; retain required notices and document changes in modified files when redistributing."
    elif is_mit:
        categories.append("Credit Required Commercial")
        summary = "Commercial use is allowed; retain the copyright and permission notices in redistributed copies."
    elif is_gpl or "sharealike" in normalized:
        categories.extend(("Credit Required Commercial", "Credit Required Share Alike"))
        summary = "Commercial use is allowed; retain required notices and distribute adaptations under the same license."
    elif is_ufl or other_permissive:
        categories.append("Credit Required Commercial")
        summary = "Commercial use is allowed under the linked permissive font license; retain required notices when redistributing."
    else:
        return [], "Excluded: the source terms do not confirm free commercial use."
    return list(dict.fromkeys(categories)), summary


def abstract_license(license_key: str | None) -> tuple[list[str], str, str, str]:
    key = (license_key or "").casefold()
    label = LICENSE_LABELS.get(key, "License details unavailable")
    url = LICENSES.get(key, "")
    categories, summary = license_classification(label, key)
    return categories, label, url, summary


def fontsource_preview(font_id: str, family: str, variable: bool = False) -> tuple[str, str]:
    """Return an isolated Fontsource stylesheet and its declared CSS family."""
    variant = ":vf" if variable else ""
    css_url = f"{FONTSOURCE_CDN_CSS}/{quote(font_id)}{variant}@latest/index.css"
    preview_family = family
    if variable and not preview_family.casefold().endswith(" variable"):
        preview_family = f"{preview_family} Variable"
    return css_url, preview_family


def _plain_text(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", markup))).strip()


def _font_library_rows(page: str) -> list[dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for href, raw_name in re.findall(r'<a\b[^>]*href=["\'](/en/font/[^"\']+)["\'][^>]*>(.*?)</a>', page, re.I | re.S):
        name = _plain_text(raw_name)
        if name and len(name) <= 100:
            rows[href] = {"name": name, "url": FONT_LIBRARY_ROOT + href}
    return list(rows.values())


def _font_library_record(page: str, row: dict[str, str]) -> dict[str, Any] | None:
    links = [(href, _plain_text(label)) for href, label in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', page, re.I | re.S)]
    license_link = next(((href, label) for href, label in links if re.search(r"OFL|CC[- ]?0|CC[- ]?BY|public domain|apache|freeware|MIT|GPL|license", label, re.I)), (row["url"], "License details"))
    license_label = license_link[1] or "License details on Font Library"
    license_categories, license_summary = license_classification(license_label)
    if not license_categories:
        return None
    image = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page, re.I)
    slug = row["url"].rsplit("/", 1)[-1]
    license_url = license_link[0] if license_link[0].startswith("http") else FONT_LIBRARY_ROOT + license_link[0]
    return {
        "id": f"fontlibrary:{slug}", "family": row["name"], "category": "Font Library",
        "weights": [], "variable": False, "source": "Font Library", "source_url": row["url"],
        "license_category": license_categories[0], "license_categories": license_categories,
        "license_label": license_label, "license_summary": license_summary,
        "license_url": license_url, "preview_css_url": f"{FONT_LIBRARY_ROOT}/face/{quote(slug)}",
        "preview_url": image.group(1) if image else "",
    }


def _fontshare_rows(page: str) -> list[dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for href, raw_name in re.findall(r'<a\b[^>]*href=["\'](/fonts/[a-z0-9-]+)["\'][^>]*>(.*?)</a>', page, re.I | re.S):
        name = _plain_text(raw_name)
        if name and len(name) <= 80:
            rows[href] = {"name": name, "url": FONT_SHARE_ROOT + href}
    return list(rows.values())[:SUPPLEMENTAL_LIMIT]


def _fontshare_record(page: str, row: dict[str, str]) -> dict[str, Any] | None:
    plain = _plain_text(page)
    title = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.I | re.S)
    family = _plain_text(title.group(1)) if title else row["name"]
    license_match = re.search(r"(?:Closed Source|Open Source|License).{0,180}?(?:ITF Free Font License|SIL Open Font License|OFL)", plain, re.I)
    license_label = license_match.group(0) if license_match else ""
    license_categories, license_summary = license_classification(license_label)
    if not license_categories:
        return None
    image = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page, re.I)
    slug = row["url"].rsplit("/", 1)[-1]
    return {
        "id": f"fontshare:{slug}", "family": family, "category": "Fontshare", "weights": [],
        "variable": False, "source": "Fontshare", "source_url": row["url"],
        "license_category": license_categories[0], "license_categories": license_categories,
        "license_label": license_label, "license_summary": license_summary, "license_url": row["url"],
        "preview_css_url": f"https://api.fontshare.com/v2/css?f[]={quote(slug)}@400&display=swap",
        "preview_url": image.group(1) if image else "",
    }


async def _scrape_font_library(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, str]] = {}
    # Sample by license facet so the catalog contains real examples across
    # the term groups instead of only whichever licenses sort to the front.
    license_facets = (
        "CC-0", "Public Domain (not a license)", "CC-BY", "CC-BY-SA",
        "OFL (SIL Open Font License)", "MIT (X11) License", "Apache 2.0",
        "GNU General Public License",
    )
    for license_label in license_facets:
        response = await client.get(
            f"{FONT_LIBRARY_ROOT}/en/search",
            params={"license": license_label, "order": ""},
        )
        response.raise_for_status()
        for row in _font_library_rows(response.text)[:12]:
            rows[row["url"]] = row
        await asyncio.sleep(SUPPLEMENTAL_DELAY_SECONDS)
    semaphore = asyncio.Semaphore(4)

    async def fetch_record(row: dict[str, str]) -> dict[str, Any] | None:
        async with semaphore:
            try:
                response = await client.get(row["url"])
                response.raise_for_status()
                record = _font_library_record(response.text, row)
                await asyncio.sleep(SUPPLEMENTAL_DELAY_SECONDS)
                return record
            except httpx.HTTPError:
                return None

    results = await asyncio.gather(*(fetch_record(row) for row in list(rows.values())[:SUPPLEMENTAL_LIMIT]))
    return [record for record in results if record]


async def _scrape_fontshare(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await client.get(FONT_SHARE_ROOT + "/")
    response.raise_for_status()
    rows = _fontshare_rows(response.text)
    semaphore = asyncio.Semaphore(4)

    async def fetch_record(row: dict[str, str]) -> dict[str, Any] | None:
        async with semaphore:
            try:
                response = await client.get(row["url"])
                response.raise_for_status()
                record = _fontshare_record(response.text, row)
                await asyncio.sleep(SUPPLEMENTAL_DELAY_SECONDS)
                return record
            except httpx.HTTPError:
                return None

    results = await asyncio.gather(*(fetch_record(row) for row in rows))
    return [record for record in results if record]


async def _scrape_supplemental() -> None:
    _supplemental_state.update(loading=True, done=False, fonts=[], error="")
    _supplemental_state["sources"] = {"Font Library": "Loading", "Fontshare": "Loading", "Fontesk": "Link only (site terms prohibit scraping)"}
    headers = {"User-Agent": "FontFinder-local-app/0.3 (polite public catalog reader)"}
    async with httpx.AsyncClient(timeout=25, headers=headers, follow_redirects=True) as client:
        for name, scraper in (("Font Library", _scrape_font_library), ("Fontshare", _scrape_fontshare)):
            try:
                records = await scraper(client)
                _supplemental_state["fonts"].extend(records)
                _supplemental_state["sources"][name] = f"Loaded {len(records):,} fonts"
            except httpx.HTTPError as exc:
                _supplemental_state["sources"][name] = "Unavailable"
                _supplemental_state["error"] = f"{name}: {exc}"
            if name == "Font Library":
                await asyncio.sleep(SUPPLEMENTAL_DELAY_SECONDS)
    _supplemental_state.update(loading=False, done=True)


app = FastAPI(title="FontFinder API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_catalog_cache: dict[str, Any] = {"expires": 0.0, "fonts": []}
_refresh_lock = asyncio.Lock()
_dafont_state: dict[str, Any] = {"loading": False, "done": False, "fonts": [], "error": ""}
_dafont_task: asyncio.Task | None = None
_supplemental_state: dict[str, Any] = {"loading": False, "done": False, "fonts": [], "sources": {}, "error": ""}
_supplemental_task: asyncio.Task | None = None
_local_model: Any = None
_local_model_lock = threading.Lock()


async def _fetch_json(client: httpx.AsyncClient, url: str) -> Any:
    response = await client.get(url)
    response.raise_for_status()
    return response.json()


def _license_index(tree: dict[str, Any]) -> dict[str, str]:
    index: dict[str, str] = {}
    for entry in tree.get("tree", []):
        if entry.get("type") != "tree":
            continue
        parts = entry.get("path", "").split("/")
        if len(parts) == 2 and parts[0] in LICENSES:
            index[parts[1].casefold()] = parts[0]
    return index


def _parse_theme_ids(page: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'href="theme\.php\?cat=(\d+)"', page)))


def _parse_dafont_page(page: str) -> list[dict[str, Any]]:
    rows = re.split(r'<a name="\d+"></a>', page)[1:]
    fonts: list[dict[str, Any]] = []
    for row in rows:
        link = re.search(r'href="([a-z0-9_-]+\.font)"[^>]*>\s*<strong>(.*?)</strong>', row, re.I | re.S)
        if not link:
            continue
        slug = link.group(1)
        family = html.unescape(re.sub(r"<.*?>", "", link.group(2))).strip()
        license_match = re.search(r'class="tdn help black"[^>]*>(.*?)</a>', row, re.I | re.S)
        license_label = html.unescape(re.sub(r"<.*?>", "", license_match.group(1))).strip() if license_match else "License details unavailable"
        preview = re.search(r'background-image:\s*url\(([^)]+)\)', row, re.I)
        preview_url = preview.group(1).strip(" '\"") if preview else ""
        if preview_url.startswith("/"):
            preview_url = DAFONT_ROOT + preview_url
        license_categories, license_summary = license_classification(license_label)
        if not license_categories:
            continue
        fonts.append({
            "id": f"dafont:{slug}", "family": family, "category": "DaFont",
            "weights": [], "variable": False, "source": "DaFont",
            "source_url": f"{DAFONT_ROOT}/{slug}",
            "license_category": license_categories[0],
            "license_categories": license_categories,
            "license_label": license_label,
            "license_summary": license_summary,
            "license_url": f"{DAFONT_ROOT}/faq.php#copyright",
            "preview_url": preview_url,
        })
    return fonts


async def _scrape_dafont() -> None:
    _dafont_state.update(loading=True, done=False, fonts=[], error="")
    headers = {"User-Agent": "FontFinder-local-app/0.2 (polite catalog reader)"}
    try:
        async with httpx.AsyncClient(timeout=25, headers=headers, follow_redirects=True) as client:
            response = await client.get(DAFONT_THEMES)
            response.raise_for_status()
            theme_ids = _parse_theme_ids(response.text)
            known: dict[str, dict[str, Any]] = {}
            for index, theme_id in enumerate(theme_ids):
                response = await client.get(f"{DAFONT_ROOT}/theme.php?cat={theme_id}")
                response.raise_for_status()
                for font in _parse_dafont_page(response.text):
                    known[font["id"]] = font
                _dafont_state["fonts"] = list(known.values())
                if index + 1 < len(theme_ids):
                    await asyncio.sleep(DAFONT_DELAY_SECONDS)
        _dafont_state.update(done=True, error="")
    except (httpx.HTTPError, asyncio.CancelledError) as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        _dafont_state["error"] = f"DaFont catalog refresh failed: {exc}"
    finally:
        _dafont_state["loading"] = False


async def _load_catalog() -> list[dict[str, Any]]:
    async with _refresh_lock:
        if _catalog_cache["expires"] > time.time():
            return _catalog_cache["fonts"]
        headers = {"User-Agent": "FontFinder-local-app"}
        try:
            async with httpx.AsyncClient(timeout=30, headers=headers) as client:
                font_data, tree = await asyncio.gather(
                    _fetch_json(client, FONT_SOURCE_URL),
                    _fetch_json(client, GOOGLE_FONTS_TREE_URL),
                )
                try:
                    other_data = await _fetch_json(client, FONT_SOURCE_OTHER_URL)
                except httpx.HTTPError:
                    other_data = []
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Could not load the public font catalogs: {exc}") from exc
        licenses = _license_index(tree)
        fonts: list[dict[str, Any]] = []
        for raw in font_data:
            font_id = str(raw.get("id", ""))
            license_categories, license_label, license_url, license_summary = abstract_license(licenses.get(font_id.casefold()))
            if not license_categories:
                continue
            family = raw.get("family", font_id)
            preview_css_url, preview_family = fontsource_preview(font_id, family, bool(raw.get("variable", False)))
            fonts.append({
                "id": f"google:{font_id}", "family": family,
                "category": raw.get("category", "unknown"), "weights": raw.get("weights", []),
                "variable": bool(raw.get("variable", False)), "source": "Google Fonts via Fontsource",
                "source_url": f"https://fonts.google.com/specimen/{quote(font_id)}",
                "license_category": license_categories[0], "license_categories": license_categories,
                "license_label": license_label, "license_summary": license_summary, "license_url": license_url,
                "preview_css_url": preview_css_url, "preview_family": preview_family,
                "provider_links": [{"label": "Bunny Fonts", "url": f"https://fonts.bunny.net/family/{quote(font_id)}"}],
            })
        for raw in other_data if isinstance(other_data, list) else []:
            font_id = str(raw.get("id", ""))
            if not font_id:
                continue
            source_url = f"https://fontsource.org/fonts/{quote(font_id)}"
            preview_css_url, preview_family = fontsource_preview(font_id, raw.get("family", font_id), bool(raw.get("variable", False)))
            raw_license = raw.get("license")
            if isinstance(raw_license, dict):
                license_label = str(raw_license.get("name") or raw_license.get("type") or "License details unavailable")
                license_url = str(raw_license.get("url") or raw_license.get("link") or source_url)
            elif isinstance(raw_license, str) and raw_license.strip():
                license_label = raw_license.strip()
                license_url = str(raw.get("licenseUrl") or raw.get("license_url") or source_url)
            else:
                license_label = "License details unavailable from Fontsource catalog"
                license_url = source_url
            license_categories, license_summary = license_classification(license_label)
            if not license_categories:
                continue
            fonts.append({
                "id": f"fontsource-other:{font_id}", "family": raw.get("family", font_id),
                "category": raw.get("category", "other"), "weights": raw.get("weights", []),
                "variable": bool(raw.get("variable", False)), "source": "Fontsource (other catalog)",
                "source_url": source_url, "license_category": license_categories[0],
                "license_categories": license_categories, "license_label": license_label,
                "license_summary": license_summary, "license_url": license_url,
                "preview_css_url": preview_css_url, "preview_family": preview_family,
            })
        _catalog_cache.update(expires=time.time() + CACHE_SECONDS, fonts=fonts)
        return fonts


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/fonts")
async def get_fonts(
    q: str = Query(default="", max_length=100),
    license_category: str = Query(default="", max_length=100),
) -> dict[str, Any]:
    fonts = await _load_catalog()
    normalized_query = q.casefold().strip()
    normalized_license = license_category.casefold().strip()
    results = [font for font in fonts if
        (not normalized_query or normalized_query in f"{font['family']} {font['category']} {font['source']}".casefold())
        and (not normalized_license or normalized_license in {
            category.casefold() for category in font.get("license_categories", [font["license_category"]])
        })]
    global _dafont_task
    if not _dafont_state["done"] and (_dafont_task is None or _dafont_task.done()):
        _dafont_task = asyncio.create_task(_scrape_dafont())
    global _supplemental_task
    if not _supplemental_state["done"] and (_supplemental_task is None or _supplemental_task.done()):
        _supplemental_task = asyncio.create_task(_scrape_supplemental())
    return {"count": len(results), "sources": ["Fontsource Google catalog", "Fontsource other catalog", "Google Fonts repository", "Bunny Fonts family pages", "DaFont public category pages", "Font Library", "Fontshare", "Fontesk link-out"],
            "license_categories": list(LICENSE_CATEGORIES), "fonts": results}


@app.get("/api/dafont")
async def get_dafont() -> dict[str, Any]:
    global _dafont_task
    if not _dafont_state["done"] and (_dafont_task is None or _dafont_task.done()):
        _dafont_task = asyncio.create_task(_scrape_dafont())
    return {"loading": _dafont_state["loading"], "done": _dafont_state["done"],
            "count": len(_dafont_state["fonts"]), "error": _dafont_state["error"], "fonts": _dafont_state["fonts"]}


@app.get("/api/supplemental")
async def get_supplemental() -> dict[str, Any]:
    global _supplemental_task
    if not _supplemental_state["done"] and (_supplemental_task is None or _supplemental_task.done()):
        _supplemental_task = asyncio.create_task(_scrape_supplemental())
    return {"loading": _supplemental_state["loading"], "done": _supplemental_state["done"],
            "count": len(_supplemental_state["fonts"]), "sources": _supplemental_state["sources"],
            "error": _supplemental_state["error"], "fonts": _supplemental_state["fonts"]}


class RouteRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1200)
    fonts: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)


def _parse_json_object(content: str) -> dict[str, Any] | None:
    """Extract the first valid JSON object, even if the model adds prose or fences."""
    if not isinstance(content, str):
        return None
    decoder = json.JSONDecoder()
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


_ROUTE_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "keywords": {"type": "array", "items": {"type": "string"}},
        "typographic_traits": {"type": "array", "items": {"type": "string"}},
        "moods": {"type": "array", "items": {"type": "string"}},
        "use_cases": {"type": "array", "items": {"type": "string"}},
        "avoid": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["keywords", "typographic_traits", "moods", "use_cases", "avoid"],
    "additionalProperties": False,
}

_STOP_WORDS = {
    "and", "the", "for", "with", "that", "this", "from", "into", "font", "fonts",
    "type", "style", "need", "needs", "want", "use", "using", "look", "looking",
    "please", "make", "more", "less", "very", "some", "like", "project", "brand",
}
_CATEGORY_TERMS = {
    "sans-serif": {"sans", "sans serif", "sans-serif"},
    "serif": {"serif"},
    "display": {"display", "headline", "poster"},
    "handwriting": {"handwriting", "handwritten", "script"},
    "monospace": {"monospace", "monospaced", "mono", "code"},
}


def _route_terms(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    terms: list[str] = []
    for item in values:
        if isinstance(item, str):
            normalized = " ".join(
                token for token in re.sub(r"[^a-z0-9]+", " ", item.casefold()).split()
                if token not in _STOP_WORDS
            )
            if normalized and normalized not in terms:
                terms.append(normalized[:80])
    return terms


def _infer_route_profile(model: Any, prompt: str) -> dict[str, list[str]]:
    """Use the local model to expand a brief into search facets before retrieval."""
    result = model.create_chat_completion(
        temperature=0,
        max_tokens=256,
        response_format={"type": "json_object", "schema": _ROUTE_PROFILE_SCHEMA},
        messages=[
            {"role": "system", "content": "Extract a concise font search profile from the user's project brief. Return concrete search keywords, typographic traits, moods, likely use cases, and traits to avoid. Use short atomic labels (one to three words) and include useful synonyms. Do not recommend font names or infer licensing."},
            {"role": "user", "content": prompt},
        ],
    )
    content = result["choices"][0]["message"].get("content")
    parsed = _parse_json_object(content)
    profile = {
        key: _route_terms(parsed.get(key, [])) if parsed else []
        for key in ("keywords", "typographic_traits", "moods", "use_cases", "avoid")
    }
    if not any(profile.values()):
        profile["keywords"] = _route_terms(re.findall(r"[a-z0-9]{3,}", prompt.casefold()))
    return profile


def _route_digest(prompt: str, value: str) -> bytes:
    return hashlib.blake2b(f"{prompt.casefold()}\0{value.casefold()}".encode("utf-8"), digest_size=8).digest()


def _route_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()


def _route_contains(text: str, term: str) -> bool:
    normalized_text = f" {_route_text(text)} "
    normalized_term = _route_text(term)
    return bool(normalized_term) and f" {normalized_term} " in normalized_text


def _category_matches(category: str, trait: str) -> bool:
    normalized_category = _route_text(category)
    normalized_trait = _route_text(trait)
    if normalized_category == normalized_trait:
        return True
    for category_name, terms in _CATEGORY_TERMS.items():
        if normalized_category == _route_text(category_name) and normalized_trait in {_route_text(term) for term in terms}:
            return True
    return False


def _route_relevance(font: dict[str, Any], prompt: str, profile: dict[str, list[str]]) -> int:
    family = str(font.get("family", "")).casefold()
    category = str(font.get("category", "")).casefold().replace("_", "-")
    source = str(font.get("source", "")).casefold()
    extra = " ".join(str(font.get(key, "")) for key in ("description", "tags", "style", "styles")).casefold()
    searchable = f"{family} {category} {source} {extra}"
    score = 0
    for term in _route_terms(re.findall(r"[a-z0-9]{3,}", prompt.casefold())):
        if _route_contains(family, term):
            score += 4
        elif _route_contains(searchable, term):
            score += 1
    for term in profile.get("keywords", []):
        if _route_contains(family, term):
            score += 7
        elif _route_contains(searchable, term):
            score += 3
    traits = profile.get("typographic_traits", []) + profile.get("moods", []) + profile.get("use_cases", [])
    for trait in traits:
        if _category_matches(category, trait) or _route_contains(extra, trait):
            score += 9
        elif _route_contains(family, trait):
            score += 5
        else:
            for category_name, terms in _CATEGORY_TERMS.items():
                if _category_matches(category, category_name) and _route_text(trait) in {_route_text(term) for term in terms}:
                    score += 8
                    break
    for avoided in profile.get("avoid", []):
        if _route_contains(searchable, avoided):
            score -= 8
    return score


def _retrieve_route_candidates(
    prompt: str, fonts: list[dict[str, Any]], profile: dict[str, list[str]], limit: int = 60,
) -> list[dict[str, Any]]:
    """Rank the entire catalog and diversify sources without using input indices."""
    by_source: dict[str, list[tuple[int, bytes, dict[str, Any]]]] = defaultdict(list)
    for font in fonts:
        font_id = font.get("id")
        if not isinstance(font_id, str) or not font_id:
            continue
        source = str(font.get("source") or font.get("category") or "Other")
        score = _route_relevance(font, prompt, profile)
        tie_break = _route_digest(prompt, font_id)
        by_source[source].append((score, tie_break, font))

    if not by_source:
        return []
    for ranked in by_source.values():
        ranked.sort(key=lambda row: (-row[0], row[1]))

    per_source = max(1, ceil(limit / len(by_source)))
    pool = [row for ranked in by_source.values() for row in ranked[:per_source]]
    if len(pool) < limit:
        included = {row[2]["id"] for row in pool}
        remainder = sorted(
            (row for ranked in by_source.values() for row in ranked[per_source:] if row[2]["id"] not in included),
            key=lambda row: (-row[0], row[1]),
        )
        pool.extend(remainder[:limit - len(pool)])
    pool.sort(key=lambda row: (-row[0], row[1]))
    candidates = []
    for score, _, font in pool[:limit]:
        candidates.append({
            "id": font["id"],
            "family": font.get("family", ""),
            "category": font.get("category", ""),
            "source": font.get("source", ""),
            "retrieval_score": score,
        })
    return candidates


@app.post("/api/smart-route")
async def smart_route(request: RouteRequest) -> dict[str, Any]:
    """Infer a brief profile, retrieve across the catalog, then locally rerank candidates."""
    model_path = os.getenv(
        "FONTFINDER_MODEL_PATH",
        str(Path.home() / ".lmstudio" / "models" / "lmstudio-community" / "LFM2.5-350M-GGUF" / "LFM2.5-350M-Q8_0.gguf"),
    )
    if not os.path.isfile(model_path):
        raise HTTPException(status_code=503, detail=f"Local model file not found: {model_path}")
    if not request.fonts:
        return {"font_ids": [], "summary": "No eligible font candidates were provided.", "model": "LFM2.5-350M (local)"}
    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Install the local model runtime with: pip install llama-cpp-python") from exc

    try:
        content, candidates = await asyncio.to_thread(_run_local_route, Llama, model_path, request)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Local model routing failed: {exc}") from exc
    candidate_ids = [candidate["id"] for candidate in candidates]
    route = _parse_json_object(content)
    if route is None or not isinstance(route.get("font_ids"), (list, str)):
        ids = candidate_ids[:8]
        summary = "The model response was malformed; showing the strongest diverse catalog matches."
    else:
        raw_ids = route.get("font_ids", [])
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        allowed = set(candidate_ids)
        routed_ids = []
        for item in raw_ids if isinstance(raw_ids, list) else []:
            font_id = item.get("id", item.get("font_id")) if isinstance(item, dict) else item
            if isinstance(font_id, str) and font_id in allowed:
                routed_ids.append(font_id)
        ids = list(dict.fromkeys(routed_ids))[:8]
        summary = str(route.get("summary") or "Matched by your project description.")[:240]
        if not ids:
            ids = candidate_ids[:8]
            if ids:
                summary = "The model returned no usable IDs; showing the strongest diverse catalog matches."
    return {"font_ids": ids, "summary": summary, "model": "LFM2.5-350M (local)"}


def _run_local_route(model_factory: Any, model_path: str, request: RouteRequest) -> tuple[str, list[dict[str, Any]]]:
    global _local_model
    with _local_model_lock:
        if _local_model is None:
            _local_model = model_factory(
                model_path=model_path,
                n_ctx=4096,
                n_threads=max(2, min(8, os.cpu_count() or 4)),
                verbose=False,
            )
        try:
            profile = _infer_route_profile(_local_model, request.prompt)
        except Exception:
            profile = {"keywords": _route_terms(re.findall(r"[a-z0-9]{3,}", request.prompt.casefold())),
                       "typographic_traits": [], "moods": [], "use_cases": [], "avoid": []}
        candidates = _retrieve_route_candidates(request.prompt, request.fonts, profile)
        candidate_ids = [candidate["id"] for candidate in candidates]
        if not candidate_ids:
            return "", []
        result = _local_model.create_chat_completion(
            temperature=0,
            max_tokens=256,
            response_format={
                "type": "json_object",
                "schema": {
                    "type": "object",
                    "properties": {
                        "font_ids": {"type": "array", "items": {"type": "string", "enum": candidate_ids}},
                        "summary": {"type": "string"},
                    },
                    "required": ["font_ids", "summary"],
                    "additionalProperties": False,
                },
            },
            messages=[
                {"role": "system", "content": "Compare every candidate with the brief and its inferred search profile. Select up to 8 best matches based on typographic traits, mood, use case, and readability. Do not favor earlier entries. The retrieval_score is a relevance signal, not a ranking mandate. Return only the supplied IDs as JSON with a concise summary. Do not make license claims."},
                {"role": "user", "content": json.dumps({"request": request.prompt, "inferred_profile": profile, "candidates": candidates}, ensure_ascii=False)},
            ],
        )
        content = result["choices"][0]["message"].get("content")
        return (content if isinstance(content, str) else ""), candidates


_bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
_frontend_build = _bundle_root / "frontend" / "dist"
if _frontend_build.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_build), html=True), name="frontend")
