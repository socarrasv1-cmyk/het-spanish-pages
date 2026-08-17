import os, re, time
from urllib.parse import urljoin, urlparse
from pathlib import Path
from collections import OrderedDict

import requests
from bs4 import BeautifulSoup, Tag
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="HET State Page Scraper API", version="1.0.0")

PARENT_URL="https://www.heavyequipmenttransport.com/heavy-equipment-transport-by-state.php"
DOMAIN="www.heavyequipmenttransport.com"
STATE_RE=re.compile(r"^/states/[a-z0-9-]+-equipment-shipping\.php$", re.I)
FAQ_RE=re.compile(r"\b(?:equipment\s+transport\s+)?faq\b|frequently\s+asked", re.I)
API_KEY=os.getenv("HET_SCRAPER_API_KEY","")
UA="HET-State-Localization-Scraper-API/1.0"

class ScrapeRequest(BaseModel):
    parent_url: str = Field(default=PARENT_URL)
    limit: int = Field(default=0, ge=0, le=80)

def auth(authorization: str|None):
    if not API_KEY:
        raise HTTPException(500,"Server API key is not configured")
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(401,"Unauthorized")

def clean(s): return re.sub(r"\s+"," ",s or "").strip()

def session():
    s=requests.Session()
    s.headers.update({"User-Agent":UA,"Accept-Language":"en-US,en;q=0.9"})
    return s

def fetch(s,url):
    r=s.get(url,timeout=30)
    r.raise_for_status()
    return r.text

def discover(s,parent):
    soup=BeautifulSoup(fetch(s,parent),"html.parser")
    out=set()
    for a in soup.find_all("a",href=True):
        u=urljoin(parent,a["href"].split("#",1)[0]); p=urlparse(u)
        if p.netloc.lower()==DOMAIN and STATE_RE.match(p.path):
            out.add(f"https://{DOMAIN}{p.path}")
    return sorted(out)

def serialize(tag):
    if tag.name in {"p","ul","ol","table","blockquote"}:
        clone=BeautifulSoup(str(tag),"html.parser").find()
        if not clone: return clean(tag.get_text(" ",strip=True))
        for bad in clone.find_all(["script","style","noscript"]): bad.decompose()
        for t in clone.find_all(True):
            keep={}
            if t.name=="a" and t.get("href"): keep["href"]=t["href"]
            t.attrs=keep
        return re.sub(r">\s+<","><",str(clone)).strip()
    return clean(tag.get_text(" ",strip=True))

def extract(s,url):
    soup=BeautifulSoup(fetch(s,url),"html.parser")
    title=clean(soup.title.get_text(" ",strip=True)) if soup.title else ""
    md=soup.find("meta",attrs={"name":"description"})
    meta=clean(md.get("content","")) if md else ""
    for sel in ["header","footer","nav","script","style","noscript"]:
        for t in soup.select(sel): t.decompose()
    h1=soup.find("h1")
    if not h1: raise ValueError("No H1 found")
    faqs=[h for h in soup.find_all(re.compile(r"^h[2-4]$")) if FAQ_RE.search(clean(h.get_text(" ",strip=True)))]
    if not faqs: raise ValueError("FAQ heading not found; endpoint refuses to guess")
    faq=faqs[-1]
    rec=OrderedDict(source_url=url,source_slug=Path(urlparse(url).path).name,
                    seo_title_en=title,seo_title_es="",
                    meta_description_en=meta,meta_description_es="",
                    hero_h1_en=clean(h1.get_text(" ",strip=True)),hero_h1_es="")
    counts={"h2":0,"h3":0,"h4":0,"content":0}; fq=0; infaq=False
    accepted={"h1","h2","h3","h4","p","ul","ol","table","blockquote"}
    for node in h1.find_all_next():
        if not isinstance(node,Tag) or node.name not in accepted: continue
        if node is h1: continue
        par=node.find_parent(list(accepted))
        if par is not None and par is not h1: continue
        if node is faq:
            infaq=True; rec["faq_h2_en"]=clean(node.get_text(" ",strip=True)); rec["faq_h2_es"]=""; continue
        if infaq and node.name=="h2": break
        txt=serialize(node)
        if not txt: continue
        if infaq:
            if node.name in {"h3","h4"}:
                fq+=1; rec[f"faq_question_{fq:02d}_en"]=txt; rec[f"faq_question_{fq:02d}_es"]=""
            else:
                key=f"faq_answer_{fq:02d}_en" if fq else "faq_intro_en"
                rec[key]=(rec.get(key,"")+(" " if rec.get(key) else "")+txt).strip()
                rec.setdefault(key[:-3]+"_es","")
        elif node.name in {"h2","h3","h4"}:
            counts[node.name]+=1; k=f"body_{node.name}_{counts[node.name]:02d}"
            rec[k+"_en"]=txt; rec[k+"_es"]=""
        else:
            counts["content"]+=1; k=f"body_content_{counts['content']:02d}"
            rec[k+"_en"]=txt; rec[k+"_es"]=""
    return rec

@app.get("/health", operation_id="healthCheck")
def health(): return {"ok":True,"service":"HET State Page Scraper API","version":"1.0.0"}

@app.get("/states", operation_id="listStatePages")
def states(authorization: str|None=Header(default=None)):
    auth(authorization); s=session(); urls=discover(s,PARENT_URL)
    return {"ok":True,"count":len(urls),"pages":[{"source_url":u,"source_slug":Path(urlparse(u).path).name} for u in urls]}

@app.get("/scrape", operation_id="scrapeStatePage")
def scrape(url:str, authorization: str|None=Header(default=None)):
    auth(authorization)
    p=urlparse(url)
    if p.netloc.lower()!=DOMAIN or not STATE_RE.match(p.path):
        raise HTTPException(400,"URL must be a HET /states/*-equipment-shipping.php page")
    try: return {"ok":True,"page":extract(session(),url)}
    except Exception as e: raise HTTPException(422,str(e))

@app.post("/scrape-all", operation_id="scrapeAllStatePages")
def scrape_all(req:ScrapeRequest, authorization: str|None=Header(default=None)):
    auth(authorization); s=session(); urls=discover(s,req.parent_url)
    if req.limit: urls=urls[:req.limit]
    pages=[]; errors=[]
    for u in urls:
        try: pages.append(extract(s,u))
        except Exception as e: errors.append({"source_url":u,"error":str(e)})
        time.sleep(.35)
    return {"ok":len(errors)==0,"discovered":len(urls),"completed":len(pages),
            "errors":errors,"pages":pages}
