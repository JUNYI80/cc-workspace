"""B-class: IPO tracking.

Sources (all free, no key):
  * Nasdaq IPO calendar: filed / upcoming / priced / withdrawn deals by month.
  * SEC EDGAR full-text search: S-1 / F-1 registration statements, which a
    company must file publicly before listing in the US.
  * Google News headlines (see news.py).

For private companies on a watchlist (e.g. Anthropic) the module estimates
how far along the road to an IPO they are: official data (SEC, Nasdaq) when
there is any, otherwise keywords in recent headlines. For recently listed
companies it applies a few evidence-based rules (don't chase day one, beware
lock-up expiry, wait for an uptrend).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from . import news

log = logging.getLogger(__name__)

NASDAQ_URL = "https://api.nasdaq.com/api/ipo/calendar"
NASDAQ_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
REGISTRATION_FORMS = "S-1,S-1/A,F-1,F-1/A"
LOCKUP_DAYS = 180  # typical; the real date is in each company's prospectus
NEWS_WINDOW_DAYS = 180

# "Name | alias | alias": aliases catch legal names in SEC and Nasdaq data.
DEFAULT_WATCHLIST = [
    "Anthropic",
    "OpenAI",
    "SpaceX | Space Exploration Technologies",
    "Databricks",
    "Stripe",
]

STAGES = [
    "暂无上市消息",
    "有上市相关报道",
    "据报道正在准备上市",
    "据报道已秘密递交申请",
    "已公开递交上市申请",
    "即将定价上市",
    "已经上市",
]
# Checked from the highest stage down; a headline must first be about an IPO at all.
# Headlines never set stage 6 ("listed"): "plans stock market debut" reads like a
# listing to a keyword match. Only the Nasdaq calendar can say a company is listed.
NEWS_RULES = [
    (5, r"roadshow|road show|sets terms|price range|launches (its )?ipo|kicks off (its )?ipo"),
    (4, r"publicly fil|\bs-1\b|\bf-1\b"),
    (3, r"confidential|draft registration"),  # "confidentially files" is not a public filing
    (4, r"files for (an |its )?ipo|filed for (an |its )?ipo|ipo filing"),
    (2, r"\bhire|\bhiring|\btaps\b|\bbanks?\b|bankers|underwrit|prepar|consider|weigh|explor"
        r"|advis|in talks|lines up|picks|\bplans?\b|debut|valuation"),
]
IPO_TOPIC = re.compile(r"\bipo\b|go(ing)? public|public listing|stock market listing|listing on|"
                       r"initial public offering|(stock|market|trading|public) debut", re.I)
NEGATION = re.compile(r"no plans?|not plan|rules? out|ruled out|no rush|not in a (rush|hurry)|"
                      r"denies|not considering|no intention", re.I)


# -- helpers --------------------------------------------------------------------

def _get_json(url: str, params: dict, headers: dict, timeout: float = 20) -> dict:
    """GET JSON, impersonating a browser's TLS when curl_cffi is available (Nasdaq needs it)."""
    try:
        from curl_cffi import requests as cffi

        resp = cffi.get(url, params=params, headers=headers, impersonate="chrome", timeout=timeout)
    except ImportError:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from {url}")
    return resp.json()


def _date(value) -> date | None:
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _number(value) -> float | None:
    if value in (None, "", "N/A", "--"):
        return None
    try:
        return float(re.sub(r"[$,\s]", "", str(value)))
    except ValueError:
        return None


def _price_range(value) -> tuple[float | None, float | None]:
    if not value:
        return None, None
    parts = [p for p in re.split(r"\s*-\s*", str(value)) if p]
    nums = [_number(p) for p in parts]
    nums = [n for n in nums if n is not None]
    if not nums:
        return None, None
    return min(nums), max(nums)


def aliases(entry: str) -> list[str]:
    return [a.strip() for a in entry.split("|") if a.strip()]


def _mentions(text: str, names: list[str]) -> bool:
    t = text.lower()
    return any(n.lower() in t for n in names)


# -- Nasdaq calendar ----------------------------------------------------------------

def parse_nasdaq(data: dict) -> pd.DataFrame:
    """Rows from one month's Nasdaq calendar payload (the `data` object)."""
    sections = {
        "filed": (data.get("filed") or {}).get("rows"),
        "upcoming": ((data.get("upcoming") or {}).get("upcomingTable") or {}).get("rows"),
        "priced": (data.get("priced") or {}).get("rows"),
        "withdrawn": (data.get("withdrawn") or {}).get("rows"),
    }
    date_key = {"filed": "filedDate", "upcoming": "expectedPriceDate", "priced": "pricedDate",
                "withdrawn": "withdrawDate"}
    rows = []
    for status, items in sections.items():
        for r in items or []:
            low, high = _price_range(r.get("proposedSharePrice"))
            rows.append({
                "status": status,
                "symbol": (r.get("proposedTickerSymbol") or "").strip() or None,
                "company": (r.get("companyName") or "").strip(),
                "exchange": r.get("proposedExchange"),
                "price_low": low,
                "price_high": high,
                "shares": _number(r.get("sharesOffered")),
                "value_usd": _number(r.get("dollarValueOfSharesOffered")),
                "date": _date(r.get(date_key[status])),
            })
    cols = ["status", "symbol", "company", "exchange", "price_low", "price_high", "shares",
            "value_usd", "date"]
    return pd.DataFrame(rows, columns=cols)


def fetch_calendar(months_back: int = 6, months_ahead: int = 1, today: date | None = None,
                   fetch=None) -> pd.DataFrame:
    """Nasdaq IPO calendar over a rolling window of months, newest first."""
    today = today or date.today()
    fetch = fetch or (lambda m: _get_json(NASDAQ_URL, {"date": m}, NASDAQ_HEADERS).get("data") or {})
    start = pd.Period(today, "M")
    months = [str(start + k) for k in range(-months_back, months_ahead + 1)]
    frames, errors = [], []
    for m in months:
        try:
            frames.append(parse_nasdaq(fetch(m)))
        except Exception as exc:
            errors.append(f"{m}: {exc}")
    if not frames:
        raise RuntimeError("Nasdaq IPO calendar unavailable: " + "; ".join(errors))
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["status", "company", "symbol"])
    return df.sort_values("date", ascending=False, na_position="last").reset_index(drop=True)


# -- SEC filings ----------------------------------------------------------------------

@dataclass(frozen=True)
class Filing:
    company: str
    form: str
    filed: str
    url: str


def parse_efts(data: dict, names: list[str]) -> list[Filing]:
    """Registration statements *filed by* the company (not ones that merely mention it)."""
    out, seen = [], set()
    for hit in (data.get("hits") or {}).get("hits") or []:
        src = hit.get("_source") or {}
        filers = src.get("display_names") or []
        if not any(_mentions(f, names) for f in filers):
            continue
        acc = src.get("adsh") or ""
        if acc in seen:
            continue
        seen.add(acc)
        ciks = src.get("ciks") or []
        doc = (hit.get("_id") or "").split(":", 1)[-1]
        url = (SEC_ARCHIVE.format(cik=int(ciks[0]), acc=acc.replace("-", ""), doc=doc)
               if ciks and acc and doc else "")
        out.append(Filing(re.sub(r"\s*\(CIK.*", "", filers[0]).strip(), src.get("form", ""),
                          src.get("file_date", ""), url))
    return sorted(out, key=lambda f: f.filed, reverse=True)


def sec_filings(entry: str, user_agent: str, since: str = "2015-01-01",
                today: date | None = None, fetch=None) -> list[Filing]:
    names = aliases(entry)
    today = today or date.today()
    fetch = fetch or (lambda params: _get_json(EFTS_URL, params, {"User-Agent": user_agent}))
    found: list[Filing] = []
    for name in names:
        params = {"q": f'"{name}"', "forms": REGISTRATION_FORMS, "dateRange": "custom",
                  "startdt": since, "enddt": today.isoformat()}
        found += parse_efts(fetch(params), names)
    unique = {f.url or (f.form, f.filed): f for f in found}
    return sorted(unique.values(), key=lambda f: f.filed, reverse=True)


# -- watchlist stage ---------------------------------------------------------------

def news_stage(headline: str) -> int:
    """0 if the headline isn't about an IPO; otherwise the stage it suggests."""
    if not IPO_TOPIC.search(headline):
        return 0
    if NEGATION.search(headline):
        return 1
    for stage, pattern in NEWS_RULES:
        if re.search(pattern, headline, re.I):
            return stage
    return 1


@dataclass
class WatchStatus:
    name: str
    stage: int
    official: bool  # stage comes from SEC/Nasdaq data rather than headlines
    evidence: str
    heat: int  # IPO headlines in the last 30 days
    headlines: list[news.Headline] = field(default_factory=list)
    filings: list[Filing] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return STAGES[self.stage] + ("" if self.official or self.stage == 0 else "（据新闻，未经证实）")


def watch_status(entry: str, headlines: list[news.Headline], filings: list[Filing],
                 calendar: pd.DataFrame | None, now: datetime | None = None,
                 errors: list[str] | None = None) -> WatchStatus:
    names = aliases(entry)
    now = now or datetime.now(timezone.utc)
    stage, official, evidence = 0, False, ""

    if calendar is not None and not calendar.empty:
        hits = calendar[calendar["company"].map(lambda c: _mentions(c, names))]
        rank = {"filed": 4, "upcoming": 5, "priced": 6}
        words = {"filed": "已递交申请", "upcoming": "即将定价", "priced": "已定价上市"}
        for _, r in hits.iterrows():
            s = rank.get(r["status"], 0)
            if s > stage:
                stage, official = s, True
                evidence = f"Nasdaq IPO 日历：{r['company']}（{words[r['status']]}，{r['date']}）"
    if filings and stage < 4:
        f = filings[0]
        stage, official, evidence = 4, True, f"SEC 文件：{f.form}，{f.filed}"

    recent = [h for h in headlines
              if h.published and (now - h.published).days <= NEWS_WINDOW_DAYS and _mentions(h.title, names)]
    heat = sum(1 for h in recent if (now - h.published).days <= 30 and news_stage(h.title) > 0)
    if not official:
        for h in recent:
            s = news_stage(h.title)
            if s > stage:
                stage, evidence = s, f"新闻（{h.published:%Y-%m-%d}）：{h.title}"
    return WatchStatus(names[0], stage, official, evidence, heat, recent[:8], filings, errors or [])


# -- recently listed companies -----------------------------------------------------

@dataclass(frozen=True)
class Advice:
    level: str  # "ok", "info" or "warn"
    text: str


def post_ipo_advice(ipo_date: date, ipo_price: float | None, closes: pd.Series | None,
                    today: date | None = None) -> Advice:
    today = today or date.today()
    days = (today - ipo_date).days
    lockup = ipo_date + timedelta(days=LOCKUP_DAYS)
    to_lockup = (lockup - today).days
    if days <= 1:
        return Advice("warn", "刚上市：不追首日。首日涨幅大多被拿到配售的机构赚走，"
                              "研究显示新股长期平均跑输大盘。")
    if -10 <= to_lockup <= 21:
        return Advice("warn", f"锁定期预计 {lockup:%Y-%m-%d} 到期：前后常有内部人抛售，暂缓买入。")
    if days < 90:
        return Advice("info", "上市不满 3 个月：先观察，至少等第一份季度财报出来。")
    closes = closes.dropna() if closes is not None else None
    if closes is None or len(closes) < 50:
        return Advice("info", "价格数据不足，暂时无法判断趋势。")
    last, sma50 = float(closes.iloc[-1]), float(closes.iloc[-50:].mean())
    above_ipo = ipo_price is None or last > ipo_price
    if last > sma50 and above_ipo:
        return Advice("ok", "趋势向好：股价在 50 日均线和发行价之上。可以小仓位关注"
                            "（单只不超过总资金的 5%）。")
    return Advice("warn", "趋势偏弱：股价低于 50 日均线或发行价，暂不考虑。")


def recent_ipos(calendar: pd.DataFrame, today: date | None = None, max_age_days: int = 365,
                min_value_usd: float = 50e6) -> pd.DataFrame:
    """Priced deals from the last year that raised at least `min_value_usd`."""
    today = today or date.today()
    df = calendar[(calendar["status"] == "priced") & calendar["symbol"].notna()].copy()
    df = df[df["date"].map(lambda d: d is not None and 0 <= (today - d).days <= max_age_days)]
    df = df[df["value_usd"].fillna(0) >= min_value_usd]
    df["ipo_price"] = df["price_high"]  # priced rows carry the final price in this field
    return df.reset_index(drop=True)


def upcoming_ipos(calendar: pd.DataFrame, today: date | None = None, days: int = 21) -> pd.DataFrame:
    today = today or date.today()
    df = calendar[calendar["status"] == "upcoming"]
    keep = df["date"].map(lambda d: d is None or 0 <= (d - today).days <= days)
    return df[keep].reset_index(drop=True)
