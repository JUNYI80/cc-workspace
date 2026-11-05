"""Probe China data sources from GitHub Actions (round 3): exact fields and speed."""
import io
import json
import socket
import sys
import threading
import time
import traceback

import requests

socket.setdefaulttimeout(30)
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PROBES = []


def probe(name, limit=120):
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


@probe("CSI constituents 000300 / 000905 (xlrd)")
def _():
    import pandas as pd
    for code in ["000300", "000905"]:
        url = f"https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{code}cons.xls"
        r = get(url, headers={"User-Agent": BROWSER})
        df = pd.read_excel(io.BytesIO(r.content), dtype=str)
        print(code, r.status_code, df.shape, df.columns.tolist())
        print(df.head(3).to_string())


@probe("BaoStock 10y k-data x5, stock_basic, trade dates", 300)
def _():
    import baostock as bs
    bs.login()
    for code in ["sh.600519", "sz.000001", "sz.300750", "sh.688981", "sh.601318"]:
        t0 = time.time()
        rs = bs.query_history_k_data_plus(code, "date,code,close,turn,tradestatus,pctChg,peTTM,isST",
                                          start_date="2015-01-01", end_date="2026-11-04", frequency="d",
                                          adjustflag="1")
        n = 0
        while rs.error_code == "0" and rs.next():
            n += 1
        print(code, rs.error_code, n, "rows", f"{time.time()-t0:.2f}s")
    t0 = time.time()
    rs = bs.query_history_k_data_plus("sh.000300", "date,code,close", start_date="2015-01-01",
                                      end_date="2026-11-04", frequency="d")
    n = 0
    while rs.error_code == "0" and rs.next():
        n += 1
    print("index sh.000300", n, f"{time.time()-t0:.2f}s")
    t0 = time.time()
    rs = bs.query_stock_basic()
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    print("stock_basic", rs.error_code, rs.fields, len(rows), rows[:2], rows[-2:], f"{time.time()-t0:.2f}s")
    recent = sorted([r for r in rows if r[4] == "1" and r[2] >= "2026-06-01"], key=lambda r: r[2])[-5:]
    print("recent listings", recent)
    bs.logout()


@probe("BaoStock hs300 constituents (long wait)", 240)
def _():
    import baostock as bs
    bs.login()
    t0 = time.time()
    rs = bs.query_hs300_stocks()
    n = 0
    while rs.error_code == "0" and rs.next():
        n += 1
    print("hs300 now", rs.error_code, rs.error_msg, n, f"{time.time()-t0:.1f}s")
    bs.logout()


@probe("SZSE IPO projects: row fields")
def _():
    r = get("https://listing.szse.cn/api/ras/projectrends/query",
            params={"bizType": 1, "pageIndex": 0, "pageSize": 2, "keyword": ""}, headers={"User-Agent": BROWSER})
    d = r.json()
    print("top keys", list(d.keys()))
    rows = d.get("data") or []
    print("n", len(rows))
    for row in rows[:2]:
        print(json.dumps(row, ensure_ascii=False)[:1500])
    r = get("https://listing.szse.cn/api/ras/projectrends/query",
            params={"bizType": 1, "pageIndex": 0, "pageSize": 5, "keyword": "宇树"}, headers={"User-Agent": BROWSER})
    d = r.json()
    print("search 宇树:", d.get("totalSize"), [(x.get("cmpnm"), x.get("prjst")) for x in d.get("data") or []])


@probe("SSE STAR IPO projects: row fields")
def _():
    r = get("https://query.sse.com.cn/statusAction.do",
            params={"isPagination": "true", "sqlId": "SH_XM_LB", "pageHelp.pageSize": 2, "pageHelp.pageNo": 1,
                    "keyword": ""},
            headers={"User-Agent": BROWSER, "Referer": "https://kcb.sse.com.cn/"})
    d = r.json()
    print("top keys", list(d.keys()))
    res = d.get("result") or (d.get("pageHelp") or {}).get("data") or []
    print("n", len(res), "total", (d.get("pageHelp") or {}).get("total"))
    for row in res[:2]:
        print(json.dumps(row, ensure_ascii=False)[:1500])
    r = get("https://query.sse.com.cn/statusAction.do",
            params={"isPagination": "true", "sqlId": "SH_XM_LB", "pageHelp.pageSize": 5, "pageHelp.pageNo": 1,
                    "keyword": "宇树"},
            headers={"User-Agent": BROWSER, "Referer": "https://kcb.sse.com.cn/"})
    d = r.json()
    res = d.get("result") or []
    print("search 宇树:", [(x.get("stockAuditName"), x.get("currStatus")) for x in res][:5])


@probe("SSE main board IPO projects: which sqlId")
def _():
    for sql in ["GP_GPZCZ_SHXXPL", "SH_XM_LB_ZB", "ZB_XM_LB"]:
        r = get("https://query.sse.com.cn/statusAction.do",
                params={"isPagination": "true", "sqlId": sql, "pageHelp.pageSize": 2, "pageHelp.pageNo": 1},
                headers={"User-Agent": BROWSER, "Referer": "https://www.sse.com.cn/"})
        try:
            d = r.json()
            res = d.get("result") or []
            print(sql, r.status_code, "keys", list(d.keys())[:8], "n", len(res))
            if res:
                print(json.dumps(res[0], ensure_ascii=False)[:800])
        except Exception as e:
            print(sql, r.status_code, "not json", r.text[:200])


@probe("cninfo: IPO prospectus / listing announcements")
def _():
    for key in ["招股说明书", "上市公告书", "发行公告"]:
        r = requests.post("http://www.cninfo.com.cn/new/hisAnnouncement/query",
                          data={"pageNum": 1, "pageSize": 5, "column": "szse", "tabName": "fulltext",
                                "searchkey": key, "seDate": "2026-09-01~2026-11-04", "isHLtitle": "true"},
                          headers={"User-Agent": BROWSER}, timeout=25)
        d = r.json()
        print(key, "total", d.get("totalAnnouncement"))
        for a in (d.get("announcements") or [])[:3]:
            print("  ", a.get("secCode"), a.get("secName"), a.get("announcementTitle"), a.get("announcementTime"),
                  a.get("adjunctUrl"), a.get("columnId"))


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
