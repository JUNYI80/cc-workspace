from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from t212quant import ipo, news

TODAY = date(2026, 11, 4)
NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)

NASDAQ = {
    "priced": {"rows": [{"proposedTickerSymbol": "ACME", "companyName": "Acme Robotics Inc.",
                         "proposedExchange": "NASDAQ Global", "proposedSharePrice": "21.00",
                         "sharesOffered": "10,000,000", "pricedDate": "06/12/2026",
                         "dollarValueOfSharesOffered": "$210,000,000"}]},
    "upcoming": {"upcomingTable": {"rows": [{
        "proposedTickerSymbol": "NEWC", "companyName": "NewCo Holdings", "proposedExchange": "NYSE",
        "proposedSharePrice": "$18.00-$20.00", "sharesOffered": "5,000,000",
        "expectedPriceDate": "11/12/2026", "dollarValueOfSharesOffered": "$100,000,000"}]}},
    "filed": {"rows": [{"proposedTickerSymbol": None, "companyName": "Anthropic, PBC",
                        "filedDate": "10/30/2026", "dollarValueOfSharesOffered": ""}]},
    "withdrawn": None,
}


def test_parse_nasdaq():
    df = ipo.parse_nasdaq(NASDAQ).set_index("status")
    assert df.loc["priced", "price_high"] == 21.0 and df.loc["priced", "value_usd"] == 210e6
    assert (df.loc["upcoming", "price_low"], df.loc["upcoming", "price_high"]) == (18.0, 20.0)
    assert df.loc["upcoming", "date"] == date(2026, 11, 12)
    assert pd.isna(df.loc["filed", "symbol"]) and pd.isna(df.loc["filed", "value_usd"])


def test_fetch_calendar_merges_months_and_survives_one_failure():
    calls = []

    def fetch(month):
        calls.append(month)
        if month == "2026-09":
            raise RuntimeError("boom")
        return NASDAQ

    df = ipo.fetch_calendar(months_back=2, months_ahead=1, today=TODAY, fetch=fetch)
    assert calls == ["2026-09", "2026-10", "2026-11", "2026-12"]
    assert len(df) == 3  # duplicates across months collapsed
    assert df["date"].iloc[0] == date(2026, 11, 12)  # newest first


def test_fetch_calendar_raises_when_everything_fails():
    def fetch(month):
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        ipo.fetch_calendar(months_back=0, months_ahead=0, today=TODAY, fetch=fetch)


EFTS = {"hits": {"hits": [
    {"_id": "0001234567-26-000001:s1.htm", "_source": {
        "display_names": ["Anthropic, PBC  (CIK 0001234567)"], "ciks": ["0001234567"],
        "form": "S-1", "file_date": "2026-10-30", "adsh": "0001234567-26-000001"}},
    {"_id": "0009999999-26-000002:x.htm", "_source": {  # mentions Anthropic, filed by someone else
        "display_names": ["Other AI Corp  (CIK 0009999999)"], "ciks": ["0009999999"],
        "form": "S-1", "file_date": "2026-09-01", "adsh": "0009999999-26-000002"}},
]}}


def test_parse_efts_keeps_only_the_companys_own_filings():
    filings = ipo.parse_efts(EFTS, ["Anthropic"])
    assert len(filings) == 1
    f = filings[0]
    assert (f.company, f.form, f.filed) == ("Anthropic, PBC", "S-1", "2026-10-30")
    assert f.url == "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/s1.htm"


def test_sec_filings_searches_every_alias():
    seen = []

    def fetch(params):
        seen.append(params["q"])
        return EFTS

    out = ipo.sec_filings("Anthropic | Anthropic PBC", "me@example.com", today=TODAY, fetch=fetch)
    assert seen == ['"Anthropic"', '"Anthropic PBC"']
    assert len(out) == 1  # same filing found twice is listed once


@pytest.mark.parametrize("title, stage", [
    ("Anthropic releases new model", 0),
    ("Anthropic debuts new model", 0),
    ("Anthropic CEO says no plans to IPO soon", 1),
    ("Anthropic IPO could be huge", 1),
    ("Anthropic hires banks for potential IPO", 2),
    ("Anthropic plans stock market debut in 2027", 2),
    ("Anthropic confidentially files for IPO", 3),
    ("Anthropic files for IPO", 4),
    ("Anthropic publicly files S-1 for IPO", 4),
    ("Anthropic launches IPO roadshow, sets price range", 5),
])
def test_news_stage(title, stage):
    assert ipo.news_stage(title) == stage


def _h(title, days_ago):
    return news.Headline(title, "https://x", NOW - timedelta(days=days_ago), "Reuters")


def test_watch_status_from_news_is_marked_unconfirmed():
    s = ipo.watch_status("Anthropic", [_h("Anthropic hires banks for IPO", 2),
                                       _h("Anthropic releases model", 1)], [], None, now=NOW)
    assert s.stage == 2 and not s.official and "未经证实" in s.label
    assert s.heat == 1


def test_old_headlines_are_ignored():
    s = ipo.watch_status("Anthropic", [_h("Anthropic files for IPO", 400)], [], None, now=NOW)
    assert s.stage == 0


def test_official_data_beats_news():
    cal = ipo.parse_nasdaq(NASDAQ)
    s = ipo.watch_status("Anthropic", [_h("Anthropic launches IPO roadshow", 1)], [], cal, now=NOW)
    assert s.stage == 4 and s.official and "已递交申请" in s.evidence
    f = ipo.Filing("Anthropic, PBC", "S-1", "2026-10-30", "u")
    s2 = ipo.watch_status("Anthropic", [], [f], None, now=NOW)
    assert s2.stage == 4 and s2.official


def _closes(trend: float, n: int = 120):
    return pd.Series([20 * (1 + trend) ** i for i in range(n)])


@pytest.mark.parametrize("ipo_day, closes, level, phrase", [
    (TODAY - timedelta(days=1), None, "warn", "不追首日"),
    (TODAY - timedelta(days=170), _closes(0.01), "warn", "锁定期"),
    (TODAY - timedelta(days=40), _closes(0.01), "info", "不满 3 个月"),
    (TODAY - timedelta(days=120), _closes(0.01), "ok", "趋势向好"),
    (TODAY - timedelta(days=120), _closes(-0.01), "warn", "趋势偏弱"),
    (TODAY - timedelta(days=120), None, "info", "数据不足"),
])
def test_post_ipo_advice(ipo_day, closes, level, phrase):
    adv = ipo.post_ipo_advice(ipo_day, 15.0, closes, today=TODAY)
    assert adv.level == level and phrase in adv.text


def test_recent_and_upcoming_filters():
    cal = ipo.parse_nasdaq(NASDAQ)
    recent = ipo.recent_ipos(cal, today=TODAY)
    assert list(recent["symbol"]) == ["ACME"] and recent["ipo_price"].iloc[0] == 21.0
    assert ipo.recent_ipos(cal, today=TODAY, min_value_usd=1e9).empty
    assert list(ipo.upcoming_ipos(cal, today=TODAY, days=14)["symbol"]) == ["NEWC"]
    assert ipo.upcoming_ipos(cal, today=TODAY, days=3).empty


def test_parse_rss():
    xml = """<?xml version="1.0"?><rss><channel>
      <item><title>Older story - FT</title><link>https://n/1</link>
            <pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate><source url="https://ft.com">FT</source></item>
      <item><title>Newer story - Reuters</title><link>https://n/2</link>
            <pubDate>Tue, 03 Nov 2026 10:00:00 GMT</pubDate><source>Reuters</source></item>
      <item><title>No date</title><link>https://n/3</link></item>
    </channel></rss>"""
    out = news.parse_rss(xml)
    assert [h.title for h in out] == ["Newer story", "Older story", "No date"]
    assert out[0].source == "Reuters" and out[2].published is None


def test_sec_needs_a_contact_email():
    with pytest.raises(ValueError):
        ipo.sec_user_agent("")
    assert ipo.sec_user_agent(" me@example.com ").endswith("me@example.com")


def test_spacs_are_not_recent_ipos():
    assert ipo.is_spac("BHAV Acquisition Corp II", "BHAIU")
    assert ipo.is_spac("Some Holdings", "ABCDU")
    assert not ipo.is_spac("Aspen Neuroscience, Inc.", "ASPE")
    cal = pd.DataFrame([
        {"status": "priced", "symbol": "ASPE", "company": "Aspen Neuroscience, Inc.", "price_low": 15.0,
         "price_high": 15.0, "value_usd": 140e6, "date": date(2026, 10, 30)},
        {"status": "priced", "symbol": "BHAIU", "company": "BHAV Acquisition Corp II", "price_low": 10.0,
         "price_high": 10.0, "value_usd": 100e6, "date": date(2026, 10, 29)},
    ])
    assert list(ipo.recent_ipos(cal, today=TODAY)["symbol"]) == ["ASPE"]
