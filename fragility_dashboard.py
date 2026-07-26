#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股脆弱度儀表板 (每日更新, 互動版)
================================
彙整槓桿、估值、情緒、資金流、波動度等訊號, 正規化後合成「脆弱度分數」,
附壓力測試(敏感度)、脆弱度歷史互動線圖(游標同步各燈號當日數值、可設日期區間、NBER 衰退灰帶)。
純風控壓力計, 不是擇時工具, 非投資建議。

資料源(皆免費, 無需金鑰):
  FinMind : 融資餘額 / 加權指數+成交值 / 三大法人 / USD-TWD
  FRED    : 美國 VIX(VIXCLS) / S&P500

用法:
  pip install requests pandas numpy
  python3 fragility_dashboard.py     # 產生 index.html + 追加 fragility_history.csv
  (可選) 設環境變數 FINMIND_TOKEN / OUT_HTML
"""
import os, sys, json, math
import requests, pandas as pd, numpy as np

FINMIND="https://api.finmindtrade.com/api/v4/data"
TOKEN=os.environ.get("FINMIND_TOKEN","")
START="2012-01-01"
HIST_CSV="fragility_history.csv"
OUT_HTML=os.environ.get("OUT_HTML","index.html")
INVERT={"vix_level","foreign_flow"}     # 越低越危險
INTERNAL={"margin_resid_z","margin_yoy_div","margin_roc","trend_health"}   # 內部;其餘=外部
WEIGHTS={"margin_resid_z":1.4,"margin_yoy_div":1.3,"margin_roc":1.0,"vix_level":0.9,
         "vix_spike":0.7,"foreign_flow":1.1,"fx_pressure":0.8,"trend_health":1.0,
         "us_nasdaq":0.9,"kr_bubble":0.6}
# 顯示格式 [是否強制正負號, 小數位, 單位]
FMT={"margin_resid_z":[1,1,"σ"],"margin_yoy_div":[1,1,"pp"],"margin_roc":[0,1,"%"],
     "vix_level":[0,1,""],"vix_spike":[1,1,""],"foreign_flow":[1,0,"億"],
     "fx_pressure":[1,1,"%"],"trend_health":[1,1,"%"],"us_nasdaq":[1,1,"%"],"kr_bubble":[1,1,"%"]}
ORDER=["margin_resid_z","margin_yoy_div","margin_roc","trend_health",
       "foreign_flow","fx_pressure","vix_level","vix_spike","us_nasdaq","kr_bubble"]
# NBER 美國景氣衰退期(資料起於~2013, 實務上僅 2020 COVID 落在範圍內)
NBER=[["1990-07-01","1991-03-31"],["2001-03-01","2001-11-30"],
      ["2007-12-01","2009-06-30"],["2020-02-01","2020-04-30"]]

def fm(dataset, **kw):
    p={"dataset":dataset,"start_date":START,"end_date":"2030-12-31",**kw}
    if TOKEN: p["token"]=TOKEN
    for _ in range(4):
        try:
            r=requests.get(FINMIND,params=p,timeout=90)
            if r.status_code==200: return pd.DataFrame(r.json().get("data",[]))
        except Exception: pass
    return pd.DataFrame()

def fred(series):
    try:
        r=requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd=2012-01-01",timeout=40)
        df=pd.read_csv(pd.io.common.StringIO(r.text)); df.columns=["date","val"]
        df["date"]=pd.to_datetime(df["date"]); df["val"]=pd.to_numeric(df["val"],errors="coerce")
        return df.dropna().set_index("date")["val"]
    except Exception:
        return pd.Series(dtype=float)

def get_data():
    d={}
    m=fm("TaiwanStockTotalMarginPurchaseShortSale")
    if len(m):
        mm=m[m["name"]=="MarginPurchaseMoney"].copy()
        mm["date"]=pd.to_datetime(mm["date"]); mm["v"]=pd.to_numeric(mm["TodayBalance"])/1e8
        d["margin"]=mm.sort_values("date").set_index("date")["v"]
    t=fm("TaiwanStockPrice", data_id="TAIEX")
    if len(t):
        t["date"]=pd.to_datetime(t["date"])
        d["idx"]=t.sort_values("date").set_index("date")["close"].astype(float)
        d["turn"]=(pd.to_numeric(t.set_index(t["date"])["Trading_money"])/1e8).sort_index()
    ii=fm("TaiwanStockTotalInstitutionalInvestors")
    if len(ii):
        f=ii[ii["name"]=="Foreign_Investor"].copy()
        f["date"]=pd.to_datetime(f["date"]); f["net"]=(pd.to_numeric(f["buy"])-pd.to_numeric(f["sell"]))/1e8
        d["foreign"]=f.sort_values("date").set_index("date")["net"]
    fx=fm("TaiwanExchangeRate", data_id="USD")
    if len(fx):
        fx["date"]=pd.to_datetime(fx["date"])
        d["usdtwd"]=fx.sort_values("date").set_index("date")["spot_sell"].astype(float)
    d["vix"]=fred("VIXCLS")
    d["nasdaq"]=fred("NASDAQCOM")      # 美股(日)
    d["kospi"]=fred("SPASTT01KRM661N") # 韓股(月, OECD)
    return d

def compute(d):
    R={}; idx=d.get("idx"); margin=d.get("margin"); turn=d.get("turn")
    base=pd.DataFrame({"idx":idx,"margin":margin,"turn":turn}).dropna()
    if len(base)>300:
        b=base.copy(); b["tma"]=b["turn"].rolling(60).mean().bfill()
        X=np.column_stack([np.log(b["idx"]),np.log(b["tma"]),np.ones(len(b))]); y=np.log(b["margin"])
        beta,_,_,_=np.linalg.lstsq(X,y,rcond=None); resid=y-X@beta; zr=(resid-resid.mean())/resid.std()
        R["margin_resid_z"]=dict(val=float(zr.iloc[-1]),series=zr,unit="σ",label="融資超額水位",
            note="剔除指數/量能後,融資多出幾個σ(去趨勢殘差)")
    if len(base)>260:
        div=(base["margin"].pct_change(244)*100)-(base["idx"].pct_change(244)*100)
        R["margin_yoy_div"]=dict(val=float(div.iloc[-1]),series=div,unit="pp",label="融資成長背離(YoY)",
            note="融資年增率 − 指數年增率;正越大=槓桿跑贏價值")
    if len(base)>150:
        roc=base["margin"].pct_change(126)*100
        R["margin_roc"]=dict(val=float(roc.iloc[-1]),series=roc,unit="%",label="融資半年擴張",
            note="融資餘額近126日變化;拋物線噴出=froth")
    vix=d.get("vix")
    if len(vix):
        R["vix_level"]=dict(val=float(vix.iloc[-1]),series=vix,unit="",label="VIX 波動度",
            note="低=自滿(脆弱累積), 高=恐慌(壓力已至)")
        R["vix_spike"]=dict(val=float(vix.iloc[-1]-vix.iloc[-6]) if len(vix)>6 else 0.0,
            series=vix.diff(5),unit="",label="VIX 5日跳升",note="正跳升=避險情緒轉向")
    fo=d.get("foreign")
    if len(fo):
        roll=fo.rolling(20).sum()
        R["foreign_flow"]=dict(val=float(roll.iloc[-1]),series=roll,unit="億",label="外資20日淨流向",
            note="大額賣超=資金撤離/去槓桿確認")
    fx=d.get("usdtwd")
    if len(fx):
        chg=(fx/fx.shift(20)-1)*100
        R["fx_pressure"]=dict(val=float(chg.iloc[-1]),series=chg,unit="%",label="台幣20日貶值",
            note="台幣走貶=外資匯出/資金外流壓力")
    if idx is not None and len(idx)>240:
        dev=(idx/idx.rolling(240).mean()-1)*100
        R["trend_health"]=dict(val=float(dev.iloc[-1]),series=dev,unit="%",label="指數距年線",
            note="過高=乖離過大易回檔;跌破=趨勢轉弱")
    nq=d.get("nasdaq")
    if nq is not None and len(nq)>200:
        dev=(nq/nq.rolling(200).mean()-1)*100
        R["us_nasdaq"]=dict(val=float(dev.iloc[-1]),series=dev,unit="%",label="美股乖離(Nasdaq)",
            note="Nasdaq 距200日均;過高=美股過熱/AI泡沫風險(外部)")
    kr=d.get("kospi")
    if kr is not None and len(kr)>12:
        dev=(kr/kr.rolling(12).mean()-1)*100
        R["kr_bubble"]=dict(val=float(dev.iloc[-1]),series=dev,unit="%",label="韓股乖離(KOSPI)",
            note="韓股(月)距12月均;過高=韓股過熱(2025–26曾極端槓桿→傳染)。月資料較粗(外部)")
    return R

def danger_ser(k, s):
    rk=s.rank(pct=True)*100
    return 100-rk if k in INVERT else rk

def build_app_data(d, R):
    """對齊所有指標到指數交易日, 產生 dates / 合成分數 / 每指標(值+危險度) 供前端同步顯示。"""
    master=d["idx"].index
    aligned={}; wsum=0.0; dmat=pd.DataFrame(index=master)
    for k,r in R.items():
        s=r["series"]
        if not isinstance(s,pd.Series): continue
        sa=s.reindex(master.union(s.index)).sort_index().ffill().reindex(master)
        dng=danger_ser(k,sa)
        aligned[k]=dict(label=r["label"],unit=r["unit"],note=r["note"],fmt=FMT.get(k,[0,1,r["unit"]]),
            group=("內部" if k in INTERNAL else "外部"),
            val=[None if pd.isna(x) else round(float(x),2) for x in sa.values],
            dng=[None if pd.isna(x) else int(round(float(x))) for x in dng.values])
        w=WEIGHTS.get(k,1.0); dmat[k]=dng*w; wsum+=w
    comp=(dmat.sum(axis=1)/wsum) if wsum else pd.Series(index=master,dtype=float)
    mask=comp.notna()
    dates=[str(x.date()) for x in master[mask]]
    inds={k:{**v,"val":[v["val"][i] for i in range(len(mask)) if mask.iloc[i]],
                    "dng":[v["dng"][i] for i in range(len(mask)) if mask.iloc[i]]} for k,v in aligned.items()}
    return dict(dates=dates, comp=[round(float(x),1) for x in comp[mask].values],
                inds=inds, order=[k for k in ORDER if k in inds])

def stress_test(d):
    margin=d.get("margin"); cur=float(margin.iloc[-1]) if margin is not None and len(margin) else float("nan")
    rows=[]
    for x in [5,10,15,20,25]:
        mu=160*(1-x/100); frac=0.5*(1+math.erf((130-mu)/25/math.sqrt(2)))
        rows.append(dict(drop=x,avg_ratio=round(mu),pct_call=round(frac*100,1),at_risk=round(cur*frac)))
    return cur, rows

def build_html(app, comp_now, asof, stress_cur, stress_rows):
    strows="".join(f"<tr><td>指數 −{x['drop']}%</td><td>{x['avg_ratio']}%</td><td class='r'>{x['pct_call']}%</td><td class='r'>{x['at_risk']:,} 億</td></tr>" for x in stress_rows)
    js=APP_JS.replace("__APPDATA__",json.dumps(app,ensure_ascii=False)).replace("__REC__",json.dumps(NBER))
    return TEMPLATE.replace("__ASOF__",asof).replace("__STRESS__",strows).replace("__MARGIN__",f"{stress_cur:,.0f}").replace("__APPJS__",js)

TEMPLATE=r"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>台股脆弱度儀表板</title>
<style>
:root{--bg:#f4f4f2;--surface:#fcfcfb;--border:#e2e2dd;--tp:#0b0b0b;--ts:#52514e;--muted:#8a8981;
 --series-1:#2a78d6;--ok:#0f9d63;--warn:#eda100;--red:#e34948;color-scheme:light}
:root[data-theme=dark]{--bg:#111110;--surface:#1a1a19;--border:#33332f;--tp:#fff;--ts:#c3c2b7;--muted:#8f8e85;
 --series-1:#3987e5;--ok:#25b878;--warn:#e0a83a;--red:#e66767;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tp);
 font-family:-apple-system,"PingFang TC","Microsoft JhengHei",Segoe UI,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:24px 18px 70px}
h1{font-size:21px;margin:0 0 3px}.sub{color:var(--ts);font-size:12.5px}
.hero{display:flex;gap:20px;align-items:center;background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:20px 24px;margin:18px 0}
.gauge{width:130px;height:130px;border-radius:50%;display:grid;place-items:center;flex:none;background:conic-gradient(var(--gc) calc(var(--v)*1%),var(--border) 0)}
.gauge .inner{width:104px;height:104px;border-radius:50%;background:var(--surface);display:grid;place-items:center;text-align:center}
.gauge .num{font-size:34px;font-weight:700;line-height:1}.gauge .lb{font-size:10px;color:var(--muted)}
.hero .txt .big{font-size:19px;font-weight:650}.hero .txt .d{color:var(--ts);font-size:13px;margin-top:5px;max-width:560px}
.hero .txt .vd{font-size:12px;color:var(--series-1);margin-top:6px;font-weight:600;min-height:16px}
.trend{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px;margin-bottom:12px}
.trend .th{font-size:13px;color:var(--ts);margin-bottom:8px;font-weight:600;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;align-items:center}
.ctrl{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.ctrl button{font:inherit;font-size:11.5px;background:var(--bg);color:var(--ts);border:1px solid var(--border);border-radius:7px;padding:4px 9px;cursor:pointer}
.ctrl button.on{background:var(--series-1);color:#fff;border-color:var(--series-1)}
.ctrl input[type=date]{font:inherit;font-size:11px;background:var(--bg);color:var(--tp);border:1px solid var(--border);border-radius:7px;padding:3px 6px}
#tc{position:relative;width:100%}#tcsvg{display:block;width:100%;cursor:crosshair}
#tctip,#sctip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:5px 8px;font-size:11px;line-height:1.4;opacity:0;transition:opacity .08s;box-shadow:0 2px 8px rgba(0,0,0,.18);white-space:nowrap;z-index:3}
#scsvg{display:block;width:100%;cursor:crosshair}#sc{width:100%}
.scstats{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-top:10px}
@media(max-width:720px){.scstats{grid-template-columns:repeat(2,1fr)}}
.stile{background:var(--bg);border:1px solid var(--border);border-radius:9px;padding:9px 11px;border-left:3px solid var(--border)}
.stile.red{border-left-color:var(--red)}.stile.warn{border-left-color:var(--warn)}.stile.ok{border-left-color:var(--ok)}
.stile .l{font-size:11px;color:var(--ts)}.stile .v{font-size:18px;font-weight:680;margin:2px 0;font-variant-numeric:tabular-nums}
.stile .s{font-size:10px;color:var(--muted)}
.indbar{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 6px}
.indbar button{font:inherit;font-size:11.5px;background:var(--bg);color:var(--ts);border:1px solid var(--border);border-radius:20px;padding:4px 10px;cursor:pointer}
.indbar button.on{background:var(--series-1);color:#fff;border-color:var(--series-1)}
.indcap{font-size:11px;color:var(--muted);margin-bottom:10px}
.cic{font-size:10.5px;color:var(--ts);margin-top:5px;border-top:1px solid var(--border);padding-top:5px}
.gtitle{font-size:12.5px;font-weight:650;margin:14px 0 8px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
@media(max-width:860px){.grid{grid-template-columns:repeat(2,1fr)}}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:13px 14px;border-left:3px solid var(--border);transition:border-color .1s}
.card.red{border-left-color:var(--red)}.card.warn{border-left-color:var(--warn)}.card.ok{border-left-color:var(--ok)}
.ct{font-size:12px;color:var(--ts)}.cv{font-size:23px;font-weight:680;margin:3px 0;font-variant-numeric:tabular-nums}
.cbarwrap{height:5px;background:var(--border);border-radius:3px;overflow:hidden}.cbar{height:100%;background:var(--red);width:0}
.card.ok .cbar{background:var(--ok)}.card.warn .cbar{background:var(--warn)}
.cp{font-size:11px;color:var(--muted);margin:4px 0 2px}.cs svg{display:block;margin:2px 0}
.cn{font-size:10.5px;color:var(--muted);line-height:1.35;margin-top:4px}
h2{font-size:15px;margin:26px 0 10px}
table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden}
th,td{padding:9px 12px;font-size:13px;border-bottom:1px solid var(--border);text-align:left}
th{color:var(--muted);font-size:11.5px}td.r{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.note{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--series-1);border-radius:8px;padding:12px 15px;margin-top:18px;font-size:12px;color:var(--ts);line-height:1.6}
#th{position:fixed;top:12px;right:12px;background:var(--surface);color:var(--tp);border:1px solid var(--border);border-radius:8px;padding:6px 11px;cursor:pointer}
</style></head><body><button id="th">◐</button><div class="wrap">
<h1>台股脆弱度儀表板</h1><div class="sub">資料 FinMind + FRED · 更新於 __ASOF__ · 壓力計非擇時工具 · 非投資建議</div>
<div class="hero"><div class="gauge" id="gauge"><div class="inner"><div><div class="num" id="cnum">–</div><div class="lb">脆弱度 / 100</div></div></div></div>
 <div class="txt"><div class="big" id="cjudge">–</div>
 <div class="d">分數由下列燈號的歷史百分位加權合成。高≠馬上崩,而是「柴火堆高、系統脆弱」——事前降曝險用,不用來擇時。</div>
 <div class="vd" id="viewdate">滑鼠移過下方線圖 → 各燈號同步顯示當日數值</div>
 <div class="vd" id="rednow" style="color:var(--red);font-weight:650"></div></div></div>
<div class="trend"><div class="th"><span>個股搜尋 — 打代號或名稱看還原股價線圖(上市/上櫃/興櫃)</span></div>
 <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
  <input id="q" list="stocklist" placeholder="例:2330 或 台積電" style="font:inherit;background:var(--bg);color:var(--tp);border:1px solid var(--border);border-radius:8px;padding:7px 11px;min-width:220px">
  <datalist id="stocklist"></datalist>
  <button id="qbtn" style="font:inherit;background:var(--series-1);color:#fff;border:0;border-radius:8px;padding:7px 14px;cursor:pointer">查詢</button>
  <span id="qmsg" style="font-size:12px;color:var(--muted)"></span></div>
 <div id="scwrap" style="display:none;margin-top:10px"><div id="sctitle" style="font-size:14px;font-weight:650;margin-bottom:4px"></div>
  <div class="ctrl" id="scranges" style="margin:0 0 6px"><button data-d="244">1年</button><button data-d="732">3年</button><button data-d="1220">5年</button><button data-d="0" class="on">全部</button>
   <input type="date" id="sd0"><span style="color:var(--muted)">~</span><input type="date" id="sd1"></div>
  <div id="sc" style="position:relative"><svg id="scsvg"></svg><div id="sctip"></div></div>
  <div style="display:flex;justify-content:space-between;color:var(--muted);font-size:10.5px;margin-top:2px"><span id="scstart"></span><span>還原股價(對數刻度,已還原除權息/分割);滑鼠移過看當日收盤</span><span id="scend"></span></div>
  <div id="scsuit" style="font-size:12.5px;margin-top:8px;padding:8px 11px;background:var(--bg);border:1px solid var(--border);border-radius:9px"></div>
  <div id="scstats" class="scstats"></div>
  <div id="scmnote" style="font-size:10.5px;color:var(--muted);margin-top:4px"></div></div></div>
<div class="trend"><div class="th"><span>脆弱度歷史趨勢</span>
 <span class="ctrl" id="ranges"><button data-d="244">1年</button><button data-d="732">3年</button><button data-d="1220">5年</button><button data-d="0">全部</button>
 <input type="date" id="d0"><span style="color:var(--muted)">~</span><input type="date" id="d1"></span></div>
 <div id="tc"><svg id="tcsvg"></svg><div id="tctip"></div></div>
 <div style="display:flex;justify-content:space-between;color:var(--muted);font-size:10.5px;margin-top:4px">
 <span>滑鼠移過可看當日數值(各燈號同步)</span><span>紅帶色深=同時進紅區項數 6→10(越深越嚴重) · 灰帶=NBER衰退 · 橫線 75/55</span></div></div>
<h2 id="indh" style="margin-bottom:6px">產業子分析 — 依對該產業報酬的預測力重排燈號</h2>
<div id="indbar" class="indbar"></div>
<div class="indcap" id="indcap"></div>
<div class="gtitle" style="color:var(--series-1)">■ 內部因素 — 台股槓桿 / 估值 / 趨勢</div>
<div class="grid" id="grp-internal"></div>
<div class="gtitle" style="color:var(--warn)">■ 外部因素 — 外資 / 匯率 / 國際市場(美股・韓股・VIX)</div>
<div class="grid" id="grp-external"></div>
<h2>壓力測試 / 敏感度分析(融資追繳連鎖,示意性)</h2>
<table><thead><tr><th>情境</th><th>估計平均維持率</th><th>逼近斷頭比例</th><th>潛在追繳部位</th></tr></thead><tbody>__STRESS__</tbody></table>
<div class="note"><b>方法與限制:</b>各指標一律轉成「歷史百分位」再合成。融資背離採<b>去趨勢殘差</b>與<b>成長率背離(vs 指數)</b>雙軌——刻意<b>不</b>用「融資/指數」比率(指數同步噴高時會被分母污染)。壓力測試假設整體融資維持率約常態(均值160%、斷頭130%),僅為示意非精算。目前融資餘額約 __MARGIN__ 億。NBER 為美國景氣衰退期,因融資資料起於約2013年,範圍內僅涵蓋2020 COVID。<b>本頁為風險框架,非投資建議。</b></div>
</div>
<script>__APPJS__</script>
<script>document.getElementById('th').onclick=()=>{const r=document.documentElement;r.setAttribute('data-theme',r.getAttribute('data-theme')=='dark'?'light':'dark');if(window.__redraw)window.__redraw();};</script>
</body></html>"""

APP_JS=r"""
const D=__APPDATA__, REC=__REC__;
const N=D.dates.length, TS=D.dates.map(s=>Date.parse(s));
const REDN=8;   // 高壓叢集門檻:10項中至少這麼多項同進紅區
const DPOS={};for(let _i=0;_i<N;_i++)DPOS[D.dates[_i]]=_i;   // 日期→脆弱度索引(供個股區間對照)
const $=id=>document.getElementById(id);
let a=Math.max(0,N-732), b=N-1, sel=N-1, pinned=false, pinIdx=null;   // 視窗[a,b], sel=游標/固定index
const light=s=>s>=75?'red':s>=55?'warn':'ok';
function fmtVal(v,fmt){if(v==null||isNaN(v))return '–';let s=(+v).toFixed(fmt[1]);if(fmt[0]&&v>=0)s='+'+s;return s+fmt[2];}
// ---- gauge ----
function redCount(i){let c=0;for(const k of D.order){const dg=D.inds[k].dng[i];if(dg!=null&&dg>=75)c++;}return c;}
function gauge(i){const c=D.comp[i];$('gauge').style.setProperty('--v',c.toFixed(0));
 $('gauge').style.setProperty('--gc','var(--'+light(c)+')');$('cnum').textContent=c.toFixed(0);
 $('cjudge').textContent='綜合研判:'+(c>=75?'高危(柴火堆頂,宜降曝險)':c>=55?'偏高(留意去槓桿風險)':'中性偏低');
 const rc=redCount(i);$('rednow').textContent=(D.dates[i]===D.dates[b]?'目前 ':D.dates[i]+' ')+rc+'/10 指標在紅區(危險度≥75)'+(rc>=REDN?' ⚠ 高壓叢集(已標記於線圖)':'');}
// ---- cards ----
function cardHTML(k){const c=D.inds[k];
 return '<div class="card" id="cd-'+k+'"><div class="ct">'+c.label+'</div><div class="cv" id="cv-'+k+'">–</div>'
 +'<div class="cbarwrap"><div class="cbar" id="cb-'+k+'"></div></div><div class="cp" id="cp-'+k+'"></div>'
 +'<div class="cs" id="cs-'+k+'"></div><div class="cn">'+c.note+'</div>'
 +'<div class="cic" id="cic-'+k+'"></div></div>';}
function buildCards(){
 $('grp-internal').innerHTML=D.order.filter(k=>D.inds[k].group==='內部').map(cardHTML).join('');
 $('grp-external').innerHTML=D.order.filter(k=>D.inds[k].group==='外部').map(cardHTML).join('');}
// ---- 產業預測力(rank-IC) ----
let curInd=(D.indorder&&D.indorder.length)?(D.indorder.includes('全市場')?'全市場':D.indorder[0]):null;
function rankify(arr){const idx=arr.map((v,i)=>[v,i]).sort((p,q)=>p[0]-q[0]);const r=new Array(arr.length);
 let i=0;while(i<idx.length){let j=i;while(j+1<idx.length&&idx[j+1][0]===idx[i][0])j++;const avg=(i+j)/2+1;for(let k=i;k<=j;k++)r[idx[k][1]]=avg;i=j+1;}return r;}
function pearson(x,y){const n=x.length;if(n<10)return null;let mx=0,my=0;for(let i=0;i<n;i++){mx+=x[i];my+=y[i];}mx/=n;my/=n;
 let sxy=0,sx=0,sy=0;for(let i=0;i<n;i++){const dx=x[i]-mx,dy=y[i]-my;sxy+=dx*dy;sx+=dx*dx;sy+=dy*dy;}return (sx>0&&sy>0)?sxy/Math.sqrt(sx*sy):null;}
function computeIC(ind){const lv=D.industries[ind],H=D.H||20,res={};if(!lv)return res;const hi=Math.min(b,N-1-H);
 for(const k of D.order){const val=D.inds[k].val,xs=[],ys=[];
  for(let t=a;t<=hi;t++){const v=val[t],p0=lv[t],p1=lv[t+H];if(v==null||p0==null||p1==null||p0<=0)continue;xs.push(v);ys.push(p1/p0-1);}
  res[k]=(xs.length>=15)?pearson(rankify(xs),rankify(ys)):null;}
 return res;}
function buildIndBar(){const bar=$('indbar');if(!D.indorder||!D.indorder.length){bar.style.display='none';$('indh').style.display='none';return;}
 bar.innerHTML=D.indorder.map(n=>'<button data-ind="'+n+'"'+(n===curInd?' class="on"':'')+'>'+n+'</button>').join('');
 bar.querySelectorAll('button').forEach(btn=>btn.onclick=()=>{curInd=btn.dataset.ind;
  bar.querySelectorAll('button').forEach(x=>x.classList.toggle('on',x.dataset.ind===curInd));applyRanking();});}
function applyRanking(){if(!curInd)return;const ic=computeIC(curInd);
 ['內部','外部'].forEach(gname=>{const keys=D.order.filter(k=>D.inds[k].group===gname);
  const ranked=keys.filter(k=>ic[k]!=null).sort((p,q)=>Math.abs(ic[q])-Math.abs(ic[p]));
  const rest=keys.filter(k=>ic[k]==null);
  [...ranked,...rest].forEach((k,i)=>{const cd=$('cd-'+k);if(cd)cd.style.order=i;const v=ic[k];const el=$('cic-'+k);if(!el)return;
   el.innerHTML=(v==null)?'<span style="color:var(--muted)">對'+curInd+'報酬 IC —</span>'
    :'對'+curInd+'報酬 IC <b style="color:'+(Math.abs(v)>=0.2?'var(--series-1)':'var(--ts)')+'">'+(v>=0?'+':'')+v.toFixed(2)+'</b>'+(i<ranked.length?' · 組內第'+(i+1)+'名':'');});});
 $('indcap').textContent='依「'+curInd+'」未來'+(D.H||20)+'日報酬的預測力(Spearman rank-IC;|IC|大=預測力強;各組內排序;區間 '+D.dates[a]+' ~ '+D.dates[b]+')';}
function cardSpark(k){const v=D.inds[k].val.slice(a,b+1).filter(x=>x!=null);if(v.length<3)return '';
 const lo=Math.min(...v),hi=Math.max(...v),w=150,h=30;
 let p='';for(let i=0;i<v.length;i++)p+=(i?'L':'M')+(i/(v.length-1)*w).toFixed(1)+' '+(h-2-(v[i]-lo)/((hi-lo)||1)*(h-4)).toFixed(1)+' ';
 return '<svg viewBox="0 0 '+w+' '+h+'" width="'+w+'" height="'+h+'" preserveAspectRatio="none"><path d="'+p+'" fill="none" stroke="var(--series-1)" stroke-width="1.2"/></svg>';}
function renderCards(i){for(const k of D.order){const c=D.inds[k],dg=c.dng[i]==null?0:c.dng[i];
 $('cv-'+k).textContent=fmtVal(c.val[i],c.fmt);$('cb-'+k).style.width=dg+'%';
 $('cp-'+k).textContent='危險度 '+dg+'/100';$('cd-'+k).className='card '+light(dg);
 $('cs-'+k).innerHTML=cardSpark(k);}}
// ---- trend chart ----
const svg=$('tcsvg'),box=$('tc'),tip=$('tctip');const H=180,padL=30,padR=12,padT=12,padB=22;
let W=800,plotW=0,plotH=0,curA=0,curB=0;
const X=i=>padL+((curB-curA)<1?0:(i-curA)/(curB-curA)*plotW), Y=v=>padT+(1-v/100)*plotH;
function idxForTs(t){let lo=0,hi=N-1;if(t<=TS[0])return 0;if(t>=TS[hi])return hi;
 while(lo<hi){const m=(lo+hi)>>1;if(TS[m]<t)lo=m+1;else hi=m;}return (lo>0&&Math.abs(TS[lo-1]-t)<Math.abs(TS[lo]-t))?lo-1:lo;}
function renderTrend(){curA=a;curB=b;W=box.clientWidth||800;plotW=W-padL-padR;plotH=H-padT-padB;
 svg.setAttribute('width',W);svg.setAttribute('height',H);svg.setAttribute('viewBox','0 0 '+W+' '+H);
 let g='';
 g+='<rect x="'+padL+'" y="'+Y(100)+'" width="'+plotW+'" height="'+(Y(75)-Y(100))+'" fill="var(--red)" opacity="0.08"/>';
 g+='<rect x="'+padL+'" y="'+Y(75)+'" width="'+plotW+'" height="'+(Y(55)-Y(75))+'" fill="var(--warn)" opacity="0.10"/>';
 REC.forEach(r=>{const s=Date.parse(r[0]),e=Date.parse(r[1]);if(e<TS[curA]||s>TS[curB])return;
  const xs=X(idxForTs(Math.max(s,TS[curA]))),xe=X(idxForTs(Math.min(e,TS[curB])));
  g+='<rect x="'+xs+'" y="'+padT+'" width="'+Math.max(2,xe-xs)+'" height="'+plotH+'" fill="var(--muted)" opacity="0.30"/>';
  g+='<text x="'+((xs+xe)/2)+'" y="'+(padT+10)+'" font-size="9" fill="var(--ts)" text-anchor="middle">NBER衰退</text>';});
 // 同時進紅區項數光譜(6→10, 色深遞增)
 const cw=plotW/Math.max(1,curB-curA);
 for(let i=curA;i<=curB;i++){const c=redCount(i);if(c>=6){const op=(0.06+(c-5)*0.085).toFixed(3);
   g+='<rect x="'+(X(i)-cw/2).toFixed(1)+'" y="'+padT+'" width="'+Math.max(1,cw+0.6).toFixed(1)+'" height="'+plotH+'" fill="var(--red)" opacity="'+op+'"/>';}}
 [75,55].forEach(v=>{g+='<line x1="'+padL+'" y1="'+Y(v)+'" x2="'+(W-padR)+'" y2="'+Y(v)+'" stroke="var(--border)" stroke-dasharray="3 3"/>'
  +'<text x="'+(padL-4)+'" y="'+(Y(v)+3)+'" font-size="9" fill="var(--muted)" text-anchor="end">'+v+'</text>';});
 let p='';for(let i=curA;i<=curB;i++)p+=(i==curA?'M':'L')+X(i).toFixed(1)+' '+Y(D.comp[i]).toFixed(1)+' ';
 g+='<path d="'+p+'" fill="none" stroke="var(--series-1)" stroke-width="1.5"/>';
 let lastY=null;for(let i=curA;i<=curB;i++){const yr=D.dates[i].slice(0,4);if(yr!==lastY){lastY=yr;
  const span=curB-curA;if(span>1600?(+yr%2==0):(span>500?true:false)||span<=500)
   g+='<text x="'+X(i)+'" y="'+(H-6)+'" font-size="9" fill="var(--muted)" text-anchor="middle">'+(span>500?yr:D.dates[i].slice(0,7))+'</text>';}}
 g+='<line id="tcx" y1="'+padT+'" y2="'+(padT+plotH)+'" stroke="var(--tp)" opacity="0"/><circle id="tcd" r="3.5" fill="var(--series-1)" opacity="0"/>';
 svg.innerHTML=g;
 if(pinned&&pinIdx>=curA&&pinIdx<=curB)showAt(pinIdx);}
function idxAt(ev){const rect=svg.getBoundingClientRect();const mx=(ev.touches?ev.touches[0].clientX:ev.clientX)-rect.left;
 let i=curA+Math.round((mx-padL)/plotW*(curB-curA));return Math.max(curA,Math.min(curB,i));}
function showAt(i){sel=i;const x=X(i),y=Y(D.comp[i]);
 $('tcx').setAttribute('x1',x);$('tcx').setAttribute('x2',x);$('tcx').setAttribute('opacity',pinned?'0.6':'0.35');
 $('tcd').setAttribute('cx',x);$('tcd').setAttribute('cy',y);$('tcd').setAttribute('opacity','1');
 const rc=redCount(i);tip.style.opacity='1';
 tip.innerHTML=(pinned?'📌 ':'')+'<b>'+D.dates[i]+'</b><br>脆弱度 '+D.comp[i]+'<br>紅區 '+rc+'/10'+(rc>=REDN?' ⚠':'');
 let tx=x+12;if(tx>W-104)tx=x-104;tip.style.left=Math.max(0,tx)+'px';tip.style.top='4px';
 $('viewdate').textContent=(pinned?'📌 已固定於 '+D.dates[i]+'(再點線圖換位置,點兩下取消)':'檢視 '+D.dates[i]+' — 下方各燈號同步(點一下可固定)');
 gauge(i);renderCards(i);}
function move(ev){if(pinned)return;showAt(idxAt(ev));}
function leave(){if(pinned){showAt(pinIdx);return;}
 $('tcx').setAttribute('opacity','0');$('tcd').setAttribute('opacity','0');tip.style.opacity='0';
 sel=b;$('viewdate').textContent='最新('+D.dates[b]+')— 點一下線圖可固定數值';gauge(b);renderCards(b);}
function unpin(){pinned=false;pinIdx=null;$('tcx').setAttribute('opacity','0');$('tcd').setAttribute('opacity','0');tip.style.opacity='0';
 sel=b;$('viewdate').textContent='最新('+D.dates[b]+')— 點一下線圖可固定數值';gauge(b);renderCards(b);}
function onClick(ev){const i=idxAt(ev);if(pinned&&Math.abs(i-pinIdx)<=1){unpin();}else{pinned=true;pinIdx=i;showAt(i);}}
function setRange(days){a=days<=0?0:Math.max(0,N-days);b=N-1;sel=b;syncInputs();redraw();}
function syncInputs(){$('d0').value=D.dates[a];$('d1').value=D.dates[b];}
function redraw(){renderTrend();gauge(sel);renderCards(sel);$('viewdate').textContent='最新('+D.dates[b]+')';
 [...document.querySelectorAll('#ranges button')].forEach(x=>x.classList.remove('on'));applyRanking();}
window.__redraw=redraw;
// events
document.querySelectorAll('#ranges button').forEach(btn=>btn.onclick=()=>{setRange(+btn.dataset.d);btn.classList.add('on');});
function fromInputs(){const t0=Date.parse($('d0').value),t1=Date.parse($('d1').value);
 if(isNaN(t0)||isNaN(t1)||t0>=t1)return;a=idxForTs(t0);b=idxForTs(t1);sel=b;redraw();}
$('d0').onchange=fromInputs;$('d1').onchange=fromInputs;
svg.addEventListener('mousemove',move);svg.addEventListener('mouseleave',leave);
svg.addEventListener('click',onClick);svg.addEventListener('dblclick',unpin);
svg.addEventListener('touchmove',e=>move(e),{passive:true});
let rt;window.addEventListener('resize',()=>{clearTimeout(rt);rt=setTimeout(renderTrend,150);});
// ---- 個股搜尋(即時抓 FinMind) ----
const FM_TOKEN=new URLSearchParams(location.search).get('token')||'';
const STOCKS=D.stocks||{};
function buildDatalist(){const dl=$('stocklist');if(!dl)return;
 dl.innerHTML=Object.keys(STOCKS).map(id=>'<option value="'+id+' '+STOCKS[id][0]+'">').join('');}
async function fmFetch(ds,id){const p=new URLSearchParams({dataset:ds,data_id:id,start_date:'2005-01-01',end_date:'2030-12-31'});
 if(FM_TOKEN)p.set('token',FM_TOKEN);const r=await fetch('https://api.finmindtrade.com/api/v4/data?'+p);
 if(!r.ok)throw new Error('HTTP '+r.status+(r.status==402?'(速率上限,稍後或加 ?token=)':''));return (await r.json()).data||[];}
function adjClose(rows){rows=rows.filter(r=>+r.close>0).sort((a,b)=>a.date<b.date?-1:1);
 if(rows.length<2)return null;const dates=rows.map(r=>r.date),raw=rows.map(r=>+r.close),vol=rows.map(r=>+r.Trading_Volume||0);
 let f=raw[0];const adj=[raw[0]];for(let i=1;i<raw.length;i++){let lr=Math.log(raw[i]/raw[i-1]);if(Math.abs(lr)>0.13)lr=0;f*=Math.exp(lr);adj.push(f);}
 return {dates,raw,adj,vol};}
// 小工具
function ols3(X,y){const k=X[0].length,A=Array.from({length:k},()=>Array(k).fill(0)),bb=Array(k).fill(0);
 for(let i=0;i<X.length;i++){for(let a=0;a<k;a++){bb[a]+=X[i][a]*y[i];for(let c=0;c<k;c++)A[a][c]+=X[i][a]*X[i][c];}}
 for(let c=0;c<k;c++){let pv=c;for(let r=c+1;r<k;r++)if(Math.abs(A[r][c])>Math.abs(A[pv][c]))pv=r;[A[c],A[pv]]=[A[pv],A[c]];[bb[c],bb[pv]]=[bb[pv],bb[c]];
  for(let r=0;r<k;r++)if(r!=c){const fr=A[r][c]/A[c][c];for(let cc=c;cc<k;cc++)A[r][cc]-=fr*A[c][cc];bb[r]-=fr*bb[c];}}
 return bb.map((v,i)=>v/A[i][i]);}
function pctile(arr,v){const s=arr.filter(x=>x!=null&&!isNaN(x));return s.length?s.filter(x=>x<v).length/s.length*100:null;}
function stLight(p){return p==null?'':p>=75?'red':p>=55?'warn':'ok';}
async function renderStockMargin(code){
 const box=$('scstats');box.innerHTML='';$('scmnote').textContent='';
 let rows;try{rows=await fmFetch('TaiwanStockMarginPurchaseShortSale',code);}catch(e){return;}
 if(!rows||!rows.length){$('scmnote').textContent='(此檔無融資融券資料)';return;}
 // 對齊到股價日期(SD.dates)
 const mm=new Map(),lm=new Map(),sm=new Map();
 rows.forEach(r=>{mm.set(r.date,+r.MarginPurchaseTodayBalance);lm.set(r.date,+r.MarginPurchaseLimit);sm.set(r.date,+r.ShortSaleTodayBalance);});
 const D0=SD.dates;let last=null;const bal=D0.map(d=>{if(mm.has(d))last=mm.get(d);return last;});
 let lim=null;const limit=D0.map(d=>{if(lm.has(d))lim=lm.get(d);return lim;});
 let ls=null;const sbal=D0.map(d=>{if(sm.has(d))ls=sm.get(d);return ls;});
 const adj=SD.adj,vol=SD.vol,n=D0.length;
 const pc=(a,k,i)=>(i>=k&&a[i-k]>0&&a[i]!=null&&a[i-k]!=null)?(a[i]/a[i-k]-1)*100:null;
 // 半年擴張 & YoY背離 序列(供百分位)
 const roc=[],ydiv=[];
 for(let i=0;i<n;i++){roc.push(pc(bal,126,i));const my=pc(bal,244,i),py=pc(adj,244,i);ydiv.push((my!=null&&py!=null)?my-py:null);}
 // 融資超額水位:log(bal)~log(adj)+log(volMA20) 殘差z(需足夠歷史)
 let residZ=null,residSeries=null;
 const volma=vol.map((_,i)=>i<20?null:vol.slice(i-19,i+1).reduce((x,y)=>x+y,0)/20);
 const Xr=[],Yr=[],idxr=[];
 for(let i=0;i<n;i++){if(bal[i]>0&&adj[i]>0&&volma[i]>0){Xr.push([Math.log(adj[i]),Math.log(volma[i]),1]);Yr.push(Math.log(bal[i]));idxr.push(i);}}
 if(Xr.length>200){const bt=ols3(Xr,Yr);const res=Yr.map((y,j)=>y-(Xr[j][0]*bt[0]+Xr[j][1]*bt[1]+bt[2]));
  const mu=res.reduce((a,b)=>a+b,0)/res.length,sd=Math.sqrt(res.reduce((a,b)=>a+(b-mu)*(b-mu),0)/res.length);
  residSeries=res.map(r=>(r-mu)/sd);residZ=residSeries[residSeries.length-1];}
 const L=n-1;
 const usage=(bal[L]!=null&&limit[L]>0)?bal[L]/limit[L]*100:null;
 const shortR=(bal[L]>0&&sbal[L]!=null)?sbal[L]/bal[L]*100:null;
 function tile(label,val,sub,p){const lt=stLight(p);
  return '<div class="stile '+lt+'"><div class="l">'+label+'</div><div class="v">'+val+'</div><div class="s">'+(sub||'')+'</div></div>';}
 const f1=x=>x==null?'–':(x>=0?'+':'')+x.toFixed(1);
 let html='';
 html+=tile('融資餘額(張)',bal[L]!=null?bal[L].toLocaleString():'–','半年擴張 '+f1(roc[L])+'%',pctile(roc,roc[L]));
 html+=tile('融資成長背離',f1(ydiv[L])+'pp','融資YoY−股價YoY',pctile(ydiv,ydiv[L]));
 html+=tile('融資超額水位',residZ==null?'資料不足':f1(residZ)+'σ','去趨勢殘差',residSeries?pctile(residSeries,residZ):null);
 html+=tile('融資使用率',usage==null?'–':usage.toFixed(0)+'%','餘額/限額',usage);
 html+=tile('券資比',shortR==null?'–':shortR.toFixed(0)+'%','融券/融資',null);
 box.innerHTML=html;
 $('scmnote').textContent='上列為「'+code+'」個股自身融資指標(危險度=該股歷史百分位);融資單位為張。';
}
let SD=null,SA=0,SB=0;   // 個股資料 + 顯示區間[SA,SB]
function resolveCode(q){q=q.trim();if(!q)return null;const code=q.split(/\s+/)[0];
 if(/^\d{4,6}[A-Z]?$/.test(code)&&STOCKS[code])return code;if(STOCKS[code])return code;
 for(const id in STOCKS)if((id+' '+STOCKS[id][0]).includes(q))return id;return /^\d{4,6}[A-Z]?$/.test(code)?code:null;}
async function doSearch(){const q=$('q').value;const code=resolveCode(q);
 if(!code){$('qmsg').textContent='查無此代號/名稱';return;}
 const nm=STOCKS[code]?STOCKS[code][0]:'',ty=STOCKS[code]?({twse:'上市',tpex:'上櫃',emerging:'興櫃'}[STOCKS[code][1]]||STOCKS[code][1]):'';
 $('qmsg').textContent='抓取中…';
 try{const rows=await fmFetch('TaiwanStockPrice',code);
  if(!rows.length){$('qmsg').textContent='查無此檔價格資料(興櫃部分冷門股可能無資料)';$('scwrap').style.display='none';return;}
  SD=adjClose(rows);SD.name=code+' '+nm+(ty?' · '+ty:'');
  $('sctitle').textContent=SD.name;$('scwrap').style.display='';$('qmsg').textContent='';
  SA=0;SB=SD.dates.length-1;$('sd0').value=SD.dates[SA];$('sd1').value=SD.dates[SB];
  document.querySelectorAll('#scranges button').forEach(x=>x.classList.toggle('on',x.dataset.d==='0'));
  drawStock();renderStockMargin(code);
 }catch(e){let m=e.message;
   if(location.protocol==='file:')m='此頁是用「檔案(file://)」開啟,瀏覽器會擋住向 FinMind 的跨站抓取。請改用網址開啟(上線到 GitHub Pages/Netlify),或在此資料夾執行 python -m http.server 後開 http://localhost:8000/';
   else if(/fetch/i.test(m))m='抓取失敗——可能是網路、廣告封鎖擴充套件擋了 api.finmindtrade.com,或速率上限(可加 ?token=)';
   $('qmsg').textContent='⚠ '+m;}}
function drawStock(){if(!SD)return;const svg=$('scsvg'),wrap=$('sc'),tip=$('sctip');
 const W=wrap.clientWidth||800,H=210,pl=46,pr=12,pt=12,pb=22,plotW=W-pl-pr,plotH=H-pt-pb;
 const ly=SD.adj.map(x=>Math.log10(x));const win=ly.slice(SA,SB+1);const lo=Math.min(...win),hi=Math.max(...win);
 const span=SB-SA;const X=i=>pl+(span<1?0:(i-SA)/span*plotW),Y=v=>pt+(hi-v)/((hi-lo)||1)*plotH;
 svg.setAttribute('width',W);svg.setAttribute('height',H);svg.setAttribute('viewBox','0 0 '+W+' '+H);
 let g='';[0,0.25,0.5,0.75,1].forEach(t=>{const lv=lo+(hi-lo)*t,pv=Math.pow(10,lv);
  g+='<line x1="'+pl+'" y1="'+Y(lv)+'" x2="'+(W-pr)+'" y2="'+Y(lv)+'" stroke="var(--border)" stroke-dasharray="3 3"/>'
   +'<text x="'+(pl-4)+'" y="'+(Y(lv)+3)+'" font-size="9" fill="var(--muted)" text-anchor="end">'+(pv>=100?pv.toFixed(0):pv.toFixed(1))+'</text>';});
 const showMonth=span<=500;let lastL=null;for(let i=SA;i<=SB;i++){const lab=showMonth?SD.dates[i].slice(0,7):SD.dates[i].slice(0,4);
  if(lab!==lastL){lastL=lab;if(showMonth||(+lab%(span>2600?3:1)==0))g+='<text x="'+X(i)+'" y="'+(H-6)+'" font-size="9" fill="var(--muted)" text-anchor="middle">'+lab+'</text>';}}
 let p='';for(let i=SA;i<=SB;i++)p+=(i==SA?'M':'L')+X(i).toFixed(1)+' '+Y(ly[i]).toFixed(1)+' ';
 g+='<path d="'+p+'" fill="none" stroke="var(--series-1)" stroke-width="1.4"/>';
 g+='<line id="scx" y1="'+pt+'" y2="'+(pt+plotH)+'" stroke="var(--tp)" opacity="0"/><circle id="scd" r="3.2" fill="var(--series-1)" opacity="0"/>';
 svg.innerHTML=g;
 $('scstart').textContent=SD.dates[SA];$('scend').textContent=SD.dates[SB];
 svg.onmousemove=ev=>{const r=svg.getBoundingClientRect();let i=SA+Math.round(((ev.clientX-r.left)-pl)/plotW*span);i=Math.max(SA,Math.min(SB,i));
  const x=X(i),y=Y(ly[i]);$('scx').setAttribute('x1',x);$('scx').setAttribute('x2',x);$('scx').setAttribute('opacity','0.35');
  $('scd').setAttribute('cx',x);$('scd').setAttribute('cy',y);$('scd').setAttribute('opacity','1');
  tip.style.opacity='1';tip.innerHTML='<b>'+SD.dates[i]+'</b><br>收盤 '+SD.raw[i];
  let tx=x+12;if(tx>W-92)tx=x-92;tip.style.left=Math.max(0,tx)+'px';tip.style.top='4px';};
 svg.onmouseleave=()=>{$('scx').setAttribute('opacity','0');$('scd').setAttribute('opacity','0');tip.style.opacity='0';};
 updateSuit();}
function updateSuit(){const el=$('scsuit');if(!el||!SD)return;
 let cs=[],r8=0,tot=0;
 for(let i=SA;i<=SB;i++){const di=DPOS[SD.dates[i]];if(di==null)continue;tot++;cs.push(D.comp[di]);if(redCount(di)>=REDN)r8++;}
 if(!tot){el.innerHTML='<span style="color:var(--muted)">此區間早於脆弱度資料(約2013前),無市場脆弱度可對照</span>';return;}
 const avg=cs.reduce((a,b)=>a+b,0)/cs.length,mx=Math.max(...cs),p8=r8/tot*100;
 let v,col;
 if(avg>=70||p8>=25){v='不宜融資持有(系統高壓)';col='var(--red)';}
 else if(avg>=55||p8>=8){v='融資需謹慎';col='var(--warn)';}
 else{v='系統壓力低,相對適合';col='var(--ok)';}
 el.innerHTML='📊 此區間市場脆弱度 平均 <b>'+avg.toFixed(0)+'</b> / 最高 '+mx.toFixed(0)+' · ≥'+REDN+'項紅區天數占 <b>'+p8.toFixed(0)+'%</b> → 融資持有研判:<b style="color:'+col+'">'+v+'</b> <span style="color:var(--muted)">(此為風險框架,非投資建議;可改上方區間比較不同時期)</span>';}
function scIdxForDate(ds){const t=Date.parse(ds);let lo=0,hi=SD.dates.length-1;const T=SD.dates.map(Date.parse);
 if(t<=T[0])return 0;if(t>=T[hi])return hi;while(lo<hi){const m=(lo+hi)>>1;if(T[m]<t)lo=m+1;else hi=m;}return lo;}
function setupSearch(){buildDatalist();$('qbtn').onclick=doSearch;
 $('q').addEventListener('keydown',e=>{if(e.key==='Enter')doSearch();});
 document.querySelectorAll('#scranges button').forEach(btn=>btn.onclick=()=>{if(!SD)return;const d=+btn.dataset.d;
  SA=d<=0?0:Math.max(0,SD.dates.length-1-d);SB=SD.dates.length-1;
  document.querySelectorAll('#scranges button').forEach(x=>x.classList.toggle('on',x===btn));
  $('sd0').value=SD.dates[SA];$('sd1').value=SD.dates[SB];drawStock();});
 function fromSD(){if(!SD)return;const t0=Date.parse($('sd0').value),t1=Date.parse($('sd1').value);if(isNaN(t0)||isNaN(t1)||t0>=t1)return;
  SA=scIdxForDate($('sd0').value);SB=scIdxForDate($('sd1').value);
  document.querySelectorAll('#scranges button').forEach(x=>x.classList.remove('on'));drawStock();}
 $('sd0').onchange=fromSD;$('sd1').onchange=fromSD;
 let st;window.addEventListener('resize',()=>{clearTimeout(st);st=setTimeout(()=>{if(SD)drawStock();},150);});}
// init
buildCards();buildIndBar();setupSearch();syncInputs();renderTrend();gauge(b);renderCards(b);applyRanking();
"""

def main():
    d=get_data()
    if "margin" not in d or "idx" not in d:
        print("資料抓取失敗(可能遇速率限制),稍後重試"); sys.exit(1)
    R=compute(d)
    app=build_app_data(d,R)
    # 產業子分析:嵌入各產業等權報酬序列(對齊到儀表板日期)
    app["industries"]={}; app["indorder"]=[]; app["H"]=20
    try:
        ir=json.load(open("industry_returns.json"))
        pos={dt:i for i,dt in enumerate(ir["dates"])}
        def alg(lv):
            return [round(lv[pos[dt]],1) if (dt in pos and lv[pos[dt]] is not None) else None for dt in app["dates"]]
        app["industries"]={n:alg(v) for n,v in ir["industries"].items()}
        app["indorder"]=(["全市場"] if "全市場" in app["industries"] else [])+sorted(k for k in app["industries"] if k!="全市場")
        print(f"   已嵌入 {len(app['industries'])} 個產業報酬序列")
    except Exception as e:
        print("   (無 industry_returns.json,略過產業子分析)")
    # 全市場代號名錄(含上市/上櫃/興櫃)供搜尋
    try:
        info=fm("TaiwanStockInfo")
        st={}
        for _,row in info.drop_duplicates("stock_id").iterrows():
            sid=str(row["stock_id"])
            st[sid]=[row["stock_name"], row["type"]]
        app["stocks"]=st
        print(f"   已嵌入 {len(st)} 檔代號名錄(供搜尋)")
    except Exception:
        app["stocks"]={}
    comp_now=app["comp"][-1]
    stress_cur,stress_rows=stress_test(d)
    asof=app["dates"][-1]
    open(OUT_HTML,"w").write(build_html(app,comp_now,asof,stress_cur,stress_rows))
    row={"date":asof,"composite":round(comp_now,1)}
    for k in R: row[k]=round(R[k]["val"],3)
    hist=pd.DataFrame([row])
    if os.path.exists(HIST_CSV):
        old=pd.read_csv(HIST_CSV); hist=pd.concat([old[old["date"]!=asof],hist],ignore_index=True)
    hist.to_csv(HIST_CSV,index=False)
    print(f"OK  {asof}  脆弱度={comp_now:.0f}/100  → {OUT_HTML}")
    for k in ORDER:
        if k in R: print(f"   {R[k]['label']:16} {R[k]['val']:+.2f}{R[k]['unit']}")

if __name__=="__main__":
    main()
