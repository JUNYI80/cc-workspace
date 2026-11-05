"""Probe data sources from a machine with normal internet access (GitHub Actions).

Round 2: China sources, with a hard timeout on every probe.
"""
import socket
import sys
import threading
import time
import traceback

import requests

socket.setdefaulttimeout(25)
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PROBES = []


def probe(name, limit=90):
    def wrap(fn):
        PROBES.append((name, limit, fn))
        return fn
    return wrap


def show(resp, n=800):
    print("status", resp.status_code, "type", resp.headers.get("content-type"), "len", len(resp.content))
    print(resp.text[:n].replace("\n", " "))


@probe("BaoStock login + small query", 120)
def _():
    import baostock as bs
    t0 = time.time()
    lg = bs.login()
    print("login", lg.error_code, lg.error_msg, f"{time.time()-t0:.1f}s")
    t0 = time.time()
    rs = bs.query_history_k_data_plus("sh.600519", "date,code,close,turn,tradestatus,pctChg,peTTM,pbMRQ,isST",
                                      start_date="2026-10-01", end_date="2026-11-04", frequency="d", adjustflag="1")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    print("k", rs.error_code, rs.error_msg, len(rows), rows[-2:], f"{time.time()-t0:.1f}s")
    t0 = time.time()
    rs = bs.query_hs300_stocks(date="2020-06-30")
    n = 0
    first = None
    while rs.error_code == "0" and rs.next():
        n += 1
        first = first or rs.get_row_data()
    print("hs300@2020-06-30", rs.fields, n, first, f"{time.time()-t0:.1f}s")
    t0 = time.time()
    rs = bs.query_history_k_data_plus("sz.000001", "date,close,turn,peTTM,isST", start_date="2015-01-01",
                                      end_date="2026-11-04", frequency="d", adjustflag="1")
    m = 0
    while rs.error_code == "0" and rs.next():
        m += 1
    print("10y k-data rows", m, f"{time.time()-t0:.1f}s")
    rs = bs.query_stock_basic()
    k = 0
    sample = []
    while rs.error_code == "0" and rs.next():
        k += 1
        if k <= 2:
            sample.append(rs.get_row_data())
    print("stock_basic", rs.fields, k, sample)
    bs.logout()


@probe("Tushare reachability")
def _():
    r = requests.post("https://api.tushare.pro", json={"api_name": "trade_cal", "token": "", "params": {}}, timeout=20)
    show(r, 300)


@probe("Yahoo A-shares")
def _():
    import yfinance as yf
    df = yf.download(["600519.SS", "000001.SZ", "000300.SS", "300750.SZ", "688981.SS"], period="1mo",
                     auto_adjust=True, progress=False)
    print(df["Close"].tail(3))
    print(df["Volume"].tail(2))
    info = yf.Ticker("600519.SS").info
    print({k: info.get(k) for k in ["shortName", "trailingPE", "sharesOutstanding", "floatShares", "currency"]})


@probe("CSI official constituents xls (CSI 300)")
def _():
    import io
    import pandas as pd
    for url in [
        "https://csi-web-dev.oss-cn-shanghai-finance-1-pub.aliyuncs.com/static/html/csindex/public/uploads/file/autofile/cons/000300cons.xls",
        "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/000300cons.xls",
    ]:
        try:
            r = requests.get(url, headers={"User-Agent": BROWSER}, timeout=25)
            print(url[:60], r.status_code, r.headers.get("content-type"), len(r.content))
            if r.status_code == 200:
                df = pd.read_excel(io.BytesIO(r.content))
                print(df.columns.tolist())
                print(df.head(3).to_string())
                print("rows", len(df))
                break
        except Exception as e:
            print("fail", url[:60], e)


@probe("cninfo announcement search")
def _():
    r = requests.post("http://www.cninfo.com.cn/new/hisAnnouncement/query",
                      data={"pageNum": 1, "pageSize": 5, "column": "szse", "tabName": "fulltext",
                            "searchkey": "首次公开发行", "seDate": "2026-10-01~2026-11-04", "isHLtitle": "true"},
                      headers={"User-Agent": BROWSER}, timeout=25)
    show(r, 1500)


@probe("SZSE IPO project dynamics")
def _():
    r = requests.get("https://listing.szse.cn/api/ras/projectrends/query",
                     params={"bizType": 1, "pageIndex": 0, "pageSize": 3}, headers={"User-Agent": BROWSER}, timeout=25)
    show(r, 2500)


@probe("SZSE IPO project search by name")
def _():
    r = requests.get("https://listing.szse.cn/api/ras/projectrends/query",
                     params={"bizType": 1, "pageIndex": 0, "pageSize": 3, "keyword": "科技"},
                     headers={"User-Agent": BROWSER}, timeout=25)
    show(r, 1200)


@probe("SSE IPO projects (STAR)")
def _():
    r = requests.get("https://query.sse.com.cn/statusAction.do",
                     params={"isPagination": "true", "sqlId": "SH_XM_LB", "pageHelp.pageSize": 3, "pageHelp.pageNo": 1},
                     headers={"User-Agent": BROWSER, "Referer": "https://kcb.sse.com.cn/"}, timeout=25)
    show(r, 2500)


@probe("SSE main-board IPO projects")
def _():
    r = requests.get("https://query.sse.com.cn/statusAction.do",
                     params={"isPagination": "true", "sqlId": "GP_GPZCZ_SHXXPL", "pageHelp.pageSize": 3,
                             "pageHelp.pageNo": 1},
                     headers={"User-Agent": BROWSER, "Referer": "https://www.sse.com.cn/"}, timeout=25)
    show(r, 1200)


@probe("Nasdaq IPO calendar rows")
def _():
    r = requests.get("https://api.nasdaq.com/api/ipo/calendar?date=2026-10",
                     headers={"User-Agent": BROWSER, "Accept": "application/json"}, timeout=20)
    d = r.json()["data"]
    for k in ["priced", "upcoming", "filed", "withdrawn"]:
        sec = d.get(k) or {}
        rows = sec.get("rows") if "rows" in sec else (sec.get("upcomingTable") or {}).get("rows")
        print(k, list(sec.keys()), (rows or [])[:1], "n", len(rows or []))


@probe("HKEX")
def _():
    r = requests.get("https://www1.hkexnews.hk/app/appindex.html", headers={"User-Agent": BROWSER}, timeout=25)
    print("status", r.status_code, len(r.content))


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
