"""Headlines from Google News RSS (free, no key), kept only from reputable outlets."""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests

log = logging.getLogger(__name__)

RSS_URL = "https://news.google.com/rss/search?q={q}&hl=en-GB&gl=GB&ceid=GB:en"
USER_AGENT = "Mozilla/5.0 (compatible; t212quant research app)"


# Major news agencies, financial papers and broadcasters. Matched against the
# source name Google News shows (case-insensitive substring).
TRUSTED_SOURCES = [
    "Reuters", "Bloomberg", "Financial Times", "FT.com", "Wall Street Journal", "WSJ",
    "Associated Press", "AP News", "CNBC", "BBC", "The New York Times", "Washington Post",
    "The Economist", "Barron's", "MarketWatch", "Fortune", "Axios", "The Information",
    "Nikkei", "The Guardian", "The Times", "Forbes", "Business Insider", "TechCrunch",
    "The Verge", "Wired", "CNN", "NPR", "Yahoo Finance", "Investopedia", "Morningstar",
    "Sky News", "The Telegraph", "Politico", "Semafor", "Crunchbase", "PitchBook",
]


def is_trusted(source: str) -> bool:
    s = source.lower()
    return any(t.lower() in s for t in TRUSTED_SOURCES)


@dataclass(frozen=True)
class Headline:
    title: str
    link: str
    published: datetime | None
    source: str


def parse_rss(xml_text: str) -> list[Headline]:
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        # Google appends " - Source" to every title; drop it when we have the source.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            published = None
        out.append(Headline(title, (item.findtext("link") or "").strip(), published, source))
    out.sort(key=lambda h: h.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out


def search(query: str, limit: int = 20, trusted_only: bool = True,
           timeout: float = 15) -> list[Headline]:
    resp = requests.get(RSS_URL.format(q=quote_plus(query)), headers={"User-Agent": USER_AGENT},
                        timeout=timeout)
    resp.raise_for_status()
    items = parse_rss(resp.text)
    if trusted_only:
        items = [h for h in items if is_trusted(h.source)]
    return items[:limit]
