import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="HET State Page Scraper API", version="1.1.0")

PARENT_URL = "https://www.heavyequipmenttransport.com/heavy-equipment-transport-by-state.php"
DOMAIN = "www.heavyequipmenttransport.com"
STATE_RE = re.compile(r"^/states/[a-z0-9-]+-equipment-shipping\.php$", re.I)

FAQ_RE = re.compile(
    r"\b(?:equipment\s+transport\s+)?faqs?\b|frequently\s+asked",
    re.I,
)

# Verified post-body module headings used when a legacy state page has no FAQ.
POST_BODY_RE = re.compile(
    r"^(?:popular articles|need a specific transport\??|about us|navigation)$",
    re.I,
)

API_KEY = os.getenv("HET_SCRAPER_API_KEY", "")
UA = "HET-State-Localization-Scraper-API/1.1"
TIMEOUT = 30


class ScrapeRequest(BaseModel):
    parent_url: str = Field(default=PARENT_URL)
    limit: int = Field(default=0, ge=0, le=80)


def auth(authorization: str | None):
    if not API_KEY:
        raise HTTPException(500, "Server API key is not configured")
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(401, "Unauthorized")


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def new_session():
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return s


def fetch(s, url):
    r = s.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def discover(s, parent):
    soup = BeautifulSoup(fetch(s, parent), "html.parser")
    urls = set()

    for a in soup.find_all("a", href=True):
        u = urljoin(parent, a["href"].split("#", 1)[0])
        p = urlparse(u)

        if p.netloc.lower() == DOMAIN and STATE_RE.match(p.path):
            urls.add(f"https://{DOMAIN}{p.path}")

    return sorted(urls)


def serialize(tag):
    if tag.name in {"p", "ul", "ol", "table", "blockquote"}:
        clone = BeautifulSoup(str(tag), "html.parser").find()

        if not clone:
            return clean(tag.get_text(" ", strip=True))

        for bad in clone.find_all(["script", "style", "noscript"]):
            bad.decompose()

        # Preserve only useful inline link destinations.
        for t in clone.find_all(True):
            keep = {}
            if t.name == "a" and t.get("href"):
                keep["href"] = t["href"]
            t.attrs = keep

        return re.sub(r">\s+<", "><", str(clone)).strip()

    return clean(tag.get_text(" ", strip=True))


def find_boundary(soup, h1):
    """
    Preferred boundary: final FAQ section.
    Fallback for legacy pages without FAQs: a verified post-body module.
    Never invent an endpoint.
    """
    headings = soup.find_all(re.compile(r"^h[2-6]$"))

    faq_candidates = [
        h
        for h in headings
        if FAQ_RE.search(clean(h.get_text(" ", strip=True)))
        and h.find_previous("h1") is h1
    ]

    if faq_candidates:
        return "faq", faq_candidates[-1]

    for h in headings:
        if h.find_previous("h1") is not h1:
            continue

        heading_text = clean(h.get_text(" ", strip=True))
        if POST_BODY_RE.match(heading_text):
            return "post_body", h

    raise ValueError(
        "No FAQ or verified post-body boundary found; endpoint refuses to guess"
    )


def extract(s, url):
    soup = BeautifulSoup(fetch(s, url), "html.parser")

    # Capture metadata before removing chrome.
    title = clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
    md = soup.find("meta", attrs={"name": "description"})
    meta = clean(md.get("content", "")) if md else ""

    # Exclude global chrome and executable/non-content elements.
    for selector in ["header", "footer", "nav", "script", "style", "noscript"]:
        for t in soup.select(selector):
            t.decompose()

    h1 = soup.find("h1")
    if not h1:
        raise ValueError("No H1 found")

    boundary_type, boundary = find_boundary(soup, h1)
    faq = boundary if boundary_type == "faq" else None
    post_body = boundary if boundary_type == "post_body" else None

    rec = OrderedDict(
        source_url=url,
        source_slug=Path(urlparse(url).path).name,
        seo_title_en=title,
        seo_title_es="",
        meta_description_en=meta,
        meta_description_es="",
        hero_h1_en=clean(h1.get_text(" ", strip=True)),
        hero_h1_es="",
    )

    # QA/debug provenance. This is descriptive metadata, not translated page copy.
    rec["extraction_boundary"] = (
        "final_faq"
        if faq is not None
        else f"before:{clean(post_body.get_text(' ', strip=True))}"
    )

    counts = {
        "h2": 0,
        "h3": 0,
        "h4": 0,
        "h5": 0,
        "h6": 0,
        "content": 0,
    }
    faq_q = 0
    in_faq = False

    accepted = {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "ul",
        "ol",
        "table",
        "blockquote",
    }

    for node in h1.find_all_next():
        if not isinstance(node, Tag) or node.name not in accepted:
            continue

        if node is h1:
            continue

        # Avoid duplicate nested semantic elements.
        parent_semantic = node.find_parent(list(accepted))
        if parent_semantic is not None and parent_semantic is not h1:
            continue

        # Legacy page with no FAQ: stop before the verified post-body module.
        if faq is None and node is post_body:
            break

        # FAQ page: record the FAQ heading and switch modes.
        if faq is not None and node is faq:
            in_faq = True
            rec["faq_h2_en"] = clean(node.get_text(" ", strip=True))
            rec["faq_h2_es"] = ""
            continue

        # The first H2 after the FAQ marks the end of requested page content.
        if faq is not None and in_faq and node.name == "h2":
            break

        txt = serialize(node)
        if not txt:
            continue

        if in_faq:
            # FAQ questions may use H3-H6 depending on legacy template.
            if node.name in {"h3", "h4", "h5", "h6"}:
                faq_q += 1
                rec[f"faq_question_{faq_q:02d}_en"] = txt
                rec[f"faq_question_{faq_q:02d}_es"] = ""
            else:
                key = (
                    f"faq_answer_{faq_q:02d}_en"
                    if faq_q
                    else "faq_intro_en"
                )
                rec[key] = (
                    rec.get(key, "")
                    + (" " if rec.get(key) else "")
                    + txt
                ).strip()
                rec.setdefault(key[:-3] + "_es", "")
            continue

        if node.name in {"h2", "h3", "h4", "h5", "h6"}:
            counts[node.name] += 1
            key = f"body_{node.name}_{counts[node.name]:02d}"
            rec[key + "_en"] = txt
            rec[key + "_es"] = ""
        else:
            counts["content"] += 1
            key = f"body_content_{counts['content']:02d}"
            rec[key + "_en"] = txt
            rec[key + "_es"] = ""

    return rec


@app.get("/health", operation_id="healthCheck")
def health():
    return {
        "ok": True,
        "service": "HET State Page Scraper API",
        "version": "1.1.0",
    }


@app.get("/states", operation_id="listStatePages")
def states(authorization: str | None = Header(default=None)):
    auth(authorization)
    s = new_session()
    urls = discover(s, PARENT_URL)

    return {
        "ok": True,
        "count": len(urls),
        "pages": [
            {
                "source_url": u,
                "source_slug": Path(urlparse(u).path).name,
            }
            for u in urls
        ],
    }


@app.get("/scrape", operation_id="scrapeStatePage")
def scrape(url: str, authorization: str | None = Header(default=None)):
    auth(authorization)

    p = urlparse(url)
    if p.scheme != "https" or p.netloc.lower() != DOMAIN or not STATE_RE.match(p.path):
        raise HTTPException(
            400,
            "URL must be a HET HTTPS /states/*-equipment-shipping.php page",
        )

    try:
        return {"ok": True, "page": extract(new_session(), url)}
    except Exception as e:
        raise HTTPException(422, str(e))


@app.post("/scrape-all", operation_id="scrapeAllStatePages")
def scrape_all(
    req: ScrapeRequest,
    authorization: str | None = Header(default=None),
):
    auth(authorization)

    # Keep discovery pinned to the approved HET parent/domain.
    if req.parent_url != PARENT_URL:
        raise HTTPException(400, "parent_url must be the approved HET State parent URL")

    s = new_session()
    urls = discover(s, req.parent_url)

    if req.limit:
        urls = urls[: req.limit]

    pages = []
    errors = []

    for u in urls:
        try:
            pages.append(extract(s, u))
        except Exception as e:
            errors.append({"source_url": u, "error": str(e)})

        time.sleep(0.35)

    return {
        "ok": len(errors) == 0,
        "discovered": len(urls),
        "completed": len(pages),
        "errors": errors,
        "pages": pages,
    }
