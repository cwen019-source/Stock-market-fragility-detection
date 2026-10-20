#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""未來三個月美國關鍵經濟數據 / FOMC 行事曆 → macro_calendar.json,並嵌入 index.html。

資料源(全部官方、每次執行重抓;某來源失敗時沿用上次 macro_calendar.json 裡該來源的日期):
  FOMC        federalreserve.gov 會議行事曆(* = 附經濟預測 SEP / 點陣圖)
  BLS         非農就業、CPI、PPI、JOLTS、ECI(各指標的 release schedule 頁)
  BEA         GDP、個人所得與支出(PCE 物價)— 官方 JSON
  Census      零售銷售(Advance Monthly Retail)

BLS 會擋沒有聯絡資訊的程式請求 → 設環境變數 BLS_CONTACT(一個 email)才會抓 BLS;
沒設就沿用上次的 BLS 日期。

用法:python macro_calendar.py            # 抓取 + 嵌入頁面
      python macro_calendar.py --no-fetch # 只用現有 macro_calendar.json 重新嵌入
"""
import datetime as dt
import html as H
import json
import os
import re
import sys
from zoneinfo import ZoneInfo

import requests

OUT = "macro_calendar.json"
PAGES = ["index.html"]
MONTHS = int(os.environ.get("MACRO_MONTHS", "3"))
ET, TW = ZoneInfo("America/New_York"), ZoneInfo("Asia/Taipei")
MON = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
WD = "一二三四五六日"
S = requests.Session()

# 重要度:5 = 市場最敏感(常見單日大波動),4 = 重要,3 = 次要
BLS = [
    ("empsit", "非農就業報告", "Employment Situation", 5),
    ("cpi", "CPI 消費者物價", "Consumer Price Index", 5),
    ("ppi", "PPI 生產者物價", "Producer Price Index", 3),
    ("jolts", "JOLTS 職缺", "Job Openings and Labor Turnover", 3),
    ("eci", "ECI 雇用成本指數", "Employment Cost Index", 3),
]


def log(*a):
    print(*a, flush=True)


def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(d.day, last))


def ev(src, d, hhmm_et, zh, en, tier, ref="", note="", url=""):
    t_et = dt.datetime.combine(d, dt.time(*map(int, hhmm_et.split(":"))), ET)
    t_tw = t_et.astimezone(TW)
    return {"date": d.isoformat(), "et": t_et.strftime("%H:%M"), "tw": t_tw.strftime("%m/%d %H:%M"),
            "tw_date": t_tw.date().isoformat(), "name": zh, "en": en, "tier": tier, "ref": ref,
            "note": note, "src": src, "url": url}


def get(url, **kw):
    r = S.get(url, timeout=(10, 40), **kw)
    r.raise_for_status()
    return r.text


# ---------------- FOMC ----------------
def fetch_fomc():
    url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    s = get(url)
    out = []
    for y in re.findall(r"(\d{4}) FOMC Meetings", s):
        i = s.find(f"{y} FOMC Meetings")
        j = s.find("FOMC Meetings", i + 20)
        blk = s[i:j if j > 0 else None]
        for mon, days in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<', blk, re.S):
            sep = "*" in days
            mon_last = mon.split("/")[-1].strip()[:3]
            day_last = re.findall(r"\d+", days)
            if mon_last not in MON or not day_last:
                continue
            d = dt.date(int(y), MON[mon_last], int(day_last[-1]))
            note = "附經濟預測(SEP)與點陣圖" if sep else ""
            out.append(ev("FOMC", d, "14:00", "FOMC 利率決策" + ("+點陣圖" if sep else ""), "FOMC rate decision", 5,
                          note=note + ("・" if note else "") + "14:30 ET 主席記者會", url=url))
    return out


# ---------------- BLS ----------------
def fetch_bls():
    contact = os.environ.get("BLS_CONTACT", "").strip()
    if not contact:
        raise RuntimeError("未設 BLS_CONTACT(BLS 要求請求附聯絡 email),沿用舊資料")
    hdr = {"User-Agent": f"fragility-dashboard/1.0 (contact: {contact})"}
    out = []
    for code, zh, en, tier in BLS:
        url = f"https://www.bls.gov/schedule/news_release/{code}.htm"
        s = get(url, headers=hdr)
        for ref, mon, day, yr, hh, mm, ap in re.findall(
                r"<td>([^<]*?\d{4})</td>\s*<td>([A-Z][a-z]{2})[a-z]*\.? (\d{1,2}), (\d{4})</td>\s*<td>(\d{2}):(\d{2}) ([AP]M)</td>", s):
            h = int(hh) % 12 + (12 if ap == "PM" else 0)
            out.append(ev("BLS", dt.date(int(yr), MON[mon], int(day)), f"{h}:{mm}", zh, en, tier, ref=ref.strip(), url=url))
    # 再用 BLS 官方 iCal 補齊(個別頁有時只列下一次)
    try:
        ics = get("https://www.bls.gov/schedule/news_release/bls.ics", headers=hdr).replace("\r\n ", "")
        names = {en: (zh, tier, code) for code, zh, en, tier in BLS}
        names["Job Openings and Labor Turnover Survey"] = names["Job Openings and Labor Turnover"]
        for blk in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", ics, re.S):
            su = re.search(r"SUMMARY[^:]*:(.*)", blk)
            dtm = re.search(r"DTSTART[^:]*:(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2}))?", blk)
            if not su or not dtm or su.group(1).strip() not in names:
                continue
            zh, tier, code = names[su.group(1).strip()]
            y, m, d, hh, mm = dtm.groups()
            out.append(ev("BLS", dt.date(int(y), int(m), int(d)), f"{int(hh or 8)}:{mm or '30'}", zh, su.group(1).strip(), tier,
                          url=f"https://www.bls.gov/schedule/news_release/{code}.htm"))
    except Exception as e:
        log(f"   [warn]  BLS iCal: {e}")
    if not out:
        raise RuntimeError("BLS 頁面解析不到日期")
    return out


# ---------------- BEA ----------------
def fetch_bea():
    url = "https://apps.bea.gov/API/signup/release_dates.json"
    j = json.loads(get(url))
    out = []
    for key, base, en in [("Gross Domestic Product", "GDP", "Gross Domestic Product"),
                          ("Personal Income and Outlays", "PCE 物價 / 個人所得支出", "Personal Income and Outlays")]:
        for iso in sorted(set(j.get(key, {}).get("release_dates", []))):
            t = dt.datetime.fromisoformat(iso).astimezone(ET)
            if base == "GDP":
                # 季後第 1 個月=初值(advance)、第 2 個月=第二次、第 3 個月=第三次估計
                stage, tier = {1: ("初值", 4), 2: ("第二次估計", 3), 0: ("第三次估計", 3)}[t.month % 3]
                name, ref = f"GDP {stage}", _gdp_ref(t.date())
            else:
                name, tier, ref = base, 4, ""
            out.append(ev("BEA", t.date(), t.strftime("%H:%M"), name, en, tier, ref=ref, url="https://www.bea.gov/news/schedule"))
    if not out:
        raise RuntimeError("BEA JSON 無資料")
    return out


def _gdp_ref(d):
    """GDP 發布月 → 所屬季度(1/2/3 月發布的是上一年 Q4)"""
    q = (d.month - 1) // 3          # 0..3 → 上一季
    return f"{d.year - 1} Q4" if q == 0 else f"{d.year} Q{q}"


# ---------------- Census ----------------
def fetch_census(years):
    out = []
    urls = [f"https://www.census.gov/economic-indicators/calendar-listview-{y}.html" for y in sorted(years)]
    urls.append("https://www.census.gov/economic-indicators/calendar-listview.html")   # 預設頁 = 最新年度
    for url in urls:
        try:
            s = get(url)
        except Exception:
            continue
        t = H.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)))
        for mon, day, yr, hh, mm, ap, ref in re.findall(
                r"Advance Monthly Sales for Retail and Food Services ([A-Z][a-z]+) (\d{1,2}), (\d{4}) (\d{1,2}):(\d{2}) ([AP]M) ([A-Z][a-z]+ \d{4})", t):
            h = int(hh) % 12 + (12 if ap == "PM" else 0)
            out.append(ev("Census", dt.date(int(yr), MON[mon[:3]], int(day)), f"{h}:{mm}", "零售銷售", "Advance Retail Sales", 4,
                          ref=ref, url="https://www.census.gov/retail/release_schedule.html"))
    if not out:
        raise RuntimeError("Census 頁面解析不到零售銷售日期")
    return out


# ---------------- 主流程 ----------------
def page_asof(path):
    try:
        m = re.search(r"更新於\s*(\d{4}-\d{2}-\d{2})", open(path, encoding="utf-8").read(200000))
        return dt.date.fromisoformat(m.group(1)) if m else None
    except Exception:
        return None


def build(fetch=True):
    asofs = [a for a in (page_asof(p) for p in PAGES) if a]
    start = max(asofs) if asofs else dt.date.today()
    end = add_months(start, MONTHS)
    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            old = {}
    events, status = [], {}
    sources = {"FOMC": fetch_fomc, "BLS": fetch_bls, "BEA": fetch_bea,
               "Census": lambda: fetch_census({start.year, end.year})}
    for name, fn in sources.items():
        got = None
        if fetch:
            try:
                got = fn()
                status[name] = "ok"
                log(f"   [ok]    {name} {len(got)} 筆")
            except Exception as e:
                status[name] = f"沿用舊資料({type(e).__name__}: {str(e)[:80]})"
                log(f"   [keep]  {name}: {e}")
        if got is None:
            got = [e for e in old.get("all", []) if e["src"] == name]
            status.setdefault(name, "沿用舊資料")
        events += got
    # 去重(同日同名)
    seen, uniq = set(), []
    for e in sorted(events, key=lambda e: (e["date"], e["et"], -e["tier"], e.get("ref", "") == "")):
        k = (e["date"], e["name"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    # all = 保留今天以後的全部(下次來源失敗時可沿用);events = 視窗內
    today = min(start, dt.date.today()).isoformat()
    keep = [e for e in uniq if e["date"] >= today]
    win = [e for e in keep if start.isoformat() <= e["date"] <= end.isoformat()]
    data = {"asof": start.isoformat(), "end": end.isoformat(), "months": MONTHS,
            "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "status": status, "events": win, "all": keep}
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"→ {OUT}:{start} ~ {end} 共 {len(win)} 筆(5 級 {sum(e['tier'] == 5 for e in win)})")
    return data


# ---------------- 嵌入頁面 ----------------
BLOCK_RE = re.compile(r"<!--MACROCAL-->.*?<!--/MACROCAL-->\n?", re.S)

CSS = """<style>
.mcal{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:16px 20px;margin:0 0 18px}
.mcal .mh{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap}
.mcal .mt{font-weight:650;font-size:14.5px}.mcal .ms{color:var(--muted);font-size:11.5px}
.mcal .nx{margin:10px 0 4px;font-size:13px;color:var(--ts)}.mcal .nx b{color:var(--tp)}
.mcal .strip{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:3px;align-items:end;height:46px;margin:10px 0 2px}
.mcal .wk{position:relative;border-radius:4px 4px 2px 2px;background:var(--border);min-height:4px}
.mcal .wk.f::after{content:"";position:absolute;left:50%;top:-7px;width:6px;height:6px;margin-left:-3px;border-radius:50%;background:var(--red)}
.mcal .wl{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:3px;font-size:10px;color:var(--muted)}
.mcal .wl span{overflow:visible;white-space:nowrap}
.mcal .lg{font-size:11px;color:var(--muted);margin:4px 0 8px}
.mcal ul{list-style:none;margin:6px 0 0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,320px),1fr));gap:2px 18px}
.mcal li{display:grid;grid-template-columns:auto 1fr auto;gap:8px;align-items:baseline;padding:5px 0;border-bottom:1px dashed var(--border);font-size:13px}
.mcal li .d{font-variant-numeric:tabular-nums;color:var(--ts);white-space:nowrap}.mcal li .n{min-width:0}
.mcal li .n small{color:var(--muted);font-size:11px;margin-left:4px}
.mcal li .c{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
.mcal li.t5 .n b{color:var(--red)}.mcal li.t4 .n b{color:var(--warn)}.mcal li.t3 .n b{font-weight:500}
.mcal li.past{opacity:.45}.mcal li.lo{display:none}.mcal.all li.lo{display:grid}
.mcal .more{margin-top:8px;border:1px solid var(--border);background:transparent;color:var(--ts);border-radius:8px;padding:4px 10px;font:inherit;font-size:12px;cursor:pointer}
@media(max-width:560px){.mcal{padding:14px}}
html:not([lang^=en]) .L-en,html[lang^=en] .L-zh{display:none}
</style>"""

JS = """<script>(function(){var r=document.getElementById('mcal');if(!r)return;var EN=/^en/.test(document.documentElement.lang||'');
var now=new Date(),first=null;
r.querySelectorAll('li[data-t]').forEach(function(li){var t=new Date(li.dataset.t),d=Math.ceil((t-now)/864e5),c=li.querySelector('.c');
 if(t<now){li.classList.add('past');c.textContent=EN?'released':'已公布';}else{c.textContent=d<=0?(EN?'today':'今天'):'D-'+d;if(!first)first=li;}});
var nx=document.getElementById('mcal-next');if(nx&&first){var dd=Math.ceil((new Date(first.dataset.t)-now)/864e5);
 var nm=first.querySelector('.n b '+(EN?'.L-en':'.L-zh')),dt=first.querySelector('.d').cloneNode(true);dt.querySelectorAll(EN?'.L-zh':'.L-en').forEach(function(x){x.remove()});
 nx.innerHTML=(EN?'Next: <b>':'下一個:<b>')+nm.textContent+'</b> · '+dt.textContent+(dd<=0?(EN?' (today)':'(今天)'):(EN?' (in '+dd+' days)':'(還有 '+dd+' 天)'));}
var b=r.querySelector('.more');if(b){var lab=function(){var a=r.classList.contains('all');b.textContent=EN?(a?'Key events only':'Show all '+b.dataset.n+' events'):(a?'只看重要':'顯示全部 '+b.dataset.n+' 項');};lab();b.onclick=function(){r.classList.toggle('all');lab();};}
})();</script>"""


def short_ref(r):
    """'November 2026' → '26年11月';'Third Quarter 2026' → '2026 Q3'"""
    m = re.match(r"([A-Z][a-z]+) (\d{4})$", r or "")
    if m and m.group(1)[:3] in MON:
        return f"{m.group(2)[2:]}年{MON[m.group(1)[:3]]}月"
    q = {"First": 1, "Second": 2, "Third": 3, "Fourth": 4}
    m = re.match(r"(First|Second|Third|Fourth) Quarter (\d{4})$", r or "")
    return f"{m.group(2)} Q{q[m.group(1)]}" if m else (r or "")


NAME_EN = {"非農就業報告": "Nonfarm payrolls", "CPI 消費者物價": "CPI inflation", "PPI 生產者物價": "PPI producer prices",
           "JOLTS 職缺": "JOLTS job openings", "ECI 雇用成本指數": "Employment Cost Index",
           "PCE 物價 / 個人所得支出": "PCE inflation / personal income", "GDP 初值": "GDP (advance)",
           "GDP 第二次估計": "GDP (2nd estimate)", "GDP 第三次估計": "GDP (3rd estimate)", "零售銷售": "Retail sales",
           "FOMC 利率決策": "FOMC rate decision", "FOMC 利率決策+點陣圖": "FOMC decision + dot plot"}
WD_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MON_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def B(zh, en):
    """雙語片段:依 <html lang> 顯示其一(i18n.js 不再翻譯這兩段)。"""
    return f'<span class="L-zh" data-noi18n>{zh}</span><span class="L-en" data-noi18n>{en}</span>'


def short_ref_en(r):
    m = re.match(r"([A-Z][a-z]+) (\d{4})$", r or "")
    if m:
        return f"{m.group(1)[:3]} {m.group(2)}"
    q = {"First": 1, "Second": 2, "Third": 3, "Fourth": 4}
    m = re.match(r"(First|Second|Third|Fourth) Quarter (\d{4})$", r or "")
    return f"{m.group(2)} Q{q[m.group(1)]}" if m else (r or "")


def render(data):
    evs = data["events"]
    start, end = dt.date.fromisoformat(data["asof"]), dt.date.fromisoformat(data["end"])
    # 每週不確定性密度(依台灣日期):Σ 重要度,FOMC 週打紅點
    w0 = start - dt.timedelta(days=start.weekday())
    nweeks = (end - w0).days // 7 + 1
    load, fomc = [0] * nweeks, [False] * nweeks
    for e in evs:
        k = (dt.date.fromisoformat(e["tw_date"]) - w0).days // 7
        if 0 <= k < nweeks:
            load[k] += e["tier"]
            fomc[k] |= e["src"] == "FOMC"
    mx = max(load) or 1

    def col(v):
        return "var(--red)" if v >= 12 else "var(--warn)" if v >= 7 else "var(--series-1)" if v > 0 else "var(--border)"
    bars = "".join(
        f'<div class="wk{" f" if fomc[k] else ""}" title="{(w0 + dt.timedelta(weeks=k)).strftime("%m/%d")} 週 · 重要度合計 {load[k]}"'
        f' style="height:{max(8, round(100 * load[k] / mx))}%;background:{col(load[k])}"></div>' for k in range(nweeks))
    labels, last_m = [], None
    for k in range(nweeks):
        d = w0 + dt.timedelta(weeks=k)
        labels.append(f"<span>{B(f'{d.month}月', MON_EN[d.month - 1])}</span>" if d.month != last_m else "<span></span>")
        last_m = d.month
    items, n_lo = [], 0
    for e in evs:
        t_et = dt.datetime.fromisoformat(f"{e['date']}T{e['et']}").replace(tzinfo=ET)
        t_tw = t_et.astimezone(TW)
        lo = e["tier"] < 4
        n_lo += lo
        sub, sub_en = short_ref(e.get("ref", "")), short_ref_en(e.get("ref", ""))
        nm_en = NAME_EN.get(e["name"], e.get("en", e["name"]))
        items.append(
            f'<li class="t{e["tier"]}{" lo" if lo else ""}" data-t="{t_et.isoformat()}" title="{H.escape(e["en"])} · 美東 {e["date"]} {e["et"]} · {e["src"]}{(" · " + H.escape(e["note"])) if e.get("note") else ""}">'
            f'<span class="d">{t_tw:%m/%d}{B(f"({WD[t_tw.weekday()]})", f" {WD_EN[t_tw.weekday()]}")} {t_tw:%H:%M}</span>'
            f'<span class="n"><b>{B(H.escape(e["name"]), H.escape(nm_en))}</b>{f"<small>{B(H.escape(sub), H.escape(sub_en))}</small>" if sub else ""}</span>'
            f'<span class="c"></span></li>')
    n5 = sum(e["tier"] == 5 for e in evs)
    stale = [k for k, v in data.get("status", {}).items() if v != "ok"]
    more = f'<button class="more" data-n="{len(evs)}" data-noi18n></button>' if n_lo else ""
    return (f'<!--MACROCAL-->{CSS}<div class="mcal" id="mcal">'
            f'<div class="mh"><span class="mt">{B("未來三個月 · 美國關鍵數據與 FOMC", "Next 3 months · key US data & FOMC")}</span>'
            f'<span class="ms">{B(f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d} · 台灣時間 · {len(evs)} 項,其中 {n5} 項最高重要度(FOMC/非農/CPI)", f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d} · Taiwan time · {len(evs)} events, {n5} top-importance (FOMC / payrolls / CPI)")}'
            f'{" · " + "/".join(stale) + B(" 沿用舊日期", " using last known dates") if stale else ""}</span></div>'
            f'<div class="nx" id="mcal-next" data-noi18n></div>'
            f'<div class="strip">{bars}</div><div class="wl">{"".join(labels)}</div>'
            f'<div class="lg">{B("每週不確定性密度 = 當週事件重要度加總:", "Weekly uncertainty density = sum of event importance that week: ")}<span style="color:var(--red)">■</span> {B("高", "high")} '
            f'<span style="color:var(--warn)">■</span> {B("中", "medium")} <span style="color:var(--series-1)">■</span> {B("低", "low")} · '
            f'<span style="color:var(--red)">●</span> {B("FOMC 週。這些日子波動通常放大,脆弱度高時更要留意。", "FOMC week. Volatility usually rises around these dates — take extra care when fragility is high.")}</div>'
            f'<ul>{"".join(items)}</ul>{more}</div>{JS}<!--/MACROCAL-->\n')


def inject(data):
    block = render(data)
    for p in PAGES:
        if not os.path.exists(p):
            continue
        s = open(p, encoding="utf-8").read()
        s = BLOCK_RE.sub("", s)
        i = s.find('<div class="trend">')        # 放在儀表(hero)之後、搜尋卡之前
        if i < 0:
            log(f"   [skip] {p} 找不到插入點")
            continue
        open(p, "w", encoding="utf-8").write(s[:i] + block + s[i:])
        log(f"   已嵌入 {p}")


if __name__ == "__main__":
    fetch = "--no-fetch" not in sys.argv
    data = build(fetch=fetch) if fetch or not os.path.exists(OUT) else json.load(open(OUT, encoding="utf-8"))
    inject(data)
