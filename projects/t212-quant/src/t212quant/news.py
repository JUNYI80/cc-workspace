"""Headlines from Google News RSS (free, no key)."""
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


def search(query: str, limit: int = 20, timeout: float = 15) -> list[Headline]:
    resp = requests.get(RSS_URL.format(q=quote_plus(query)), headers={"User-Agent": USER_AGENT},
                        timeout=timeout)
    resp.raise_for_status()
    return parse_rss(resp.text)[:limit]
