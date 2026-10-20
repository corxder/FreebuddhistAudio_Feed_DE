#!/usr/bin/env python3
"""
Podcast-Feed (RSS 2.0 + iTunes-Tags) nur mit deutschsprachigen Vorträgen
von freebuddhistaudio.com – ohne geführte Meditationen und ohne andere Sprachen.

Datenquellen (öffentliche JSON-API der Website, Stand Okt. 2026):
  /api/v1/collections/p?p=<Ort>&t=audio      Vorträge eines Zentrums
  /api/v1/collections/themes                  Themen-Index (Genres, z.B. "Ethik (deutsch)")
  /api/v1/collections/th?th=<Thema>&t=audio   Vorträge eines Themas
  /api/v1/collections/latest                  zuletzt hochgeladene Vorträge
  /audio/details?num=<CatNum>                 Detailseite, JSON in document.__FBA__.talk
                                              (language, genre, tracks[].audio.mp3 …)

Nur Standardbibliothek, Python >= 3.9.

    python fba_de_feed.py --out feed.xml --state state.json
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

BASE = "https://www.freebuddhistaudio.com"
USER_AGENT = "fba-de-feed/1.0 (privater Podcast-Feed, 1x taeglich)"
REQUEST_PAUSE_S = 0.4

# Deutschsprachige Zentren im Orts-Index von FBA
GERMAN_PLACES = [
    "Essen",
    "Buddhistisches_Tor_Berlin",
    "Fulda_Triratna_Buddhist_Community",
    "Minden",
    "Das_Stadtretreat",
]

# Ausschluss geführter Meditationen: Titel-Muster (case-insensitive).
# Bewusst eng gefasst, damit Vorträge ÜBER Meditation drin bleiben.
# Was hier rausfällt, steht mit Grund in excluded.txt.
MEDITATION_TITLE_PATTERNS = [
    r"\bgef(ü|ue)hrte\s+meditation",
    r"\bangeleitete\s+meditation",
    r"\bmeditationsanleitung",
    r"\banleitung\s+(zur|zu\s+einer)\s+.*meditation",
    r"\bmeditationsabend\b",
    r"\bmeditationsglocke",
    r"\bmetta[\s-]*bhavana\b",
    r"\bachtsamkeit\s+(auf|mit)\s+dem\s+atem\b",
    r"\bvergegenw(ä|ae)rtigung\s+des\s+atmens\b",
    r"\(\s*meditation\s*\)",
    r"\bguided\s+meditation\b",
    r"\bmed\.?\s+anleitung\b",
]
# Groß-/Kleinschreibung beachtend: "Meditation Metta", "Meditation Stille Im Offenen Raum",
# "Meditation-Die 4 Brahmaviharas" sind Anleitungen (Minden-Retreat). Vorträge wie
# "Meditation - der Weg der Transformation" oder "Meditation zwischen Schlangen und
# Tigern" passen bewusst nicht.
MEDITATION_TITLE_PATTERNS_CS = [
    r"^Meditation(\s+[A-ZÄÖÜ0-9]\S*)+\s*$",
    r"^Meditation-\S",
]
# Genres, die keine Vorträge sind
EXCLUDE_GENRES = [
    "chanting und rituale (deutsch)",
]
# Vorträge mit Konsekutiv-Übersetzung ins Deutsche (z.B. englischer Redner in Essen)
INCLUDE_TRANSLATED = True

# Manuelle Korrekturen (CatNums), falls die Heuristik danebenliegt
ALWAYS_INCLUDE: set[str] = set()
ALWAYS_EXCLUDE: set[str] = set()


# --------------------------------------------------------------------------- HTTP

def fetch(url: str, accept: str = "*/*", retries: int = 3) -> str:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", errors="replace")
            time.sleep(REQUEST_PAUSE_S)
            return body
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                raise
            last = e
        except Exception as e:  # Netzwerkfehler -> erneut versuchen
            last = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Abruf fehlgeschlagen: {url}: {last}")


def fetch_json(url: str) -> dict:
    return json.loads(fetch(url, accept="application/json"))


def api_collection(kind: str, params: dict[str, str] | None = None, limit: int = 1000) -> list[dict]:
    """Alle Einträge einer API-Collection (limit muss gesetzt sein, Default ist sonst 1)."""
    out: list[dict] = []
    page = 1
    while True:
        q = dict(params or {})
        q.update({"page": str(page), "limit": str(limit)})
        url = f"{BASE}/api/v1/collections/{urllib.parse.quote(kind)}?{urllib.parse.urlencode(q)}"
        coll = fetch_json(url).get("collection") or {}
        items = coll.get("items") or []
        out.extend(items)
        total = int(coll.get("total_items") or 0)
        if not items or len(out) >= total:
            return out
        page += 1


def extract_fba_json(page_html: str, key: str) -> dict | None:
    """document.__FBA__.<key> = {...} aus dem HTML einer Seite lesen."""
    for m in re.finditer(r"document\.__FBA__\." + re.escape(key) + r"\s*=\s*", page_html):
        start = m.end()
        if start < len(page_html) and page_html[start] == "{":
            try:
                obj, _ = json.JSONDecoder().raw_decode(page_html, start)
                return obj
            except json.JSONDecodeError:
                continue
    return None


# ----------------------------------------------------------------- Kandidaten

def talk_items(items: list[dict]) -> list[dict]:
    """Nur Audio-Vorträge aus einer Collection, als {cat_num, title, centre}."""
    res = []
    for it in items:
        url = it.get("url") or ""
        cat = it.get("cat_num") or it.get("catNum") or ""
        if "/audio/" not in url or not cat:
            continue
        res.append({"cat_num": cat, "title": html.unescape(it.get("title") or ""),
                    "centre": it.get("centre") or ""})
    return res


def collect_candidates(log) -> dict[str, dict]:
    cands: dict[str, dict] = {}

    def add(items, source):
        for t in talk_items(items):
            c = cands.setdefault(t["cat_num"], {**t, "sources": []})
            c["sources"].append(source)

    for place in GERMAN_PLACES:
        try:
            add(api_collection("p", {"p": place, "t": "audio"}), f"ort:{place}")
        except Exception as e:
            log(f"WARN Ort {place}: {e}")

    try:
        themes = api_collection("themes")
        german = [html.unescape(t.get("title") or "") for t in themes]
        german = [re.sub(r"\s*\(\d+\)\s*$", "", t) for t in german if "(deutsch)" in t.lower()]
        for th in german:
            try:
                items = api_collection("th", {"th": th, "t": "audio"})
            except Exception as e:
                log(f"WARN Thema {th}: {e}")
                continue
            add(items, f"thema:{th}")
    except Exception as e:
        log(f"WARN Themen-Index: {e}")

    try:
        add(api_collection("latest", {"t": "audio"}, limit=200), "latest")
    except Exception as e:
        log(f"WARN latest: {e}")

    return cands


# ------------------------------------------------------------- Klassifizierung

def classify(talk: dict, cand: dict) -> tuple[bool, str]:
    cat = cand["cat_num"]
    if cat in ALWAYS_EXCLUDE:
        return False, "manuell ausgeschlossen"
    if cat in ALWAYS_INCLUDE:
        return True, "manuell aufgenommen"

    lang = (talk.get("language") or "").lower()
    lang_src = (talk.get("language_source") or "").lower()
    trans = (talk.get("translation_language") or "").lower()
    genre = html.unescape(talk.get("genre") or talk.get("genre1") or "").strip()
    title = html.unescape(talk.get("title") or cand.get("title") or "")
    german_place = any(s.startswith("ort:") for s in cand["sources"])
    german_genre = "(deutsch)" in genre.lower()

    if lang == "de":
        is_de, why = True, "language=de"
    elif german_genre:
        is_de, why = True, f"genre={genre}"
    elif INCLUDE_TRANSLATED and trans == "de":
        is_de, why = True, "dt. Übersetzung"
    elif german_place and lang_src != "tagged":
        # Sprache nur per Formular-Default auf "en" gesetzt, Zentrum deutschsprachig
        is_de, why = True, "dt. Zentrum, Sprache ungetaggt"
    else:
        return False, f"Sprache {lang or '?'} ({lang_src or '?'})"

    if genre.lower() in EXCLUDE_GENRES:
        return False, f"Genre ausgeschlossen: {genre}"
    for pat in MEDITATION_TITLE_PATTERNS:
        if re.search(pat, title, re.IGNORECASE):
            return False, f"Meditation (Titel passt auf /{pat}/)"
    for pat in MEDITATION_TITLE_PATTERNS_CS:
        if re.search(pat, title.strip()):
            return False, f"Meditation (Titel passt auf /{pat}/)"
    return True, why


def to_record(talk: dict, cand: dict) -> dict:
    tracks = []
    for t in talk.get("tracks") or []:
        audio = t.get("audio") or {}
        mp3 = audio.get("mp3") if isinstance(audio, dict) else None
        if not mp3:
            continue
        url = mp3 if mp3.startswith("http") else BASE + mp3
        dur = t.get("durationSeconds")
        dur = dur if isinstance(dur, int) and 0 < dur < 360000 else 0
        tracks.append({"title": html.unescape(t.get("title") or ""), "url": url, "duration": dur})
    speaker = talk.get("speaker") or ((talk.get("speakers") or [None])[0]) or ""
    year = talk.get("year")
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = None
    return {
        "cat_num": cand["cat_num"],
        "title": html.unescape(talk.get("title") or cand.get("title") or cand["cat_num"]),
        "speaker": html.unescape(speaker),
        "year": year,
        "centre": cand.get("centre") or "",
        "blurb": talk.get("blurb") or talk.get("description") or "",
        "image": talk.get("image") or talk.get("imageUrl") or talk.get("image_url") or "",
        "tracks": tracks,
    }


# --------------------------------------------------------------------- Feed

def quote_url(url: str) -> str:
    """Leerzeichen/Umlaute in MP3-Pfaden korrekt kodieren."""
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme, p.netloc, urllib.parse.quote(urllib.parse.unquote(p.path)),
                                    p.query, p.fragment))


def hms(sec: int) -> str:
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def build_feed(entries: list[dict], out_path: str, self_url: str, image_url: str) -> int:
    ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    ATOM = "http://www.w3.org/2005/Atom"
    ET.register_namespace("itunes", ITUNES)
    ET.register_namespace("atom", ATOM)
    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")

    def sub(parent, tag, text=None, **attrs):
        el = ET.SubElement(parent, tag, attrs)
        if text is not None:
            el.text = text
        return el

    sub(ch, "title", "Free Buddhist Audio – Deutsche Vorträge")
    sub(ch, "link", BASE)
    sub(ch, "description", "Deutschsprachige Dharma-Vorträge von freebuddhistaudio.com "
                           "(Triratna), ohne geführte Meditationen. Inoffizieller, automatisch erzeugter Feed.")
    sub(ch, "language", "de")
    sub(ch, f"{{{ITUNES}}}author", "Free Buddhist Audio")
    sub(ch, f"{{{ITUNES}}}explicit", "false")
    if self_url:
        sub(ch, f"{{{ATOM}}}link", href=self_url, rel="self", type="application/rss+xml")
    if image_url:
        sub(ch, f"{{{ITUNES}}}image", href=image_url)
        img = sub(ch, "image")
        sub(img, "url", image_url)
        sub(img, "title", "Free Buddhist Audio – Deutsche Vorträge")
        sub(img, "link", BASE)

    n = 0
    for e in sorted(entries, key=lambda x: x["pub"], reverse=True):
        rec = e["record"]
        tracks = rec["tracks"]
        link = f"{BASE}/audio/details?num={rec['cat_num']}"
        meta = " · ".join(x for x in [rec["speaker"], rec["centre"], str(rec["year"] or "")] if x)
        desc = (f"<p>{html.escape(meta)}</p>" if meta else "") + (rec["blurb"] or "") + \
               f'<p><a href="{link}">Auf freebuddhistaudio.com öffnen</a></p>'
        base_pub = datetime.fromisoformat(e["pub"])
        for i, tr in enumerate(tracks, 1):
            title = rec["title"]
            if len(tracks) > 1:
                title += f" ({i}/{len(tracks)})"
                if tr["title"] and tr["title"].strip().lower() != rec["title"].strip().lower():
                    title += f" – {tr['title']}"
            item = sub(ch, "item")
            sub(item, "title", title)
            sub(item, "link", link)
            sub(item, "guid", f"fba-{rec['cat_num']}-{i}", isPermaLink="false")
            # Teil 1 ist die älteste Episode -> "älteste zuerst" spielt die Teile in Reihenfolge
            sub(item, "pubDate", format_datetime(base_pub + timedelta(seconds=i - 1)))
            sub(item, "description", desc)
            sub(item, "enclosure", url=quote_url(tr["url"]), length="0", type="audio/mpeg")
            if rec["speaker"]:
                sub(item, f"{{{ITUNES}}}author", rec["speaker"])
            if tr["duration"]:
                sub(item, f"{{{ITUNES}}}duration", hms(tr["duration"]))
            if rec["image"]:
                img = rec["image"] if rec["image"].startswith("http") else BASE + rec["image"]
                sub(item, f"{{{ITUNES}}}image", href=quote_url(img))
            n += 1

    ET.indent(rss)
    ET.ElementTree(rss).write(out_path, encoding="utf-8", xml_declaration=True)
    return n


def synthetic_pub(rec: dict) -> str:
    """Erstbefüllung: kein Upload-Datum verfügbar -> Jahr der Aufnahme + Katalognummer als Reihenfolge."""
    year = rec["year"] or 2000
    m = re.search(r"(\d+)", rec["cat_num"])
    offset = int(m.group(1)) if m else 0
    dt = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=10 * offset)
    return dt.isoformat()


# --------------------------------------------------------------------- Main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="feed.xml")
    ap.add_argument("--state", default="state.json", help="Cache: bereits geprüfte Vorträge")
    ap.add_argument("--excluded", default="excluded.txt", help="Liste der aussortierten Kandidaten mit Grund")
    ap.add_argument("--self-url", default="", help="Öffentliche URL des Feeds (optional)")
    ap.add_argument("--image-url", default="", help="Cover-Bild für den Podcast (optional)")
    ap.add_argument("--recheck", action="store_true", help="Alle Kandidaten neu klassifizieren")
    args = ap.parse_args()

    log = lambda msg: print(msg, file=sys.stderr, flush=True)
    try:
        with open(args.state, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    first_run = not state

    cands = collect_candidates(log)
    log(f"{len(cands)} Kandidaten gefunden")
    if not cands:
        log("FEHLER: keine Kandidaten – API geändert oder nicht erreichbar. Feed bleibt unverändert.")
        return 1

    now = datetime.now(timezone.utc)
    new_de = 0
    for cat, cand in cands.items():
        known = state.get(cat)
        if known and not args.recheck:
            continue
        try:
            page = fetch(f"{BASE}/audio/details?num={urllib.parse.quote(cat)}")
        except Exception as e:
            log(f"WARN {cat}: {e}")
            continue
        talk = extract_fba_json(page, "talk")
        if not talk:
            log(f"WARN {cat}: kein Talk-JSON auf der Detailseite")
            continue
        ok, why = classify(talk, cand)
        rec = to_record(talk, cand)
        if ok and not rec["tracks"]:
            ok, why = False, "keine MP3 (evtl. nur für Ordensmitglieder)"
        pub = (known or {}).get("pub") or (synthetic_pub(rec) if first_run else now.isoformat())
        state[cat] = {"include": ok, "reason": why, "pub": pub, "record": rec,
                      "checked": now.isoformat(timespec="seconds")}
        if ok and not known:
            new_de += 1
        log(f"{'+' if ok else '-'} {cat:8} {rec['title'][:60]:60} [{why}]")

    with open(args.state, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)

    included = [v for v in state.values() if v["include"]]
    with open(args.excluded, "w", encoding="utf-8") as f:
        for cat, v in sorted(state.items()):
            if not v["include"]:
                f.write(f"{cat}\t{v['record']['title']}\t{v['reason']}\n")

    n = build_feed(included, args.out, args.self_url, args.image_url)
    log(f"Feed: {len(included)} Vorträge, {n} Episoden ({new_de} neu) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
