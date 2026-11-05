"""新闻：Google 新闻 RSS（官方订阅源），只保留官方媒体和主流财经媒体的报道。"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests

log = logging.getLogger(__name__)

RSS_URL = "https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
USER_AGENT = "cnquant research app (personal use)"

# 官方媒体、证监会指定信息披露报刊、主流财经媒体。名字按 Google 新闻显示的来源名匹配（包含即可）。
# 门户网站和行情软件（东方财富、新浪财经、同花顺、证券之星、金融界、凤凰网等）多为转载，不在名单里。
TRUSTED_SOURCES = [
    "新华", "人民网", "人民日报", "央视", "CCTV", "央广", "中国新闻网", "中新网", "光明网", "经济日报",
    "科技日报", "中国科技网", "上观", "解放日报",
    "中国证券报", "中证网", "上海证券报", "上证", "证券时报", "证券日报", "金融时报",
    "第一财经", "财新", "21世纪经济报道", "21财经", "经济观察", "界面新闻", "澎湃", "每日经济新闻",
    "中国经营报", "华夏时报", "财联社", "证券市场周刊", "新京报", "南方都市报",
    "路透", "Reuters", "彭博", "Bloomberg", "金融时报中文网", "FT中文网", "华尔街日报",
    "BBC", "联合早报", "香港经济日报", "信报", "南华早报", "SCMP",
]


@dataclass(frozen=True)
class Headline:
    title: str
    link: str
    published: datetime | None
    source: str


def is_trusted(source: str) -> bool:
    return any(t.lower() in source.lower() for t in TRUSTED_SOURCES)


def parse_rss(xml_text: str) -> list[Headline]:
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
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


def search(query: str, limit: int = 20, trusted_only: bool = True, timeout: float = 15) -> list[Headline]:
    resp = requests.get(RSS_URL.format(q=quote_plus(query)), headers={"User-Agent": USER_AGENT},
                        timeout=timeout)
    resp.raise_for_status()
    items = parse_rss(resp.text)
    if trusted_only:
        items = [h for h in items if is_trusted(h.source)]
    return items[:limit]
