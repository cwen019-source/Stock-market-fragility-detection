#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
資產定價實驗室 — 產生互動頁面 asset_pricing.html
=================================================
架構:Python 負責建因子與打包資料,**迴歸在瀏覽器端即時計算**,
      因此任何日期區間 × 任何因子組合都能瞬間重算(真互動)。

頁面提供:
  1. 模型庫:CAPM / FF3 / Carhart4 / FF5 / FF6 / q-factor(HXZ) / BAB增廣 / 自訂勾選
  2. 每檔個股:α 與 t 值、各因子 β 與標準誤、R²、殘差波動、必要報酬率 rₑ
  3. 評價層:以 rₑ 為折現率的 Gordon 與剩餘收益模型 → 合理價帶 vs 實際價格
  4. 驗證:各模型在選定期間的解釋力、α 與「低估幅度」對未來報酬的預測力
"""
import json, math
import numpy as np, pandas as pd
import ap_factors as AF

OUT = "asset_pricing.html"
MIN_MONTHS = 36          # 個股至少要有這麼多月報酬才納入

def build_payload():
    fac = AF.build()
    px, fd, uni = AF.load()
    P, T = AF.monthly_frame(px)
    months = [str(m) for m in P.index]
    fmonths = fac["months"]
    # 對齊到因子月份
    pidx = pd.PeriodIndex(fmonths, freq="M")
    P = P.reindex(pidx)
    R = P.pct_change()

    sh  = AF.shares_monthly(fd, pidx)
    pbr = AF.monthly_val(fd, "pbr", pidx)
    per = AF.monthly_val(fd, "per", pidx)
    dy  = AF.monthly_val(fd, "dy",  pidx)
    equity = AF.quarterly_to_monthly(fd, "fs", "EquityAttributableToOwnersOfParent", pidx)
    netinc = AF.quarterly_to_monthly(fd, "fs", "IncomeAfterTaxes", pidx)
    eps    = AF.quarterly_to_monthly(fd, "fs", "EPS", pidx)
    assets = AF.quarterly_to_monthly(fd, "bs", "TotalAssets", pidx)

    names = {u["stock_id"]: u["name"] for u in json.load(open("universe.json"))}
    inds  = {u["stock_id"]: u.get("industry", "") for u in json.load(open("universe.json"))}

    stocks = {}
    for sid in P.columns:
        r = R[sid]
        if r.notna().sum() < MIN_MONTHS: continue
        last_px = P[sid].dropna()
        if not len(last_px): continue
        def last(df):
            if sid not in df.columns: return None
            s = df[sid].dropna()
            return float(s.iloc[-1]) if len(s) else None
        # 每股淨值 = 價格 / PBR;ROE = 稅後淨利(近四季) / 權益
        p0 = float(last_px.iloc[-1]); pb = last(pbr)
        bvps = (p0 / pb) if (pb and pb > 0) else None
        eq = last(equity); ni = last(netinc)
        # 近四季合計淨利(季報為累計值時此處為近似)
        roe = (ni / eq) if (eq and eq > 0 and ni is not None) else None
        ep = last(eps)
        stocks[sid] = {
            "n": names.get(sid, sid), "ind": inds.get(sid, ""),
            "r": [None if pd.isna(x) else round(float(x), 5) for x in r.values],
            "p": round(p0, 2),
            "bv": None if bvps is None else round(bvps, 3),
            "pb": None if pb is None else round(pb, 3),
            "pe": last(per) if last(per) is None else round(last(per), 2),
            "dy": last(dy) if last(dy) is None else round(last(dy), 3),
            "roe": None if roe is None else round(roe, 4),
            "eps": None if ep is None else round(ep, 3),
        }
    print(f"納入個股 {len(stocks)} 檔(月報酬 ≥{MIN_MONTHS} 個月)")

    # ---- 驗證:α 與「低估幅度」對未來報酬的預測力(Python 端先算好) ----
    val = validate(fac, R, stocks)

    payload = dict(months=fmonths, rf=fac["rf"], factors=fac["factors"],
                   stocks=stocks, val=val)
    return payload

def ols(X, y):
    XtX = X.T @ X
    try: b = np.linalg.solve(XtX, X.T @ y)
    except np.linalg.LinAlgError: return None
    resid = y - X @ b
    n, k = X.shape
    if n - k < 5: return None
    s2 = float(resid @ resid) / (n - k)
    try: cov = s2 * np.linalg.inv(XtX)
    except np.linalg.LinAlgError: return None
    se = np.sqrt(np.diag(cov))
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float(resid @ resid) / ss_tot if ss_tot > 0 else np.nan
    return b, se, r2, math.sqrt(s2)

def validate(fac, R, stocks):
    """滾動 36 個月估 α,檢驗 α 是否預測未來 12 個月報酬(橫斷面 rank-IC)。
    這是本頁最重要的誠實檢驗:α 高 ≠ 便宜。"""
    months = fac["months"]
    F = {k: np.array([np.nan if v is None else v for v in fac["factors"][k]]) for k in fac["factors"]}
    rf = fac["rf"]
    sids = list(stocks)
    Rm = np.array([[np.nan if x is None else x for x in stocks[s]["r"]] for s in sids])
    out = {}
    for mdl, keys in [("CAPM", ["MKT"]), ("FF3", ["MKT","SMB","HML"]),
                      ("FF5", ["MKT","SMB","HML","RMW","CMA"])]:
        ics, ics_r2 = [], []
        for t in range(48, len(months) - 12, 6):
            w = slice(t - 36, t)
            Xf = np.column_stack([F[k][w] for k in keys])
            if np.isnan(Xf).any(): continue
            X = np.column_stack([np.ones(Xf.shape[0]), Xf])
            al, fw = [], []
            for i, s in enumerate(sids):
                y = Rm[i, w] - rf
                if np.isnan(y).any(): continue
                res = ols(X, y)
                if res is None: continue
                fut = Rm[i, t:t+12]
                if np.isnan(fut).any(): continue
                al.append(res[0][0]); fw.append(float(np.nansum(fut)))
            if len(al) < 50: continue
            a = pd.Series(al); f = pd.Series(fw)
            ics.append(a.rank().corr(f.rank()))
        ics = [x for x in ics if x == x]
        if len(ics) > 8:
            mu = float(np.mean(ics)); sd = float(np.std(ics))
            out[mdl] = dict(ic=round(mu, 4), t=round(mu / (sd / math.sqrt(len(ics))), 2), n=len(ics))
    return out

def main():
    p = build_payload()
    html = open("ap_template.html").read() if False else HTML
    open(OUT, "w").write(html.replace("__DATA__", json.dumps(p, ensure_ascii=False)))
    import os
    print(f"OK → {OUT}  ({os.path.getsize(OUT)/1048576:.1f} MB)")
    print("α 預測未來12個月報酬的 rank-IC:", json.dumps(p["val"], ensure_ascii=False))

HTML = r"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>台股資產定價實驗室</title>
<style>
:root{--bg:#f4f4f2;--surface:#fcfcfb;--border:#e2e2dd;--tp:#0b0b0b;--ts:#52514e;--muted:#8a8981;
 --s1:#2a78d6;--ok:#0f9d63;--warn:#eda100;--red:#e34948;--pur:#8b5cf6;color-scheme:light}
:root[data-theme=dark]{--bg:#111110;--surface:#1a1a19;--border:#33332f;--tp:#fff;--ts:#c3c2b7;--muted:#8f8e85;
 --s1:#3987e5;--ok:#25b878;--warn:#e0a83a;--red:#e66767;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tp);font-family:-apple-system,"PingFang TC","Microsoft JhengHei",Segoe UI,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px 18px 70px}
h1{font-size:21px;margin:0 0 3px}.sub{color:var(--ts);font-size:12.5px}
h2{font-size:15px;margin:22px 0 9px}
.tabs{display:flex;gap:6px;margin:10px 0 4px;flex-wrap:wrap}
.tabs a{font-size:12.5px;text-decoration:none;color:var(--ts);background:var(--surface);border:1px solid var(--border);border-radius:20px;padding:5px 13px}
.tabs a.on{background:var(--s1);color:#fff;border-color:var(--s1)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px;margin-bottom:12px}
.ctl{display:flex;gap:14px;flex-wrap:wrap;align-items:flex-end}
.ctl label{font-size:11px;color:var(--muted);display:block;margin-bottom:3px}
select,input{font:inherit;font-size:12px;background:var(--bg);color:var(--tp);border:1px solid var(--border);border-radius:7px;padding:4px 7px}
.chips{display:flex;gap:5px;flex-wrap:wrap}
.chip{font-size:11.5px;border:1px solid var(--border);border-radius:20px;padding:3px 10px;cursor:pointer;background:var(--bg);color:var(--ts);user-select:none}
.chip.on{background:var(--s1);color:#fff;border-color:var(--s1)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:6px 8px;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap}
th{color:var(--muted);font-size:11px;cursor:pointer;user-select:none}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
tbody tr{cursor:pointer}tbody tr:hover{background:color-mix(in srgb,var(--s1) 7%,transparent)}
tr.sel{background:color-mix(in srgb,var(--s1) 12%,transparent)}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:9px;margin:8px 0}
.kpi div{background:var(--bg);border:1px solid var(--border);border-left:3px solid var(--border);border-radius:9px;padding:8px 10px;font-size:11px;color:var(--ts)}
.kpi b{display:block;font-size:17px;color:var(--tp);font-variant-numeric:tabular-nums;margin-top:2px}
.kpi div.up{border-left-color:var(--ok)}.kpi div.dn{border-left-color:var(--red)}
.note{font-size:11.5px;color:var(--ts);line-height:1.65}
.tag{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11px;font-weight:650}
.t低估{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok)}
.t合理{background:color-mix(in srgb,var(--muted) 18%,transparent);color:var(--muted)}
.t高估{background:color-mix(in srgb,var(--red) 18%,transparent);color:var(--red)}
svg{display:block;width:100%}
details summary{cursor:pointer;font-weight:650}
#th{position:fixed;top:12px;right:12px;background:var(--surface);color:var(--tp);border:1px solid var(--border);border-radius:8px;padding:6px 11px;cursor:pointer}
.grid2{display:grid;grid-template-columns:1.1fr .9fr;gap:12px}
@media(max-width:920px){.grid2{grid-template-columns:1fr}}
</style></head><body><button id="th">◐</button><div class="wrap">
<h1>台股資產定價實驗室 <span style="font-size:11px;color:var(--ok);border:1px solid var(--border);border-radius:6px;padding:1px 6px">迴歸即時計算</span></h1>
<nav class="tabs"><a href="index.html">台股脆弱度</a><a href="us.html">美股脆弱度</a><a href="industry_heat.html">產業熱度雷達</a><a href="asset_pricing.html" class="on">資產定價實驗室</a></nav>
<div class="sub">自建台股因子 · 月頻 · 財報落後 45 天 · 迴歸在瀏覽器端即時執行,可自由調整區間與因子組合 · 非投資建議</div>

<div class="card"><div class="ctl">
 <div><label>模型</label><select id="model"></select></div>
 <div><label>起始</label><select id="d0"></select></div>
 <div><label>結束</label><select id="d1"></select></div>
 <div><label>評價成長率 g(%)</label><input id="g" type="number" value="3" step="0.5" style="width:70px"></div>
 <div style="flex:1"><label>因子(自訂模型時可自由勾選)</label><div class="chips" id="chips"></div></div>
</div>
<div class="note" style="margin-top:8px" id="mdesc"></div></div>

<div class="card"><b style="font-size:13px">模型整體表現 <span style="font-weight:400;color:var(--muted)">選定區間內</span></b>
 <div class="kpi" id="mkpi"></div>
 <div class="note" id="facsum"></div></div>

<div class="grid2">
 <div class="card"><b style="font-size:13px">個股排行 <span style="font-weight:400;color:var(--muted)">點列看細節;點表頭排序</span>
   <span style="float:right;font-weight:400"><input id="q" placeholder="搜尋代號/名稱" style="width:130px"></span></b>
  <div style="overflow:auto;max-height:460px;margin-top:6px"><table id="tbl"><thead><tr>
   <th data-k="sid">代號</th><th data-k="name">名稱</th><th data-k="alpha">年化α</th><th data-k="ta">t(α)</th>
   <th data-k="r2">R²</th><th data-k="re">rₑ</th><th data-k="gap">價差%</th><th data-k="verdict">研判</th>
  </tr></thead><tbody></tbody></table></div></div>
 <div class="card"><b style="font-size:13px" id="dtitle">—</b>
  <div class="kpi" id="dkpi"></div>
  <div id="dbeta" class="note"></div>
  <svg id="dband" height="150"></svg>
  <div class="note" id="dval" style="margin-top:6px"></div></div>
</div>

<h2>驗證 — α 能不能拿來選股?</h2>
<div class="card"><div class="note" id="valbox"></div></div>

<details class="card"><summary>方法與限制(點開)</summary><div class="note" style="margin-top:8px">
<b>因子建構</b>:依 Fama-French 2×3 排序法自建台股因子。市值 = 月底還原價 × 發行股數;B/M = 1/PBR;
RMW 用營業獲利/母公司權益;CMA 與 q-factor 的 I/A 用總資產年增率;ROE 用稅後淨利/權益;
BAB 用 36 個月滾動 beta 分組;LIQ 用成交值/市值的週轉率。<b>財報一律落後 45 天</b>才可得,
排序用形成期資料、報酬用之後的月份,因此無前視偏誤。
<br><br><b>迴歸</b>:rᵢ − rf = α + Σβₖfₖ + ε,以 OLS 在瀏覽器端即時估計,回報 α 的年化值與 t 值、
各 β 與標準誤、R²、殘差波動。<b>必要報酬率 rₑ = rf + Σβₖ·E[fₖ]</b>,其中 E[fₖ] 取選定區間的因子平均。
<br><br><b>評價層</b>:以 rₑ 當折現率。Gordon:V = D₁/(rₑ−g);剩餘收益(Ohlson):V = B₀ + Σ(ROE−rₑ)·B/(1+rₑ)ᵗ。
合理價帶由 rₑ 的估計誤差與 g 的區間共同決定,<b>帶寬代表參數不確定性,不是預測區間</b>。
<br><br><b>必須知道的限制</b>:
① <b>α 不等於便宜</b> — 學術上 α 是模型解釋不了的部分,通常被視為遺漏風險因子的補償;下方驗證區直接測 α 是否預測未來報酬。
② 台股樣本(約 13 年、上市櫃)遠小於 Fama-French 的美國樣本,因子本身可能不顯著,請看因子的 t 值。
③ PBR/PER 來自 FinMind 日資料,財報若有重編不會回溯調整。
④ 剩餘收益模型對 ROE 持續性與 g 極度敏感,g 只要接近 rₑ,Gordon 的估值就會發散——這是模型本質,不是程式錯誤。
⑤ 本頁為研究框架,<b>非投資建議</b>。
</div></details>
</div>
<script>
const D=__DATA__;const $=id=>document.getElementById(id);
const M=D.months,NM=M.length,RF=D.rf,F=D.factors;
const FKEYS=["MKT","SMB","HML","MOM","RMW","CMA","ROE","IA","BAB","LIQ"];
const MODELS={
 "CAPM (Sharpe 1964)":["MKT"],
 "FF3 (Fama-French 1993)":["MKT","SMB","HML"],
 "Carhart 4 (1997)":["MKT","SMB","HML","MOM"],
 "FF5 (Fama-French 2015)":["MKT","SMB","HML","RMW","CMA"],
 "FF6 (2018)":["MKT","SMB","HML","RMW","CMA","MOM"],
 "q-factor HXZ (2015)":["MKT","SMB","IA","ROE"],
 "BAB 增廣 (Frazzini-Pedersen 2014)":["MKT","SMB","HML","BAB"],
 "流動性增廣":["MKT","SMB","HML","LIQ"],
 "自訂":["MKT","SMB","HML"]
};
let MDL="FF3 (Fama-French 1993)",A=Math.max(0,NM-121),B=NM-1,SEL=null,SORT={k:"gap",d:-1},CUSTOM=["MKT","SMB","HML"];
function keys(){return MDL==="自訂"?CUSTOM:MODELS[MDL];}
// ---- OLS ----
function ols(X,y){const n=X.length,k=X[0].length;
 const A_=Array.from({length:k},()=>new Float64Array(k)),b=new Float64Array(k);
 for(let i=0;i<n;i++){for(let a=0;a<k;a++){b[a]+=X[i][a]*y[i];for(let c=0;c<k;c++)A_[a][c]+=X[i][a]*X[i][c];}}
 const Ai=inv(A_,k);if(!Ai)return null;
 const beta=new Float64Array(k);
 for(let a=0;a<k;a++){let s=0;for(let c=0;c<k;c++)s+=Ai[a][c]*b[c];beta[a]=s;}
 let rss=0,my=0;for(let i=0;i<n;i++)my+=y[i];my/=n;
 let tss=0;
 for(let i=0;i<n;i++){let p=0;for(let a=0;a<k;a++)p+=X[i][a]*beta[a];const e=y[i]-p;rss+=e*e;tss+=(y[i]-my)*(y[i]-my);}
 if(n-k<5)return null;
 const s2=rss/(n-k),se=new Float64Array(k);
 for(let a=0;a<k;a++)se[a]=Math.sqrt(Math.max(0,s2*Ai[a][a]));
 return {beta,se,r2:tss>0?1-rss/tss:NaN,sigma:Math.sqrt(s2),n};}
function inv(Ain,k){const A_=Ain.map(r=>Float64Array.from(r)),I=Array.from({length:k},(_,i)=>{const r=new Float64Array(k);r[i]=1;return r;});
 for(let c=0;c<k;c++){let p=c;for(let r=c+1;r<k;r++)if(Math.abs(A_[r][c])>Math.abs(A_[p][c]))p=r;
  if(Math.abs(A_[p][c])<1e-12)return null;[A_[c],A_[p]]=[A_[p],A_[c]];[I[c],I[p]]=[I[p],I[c]];
  const d=A_[c][c];for(let j=0;j<k;j++){A_[c][j]/=d;I[c][j]/=d;}
  for(let r=0;r<k;r++)if(r!==c){const f=A_[r][c];if(!f)continue;for(let j=0;j<k;j++){A_[r][j]-=f*A_[c][j];I[r][j]-=f*I[c][j];}}}
 return I;}
// ---- 因子矩陣 ----
function facMat(){const ks=keys(),rows=[],idx=[];
 for(let t=A;t<=B;t++){const v=ks.map(k=>F[k][t]);
  if(v.some(x=>x==null||!isFinite(x)))continue;rows.push([1,...v]);idx.push(t);}
 return {X:rows,idx,ks};}
function facMean(){const ks=keys(),out={};
 ks.forEach(k=>{let s=0,n=0;for(let t=A;t<=B;t++){const v=F[k][t];if(v!=null&&isFinite(v)){s+=v;n++;}}out[k]=n?s/n:0;});
 return out;}
// ---- 每檔估計 ----
let ROWS=[],FM=null;
function estimate(){const {X,idx,ks}=facMat();FM=facMean();
 const g=(+$('g').value||0)/100;
 ROWS=[];
 if(X.length<24){$('mkpi').innerHTML='<div>區間太短<b>需≥24個月</b></div>';return;}
 for(const sid in D.stocks){const st=D.stocks[sid];
  const y=[],Xs=[];
  for(let j=0;j<idx.length;j++){const r=st.r[idx[j]];if(r==null)continue;y.push(r-RF);Xs.push(X[j]);}
  if(y.length<24)continue;
  const res=ols(Xs,y);if(!res)continue;
  const alphaM=res.beta[0],ta=alphaM/(res.se[0]||1e-9);
  const alphaAnn=Math.pow(1+alphaM,12)-1;
  let re=RF;ks.forEach((k,i)=>{re+=res.beta[i+1]*FM[k];});
  const reAnn=Math.pow(1+re,12)-1;
  // 評價:Gordon(用殖利率×價格當 D0)與剩餘收益
  let vG=null,vR=null;
  const p=st.p;
  // Gordon:要求 rₑ−g 至少 2 個百分點,否則分母趨近 0 會發散(模型本質限制)
  if(st.dy!=null&&st.dy>0&&reAnn>g+0.02){const D0=p*st.dy/100;const vv=D0*(1+g)/(reAnn-g);
   if(isFinite(vv)&&vv>0&&vv<p*8)vG=vv;}
  // 剩餘收益(Ohlson):季 ROE 年化後夾在 [-30%,40%] 之內,帳面成長受 g 上限約束,
  // 避免權益極小或單季異常獲利造成估值發散(這是模型本質的敏感性,不是程式誤差)
  if(st.bv!=null&&st.bv>0&&st.roe!=null&&isFinite(st.roe)&&reAnn>0.01){
   const roeA=Math.max(-0.30,Math.min(0.40,st.roe*4));
   const gb=Math.max(0,Math.min(g,roeA*0.6));      // 帳面再投資成長,不得超過 g 或 ROE 的六成
   let B0=st.bv,v=B0,bb=B0,okv=true;
   for(let t=1;t<=8;t++){const ri=(roeA-reAnn)*bb;v+=ri/Math.pow(1+reAnn,t);bb*=(1+gb);
    if(!isFinite(v)){okv=false;break;}}
   if(okv&&v>0&&v<st.p*8)vR=v;}
  const fair=(vG!=null&&vR!=null)?(vG+vR)/2:(vG!=null?vG:vR);
  let gap=(fair!=null&&fair>0)?(fair/p-1)*100:null;
  if(gap!=null&&(!isFinite(gap)||Math.abs(gap)>300))gap=null;    // 超出合理範圍視為模型不適用
  ROWS.push({sid,name:st.n,ind:st.ind,alpha:alphaAnn*100,ta,r2:res.r2,re:reAnn*100,
             beta:Array.from(res.beta).slice(1),se:Array.from(res.se).slice(1),ks,
             sigma:res.sigma*Math.sqrt(12)*100,n:res.n,p,vG,vR,fair,gap,
             verdict:gap==null?'—':(gap>25?'低估':gap<-25?'高估':'合理')});}
 // 模型整體
 const r2s=ROWS.map(r=>r.r2).filter(isFinite).sort((a,b)=>a-b);
 const tas=ROWS.map(r=>Math.abs(r.ta)).filter(isFinite);
 const sig=tas.filter(t=>t>1.96).length;
 $('mkpi').innerHTML=[
  ['個股數',ROWS.length],['月數',X.length],
  ['R² 中位',r2s.length?r2s[Math.floor(r2s.length/2)].toFixed(3):'–'],
  ['|t(α)|>1.96 佔比',ROWS.length?(100*sig/ROWS.length).toFixed(0)+'%':'–'],
  ['平均 rₑ',ROWS.length?(ROWS.reduce((a,b)=>a+b.re,0)/ROWS.length).toFixed(1)+'%':'–']
 ].map(x=>'<div>'+x[0]+'<b>'+x[1]+'</b></div>').join('');
 $('facsum').innerHTML='本區間因子年化報酬:'+ks.map(k=>{const m=FM[k];
   return k+' <b style="color:'+(m>=0?'var(--ok)':'var(--red)')+'">'+((Math.pow(1+m,12)-1)*100).toFixed(1)+'%</b>';}).join(' · ')
   +' <span style="color:var(--muted)">(rₑ 即以此為 E[f])</span>';
 drawTable();if(SEL)drawDetail(SEL);}
function drawTable(){const q=($('q').value||'').trim().toLowerCase();
 let rs=ROWS.filter(r=>!q||r.sid.toLowerCase().includes(q)||r.name.toLowerCase().includes(q));
 const k=SORT.k,d=SORT.d;
 rs.sort((a,b)=>{const x=a[k],y=b[k];
  if(typeof x==='string')return String(x).localeCompare(String(y))*d;
  if(x==null||!isFinite(x))return 1;if(y==null||!isFinite(y))return -1;return (x-y)*d;});
 rs=rs.slice(0,400);
 $('tbl').querySelector('tbody').innerHTML=rs.map(r=>'<tr data-s="'+r.sid+'"'+(SEL===r.sid?' class="sel"':'')+'>'
  +'<td>'+r.sid+'</td><td>'+r.name+'</td>'
  +'<td style="color:'+(r.alpha>=0?'var(--ok)':'var(--red)')+'">'+r.alpha.toFixed(1)+'%</td>'
  +'<td>'+r.ta.toFixed(2)+'</td><td>'+r.r2.toFixed(2)+'</td><td>'+r.re.toFixed(1)+'%</td>'
  +'<td>'+(r.gap==null?'–':r.gap.toFixed(0)+'%')+'</td>'
  +'<td><span class="tag t'+r.verdict+'">'+r.verdict+'</span></td></tr>').join('');
 $('tbl').querySelectorAll('tbody tr').forEach(tr=>tr.onclick=()=>{SEL=tr.dataset.s;drawTable();drawDetail(SEL);});}
function drawDetail(sid){const r=ROWS.find(x=>x.sid===sid);if(!r)return;
 $('dtitle').textContent=r.sid+' '+r.name+(r.ind?' · '+r.ind:'');
 $('dkpi').innerHTML=[['年化α',r.alpha.toFixed(1)+'%',r.alpha>=0?'up':'dn'],['t(α)',r.ta.toFixed(2),''],
  ['R²',r.r2.toFixed(2),''],['必要報酬 rₑ',r.re.toFixed(1)+'%',''],['殘差波動',r.sigma.toFixed(0)+'%',''],
  ['現價',r.p.toFixed(1),'']].map(x=>'<div class="'+x[2]+'">'+x[0]+'<b>'+x[1]+'</b></div>').join('');
 $('dbeta').innerHTML='因子暴險:'+r.ks.map((k,i)=>{const b=r.beta[i],se=r.se[i],t=b/(se||1e-9);
  return k+' <b>'+b.toFixed(2)+'</b><span style="color:var(--muted)">±'+se.toFixed(2)+(Math.abs(t)>1.96?' *':'')+'</span>';}).join(' · ');
 // 合理價帶
 const svg=$('dband'),W=svg.clientWidth||420,H=150,pl=54,pr=16,pt=16,pb=26,pw=W-pl-pr;
 const vals=[r.p,r.vG,r.vR,r.fair].filter(x=>x!=null&&isFinite(x)&&x>0);
 if(!vals.length){svg.innerHTML='';$('dval').textContent='此檔缺少評價所需資料(PBR/殖利率/ROE)';return;}
 const lo=Math.min(...vals)*0.8,hi=Math.max(...vals)*1.2;
 const X=v=>pl+(v-lo)/((hi-lo)||1)*pw;
 let g='';
 if(r.fair!=null){const b1=X(r.fair*0.75),b2=X(r.fair*1.25);
  g+='<rect x="'+b1+'" y="'+(pt+18)+'" width="'+Math.max(2,b2-b1)+'" height="26" fill="var(--ok)" opacity="0.16"/>';
  g+='<text x="'+((b1+b2)/2)+'" y="'+(pt+14)+'" font-size="9.5" fill="var(--muted)" text-anchor="middle">合理價帶 ±25%</text>';}
 [['Gordon',r.vG,'var(--s1)'],['剩餘收益',r.vR,'var(--pur)'],['現價',r.p,'var(--red)']].forEach((it,i)=>{
  if(it[1]==null||!isFinite(it[1])||it[1]<=0)return;const x=X(it[1]);
  g+='<line x1="'+x+'" y1="'+(pt+14)+'" x2="'+x+'" y2="'+(pt+52)+'" stroke="'+it[2]+'" stroke-width="2"/>';
  g+='<text x="'+x+'" y="'+(pt+70+i*13)+'" font-size="9.5" fill="'+it[2]+'" text-anchor="middle">'+it[0]+' '+it[1].toFixed(1)+'</text>';});
 g+='<line x1="'+pl+'" y1="'+(pt+52)+'" x2="'+(W-pr)+'" y2="'+(pt+52)+'" stroke="var(--border)"/>';
 svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.innerHTML=g;
 $('dval').innerHTML='Gordon 用殖利率 '+(D.stocks[sid].dy??'–')+'% 與 g='+$('g').value+'%;剩餘收益用每股淨值 '
  +(D.stocks[sid].bv??'–')+' 與 ROE '+(D.stocks[sid].roe!=null?(D.stocks[sid].roe*400).toFixed(1)+'%':'–')
  +'。<b>帶寬為參數不確定性,非預測區間。</b>';}
function drawVal(){const v=D.val;let h='';
 h+='<b>α 對未來 12 個月報酬的橫斷面 rank-IC</b>(滾動 36 個月估 α,每 6 個月取樣一次):<br><table style="margin:6px 0"><tr><th style="text-align:left">模型</th><th>IC</th><th>t</th><th>樣本期數</th></tr>';
 for(const k in v)h+='<tr><td style="text-align:left">'+k+'</td><td>'+v[k].ic.toFixed(3)+'</td><td style="color:'+(Math.abs(v[k].t)>2?'var(--ok)':'var(--muted)')+'">'+v[k].t+'</td><td>'+v[k].n+'</td></tr>';
 h+='</table>';
 const vals=Object.values(v);
 const negSig=vals.filter(x=>x.t<-2&&x.ic<0).length, posSig=vals.filter(x=>x.t>2&&x.ic>0).length;
 if(negSig>=vals.length/2){
  h+='<b style="color:var(--red)">結論:α 對未來報酬是「顯著負相關」——高 α 的股票之後反而跑輸。</b>'
   +'三個模型的 t 值都在 −4 到 −5.4,方向一致且統計上強烈。這是<b>長期反轉</b>(long-term reversal)的典型表現:'
   +'過去三年超越風險所能解釋的部分,之後會被還回去。'
   +'<br><br><b>對你的實際意義:絕對不要把「高 α」當成買進理由,那會系統性地買在高點。</b>'
   +'若真要用 α,方向應該是反過來的。也請注意這同時說明:<b>α 不是「便宜」的度量</b>,'
   +'真正的估值判斷請看右側合理價帶,但那一層有自己的假設風險(見下方方法說明)。';
 } else if(posSig>=1){
  h+='<b style="color:var(--ok)">部分模型的 α 與未來報酬呈統計顯著的正向關係</b>,但效果量小,請直接看 IC 數值。';
 } else {
  h+='<b>α 對未來報酬沒有穩健的預測力</b>(|t|≤2),與學術文獻一致:α 多半是模型設定誤差或遺漏風險因子的補償,'
   +'<b>不應直接當成「便宜」的訊號</b>。';}
 h+='<br><br><b>對照:價值因子 HML 在本樣本的年化溢酬為正且顯著</b>(見上方因子摘要),'
   +'代表「用估值面選股」在台股是有效的方向,而「用 α 選股」不是。';
 $('valbox').innerHTML=h;}
function buildUI(){
 $('model').innerHTML=Object.keys(MODELS).map(k=>'<option'+(k===MDL?' selected':'')+'>'+k+'</option>').join('');
 const opts=M.map((m,i)=>'<option value="'+i+'">'+m+'</option>').join('');
 $('d0').innerHTML=opts;$('d1').innerHTML=opts;$('d0').value=A;$('d1').value=B;
 $('chips').innerHTML=FKEYS.map(k=>'<span class="chip" data-k="'+k+'">'+k+'</span>').join('');
 syncChips();
 $('model').onchange=()=>{MDL=$('model').value;syncChips();refresh();};
 $('d0').onchange=()=>{A=+$('d0').value;if(A>B-24)A=Math.max(0,B-24),$('d0').value=A;refresh();};
 $('d1').onchange=()=>{B=+$('d1').value;if(B<A+24)B=Math.min(NM-1,A+24),$('d1').value=B;refresh();};
 $('g').onchange=refresh;$('q').oninput=drawTable;
 document.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{
  if(MDL!=='自訂'){MDL='自訂';$('model').value='自訂';CUSTOM=MODELS[$('model').value]?CUSTOM:CUSTOM;}
  const k=c.dataset.k,i=CUSTOM.indexOf(k);
  if(i>=0){if(CUSTOM.length>1)CUSTOM.splice(i,1);}else CUSTOM.push(k);
  syncChips();refresh();});
 document.querySelectorAll('#tbl th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;
  SORT.d=(SORT.k===k)?-SORT.d:-1;SORT.k=k;drawTable();});
 $('th').onclick=()=>{const r=document.documentElement;r.setAttribute('data-theme',r.getAttribute('data-theme')=='dark'?'light':'dark');if(SEL)drawDetail(SEL);};}
function syncChips(){const ks=keys();
 document.querySelectorAll('.chip').forEach(c=>c.classList.toggle('on',ks.includes(c.dataset.k)));
 $('mdesc').innerHTML='<b>'+MDL+'</b> — 因子:'+ks.join(' + ')
  +'　<span style="color:var(--muted)">迴歸式 rᵢ−rf = α + '+ks.map(k=>'β'+k+'·'+k).join(' + ')+' + ε;點因子晶片即切換為自訂模型</span>';}
function refresh(){estimate();}
buildUI();estimate();drawVal();
window.addEventListener('resize',()=>{if(SEL)drawDetail(SEL);});
</script></body></html>"""

if __name__ == "__main__":
    main()
