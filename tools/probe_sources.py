"""Probe each data source from a machine with normal internet access (GitHub Actions).

Prints status codes and the shape of each response so parsers can be written
against real payloads. Never fails the job: every probe is isolated.
"""
import io
import json
import sys
import time
import traceback

import requests

UA_SEC = "cc-workspace research tool (github.com/JUNYI80/cc-workspace)"
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def probe(name):
    def wrap(fn):
        print(f"\n===== {name} =====", flush=True)
        t0 = time.time()
        try:
            fn()
        except Exception:
            traceback.print_exc(limit=2, file=sys.stdout)
        print(f"----- {name}: {time.time() - t0:.1f}s", flush=True)
        return fn
    return wrap


def show(resp, n=600):
    print("status", resp.status_code, "type", resp.headers.get("content-type"), "len", len(resp.content))
    print(resp.text[:n].replace("\n", " "))


@probe("yfinance prices")
def _():
    import yfinance as yf
    df = yf.download(["SPY", "AAPL", "BRK-B"], period="10d", auto_adjust=True, progress=False)
    print(df["Close"].tail(3))


@probe("yfinance info")
def _():
    import yfinance as yf
    info = yf.Ticker("AAPL").info
    print({k: info.get(k) for k in ["shortName", "sector", "trailingPE", "forwardPE", "profitMargins", "currency"]})


@probe("SSGA SPY holdings xlsx")
def _():
    url = "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"
    r = requests.get(url, headers={"User-Agent": BROWSER}, timeout=30)
    print("status", r.status_code, r.headers.get("content-type"), len(r.content))
    import pandas as pd
    df = pd.read_excel(io.BytesIO(r.content), header=None)
    print(df.head(8).to_string()[:1500])
    print("rows", len(df))


@probe("iShares IVV holdings csv")
def _():
    url = ("https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf/1467271812596.ajax"
           "?fileType=csv&fileName=IVV_holdings&dataType=fund")
    r = requests.get(url, headers={"User-Agent": BROWSER}, timeout=30)
    show(r, 1200)


@probe("SEC company_tickers.json")
def _():
    r = requests.get("https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": UA_SEC}, timeout=30)
    print("status", r.status_code, len(r.content))
    d = r.json()
    print(list(d.items())[:3], "count", len(d))


@probe("SEC submissions AAPL")
def _():
    r = requests.get("https://data.sec.gov/submissions/CIK0000320193.json", headers={"User-Agent": UA_SEC}, timeout=30)
    d = r.json()
    print("status", r.status_code, list(d.keys()))
    rec = d["filings"]["recent"]
    print({k: rec[k][:3] for k in ["form", "filingDate", "accessionNumber", "primaryDocument"]})


@probe("SEC companyfacts AAPL")
def _():
    r = requests.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json", headers={"User-Agent": UA_SEC}, timeout=60)
    d = r.json()
    gaap = d["facts"]["us-gaap"]
    print("status", r.status_code, "n tags", len(gaap))
    for tag in ["NetIncomeLoss", "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "EarningsPerShareDiluted"]:
        if tag in gaap:
            units = gaap[tag]["units"]
            u = list(units)[0]
            print(tag, u, units[u][-3:])


@probe("SEC EFTS full-text search 424B4 recent")
def _():
    params = {"q": "", "forms": "424B4", "dateRange": "custom", "startdt": "2026-09-01", "enddt": "2026-11-04"}
    r = requests.get("https://efts.sec.gov/LATEST/search-index", params=params, headers={"User-Agent": UA_SEC}, timeout=30)
    print("status", r.status_code)
    d = r.json()
    hits = d.get("hits", {}).get("hits", [])
    print("total", d.get("hits", {}).get("total"), "n", len(hits))
    for h in hits[:5]:
        s = h["_source"]
        print(h["_id"], s.get("display_names"), s.get("form"), s.get("file_date"), s.get("root_forms"))


@probe("SEC EFTS S-1 Anthropic")
def _():
    params = {"q": '"Anthropic"', "forms": "S-1,S-1/A,F-1,DRS", "dateRange": "custom", "startdt": "2024-01-01", "enddt": "2026-11-04"}
    r = requests.get("https://efts.sec.gov/LATEST/search-index", params=params, headers={"User-Agent": UA_SEC}, timeout=30)
    d = r.json()
    hits = d.get("hits", {}).get("hits", [])
    print("status", r.status_code, "total", d.get("hits", {}).get("total"))
    for h in hits[:8]:
        s = h["_source"]
        print(s.get("display_names"), s.get("form"), s.get("file_date"))


@probe("SEC current filings atom S-1")
def _():
    url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=S-1&company=&dateb=&owner=include&start=0&count=40&output=atom"
    r = requests.get(url, headers={"User-Agent": UA_SEC}, timeout=30)
    show(r, 1500)


@probe("SEC 424B4 document sample")
def _():
    params = {"q": "", "forms": "424B4", "dateRange": "custom", "startdt": "2026-10-01", "enddt": "2026-11-04"}
    d = requests.get("https://efts.sec.gov/LATEST/search-index", params=params, headers={"User-Agent": UA_SEC}, timeout=30).json()
    h = d["hits"]["hits"][0]
    s = h["_source"]
    cik = int(s["ciks"][0]); acc = s["adsh"].replace("-", ""); doc = h["_id"].split(":", 1)[1]
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
    r = requests.get(url, headers={"User-Agent": UA_SEC}, timeout=30)
    import re
    text = re.sub(r"<[^>]+>", " ", r.text)
    text = re.sub(r"\s+", " ", text)
    print(url, r.status_code, len(text))
    i = text.lower().find("public offering price")
    print(text[max(0, i - 400): i + 400])


@probe("Google News RSS en")
def _():
    r = requests.get("https://news.google.com/rss/search?q=%22Anthropic%22+IPO&hl=en-GB&gl=GB&ceid=GB:en", timeout=30)
    show(r, 800)


@probe("Google News RSS zh")
def _():
    r = requests.get("https://news.google.com/rss/search?q=%E5%AE%87%E6%A0%91%E7%A7%91%E6%8A%80+IPO&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", timeout=30)
    show(r, 800)


@probe("Nasdaq IPO calendar (plain requests)")
def _():
    r = requests.get("https://api.nasdaq.com/api/ipo/calendar?date=2026-10",
                     headers={"User-Agent": BROWSER, "Accept": "application/json"}, timeout=20)
    show(r, 400)


@probe("BaoStock")
def _():
    import baostock as bs
    lg = bs.login()
    print("login", lg.error_code, lg.error_msg)
    rs = bs.query_hs300_stocks()
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    print("hs300", rs.fields, len(rows), rows[:3])
    rs = bs.query_hs300_stocks(date="2018-06-30")
    n = 0
    while rs.error_code == "0" and rs.next():
        n += 1
    print("hs300 2018-06-30 count", n, rs.error_msg)
    t0 = time.time()
    rs = bs.query_history_k_data_plus("sh.600519", "date,code,close,volume,amount,turn,tradestatus,pctChg,peTTM,pbMRQ,isST",
                                      start_date="2016-01-01", end_date="2026-11-04", frequency="d", adjustflag="2")
    data = []
    while rs.error_code == "0" and rs.next():
        data.append(rs.get_row_data())
    print("k-data", rs.error_code, rs.error_msg, len(data), f"{time.time()-t0:.1f}s", data[-2:])
    rs = bs.query_history_k_data_plus("sh.000300", "date,code,close", start_date="2026-10-01", end_date="2026-11-04", frequency="d")
    idx = []
    while rs.error_code == "0" and rs.next():
        idx.append(rs.get_row_data())
    print("index", idx[-2:])
    rs = bs.query_stock_basic(code="sh.600519")
    print("basic", rs.fields, rs.get_row_data() if rs.next() else None)
    bs.logout()


@probe("cninfo announcement search")
def _():
    r = requests.post("http://www.cninfo.com.cn/new/hisAnnouncement/query",
                      data={"pageNum": 1, "pageSize": 10, "column": "szse", "tabName": "fulltext",
                            "searchkey": "招股说明书", "seDate": "2026-10-01~2026-11-04", "isHLtitle": "true"},
                      headers={"User-Agent": BROWSER}, timeout=30)
    show(r, 1200)


@probe("SZSE IPO project dynamics")
def _():
    r = requests.get("https://listing.szse.cn/api/ras/projectrends/query",
                     params={"bizType": 1, "pageIndex": 0, "pageSize": 5}, headers={"User-Agent": BROWSER}, timeout=30)
    show(r, 1500)


@probe("SSE STAR IPO projects")
def _():
    r = requests.get("https://query.sse.com.cn/statusAction.do",
                     params={"isPagination": "true", "sqlId": "SH_XM_LB", "pageHelp.pageSize": 5, "pageHelp.pageNo": 1},
                     headers={"User-Agent": BROWSER, "Referer": "https://kcb.sse.com.cn/"}, timeout=30)
    show(r, 1500)


@probe("CSRC site")
def _():
    r = requests.get("http://www.csrc.gov.cn/csrc/c100028/common_list.shtml", headers={"User-Agent": BROWSER}, timeout=30)
    show(r, 300)


@probe("Wikipedia S&P 500")
def _():
    r = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers={"User-Agent": BROWSER}, timeout=30)
    print("status", r.status_code, len(r.content))
