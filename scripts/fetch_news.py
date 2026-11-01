"""Collect live news for the ITIC 2026 planner.

Runs hourly in GitHub Actions and writes data/news.json, which the planner
reads. Sources: Google News RSS searches for ITIC Global, Istanbul travel
disruptions, and every company in the planner (read from index.html) plus
the walk-up meetings the team adds (read from the planner's Firestore).
Also builds the daily client update: new clients and fresh news from the
last 24 hours. Standard library only.
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


ITIC_WORDS = ("travel & health insurance", "travel and health insurance")
# Look-alike companies that share a name with one we meet.
EXCLUDE = {"IMG (International Medical Group)": ("Town Health",), "Medexpress": ("UPMC", "GoHealth", "urgent care")}


def is_itic(title):
    if "Investors Title" in title:                     # US stock with the ticker ITIC
        return False
    return "ITIC Global" in title or any(w in title.lower() for w in ITIC_WORDS) or (
        re.search(r"\bITIC\b", title) is not None and re.search(r"conference|insurance|Istanbul|assistance", title, re.I) is not None)


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


def mentions(items, phrase, co=""):
    """Keep headlines naming the company exactly (same capitals), minus known look-alikes."""
    bad = EXCLUDE.get(co, ())
    return [n for n in items if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", n["t"])
            and not any(b.lower() in n["t"].lower() for b in bad)]


def companies(src):
    seen, out = set(), []
    for co in re.findall(r'\bco:"([^"]+)"', src):
        if co not in seen:
            seen.add(co)
            out.append(co)
    return out


def fs_value(v):
    for k in ("stringValue", "integerValue", "doubleValue", "booleanValue"):
        if k in v:
            return v[k]
    return None


def walkups(src):
    """Walk-up meetings from the planner's shared Firestore (same public read access as the page)."""
    key = re.search(r'apiKey:"([^"]+)"', src).group(1)
    project = re.search(r'projectId:"([^"]+)"', src).group(1)
    prefix = re.search(r'const FB_PREFIX="([^"]+)"', src).group(1)
    base = (f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents/"
            f"{prefix}walkups?pageSize=300&key={key}")
    out, token = [], ""
    while True:
        with urllib.request.urlopen(base + (f"&pageToken={token}" if token else ""), timeout=20) as r:
            page = json.load(r)
        for d in page.get("documents", []):
            f = {k: fs_value(v) for k, v in d.get("fields", {}).items()}
            co = str(f.get("co") or "").strip()[:120]
            if co:
                out.append({"co": co, "contact": str(f.get("contact") or "")[:160], "type": str(f.get("type") or "")[:60],
                            "country": str(f.get("country") or "")[:60], "day": str(f.get("day") or "")[:3],
                            "start": str(f.get("start") or "")[:5], "added": int(float(f.get("at") or 0) / 1000)})
        token = page.get("nextPageToken")
        if not token:
            return out


def main():
    now = int(time.time())
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    data = {"updated": now, "itic": [], "istanbul": [], "industry": [], "companies": {}}
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
            items = [n for n in items if is_itic(n["t"])]
        items.sort(key=lambda x: -x["d"])
        data[key] = items[:12] if ok else old.get(key, [])  # keep the last good copy if every search failed
    try:
        walks = walkups(src)
    except Exception as e:
        print("walk-ups not readable", e)
        walks = old.get("walkups", [])
    data["walkups"] = walks
    names = companies(src)
    names += [w["co"] for w in walks if w["co"] not in names]
    for co in names:
        try:
            items = mentions(fetch(company_query(co), 10), company_terms(co)[0], co)[:4]
        except Exception as e:
            print("failed", co, e)
            items = old.get("companies", {}).get(co, [])
        if items:
            data["companies"][co] = items
        time.sleep(1)
    # Daily client update: walk-ups added and company news published in the last 24 hours.
    since = now - 86400
    fresh = {co: [n for n in items if n["d"] >= since] for co, items in data["companies"].items()}
    data["daily"] = {"since": since,
                     "new_clients": sorted([w for w in walks if w["added"] >= since], key=lambda w: -w["added"]),
                     "fresh": {co: items for co, items in fresh.items() if items}}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("wrote", OUT, datetime.now(timezone.utc).isoformat(), len(data["companies"]), "companies with news")


if __name__ == "__main__":
    main()
