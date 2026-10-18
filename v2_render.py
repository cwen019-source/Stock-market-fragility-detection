#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脆弱度 v2 — 面板(嵌入 index.html / us.html)與 methodology.html(中英雙語)。

面板以中文產生,英文由 i18n.js 字典翻譯;方法論頁直接輸出中、英兩份內容(依 <html lang> 切換)。
"""
import html as H
import json
import re

import pandas as pd

PAGES = {"TW": "index.html", "US": "us.html"}
BLOCK = re.compile(r"<!--V2PANEL-->.*?<!--/V2PANEL-->\n?", re.S)

LEVEL = {
    "zh": {0: ("平穩", "結構面未見明顯脆弱", "ok"),
           1: ("留意", "部分類別偏高,持續觀察", "info"),
           2: ("結構脆弱", "紅區,或 ≥2 類(含估值/槓桿)偏高 → 不加槓桿、調低部位上限", "warn"),
           3: ("主動減碼", "結構脆弱且快速觸發成立(跌破長期均線或 VIX 急升)→ 主動降低多單部位", "red")},
    "en": {0: ("Calm", "No meaningful structural vulnerability", "ok"),
           1: ("Watch", "Some categories elevated — keep monitoring", "info"),
           2: ("Vulnerable", "Red zone, or ≥2 categories (incl. valuation/leverage) elevated → no added leverage, lower position cap", "warn"),
           3: ("Reduce longs", "Structurally vulnerable AND fast trigger fired (below long-term MA or VIX spike) → actively cut long exposure", "red")},
}
PNAME = {"zh": {"V": "估值 vs 基本面", "L": "槓桿與信用", "M": "景氣循環(實體經濟)", "S": "市場壓力"},
         "en": {"V": "Valuation vs fundamentals", "L": "Leverage & credit", "M": "Business cycle (real economy)", "S": "Market stress"}}
MNAME = {"zh": {"TW": "台股(加權指數)", "US": "美股(S&P 500)"}, "en": {"TW": "Taiwan (TAIEX)", "US": "US (S&P 500)"}}
IND_EN = {"cape": "Shiller CAPE", "mv_gdp": "Market cap / GDP (Buffett)", "mv_cp": "Market cap / corporate profits",
          "credit_gap3y": "Private credit/GDP, 3y change", "margin_gdp_1y": "Margin debt/GDP, 1y change",
          "unemp_rise": "Unemployment rise from 12m low", "curve_inv": "10Y−3M spread (inversion = risk)",
          "ip_weak": "Industrial production YoY (contraction = risk)", "baa_spread": "BAA credit spread",
          "nfci": "Chicago Fed financial conditions", "vix": "VIX (monthly avg)",
          "agg_pe": "Large-cap weighted P/E", "price_vs_sales3y": "3y price gain − sales growth",
          "margin_gdp_3y": "Margin debt vs GDP, 3y excess growth", "margin_6m": "Margin debt, 6m growth",
          "rev_weak": "Large-cap revenue YoY (low = risk)", "rev_decel": "Revenue YoY, 6m change (slowdown = risk)",
          "foreign_sell": "Foreign net buying, 20d (selling = risk)", "twd_weak": "TWD 20d depreciation"}

CSS = """<style>
.v2{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:18px 20px;margin:18px 0 10px}
.v2 .h{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.v2 .t{font-weight:700;font-size:15px}
.v2 .lv{font-size:12px;font-weight:700;padding:3px 10px;border-radius:999px;color:#fff}
.v2 .lv.ok{background:var(--ok)}.v2 .lv.info{background:var(--series-1)}.v2 .lv.warn{background:var(--warn)}.v2 .lv.red{background:var(--red)}
.v2 .h a{margin-left:auto;font-size:12.5px;color:var(--series-1);text-decoration:none}
.v2 .s{color:var(--ts);font-size:12.5px;margin:6px 0 12px}
.v2 .k{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:12px}
.v2 .kc{border:1px solid var(--border);border-radius:12px;padding:10px 12px;min-width:0}
.v2 .kl{font-size:11.5px;color:var(--muted)}.v2 .kv{font-size:20px;font-weight:700;margin:2px 0;font-variant-numeric:tabular-nums}
.v2 .kd{font-size:11.5px;color:var(--ts);overflow-wrap:anywhere}
.v2 .g{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.v2 .p{border:1px solid var(--border);border-radius:12px;padding:10px 12px;min-width:0}
.v2 .pt{display:flex;justify-content:space-between;align-items:baseline;font-size:12.5px;font-weight:650;gap:6px}
.v2 .pt b{font-size:18px;font-variant-numeric:tabular-nums}
.v2 .bar{height:6px;border-radius:3px;background:var(--border);margin:6px 0 8px;position:relative;overflow:hidden}
.v2 .bar i{position:absolute;left:0;top:0;bottom:0;border-radius:3px}
.v2 .bar s{position:absolute;left:80%;top:-2px;bottom:-2px;border-left:1px dashed var(--ts);text-decoration:none}
.v2 .it{display:grid;grid-template-columns:1fr auto auto;gap:6px;font-size:11.5px;padding:2px 0;color:var(--ts);align-items:baseline}
.v2 .it span:first-child{min-width:0;overflow-wrap:anywhere}.v2 .it .v{font-variant-numeric:tabular-nums;color:var(--tp)}
.v2 .it .q{font-variant-numeric:tabular-nums;min-width:26px;text-align:right;font-weight:650}
.v2 .ch{margin-top:12px;overflow-x:auto}.v2 .ch svg{width:100%;min-width:560px;height:auto;display:block}
.v2 .f{font-size:11.5px;color:var(--muted);margin-top:8px;line-height:1.55}
.v1note{font-size:12px;color:var(--muted);margin:4px 2px 0}
@media(max-width:820px){.v2 .k,.v2 .g{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:420px){.v2{padding:14px}.v2 .k,.v2 .g{grid-template-columns:minmax(0,1fr)}}
html:not([lang^=en]) .L-en,html[lang^=en] .L-zh{display:none}
</style>"""


def col(p):
    if p is None:
        return "var(--muted)"
    return "var(--red)" if p >= 0.8 else "var(--warn)" if p >= 0.6 else "var(--ok)"


def pct(p):
    return "–" if p is None else f"{round(p * 100)}"


def _p(x):
    return "–" if x is None else f"{x * 100:.0f}%"


def _ps(x, d=1):
    return "–" if x is None else f"{x * 100:+.{d}f}%"


def chart(m, years=None, w=800, h=180, lang="zh"):
    """綜合分數(PIT 百分位)歷史 + 熊市陰影 + 警示≥2(底部橘點)+ 主動減碼日(頂部紅三角)。"""
    hs = m["history"]
    dates = pd.to_datetime(hs["dates"])
    vals, lv = hs["comp_pct"], hs["level"]
    if years:
        start = dates[-1] - pd.DateOffset(years=years)
        keep = [i for i, d in enumerate(dates) if d >= start]
    else:
        first = [i for i, v in enumerate(vals) if v is not None]
        keep = list(range(first[0] if first else 0, len(dates)))
    dates = dates[keep]
    vals = [vals[i] for i in keep]
    lv = [lv[i] for i in keep]
    if len(dates) < 2:
        return ""
    L, R, T, B = 34, 8, 16, 22
    t0, t1 = dates[0].value, dates[-1].value
    X = lambda d: L + (pd.Timestamp(d).value - t0) / (t1 - t0) * (w - L - R)
    Y = lambda v: T + (1 - v) * (h - T - B)
    bear_t = "Bear market" if lang == "en" else "熊市"
    red_t = "Reduce-longs date" if lang == "en" else "主動減碼日"
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{"Composite fragility history" if lang == "en" else "綜合脆弱度歷史"}">']
    for b in m.get("backtest", {}).get("bears", []):
        a, z = max(pd.Timestamp(b["peak"]), dates[0]), min(pd.Timestamp(b["trough"]), dates[-1])
        if z > a:
            parts.append(f'<rect x="{X(a):.1f}" y="{T}" width="{X(z) - X(a):.1f}" height="{h - T - B}" fill="var(--red)" opacity=".13">'
                         f'<title>{bear_t} {b["peak"]} → {b["trough"]} ({b["dd"] * 100:.0f}%)</title></rect>')
    for v in (0.2, 0.5, 0.8):
        dash = ' stroke-dasharray="4 3"' if v == 0.8 else ""
        parts.append(f'<line x1="{L}" x2="{w - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="var(--border)"{dash}/>'
                     f'<text x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end" font-size="10" fill="var(--muted)">{int(v * 100)}</text>')
    for i, d in enumerate(dates):
        if lv[i] is not None and lv[i] >= 2:
            parts.append(f'<rect x="{X(d) - 1.2:.1f}" y="{h - B + 3}" width="2.4" height="5" fill="var(--warn)"/>')
    seg, segs = [], []
    for d, v in zip(dates, vals):
        if v is None:
            if len(seg) > 1:
                segs.append(seg)
            seg = []
        else:
            seg.append(f"{X(d):.1f},{Y(v):.1f}")
    if len(seg) > 1:
        segs.append(seg)
    for sg in segs:
        parts.append(f'<polyline points="{" ".join(sg)}" fill="none" stroke="var(--series-1)" stroke-width="1.6"/>')
    for e in m.get("reduce", {}).get("onsets", []):          # 主動減碼日:頂部紅色倒三角
        d = pd.Timestamp(e["date"])
        if dates[0] <= d <= dates[-1]:
            x = X(d)
            r12 = "" if e.get("maxloss12m") is None else (f" · 12m worst {e['maxloss12m'] * 100:.0f}%" if lang == "en" else f" · 之後 12 個月最深 {e['maxloss12m'] * 100:.0f}%")
            parts.append(f'<path d="M{x - 4:.1f},{T - 12} L{x + 4:.1f},{T - 12} L{x:.1f},{T - 4} Z" fill="var(--red)">'
                         f'<title>{red_t} {e["date"]}{r12}</title></path>')
    yrs = sorted({d.year for d in dates})
    step = max(1, len(yrs) // 8)
    for y in yrs[::step]:
        x = X(pd.Timestamp(f"{y}-01-01"))
        if L <= x <= w - R:
            parts.append(f'<text x="{x:.1f}" y="{h - 4}" text-anchor="middle" font-size="10" fill="var(--muted)">{y}</text>')
    parts.append("</svg>")
    return "".join(parts)


def B(zh, en):
    """雙語片段:依 <html lang> 顯示其一(i18n.js 不翻譯)。"""
    return f'<span class="L-zh" data-noi18n>{zh}</span><span class="L-en" data-noi18n>{en}</span>'


def disp_en(d):
    """英文顯示值:億 → NT$ 十億。"""
    m = re.match(r"^([+\-−]?[\d,]+(?:\.\d+)?)億$", d or "")
    if m:
        v = float(m.group(1).replace(",", "").replace("−", "-")) / 10
        return f"{'+' if v > 0 else '−' if v < 0 else ''}NT${abs(v):,.1f}B"
    return d


def bt_lines(m):
    b = m.get("backtest") or {}
    t = b.get("table", {})
    a = t.get("v2 警示 ≥2", {})
    eps = b.get("episodes", {}).get("v2 警示 ≥2", [])
    warned = sum(e["warned"] for e in eps)
    o, n = t.get("舊版 6燈≥4(同期)"), t.get("v2 警示 ≥2(同期)")
    zh = (f"回測 {b.get('window', '')}:警示≥2 之後 12 個月內比當時價位低 ≥20% 的機率 {_p(a.get('p_event_given_signal'))}"
          f"(全期平均 {_p(a.get('p_event_uncond'))});{len(eps)} 次熊市中 {warned} 次事前 12 個月內有警示。")
    en = (f"Backtest {b.get('window', '')}: after a level ≥2 warning, the chance of trading ≥20% below that price within 12 months was "
          f"{_p(a.get('p_event_given_signal'))} (vs {_p(a.get('p_event_uncond'))} on average); {warned} of {len(eps)} bear markets were preceded by a warning within 12 months. ")
    if o and n:
        zh += f"同期舊版 6 燈≥4:{_p(o.get('p_event_given_signal'))} vs v2:{_p(n.get('p_event_given_signal'))}。"
        en += f"Same period, old 6-light rule: {_p(o.get('p_event_given_signal'))} vs v2: {_p(n.get('p_event_given_signal'))}. "
    rd = m.get("reduce", {})
    rs, bh = rd.get("strategy", {}), rd.get("buyhold", {})
    if rs and bh:
        zh += (f"主動減碼日回測({rd.get('window', '')}):之後 12 個月最深跌幅平均 {_ps(rd['avg_after'].get('maxloss12m'), 0)}"
               f"(全部交易日 {_ps(rd['base'].get('maxloss12m'), 0)});減碼期間持有 50% 的策略最大回撤 {_ps(rs.get('maxdd'), 0)} vs 買進持有 {_ps(bh.get('maxdd'), 0)},"
               f"年化報酬 {_ps(rs.get('cagr'))} vs {_ps(bh.get('cagr'))} —— 降風險工具,不是增加報酬的工具。")
        en += (f"Reduce-longs dates ({rd.get('window', '')}): worst drop within the next 12 months averaged {_ps(rd['avg_after'].get('maxloss12m'), 0)} "
               f"(all days {_ps(rd['base'].get('maxloss12m'), 0)}); holding 50% while the signal is on gave max drawdown {_ps(rs.get('maxdd'), 0)} vs "
               f"{_ps(bh.get('maxdd'), 0)} buy-and-hold, return {_ps(rs.get('cagr'))}/yr vs {_ps(bh.get('cagr'))}/yr — a risk-control tool, not a return booster. ")
    return zh, en


def panel(m):
    lv = m.get("level", 0)
    name, desc, cls = LEVEL["zh"][lv]
    name_en, desc_en, _ = LEVEL["en"][lv]
    f = m["fast"]
    ma_en = "200-day MA" if m["code"] == "US" else "240-day MA"
    rd = m.get("reduce", {})
    last = rd.get("onsets", [])[-1]["date"] if rd.get("onsets") else "–"
    gar = m.get("gar10")
    red_on = rd.get("active")
    sl = m.get("struct_level", 0)
    vsp = pct(f.get("vix_spike_pct"))
    k = [
        (B("綜合脆弱度(CISS)", "Composite fragility (CISS)"), pct(m.get("composite_pct")),
         B("歷史百分位;多類同時偏高才會高", "Historical percentile; high only when several categories are elevated together"), col(m.get("composite_pct"))),
        (B("紅區:信用 × 股價", "Red zone: credit × price"), B("是", "Yes") if m.get("redzone") else B("否", "No"),
         B(f"信用成長 {pct(m.get('credit_growth_pct'))} / 3年漲幅 {pct(m.get('price_growth_pct'))} 百分位",
           f"Credit growth {pct(m.get('credit_growth_pct'))}th / 3-yr gain {pct(m.get('price_growth_pct'))}th percentile"),
         "var(--red)" if m.get("redzone") else "var(--ok)"),
        (B("12 個月壞情境(第10百分位)", "12-month bad case (10th percentile)"), "–" if gar is None else f"{gar * 100:+.0f}%",
         B("Growth-at-Risk:10% 機率比這更差", "Growth-at-Risk: 10% chance of worse"),
         "var(--red)" if (gar is not None and gar <= -0.15) else "var(--warn)" if (gar is not None and gar <= -0.08) else "var(--ok)"),
        (B("主動減碼訊號", "Reduce-longs signal"), B("成立", "ON") if red_on else B("未成立", "Off"),
         B(f"結構 {sl}(需 ≥2)· 跌破{H.escape(f['ma_name'])}:{'是' if f['below_ma'] else '否'}(距 {f['dist_ma'] * 100:+.1f}%)· VIX 5日急升 {vsp} 百分位(需 ≥85)· 最近一次 {last}",
           f"Structural level {sl} (needs ≥2) · below {ma_en}: {'yes' if f['below_ma'] else 'no'} ({f['dist_ma'] * 100:+.1f}%) · VIX 5-day spike {vsp}th pct (needs ≥85) · last signal {last}"),
         "var(--red)" if red_on else "var(--ok)"),
    ]
    kh = "".join(f'<div class="kc"><div class="kl">{a}</div><div class="kv" style="color:{c}">{b}</div><div class="kd">{d}</div></div>' for a, b, d, c in k)
    ph = ""
    for key, p in m["pillars"].items():
        sc = p.get("score")
        items = "".join(f'<div class="it"><span>{B(H.escape(i["label"]), H.escape(IND_EN.get(i["key"], i["label"])))}</span>'
                        f'<span class="v">{B(H.escape(i.get("disp", "–")), H.escape(disp_en(i.get("disp", "–"))))}</span>'
                        f'<span class="q" style="color:{col(i.get("pct"))}">{pct(i.get("pct"))}</span></div>' for i in p["items"])
        ph += (f'<div class="p"><div class="pt"><span>{key} · {B(PNAME["zh"][key], PNAME["en"][key])}</span><b style="color:{col(sc)}">{pct(sc)}</b></div>'
               f'<div class="bar"><i style="width:{(sc or 0) * 100:.0f}%;background:{col(sc)}"></i><s></s></div>{items}</div>')
    stale = any(not str(v).startswith(("net", "cache")) for v in m.get("status", {}).values()) or m.get("stale")
    bz, be = bt_lines(m)
    foot = B(f"藍線=綜合脆弱度歷史百分位(虛線 80)· 紅色陰影=實際熊市 · 底部橘點=結構脆弱(≥2)月份 · "
             f"<b style='color:var(--red)'>頂部紅色 ▼ = 主動減碼日</b>(結構 ≥2 且快速觸發,滑過可看日期與之後最深跌幅)。數字 = PIT 歷史百分位。{H.escape(bz)}非投資建議。",
             f"Blue = composite fragility percentile (dashed line 80) · red bands = actual bear markets · orange ticks = vulnerable (≥2) months · "
             f"<b style='color:var(--red)'>red ▼ on top = reduce-longs dates</b> (structural ≥2 and fast trigger; hover for the date and the worst drop that followed). "
             f"Numbers are point-in-time historical percentiles. {H.escape(be)}Not investment advice.")
    return (f'<!--V2PANEL-->{CSS}<div class="v2" id="v2">'
            f'<div class="h"><span class="t">{B("結構風險 v2 · 四類脆弱性", "Structural risk v2 · four vulnerability categories")}</span>'
            f'<span class="lv {cls}">{B(name, name_en)}</span><a href="methodology.html">{B("方法與回測 →", "Method & backtest →")}</a></div>'
            f'<div class="s">{B(H.escape(desc), H.escape(desc_en))} · {B("資料至", "Data through")} {m["asof"]}'
            f'{B(" · ⚠ 部分資料沿用快取", " · ⚠ some data from cache") if stale else ""}</div>'
            f'<div class="k">{kh}</div><div class="g">{ph}</div>'
            f'<div class="ch">{chart(m, years=15)}</div><div class="f">{foot}</div></div>'
            f'<div class="v1note">{B("↓ 以下為舊版 6 燈(融資三指標同源、重複計票;保留供參考,警報信已改用 v2)", "↓ Below: the original 6-light dashboard (three margin-debt signals double-count; kept for reference — alert emails now use v2)")}</div><!--/V2PANEL-->\n')


def inject_pages(data):
    for code, page in PAGES.items():
        m = data["markets"].get(code)
        try:
            s = open(page, encoding="utf-8").read()
        except FileNotFoundError:
            continue
        s = BLOCK.sub("", s)
        if not m:
            open(page, "w", encoding="utf-8").write(s)
            continue
        i = s.find('<div class="hero">')
        if i < 0:
            print(f"   [skip] {page} 找不到插入點")
            continue
        open(page, "w", encoding="utf-8").write(s[:i] + panel(m) + s[i:])
        print(f"   v2 面板已嵌入 {page}")


# ================================================================ 方法論頁(中/英)
REFS = [
    ("Federal Reserve — Financial Stability Report", "https://www.federalreserve.gov/publications/2026-may-financial-stability-report-overview.htm"),
    ("Holló, Kremer & Lo Duca (2012) — CISS: A Composite Indicator of Systemic Stress, ECB WP 1426", "https://ECB.eu/pub/pdf/scpwps/ecbwp1426.pdf"),
    ("Greenwood, Hanson, Shleifer & Sørensen (2022) — Predictable Financial Crises, Journal of Finance", "https://www.nber.org/system/files/working_papers/w27396/w27396.pdf"),
    ("Adrian et al. (2018) — The Term Structure of Growth-at-Risk, IMF WP 18/180", "https://www.imf.org/en/publications/wp/issues/2018/08/02/the-term-structure-of-growth-at-risk-46150"),
    ("Kaminsky, Lizondo & Reinhart (1998) — Leading Indicators of Currency Crises", "https://mpra.ub.uni-muenchen.de/24577/1/MPRA_paper_24577.pdf"),
    ("Federal Reserve IFDP 675 — Do Indicators of Financial Crises Work?", "https://www.federalreserve.gov/econres/ifdp/do-indicators-of-financial-crises-work-an-evaluation-of-an-early-warning-system.htm"),
    ("Goldman Sachs Bear Market Risk Indicator", "https://www.cnbc.com/2018/11/12/goldmans-bear-market-indicator-shows-zero-returns-over-next-year.html"),
]

SOURCES = {
    "US": [("V", "cape", "multpl.com (Shiller data)", "M", "0"),
           ("V", "mv_gdp", "FRED NCBEILQ027S ÷ GDP (rolled forward with S&P since quarter end)", "Q", "75d"),
           ("V", "mv_cp", "FRED NCBEILQ027S ÷ CP", "Q", "75d / 65d"),
           ("L", "credit_gap3y", "FRED QUSPAM770A (BIS)", "Q", "160d"),
           ("L", "margin_gdp_1y", "FRED BOGZ1FL663067003Q (broker margin receivables)", "Q", "75d"),
           ("M", "unemp_rise", "FRED UNRATE", "M", "7d"),
           ("M", "curve_inv", "FRED GS10 − TB3MS", "M", "1d"),
           ("M", "ip_weak", "FRED INDPRO", "M", "17d"),
           ("S", "baa_spread", "FRED BAA − GS10", "M", "1d"),
           ("S", "nfci", "FRED NFCI", "W", "6d"),
           ("S", "vix", "FRED VIXCLS (VXOCLS before 1990)", "D", "0")],
    "TW": [("V", "agg_pe", "FinMind, top-25 caps: Σcap / Σ(cap/PE)", "D", "0"),
           ("V", "price_vs_sales3y", "TAIEX vs chain-linked revenue of the same firms", "M", "10th of next month"),
           ("L", "margin_gdp_3y", "FinMind margin balance vs IMF WEO nominal GDP", "D/Y", "GDP 90d"),
           ("L", "margin_6m", "FinMind margin balance", "D", "0"),
           ("M", "rev_weak", "FinMind monthly revenue (3m avg)", "M", "10th of next month"),
           ("M", "rev_decel", "same", "M", "10th of next month"),
           ("S", "foreign_sell", "FinMind institutional flows", "D", "0"),
           ("S", "twd_weak", "FinMind FX", "D", "0"),
           ("S", "vix", "FRED VIXCLS (previous US close)", "D", "1d")],
}
FREQ = {"zh": {"D": "日", "W": "週", "M": "月", "Q": "季", "Y": "年", "D/Y": "日/年"}, "en": {}}
RULE_EN = {"組 V ≥80": "Group V ≥80", "組 L ≥80": "Group L ≥80", "組 M ≥80": "Group M ≥80", "組 S ≥80": "Group S ≥80",
           "綜合 CISS ≥80": "Composite CISS ≥80", "紅區(信用×價格)": "Red zone (credit × price)", "v2 警示 ≥2": "v2 level ≥2",
           "舊版 6燈≥4(同期)": "Old 6-light ≥4 (same period)", "v2 警示 ≥2(同期)": "v2 level ≥2 (same period)",
           "紅區": "Red zone", "綜合 ≥80": "Composite ≥80"}
TERC_EN = {"低": "Low", "中": "Middle", "高": "High"}


def T(lang, zh, en):
    return en if lang == "en" else zh


def bt_table(m, lang):
    t = m["backtest"]["table"]
    hdr = T(lang, ["規則", "樣本月數", "發出訊號月份", "命中率", "假警報率", "NSR", "P(事件|訊號)", "P(事件)", "提升倍數", "90% 區間*"],
            ["Rule", "Months", "Signal on", "Hit rate", "False alarms", "NSR", "P(event|signal)", "P(event)", "Lift", "90% CI*"])
    rows = ""
    for k, v in t.items():
        lift = "–" if v["lift"] is None else f"{v['lift']:.2f}×"
        ci = "–" if not v.get("lift_ci90") else f"{v['lift_ci90'][0]:.2f}–{v['lift_ci90'][1]:.2f}×"
        nsr = "–" if v["nsr"] is None else f"{v['nsr']:.2f}"
        rows += (f"<tr><td>{H.escape(RULE_EN.get(k, k) if lang == 'en' else k)}</td><td>{v['months']}</td><td>{_p(v['signal_share'])}</td>"
                 f"<td>{_p(v['hit_rate'])}</td><td>{_p(v['false_alarm'])}</td><td>{nsr}</td><td><b>{_p(v['p_event_given_signal'])}</b></td>"
                 f"<td>{_p(v['p_event_uncond'])}</td><td>{lift}</td><td>{ci}</td></tr>")
    return "<div class='tw'><table><thead><tr>" + "".join(f"<th>{x}</th>" for x in hdr) + "</tr></thead><tbody>" + rows + "</tbody></table></div>"


def reduce_section(m, lang):
    rd = m.get("reduce")
    if not rd:
        return ""
    on = rd["onsets"]
    hdr = T(lang, ["減碼日", "當時指數", "1 個月後", "3 個月後", "6 個月後", "12 個月後", "之後 12 個月最深"],
            ["Date", "Index", "+1m", "+3m", "+6m", "+12m", "Worst within 12m"])
    rows = "".join(f"<tr><td>{e['date']}</td><td>{e['price']:,.0f}</td><td>{_ps(e['r1m'])}</td><td>{_ps(e['r3m'])}</td>"
                   f"<td>{_ps(e['r6m'])}</td><td>{_ps(e['r12m'])}</td><td><b>{_ps(e['maxloss12m'])}</b></td></tr>" for e in reversed(on))
    a, b = rd["avg_after"], rd["base"]
    summ = (f"<tr class='sum'><td>{T(lang, '減碼日平均', 'Average after signal')}</td><td></td><td>{_ps(a['r1m'])}</td><td>{_ps(a['r3m'])}</td>"
            f"<td>{_ps(a['r6m'])}</td><td>{_ps(a['r12m'])}</td><td><b>{_ps(a['maxloss12m'])}</b></td></tr>"
            f"<tr class='sum'><td>{T(lang, '全部交易日平均', 'All trading days')}</td><td></td><td>{_ps(b['r1m'])}</td><td>{_ps(b['r3m'])}</td>"
            f"<td>{_ps(b['r6m'])}</td><td>{_ps(b['r12m'])}</td><td>{_ps(b['maxloss12m'])}</td></tr>")
    s, h = rd["strategy"], rd["buyhold"]
    strat = (f"<div class='tw'><table><tr><th></th><th>{T(lang, '年化報酬', 'CAGR')}</th><th>{T(lang, '年化波動', 'Volatility')}</th>"
             f"<th>{T(lang, '最大回撤', 'Max drawdown')}</th></tr>"
             f"<tr><td>{T(lang, '買進持有', 'Buy & hold')}</td><td>{_ps(h['cagr'])}</td><td>{_p(h['vol'])}</td><td>{_ps(h['maxdd'], 0)}</td></tr>"
             f"<tr><td>{T(lang, '減碼期間持有 50%', '50% exposure while signal is on')}</td><td>{_ps(s['cagr'])}</td><td>{_p(s['vol'])}</td>"
             f"<td><b>{_ps(s['maxdd'], 0)}</b></td></tr></table></div>")
    status = T(lang, f"今天:{'<b style=\"color:var(--red)\">成立</b>' if rd['active'] else '未成立'}(結構等級 {m.get('struct_level')},快速觸發 {'是' if rd['trig_today'] else '否'})",
               f"Today: {'<b style=\"color:var(--red)\">ON</b>' if rd['active'] else 'off'} (structural level {m.get('struct_level')}, fast trigger {'yes' if rd['trig_today'] else 'no'})")
    note = T(lang,
             f"規則(事前固定):{H.escape(rd['rule'])}。訊號於收盤得知、次日生效;策略以指數價格計算(不含股息),現金報酬假設 0%(保守)。"
             f"減碼期間占 {_p(rd['time_reduced'])} 的交易日。<b>解讀:</b>減碼日之後確實較常出現較深的跌幅,依訊號降到 50% 主要降低最大回撤與波動,"
             f"但會少賺一些報酬 —— 這是風險控管工具,不是擇時賺錢工具。",
             f"Rule (fixed in advance): structural level ≥2 AND fast trigger (close below the long-term moving average, or VIX 5-day change ≥ 85th pct); "
             f"a new date is counted only after ≥20 trading days without the signal. Signal known at the close, acted on next day; index prices "
             f"(no dividends), cash earns 0% (conservative). The signal was on {_p(rd['time_reduced'])} of trading days. <b>Reading:</b> reduce dates are "
             f"followed by deeper drawdowns on average; cutting to 50% mainly lowers max drawdown and volatility at a small cost in return — a "
             f"risk-control tool, not a way to beat the market.")
    return (f"<h3>{T(lang, '主動減碼日(圖上紅色 ▼)', 'Reduce-longs dates (red ▼ on the chart)')} · {rd['window']}</h3>"
            f"<p>{status}</p><div class='tw'><table><thead><tr>" + "".join(f"<th>{x}</th>" for x in hdr) +
            f"</tr></thead><tbody>{summ}{rows}</tbody></table></div><h3>{T(lang, '依訊號減碼 vs 買進持有', 'Acting on the signal vs buy & hold')}</h3>"
            f"{strat}<p class='note'>{note}</p>")


def market_section(m, lang):
    code = m["code"]
    b = m["backtest"]
    eps = "".join(f"<li>{H.escape(RULE_EN.get(rule, rule) if lang == 'en' else rule)}: " +
                  " ".join(f"<span class='ep {'y' if e['warned'] else 'n'}'>{e['peak']}{'✓' if e['warned'] else '✗'}</span>" for e in lst) + "</li>"
                  for rule, lst in b["episodes"].items())
    terc = "".join(f"<tr><td>{TERC_EN.get(k, k) if lang == 'en' else k}</td><td>{v['months']}</td><td>{_p(v['p_bear'])}</td>"
                   f"<td>{_p(v['median_12m'])}</td><td>{_p(v['p10_12m'])}</td></tr>" for k, v in b["by_composite"].items())
    labels = {i["key"]: i["label"] for p in m["pillars"].values() for i in p["items"]}
    src = "".join(f"<tr><td>{a}</td><td>{H.escape(IND_EN[k] if lang == 'en' else labels.get(k, k))}</td><td>{H.escape(s)}</td>"
                  f"<td>{FREQ[lang].get(f, f)}</td><td>{l}</td></tr>" for a, k, s, f, l in SOURCES[code])
    corr = m.get("corr")
    ch = ""
    if corr:
        ks = corr["keys"]
        ch = ("<div class='tw'><table class='c'><tr><th></th>" + "".join(f"<th>{k}</th>" for k in ks) + "</tr>" +
              "".join(f"<tr><th>{ks[i]}</th>" + "".join(f"<td>{x:.2f}</td>" for x in row) + "</tr>" for i, row in enumerate(corr["m"])) + "</table></div>")
    surv = T(lang, "<b>台股注意:</b>估值與營收使用目前前 25 大權值股,含存活者偏誤,台股 V/M 類的回測可能偏樂觀。<br>",
             "<b>Taiwan caveat:</b> valuation and revenue use today's 25 largest companies (survivorship bias), so Taiwan's V/M results may be optimistic.<br>") if code == "TW" else ""
    lvl = LEVEL[lang][m["level"]][0]
    return f"""
<section><h2>{MNAME[lang][code]} · {T(lang, '目前', 'now')}: {lvl} · {T(lang, '回測', 'backtest')} {b['window']}({b['n_bears']} {T(lang, '次熊市', 'bear markets')})</h2>
<div class="chart">{chart(m, lang=lang)}</div>
<p class="note">{T(lang, '藍線 = 綜合脆弱度歷史百分位;紅色陰影 = 熊市(高點→低點 ≥20%);底部橘點 = 結構脆弱(≥2)月份;頂部紅 ▼ = 主動減碼日。',
                  'Blue = composite fragility (historical percentile); red bands = bear markets (peak→trough ≥20%); orange ticks = vulnerable (level ≥2) months; red ▼ = reduce-longs dates.')}</p>
{reduce_section(m, lang)}
<h3>{T(lang, '訊號評估(事件 = 12 個月內比當時價位低 ≥20%)', 'Signal evaluation (event = price ≥20% below the signal-date level within 12 months)')}</h3>{bt_table(m, lang)}
<p class="note">{T(lang, '*90% 區間:24 個月區塊的移動區塊 bootstrap,處理視窗重疊的自相關;下緣 &gt;1× 才算穩健優於隨機。', '*90% CI: moving-block bootstrap with 24-month blocks to handle overlapping windows; a lower bound &gt;1× means reliably better than chance.')}
{surv}{T(lang, '命中率 = 事件月份中有訊號的比例;假警報率 = 非事件月份中有訊號的比例;NSR = 假警報率 ÷ 命中率(&lt;1 才有資訊,&lt;0.5 算好);提升倍數 = P(事件|訊號) ÷ P(事件)。',
        'Hit rate = share of event months with a signal; false alarms = share of non-event months with a signal; NSR = false alarms ÷ hit rate (&lt;1 is informative, &lt;0.5 good); lift = P(event|signal) ÷ P(event).')}</p>
<h3>{T(lang, '每次熊市前 12 個月是否有警示', 'Was there a warning in the 12 months before each bear market?')}</h3><ul class="eps">{eps}</ul>
<h3>{T(lang, '依綜合分數三等分的實際結果', 'Outcomes by composite-score tercile')}</h3>
<div class='tw'><table><tr>{''.join(f'<th>{x}</th>' for x in T(lang, ['綜合分數', '月數', '12 個月內熊市機率', '12 個月報酬中位數', '12 個月報酬第 10 百分位'], ['Composite', 'Months', 'P(bear within 12m)', 'Median 12m return', '10th pct 12m return']))}</tr>{terc}</table></div>
<p class="note">{T(lang, 'Growth-at-Risk 校準:實際 12 個月報酬低於模型第 10 百分位的比例 = ', 'Growth-at-Risk calibration: share of actual 12m returns below the model 10th percentile = ')}{_p(b.get('gar_coverage'))}{T(lang, '(理想 ≈ 10%)', ' (ideal ≈ 10%)')}</p>
<h3>{T(lang, '目前四類之間的時變相關(CISS 用)', 'Current time-varying correlation between the 4 categories (used by CISS)')}</h3>{ch}
<h3>{T(lang, '指標與資料源', 'Indicators and sources')}</h3><div class='tw'><table><tr>{''.join(f'<th>{x}</th>' for x in T(lang, ['類別', '指標', '來源', '頻率', '公布落後'], ['Group', 'Indicator', 'Source', 'Freq.', 'Publication lag']))}</tr>{src}</table></div>
</section>"""


INTRO = {
    "zh": """<div class="card"><h2 style="margin-top:4px">為什麼改版</h2><ol>
<li><b>同源指標重複計票。</b>舊版 6 燈裡有 3 盞都是融資餘額的變形(彼此相關 0.6–0.87),有效獨立資訊只有約 3 個;「≥4 亮紅」實際上等於「融資偏高 + 再一個」。</li>
<li><b>沒有「價格是否有實體支撐」的檢查。</b>舊版只看槓桿與市場情緒,沒有估值、企業盈餘/營收、景氣循環。</li>
<li><b>沒有定義要預測什麼、也沒量過準不準。</b>舊規則在 60 天尺度沒有預警力,美股甚至反向。</li></ol></div>
<div class="card"><h2 style="margin-top:4px">新架構(每一點對應一個專業作法)</h2><ol>
<li><b>四類脆弱性,每類一票</b>(Fed 金融穩定報告):V 估值 vs 基本面、L 槓桿與信用、M 景氣循環(實體經濟)、S 市場壓力。同類先平均成一分,再轉成歷史百分位。</li>
<li><b>無前視偏誤</b>:只用當時已公布的資料(由期末起算公布落後:Z.1 75 天、GDP 35 天、BIS 160 天、月營收次月 10 日),再轉成 PIT 擴張百分位。獨立審查後修正了 5 處前視問題。</li>
<li><b>相關性感知的綜合分數</b>(ECB CISS):<code>(w∘s)ᵀ · Cₜ · (w∘s)</code>,Cₜ 為四類 EWMA 時變相關(λ=0.94),四類同時偏高才會高。</li>
<li><b>紅區</b>(Greenwood-Hanson-Shleifer-Sørensen 2022):信用 3 年成長前 20% 且股價 3 年漲幅前 1/3。</li>
<li><b>Growth-at-Risk</b>(IMF):分量迴歸估計未來 12 個月報酬第 10 百分位,只用當時已實現樣本重估。</li>
<li><b>事前定義的等級</b>:0 平穩;1 留意(綜合 ≥80 或任一類 ≥90);2 結構脆弱(紅區,或 ≥2 類 ≥80 且含估值/槓桿);<b>3 主動減碼</b>(等級 ≥2 且快速觸發)。</li>
<li><b>快速觸發</b>(Fed 區分脆弱性與衝擊):跌破長期均線(美股 200 日、台股 240 日)或 VIX 5 日漲幅 ≥85 百分位。</li>
<li><b>訊號法評估</b>(Kaminsky-Lizondo-Reinhart):事件 = 12 個月內比當時價位低 ≥20%;報告命中率、假警報、NSR、bootstrap 信賴區間,並與舊版同期比較。</li></ol>
<p class="note">警報信:等級升到 ≥2 寄出;維持 ≥2 每週一提醒;等級 3(主動減碼)每天寄;降回 &lt;2 寄解除。</p></div>""",
    "en": """<div class="card"><h2 style="margin-top:4px">Why a new version</h2><ol>
<li><b>Duplicate signals.</b> Three of the old six lights were variants of margin debt (correlation 0.6–0.87), so the six lights carried only ~3 independent signals; "≥4 red" effectively meant "margin is high, plus one more".</li>
<li><b>No check on whether prices are backed by the real economy.</b> The old version looked only at leverage and market sentiment — no valuation, earnings/revenue or business cycle.</li>
<li><b>No defined target and no measured accuracy.</b> The old rule had no warning power at a 60-day horizon, and in the US it even pointed the wrong way.</li></ol></div>
<div class="card"><h2 style="margin-top:4px">New design (each part follows a professional method)</h2><ol>
<li><b>Four vulnerability categories, one vote each</b> (Fed Financial Stability Report): V valuation vs fundamentals, L leverage & credit, M business cycle (real economy), S market stress. Indicators within a category are averaged, then converted to a historical percentile.</li>
<li><b>No look-ahead</b>: only data published by each date (lags counted from period end: Z.1 75 days, GDP 35, BIS 160, monthly revenue by the 10th of the next month), converted to point-in-time expanding percentiles. An independent review found and fixed 5 look-ahead issues.</li>
<li><b>Correlation-aware composite</b> (ECB CISS): <code>(w∘s)ᵀ · Cₜ · (w∘s)</code> with Cₜ the EWMA time-varying correlation (λ=0.94) — high only when categories are elevated together.</li>
<li><b>Red zone</b> (Greenwood-Hanson-Shleifer-Sørensen 2022): 3-year credit growth in the top 20% AND 3-year price gain in the top third.</li>
<li><b>Growth-at-Risk</b> (IMF): quantile regression for the 10th-percentile 12-month return, re-estimated each month on outcomes already realised.</li>
<li><b>Levels fixed in advance</b>: 0 Calm; 1 Watch (composite ≥80 or any category ≥90); 2 Vulnerable (red zone, or ≥2 categories ≥80 incl. valuation/leverage); <b>3 Reduce longs</b> (level ≥2 AND fast trigger).</li>
<li><b>Fast trigger</b> (Fed: vulnerabilities vs shocks): close below the long-term moving average (US 200-day, Taiwan 240-day) or VIX 5-day change ≥ 85th percentile.</li>
<li><b>Signal-approach evaluation</b> (Kaminsky-Lizondo-Reinhart): event = price ≥20% below the signal-date level within 12 months; hit rate, false alarms, NSR and bootstrap confidence intervals, compared with the old rule over the same period.</li></ol>
<p class="note">Alert emails: sent when the level rises to ≥2; weekly Monday reminder while ≥2; daily at level 3 (reduce longs); an all-clear when it drops below 2.</p></div>""",
}
LIMITS = {
    "zh": """<div class="card"><h2 style="margin-top:4px">限制(請務必一起看)</h2><ul>
<li>熊市次數少(美股 1960 年後 10 次、台股 2007 年後 5 次),月樣本視窗重疊 → 統計差距是方向性證據,請看信賴區間。</li>
<li>台股估值與營收使用目前前 25 大權值股(存活者偏誤);台股無免費的長期全市場本益比與景氣燈號資料。</li>
<li>台灣 GDP 取自 IMF WEO 最新版本(含事後修正);美國季資料亦為修正後數值,只以公布落後近似 PIT。</li>
<li>台股景氣類(營收)沒有預警力;美股市場壓力類偏同時指標。兩者保留、權重不變,避免依回測挑指標。</li>
<li>美股長期日線來自 Yahoo Finance 非官方介面(有快取與種子備援,近期以 FRED 補齊);CAPE 來自 multpl.com。</li>
<li>結構脆弱度描述的是未來 1–2 年的尾端風險,不是短線擇時工具。非投資建議。</li></ul></div>""",
    "en": """<div class="card"><h2 style="margin-top:4px">Limitations (please read)</h2><ul>
<li>Few bear markets (10 in the US since 1960, 5 in Taiwan since 2007) and overlapping monthly windows → treat differences as directional evidence and check the confidence intervals.</li>
<li>Taiwan valuation and revenue use today's 25 largest companies (survivorship bias); no free long-history market-wide P/E or business-cycle indicator exists for Taiwan.</li>
<li>Taiwan GDP comes from the latest IMF WEO vintage (includes later revisions); US quarterly data are also revised values, with publication lags approximating point-in-time.</li>
<li>Taiwan's business-cycle category (revenue) shows no warning power; US market stress is mostly coincident. Both are kept at equal weight to avoid picking indicators by backtest.</li>
<li>Long US daily prices come from an unofficial Yahoo Finance endpoint (cached, seeded, and topped up from FRED); CAPE from multpl.com.</li>
<li>Structural fragility describes tail risk over the next 1–2 years; it is not a short-term timing tool. Not investment advice.</li></ul></div>""",
}


def methodology(data):
    asof = max(m["asof"] for m in data["markets"].values())

    def body(lang):
        secs = "".join(market_section(data["markets"][c], lang) for c in ("TW", "US") if c in data["markets"])
        refs = "".join(f'<li><a href="{u}">{H.escape(t)}</a></li>' for t, u in REFS)
        return (f'<h1>{T(lang, "脆弱度 v2 · 方法與回測", "Fragility v2 · Methodology & backtest")}</h1>'
                f'<div class="sub">{T(lang, "更新於", "Updated")} {asof} · {T(lang, "每個交易日自動更新 · 非投資建議", "auto-updated every trading day · not investment advice")}</div>'
                f'<nav class="tabs"><a href="index.html">{T(lang, "台股脆弱度", "Taiwan fragility")}</a><a href="us.html">{T(lang, "美股脆弱度", "US fragility")}</a>'
                f'<a href="industry_heat.html">{T(lang, "產業熱度雷達", "Industry heat radar")}</a><a href="asset_pricing.html">{T(lang, "資產定價實驗室", "Asset pricing lab")}</a>'
                f'<a href="methodology.html" class="on">{T(lang, "方法論", "Methodology")}</a></nav>'
                f'{INTRO[lang]}{secs}{LIMITS[lang]}<div class="card"><h2 style="margin-top:4px">{T(lang, "參考文獻", "References")}</h2><ul>{refs}</ul></div>')
    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>脆弱度 v2 方法論</title>
<link rel="manifest" href="manifest.webmanifest"><meta name="theme-color" content="#111110"><link rel="apple-touch-icon" href="icon-192.png">
<style>
:root{{--bg:#f4f4f2;--surface:#fcfcfb;--border:#e2e2dd;--tp:#0b0b0b;--ts:#52514e;--muted:#8a8981;--series-1:#2a78d6;--ok:#0f9d63;--warn:#eda100;--red:#e34948;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#111110;--surface:#1a1a19;--border:#33332f;--tp:#fff;--ts:#c3c2b7;--muted:#8f8e85;--series-1:#3987e5;--ok:#25b878;--warn:#e0a83a;--red:#e66767;color-scheme:dark}}}}
:root[data-theme=dark]{{--bg:#111110;--surface:#1a1a19;--border:#33332f;--tp:#fff;--ts:#c3c2b7;--muted:#8f8e85;--series-1:#3987e5;--ok:#25b878;--warn:#e0a83a;--red:#e66767;color-scheme:dark}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--tp);font:15px/1.65 -apple-system,"PingFang TC","Microsoft JhengHei",Segoe UI,sans-serif}}
.wrap{{max-width:980px;margin:0 auto;padding:24px 16px 70px}}h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:18px;margin:30px 0 10px}}h3{{font-size:15px;margin:22px 0 8px}}
.sub{{color:var(--ts);font-size:13px}}a{{color:var(--series-1)}}
.tabs{{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 6px}}.tabs a{{padding:6px 14px;border:1px solid var(--border);border-radius:999px;color:var(--ts);text-decoration:none;font-size:13px;background:var(--surface)}}
.tabs a.on{{background:var(--series-1);border-color:var(--series-1);color:#fff}}
section,.card{{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:16px 20px;margin:16px 0}}
.tw{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}}
th,td{{border-bottom:1px solid var(--border);padding:6px 8px;text-align:left;white-space:nowrap}}th{{color:var(--ts);font-weight:600}}
tr.sum td{{background:color-mix(in srgb,var(--border) 35%,transparent);font-weight:600}}
table.c{{width:auto}}table.c td{{text-align:right}}.note{{color:var(--muted);font-size:12.5px}}
.chart svg{{width:100%;height:auto;display:block}}
.eps{{padding-left:18px}}.ep{{display:inline-block;margin:2px 4px 2px 0;padding:1px 7px;border-radius:6px;font-size:12px;font-variant-numeric:tabular-nums}}
.ep.y{{background:color-mix(in srgb,var(--ok) 18%,transparent)}}.ep.n{{background:color-mix(in srgb,var(--red) 18%,transparent)}}
ol li,ul li{{margin:4px 0}}code{{background:color-mix(in srgb,var(--border) 60%,transparent);padding:1px 5px;border-radius:5px;font-size:13px}}
html:not([lang^=en]) .L-en,html[lang^=en] .L-zh{{display:none}}
</style></head><body><div class="wrap">
<div class="L-zh" data-noi18n>{body("zh")}</div>
<div class="L-en" data-noi18n>{body("en")}</div>
</div></body></html>"""


def inject_all(data):
    inject_pages(data)
    open("methodology.html", "w", encoding="utf-8").write(methodology(data))
    print("   → methodology.html")


if __name__ == "__main__":
    inject_all(json.load(open("fragility_v2.json", encoding="utf-8")))
