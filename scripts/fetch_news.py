"""Collect live news for the ITIC 2026 planner.

Runs hourly in GitHub Actions and writes data/news.json, which the planner
reads. Sources: Google News RSS searches for ITIC Global, Istanbul travel
disruptions, and every company in the planner (read from index.html).
Standard library only.
"""
import email.utils
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "news.json"
UA = "Mozilla/5.0 (HWA ITIC planner news fetcher)"
SECTOR = "(assistance OR insurance OR medical OR hospital OR ambulance OR health)"

# Several simple searches per section work better than one long OR query.
QUERIES = {
    "itic": ['"ITIC Global" when:120d', 'ITIC Istanbul insurance when:120d',
             '"International Travel & Health Insurance Conference" when:365d', '"ITIC" travel health insurance when:60d'],
    "istanbul": 'Istanbul (airport OR traffic OR strike OR metro OR ferry OR flights OR storm) when:2d',
    "industry": '("travel insurance" OR "medical assistance" OR "air ambulance") when:7d',
}


def rss_url(q):
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-GB", "gl": "GB", "ceid": "GB:en"})


def fetch(q, limit):
    req = urllib.request.Request(rss_url(q), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse(r.read(), limit)


def parse(xml_bytes, limit):
    items = []
    for it in ET.fromstring(xml_bytes).iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        link = (it.findtext("link") or "").strip()
        when = it.findtext("pubDate")
        try:
            ts = int(email.utils.parsedate_to_datetime(when).timestamp()) if when else 0
        except (TypeError, ValueError):
            ts = 0
        if title and link.startswith("https://"):
            items.append({"t": title[:220], "u": link, "s": src[:80], "d": ts})
    items.sort(key=lambda x: -x["d"])
    return items[:limit]


ITIC_WORDS = ("itic", "travel & health insurance", "travel and health insurance")


def company_terms(name):
    """Return (phrase that must appear in headlines, extra search words)."""
    inner = re.findall(r"\((.*?)\)", name)
    outer = re.split(r"\s+\+\s+|\s*/\s*", re.sub(r"\s*\(.*?\)", "", name))[0].strip()
    extra = ""
    if inner:
        if re.fullmatch(r"[A-Z0-9&]{2,6}", outer):    # "ISAS (Inter Secours Assistance)" -> use the full name
            outer = inner[0].strip()
        else:                                          # "Medical Park (Türkiye)" -> add the country as context
            extra = inner[0].strip()
    return outer, extra


def company_query(name):
    phrase, extra = company_terms(name)
    return f'"{phrase}" {extra} {SECTOR} when:90d'.replace("  ", " ")


def mentions(items, phrase):
    p = phrase.lower()
    return [n for n in items if p in n["t"].lower()]


def companies():
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    seen, out = set(), []
    for co in re.findall(r'\bco:"([^"]+)"', src):
        if co not in seen:
            seen.add(co)
            out.append(co)
    return out


def main():
    data = {"updated": int(time.time()), "itic": [], "istanbul": [], "industry": [], "companies": {}}
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    for key, qs in QUERIES.items():
        got, ok = {}, False
        for q in ([qs] if isinstance(qs, str) else qs):
            try:
                for n in fetch(q, 15):
                    got.setdefault(n["t"].lower(), n)
                ok = True
            except Exception as e:
                print("failed", key, q, e)
            time.sleep(1)
        items = list(got.values())
        if key == "itic":
            items = [n for n in items if any(w in n["t"].lower() for w in ITIC_WORDS)]
        items.sort(key=lambda x: -x["d"])
        data[key] = items[:12] if ok else old.get(key, [])  # keep the last good copy if every search failed
    for co in companies():
        try:
            items = mentions(fetch(company_query(co), 10), company_terms(co)[0])[:4]
        except Exception as e:
            print("failed", co, e)
            items = old.get("companies", {}).get(co, [])
        if items:
            data["companies"][co] = items
        time.sleep(1)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("wrote", OUT, datetime.now(timezone.utc).isoformat(), len(data["companies"]), "companies with news")


if __name__ == "__main__":
    main()
