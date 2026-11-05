"""Fixtures are trimmed copies of real responses fetched on 2026-11-04/05."""
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from cnquant import ipo, news

NOW = datetime(2026, 11, 5, 8, tzinfo=timezone.utc)
TODAY = date(2026, 11, 5)

SZSE = {"totalSize": 2, "data": [
    {"prjid": 1004709, "cmpnm": "南京沁恒微电子股份有限公司", "cmpsnm": "沁恒微", "prjstatus": 20,
     "prjst": "已受理", "csrcind": "软件和信息技术服务业", "sprinsts": "华泰联合", "maramt": "14.8614",
     "updtdt": "2026-11-03 18:00:00"},
    {"prjid": 1004414, "cmpnm": "上海季丰电子股份有限公司", "cmpsnm": "季丰电子", "prjstatus": 70,
     "prjst": "注册生效", "csrcind": "专业技术服务业", "sprinsts": "光大证券", "maramt": "9.2375",
     "updtdt": "2026-10-30 18:00:00"},
]}
SSE = {"result": [
    {"stockAuditName": "宇树科技股份有限公司", "currStatus": 5, "registeResult": "", "updateDate": "20260720100000",
     "planIssueCapital": 30.0},
    {"stockAuditName": "浙江金连接科技股份有限公司", "currStatus": 8, "registeResult": "", "updateDate": "20261102092640",
     "planIssueCapital": 10.2625},
]}
CNINFO = {"announcements": [
    {"secCode": "688836", "secName": "宇树科技", "announcementTitle": "<em>宇树科技</em>2026年第三季度报告",
     "announcementTime": 1793721600000, "adjunctUrl": "finalpage/2026-10-30/1.PDF"},
    {"secCode": "000917", "secName": "电广传媒", "announcementTitle": "关于参股公司宇树科技首次公开发行股票的公告",
     "announcementTime": 1793721600000, "adjunctUrl": "finalpage/2026-11-04/2.PDF"},
]}
LISTINGS = {"announcements": [
    {"secCode": "001381", "secName": "皇冠新材", "announcementTitle": "首次公开发行股票并在主板上市之上市公告书提示性公告",
     "announcementTime": 1793548800000, "adjunctUrl": "a.PDF"},
    {"secCode": "001381", "secName": "皇冠新材", "announcementTitle": "首次公开发行股票并在主板上市之上市公告书",
     "announcementTime": 1793548800000, "adjunctUrl": "b.PDF"},
    {"secCode": "301660", "secName": "粤芯半导体", "announcementTitle": "首次公开发行股票并在创业板上市之上市公告书",
     "announcementTime": 1791676800000, "adjunctUrl": "c.PDF"},
    {"secCode": "300018", "secName": "中元股份", "announcementTitle": "向特定对象发行股票上市公告书",
     "announcementTime": 1793548800000, "adjunctUrl": "d.PDF"},
]}


def test_parse_szse_statuses():
    p = ipo.parse_szse(SZSE)
    assert [(x.short_name, x.status, x.stage) for x in p] == [("沁恒微", "已受理", 3), ("季丰电子", "注册生效", 5)]
    assert p[0].amount_yi == pytest.approx(14.8614) and p[0].updated == "2026-11-03"


def test_parse_sse_statuses():
    p = ipo.parse_sse(SSE)
    assert (p[0].stage, p[0].status, p[0].updated) == (5, "注册结果", "2026-07-20")
    assert p[1].stage == 0 and p[1].status == "终止"


def test_cninfo_listing_beats_projects_and_ignores_other_companies():
    anns = ipo.parse_cninfo(CNINFO)
    s = ipo.watch_status("宇树科技", [], ipo.parse_sse(SSE)[:1], anns, now=NOW)
    assert s.stage == 6 and s.official and s.code == "688836"
    other_only = [a for a in anns if a.code == "000917"]  # 电广传媒 mentioning 宇树科技
    s2 = ipo.watch_status("宇树科技", [], [], other_only, now=NOW)
    assert s2.stage == 0


def test_terminated_project_is_reported_not_staged():
    s = ipo.watch_status("金连接", [], ipo.parse_sse(SSE)[1:], [], now=NOW)
    assert s.stage == 0 and "终止" in s.terminated


@pytest.mark.parametrize("title, stage", [
    ("智谱发布新模型", 0),
    ("月之暗面否认近期上市计划", 1),
    ("某公司完成上市辅导备案", 2),
    ("科创板IPO获受理", 3),
    ("创业板上市委审议通过", 4),
    ("首发注册生效 即将申购", 5),
])
def test_news_stage(title, stage):
    assert ipo.news_stage(title) == stage


def test_news_only_stage_is_unconfirmed():
    h = [news.Headline("智谱完成上市辅导备案", "https://x", NOW - timedelta(days=3), "证券时报")]
    s = ipo.watch_status("智谱", h, [], [], now=NOW)
    assert s.stage == 2 and not s.official and "未经证实" in s.label and s.heat == 1


def test_recent_listings_dedupes_and_skips_follow_ons():
    df = ipo.recent_listings(today=TODAY, fetch=lambda: LISTINGS)
    assert list(df["code"]) == ["001381", "301660"]
    assert list(df["board"]) == ["主板", "创业板"]


@pytest.mark.parametrize("days_ago, level, phrase", [
    (3, "warn", "前 5 个交易日"),
    (178, "warn", "解禁"),
    (60, "info", "不满 3 个月"),
])
def test_post_listing_advice(days_ago, level, phrase):
    adv = ipo.post_listing_advice(TODAY - timedelta(days=days_ago), None, today=TODAY)
    assert adv.level == level and phrase in adv.text


def test_post_listing_trend():
    up = pd.Series([10 + i * 0.1 for i in range(120)])
    assert ipo.post_listing_advice(TODAY - timedelta(days=130), up, today=TODAY).level == "ok"
    assert ipo.post_listing_advice(TODAY - timedelta(days=130), up[::-1].reset_index(drop=True),
                                   today=TODAY).level == "warn"


def test_trusted_chinese_sources():
    assert news.is_trusted("证券时报") and news.is_trusted("央广财经") and news.is_trusted("21财经")
    assert not news.is_trusted("东方财富") and not news.is_trusted("新浪财经")
