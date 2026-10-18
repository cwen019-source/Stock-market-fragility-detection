#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脆弱度 v2 — 依專業框架重建的結構風險模型(台股 / 美股)。

方法(詳見 methodology.html):
  1. 指標依「脆弱性類別」分 4 組(Fed 金融穩定報告):估值 V / 槓桿與信用 L / 景氣循環 M / 市場壓力 S。
     同組指標先平均再當一票 → 解決同源指標(融資三兄弟)重複計票。
  2. 所有指標以 point-in-time(PIT)擴張百分位標準化,並套用公布落後(季資料 +75~150 天等),無前視偏誤。
  3. 綜合分數用 ECB CISS 作法:s'Cs,C = 各組間的 EWMA 時變相關 → 多組「同時」偏高才會高。
  4. 紅區旗標(Greenwood-Hanson-Shleifer-Sørensen 2022):信用 3 年成長在前 20% 且 3 年股價漲幅在前 1/3。
  5. Growth-at-Risk(IMF):以分量迴歸估計「未來 12 個月報酬的第 10 百分位」,只用當時已實現的樣本擴張重估。
  6. 評估(Kaminsky-Lizondo-Reinhart 訊號法):事件 = 之後 12 個月內比當時價位低 ≥20%;報告命中率、
     假警報率、雜訊/訊號比(NSR)、P(事件|訊號) vs 無條件機率,並與舊版「6 燈 ≥4」同期比較。
  7. 快速觸發層(VIX 急升 / 跌破長期均線)維持獨立,只用來判斷「是否已開始」。

輸出:fragility_v2.json(頁面、警報信使用)、v2_backtest.json;
     python fragility_v2.py --inject 另將面板嵌入 index.html / us.html 並產生 methodology.html。
"""
import datetime as dt
import json
import math
import os
import sys
import warnings

import numpy as np
import pandas as pd

import v2_data as V

warnings.filterwarnings("ignore")

OUT = "fragility_v2.json"
BT_OUT = "v2_backtest.json"
PILLARS = {"V": "估值 vs 基本面", "L": "槓桿與信用", "M": "景氣循環(實體經濟)", "S": "市場壓力"}
EVENT_DD = -0.20          # 事件:未來 12 個月內任一日收盤比當時價位低 ≥20%
EVENT_H = 12              # 月
SIG = 0.80                # 單組 / 綜合「偏高」門檻(PIT 百分位)— 事前固定,非回測最佳化
RZ_CREDIT, RZ_PRICE = 0.80, 2 / 3   # 紅區門檻(沿用 Greenwood et al.)
LAMBDA = 0.94             # CISS EWMA 相關衰減(月資料,半衰期約 11 個月)


# ======================================================================
# 共用工具
# ======================================================================
def pit_pct(s, min_periods):
    """PIT 擴張百分位(0~1):第 t 期只用 ≤t 的觀測。"""
    v = s.values.astype(float)
    out = np.full(len(v), np.nan)
    hist = []
    import bisect
    for i, x in enumerate(v):
        if np.isnan(x):
            continue
        bisect.insort(hist, x)
        if len(hist) >= min_periods:
            lo = bisect.bisect_left(hist, x)
            hi = bisect.bisect_right(hist, x)
            out[i] = (lo + hi) / 2 / len(hist)
    return pd.Series(out, index=s.index)


def month_grid(start, end_date):
    g = pd.date_range(start, end_date, freq="ME")
    if g[-1] < pd.Timestamp(end_date):
        g = g.append(pd.DatetimeIndex([pd.Timestamp(end_date)]))   # 最後一點 = 最新資料日(當月未結束)
    return g


def fwd_maxdd(px_daily, grid, months=EVENT_H):
    """grid 每個日期之後 N 個月內,日線最低價相對「當時價位」的跌幅(= 若在警示當下持有,最多會虧多少);資料不足者為 NaN。"""
    px = px_daily.dropna()
    out = []
    for d in grid:
        end = d + pd.DateOffset(months=months)
        if end > px.index[-1]:
            out.append(np.nan)
            continue
        p0 = px[:d]
        if not len(p0):
            out.append(np.nan)
            continue
        w = px[(px.index > d) & (px.index <= end)]
        out.append(float(w.min() / p0.iloc[-1] - 1) if len(w) else np.nan)
    return pd.Series(out, index=grid)


def fwd_ret(px_daily, grid, months=EVENT_H):
    px = px_daily.dropna()
    a = px.reindex(px.index.union(grid)).ffill().reindex(grid)
    b = []
    for d in grid:
        e = d + pd.DateOffset(months=months)
        if e > px.index[-1]:
            b.append(np.nan)
        else:
            b.append(float(px[:e].iloc[-1] / a[d] - 1))
    return pd.Series(b, index=grid)


def daily_at(s, grid):
    return V.known_at(s, grid, 0)


# ======================================================================
# 市場別指標建構(全部回傳「越高越危險」方向的原始值)
# ======================================================================
def build_us():
    sp = V.yahoo("%5EGSPC")                                # 1927 起日線(失敗時用快取/種子)
    sp = sp.combine_first(V.fred("SP500"))                 # FRED 補最近幾天(兩者同為 S&P 500 指數)
    asof = sp.index[-1]
    g = month_grid("1950-01-31", asof)

    mv = V.fred("NCBEILQ027S", 168)        # 非金融企業股權市值(Z.1,季)
    gdp = V.fred("GDP", 168)
    cp = V.fred("CP", 168)                 # 企業稅後盈餘
    credit = V.fred("QUSPAM770A", 168)     # BIS:私部門非金融信用 / GDP(%)
    margin = V.fred("BOGZ1FL663067003Q", 168)   # 券商融資應收款(Z.1)
    cape = V.multpl("shiller-pe")
    unrate = V.fred("UNRATE")
    gs10, tb3 = V.fred("GS10"), V.fred("TB3MS")
    indpro = V.fred("INDPRO")
    baa = V.fred("BAA")
    nfci = V.fred("NFCI")
    vix = V.fred("VIXCLS")
    vxo = V.fred("VXOCLS")

    spm = daily_at(sp, g)
    # FRED 把季資料標在「季初」、月資料標在「月初」→ 先移到期末,公布落後一律從期末起算
    QE = lambda s: pd.Series(s.values, index=s.index + pd.offsets.QuarterEnd(0))
    ME = lambda s: pd.Series(s.values, index=s.index + pd.offsets.MonthEnd(0))
    mv_e, gdp_e, cp_e, cr_e, mg_e = QE(mv), QE(gdp), QE(cp), QE(credit), QE(margin)
    LAG = {"mv": 75, "gdp": 35, "cp": 65, "credit": 160, "margin": 75}   # Z.1 約季後 10 週;GDP 初值約 30 天;BIS 約 5 個月
    # 市值以「季末後 75 天公布」對齊,再用「同一季末」以來的 S&P 漲跌推到當下(Buffett 指標常見更新法)
    mv_q = V.known_at(mv_e, g, LAG["mv"])
    sp_qe = pd.Series(V.known_at(sp, mv_e.index, 0).values, index=mv_e.index)       # 季末當天的 S&P
    sp_at_q = V.known_at(sp_qe, g, LAG["mv"])
    mv_now = mv_q * spm / sp_at_q
    gdp_k = V.known_at(gdp_e, g, LAG["gdp"])
    cp_k = V.known_at(cp_e, g, LAG["cp"])
    ind = pd.DataFrame(index=g)
    ind["cape"] = V.known_at(ME(cape), g, 0)
    ind["mv_gdp"] = mv_now / gdp_k / 1e3           # 百萬 vs 十億
    ind["mv_cp"] = mv_now / cp_k / 1e3
    cr = V.known_at(cr_e, g, LAG["credit"])
    ind["credit_gap3y"] = cr - V.known_at(cr_e, g - pd.DateOffset(years=3), LAG["credit"]).values
    mg = V.known_at(mg_e, g, LAG["margin"])
    ind["margin_gdp_1y"] = (mg / gdp_k) / (V.known_at(mg_e, g - pd.DateOffset(years=1), LAG["margin"]).values /
                                           V.known_at(gdp_e, g - pd.DateOffset(years=1), LAG["gdp"]).values) - 1
    # 月資料:先在「月序列」上算 12 個月視窗,再以期末 + 公布落後對齊
    ur_m = ME(unrate)
    ind["unemp_rise"] = V.known_at(ur_m - ur_m.rolling(12, min_periods=6).min(), g, 7)   # Sahm 型:失業率自 12 月低點上升
    ind["curve_inv"] = -(V.known_at(ME(gs10), g, 1) - V.known_at(ME(tb3), g, 1))        # 月均利率:月底後隔日可得
    ip_m = ME(indpro)
    ind["ip_weak"] = V.known_at(-(ip_m / ip_m.shift(12) - 1), g, 17)
    ind["baa_spread"] = V.known_at(ME(baa), g, 1) - V.known_at(ME(gs10), g, 1)
    ind["nfci"] = V.known_at(nfci, g, 6)
    vol = vix.combine_first(vxo)
    ind["vix"] = vol.rolling(21, min_periods=5).mean().pipe(lambda s: daily_at(s, g))
    price3y = np.log(spm / daily_at(sp, g - pd.DateOffset(years=3)).values)

    disp = {"cape": (1, 1, "x", 1), "mv_gdp": (1, 1, "x GDP", 2), "mv_cp": (1, 1, "x", 1),
            "credit_gap3y": (1, 1, "pp", 1), "margin_gdp_1y": (1, 100, "%", 1), "unemp_rise": (1, 1, "pp", 1),
            "curve_inv": (-1, 1, "pp", 2), "ip_weak": (-1, 100, "%", 1), "baa_spread": (1, 1, "pp", 2),
            "nfci": (1, 1, "", 2), "vix": (1, 1, "", 1)}
    groups = {"V": ["cape", "mv_gdp", "mv_cp"], "L": ["credit_gap3y", "margin_gdp_1y"],
              "M": ["unemp_rise", "curve_inv", "ip_weak"], "S": ["baa_spread", "nfci", "vix"]}
    labels = {"cape": "Shiller CAPE", "mv_gdp": "股市市值 / GDP(巴菲特指標)", "mv_cp": "股市市值 / 企業盈餘",
              "credit_gap3y": "私部門信用/GDP 3年變化", "margin_gdp_1y": "融資/GDP 年變化",
              "unemp_rise": "失業率自低點上升", "curve_inv": "10年−3月利差(倒掛=險)", "ip_weak": "工業生產年增(衰退=險)",
              "baa_spread": "BAA 信用利差", "nfci": "芝加哥聯準金融狀況", "vix": "VIX(月均)"}
    # 快速觸發層(日線)
    ma200 = sp.rolling(200).mean()
    vix_spike = (vix / vix.shift(5) - 1)
    vsp = pit_pct(vix_spike.dropna(), 250)
    trig_daily = (sp < ma200) | (V.known_at(vsp, sp.index, 0) >= 0.85)          # 美股收盤時 VIX 已知
    fast = {"below_ma": bool(sp.iloc[-1] < ma200.iloc[-1]),
            "vix_spike_pct": float(pit_pct(vix_spike.dropna(), 250).iloc[-1]) if len(vix_spike.dropna()) > 300 else None,
            "ma_name": "200 日線", "dist_ma": float(sp.iloc[-1] / ma200.iloc[-1] - 1)}
    return dict(code="US", name="美股(S&P 500)", grid=g, ind=ind, groups=groups, labels=labels, disp=disp,
                trig_daily=trig_daily.where(ma200.notna()), price=sp, price3y=price3y, credit_growth=ind["credit_gap3y"], fast=fast, min_pct=60,
                eval_start="1960-01-01", asof=asof)


def build_tw():
    tx = V.finmind("TaiwanStockPrice", "TAIEX", "1999-01-01", "close")
    asof = tx.index[-1]
    g = month_grid("2002-01-31", asof)

    def margin_tf(df):
        m = df[df["name"] == "MarginPurchaseMoney"]
        return pd.Series(pd.to_numeric(m["TodayBalance"]).values / 1e8, index=pd.to_datetime(m["date"]))
    margin = V.finmind("TaiwanStockTotalMarginPurchaseShortSale", None, "2001-01-01", transform=margin_tf)

    def foreign_tf(df):
        f = df[df["name"] == "Foreign_Investor"]
        return pd.Series((pd.to_numeric(f["buy"]) - pd.to_numeric(f["sell"])).values / 1e8, index=pd.to_datetime(f["date"]))
    foreign = V.finmind("TaiwanStockTotalInstitutionalInvestors", None, "2004-01-01", transform=foreign_tf)
    fx = V.finmind("TaiwanExchangeRate", "USD", "2001-01-01", "spot_sell")
    fx = fx.where(fx > 0).dropna()                          # 資料源有 −1 佔位值
    vix = V.fred("VIXCLS")
    gdp = V.tw_gdp_annual()
    panels = V.tw_stock_panels()

    txm = daily_at(tx, g)
    # --- 估值:大型權值股市值加權本益比(Σ市值 / Σ盈餘,只計正盈餘)
    mcap, earn = [], []
    for sid, p in panels.items():
        if not len(p["px"]) or not len(p["sh"]) or not len(p["pe"]):
            continue
        px = daily_at(p["px"], g)
        sh = V.known_at(p["sh"], g, 0)
        pe = daily_at(p["pe"], g)
        mc = px * sh
        e = (mc / pe).where(pe > 0)
        mcap.append(mc.where(e.notna()))
        earn.append(e)
    agg_pe = pd.concat(mcap, axis=1).sum(axis=1, min_count=5) / pd.concat(earn, axis=1).sum(axis=1, min_count=5)
    # --- 實體營收指數(連鎖年增:同一批公司今年 vs 去年同月,避免成分變動造成跳點)
    rev = {}
    for sid, p in panels.items():
        if len(p["rev"]):
            rev[sid] = p["rev"]
    # 依「營收所屬月份」對齊(公布日 − 9 天 − 1 個月 ≈ 所屬月)
    rv = pd.DataFrame({k: pd.Series(v.values, index=(v.index - pd.Timedelta(days=9) - pd.DateOffset(months=1)).to_period("M"))
                       for k, v in rev.items()})
    rv = rv[~rv.index.duplicated(keep="last")].sort_index()
    both = rv.notna() & rv.shift(12).notna()
    yoy = (rv.where(both).sum(axis=1) / rv.shift(12).where(both).sum(axis=1) - 1).where(both.sum(axis=1) >= 5)
    yoy3 = yoy.rolling(3, min_periods=2).mean()                 # 3 個月平均年增(去雜訊)
    sales_idx = np.exp(np.log1p(yoy).fillna(0).cumsum() / 12)   # 近似:年增 → 月成長累積
    # 以公布日(所屬月 + 1 個月 + 10 天)對齊 grid
    pub = (yoy3.index.to_timestamp(how="start") + pd.DateOffset(months=1, days=9)).normalize()   # 所屬月次月 10 日
    yoy3_k = V.known_at(pd.Series(yoy3.values, index=pub), g, 0)
    sales_k = V.known_at(pd.Series(sales_idx.values, index=pub), g, 0)
    sales_3y = V.known_at(pd.Series(sales_idx.values, index=pub), g - pd.DateOffset(years=3), 0).values
    price3y = np.log(txm / daily_at(tx, g - pd.DateOffset(years=3)).values)

    ind = pd.DataFrame(index=g)
    ind["agg_pe"] = V.known_at(agg_pe.dropna(), g, 0)
    ind["price_vs_sales3y"] = price3y - np.log(sales_k / sales_3y)        # 股價漲幅超過實際營收成長的幅度
    # --- 槓桿:融資(對 GDP 的 3 年超額成長 = 信用缺口類比)+ 融資半年成長
    mg = daily_at(margin, g)
    # 年 GDP 以「階梯」使用:只有已公布(年底 + 90 天)的年度值,不往未公布年度內插
    gdp_k = V.known_at(gdp, g, 90) if len(gdp) else pd.Series(index=g, dtype=float)
    gdp_k3 = V.known_at(gdp, g - pd.DateOffset(years=3), 90).values if len(gdp) else np.nan
    ind["margin_gdp_3y"] = np.log(mg / daily_at(margin, g - pd.DateOffset(years=3)).values) - np.log(gdp_k / gdp_k3)
    ind["margin_6m"] = mg / daily_at(margin, g - pd.DateOffset(months=6)).values - 1
    # --- 景氣循環:大型企業營收年增(越低越危險)、營收動能轉弱
    ind["rev_weak"] = -yoy3_k
    ind["rev_decel"] = -(yoy3_k - V.known_at(pd.Series(yoy3.values, index=pub), g - pd.DateOffset(months=6), 0).values)
    # --- 市場壓力:外資 20 日賣超、台幣 20 日貶值、VIX
    ff = foreign.rolling(20, min_periods=10).sum()
    ind["foreign_sell"] = -daily_at(ff, g)
    ind["twd_weak"] = daily_at(fx / fx.shift(20) - 1, g)
    ind["vix"] = daily_at(vix.rolling(21, min_periods=5).mean(), g)

    disp = {"agg_pe": (1, 1, "x", 1), "price_vs_sales3y": (1, 100, "%", 0), "margin_gdp_3y": (1, 100, "%", 0),
            "margin_6m": (1, 100, "%", 1), "rev_weak": (-1, 100, "%", 1), "rev_decel": (-1, 100, "pp", 1),
            "foreign_sell": (-1, 1, "億", 0), "twd_weak": (1, 100, "%", 2), "vix": (1, 1, "", 1)}
    groups = {"V": ["agg_pe", "price_vs_sales3y"], "L": ["margin_gdp_3y", "margin_6m"],
              "M": ["rev_weak", "rev_decel"], "S": ["foreign_sell", "twd_weak", "vix"]}
    labels = {"agg_pe": "權值股加權本益比", "price_vs_sales3y": "3年股價漲幅 − 營收成長",
              "margin_gdp_3y": "融資對 GDP 3年超額成長", "margin_6m": "融資半年成長",
              "rev_weak": "權值股營收年增(低=險)", "rev_decel": "營收年增 6 月變化(轉弱=險)",
              "foreign_sell": "外資 20 日淨買超(賣超=險)", "twd_weak": "台幣 20 日貶值", "vix": "VIX(月均)"}
    ma = tx.rolling(240).mean()
    vs = vix / vix.shift(5) - 1
    vsp = pit_pct(vs.dropna(), 250)
    trig_daily = (tx < ma) | (V.known_at(vsp, tx.index, 1) >= 0.85)              # 台股當日只知道前一晚的 VIX
    fast = {"below_ma": bool(tx.iloc[-1] < ma.iloc[-1]),
            "vix_spike_pct": float(pit_pct(vs.dropna(), 250).iloc[-1]) if len(vs.dropna()) > 300 else None,
            "ma_name": "年線(240 日)", "dist_ma": float(tx.iloc[-1] / ma.iloc[-1] - 1)}
    return dict(code="TW", name="台股(加權指數)", grid=g, ind=ind, groups=groups, labels=labels, disp=disp,
                trig_daily=trig_daily.where(ma.notna()), price=tx, price3y=price3y, credit_growth=ind["margin_gdp_3y"], fast=fast, min_pct=36,
                eval_start="2007-01-01", asof=asof)


# ======================================================================
# 模型
# ======================================================================
def ciss(scores, lam=LAMBDA):
    """ECB CISS:(w∘s)' C_t (w∘s),C_t 為 (s−0.5) 的 EWMA 相關矩陣;缺組時以可用組重新等權。"""
    cols = list(scores.columns)
    n = len(cols)
    cov = np.eye(n) * (1 / 12)          # U(0,1) 變異數起始值
    out, corr_last = [], None
    for _, row in scores.iterrows():
        x = row.values.astype(float)
        m = ~np.isnan(x)
        if m.sum() >= 3:
            d = np.where(m, x - 0.5, 0.0)
            upd = np.outer(d, d)
            mm = np.outer(m, m)
            cov = np.where(mm, lam * cov + (1 - lam) * upd, cov)
            sd = np.sqrt(np.diag(cov))
            C = cov / np.outer(sd, sd)
            C = np.clip(C, -1, 1)
            w = np.where(m, 1 / m.sum(), 0.0)
            ws = w * np.where(m, x, 0.0)
            out.append(float(ws @ C @ ws))
            corr_last = C
        else:
            out.append(np.nan)
    return pd.Series(out, index=scores.index), corr_last


def gar_quantile(comp, fwd, grid, q=0.10, min_n=60):
    """IMF Growth-at-Risk:Q_q(未來12月報酬 | 綜合分數),每月只用「已實現」樣本擴張重估。"""
    import statsmodels.api as sm
    out = pd.Series(np.nan, index=grid)
    for i, d in enumerate(grid):
        realized = grid[(grid + pd.DateOffset(months=EVENT_H)) <= d]
        x = comp.reindex(realized)
        y = fwd.reindex(realized)
        ok = x.notna() & y.notna()
        if ok.sum() < min_n or pd.isna(comp.get(d)):
            continue
        X = sm.add_constant(x[ok].values)
        try:
            res = sm.QuantReg(y[ok].values, X).fit(q=q, max_iter=10000, p_tol=1e-6)
            if getattr(res, "iterations", 0) < 10000:      # 未收斂的估計不採用
                out[d] = float(res.params[0] + res.params[1] * comp[d])
        except Exception:
            pass
    return out


def klr(signal, event, mask):
    """Kaminsky-Lizondo-Reinhart 訊號評估(月樣本)。"""
    s = signal[mask].astype(bool)
    e = event[mask].astype(bool)
    A = int((s & e).sum()); B = int((s & ~e).sum()); C = int((~s & e).sum()); D = int((~s & ~e).sum())
    hit = A / (A + C) if A + C else np.nan            # 事件中有被訊號涵蓋的比例
    fa = B / (B + D) if B + D else np.nan             # 非事件月份誤報比例
    nsr = fa / hit if hit and hit > 0 else np.nan
    p_sig = A / (A + B) if A + B else np.nan
    p_unc = (A + C) / (A + B + C + D) if A + B + C + D else np.nan
    return {"months": int(mask.sum()), "signal_share": round((A + B) / max(1, A + B + C + D), 3),
            "hit_rate": _r(hit), "false_alarm": _r(fa), "nsr": _r(nsr),
            "p_event_given_signal": _r(p_sig), "p_event_uncond": _r(p_unc),
            "lift": _r(p_sig / p_unc if p_unc and p_sig == p_sig else np.nan)}


def lift_ci(signal, event, mask, block=24, reps=1000, seed=7):
    """移動區塊 bootstrap(區塊 24 個月)估計提升倍數的 90% 信賴區間 —— 處理 12 個月視窗重疊造成的自相關。"""
    s = signal[mask].astype(bool).values
    e = event[mask].astype(bool).values
    n = len(s)
    if n < block * 2 or e.sum() == 0:
        return None
    rng = np.random.default_rng(seed)
    starts = np.arange(n - block + 1)
    out = []
    for _ in range(reps):
        idx = np.concatenate([np.arange(b, b + block) for b in rng.choice(starts, n // block + 1)])[:n]
        ss, ee = s[idx], e[idx]
        if ss.sum() == 0 or ee.mean() == 0:
            continue
        out.append(ee[ss].mean() / ee.mean())
    if len(out) < reps * 0.5:
        return None
    return [_r(np.percentile(out, 5), 2), _r(np.percentile(out, 95), 2)]


def runs(x):
    """布林序列中「連續為真」的段數(獨立事件/訊號次數的近似)。"""
    x = x.fillna(False).astype(bool)
    return int((x & ~x.shift(1, fill_value=False)).sum())


def episodes_caught(signal, event_start_dates, lead_months=12):
    """每個熊市事件(高點日)在發生前 lead_months 內是否出現過訊號。"""
    res = []
    for d in event_start_dates:
        w = signal[(signal.index >= d - pd.DateOffset(months=lead_months)) & (signal.index <= d)]
        res.append({"peak": d.strftime("%Y-%m"), "warned": bool(w.fillna(False).any())})
    return res


def bear_peaks(px, thresh=EVENT_DD, start=None):
    """日線找熊市(高點到低點 ≤ −20%)的高點日期。"""
    p = px.dropna()
    if start:
        p = p[start:]
    peaks, peak_v, peak_d, in_bear = [], p.iloc[0], p.index[0], False
    for d, v in p.items():
        if v > peak_v:
            peak_v, peak_d = v, d
            in_bear = False
        elif not in_bear and v / peak_v - 1 <= thresh:
            peaks.append(peak_d)
            in_bear = True
    return peaks


def fmt_disp(v, d):
    sign, mult, unit, dec = d
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    x = sign * mult * float(v)
    pre = "+" if unit.startswith(("%", "pp")) and x > 0 else ""
    return f"{pre}{x:,.{dec}f}{unit}"


def bear_periods(px, thresh=EVENT_DD, start=None):
    """熊市區間(高點 → 低點),供圖表陰影。"""
    p = px.dropna()
    if start:
        p = p[start:]
    out, peak_v, peak_d, trough_v, trough_d, in_bear = [], p.iloc[0], p.index[0], None, None, False
    for d, v in p.items():
        if not in_bear:
            if v > peak_v:
                peak_v, peak_d = v, d
            elif v / peak_v - 1 <= thresh:
                in_bear, trough_v, trough_d = True, v, d
        else:
            if v < trough_v:
                trough_v, trough_d = v, d
            elif v >= peak_v:                       # 回到前高 → 熊市結束
                out.append((peak_d, trough_d, trough_v / peak_v - 1))
                in_bear, peak_v, peak_d = False, v, d
    if in_bear:
        out.append((peak_d, trough_d, trough_v / peak_v - 1))
    return [{"peak": a.strftime("%Y-%m-%d"), "trough": b.strftime("%Y-%m-%d"), "dd": round(c, 3)} for a, b, c in out]


def _r(x, n=3):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(float(x), n)


def run_market(M):
    g, ind = M["grid"], M["ind"]
    pct = pd.DataFrame({c: pit_pct(ind[c], M["min_pct"]) for c in ind.columns}, index=g)
    # 組分數 = 組內百分位平均,再做一次 PIT 百分位(CISS 的 ECDF 轉換,讓每組可比)
    raw = pd.DataFrame({k: pct[v].mean(axis=1, skipna=True).where(pct[v].notna().sum(axis=1) >= 1) for k, v in M["groups"].items()})
    grp = pd.DataFrame({k: pit_pct(raw[k], M["min_pct"]) for k in raw.columns})
    comp, corr = ciss(grp)
    comp_pct = pit_pct(comp, M["min_pct"])
    cg = pit_pct(M["credit_growth"], M["min_pct"])
    pg = pit_pct(pd.Series(M["price3y"], index=g), M["min_pct"])
    redzone = (cg >= RZ_CREDIT) & (pg >= RZ_PRICE)
    n_hi = (grp >= SIG).sum(axis=1)
    vl_hi = (grp["V"] >= SIG) | (grp["L"] >= SIG)
    # 警示等級(事前定義):2 = 結構脆弱(紅區,或 ≥2 組偏高且含估值/槓桿);1 = 留意(綜合 ≥80 百分位或任一組 ≥90)
    lvl = pd.Series(0, index=g)
    lvl[(comp_pct >= SIG) | (grp >= 0.9).any(axis=1)] = 1
    lvl[redzone | ((n_hi >= 2) & vl_hi)] = 2
    # 事件與報酬
    dd = fwd_maxdd(M["price"], g)
    fr = fwd_ret(M["price"], g)
    event = (dd <= EVENT_DD).where(dd.notna())
    gar = gar_quantile(comp, fr, g)
    return dict(pct=pct, grp=grp, comp=comp, comp_pct=comp_pct, corr=corr, redzone=redzone, cg=cg, pg=pg,
                level=lvl, n_hi=n_hi, dd=dd, fr=fr, event=event, gar=gar)


REDUCE_GAP = 20         # 交易日:訊號中斷 ≥20 日後再出現才算新的一次「減碼日」
REDUCE_POS = 0.5        # 比較用策略:減碼期間持有 50%(其餘現金,保守假設 0% 利息)


def reduce_signals(M, R):
    """主動減碼訊號(日):結構等級 ≥2(上個已完成月底的 PIT 值,次一交易日起適用)且快速觸發成立。"""
    px = M["price"].dropna()
    lvl = R["level"].copy()
    lvl.iloc[:-1] = lvl.iloc[:-1]                      # 月底值;最後一點為當月最新
    struct = V.known_at(lvl.astype(float), px.index, 1)        # 月底收盤算出 → 下一交易日起使用
    trig = M["trig_daily"].reindex(px.index).astype(float)
    on = ((struct >= 2) & (trig >= 1)).astype(bool)
    start = pd.Timestamp(M["eval_start"])
    on_e = on[on.index >= start]
    # 每次「減碼日」= 中斷 ≥ REDUCE_GAP 個交易日後第一次成立
    onsets, last_true = [], None
    arr, idx = on_e.values, on_e.index
    for i in range(len(arr)):
        if arr[i]:
            if last_true is None or i - last_true > REDUCE_GAP:
                onsets.append(i)
            last_true = i
    pxe = px[px.index >= start]
    P = pxe.values

    def fwd(i, n):
        return float(P[i + n] / P[i] - 1) if i + n < len(P) else None

    def fmin(i, n=252):
        return float(P[i + 1:i + n + 1].min() / P[i] - 1) if i + n < len(P) else None
    ev = []
    for i in onsets:
        d = idx[i]
        j = pxe.index.get_loc(d)
        ev.append({"date": d.strftime("%Y-%m-%d"), "price": round(float(P[j]), 2),
                   "r1m": _r(fwd(j, 21)), "r3m": _r(fwd(j, 63)), "r6m": _r(fwd(j, 126)), "r12m": _r(fwd(j, 252)),
                   "maxloss12m": _r(fmin(j)), "level": int(struct.loc[d]) if not np.isnan(struct.loc[d]) else None})
    # 無條件基準(所有交易日)
    base = {}
    for k, n in [("r1m", 21), ("r3m", 63), ("r6m", 126), ("r12m", 252)]:
        v = pd.Series(P[n:] / P[:-n] - 1) if len(P) > n else pd.Series(dtype=float)
        base[k] = _r(v.mean())
    ml = [P[i + 1:i + 253].min() / P[i] - 1 for i in range(0, len(P) - 252, 5)]
    base["maxloss12m"] = _r(float(np.mean(ml))) if ml else None
    sig_avg = {k: _r(np.mean([e[k] for e in ev if e[k] is not None])) if any(e[k] is not None for e in ev) else None
               for k in ("r1m", "r3m", "r6m", "r12m", "maxloss12m")}
    # 策略比較:減碼期間 50%(訊號於收盤得知,次日生效)
    r = pxe.pct_change().fillna(0)
    pos = pd.Series(np.where(on_e.shift(1, fill_value=False).values, REDUCE_POS, 1.0), index=pxe.index)
    strat = (1 + pos * r).cumprod()
    bh = (1 + r).cumprod()

    def stats(eq):
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        dr = eq.pct_change().dropna()
        return {"cagr": _r(eq.iloc[-1] ** (1 / yrs) - 1), "vol": _r(dr.std() * np.sqrt(252)),
                "maxdd": _r((eq / eq.cummax() - 1).min())}
    return {"active": bool(on.iloc[-1]), "struct_today": _r(struct.iloc[-1]), "trig_today": bool(trig.iloc[-1] >= 1),
            "onsets": ev, "avg_after": sig_avg, "base": base, "time_reduced": _r(on_e.mean()),
            "buyhold": stats(bh), "strategy": stats(strat), "window": f"{pxe.index[0]:%Y-%m} ~ {pxe.index[-1]:%Y-%m}",
            "rule": f"結構等級 ≥2 且快速觸發(跌破{M['fast']['ma_name']}或 VIX 5 日漲幅 ≥85 百分位);中斷 ≥{REDUCE_GAP} 交易日後再成立才算新的一次"}


def v1_monthly(page, grid):
    """舊版規則(6 燈 ≥4 亮紅)取月底值,作為同期比較基準。"""
    try:
        s = open(page, encoding="utf-8").read()
        i = s.index("const D=") + len("const D=")
        d, _ = json.JSONDecoder().raw_decode(s, i)
        idx = pd.to_datetime(d["dates"])
        dng = pd.DataFrame({k: d["inds"][k]["dng"] for k in d["slow"]}, index=idx).astype(float)
        cnt = (dng >= 75).sum(axis=1)
        sig = (cnt >= 4).astype(float).where(dng.notna().sum(axis=1) >= 4)
        return V.known_at(sig.dropna(), grid, 0).where(grid >= idx[0])
    except Exception:
        return pd.Series(np.nan, index=grid)


def evaluate(M, R, page):
    g = M["grid"]
    mask = (g >= pd.Timestamp(M["eval_start"])) & R["event"].notna()
    mask = pd.Series(mask, index=g)
    ev = R["event"].fillna(False).astype(bool)
    rows = {}
    for k in PILLARS:
        rows[f"組 {k} ≥80"] = klr(R["grp"][k] >= SIG, ev, mask & R["grp"][k].notna())
    rows["綜合 CISS ≥80"] = klr(R["comp_pct"] >= SIG, ev, mask & R["comp_pct"].notna())
    rows["紅區(信用×價格)"] = klr(R["redzone"], ev, mask & R["cg"].notna() & R["pg"].notna())
    rows["v2 警示 ≥2"] = klr(R["level"] >= 2, ev, mask & R["comp"].notna())
    for k, sig_, m_ in [("綜合 CISS ≥80", R["comp_pct"] >= SIG, mask & R["comp_pct"].notna()),
                        ("紅區(信用×價格)", R["redzone"], mask & R["cg"].notna() & R["pg"].notna()),
                        ("v2 警示 ≥2", R["level"] >= 2, mask & R["comp"].notna())]:
        rows[k]["lift_ci90"] = lift_ci(sig_, ev, m_)
        rows[k]["signal_runs"] = runs(sig_[m_])
        rows[k]["event_runs"] = runs(ev[m_])
    v1 = v1_monthly(page, g)
    m1 = mask & v1.notna()
    if m1.sum() >= 24:
        rows["舊版 6燈≥4(同期)"] = klr(v1 >= 1, ev, m1)
        rows["v2 警示 ≥2(同期)"] = klr(R["level"] >= 2, ev, m1)
        rows["舊版 6燈≥4(同期)"]["lift_ci90"] = lift_ci(v1 >= 1, ev, m1)
        rows["v2 警示 ≥2(同期)"]["lift_ci90"] = lift_ci(R["level"] >= 2, ev, m1)
    peaks = [p for p in bear_peaks(M["price"], start=M["eval_start"])]
    caught = {"v2 警示 ≥2": episodes_caught(R["level"] >= 2, peaks),
              "紅區": episodes_caught(R["redzone"], peaks),
              "綜合 ≥80": episodes_caught(R["comp_pct"] >= SIG, peaks)}
    # GaR 校準:實際報酬低於預測第 10 百分位的比例(理想 ≈ 10%)
    gm = mask & R["gar"].notna() & R["fr"].notna()
    cover = float((R["fr"][gm] < R["gar"][gm]).mean()) if gm.sum() else None
    # 依綜合分數分 3 組的實際結果
    terc = {}
    cm = mask & R["comp_pct"].notna()
    for lo, hi, nm in [(0, 1 / 3, "低"), (1 / 3, 2 / 3, "中"), (2 / 3, 1.01, "高")]:
        mm = cm & (R["comp_pct"] >= lo) & (R["comp_pct"] < hi)
        if mm.sum():
            terc[nm] = {"months": int(mm.sum()), "p_bear": _r(ev[mm].mean()),
                        "median_12m": _r(R["fr"][mm].median()), "p10_12m": _r(R["fr"][mm].quantile(0.1))}
    return {"window": f"{g[mask].min():%Y-%m} ~ {g[mask].max():%Y-%m}" if mask.any() else None,
            "bears": bear_periods(M["price"], start=str(pd.Timestamp(M["eval_start"]) - pd.DateOffset(years=2))[:10]),
            "table": rows, "episodes": caught, "gar_coverage": _r(cover), "by_composite": terc,
            "n_bears": len(peaks)}


# ======================================================================
# 輸出
# ======================================================================
def snapshot(M, R):
    g = M["grid"]
    t = g[-1]
    pillars = {}
    for k, cols in M["groups"].items():
        pillars[k] = {"name": PILLARS[k], "score": _r(R["grp"][k].iloc[-1]),
                      "items": [{"key": c, "label": M["labels"][c], "value": _r(M["ind"][c].iloc[-1], 4),
                                 "disp": fmt_disp(M["ind"][c].iloc[-1], M["disp"][c]),
                                 "pct": _r(R["pct"][c].iloc[-1])} for c in cols]}
    hist = pd.DataFrame({"comp": R["comp"], "comp_pct": R["comp_pct"], "level": R["level"], "redzone": R["redzone"].astype(int),
                         "gar": R["gar"], **{k: R["grp"][k] for k in PILLARS}})
    hist = hist[hist.index >= pd.Timestamp(M["eval_start"]) - pd.DateOffset(years=2)]
    fast = M["fast"]
    trig = fast["below_ma"] or (fast["vix_spike_pct"] is not None and fast["vix_spike_pct"] >= 0.85)
    lvl = int(R["level"].iloc[-1])
    level = 3 if (trig and lvl >= 2) else lvl          # 3 = 結構脆弱 + 已觸發 → 主動減碼
    return {
        "code": M["code"], "name": M["name"], "asof": t.strftime("%Y-%m-%d"),
        "level": level, "struct_level": lvl, "trigger": bool(trig), "fast": fast,
        "composite": _r(R["comp"].iloc[-1]), "composite_pct": _r(R["comp_pct"].iloc[-1]),
        "redzone": bool(R["redzone"].iloc[-1]), "credit_growth_pct": _r(R["cg"].iloc[-1]), "price_growth_pct": _r(R["pg"].iloc[-1]),
        "gar10": _r(R["gar"].dropna().iloc[-1]) if R["gar"].notna().any() else None,
        "pillars": pillars,
        "corr": {"keys": list(PILLARS), "m": [[_r(x, 2) for x in row] for row in R["corr"]]} if R["corr"] is not None else None,
        "history": {"dates": [d.strftime("%Y-%m") for d in hist.index],
                    **{c: [_r(x) for x in hist[c]] for c in hist.columns}},
        "status": {k: v for k, v in V.STATUS.items()},
    }


LEVEL_TXT = {0: ("平穩", "結構面未見明顯脆弱"), 1: ("留意", "部分類別偏高,持續觀察"),
             2: ("結構脆弱", "紅區或 ≥2 類(含估值/槓桿)偏高:不加槓桿、降低部位上限"),
             3: ("警戒:脆弱且已觸發", "結構偏高且快速觸發成立(跌破長期均線或 VIX 急升)")}


def main():
    inject = "--inject" in sys.argv
    out, bt = {"generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), "markets": {}}, {}
    for builder, page in [(build_tw, "index.html"), (build_us, "us.html")]:
        try:
            M = builder()
            R = run_market(M)
            snap = snapshot(M, R)
            snap["backtest"] = evaluate(M, R, page)
            snap["reduce"] = reduce_signals(M, R)
            out["markets"][M["code"]] = snap
            print(f"[{M['code']}] {snap['asof']} 等級 {snap['level']} 綜合 {snap['composite']} (pct {snap['composite_pct']}) "
                  f"紅區 {snap['redzone']} GaR10 {snap['gar10']}")
            for k, p in snap["pillars"].items():
                print(f"   {k} {p['name']:12} {p['score']}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[{builder.__name__}] 失敗:{e}")
    if not out["markets"]:
        return 1
    # 有市場失敗時保留上次結果
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8"))
            for k, v in old.get("markets", {}).items():
                out["markets"].setdefault(k, {**v, "stale": True})
        except Exception:
            pass
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"→ {OUT}")
    if inject:
        import v2_render
        v2_render.inject_all(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
