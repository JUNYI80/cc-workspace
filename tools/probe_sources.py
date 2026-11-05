"""Probe China data sources from GitHub Actions (round 4)."""
import io
import json
import re
import socket
import sys
import threading
import time
import traceback

import requests

socket.setdefaulttimeout(30)
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PROBES = []


def probe(name, limit=180):
    def wrap(fn):
        PROBES.append((name, limit, fn))
        return fn
    return wrap


def get(url, tries=3, **kw):
    for i in range(tries):
        try:
            return requests.get(url, timeout=25, **kw)
        except requests.ConnectionError as e:
            print("  retry after", e.__class__.__name__)
            time.sleep(2 * (i + 1))
    raise RuntimeError("failed after retries")


def cons(code):
    import pandas as pd
    url = f"https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{code}cons.xls"
    r = get(url, headers={"User-Agent": BROWSER})
    return pd.read_excel(io.BytesIO(r.content), dtype=str, engine="calamine")


@probe("calamine + Yahoo bulk CSI 800 since 2014", 600)
def _():
    import pandas as pd
    import yfinance as yf
    df = pd.concat([cons("000300"), cons("000905")])
    codes = df["成份券代码Constituent Code"].str.zfill(6).tolist()
    tick = [c + (".SS" if c.startswith("6") else ".SZ") for c in codes]
    print("constituents", len(tick), tick[:5])
    t0 = time.time()
    raw = yf.download(tick, start="2014-01-01", auto_adjust=True, progress=False, threads=True)
    close, vol = raw["Close"], raw["Volume"]
    print("download", f"{time.time()-t0:.1f}s", close.shape, "last", close.index[-1].date())
    have = close.notna().sum()
    print("symbols with no data:", int((have == 0).sum()), list(have[have == 0].index[:10]))
    print("last row coverage", int(close.iloc[-1].notna().sum()), "prev", int(close.iloc[-2].notna().sum()))
    rets = close.pct_change(fill_method=None)
    big = (rets.abs() > 0.21).sum()
    print("days with |ret|>21% per symbol: total", int(big.sum()), "symbols", int((big > 0).sum()))
    print(rets.stack()[rets.stack().abs() > 0.21].sort_values().head(8))
    zero_vol = (vol == 0).sum().sum()
    print("zero-volume cells", int(zero_vol))
    print(close[["600519.SS", "000001.SZ", "300750.SZ"]].tail(3))


@probe("Yahoo info x20 A-shares (PE, sector)", 180)
def _():
    import yfinance as yf
    t0 = time.time()
    for t in ["600519.SS", "000001.SZ", "300750.SZ", "601318.SS", "000858.SZ", "600036.SS", "002594.SZ",
              "601899.SS", "688981.SS", "000333.SZ"]:
        i = yf.Ticker(t).info
        print(t, i.get("shortName"), i.get("trailingPE"), i.get("sector"), i.get("industry"), i.get("profitMargins"))
    print(f"{time.time()-t0:.1f}s for 10")


@probe("Benchmark ETFs on Yahoo", 60)
def _():
    import yfinance as yf
    df = yf.download(["510300.SS", "510500.SS", "000300.SS", "000905.SS"], start="2014-01-01",
                     auto_adjust=True, progress=False)["Close"]
    print(df.notna().sum())
    print(df.tail(4))


@probe("SSE status code names (from SSE site JS)", 120)
def _():
    r = get("https://kcb.sse.com.cn/renewal/", headers={"User-Agent": BROWSER})
    srcs = re.findall(r'src="([^"]+\.js[^"]*)"', r.text)
    print("scripts", srcs[:20])
    found = 0
    for src in srcs:
        url = src if src.startswith("http") else "https://kcb.sse.com.cn" + (src if src.startswith("/") else "/renewal/" + src)
        try:
            js = get(url, headers={"User-Agent": BROWSER}).text
        except Exception as e:
            print("fail", url, e)
            continue
        for m in re.finditer("已受理", js):
            print(url, "...", js[max(0, m.start() - 300): m.start() + 400].replace("\n", " "))
            found += 1
            if found > 4:
                return


@probe("SSE projects: status distribution", 120)
def _():
    from collections import Counter, defaultdict
    rows = []
    for page in range(1, 4):
        r = get("https://query.sse.com.cn/statusAction.do",
                params={"isPagination": "true", "sqlId": "SH_XM_LB", "pageHelp.pageSize": 100,
                        "pageHelp.pageNo": page, "keyword": ""},
                headers={"User-Agent": BROWSER, "Referer": "https://kcb.sse.com.cn/"})
        rows += r.json().get("result") or []
    c = Counter((x.get("currStatus"), x.get("registeResult"), x.get("suspendStatus"), x.get("projectType")) for x in rows)
    print(len(rows), c.most_common(20))
    names = defaultdict(list)
    for x in rows:
        names[x.get("currStatus")].append(x.get("stockAuditName"))
    for k, v in names.items():
        print(k, v[:3])
    print("keys", list(rows[0].keys()))
    print({k: rows[0].get(k) for k in ["auditApplyDate", "updateDate", "issueMarketType", "planIssueCapital", "stockAuditNum", "currStatus", "registeResult", "suspendStatus", "projectType"]})
    r = get("https://query.sse.com.cn/statusAction.do",
            params={"isPagination": "true", "sqlId": "SH_XM_LB", "pageHelp.pageSize": 5, "pageHelp.pageNo": 1,
                    "keyword": "宇树"},
            headers={"User-Agent": BROWSER, "Referer": "https://kcb.sse.com.cn/"})
    for x in r.json().get("result") or []:
        print("宇树:", {k: x.get(k) for k in ["stockAuditName", "currStatus", "registeResult", "suspendStatus", "updateDate", "auditApplyDate", "issueMarketType"]})


@probe("SZSE projects: stage list + search param", 120)
def _():
    r = get("https://listing.szse.cn/api/ras/projectrends/query",
            params={"bizType": 1, "pageIndex": 0, "pageSize": 1}, headers={"User-Agent": BROWSER})
    d = r.json()
    for st in d.get("stageList") or []:
        print("stage", st.get("stage"), st.get("stageName"), [(x.get("status"), x.get("statusName")) for x in st.get("projectStatusList") or []])
    for param in ["keywords", "keyword", "cmpnm", "searchKey"]:
        r = get("https://listing.szse.cn/api/ras/projectrends/query",
                params={"bizType": 1, "pageIndex": 0, "pageSize": 3, param: "季丰"}, headers={"User-Agent": BROWSER})
        d = r.json()
        print(param, d.get("totalSize"), [x.get("cmpnm") for x in d.get("data") or []])


@probe("cninfo: first-day listing announcements, both exchanges", 120)
def _():
    for column in ["szse", "sse"]:
        r = requests.post("http://www.cninfo.com.cn/new/hisAnnouncement/query",
                          data={"pageNum": 1, "pageSize": 30, "column": column, "tabName": "fulltext",
                                "searchkey": "首次公开发行股票上市公告书", "seDate": "2026-08-01~2026-11-04",
                                "isHLtitle": "true"},
                          headers={"User-Agent": BROWSER}, timeout=25)
        d = r.json()
        anns = d.get("announcements") or []
        print(column, "total", d.get("totalAnnouncement"))
        for a in anns[:8]:
            print("  ", a.get("secCode"), a.get("secName"), re.sub("<.*?>", "", a.get("announcementTitle") or ""),
                  time.strftime("%Y-%m-%d", time.gmtime(a.get("announcementTime", 0) / 1000)))
    r = requests.post("http://www.cninfo.com.cn/new/hisAnnouncement/query",
                      data={"pageNum": 1, "pageSize": 10, "column": "szse", "tabName": "fulltext",
                            "searchkey": "宇树科技", "seDate": "2025-01-01~2026-11-04", "isHLtitle": "true"},
                      headers={"User-Agent": BROWSER}, timeout=25)
    for a in (r.json().get("announcements") or [])[:8]:
        print("宇树:", a.get("secCode"), a.get("secName"), re.sub("<.*?>", "", a.get("announcementTitle") or ""))


@probe("Google News zh sources", 60)
def _():
    import xml.etree.ElementTree as ET
    r = get("https://news.google.com/rss/search?q=%E5%AE%87%E6%A0%91%E7%A7%91%E6%8A%80+%E4%B8%8A%E5%B8%82&hl=zh-CN&gl=CN&ceid=CN:zh-Hans")
    root = ET.fromstring(r.text)
    items = list(root.iter("item"))
    print(len(items))
    from collections import Counter
    print(Counter((i.findtext("source") or "") for i in items).most_common(25))
    for i in items[:5]:
        print(i.findtext("pubDate"), "|", i.findtext("source"), "|", i.findtext("title"))


def main():
    for name, limit, fn in PROBES:
        print(f"\n===== {name} =====", flush=True)
        t0 = time.time()
        done = threading.Event()

        def run():
            try:
                fn()
            except Exception:
                traceback.print_exc(limit=2, file=sys.stdout)
            finally:
                done.set()

        threading.Thread(target=run, daemon=True).start()
        if not done.wait(limit):
            print(f"TIMEOUT after {limit}s")
        print(f"----- {name}: {time.time() - t0:.1f}s", flush=True)
    sys.stdout.flush()
    import os
    os._exit(0)


main()
