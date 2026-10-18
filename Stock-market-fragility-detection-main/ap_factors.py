#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
資產定價實驗室 — 台股自建因子(月頻, PIT 無前視)
================================================
依 Fama-French / Carhart / Hou-Xue-Zhang 的原始建構方式,以台股資料自建因子。

關鍵的無前視處理:
  * 財報用「公布落後」對齊:季報假設 45 天後才可得(t 月只用 t-45 天前已公布的季報)
  * 排序用的市值/帳面價值一律取「形成期」數值,持有期報酬用「之後」的月報酬
  * 每年 6 月重組(Fama-French 慣例),中間月份沿用當次分組

因子:
  MKT  市場超額報酬(市值加權 − 無風險利率)
  SMB  規模:小型 − 大型
  HML  價值:高 B/M − 低 B/M
  MOM  動能:過去 12-2 月贏家 − 輸家
  RMW  獲利:高營業獲利/權益 − 低
  CMA  投資:低資產成長 − 高
  ROE  q-factor 獲利:高 ROE − 低(月頻更新)
  IA   q-factor 投資:低資產成長 − 高
  BAB  低 beta − 高 beta(Frazzini-Pedersen,以 beta 倒數加權簡化為多空分組)
  LIQ  流動性:低週轉 − 高週轉
輸出 → ap_factors.json
"""
import json, math
import numpy as np, pandas as pd

RF_ANNUAL = 0.011          # 台灣無風險利率近似(一年期定存~1.1%);可改為讀取實際利率
LAG_DAYS  = 45             # 財報公布落後
MIN_STOCK = 100            # 每月至少要有這麼多檔才建因子
OUT       = "ap_factors.json"

def load():
    px = json.load(open("ap_px_cache.json"))
    fd = json.load(open("ap_fund_cache.json"))
    uni = {u["stock_id"]: u for u in json.load(open("universe.json"))}
    return px, fd, uni

def monthly_frame(px):
    """{sid:{m,px,tv}} → 月頻 DataFrame(價格 / 成交值)"""
    ps, ts = {}, {}
    for sid, v in px.items():
        if not v.get("px") or len(v["px"]) < 24: continue
        idx = pd.PeriodIndex(v["m"], freq="M")
        ps[sid] = pd.Series(v["px"], index=idx)
        if v.get("tv"): ts[sid] = pd.Series(v["tv"], index=idx)
    P = pd.DataFrame(ps).sort_index()
    T = pd.DataFrame(ts).reindex(P.index)
    return P, T

def quarterly_to_monthly(fd, key, field, months):
    """季報 → 月頻(依公布落後 LAG_DAYS 對齊,只用當時已公布者)"""
    out = {}
    for sid, d in fd.items():
        blk = (d or {}).get(key) or {}
        ser = blk.get(field)
        if not ser: continue
        dates = pd.to_datetime(list(ser.keys()))
        avail = dates + pd.Timedelta(days=LAG_DAYS)      # 可得日 = 季末 + 落後
        s = pd.Series(list(ser.values()), index=avail).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        mm = s.resample("ME").last().ffill()
        mm.index = pd.PeriodIndex(mm.index, freq="M")
        out[sid] = mm.reindex(months).ffill()
    return pd.DataFrame(out)

def monthly_val(fd, field, months):
    """PBR/PER/殖利率(月頻,當月底可得)"""
    out = {}
    for sid, d in fd.items():
        v = (d or {}).get("val")
        if not v or not v.get("m"): continue
        s = pd.Series(v[field], index=pd.PeriodIndex(v["m"], freq="M"))
        out[sid] = s.reindex(months)
    return pd.DataFrame(out)

def shares_monthly(fd, months):
    out = {}
    for sid, d in fd.items():
        v = (d or {}).get("sh")
        if not v or not v.get("m"): continue
        s = pd.Series(v["n"], index=pd.PeriodIndex(v["m"], freq="M"))
        out[sid] = s.reindex(months).ffill()
    return pd.DataFrame(out)

def tercile_sort(x, q=(0.3, 0.7)):
    """回傳 -1(低) / 0(中) / +1(高);NaN 保持 NaN"""
    v = x.dropna()
    if len(v) < 30: return pd.Series(np.nan, index=x.index)
    lo, hi = v.quantile(q[0]), v.quantile(q[1])
    out = pd.Series(np.nan, index=x.index)
    out[x <= lo] = -1; out[x >= hi] = 1
    out[(x > lo) & (x < hi)] = 0
    return out

def wavg(r, w):
    m = r.notna() & w.notna() & (w > 0)
    if m.sum() < 3: return np.nan
    return float((r[m] * w[m]).sum() / w[m].sum())

def two_way(ret, size, sig, mcap, hi_minus_lo=True):
    """Fama-French 2×3 排序:以市值中位數分大小, 訊號分三組, 取角落組合價差。"""
    med = size.median()
    small, big = size <= med, size > med
    s = tercile_sort(sig)
    def leg(mask, lvl):
        m = mask & (s == lvl)
        return wavg(ret[m], mcap[m]) if m.sum() >= 3 else np.nan
    sh, sl = leg(small, 1), leg(small, -1)
    bh, bl = leg(big, 1), leg(big, -1)
    hi = np.nanmean([sh, bh]); lo = np.nanmean([sl, bl])
    val = hi - lo if hi_minus_lo else lo - hi
    # SMB 用同一組排序的大小腿
    sm = np.nanmean([sh, sl, leg(small, 0)]); bg = np.nanmean([bh, bl, leg(big, 0)])
    return val, (sm - bg)

def build():
    px, fd, uni = load()
    P, T = monthly_frame(px)
    months = P.index
    R = P.pct_change()                                  # 月報酬(還原價)
    rf_m = (1 + RF_ANNUAL) ** (1/12) - 1

    sh   = shares_monthly(fd, months)
    pbr  = monthly_val(fd, "pbr", months)
    per  = monthly_val(fd, "per", months)
    dy   = monthly_val(fd, "dy",  months)
    # 市值 = 月底價 × 發行股數(僅取兩者皆有者)
    MC = (P * sh.reindex(columns=P.columns)).replace([np.inf, -np.inf], np.nan)
    BM = (1.0 / pbr.replace(0, np.nan)).reindex(columns=P.columns)     # 帳面市值比

    equity = quarterly_to_monthly(fd, "fs", "EquityAttributableToOwnersOfParent", months).reindex(columns=P.columns)
    opinc  = quarterly_to_monthly(fd, "fs", "OperatingIncome", months).reindex(columns=P.columns)
    netinc = quarterly_to_monthly(fd, "fs", "IncomeAfterTaxes", months).reindex(columns=P.columns)
    assets = quarterly_to_monthly(fd, "bs", "TotalAssets", months).reindex(columns=P.columns)

    OP  = (opinc / equity.replace(0, np.nan))                      # 營業獲利率 → RMW
    ROE = (netinc / equity.replace(0, np.nan))                     # → q-factor ROE
    AG  = (assets / assets.shift(12) - 1)                          # 資產成長 → CMA / IA
    MOM = (P.shift(2) / P.shift(12) - 1)                           # 12-2 月動能
    TURN = (T / MC.replace(0, np.nan))                             # 週轉率 → 流動性

    # 滾動 beta(以市值加權市場報酬, 36 個月)
    mktw = MC.shift(1)
    mkt = pd.Series({m: wavg(R.loc[m], mktw.loc[m]) for m in months})
    beta = pd.DataFrame(index=months, columns=P.columns, dtype=float)
    for i in range(36, len(months)):
        w = slice(i-35, i+1)
        mk = mkt.iloc[w]
        sub = R.iloc[w]
        vm = mk.var()
        if not np.isfinite(vm) or vm <= 0: continue
        cov = sub.apply(lambda c: c.cov(mk))
        beta.iloc[i] = cov / vm

    rows = {}
    for i, m in enumerate(months):
        if i < 13: continue
        r = R.loc[m]
        prev = months[i-1]
        size, mcap = MC.loc[prev], MC.loc[prev]
        ok = r.notna() & size.notna()
        if ok.sum() < MIN_STOCK: continue
        rec = {}
        rec["MKT"] = wavg(r[ok], mcap[ok]) - rf_m
        hml, smb1 = two_way(r[ok], size[ok], BM.loc[prev][ok], mcap[ok])
        rec["HML"] = hml; rec["SMB"] = smb1
        rec["MOM"] = two_way(r[ok], size[ok], MOM.loc[prev][ok], mcap[ok])[0]
        rec["RMW"] = two_way(r[ok], size[ok], OP.loc[prev][ok], mcap[ok])[0]
        rec["CMA"] = two_way(r[ok], size[ok], AG.loc[prev][ok], mcap[ok], hi_minus_lo=False)[0]
        rec["ROE"] = two_way(r[ok], size[ok], ROE.loc[prev][ok], mcap[ok])[0]
        rec["IA"]  = two_way(r[ok], size[ok], AG.loc[prev][ok], mcap[ok], hi_minus_lo=False)[0]
        rec["BAB"] = two_way(r[ok], size[ok], beta.loc[prev][ok], mcap[ok], hi_minus_lo=False)[0]
        rec["LIQ"] = two_way(r[ok], size[ok], TURN.loc[prev][ok], mcap[ok], hi_minus_lo=False)[0]
        rows[str(m)] = {k: (None if (v is None or not np.isfinite(v)) else round(float(v), 6))
                        for k, v in rec.items()}
    out = {"rf": rf_m, "months": list(rows.keys()),
           "factors": {k: [rows[m].get(k) for m in rows] for k in
                       ["MKT","SMB","HML","MOM","RMW","CMA","ROE","IA","BAB","LIQ"]}}
    json.dump(out, open(OUT, "w"))
    # 摘要
    print(f"月數 {len(rows)}  {list(rows)[0]} ~ {list(rows)[-1]}")
    print(f"{'因子':6}{'年化%':>9}{'年化σ%':>9}{'夏普':>8}{'t值':>8}")
    for k, v in out["factors"].items():
        s = pd.Series([x for x in v if x is not None], dtype=float)
        if len(s) < 24: print(f"{k:6}{'資料不足':>9}"); continue
        ann = (1 + s.mean()) ** 12 - 1
        vol = s.std() * math.sqrt(12)
        t = s.mean() / (s.std() / math.sqrt(len(s)))
        print(f"{k:6}{ann*100:>9.2f}{vol*100:>9.2f}{(ann/vol if vol else float('nan')):>8.2f}{t:>8.2f}")
    return out

if __name__ == "__main__":
    build()
