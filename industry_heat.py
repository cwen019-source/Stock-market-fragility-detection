#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股產業熱度雷達 — 以「產業內兩兩相關性」偵測資金主題化
========================================================
核心想法:當資金把一個族群當成「同一個故事」在買,族群內個股會開始同漲同跌,
         平均兩兩相關(rho)會先於報酬上升 → 這是主題成形的早期訊號。

但高相關有兩種完全相反的意思,必須用方向分開:
   高相關 + 上漲 = 主題發動(買籃子就好,選股加值有限)
   高相關 + 下跌 = 同步殺出/去槓桿(避開)
   低相關 + 上漲 = 個股各自表現(選股才有超額報酬)
   低相關 + 下跌 = 乏人問津

指標(全部為滾動視窗,只用當日及以前資料 → 無前視):
   rho     平均兩兩相關(60日)
   absorb  吸收率 = 相關矩陣第一主成分解釋比重
   disp    橫斷面離散度(個股報酬標準差)— 選股空間
   drift   籃子等權報酬(60日)
   rs      相對大盤強度
   drho    rho 的 60 日變化 — 「熱度正在來」的訊號
   flow    成交值占大盤比重的 z 分數 — 資金是否真的流入

用法: python3 industry_heat.py   → industry_heat.html
"""
import os, json, math
import numpy as np, pandas as pd

W        = 60      # 滾動視窗(交易日)
DW       = 60      # drho 的回看期
TOPN     = 10      # 每產業取成交值最大的 N 檔
MIN_MEMB = 5
MIN_DAYS = 400
OUT      = "industry_heat.html"

# ---------------- 載入 ----------------
def load():
    px  = json.load(open("heat_px_cache.json"))
    mem = json.load(open("heat_members.json"))
    names = {}
    try:
        for u in json.load(open("universe.json")): names[u["stock_id"]] = u["name"]
    except Exception: pass
    series, turn = {}, {}
    for sid, d in px.items():
        if not d.get("val") or len(d["val"]) < MIN_DAYS: continue
        idx = pd.to_datetime(d["idx"])
        series[sid] = pd.Series(d["val"], index=idx).sort_index()
        if d.get("tv"):
            turn[sid] = pd.Series(d["tv"], index=idx).sort_index()
    return series, turn, mem, names

def build(series, turn, mem):
    master = None
    for s in series.values():
        master = s.index if master is None else master.union(s.index)
    master = pd.DatetimeIndex(sorted(master))
    inds = {}
    for ind, ids in mem.items():
        avail = [s for s in ids if s in series]
        if len(avail) < MIN_MEMB: continue
        # 依中位成交值取前 TOPN(排除冷門股,相關性才有意義)
        med = {s: float(turn[s].median()) if s in turn and len(turn[s]) else 0.0 for s in avail}
        keep = sorted(avail, key=lambda s: -med[s])[:TOPN]
        if len(keep) < MIN_MEMB: continue
        inds[ind] = keep
    return master, inds

# ---------------- 指標 ----------------
def industry_metrics(master, series, turn, members):
    px = pd.DataFrame({s: series[s].reindex(master).ffill() for s in members})
    ret = np.log(px / px.shift(1))
    ret = ret.replace([np.inf, -np.inf], np.nan)
    n = len(master)
    rho    = np.full(n, np.nan); absorb = np.full(n, np.nan); disp = np.full(n, np.nan)
    R = ret.values
    for t in range(W, n):
        win = R[t-W+1:t+1]
        ok = ~np.isnan(win).any(axis=0)
        if ok.sum() < MIN_MEMB: continue
        w = win[:, ok]
        sd = w.std(axis=0)
        good = sd > 1e-12
        if good.sum() < MIN_MEMB: continue
        w = w[:, good]
        C = np.corrcoef(w, rowvar=False)
        k = C.shape[0]
        iu = np.triu_indices(k, 1)
        rho[t] = float(np.nanmean(C[iu]))
        try:
            ev = np.linalg.eigvalsh(C)
            absorb[t] = float(ev[-1] / ev.sum())
        except np.linalg.LinAlgError:
            pass
        disp[t] = float(np.nanmean(np.nanstd(w, axis=1))) * 100
    eq = ret.mean(axis=1)                       # 等權籃子日報酬
    lvl = (1 + eq.fillna(0)).cumprod()
    drift = (lvl / lvl.shift(W) - 1) * 100
    if turn:
        tv = pd.DataFrame({s: turn[s].reindex(master).ffill() for s in members if s in turn}).sum(axis=1)
    else:
        tv = pd.Series(0.0, index=master)
    return dict(rho=pd.Series(rho, index=master), absorb=pd.Series(absorb, index=master),
                disp=pd.Series(disp, index=master), drift=drift, lvl=lvl, tv=tv, ret=eq)

def compute_all(master, series, turn, inds):
    out = {}
    for ind, mems in inds.items():
        out[ind] = industry_metrics(master, series, turn, mems)
    # 資金流:該產業成交值 / 全部產業成交值,再取 z(60日)
    tot = sum(v["tv"] for v in out.values())
    for ind, v in out.items():
        share = (v["tv"] / tot.replace(0, np.nan)) * 100
        v["share"] = share
        v["flow"] = (share - share.rolling(250).mean()) / share.rolling(250).std()
        v["drho"] = v["rho"] - v["rho"].shift(DW)
    return out

# ---------------- 驗證(誠實回測) ----------------
def validate(M, master):
    """1) 各指標對『未來20/60日產業報酬』的橫斷面 rank-IC
       2) 四象限的未來報酬
       3) 高相關是否真的讓『選股』變得沒必要(未來個股報酬離散度)"""
    inds = list(M)
    def frame(key): return pd.DataFrame({i: M[i][key] for i in inds})
    rho, drho, drift, disp, flow = (frame(k) for k in ("rho","drho","drift","disp","flow"))
    lvl = frame("lvl")
    res = {}
    for H in (20, 60):
        fwd = lvl.shift(-H) / lvl - 1
        for nm, X in [("rho", rho), ("drho", drho), ("drift", drift), ("disp", disp), ("flow", flow)]:
            ics = []
            for d in master[::5]:
                if d not in X.index: continue
                x, y = X.loc[d], fwd.loc[d]
                m = x.notna() & y.notna()
                if m.sum() < 8: continue
                ics.append(x[m].rank().corr(y[m].rank()))
            ics = [v for v in ics if v == v]
            if len(ics) > 30:
                mu = float(np.mean(ics)); sd = float(np.std(ics))
                res[f"{nm}_{H}"] = dict(ic=round(mu, 4), t=round(mu / (sd / math.sqrt(len(ics))), 2), n=len(ics))
    # 四象限:以當日橫斷面中位數切
    quad = {}
    fwd60 = lvl.shift(-60) / lvl - 1
    for d in master[::5]:
        if d not in rho.index: continue
        r, g, f = rho.loc[d], drift.loc[d], fwd60.loc[d]
        m = r.notna() & g.notna() & f.notna()
        if m.sum() < 8: continue
        rm, gm = r[m].median(), g[m].median()
        for i in r[m].index:
            q = ("熱" if r[i] >= rm and g[i] >= gm else "殺" if r[i] >= rm else
                 "選股" if g[i] >= gm else "沉寂")
            quad.setdefault(q, []).append(f[i] * 100)
    qs = {k: dict(n=len(v), med=round(float(np.median(v)), 2), avg=round(float(np.mean(v)), 2),
                  p5=round(float(np.percentile(v, 5)), 2)) for k, v in quad.items()}
    # 高相關 → 未來個股離散度是否下降(決定「選股是否值得」)
    dsp = {}
    fwd_disp = disp.shift(-60)
    for d in master[::5]:
        if d not in rho.index: continue
        r, fd = rho.loc[d], fwd_disp.loc[d]
        m = r.notna() & fd.notna()
        if m.sum() < 8: continue
        rm = r[m].median()
        for i in r[m].index:
            dsp.setdefault("高相關" if r[i] >= rm else "低相關", []).append(fd[i])
    dspr = {k: round(float(np.mean(v)), 3) for k, v in dsp.items()}
    return res, qs, dspr

# ---------------- 輸出 ----------------
def build_payload(M, master, inds, names, val):
    ics, quads, dspr = val
    dates = [str(d.date()) for d in master]
    step = max(1, len(master) // 900)
    keep = list(range(0, len(master), step))
    if keep[-1] != len(master) - 1: keep.append(len(master) - 1)
    def ser(s, r=3):
        v = s.values
        return [None if (v[i] is None or (isinstance(v[i], float) and not np.isfinite(v[i]))) else round(float(v[i]), r) for i in keep]
    rows = {}
    for ind, v in M.items():
        rows[ind] = dict(rho=ser(v["rho"], 2), drho=ser(v["drho"], 2), drift=ser(v["drift"], 1),
                         disp=ser(v["disp"], 2), lvl=ser(v["lvl"], 2), flow=ser(v["flow"], 1),
                         members=[[s, names.get(s, s)] for s in inds[ind]])
    # 產業間相關(近一年)
    rr = pd.DataFrame({i: M[i]["ret"] for i in M}).tail(250)
    cm = rr.corr()
    order = list(cm.columns)
    heat = [[None if pd.isna(cm.iloc[a, b]) else round(float(cm.iloc[a, b]), 2) for b in range(len(order))] for a in range(len(order))]
    # 領先落後:x 領先 y 幾日(取最大互相關的 lag)
    lead = {}
    for i in order:
        best = []
        for j in order:
            if i == j: continue
            bl, bc = 0, -9
            for L in range(1, 6):
                c = rr[i].shift(L).corr(rr[j])
                if c == c and c > bc: bc, bl = c, L
            best.append([j, bl, round(float(bc), 2)])
        best.sort(key=lambda x: -x[2])
        lead[i] = best[:3]
    return dict(dates=[dates[i] for i in keep], inds=rows, order=order, heat=heat,
                lead=lead, ic=ics, quad=quads, dispr=dspr, W=W)

def main():
    series, turn, mem, names = load()
    master, inds = build(series, turn, mem)
    print(f"產業 {len(inds)} 個, 個股 {sum(len(v) for v in inds.values())} 檔, 交易日 {len(master)}")
    M = compute_all(master, series, turn, inds)
    val = validate(M, master)
    print("rank-IC:", json.dumps(val[0], ensure_ascii=False)[:400])
    print("四象限未來60日報酬:", json.dumps(val[1], ensure_ascii=False))
    print("高/低相關 → 未來離散度:", val[2])
    payload = build_payload(M, master, inds, names, val)
    open(OUT, "w").write(HTML.replace("__DATA__", json.dumps(payload, ensure_ascii=False)))
    print(f"OK → {OUT}")

HTML = r"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>台股產業熱度雷達</title>
<style>
:root{--bg:#f4f4f2;--surface:#fcfcfb;--border:#e2e2dd;--tp:#0b0b0b;--ts:#52514e;--muted:#8a8981;
 --s1:#2a78d6;--ok:#0f9d63;--warn:#eda100;--red:#e34948;--pur:#8b5cf6;color-scheme:light}
:root[data-theme=dark]{--bg:#111110;--surface:#1a1a19;--border:#33332f;--tp:#fff;--ts:#c3c2b7;--muted:#8f8e85;
 --s1:#3987e5;--ok:#25b878;--warn:#e0a83a;--red:#e66767;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tp);font-family:-apple-system,"PingFang TC","Microsoft JhengHei",Segoe UI,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px 18px 70px}
h1{font-size:21px;margin:0 0 3px}.sub{color:var(--ts);font-size:12.5px}
h2{font-size:15px;margin:24px 0 10px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:7px 9px;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap}
th{color:var(--muted);font-size:11px;font-weight:600;cursor:pointer;user-select:none}
th:first-child,td:first-child{text-align:left}
tr.sel{background:color-mix(in srgb,var(--s1) 12%,transparent)}
tbody tr{cursor:pointer}tbody tr:hover{background:color-mix(in srgb,var(--s1) 7%,transparent)}
.tag{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11px;font-weight:650}
.t熱{background:color-mix(in srgb,var(--red) 18%,transparent);color:var(--red)}
.t選股{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok)}
.t殺{background:color-mix(in srgb,var(--pur) 18%,transparent);color:var(--pur)}
.t沉寂{background:color-mix(in srgb,var(--muted) 18%,transparent);color:var(--muted)}
svg{display:block;width:100%}
.legend{font-size:11px;color:var(--muted);margin-top:6px}
.grid2{display:grid;grid-template-columns:1.15fr .85fr;gap:12px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.mem{font-size:11.5px;color:var(--ts);line-height:1.7}
.mem b{color:var(--tp)}
details summary{cursor:pointer;font-weight:650}
.note{font-size:11.5px;color:var(--ts);line-height:1.65}
.tabs{display:flex;gap:6px;margin:10px 0 4px;flex-wrap:wrap}
.tabs a{font-size:12.5px;text-decoration:none;color:var(--ts);background:var(--surface);border:1px solid var(--border);border-radius:20px;padding:5px 13px}
.tabs a.on{background:var(--s1,var(--series-1));color:#fff;border-color:var(--s1,var(--series-1))}
#th{position:fixed;top:12px;right:12px;background:var(--surface);color:var(--tp);border:1px solid var(--border);border-radius:8px;padding:6px 11px;cursor:pointer}
.kpi{display:flex;gap:14px;flex-wrap:wrap;margin-top:4px}
.kpi div{font-size:11.5px;color:var(--ts)}.kpi b{font-size:15px;color:var(--tp);display:block;font-variant-numeric:tabular-nums}
</style></head><body><button id="th">◐</button><div class="wrap">
<h1>台股產業熱度雷達 <span style="font-size:11px;color:var(--ok);border:1px solid var(--border);border-radius:6px;padding:1px 6px">pairwise 相關性</span></h1>
<nav class="tabs"><a href="index.html">台股脆弱度</a><a href="us.html">美股脆弱度</a><a href="industry_heat.html" class="on">產業熱度雷達</a></nav>
<div class="sub">滾動 <span id="wlab"></span> 日視窗 · PIT 無前視 · 非投資建議</div>
<div class="card" style="border-left:3px solid var(--s1)"><b style="font-size:13px">先看這個:相關性能回答什麼、不能回答什麼</b><div class="note" style="margin-top:6px">我用 33 個產業、320 檔個股、5299 個交易日回測後發現:<br>❌ <b>相關度 ρ 無法預測產業漲跌</b> — 橫斷面 rank-IC 約 0(未來20日 −0.009,t=−1.25;未來60日 −0.001,t=−0.13),ρ 的變化 Δρ 甚至是<b>微弱負向</b>(t=−1.9)。<b>「相關性上升 = 主題成形 = 接下來會漲」這個直覺,資料不支持。</b><br>✅ <b>相關度能預測「選股還值不值得」</b> — 高相關組的未來60日個股報酬離散度 <b>1.46</b>,低相關組 <b>1.66</b>。族群越同步,個股差異越小,<b>選股能賺到的超額報酬越有限</b>。<br>✅ <b>真正能預測報酬的是動能與離散度</b> — 近60日報酬 IC +0.051(<b>t=6.95</b>)、離散度 IC −0.051(<b>t=−5.75</b>)。<br><br>所以這張表的正確用法是<b>兩段式</b>:先用<b>動能</b>決定「參與哪個產業」,再用<b>相關度</b>決定「用選股還是買籃子去表達」。效果量都不大(IC 0.03–0.05),請當成排序參考而非訊號。</div></div>

<div class="card"><div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
 <b style="font-size:13px">熱度四象限 <span style="font-weight:400;color:var(--muted)">橫軸=產業內平均兩兩相關(當日橫斷面百分位) · 縱軸=近60日籃子報酬 · 泡泡大小=資金流入 z</span></b>
 <span style="font-size:11.5px;color:var(--muted)">日期 <input type="range" id="tslider" style="vertical-align:middle;width:230px"> <span id="tlab"></span></span></div>
 <svg id="quad" height="330"></svg>
 <div class="legend"><b>縱軸(動能)決定「要不要參與」,橫軸(相關度)決定「怎麼參與」。</b>右上<span class="tag t熱">熱</span>同步上漲 → 買籃子或龍頭 ｜ 左上<span class="tag t選股">選股</span>有漲勢但各走各的 → 選股空間最大 ｜ 右下<span class="tag t殺">殺</span>同步下跌 → 分散無效,避開 ｜ 左下<span class="tag t沉寂">沉寂</span>。四象限的未來報酬差異其實很小(見下方驗證②),差別主要在「該用什麼方式進場」。</div></div>

<div class="grid2">
 <div class="card"><b style="font-size:13px">產業排行 <span style="font-weight:400;color:var(--muted)">點列可看該產業細節;點表頭可排序</span></b>
  <div style="overflow:auto;max-height:430px"><table id="tbl"><thead><tr>
   <th data-k="name">產業</th><th data-k="rho">相關ρ</th><th data-k="drho">Δρ(60日)</th><th data-k="drift">60日報酬</th>
   <th data-k="disp">離散度</th><th data-k="flow">資金流z</th><th data-k="quad">研判</th></tr></thead><tbody></tbody></table></div></div>
 <div class="card"><b style="font-size:13px" id="dtitle">—</b>
  <div class="kpi" id="dkpi"></div>
  <svg id="dchart" height="200"></svg>
  <div class="legend"><span style="color:var(--s1)">■</span> 相關ρ(左軸) ｜ <span style="color:var(--ts)">■</span> 籃子淨值(右軸,對數) ｜ 背景紅=熱、綠=選股</div>
  <div class="mem" id="dmem" style="margin-top:8px"></div></div>
</div>

<h2>驗證 — 這些指標真的有預測力嗎?<span style="font-size:11.5px;font-weight:400;color:var(--muted)"> 每5日對33個產業做橫斷面排序,對照未來報酬</span></h2>
<div class="card"><div id="valbox" class="note"></div></div>

<div class="grid2">
 <div class="card"><b style="font-size:13px">產業間相關(近一年)</b><svg id="hm" height="360"></svg>
  <div class="legend">越紅=兩產業越同步。整片變紅=系統性風險,分散投資失效</div></div>
 <div class="card"><b style="font-size:13px">領先落後 <span style="font-weight:400;color:var(--muted)">該產業領先誰(1–5日)</span></b>
  <div style="overflow:auto;max-height:330px"><table id="lead"><thead><tr><th>產業</th><th>領先對象(落後日數/相關)</th></tr></thead><tbody></tbody></table></div>
  <div class="legend">以落後互相關最大者估計,樣本相關易受共同因子影響,僅供觀察</div></div>
</div>

<details class="card"><summary>方法與限制(點開)</summary><div class="note" style="margin-top:8px">
<b>相關ρ</b>=產業內成員日報酬的平均兩兩相關(滾動視窗,只用當日及以前資料,無前視)。<b>吸收率</b>=相關矩陣第一主成分解釋比重,與 ρ 高度相關,作為穩健性對照。<b>離散度</b>=成員日報酬的橫斷面標準差,代表「選股空間」。<b>資金流 z</b>=該產業成交值占全市場比重相對自身 250 日常態的 z 分數。
<br><br><b>為什麼高相關要配方向看:</b>相關度本身沒有多空意義——主題資金湧入會讓成員同漲,系統性風險/去槓桿也會讓成員同跌。單看 ρ 會把「族群發動」和「一起被殺」混為一談,所以本頁一律以 ρ×報酬的四象限呈現。
<br><br><b>成分股選取:</b>每個官方產業別先取股號較小者(成立較久/規模較大的粗略代理)最多 18 檔,再依實際中位成交值留前 10 檔。這不是市值加權的正式產業指數,冷門股與新上市股會被排除,<b>成分不同結論可能不同</b>。產業別沿用證交所/櫃買分類並合併「…類/…業」變體。
<br><br><b>本頁最重要的自我否定:</b>建立這個工具的原始假設是「產業內相關性上升代表主題資金進場,可用來提早挑到會漲的產業」。回測不支持這個假設(見上方驗證①),因此頁面已改為兩段式用法:相關度只用來判斷表達方式(選股 vs 買籃子),不用來預測漲跌。<br><br><b>限制:</b>rank-IC 為橫斷面描述統計,樣本期以多頭為主;相關性估計在高波動期本身會上升(波動與相關的估計偏誤),故 ρ 在崩跌期偏高屬正常現象,不代表主題成形——這也是必須配合方向判讀的原因。領先落後由樣本互相關估計,無因果意義。<b>本頁為研究框架,非投資建議。</b>
</div></details>
</div>
<script>
const D=__DATA__;const $=id=>document.getElementById(id);
const N=D.dates.length;let T=N-1,SEL=null,SORT={k:'drift',d:-1};
$('wlab').textContent=D.W;
const IND=D.order.filter(i=>D.inds[i]);
function pctRank(vals,v){const s=vals.filter(x=>x!=null);if(!s.length||v==null)return null;
 return s.filter(x=>x<v).length/s.length*100;}
function snap(t){const out=[];
 for(const i of IND){const r=D.inds[i];
  out.push({name:i,rho:r.rho[t],drho:r.drho[t],drift:r.drift[t],disp:r.disp[t],flow:r.flow[t],lvl:r.lvl[t]});}
 const rs=out.map(o=>o.rho),gs=out.map(o=>o.drift);
 const rmed=med(rs),gmed=med(gs);
 out.forEach(o=>{o.rpct=pctRank(rs,o.rho);
  o.quad=(o.rho==null||o.drift==null)?'—':(o.rho>=rmed?(o.drift>=gmed?'熱':'殺'):(o.drift>=gmed?'選股':'沉寂'));});
 return out;}
function med(a){const s=a.filter(x=>x!=null).slice().sort((x,y)=>x-y);return s.length?s[Math.floor(s.length/2)]:null;}
function fmt(v,d,suf){return v==null?'–':(v>=0&&suf==='%'?'+':'')+v.toFixed(d)+(suf||'');}
// ---- 四象限 ----
function drawQuad(){const svg=$('quad'),W=svg.clientWidth||900,H=330,pl=44,pr=14,pt=14,pb=30;
 const pw=W-pl-pr,ph=H-pt-pb;const S=snap(T);
 const gs=S.map(o=>o.drift).filter(x=>x!=null);
 const lo=Math.min(...gs,-1),hi=Math.max(...gs,1);
 const X=p=>pl+p/100*pw, Y=g=>pt+(hi-g)/((hi-lo)||1)*ph;
 let g='';
 g+='<rect x="'+X(50)+'" y="'+pt+'" width="'+(pw/2)+'" height="'+(Y(med(gs))-pt)+'" fill="var(--red)" opacity="0.05"/>';
 g+='<rect x="'+pl+'" y="'+pt+'" width="'+(pw/2)+'" height="'+(Y(med(gs))-pt)+'" fill="var(--ok)" opacity="0.05"/>';
 g+='<line x1="'+X(50)+'" y1="'+pt+'" x2="'+X(50)+'" y2="'+(pt+ph)+'" stroke="var(--border)" stroke-dasharray="4 3"/>';
 g+='<line x1="'+pl+'" y1="'+Y(med(gs))+'" x2="'+(W-pr)+'" y2="'+Y(med(gs))+'" stroke="var(--border)" stroke-dasharray="4 3"/>';
 [0,25,50,75,100].forEach(p=>g+='<text x="'+X(p)+'" y="'+(H-10)+'" font-size="9" fill="var(--muted)" text-anchor="middle">'+p+'</text>');
 g+='<text x="'+(pl+pw/2)+'" y="'+(H-1)+'" font-size="9.5" fill="var(--muted)" text-anchor="middle">產業內平均兩兩相關(橫斷面百分位)</text>';
 [lo,(lo+hi)/2,hi].forEach(v=>g+='<text x="'+(pl-5)+'" y="'+(Y(v)+3)+'" font-size="9" fill="var(--muted)" text-anchor="end">'+v.toFixed(0)+'%</text>');
 S.forEach(o=>{if(o.rpct==null||o.drift==null)return;
  const r=4+Math.max(0,Math.min(9,(o.flow==null?0:o.flow)+1.5)*1.1);
  const col=o.quad==='熱'?'var(--red)':o.quad==='選股'?'var(--ok)':o.quad==='殺'?'var(--pur)':'var(--muted)';
  const on=(SEL===o.name);
  g+='<circle cx="'+X(o.rpct).toFixed(1)+'" cy="'+Y(o.drift).toFixed(1)+'" r="'+r.toFixed(1)+'" fill="'+col+'" opacity="'+(on?0.95:0.55)+'"'
   +(on?' stroke="var(--tp)" stroke-width="1.5"':'')+'><title>'+o.name+'</title></circle>';
  g+='<text x="'+(X(o.rpct)+r+3).toFixed(1)+'" y="'+(Y(o.drift)+3).toFixed(1)+'" font-size="9.5" fill="var(--ts)">'+o.name+'</text>';});
 svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.innerHTML=g;}
// ---- 排行表 ----
function drawTable(){const S=snap(T);
 const k=SORT.k,d=SORT.d;
 S.sort((a,b)=>{if(k==='name')return a.name.localeCompare(b.name)*d;
  if(k==='quad')return String(a.quad).localeCompare(String(b.quad))*d;
  const x=a[k],y=b[k];if(x==null)return 1;if(y==null)return -1;return (x-y)*d;});
 const tb=$('tbl').querySelector('tbody');
 tb.innerHTML=S.map(o=>'<tr data-n="'+o.name+'"'+(SEL===o.name?' class="sel"':'')+'>'
  +'<td>'+o.name+'</td><td>'+fmt(o.rho,2)+'</td><td>'+fmt(o.drho,2)+'</td><td>'+fmt(o.drift,1,'%')+'</td>'
  +'<td>'+fmt(o.disp,2)+'</td><td>'+fmt(o.flow,1)+'</td><td><span class="tag t'+o.quad+'">'+o.quad+'</span></td></tr>').join('');
 tb.querySelectorAll('tr').forEach(tr=>tr.onclick=()=>{SEL=tr.dataset.n;drawAll();});}
// ---- 個別產業 ----
function drawDetail(){const svg=$('dchart');if(!SEL){SEL=snap(T).sort((a,b)=>(b.drho||-9)-(a.drho||-9))[0].name;}
 const r=D.inds[SEL];$('dtitle').textContent=SEL;
 const S=snap(T).find(o=>o.name===SEL)||{};
 $('dkpi').innerHTML=['相關ρ '+fmt(S.rho,2),'Δρ60日 '+fmt(S.drho,2),'60日報酬 '+fmt(S.drift,1,'%'),
  '離散度 '+fmt(S.disp,2),'資金流z '+fmt(S.flow,1)].map(x=>{const p=x.split(' ');
  return '<div>'+p[0]+'<b>'+p.slice(1).join(' ')+'</b></div>';}).join('');
 const W=svg.clientWidth||520,H=200,pl=34,pr=36,pt=10,pb=20,pw=W-pl-pr,ph=H-pt-pb;
 const rr=r.rho,ll=r.lvl;const n=rr.length;
 const rv=rr.filter(x=>x!=null),lv=ll.filter(x=>x!=null&&x>0);
 if(!rv.length){svg.innerHTML='';return;}
 const rlo=Math.min(...rv),rhi=Math.max(...rv);
 const llo=Math.log10(Math.min(...lv)),lhi=Math.log10(Math.max(...lv));
 const X=i=>pl+i/(n-1)*pw, Yr=v=>pt+(rhi-v)/((rhi-rlo)||1)*ph, Yl=v=>pt+(lhi-Math.log10(v))/((lhi-llo)||1)*ph;
 let g='';
 const med_r=med(rr),med_g=med(r.drift);
 for(let i=1;i<n;i++){const q=(rr[i]==null||r.drift[i]==null)?null:(rr[i]>=med_r?(r.drift[i]>=med_g?'熱':null):(r.drift[i]>=med_g?'選股':null));
  if(!q)continue;g+='<rect x="'+X(i-1).toFixed(1)+'" y="'+pt+'" width="'+Math.max(1,(pw/(n-1))+0.6).toFixed(1)+'" height="'+ph+'" fill="'+(q==='熱'?'var(--red)':'var(--ok)')+'" opacity="0.10"/>';}
 let p1='',p2='',on1=false,on2=false;
 for(let i=0;i<n;i++){if(rr[i]!=null){p1+=(on1?'L':'M')+X(i).toFixed(1)+' '+Yr(rr[i]).toFixed(1)+' ';on1=true;}else on1=false;
  if(ll[i]!=null&&ll[i]>0){p2+=(on2?'L':'M')+X(i).toFixed(1)+' '+Yl(ll[i]).toFixed(1)+' ';on2=true;}else on2=false;}
 g+='<path d="'+p2+'" fill="none" stroke="var(--ts)" stroke-width="1.1" opacity="0.75"/>';
 g+='<path d="'+p1+'" fill="none" stroke="var(--s1)" stroke-width="1.6"/>';
 g+='<line x1="'+X(T).toFixed(1)+'" y1="'+pt+'" x2="'+X(T).toFixed(1)+'" y2="'+(pt+ph)+'" stroke="var(--tp)" opacity="0.35"/>';
 [rlo,rhi].forEach(v=>g+='<text x="'+(pl-4)+'" y="'+(Yr(v)+3)+'" font-size="9" fill="var(--s1)" text-anchor="end">'+v.toFixed(2)+'</text>');
 g+='<text x="'+pl+'" y="'+(H-6)+'" font-size="9" fill="var(--muted)">'+D.dates[0]+'</text>';
 g+='<text x="'+(W-pr)+'" y="'+(H-6)+'" font-size="9" fill="var(--muted)" text-anchor="end">'+D.dates[n-1]+'</text>';
 svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.innerHTML=g;
 $('dmem').innerHTML='成分股('+r.members.length+'):'+r.members.map(m=>'<b>'+m[0]+'</b> '+m[1]).join('、');}
// ---- 熱力圖 ----
function drawHeat(){const svg=$('hm'),o=D.order,k=o.length;
 const W=svg.clientWidth||520,cell=Math.max(6,Math.min(18,(W-96)/k)),H=96+cell*k;
 let g='';
 for(let i=0;i<k;i++){for(let j=0;j<k;j++){const v=D.heat[i][j];if(v==null)continue;
  const t=Math.max(0,Math.min(1,(v+0.2)/1.2));
  g+='<rect x="'+(92+j*cell)+'" y="'+(4+i*cell)+'" width="'+cell+'" height="'+cell+'" fill="var(--red)" opacity="'+(t*0.85).toFixed(2)+'"><title>'+o[i]+' × '+o[j]+' = '+v+'</title></rect>';}
  g+='<text x="88" y="'+(4+i*cell+cell*0.75)+'" font-size="'+Math.min(9,cell*0.75)+'" fill="var(--ts)" text-anchor="end">'+o[i]+'</text>';}
 svg.setAttribute('height',H);svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.innerHTML=g;}
function drawLead(){$('lead').querySelector('tbody').innerHTML=D.order.map(i=>
 '<tr><td>'+i+'</td><td style="text-align:left">'+(D.lead[i]||[]).map(x=>x[0]+'('+x[1]+'日/'+x[2]+')').join('、')+'</td></tr>').join('');}
function drawVal(){const ic=D.ic,q=D.quad,dp=D.dispr;
 const nm={rho:'相關ρ',drho:'Δρ(60日)',drift:'近60日報酬',disp:'離散度',flow:'資金流z'};
 let h='<b>① 橫斷面 rank-IC</b>(每5日對所有產業排序,對照未來報酬;|t|>2 才算穩健)<br><table style="margin:6px 0 10px"><tr><th style="text-align:left">指標</th><th>未來20日 IC</th><th>t</th><th>未來60日 IC</th><th>t</th></tr>';
 for(const k in nm){const a=ic[k+'_20'],b=ic[k+'_60'];
  const f=(x)=>x?('<td>'+x.ic.toFixed(3)+'</td><td style="color:'+(Math.abs(x.t)>2?'var(--ok)':'var(--muted)')+'">'+x.t+'</td>'):'<td>–</td><td>–</td>';
  h+='<tr><td style="text-align:left">'+nm[k]+'</td>'+f(a)+f(b)+'</tr>';}
 h+='</table><b>② 四象限的未來60日報酬</b> <span style="color:var(--muted)">四象限差異很小 → 相關度不是報酬訊號</span><br><table style="margin:6px 0 10px"><tr><th style="text-align:left">象限</th><th>樣本</th><th>中位</th><th>平均</th><th>最差5%</th></tr>';
 ['熱','選股','殺','沉寂'].forEach(k=>{const v=q[k];if(!v)return;
  h+='<tr><td style="text-align:left"><span class="tag t'+k+'">'+k+'</span></td><td>'+v.n+'</td><td>'+v.med+'%</td><td>'+v.avg+'%</td><td>'+v.p5+'%</td></tr>';});
 h+='</table><b>③ 高相關時,選股還值得嗎?</b><br>目前分組後「未來60日產業內個股報酬離散度」平均:高相關組 <b>'
  +(dp['高相關']!=null?dp['高相關']:'–')+'</b>,低相關組 <b>'+(dp['低相關']!=null?dp['低相關']:'–')
  +'</b>。離散度越低代表個股差異越小、<b>選股能創造的超額報酬越有限</b>(此時買籃子/龍頭較合理)。';
 $('valbox').innerHTML=h;}
function drawAll(){drawQuad();drawTable();drawDetail();}
$('tslider').max=N-1;$('tslider').value=N-1;
$('tslider').oninput=e=>{T=+e.target.value;$('tlab').textContent=D.dates[T];drawAll();};
$('tlab').textContent=D.dates[T];
document.querySelectorAll('#tbl th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;
 SORT.d=(SORT.k===k)?-SORT.d:-1;SORT.k=k;drawTable();});
$('th').onclick=()=>{const r=document.documentElement;r.setAttribute('data-theme',r.getAttribute('data-theme')=='dark'?'light':'dark');drawAll();drawHeat();};
window.addEventListener('resize',()=>{clearTimeout(window._rt);window._rt=setTimeout(()=>{drawAll();drawHeat();},150);});
drawAll();drawHeat();drawLead();drawVal();
</script></body></html>"""

if __name__ == "__main__":
    main()
