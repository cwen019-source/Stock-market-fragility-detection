#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脆弱度 v2 — 資料層(長歷史、附磁碟快取)。

每個原始序列快取在 v2_cache/<key>.json(CI 以 actions/cache 保存);
超過 max_age 才重抓,抓取失敗時沿用舊快取(並標示 stale)。
"""
import datetime as dt
import io
import json
import os
import time

import pandas as pd
import requests

CACHE_DIR = os.environ.get("V2_CACHE_DIR", "v2_cache")
SEED_DIR = "v2_seed"          # 進版控的小型種子(長歷史 S&P 500、CAPE):來源暫時擋 CI 時的後備
FINMIND = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
S = requests.Session()
STATUS = {}            # key -> "net" | "cache" | "stale" | "fail"

os.makedirs(CACHE_DIR, exist_ok=True)


def _path(key):
    return os.path.join(CACHE_DIR, key.replace("/", "_") + ".json")


def cached(key, fn, max_age_h=20):
    """回傳 pd.Series(DatetimeIndex)。fn() 需回傳 pd.Series。"""
    p = _path(key)
    old = None
    seed = os.path.join(SEED_DIR, os.path.basename(p))
    if not os.path.exists(p) and os.path.exists(seed):
        p_read = seed
    else:
        p_read = p
    if os.path.exists(p_read):
        try:
            j = json.load(open(p_read))
            old = pd.Series(j["v"], index=pd.to_datetime(j["i"]), dtype=float)
            if p_read == seed:
                j["t"] = 0                       # 種子一律視為過期 → 先試網路
            if time.time() - j.get("t", 0) < max_age_h * 3600 and len(old):
                STATUS[key] = "cache"
                return old
        except Exception:
            old = None
    try:
        s = fn().dropna().sort_index()
        s = s[~s.index.duplicated(keep="last")]
        if not len(s):
            raise ValueError("empty")
        json.dump({"t": time.time(), "i": [d.strftime("%Y-%m-%d") for d in s.index], "v": [float(x) for x in s.values]},
                  open(p, "w"))
        STATUS[key] = "net"
        return s
    except Exception as e:
        if old is not None and len(old):
            STATUS[key] = f"stale({type(e).__name__})"
            return old
        STATUS[key] = f"fail({type(e).__name__}: {str(e)[:60]})"
        return pd.Series(dtype=float)


# ---------------- 來源 ----------------
def fred(sid, max_age_h=20):
    def f():
        r = S.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=(10, 60))
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = ["d", "v"]
        return pd.Series(pd.to_numeric(df["v"], errors="coerce").values, index=pd.to_datetime(df["d"]))
    return cached(f"fred_{sid}", f, max_age_h)


def yahoo(sym, max_age_h=20):
    """長期日線(Yahoo 非官方 API);失敗時沿用快取。"""
    def f():
        r = S.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                  params={"period1": -1325635200, "period2": int(time.time()) + 86400, "interval": "1d"},
                  headers={"User-Agent": "Mozilla/5.0"}, timeout=(10, 60))
        res = r.json()["chart"]["result"][0]
        idx = pd.to_datetime(res["timestamp"], unit="s").normalize()
        return pd.Series(res["indicators"]["quote"][0]["close"], index=idx, dtype=float)
    return cached(f"yahoo_{sym}", f, max_age_h)


def multpl(table, max_age_h=72):
    """multpl.com 月表(Shiller CAPE 等)。"""
    import re
    def f():
        s = S.get(f"https://www.multpl.com/{table}/table/by-month", timeout=(10, 60),
                  headers={"User-Agent": "Mozilla/5.0"}).text
        rows = re.findall(r"<td>([A-Z][a-z]{2} \d+, \d{4})</td>\s*<td>\s*(?:&#x2002;|&#8226;|\s)*([\d.,]+)", s)
        return pd.Series([float(v.replace(",", "")) for _, v in rows], index=pd.to_datetime([d for d, _ in rows]))
    return cached(f"multpl_{table}", f, max_age_h)


def finmind(dataset, data_id=None, start="1999-01-01", value=None, max_age_h=20, transform=None):
    """FinMind;value=欄位名 或 transform(df)->Series。"""
    key = f"fm_{dataset}_{data_id or 'all'}_{value or 'x'}"

    def f():
        p = {"dataset": dataset, "start_date": start, "end_date": dt.date.today().isoformat()}
        if data_id:
            p["data_id"] = data_id
        h = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
        for a in range(3):
            r = S.get(FINMIND, params=p, headers=h, timeout=(10, 120))
            if r.status_code == 200:
                df = pd.DataFrame(r.json().get("data", []))
                if not len(df):
                    return pd.Series(dtype=float)
                if transform:
                    return transform(df)
                df["date"] = pd.to_datetime(df["date"])
                return pd.Series(pd.to_numeric(df[value], errors="coerce").values, index=df["date"])
            if r.status_code in (402, 429):
                raise RuntimeError("FinMind rate limit")
            time.sleep(3 * (a + 1))
        raise RuntimeError(f"FinMind HTTP {r.status_code}")
    return cached(key, f, max_age_h)


def tw_gdp_annual(max_age_h=168):
    """台灣名目 GDP(IMF WEO via DBnomics,年頻);僅保留已實現年份。"""
    def f():
        j = S.get("https://api.db.nomics.world/v22/series/IMF/WEO:latest/TWN.NGDP?observations=1", timeout=60).json()
        s = j["series"]["docs"][0]
        this_year = dt.date.today().year
        pts = {int(p): v for p, v in zip(s["period"], s["value"]) if v is not None and int(p) < this_year}
        return pd.Series(list(pts.values()), index=pd.to_datetime([f"{y}-12-31" for y in pts]))
    return cached("tw_gdp", f, max_age_h)


# ---------------- 對齊工具 ----------------
def known_at(s, grid, lag_days=0):
    """在 grid 每個日期「已公布」的最新值:觀測日 + lag_days <= 該日。"""
    if s is None or not len(s):
        return pd.Series(index=grid, dtype=float)
    s = s.dropna().sort_index()
    s2 = pd.Series(s.values, index=s.index + pd.Timedelta(days=lag_days))
    s2 = s2[~s2.index.duplicated(keep="last")]
    return s2.reindex(s2.index.union(grid)).ffill().reindex(grid)


# 台股大型權值股(約前 25 大,2026 年市值;涵蓋約 2/3 市值)— 存活者偏誤見方法論頁
TW_LARGE = ["2330", "2317", "2454", "2308", "2382", "3711", "2881", "2882", "2412", "2891", "2303", "6669",
            "2345", "2886", "1216", "2884", "2357", "3017", "2603", "1301", "2002", "2892", "5880", "3231", "2885"]


def tw_stock_panels(ids=TW_LARGE):
    """每檔:收盤價、發行股數、本益比、月營收(營收以「公布日」為索引)。"""
    out = {}
    for sid in ids:
        px = finmind("TaiwanStockPrice", sid, "2004-01-01", "close")
        sh = finmind("TaiwanStockShareholding", sid, "2004-01-01", "NumberOfSharesIssued", max_age_h=168)
        pe = finmind("TaiwanStockPER", sid, "2004-01-01", "PER")

        def rev_tf(df):
            # FinMind 'date' = 營收所屬月份的次月 1 日;法定 10 日前公布 → 以 date+9 天為可得日
            d = pd.to_datetime(df["date"]) + pd.Timedelta(days=9)
            return pd.Series(pd.to_numeric(df["revenue"], errors="coerce").values, index=d)
        rv = finmind("TaiwanStockMonthRevenue", sid, "2002-01-01", transform=rev_tf, max_age_h=72)
        out[sid] = {"px": px, "sh": sh, "pe": pe, "rev": rv}
    return out
