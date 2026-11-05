"""B 类：A 股新股与 IPO。

官方来源：
  * 深圳证券交易所「发行上市审核信息公开」：创业板、深市主板 IPO 项目和审核状态；
  * 上海证券交易所「发行上市审核」：科创板、沪市主板 IPO 项目（连接不稳定时自动跳过）；
  * 巨潮资讯网（证监会指定信息披露网站）：招股说明书、发行公告、上市公告书等公告。
上市前更早的阶段（传闻、上市辅导）只能来自新闻，会标明「据新闻」。
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from . import news

log = logging.getLogger(__name__)

SZSE_URL = "https://listing.szse.cn/api/ras/projectrends/query"
SSE_URL = "https://query.sse.com.cn/statusAction.do"
CNINFO_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_PDF = "http://static.cninfo.com.cn/{path}"
BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

STAGES = [
    "暂无上市消息",
    "有上市相关报道",
    "上市辅导中",
    "交易所已受理",
    "审核中（问询 / 上市委）",
    "注册阶段（即将发行）",
    "已经上市",
]
# 深交所项目状态码（来自深交所接口自带的状态表）-> (阶段, 名称)；阶段 0 表示已终止。
SZSE_STATUS = {
    20: (3, "已受理"), 30: (4, "已问询"), 45: (4, "上市委会议通过"), 44: (0, "上市委会议未通过"),
    46: (4, "暂缓审议"), 56: (4, "上市委复审会议通过"), 54: (0, "上市委复审会议未通过"),
    60: (5, "提交注册"), 70: (5, "注册生效"), 74: (0, "不予注册"), 78: (4, "补充审核"),
    76: (0, "终止注册"), 80: (4, "中止"), 90: (0, "终止（审核不通过）"), 95: (0, "终止（撤回）"),
    94: (0, "终止（未在规定时限内回复）"),
}
# 上交所项目状态码。1–5、8 已与上交所公告核对；6、7 未能确认，只标为「其他状态」。
SSE_STATUS = {1: (3, "已受理"), 2: (4, "已问询"), 3: (4, "上市委会议"), 4: (5, "提交注册"),
              5: (5, "注册结果"), 6: (4, "其他状态"), 7: (4, "其他状态"), 8: (0, "终止")}

# A company that publishes these is already trading; these mean it is being issued now.
LISTED_TITLE = re.compile(r"上市公告书|季度报告|年度报告|半年度报告|股票交易异常波动|股东大会")
ISSUING_TITLE = re.compile(r"招股说明书|招股意向书|发行公告|发行安排|初步询价")

NEWS_TOPIC = re.compile(r"上市|IPO|ipo|首发|招股|发行|辅导|申购|打新|科创板|创业板|北交所|港股|递表|聆讯")
NEWS_NEGATION = re.compile(r"暂无上市计划|没有上市计划|无上市计划|否认.*上市|辟谣|不考虑上市|暂不上市")
NEWS_RULES = [
    (5, r"注册生效|同意.*注册|提交注册|发行价|申购|打新|招股意向书"),
    (4, r"问询|上市委|上会|过会|审议通过"),
    (3, r"已受理|获受理|申请.*受理|受理"),
    (2, r"辅导备案|上市辅导|辅导"),
]


def _with_retry(fn, tries: int = 3):
    last = None
    for i in range(tries):
        try:
            return fn()
        except (requests.RequestException, ValueError) as exc:  # ValueError: non-JSON reply
            last = exc
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"连接失败：{last}")


def aliases(entry: str) -> list[str]:
    return [a.strip() for a in entry.split("|") if a.strip()]


def _mentions(text: str, names: list[str]) -> bool:
    return any(n in (text or "") for n in names)


# -- exchange IPO projects ------------------------------------------------------------

@dataclass(frozen=True)
class Project:
    exchange: str
    company: str
    short_name: str
    status: str
    stage: int  # 0 = 已终止 / 未通过
    updated: str
    sponsor: str = ""
    amount_yi: float | None = None  # 计划募资（亿元）
    industry: str = ""


def parse_szse(data: dict) -> list[Project]:
    out = []
    for r in data.get("data") or []:
        stage, status = SZSE_STATUS.get(r.get("prjstatus"), (3, r.get("prjst") or "未知"))
        amount = pd.to_numeric(r.get("maramt"), errors="coerce")
        out.append(Project("深交所", r.get("cmpnm") or "", r.get("cmpsnm") or "", r.get("prjst") or status,
                           stage, str(r.get("updtdt") or "")[:10], r.get("sprinsts") or "",
                           None if pd.isna(amount) else float(amount), r.get("csrcind") or ""))
    return out


def parse_sse(data: dict) -> list[Project]:
    out = []
    for r in data.get("result") or []:
        stage, status = SSE_STATUS.get(r.get("currStatus"), (3, "未知"))
        result = str(r.get("registeResult") or "")
        if stage == 5 and ("不予" in result or "终止" in result):
            stage, status = 0, result
        upd = str(r.get("updateDate") or "")
        out.append(Project("上交所", r.get("stockAuditName") or "", "", status, stage,
                           f"{upd[:4]}-{upd[4:6]}-{upd[6:8]}" if len(upd) >= 8 else upd,
                           amount_yi=r.get("planIssueCapital")))
    return out


def szse_search(name: str, fetch=None) -> list[Project]:
    fetch = fetch or (lambda: requests.get(
        SZSE_URL, params={"bizType": 1, "pageIndex": 0, "pageSize": 20, "keywords": name},
        headers={"User-Agent": BROWSER}, timeout=20).json())
    return [p for p in parse_szse(_with_retry(fetch)) if name in p.company or name in p.short_name]


def sse_search(name: str, fetch=None) -> list[Project]:
    fetch = fetch or (lambda: requests.get(
        SSE_URL, params={"isPagination": "true", "sqlId": "SH_XM_LB", "pageHelp.pageSize": 20,
                         "pageHelp.pageNo": 1, "keyword": name},
        headers={"User-Agent": BROWSER, "Referer": "https://kcb.sse.com.cn/"}, timeout=20).json())
    return [p for p in parse_sse(_with_retry(fetch)) if name in p.company]


def szse_recent(page_size: int = 50, fetch=None) -> list[Project]:
    """深交所最新更新的 IPO 项目（按更新时间）。"""
    fetch = fetch or (lambda: requests.get(
        SZSE_URL, params={"bizType": 1, "pageIndex": 0, "pageSize": page_size},
        headers={"User-Agent": BROWSER}, timeout=20).json())
    return parse_szse(_with_retry(fetch))


# -- cninfo announcements ---------------------------------------------------------------

@dataclass(frozen=True)
class Announcement:
    code: str
    name: str
    title: str
    date: date
    url: str


def parse_cninfo(data: dict) -> list[Announcement]:
    out = []
    for a in data.get("announcements") or []:
        ts = a.get("announcementTime")
        when = datetime.fromtimestamp(ts / 1000, tz=timezone(timedelta(hours=8))).date() if ts else None
        title = re.sub(r"<.*?>", "", a.get("announcementTitle") or "")
        name = re.sub(r"<.*?>", "", a.get("secName") or "")
        out.append(Announcement(a.get("secCode") or "", name, title, when,
                                CNINFO_PDF.format(path=a.get("adjunctUrl") or "")))
    return out


def cninfo_search(keyword: str, start: date, end: date, page_size: int = 30, fetch=None) -> list[Announcement]:
    form = {"pageNum": 1, "pageSize": page_size, "column": "szse", "tabName": "fulltext",
            "searchkey": keyword, "seDate": f"{start:%Y-%m-%d}~{end:%Y-%m-%d}", "isHLtitle": "true"}
    fetch = fetch or (lambda: requests.post(CNINFO_URL, data=form, headers={"User-Agent": BROWSER},
                                            timeout=25).json())
    return parse_cninfo(_with_retry(fetch))


def recent_listings(today: date | None = None, days: int = 180, fetch=None) -> pd.DataFrame:
    """最近上市的新股（来自上市公告书）：code, name, date, board, url。"""
    today = today or date.today()
    anns = cninfo_search("首次公开发行股票上市公告书", today - timedelta(days=days), today,
                         page_size=50, fetch=fetch)
    rows = {}
    for a in anns:
        if "首次公开发行" in a.title and "上市公告书" in a.title and "提示性" not in a.title and a.code:
            rows.setdefault(a.code, {"code": a.code, "name": a.name, "date": a.date,
                                     "board": _board_from_title(a.title, a.code), "url": a.url})
    return pd.DataFrame(list(rows.values()), columns=["code", "name", "date", "board", "url"])


def upcoming_subscriptions(today: date | None = None, days: int = 10, fetch=None) -> pd.DataFrame:
    """最近发布的新股发行公告（发行公告后一个交易日一般就是申购日）。"""
    today = today or date.today()
    anns = cninfo_search("首次公开发行股票发行公告", today - timedelta(days=days), today,
                         page_size=50, fetch=fetch)
    rows = {}
    for a in anns:
        if "首次公开发行" in a.title and "发行公告" in a.title and "提示性" not in a.title \
                and "更正" not in a.title and "结果" not in a.title:
            rows.setdefault(a.code or a.name, {"code": a.code, "name": a.name, "date": a.date,
                                               "board": _board_from_title(a.title, a.code),
                                               "title": a.title, "url": a.url})
    return pd.DataFrame(list(rows.values()), columns=["code", "name", "date", "board", "title", "url"])


def _board_from_title(title: str, code: str) -> str:
    for b in ("科创板", "创业板", "北京证券交易所", "主板"):
        if b in title:
            return "北交所" if b == "北京证券交易所" else b
    return "北交所" if code.startswith(("8", "4", "92")) else "主板"


# -- watchlist stage -----------------------------------------------------------------

def news_stage(headline: str) -> int:
    if not NEWS_TOPIC.search(headline):
        return 0
    if NEWS_NEGATION.search(headline):
        return 1
    for stage, pattern in NEWS_RULES:
        if re.search(pattern, headline):
            return stage
    return 1


@dataclass
class WatchStatus:
    name: str
    stage: int
    official: bool
    evidence: str
    heat: int
    code: str = ""
    terminated: str = ""  # 最近一次审核被终止的原因（如果有）
    headlines: list[news.Headline] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return STAGES[self.stage] + ("" if self.official or self.stage == 0 else "（据新闻，未经证实）")


def watch_status(entry: str, headlines: list[news.Headline], projects: list[Project],
                 announcements: list[Announcement], now: datetime | None = None,
                 errors: list[str] | None = None) -> WatchStatus:
    names = aliases(entry)
    now = now or datetime.now(timezone.utc)
    stage, official, evidence, code, terminated = 0, False, "", "", ""

    # Only the company's own announcements count, not other firms mentioning it.
    own = [a for a in announcements if a.code and _mentions(a.name, names)]
    listed = [a for a in own if LISTED_TITLE.search(a.title)]
    issuing = [a for a in own if ISSUING_TITLE.search(a.title)]
    if listed or issuing:
        a = max(listed or issuing, key=lambda x: x.date or date.min)
        stage, official, code = (6 if listed else 5), True, a.code
        evidence = f"巨潮资讯：证券代码 {a.code}（{a.name}），{a.date}「{a.title}」"
    elif projects:
        p = max(projects, key=lambda x: x.updated)
        if p.stage == 0:
            terminated = f"{p.exchange}：{p.status}（{p.updated}）"
        else:
            stage, official = p.stage, True
            evidence = f"{p.exchange}审核状态：{p.status}（更新于 {p.updated}）"

    recent = [h for h in headlines if h.published and (now - h.published).days <= 180
              and _mentions(h.title, names)]
    heat = sum(1 for h in recent if (now - h.published).days <= 30 and news_stage(h.title) > 0)
    if not official:
        for h in recent:
            s = news_stage(h.title)
            if s > stage:
                stage, evidence = s, f"新闻（{h.published:%Y-%m-%d}，{h.source}）：{h.title}"
    return WatchStatus(names[0], stage, official, evidence, heat, code, terminated,
                       recent[:8], projects, errors or [])


def gather(entry: str, today: date | None = None) -> tuple[list, list[Project], list[Announcement], list[str]]:
    """All sources for one watchlist entry, searched under every alias. Failures become messages."""
    today = today or date.today()
    heads, projects, anns, errors = [], [], [], []
    for name in aliases(entry):
        try:
            heads += news.search(f"{name} 上市", limit=30)
        except Exception:
            errors.append(f"新闻暂时无法获取（{name}）")
        for label, fn in (("深交所", szse_search), ("上交所", sse_search)):
            try:
                projects += fn(name)
            except Exception:
                errors.append(f"{label}审核信息暂时无法获取（{name}）")
        try:
            anns += cninfo_search(name, today - timedelta(days=365), today)
        except Exception:
            errors.append(f"巨潮资讯暂时无法获取（{name}）")
    seen = set()
    heads = [h for h in heads if not (h.link in seen or seen.add(h.link))]
    heads.sort(key=lambda h: h.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return heads, list(dict.fromkeys(projects)), list(dict.fromkeys(anns)), errors


# -- after listing ---------------------------------------------------------------------

@dataclass(frozen=True)
class Advice:
    level: str  # ok / info / warn
    text: str


def post_listing_advice(listed_on: date, closes: pd.Series | None, today: date | None = None) -> Advice:
    """A 股新股的几条经验规则。"""
    today = today or date.today()
    days = (today - listed_on).days
    if days <= 7:
        return Advice("warn", "刚上市：前 5 个交易日没有涨跌幅限制，一天涨跌几倍都可能，不要追高。")
    for months, who in ((6, "网下配售和部分战略配售股份"), (12, "上市前股东股份")):
        unlock = listed_on + timedelta(days=round(months * 30.44))
        if -10 <= (unlock - today).days <= 21:
            return Advice("warn", f"约 {unlock:%Y-%m-%d} 有{who}解禁（上市满 {months} 个月），"
                                  "前后常有抛压，暂缓买入。准确日期以公告为准。")
    if days < 90:
        return Advice("info", "上市不满 3 个月：先观察，至少等第一份定期报告。")
    closes = closes.dropna() if closes is not None else None
    if closes is None or len(closes) < 50:
        return Advice("info", "价格数据不足，暂时无法判断趋势。")
    if float(closes.iloc[-1]) > float(closes.iloc[-50:].mean()):
        return Advice("ok", "趋势向好：股价在 50 日均线之上。可以小仓位关注（单只不超过总资金的 5%）。")
    return Advice("warn", "趋势偏弱：股价在 50 日均线之下，暂不考虑。")
