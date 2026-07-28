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
# ── 雙層架構(2026-07 依回測改版)──────────────────────────────────
# 慢層 SLOW:結構脆弱度 = 「萬一出事會多慘」→ 只決定槓桿上限, 不當出場訊號。
# 快層 TRIGGER:「已經開始了」→ 真正踩剎車的觸發(VIX跳升 / 跌破年線)。
# 動能 MOMO:乖離類。回測顯示其危險度與未來回撤『方向相反』(韓股IC−0.139、
#            美股−0.076、距年線−0.051):漲多不代表要跌,動能會延續。
#            故移出計分, 僅作情境參考, 避免誤殺仍在噴出的飆股。
SLOW={"margin_resid_z","margin_yoy_div","margin_roc","vix_level","foreign_flow","fx_pressure"}
TRIGGER={"vix_spike"}                   # 另有「跌破年線」由 trend_health 值判定
MOMO={"trend_health","us_nasdaq","kr_bubble"}
GROUP={**{k:"內部槓桿" for k in ("margin_resid_z","margin_yoy_div","margin_roc")},
       **{k:"外部資金情緒" for k in ("vix_level","foreign_flow","fx_pressure")},
       **{k:"觸發層" for k in TRIGGER},
       **{k:"動能參考" for k in MOMO}}
TRIG_PCT=85                             # VIX跳升危險度達此百分位即視為觸發
LEV_CAP={"high":1.0,"mid":1.2,"low":1.5}   # 結構脆弱度 →槓桿上限
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

def fetch_gdp_yoy(cap_year):
    """台灣名目GDP年增率(IMF WEO via DBnomics,年頻)。僅取 <= cap_year 的『已實現』年份,
    排除 WEO 對未來年份的預測,避免前視偏誤。回傳 {year:int -> yoy%} 或 None(抓取失敗)。"""
    try:
        u="https://api.db.nomics.world/v22/series/IMF/WEO:latest/TWN.NGDP?observations=1"
        j=requests.get(u,timeout=30).json(); s=j["series"]["docs"][0]
        lvl={int(p):v for p,v in zip(s["period"],s["value"]) if v is not None}
        yoy={y:(lvl[y]/lvl[y-1]-1)*100 for y in lvl if (y-1) in lvl and lvl[y-1]>0 and y<=cap_year}
        return yoy or None
    except Exception:
        return None

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
    if "idx" in d and len(d["idx"]):
        d["asof_year"]=int(d["idx"].index[-1].year)
        d["gdp_yoy"]=fetch_gdp_yoy(d["asof_year"]-1)   # 名目GDP年增率(已實現年份,PIT)
    return d

def expanding_resid_z(y, cols, min_obs=252):
    """擴張視窗迴歸 y ~ cols + 截距 的『當期殘差 z』。
    第 t 期的迴歸係數、殘差平均與標準差,全部只用第 0..t 期資料 → 無前視偏誤
    (取代全樣本一次迴歸:後者會把未來資訊洩漏進過去的殘差)。以累積交叉乘積遞推,O(n)。"""
    n=len(y); k=len(cols)+1; out=np.full(n,np.nan)
    A=np.zeros((k,k)); bvec=np.zeros(k)
    rs=0.0; rss=0.0; rn=0
    for i in range(n):
        xi=np.array([c[i] for c in cols]+[1.0])
        A+=np.outer(xi,xi); bvec+=xi*y[i]
        if i+1<min_obs: continue
        try: beta=np.linalg.solve(A+np.eye(k)*1e-8, bvec)
        except np.linalg.LinAlgError: continue
        r=y[i]-xi@beta
        rs+=r; rss+=r*r; rn+=1
        if rn>30:
            mu=rs/rn; var=max(rss/rn-mu*mu,1e-12)
            out[i]=(r-mu)/math.sqrt(var)
    return out

def compute(d):
    R={}; idx=d.get("idx"); margin=d.get("margin"); turn=d.get("turn")
    base=pd.DataFrame({"idx":idx,"margin":margin,"turn":turn}).dropna()
    if len(base)>300:
        b=base.copy(); b["tma"]=b["turn"].rolling(60).mean()
        b=b.dropna(subset=["tma"])                      # 不用 bfill(回填=用到未來資料)
        zr=expanding_resid_z(np.log(b["margin"].values),
                             [np.log(b["idx"].values), np.log(b["tma"].values)], min_obs=252)
        zr=pd.Series(zr,index=b.index).dropna()
        if len(zr)>200:
            R["margin_resid_z"]=dict(val=float(zr.iloc[-1]),series=zr,unit="σ",label="融資超額水位",
                note="去趨勢後融資多出幾個σ")
    if len(base)>260:
        m_yoy=base["margin"].pct_change(244)*100
        gdp=d.get("gdp_yoy")
        if gdp:                                          # 以名目GDP為錨(去除指數分母污染, PIT 落後一年)
            gyears=sorted(gdp)
            vals=[]
            for y in base.index.year:
                ks=[k for k in gyears if k<=y-1]         # 只用到該日年份前一年的已實現GDP
                vals.append(gdp[ks[-1]] if ks else None)
            gser=pd.Series(vals,index=base.index).astype(float).ffill().bfill()
            div=m_yoy-gser
            lbl="融資成長背離(vs名目GDP)"
            note="融資年增 − 名目GDP年增;正=槓桿跑贏實體"
        else:                                            # 退回:GDP源不可得時用指數
            div=m_yoy-(base["idx"].pct_change(244)*100)
            lbl="融資成長背離(vs指數)"
            note="融資年增 − 指數年增(GDP不可得時)"
        R["margin_yoy_div"]=dict(val=float(div.iloc[-1]),series=div,unit="pp",label=lbl,note=note)
    if len(base)>150:
        roc=base["margin"].pct_change(126)*100
        R["margin_roc"]=dict(val=float(roc.iloc[-1]),series=roc,unit="%",label="融資半年擴張",
            note="融資餘額近半年變化")
    vix=d.get("vix")
    if len(vix):
        R["vix_level"]=dict(val=float(vix.iloc[-1]),series=vix,unit="",label="VIX 波動度",
            note="低=自滿(反向計分)")
        R["vix_spike"]=dict(val=float(vix.iloc[-1]-vix.iloc[-6]) if len(vix)>6 else 0.0,
            series=vix.diff(5),unit="",label="VIX 5日跳升",note="正跳升=避險轉向")
    fo=d.get("foreign")
    if len(fo):
        roll=fo.rolling(20).sum()
        R["foreign_flow"]=dict(val=float(roll.iloc[-1]),series=roll,unit="億",label="外資20日淨流向",
            note="賣超=資金撤離")
    fx=d.get("usdtwd")
    if len(fx):
        chg=(fx/fx.shift(20)-1)*100
        R["fx_pressure"]=dict(val=float(chg.iloc[-1]),series=chg,unit="%",label="台幣20日貶值",
            note="走貶=資金外流")
    if idx is not None and len(idx)>240:
        dev=(idx/idx.rolling(240).mean()-1)*100
        R["trend_health"]=dict(val=float(dev.iloc[-1]),series=dev,unit="%",label="指數距年線",
            note="距年線乖離(不計分)")
    nq=d.get("nasdaq")
    if nq is not None and len(nq)>200:
        dev=(nq/nq.rolling(200).mean()-1)*100
        R["us_nasdaq"]=dict(val=float(dev.iloc[-1]),series=dev,unit="%",label="美股乖離(Nasdaq)",
            note="Nasdaq 距200日均(不計分)")
    kr=d.get("kospi")
    if kr is not None and len(kr)>12:
        dev=(kr/kr.rolling(12).mean()-1)*100
        R["kr_bubble"]=dict(val=float(dev.iloc[-1]),series=dev,unit="%",label="韓股乖離(KOSPI)",
            note="KOSPI 距12月均·月頻(不計分)")
    return R

PIT_WARMUP=252   # 擴張百分位暖身期(需至少一年歷史才開始評分)

def pit_pct(vals, invert=False, min_periods=PIT_WARMUP):
    """PIT 擴張百分位:第 t 日的危險度只用「第 t 日及以前」的分佈計算,絕不偷看未來。
    取代全樣本 rank(pct=True) —— 徹底消除前視偏誤。"""
    import bisect
    out=[]; hist=[]
    for v in vals:
        if v is None or (isinstance(v,float) and math.isnan(v)):
            out.append(None); continue
        bisect.insort(hist, v)                        # 只累積到今天, 無未來資料
        if len(hist) < min_periods:
            out.append(None); continue
        r = bisect.bisect_right(hist, v)/len(hist)*100.0   # 含今日、不含未來
        out.append(100.0-r if invert else r)
    return out

def build_app_data(d, R):
    d_idx=d["idx"]
    """對齊所有指標到指數交易日, 產生 dates / 合成分數 / 每指標(值+危險度) 供前端同步顯示。
    危險度一律採 PIT 擴張百分位(只用當日及以前資料), 無前視偏誤。"""
    master=d["idx"].index
    aligned={}; wsum=0.0; dmat=pd.DataFrame(index=master)
    for k,r in R.items():
        s=r["series"]
        if not isinstance(s,pd.Series): continue
        sa=s.reindex(master.union(s.index)).sort_index().ffill().reindex(master)
        dvals=pit_pct(sa.values, invert=(k in INVERT))     # ← PIT 取代全樣本 rank
        dng=pd.Series(dvals, index=master, dtype="float64")
        aligned[k]=dict(label=r["label"],unit=r["unit"],note=r["note"],fmt=FMT.get(k,[0,1,r["unit"]]),
            group=GROUP.get(k,"外部資金情緒"),
            val=[None if pd.isna(x) else round(float(x),2) for x in sa.values],
            dng=[None if x is None else int(round(x)) for x in dvals])
        if k in SLOW:                       # ★ 只有慢層計入結構脆弱度
            w=WEIGHTS.get(k,1.0); dmat[k]=dng*w; wsum+=w
    cols=list(dmat.columns); wvec=np.array([WEIGHTS.get(k,1.0) for k in cols])
    present=dmat.notna()
    wsum_row=(present.values*wvec).sum(axis=1)
    with np.errstate(invalid='ignore',divide='ignore'):
        comp_vals=np.nansum(dmat.values,axis=1)/np.where(wsum_row>0,wsum_row,np.nan)
    need=max(4,int(0.8*len(cols)))                       # 需 ≥80% 指標過暖身才起算
    comp_vals=np.where(present.sum(axis=1).values>=need, comp_vals, np.nan)
    comp=pd.Series(comp_vals,index=master); mask=comp.notna()
    dates=[str(x.date()) for x in master[mask]]
    inds={k:{**v,"val":[v["val"][i] for i in range(len(mask)) if mask.iloc[i]],
                    "dng":[v["dng"][i] for i in range(len(mask)) if mask.iloc[i]]} for k,v in aligned.items()}
    # ── 快層觸發:VIX跳升危險度≥TRIG_PCT 或 指數跌破年線 ──
    def _col(k,f):
        if k not in aligned: return [None]*int(mask.sum())
        src=aligned[k][f]; return [src[i] for i in range(len(mask)) if mask.iloc[i]]
    # 供前端「敏感度旋鈕」即時重算觸發用的輔助序列
    _ix=d_idx.reindex(master).ffill()
    _ma60=_ix.rolling(60).mean(); _br=[1 if (a is not None and b is not None and a<b) else 0
        for a,b in zip(_ix.values,_ma60.values)]
    _r20=((_ix/_ix.shift(20)-1)*100)
    ma60br=[_br[i] for i in range(len(mask)) if mask.iloc[i]]
    r20=[None if pd.isna(x) else round(float(x),2) for x in _r20.values]
    r20=[r20[i] for i in range(len(mask)) if mask.iloc[i]]
    vs=_col("vix_spike","dng"); th=_col("trend_health","val")
    trig_vix=[1 if (x is not None and x>=TRIG_PCT) else 0 for x in vs]
    trig_ma =[1 if (x is not None and x<0) else 0 for x in th]
    trig=[1 if (a or b) else 0 for a,b in zip(trig_vix,trig_ma)]
    return dict(dates=dates, comp=[round(float(x),1) for x in comp[mask].values],
                inds=inds, order=[k for k in ORDER if k in inds],
                trig=trig, trigvix=trig_vix, trigma=trig_ma, trigpct=TRIG_PCT,
                ma60br=ma60br, r20=r20,
                slow=[k for k in ORDER if k in inds and k in SLOW],
                momo=[k for k in ORDER if k in inds and k in MOMO])

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
<link rel="manifest" href="manifest.webmanifest"><meta name="theme-color" content="#111110">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="脆弱度"><link rel="apple-touch-icon" href="icon-192.png">
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
.xlink{display:inline-block;margin-top:8px;font-size:12px;color:var(--series-1);text-decoration:none;border:1px solid var(--border);border-radius:7px;padding:4px 10px}
details.note summary::-webkit-details-marker{color:var(--muted)}
.tabs{display:flex;gap:6px;margin:10px 0 4px;flex-wrap:wrap}
.tabs a{font-size:12.5px;text-decoration:none;color:var(--ts);background:var(--surface);border:1px solid var(--border);border-radius:20px;padding:5px 13px}
.tabs a.on{background:var(--s1,var(--series-1));color:#fff;border-color:var(--s1,var(--series-1))}
#th{position:fixed;top:12px;right:12px;background:var(--surface);color:var(--tp);border:1px solid var(--border);border-radius:8px;padding:6px 11px;cursor:pointer}
</style></head><body><button id="th">◐</button><div class="wrap">
<h1>台股脆弱度儀表板 <span style="font-size:11px;color:var(--ok);border:1px solid var(--border);border-radius:6px;padding:1px 6px">PIT 無前視偏誤</span></h1><div class="sub">資料 FinMind + FRED · 更新於 __ASOF__ · 危險度=PIT擴張百分位(只用當日及以前) · 壓力計非擇時工具 · 非投資建議</div>
<nav class="tabs"><a href="index.html" class="on">台股脆弱度</a><a href="us.html">美股脆弱度</a><a href="industry_heat.html">產業熱度雷達</a><a href="asset_pricing.html">資產定價實驗室</a></nav>
<div class="hero"><div class="gauge" id="gauge"><div class="inner"><div><div class="num" id="cnum">–</div><div class="lb">脆弱度 / 100</div></div></div></div>
 <div class="txt"><div class="big" id="cjudge">–</div>
 <div class="d">慢層<b>結構脆弱度</b>定槓桿上限,快層<b>觸發</b>(VIX跳升/跌破均線)才真的降到 1x。脆弱但未觸發=不加碼、不出場。</div>
 <div class="vd" id="viewdate">游標移過同步各燈號;點按固定、雙擊解除</div>
 <div class="vd" id="rednow" style="color:var(--red);font-weight:650"></div></div></div>
<div class="trend"><div class="th"><span>個股搜尋 <span style="font-weight:400;color:var(--muted)">代號或名稱(上市/上櫃/興櫃)</span></span></div>
 <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
  <input id="q" list="stocklist" placeholder="例:2330 或 台積電" style="font:inherit;background:var(--bg);color:var(--tp);border:1px solid var(--border);border-radius:8px;padding:7px 11px;min-width:220px">
  <datalist id="stocklist"></datalist>
  <button id="qbtn" style="font:inherit;background:var(--series-1);color:#fff;border:0;border-radius:8px;padding:7px 14px;cursor:pointer">查詢</button>
  <span id="qmsg" style="font-size:12px;color:var(--muted)"></span></div>
 <div id="scwrap" style="display:none;margin-top:10px"><div id="sctitle" style="font-size:14px;font-weight:650;margin-bottom:4px"></div>
  <div class="ctrl" id="scranges" style="margin:0 0 6px"><button data-d="244">1年</button><button data-d="732">3年</button><button data-d="1220">5年</button><button data-d="0" class="on">全部</button>
   <span style="color:var(--muted);font-size:11px">(日期範圍見下方脆弱度圖,兩圖共用)</span></div>
  <div id="scstats" class="scstats"></div>
  <div id="scmnote" style="font-size:10.5px;color:var(--muted);margin-top:4px"></div>
  <div id="scsuit" style="font-size:12.5px;margin-top:8px;padding:8px 11px;background:var(--bg);border:1px solid var(--border);border-radius:9px"></div>
  <div id="sc" style="position:relative"><svg id="scsvg"></svg><div id="sctip"></div></div>
  <div style="display:flex;justify-content:space-between;color:var(--muted);font-size:10.5px;margin-top:2px"><span id="scstart"></span><span>還原股價(對數)· 背景=剎車狀態 · 紅虛線▼=本檔出場線 · 兩圖共用游標/縮放</span><span id="scend"></span></div></div></div>
<div class="trend"><div class="th"><span>脆弱度歷史趨勢 <span style="font-weight:400;color:var(--muted)">｜觸發敏感度 </span><select id="sens" style="font:inherit;font-size:11.5px;background:var(--bg);color:var(--tp);border:1px solid var(--border);border-radius:7px;padding:3px 6px"></select><div id="sensnote" style="font-weight:400;font-size:10.5px;color:var(--muted);margin-top:3px"></div></span>
 <span class="ctrl" id="ranges"><button data-d="244">1年</button><button data-d="732">3年</button><button data-d="1220">5年</button><button data-d="0">全部</button>
 <input type="date" id="d0"><span style="color:var(--muted)">~</span><input type="date" id="d1"></span></div>
 <div id="tc"><svg id="tcsvg"></svg><div id="tctip"></div></div>
 <div style="display:flex;justify-content:space-between;color:var(--muted);font-size:10.5px;margin-top:4px">
 <span>移過看數值 · 滾輪縮放 · 點按釘選</span><span>深紅=踩剎車 · 淺紅=僅觸發 · 橙=僅脆弱 · 灰=NBER衰退</span></div></div>
<h2 id="indh" style="margin-bottom:6px">產業子分析 <span style="font-size:11px;font-weight:400;color:var(--muted)">依對該產業報酬的預測力(rank-IC)重排</span></h2>
<div id="indbar" class="indbar"></div>
<div class="indcap" id="indcap"></div>
<div class="gtitle" style="color:var(--series-1)">■ 慢層 · 內部槓桿 <span style="font-weight:400;color:var(--muted)">計分</span></div>
<div class="grid" id="grp-internal"></div>
<div class="gtitle" style="color:var(--series-1)">■ 慢層 · 外部資金與情緒 <span style="font-weight:400;color:var(--muted)">計分</span></div>
<div class="grid" id="grp-external"></div>
<div class="gtitle" style="color:var(--red)">■ 快層 · 觸發 <span style="font-weight:400;color:var(--muted)">亮起才踩剎車</span></div>
<div class="grid" id="grp-trigger"></div>
<div class="gtitle" style="color:var(--muted)">■ 動能參考 <span style="font-weight:400;color:var(--muted)">不計分(乖離越大→後續回撤反而越淺)</span></div>
<div class="grid" id="grp-momo"></div>
<h2>壓力測試 / 敏感度分析(融資追繳連鎖,示意性)</h2>
<table><thead><tr><th>情境</th><th>估計平均維持率</th><th>逼近斷頭比例</th><th>潛在追繳部位</th></tr></thead><tbody>__STRESS__</tbody></table>
<details class="note"><summary style="cursor:pointer;font-weight:650;color:var(--tp)">方法、資料源與已知限制(點開)</summary><div style="margin-top:8px">各指標一律轉成 <b>PIT 擴張百分位</b>(第 t 日危險度只用「當日及以前」的分佈計算,<b>無前視偏誤</b>;需滿一年暖身才起算)再合成——非全樣本百分位。融資背離採<b>去趨勢殘差</b>(<b>擴張視窗迴歸</b>:每日迴歸係數與標準化只用當日及以前資料,已消除「全樣本一次迴歸」造成的前視洩漏)與<b>成長率背離(vs 名目GDP)</b>雙軌:成長背離改以 <b>IMF WEO 台灣名目GDP 年增率</b>(DBnomics,年頻,僅取已實現年份、PIT 落後)為分母——把「經濟自然成長」放進分母,<b>去除用被炒高的指數當分母的泡沫污染</b>(GDP 源不可得時自動退回指數)。真 GDP 為<b>季頻、落後 1–2 月、會修正</b>,故僅用年頻已實現值當慢速結構性錨,非即時訊號。壓力測試假設整體融資維持率約常態(均值160%、斷頭130%),僅為示意非精算。目前融資餘額約 __MARGIN__ 億。NBER 為美國景氣衰退期,因融資資料起於約2013年,範圍內僅涵蓋2020 COVID。<br><b>2026-07 改版(依回測修正):</b>原設計把「乖離類」指標(指數距年線、美股乖離、韓股乖離)當危險訊號,但以 2013–2026 台股樣本回測,其危險度與<b>未來60日最大回撤的 Spearman IC 為負</b>(韓股 −0.139、美股 −0.076、距年線 −0.051)——<b>漲多預測的是後續回撤較淺,不是較深</b>,計分會系統性誤殺仍在噴出的個股。且原「脆弱度≥55 即降槓桿」的撥盤,在 2018/10 與 2022 兩次真空頭中降槓桿天數僅 0% 與 5%,績效反而被「完全不用融資」支配。故改為雙層:慢層只設上限、快層才觸發。<b>但需注意:指標的預測方向跨期並不穩定</b>(例:融資超額水位 IC 在 2013–19 為 −0.119、2020–26 為 +0.112),且樣本以多頭為主,任何減碼規則天生吃虧——本改版降低誤殺,<b>不保證提升績效</b>。<b>本頁為風險框架,非投資建議。</b></div></details></div>
</div>
<script>__APPJS__</script>
<script>document.getElementById('th').onclick=()=>{const r=document.documentElement;r.setAttribute('data-theme',r.getAttribute('data-theme')=='dark'?'light':'dark');if(window.__redraw)window.__redraw();};
if('serviceWorker' in navigator){window.addEventListener('load',()=>navigator.serviceWorker.register('service-worker.js').catch(()=>{}));}</script>
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
function redCount(i){let c=0;for(const k of (D.slow||D.order)){const dg=D.inds[k].dng[i];if(dg!=null&&dg>=75)c++;}return c;}
const NSLOW=(D.slow||D.order).length;
// 雙層:慢層(結構脆弱度)決定槓桿上限;快層(觸發)才真的踩剎車
// 慢層只「收上限」(1.2/1.35/1.5),不強制降到1x;真正降到1x由快層觸發。
// 回測(2013-2026,訊號延後1日、融資成本6%):此組合 TAIEX CAGR +19.2%/MDD -31.7%,
// 優於舊撥盤 +14.0%/-41.5%,也優於一路1.5x的 +16.4%/-45.5%;6442 終值×93 vs 舊撥盤×36。
function levCap(c){return c>=75?1.2:(c>=55?1.35:1.5);}
function levNow(i){return trigAt(i)?1.0:levCap(D.comp[i]);}
// == 觸發敏感度旋鈕:使用者可即時調整「多容易踩剎車」,所有燈號/色帶/曝險即時重算 ==
// 各檔數字為全期回測(訊號延後1日、融資成本6%),樣本以多頭為主,僅供比較鬆緊度的取捨。
const VIXD=D.inds['vix_spike']?D.inds['vix_spike'].dng:[];
const TRV=D.inds['trend_health']?D.inds['trend_health'].val:[];
const SENS=[
 {n:'保守',d:'VIX跳升>=85分位 或 跌破年線',v:85,f:i=>(VIXD[i]!=null&&VIXD[i]>=85)||(TRV[i]!=null&&TRV[i]<0)},
 {n:'標準',d:'VIX跳升>=75分位 或 跌破年線',v:75,f:i=>(VIXD[i]!=null&&VIXD[i]>=75)||(TRV[i]!=null&&TRV[i]<0)},
 {n:'敏感',d:'VIX>=70 或 破年線 或 破季線',v:70,f:i=>(VIXD[i]!=null&&VIXD[i]>=70)||(TRV[i]!=null&&TRV[i]<0)||(D.ma60br&&D.ma60br[i]===1)},
 {n:'最敏感',d:'VIX>=60 或 破季線 或 20日跌逾3%',v:60,f:i=>(VIXD[i]!=null&&VIXD[i]>=60)||(D.ma60br&&D.ma60br[i]===1)||(D.r20&&D.r20[i]!=null&&D.r20[i]<-3)}
];
let SI=1;                                   // 預設「標準」:回測覆蓋率與報酬皆優於原設定,回撤相同
function trigAt(i){try{return SENS[SI].f(i)?1:0;}catch(e){return (D.trig&&D.trig[i])?1:0;}}
function brakeState(i){const c=D.comp[i],t=trigAt(i);
 if(t&&c>=75)return 'brake';        // 又脆弱又已觸發 → 真的降
 if(t)return 'trig';                // 已觸發但結構不脆弱 → 短線避險
 if(c>=75)return 'fragile';         // 脆弱但沒觸發 → 只是不加碼(不出場)
 return 'ok';}
function gauge(i){const c=D.comp[i];$('gauge').style.setProperty('--v',c.toFixed(0));
 $('gauge').style.setProperty('--gc','var(--'+light(c)+')');$('cnum').textContent=c.toFixed(0);
 const cap=levCap(c),st=brakeState(i),tg=trigAt(i);
 const tv=tg&&VIXD[i]!=null&&VIXD[i]>=SENS[SI].v;
 const tm=tg&&TRV[i]!=null&&TRV[i]<0;
 const tb=tg&&SI>=2&&D.ma60br&&D.ma60br[i]===1;
 const td=tg&&SI>=3&&D.r20&&D.r20[i]!=null&&D.r20[i]<-3;
 const why=[tv?'VIX跳升':null,tm?'跌破年線':null,tb?'跌破季線':null,td?'20日跌逾3%':null].filter(Boolean).join('＋')||'趨勢轉弱';
 var cj=$('cjudge');
 const act=levNow(i);
 if(st==='brake'){cj.innerHTML='🔴 <b style="color:var(--red)">踩剎車 → 1.0x</b> <span style="font-size:13px;color:var(--ts)">脆弱'+c.toFixed(0)+' + '+why+'</span>';}
 else if(st==='trig'){cj.innerHTML='🟠 <b style="color:var(--warn)">短線避險 → 1.0x</b> <span style="font-size:13px;color:var(--ts)">'+why+'(結構'+c.toFixed(0)+')</span>';}
 else if(st==='fragile'){cj.innerHTML='🟡 <b style="color:var(--warn)">不加碼,不出場 → 上限 '+cap.toFixed(2)+'x</b> <span style="font-size:13px;color:var(--ts)">脆弱'+c.toFixed(0)+',未觸發</span>';}
 else{cj.innerHTML='🟢 <b>融資可用 → 上限 '+cap.toFixed(2)+'x</b>';}
 const rc=redCount(i);
 $('rednow').innerHTML=(D.dates[i]===D.dates[b]?'':D.dates[i]+' · ')
  +'曝險 <b>'+levNow(i).toFixed(2)+'x</b> · 觸發 '
  +(tg?'<b style="color:var(--red)">是</b>':'<b style="color:var(--ok)">否</b>')
  +' · 亮紅 '+rc+'/'+NSLOW;}
// ---- cards ----
function cardHTML(k){const c=D.inds[k];
 return '<div class="card" id="cd-'+k+'"><div class="ct">'+c.label+'</div><div class="cv" id="cv-'+k+'">–</div>'
 +'<div class="cbarwrap"><div class="cbar" id="cb-'+k+'"></div></div><div class="cp" id="cp-'+k+'"></div>'
 +'<div class="cs" id="cs-'+k+'"></div><div class="cn">'+c.note+'</div>'
 +'<div class="cic" id="cic-'+k+'"></div></div>';}
function buildCards(){
 const g=(n,el)=>{const ks=D.order.filter(k=>D.inds[k].group===n);$(el).innerHTML=ks.map(cardHTML).join('');
   if(!ks.length)$(el).style.display='none';};
 g('內部槓桿','grp-internal'); g('外部資金情緒','grp-external');
 g('觸發層','grp-trigger');    g('動能參考','grp-momo');}
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
 // 背景 = 雙層剎車狀態:深紅=脆弱且已觸發(真的降槓桿)、淺紅=僅觸發、橙=僅結構脆弱(不加碼但不出場)
 const cw=plotW/Math.max(1,curB-curA);
 for(let i=curA;i<=curB;i++){const st=brakeState(i);if(st==='ok')continue;
   const col=(st==='fragile')?'var(--warn)':'var(--red)';
   const op=(st==='brake')?'0.26':(st==='trig'?'0.13':'0.10');
   g+='<rect x="'+(X(i)-cw/2).toFixed(1)+'" y="'+padT+'" width="'+Math.max(1,cw+0.6).toFixed(1)+'" height="'+plotH+'" fill="'+col+'" opacity="'+op+'"/>';}
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
let SCG=null;   // 個股圖幾何快照(供跨圖共用游標)
// 依日期在個股圖畫出同步游標(釘選游標:兩張圖以同一日期對齊)
function syncStockCursor(ds){if(!SD||!SCG)return;const j=scIdxForDate(ds);
 if(j<SCG.SA||j>SCG.SB){$('scx').setAttribute('opacity','0');$('scd').setAttribute('opacity','0');$('sctip').style.opacity='0';return;}
 const x=SCG.X(j),y=SCG.Y(SCG.ly[j]);
 $('scx').setAttribute('x1',x);$('scx').setAttribute('x2',x);$('scx').setAttribute('opacity',pinned?'0.6':'0.35');
 $('scd').setAttribute('cx',x);$('scd').setAttribute('cy',y);$('scd').setAttribute('opacity','1');
 const tp=$('sctip');tp.style.opacity='1';const pf=v=>(v==null||v<=0)?'–':(v>=100?v.toFixed(0):v.toFixed(1));
 tp.innerHTML=(pinned?'📌 ':'')+'<b>'+SD.dates[j]+'</b><br>收盤 '+SD.raw[j]+' <span style="color:var(--muted)">(還原'+pf(SD.adj[j])+')</span>'
  +'<br><span style="color:#2a78d6">月'+pf(SD.ma20?SD.ma20[j]:null)+'</span> <span style="color:#8b5cf6">季'+pf(SD.ma60?SD.ma60[j]:null)+'</span> <span style="color:#0891b2">半年'+pf(SD.ma120?SD.ma120[j]:null)+'</span>';
 let tx=x+12;if(tx>SCG.W-104)tx=x-104;tp.style.left=Math.max(0,tx)+'px';tp.style.top='4px';
 renderStockCardsAt(j);}
function hideStockCursor(){if(!SD)return;$('scx').setAttribute('opacity','0');$('scd').setAttribute('opacity','0');$('sctip').style.opacity='0';if(SM)renderStockCardsAt(SB);}
function showAt(i){sel=i;const x=X(i),y=Y(D.comp[i]);
 $('tcx').setAttribute('x1',x);$('tcx').setAttribute('x2',x);$('tcx').setAttribute('opacity',pinned?'0.6':'0.35');
 $('tcd').setAttribute('cx',x);$('tcd').setAttribute('cy',y);$('tcd').setAttribute('opacity','1');
 const rc=redCount(i);tip.style.opacity='1';
 tip.innerHTML=(pinned?'📌 ':'')+'<b>'+D.dates[i]+'</b><br>脆弱度 '+D.comp[i]+'<br>紅區 '+rc+'/'+NSLOW+(brakeState(i)==='brake'?' ⚠踩剎車':'');
 let tx=x+12;if(tx>W-104)tx=x-104;tip.style.left=Math.max(0,tx)+'px';tip.style.top='4px';
 $('viewdate').textContent=(pinned?'📌 已固定於 '+D.dates[i]+'(移動任一圖換位置,點兩下取消)':'檢視 '+D.dates[i]+' — 下方各燈號同步(點一下可固定)');
 gauge(i);renderCards(i);
 syncStockCursor(D.dates[i]);}   // ← 共用游標:同步個股圖
function move(ev){if(pinned)return;showAt(idxAt(ev));}
function leave(){if(pinned){showAt(pinIdx);return;}
 $('tcx').setAttribute('opacity','0');$('tcd').setAttribute('opacity','0');tip.style.opacity='0';
 sel=b;$('viewdate').textContent='最新('+D.dates[b]+')— 點一下線圖可固定數值';gauge(b);renderCards(b);hideStockCursor();}
function unpin(){pinned=false;pinIdx=null;$('tcx').setAttribute('opacity','0');$('tcd').setAttribute('opacity','0');tip.style.opacity='0';
 sel=b;$('viewdate').textContent='最新('+D.dates[b]+')— 點一下線圖可固定數值';gauge(b);renderCards(b);hideStockCursor();}
function onClick(ev){const i=idxAt(ev);if(pinned&&Math.abs(i-pinIdx)<=1){unpin();}else{pinned=true;pinIdx=i;showAt(i);}}
// 由個股圖的滑鼠位置換算共用游標(對映到脆弱度日期,驅動兩圖)
function stockFragIdx(clientX){const r=$('scsvg').getBoundingClientRect();const g=SCG;if(!g)return null;
 let j=g.SA+Math.round(((clientX-r.left)-g.pl)/g.plotW*(g.SB-g.SA));j=Math.max(g.SA,Math.min(g.SB,j));
 const ds=SD.dates[j];const fi=(DPOS[ds]!=null?DPOS[ds]:idxForTs(Date.parse(ds)));return Math.max(curA,Math.min(curB,fi));}
function syncInputs(){$('d0').value=D.dates[a];$('d1').value=D.dates[b];}
// ---- 共用時間軸:一組控制同時驅動脆弱度圖與個股圖 ----
function markBtns(days){document.querySelectorAll('#ranges button,#scranges button').forEach(x=>x.classList.toggle('on',+x.dataset.d===days));}
function clearBtns(){document.querySelectorAll('#ranges button,#scranges button').forEach(x=>x.classList.remove('on'));}
function applyWindow(d0,d1){                    // d0,d1:日期字串,兩張圖同步套用
 a=idxForTs(Date.parse(d0));b=idxForTs(Date.parse(d1));sel=b;
 $('d0').value=D.dates[a];$('d1').value=D.dates[b];
 renderTrend();gauge(sel);renderCards(sel);applyRanking();
 if(SD){SA=scIdxForDate(d0);SB=scIdxForDate(d1);drawStock();}
}
// ── 滾輪縮放:游標進到圖框內滾動即可放大/縮小,兩張圖共用同一時間窗 ──
// 以游標所在日期為錨點縮放(該日期在畫面上的相對位置保持不變),最少保留 20 根。
let _zraf=null,_zpend=null;
function zoomAt(centerIdx,factor){
 const span=Math.max(1,b-a);
 let nspan=Math.round(span*factor);
 nspan=Math.max(20,Math.min(N-1,nspan));
 if(nspan===span&&factor<1)nspan=Math.max(20,span-1);
 const frac=(centerIdx-a)/span;
 let na=Math.round(centerIdx-frac*nspan), nb=na+nspan;
 if(na<0){nb-=na;na=0;}
 if(nb>N-1){na-=(nb-(N-1));nb=N-1;}
 na=Math.max(0,na);
 if(nb-na<20)return;
 _zpend=[na,nb];
 if(_zraf)return;
 _zraf=requestAnimationFrame(()=>{_zraf=null;const[qa,qb]=_zpend;
  clearBtns();a=qa;b=qb;applyWindow(D.dates[a],D.dates[b]);});
}
function wheelZoom(ev,idxFn){
 const f=(ev.deltaY<0)?0.82:1.22;      // 上滾=放大(區間變窄)、下滾=縮小
 const ci=idxFn(ev);
 if(ci==null)return;
 ev.preventDefault();
 zoomAt(ci,f);
}
function sharedDays(days){b=N-1;a=days<=0?0:Math.max(0,N-days);markBtns(days);applyWindow(D.dates[a],D.dates[b]);}
function sharedDates(d0,d1){if(!d0||!d1||Date.parse(d0)>=Date.parse(d1))return;clearBtns();applyWindow(d0,d1);}
window.__redraw=function(){renderTrend();if(SD)drawStock();gauge(sel);renderCards(sel);applyRanking();};
// events(脆弱度圖的控制)
document.querySelectorAll('#ranges button').forEach(btn=>btn.onclick=()=>sharedDays(+btn.dataset.d));
$('d0').onchange=()=>sharedDates($('d0').value,$('d1').value);
$('d1').onchange=()=>sharedDates($('d0').value,$('d1').value);
svg.addEventListener('wheel',e=>wheelZoom(e,ev=>idxAt(ev)),{passive:false});
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
 const ropen=rows.map(r=>+r.open||0),rhigh=rows.map(r=>+r.max||0),rlow=rows.map(r=>+r.min||0);
 let f=raw[0];const adj=[raw[0]];for(let i=1;i<raw.length;i++){let lr=Math.log(raw[i]/raw[i-1]);if(Math.abs(lr)>0.13)lr=0;f*=Math.exp(lr);adj.push(f);}
 const rt=adj.map((a,i)=>raw[i]>0?a/raw[i]:1);   // 還原比例(套用同一調整因子到 OHLC)
 const aopen=ropen.map((v,i)=>v>0?v*rt[i]:null),ahigh=rhigh.map((v,i)=>v>0?v*rt[i]:null),alow=rlow.map((v,i)=>v>0?v*rt[i]:null);
 return {dates,raw,adj,vol,aopen,ahigh,alow};}
// 簡單移動平均(以還原收盤計算;未滿 n 日為 null)
function sma(arr,n){const out=new Array(arr.length).fill(null);let s=0;for(let i=0;i<arr.length;i++){const v=arr[i]||0;s+=v;if(i>=n)s-=(arr[i-n]||0);if(i>=n-1)out[i]=s/n;}return out;}
// ── 個股自身出場線:Chandelier Exit = 近22日最高 − 3×ATR(22) ──────────
// 為什麼用它:跨期最穩定的是「趨勢」而非大盤脆弱度;此線隨波動自動放寬,
// 高波動飆股不會被一點回檔就洗掉,但真的轉勢時會明確跌破。
function chandelier(SDx,n=22,mult=3){
 const c=SDx.adj,h=SDx.ahigh,l=SDx.alow,N=c.length;
 const tr=new Array(N).fill(null);
 for(let i=1;i<N;i++){const hi=(h[i]!=null?h[i]:c[i]),lo=(l[i]!=null?l[i]:c[i]);
  tr[i]=Math.max(hi-lo,Math.abs(hi-c[i-1]),Math.abs(lo-c[i-1]));}
 const atr=new Array(N).fill(null);let acc=0,cnt=0;
 for(let i=1;i<N;i++){acc+=tr[i];cnt++;if(cnt>n){acc-=tr[i-n];cnt=n;}if(cnt>=n)atr[i]=acc/n;}
 const out=new Array(N).fill(null);
 for(let i=0;i<N;i++){if(atr[i]==null||i<n)continue;
  let mx=0;for(let j=i-n+1;j<=i;j++){const v=(h[j]!=null?h[j]:c[j]);if(v>mx)mx=v;}
  out[i]=mx-mult*atr[i];}
 return out;}
// 該檔套用「跌破出場線就出場、站回60日線再進場」的歷史統計(含誤殺次數)
function exitRuleStats(SDx,ch){
 const c=SDx.adj,ma=SDx.ma60,N=c.length;
 let inPos=true,trades=0,kills=0,saved=[],lastExit=-1;
 for(let i=1;i<N;i++){
  if(ch[i]==null)continue;
  const conf=(ma[i]!=null&&c[i]<ma[i]);            // 確認條件:同時跌破季線(大幅減少洗刷)
  if(inPos&&c[i]<ch[i]&&conf){inPos=false;trades++;lastExit=i;
   // 出場後60日:最低點(避開多少)與最高點(誤殺多少)
   const e=Math.min(N-1,i+60);let mn=Infinity,mx=0;
   for(let j=i;j<=e;j++){if(c[j]<mn)mn=c[j];if(c[j]>mx)mx=c[j];}
   saved.push((mn/c[i]-1)*100);
   if(mx/c[i]-1>0.20)kills++;                       // 出場後60日內又漲>20% = 誤殺
  } else if(!inPos&&ma[i]!=null&&c[i]>ma[i]){inPos=true;}
 }
 // 權益曲線對照(訊號延後1日生效)
 let eqR=1,eqB=1,pos=true,peakR=1,peakB=1,mddR=0,mddB=0;
 let hold=true;
 for(let i=1;i<N;i++){
  const r=(c[i-1]>0)?c[i]/c[i-1]-1:0;
  eqB*=(1+r); peakB=Math.max(peakB,eqB); mddB=Math.min(mddB,eqB/peakB-1);
  if(hold)eqR*=(1+r); peakR=Math.max(peakR,eqR); mddR=Math.min(mddR,eqR/peakR-1);
  // 用「昨日收盤」決定今天是否在場
  if(ch[i]!=null&&c[i]<ch[i]&&ma[i]!=null&&c[i]<ma[i])hold=false; else if(ma[i]!=null&&c[i]>ma[i])hold=true;
 }
 return {trades,kills,avgSaved:saved.length?saved.reduce((a,x)=>a+x,0)/saved.length:null,
         eqR,eqB,mddR:mddR*100,mddB:mddB*100};}
// 小工具
function ols3(X,y){const k=X[0].length,A=Array.from({length:k},()=>Array(k).fill(0)),bb=Array(k).fill(0);
 for(let i=0;i<X.length;i++){for(let a=0;a<k;a++){bb[a]+=X[i][a]*y[i];for(let c=0;c<k;c++)A[a][c]+=X[i][a]*X[i][c];}}
 for(let c=0;c<k;c++){let pv=c;for(let r=c+1;r<k;r++)if(Math.abs(A[r][c])>Math.abs(A[pv][c]))pv=r;[A[c],A[pv]]=[A[pv],A[c]];[bb[c],bb[pv]]=[bb[pv],bb[c]];
  for(let r=0;r<k;r++)if(r!=c){const fr=A[r][c]/A[c][c];for(let cc=c;cc<k;cc++)A[r][cc]-=fr*A[c][cc];bb[r]-=fr*bb[c];}}
 return bb.map((v,i)=>v/A[i][i]);}
function pctile(arr,v){const s=arr.filter(x=>x!=null&&!isNaN(x));return s.length?s.filter(x=>x<v).length/s.length*100:null;}
function stLight(p){return p==null?'':p>=75?'red':p>=55?'warn':'ok';}
let _txmap=null;
function txMap(){if(_txmap)return _txmap;_txmap=new Map();const t=D.taiex||{dates:[],close:[]};for(let i=0;i<t.dates.length;i++)_txmap.set(t.dates[i],t.close[i]);return _txmap;}
function computeBeta(win){return computeBetaAt(win,SD?SD.dates.length-1:0);}
// 市場 Beta:以「截至第 t 日、往前 win 日」的個股vs大盤(TAIEX)日報酬迴歸斜率(前推視窗)
function computeBetaAt(win,t){
 if(!SD||!D.taiex||!D.taiex.dates.length)return null;const tm=txMap();const rs=[],rm=[];
 const start=Math.max(1,t-win+1);
 for(let i=start;i<=t;i++){const m=tm.get(SD.dates[i]),mp=tm.get(SD.dates[i-1]);const sp=SD.adj[i-1],sc=SD.adj[i];
  if(m==null||mp==null||sp<=0||sc<=0||mp<=0||m<=0)continue;rs.push(Math.log(sc/sp));rm.push(Math.log(m/mp));}
 if(rs.length<60)return null;
 const mmn=rm.reduce((a,b)=>a+b,0)/rm.length,smn=rs.reduce((a,b)=>a+b,0)/rs.length;
 let cov=0,vm=0;for(let i=0;i<rs.length;i++){cov+=(rs[i]-smn)*(rm[i]-mmn);vm+=(rm[i]-mmn)**2;}
 return vm>0?cov/vm:null;}
// PIT 百分位:只用「第 t 日及以前」的非空值計算(游標指向歷史點時,危險度不含未來資訊)
function pctileAt(arr,t){const v=arr[t];if(v==null||isNaN(v))return null;let cnt=0,less=0;
 for(let i=0;i<=t&&i<arr.length;i++){const x=arr[i];if(x==null||isNaN(x))continue;cnt++;if(x<v)less++;}
 return cnt?less/cnt*100:null;}
let SM=null;   // 已抓取之個股融資序列(供各時點重算)
async function renderStockMargin(code){
 SM=null;const box=$('scstats');box.innerHTML='';$('scmnote').textContent='';
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
 // 逐日序列:半年擴張、YoY背離、使用率、券資比(各時點皆可讀)
 const roc=[],ydiv=[],usage=[],shortR=[];
 for(let i=0;i<n;i++){roc.push(pc(bal,126,i));const my=pc(bal,244,i),py=pc(adj,244,i);ydiv.push((my!=null&&py!=null)?my-py:null);
  usage.push((bal[i]!=null&&limit[i]>0)?bal[i]/limit[i]*100:null);
  shortR.push((bal[i]>0&&sbal[i]!=null)?sbal[i]/bal[i]*100:null);}
 // 融資超額水位:log(bal)~log(adj)+log(volMA20) 殘差z(整段迴歸,值依原索引存回)
 let residSeries=new Array(n).fill(null);
 const volma=vol.map((_,i)=>i<20?null:vol.slice(i-19,i+1).reduce((x,y)=>x+y,0)/20);
 const Xr=[],Yr=[],idxr=[];
 for(let i=0;i<n;i++){if(bal[i]>0&&adj[i]>0&&volma[i]>0){Xr.push([Math.log(adj[i]),Math.log(volma[i]),1]);Yr.push(Math.log(bal[i]));idxr.push(i);}}
 if(Xr.length>200){const bt=ols3(Xr,Yr);const res=Yr.map((y,j)=>y-(Xr[j][0]*bt[0]+Xr[j][1]*bt[1]+bt[2]));
  const mu=res.reduce((a,b)=>a+b,0)/res.length,sd=Math.sqrt(res.reduce((a,b)=>a+(b-mu)*(b-mu),0)/res.length);
  res.forEach((r,j)=>{residSeries[idxr[j]]=sd>0?(r-mu)/sd:null;});}
 SM={code,bal,limit,sbal,roc,ydiv,usage,shortR,residSeries,n};
 renderStockCardsAt(SB);
}
// 依「第 t 日」重算並顯示個股指標卡(游標指向該點時觸發;窗口皆前推至 t)
function renderStockCardsAt(t){if(!SM||!SD)return;t=Math.max(0,Math.min(SM.n-1,t));
 const box=$('scstats');const {code,bal,roc,ydiv,usage,shortR,residSeries}=SM;
 const f1=x=>x==null?'–':(x>=0?'+':'')+x.toFixed(1);
 const beta=computeBetaAt(252,t);
 const residZ=residSeries[t];
 const us=usage[t],sr=shortR[t];
 function tile(label,val,sub,p){const lt=stLight(p);
  return '<div class="stile '+lt+'"><div class="l">'+label+'</div><div class="v">'+val+'</div><div class="s">'+(sub||'')+'</div></div>';}
 let html='';
 html+=tile('市場Beta(1年)',beta==null?'–':beta.toFixed(2),'對大盤敏感度'+(beta!=null&&beta>=1.2?' · 高':''), beta==null?null:Math.min(100,Math.max(0,(beta-0.5)/1.5*100)));
 html+=tile('融資餘額(張)',bal[t]!=null?Math.round(bal[t]).toLocaleString():'–','半年擴張 '+f1(roc[t])+'%',pctileAt(roc,t));
 html+=tile('融資成長背離',f1(ydiv[t])+'pp','融資YoY−股價YoY',pctileAt(ydiv,t));
 html+=tile('融資超額水位',residZ==null?'資料不足':f1(residZ)+'σ','去趨勢殘差',pctileAt(residSeries,t));
 html+=tile('融資使用率',us==null?'–':us.toFixed(0)+'%','餘額/限額',us);
 html+=tile('券資比',sr==null?'–':sr.toFixed(0)+'%','融券/融資',null);
 box.innerHTML=html;
 const marketDi=DPOS[SD.dates[t]];const marketRed=(marketDi!=null&&D.comp[marketDi]>=75);
 const priority=(beta!=null&&beta>=1.2)&&(marketRed||(residZ!=null&&residZ>1)||(us!=null&&us>=60));
 const isLatest=(t===SM.n-1);
 $('scmnote').innerHTML='<b>'+SD.dates[t]+'</b>'+(isLatest?'(最新)':'(游標點,前推視窗)')+' · '+code+' 個股指標,危險度=該股至此日百分位'
   +(priority?' · <b style="color:var(--red)">⚠ 高Beta+槓桿偏高,優先降本檔</b>':'')
   +'';
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
  SD.ma20=sma(SD.adj,20);SD.ma60=sma(SD.adj,60);SD.ma120=sma(SD.adj,120);   // 月線/季線/半年線(還原收盤)
  SD.exit=chandelier(SD);SD.exstats=exitRuleStats(SD,SD.exit);              // 個股自身出場線 + 規則統計
  $('sctitle').textContent=SD.name;$('scwrap').style.display='';$('qmsg').textContent='';
  // 對齊到目前脆弱度圖的時間窗(共用時間軸)
  SA=scIdxForDate(D.dates[a]);SB=scIdxForDate(D.dates[b]);
  renderStockMargin(code);drawStock();
 }catch(e){let m=e.message;
   if(location.protocol==='file:')m='此頁是用「檔案(file://)」開啟,瀏覽器會擋住向 FinMind 的跨站抓取。請改用網址開啟(上線到 GitHub Pages/Netlify),或在此資料夾執行 python -m http.server 後開 http://localhost:8000/';
   else if(/fetch/i.test(m))m='抓取失敗——可能是網路、廣告封鎖擴充套件擋了 api.finmindtrade.com,或速率上限(可加 ?token=)';
   $('qmsg').textContent='⚠ '+m;}}
function drawStock(){if(!SD)return;const svg=$('scsvg'),wrap=$('sc'),tip=$('sctip');
 const W=wrap.clientWidth||800,H=210,pl=46,pr=12,pt=12,pb=22,plotW=W-pl-pr,plotH=H-pt-pb;
 const ly=SD.adj.map(x=>Math.log10(x));const span=SB-SA;
 // y 範圍:納入還原 OHLC 高低與三條均線,避免K棒影線/均線被裁掉
 let vmn=Infinity,vmx=-Infinity;const psh=v=>{if(v!=null&&v>0){if(v<vmn)vmn=v;if(v>vmx)vmx=v;}};
 for(let i=SA;i<=SB;i++){psh(SD.ahigh[i]!=null?SD.ahigh[i]:SD.adj[i]);psh(SD.alow[i]!=null?SD.alow[i]:SD.adj[i]);psh(SD.ma20[i]);psh(SD.ma60[i]);psh(SD.ma120[i]);}
 if(!isFinite(vmn)){vmn=Math.min(...SD.adj.slice(SA,SB+1));vmx=Math.max(...SD.adj.slice(SA,SB+1));}
 const lo=Math.log10(vmn),hi=Math.log10(vmx);
 const X=i=>pl+(span<1?0:(i-SA)/span*plotW),Y=v=>pt+(hi-v)/((hi-lo)||1)*plotH;const Yl=pv=>Y(Math.log10(pv));
 svg.setAttribute('width',W);svg.setAttribute('height',H);svg.setAttribute('viewBox','0 0 '+W+' '+H);
 let g='';[0,0.25,0.5,0.75,1].forEach(t=>{const lv=lo+(hi-lo)*t,pv=Math.pow(10,lv);
  g+='<line x1="'+pl+'" y1="'+Y(lv)+'" x2="'+(W-pr)+'" y2="'+Y(lv)+'" stroke="var(--border)" stroke-dasharray="3 3"/>'
   +'<text x="'+(pl-4)+'" y="'+(Y(lv)+3)+'" font-size="9" fill="var(--muted)" text-anchor="end">'+(pv>=100?pv.toFixed(0):pv.toFixed(1))+'</text>';});
 // 背景延伸「市場高危險區」到個股圖:合成脆弱度 ≥75 紅、55–75 橙(與儀表燈號一致、共用時間軸)
 const cw=plotW/Math.max(1,span);
 for(let i=SA;i<=SB;i++){const di=DPOS[SD.dates[i]];if(di==null)continue;const st=brakeState(di);
  if(st==='ok')continue;
  const col=(st==='fragile')?'var(--warn)':'var(--red)';
  const op=(st==='brake')?'0.24':(st==='trig'?'0.12':'0.09');
  g+='<rect x="'+(X(i)-cw/2).toFixed(1)+'" y="'+pt+'" width="'+Math.max(1,cw+0.6).toFixed(1)+'" height="'+plotH+'" fill="'+col+'" opacity="'+op+'"/>';}
 const showMonth=span<=500;let lastL=null;for(let i=SA;i<=SB;i++){const lab=showMonth?SD.dates[i].slice(0,7):SD.dates[i].slice(0,4);
  if(lab!==lastL){lastL=lab;if(showMonth||(+lab%(span>2600?3:1)==0))g+='<text x="'+X(i)+'" y="'+(H-6)+'" font-size="9" fill="var(--muted)" text-anchor="middle">'+lab+'</text>';}}
 // K線(還原OHLC;台股慣例:收≥開為紅、收<開為綠)。太密(K棒<2.2px)則退回還原收盤線
 const slot=plotW/Math.max(1,span+1),drawK=slot>=2.2;
 if(drawK){const bw=Math.max(1,Math.min(9,slot*0.62));
  for(let i=SA;i<=SB;i++){const o=SD.aopen[i],c=SD.adj[i],h=SD.ahigh[i],l=SD.alow[i];if(!(c>0))continue;
   const x=X(i),up=(o!=null?c>=o:true),col=up?'var(--red)':'var(--ok)';
   if(h>0&&l>0)g+='<line x1="'+x.toFixed(1)+'" y1="'+Yl(h).toFixed(1)+'" x2="'+x.toFixed(1)+'" y2="'+Yl(l).toFixed(1)+'" stroke="'+col+'" stroke-width="1"/>';
   if(o!=null&&o>0){const yo=Yl(o),yc=Yl(c),top=Math.min(yo,yc),ht=Math.max(1,Math.abs(yo-yc));
    g+='<rect x="'+(x-bw/2).toFixed(1)+'" y="'+top.toFixed(1)+'" width="'+bw.toFixed(1)+'" height="'+ht.toFixed(1)+'" fill="'+col+'"/>';}}
 }else{let p='';for(let i=SA;i<=SB;i++)p+=(i==SA?'M':'L')+X(i).toFixed(1)+' '+Y(ly[i]).toFixed(1)+' ';
  g+='<path d="'+p+'" fill="none" stroke="var(--ts)" stroke-width="1.2" opacity="0.75"/>';}
 // 均線:月線(20)/季線(60)/半年線(120)
 function maPath(ma){let p='',on=false;for(let i=SA;i<=SB;i++){const v=ma[i];if(v==null||v<=0){on=false;continue;}p+=(on?'L':'M')+X(i).toFixed(1)+' '+Yl(v).toFixed(1)+' ';on=true;}return p;}
 const MAS=[[SD.ma20,'#2a78d6','月線20'],[SD.ma60,'#8b5cf6','季線60'],[SD.ma120,'#0891b2','半年線120']];
 MAS.forEach(m=>{const d=maPath(m[0]);if(d)g+='<path d="'+d+'" fill="none" stroke="'+m[1]+'" stroke-width="1.2" opacity="0.95"/>';});
 // 個股自身出場線(Chandelier: 22日高 − 3×ATR)+ 跌破訊號▼
 if(SD.exit){const de=maPath(SD.exit);
  if(de)g+='<path d="'+de+'" fill="none" stroke="var(--red)" stroke-width="1.1" stroke-dasharray="4 3" opacity="0.8"/>';
  for(let i=Math.max(SA,1);i<=SB;i++){const e=SD.exit[i],ep=SD.exit[i-1];
   if(e==null||ep==null)continue;
   const m6=SD.ma60[i],m6p=SD.ma60[i-1];
   const now=(SD.adj[i]<e)&&(m6!=null&&SD.adj[i]<m6);
   const prev=(SD.adj[i-1]<ep)&&(m6p!=null&&SD.adj[i-1]<m6p);
   if(now&&!prev){
    const yy=Yl(SD.adj[i])-8;
    g+='<path d="M'+X(i).toFixed(1)+' '+yy.toFixed(1)+' l4.5 -7 l-9 0 z" fill="var(--red)"/>';}}}
 // 圖例
 let lx=pl+3;MAS.concat([[null,'var(--red)','出場線▼']]).forEach(m=>{
  g+='<line x1="'+lx+'" y1="'+(pt+7)+'" x2="'+(lx+13)+'" y2="'+(pt+7)+'" stroke="'+m[1]+'" stroke-width="2"'+(m[0]===null?' stroke-dasharray="3 2"':'')+'/>'
  +'<text x="'+(lx+16)+'" y="'+(pt+10)+'" font-size="9" fill="var(--muted)">'+m[2]+'</text>';lx+=16+m[2].length*9+16;});
 g+='<line id="scx" y1="'+pt+'" y2="'+(pt+plotH)+'" stroke="var(--tp)" opacity="0"/><circle id="scd" r="3.2" fill="var(--series-1)" opacity="0"/>';
 svg.innerHTML=g;
 $('scstart').textContent=SD.dates[SA];$('scend').textContent=SD.dates[SB];
 SCG={X,Y,ly,SA,SB,W,pl,plotW};   // 幾何快照:供跨圖共用游標定位
 // 個股圖滑鼠 → 換算共用日期 → 驅動兩圖(釘選游標)
 svg.onwheel=ev=>wheelZoom(ev,e=>stockFragIdx(e.clientX));
 svg.onmousemove=ev=>{if(pinned)return;const fi=stockFragIdx(ev.clientX);if(fi!=null)showAt(fi);};
 svg.onmouseleave=()=>{leave();};
 svg.onclick=ev=>{const fi=stockFragIdx(ev.clientX);if(fi==null)return;
  if(pinned&&Math.abs(fi-pinIdx)<=1){unpin();}else{pinned=true;pinIdx=fi;showAt(fi);}};
 svg.ondblclick=unpin;
 if(pinned&&pinIdx>=curA&&pinIdx<=curB)syncStockCursor(D.dates[pinIdx]);   // 重繪後保持釘選游標
 else if(SM)renderStockCardsAt(SB);   // 未指向時,卡片對齊目前視窗右緣(SB)
 updateSuit();}
function updateSuit(){const el=$('scsuit');if(!el||!SD)return;
 let cs=[],nb=0,tot=0;
 for(let i=SA;i<=SB;i++){const di=DPOS[SD.dates[i]];if(di==null)continue;tot++;cs.push(D.comp[di]);if(brakeState(di)==='brake')nb++;}
 if(!tot){el.innerHTML='<span style="color:var(--muted)">此區間早於脆弱度資料(約2013前),無市場脆弱度可對照</span>';return;}
 const avg=cs.reduce((a,b)=>a+b,0)/cs.length,mx=Math.max(...cs),pb=nb/tot*100;
 let v,col;
 if(avg>=70||pb>=15){v='不宜融資持有(系統高壓)';col='var(--red)';}
 else if(avg>=55||pb>=5){v='融資需謹慎';col='var(--warn)';}
 else{v='系統壓力低,相對適合';col='var(--ok)';}
 let h='此區間 脆弱度均 <b>'+avg.toFixed(0)+'</b>/峰 '+mx.toFixed(0)+' · 踩剎車 <b>'+pb.toFixed(0)+'%</b> → <b style="color:'+col+'">'+v+'</b>';
 const s=SD.exstats;
 if(s&&s.trades>0){
  h+='<div style="margin-top:6px;padding-top:6px;border-top:1px solid var(--border)">'
   +'<b>本檔出場線</b>(紅虛線+跌破季線)全期:觸發 <b>'+s.trades+'</b> 次 · 平均再跌 <b>'+(s.avgSaved==null?'–':s.avgSaved.toFixed(1)+'%')+'</b> · '
   +'<b style="color:var(--warn)">誤殺 '+s.kills+'</b> 次 · 規則 ×<b>'+s.eqR.toFixed(1)+'</b>('+s.mddR.toFixed(0)+'%) vs 抱著 ×<b>'+s.eqB.toFixed(1)+'</b>('+s.mddB.toFixed(0)+'%)</div>';}
 el.innerHTML=h;}
function scIdxForDate(ds){const t=Date.parse(ds);let lo=0,hi=SD.dates.length-1;const T=SD.dates.map(Date.parse);
 if(t<=T[0])return 0;if(t>=T[hi])return hi;while(lo<hi){const m=(lo+hi)>>1;if(T[m]<t)lo=m+1;else hi=m;}return lo;}
function setupSearch(){buildDatalist();$('qbtn').onclick=doSearch;
 $('q').addEventListener('keydown',e=>{if(e.key==='Enter')doSearch();});
 // 個股圖的區間鈕 → 走共用時間軸(同步驅動脆弱度圖;日期輸入已統一至脆弱度圖)
 document.querySelectorAll('#scranges button').forEach(btn=>btn.onclick=()=>sharedDays(+btn.dataset.d));
 let st;window.addEventListener('resize',()=>{clearTimeout(st);st=setTimeout(()=>{if(SD)drawStock();},150);});}

// 敏感度旋鈕
const SENSTAT={"0": ["34%", "44%", "+19.2%", "-32%", "x93"], "1": ["41%", "51%", "+19.8%", "-32%", "x81"], "2": ["55%", "67%", "+18.5%", "-32%", "x67"], "3": ["59%", "63%", "+19.9%", "-31%", "x61"]};
function sensLabel(){const t=SENSTAT[SI];
 return SENS[SI].d+(t?' · 回測 降1x '+t[0]+' / 覆蓋 '+t[1]+' / CAGR '+t[2]+' / MDD '+t[3]:'');}
function buildSens(){const el=$('sens');if(!el)return;
 el.innerHTML=SENS.map((x,i)=>'<option value="'+i+'"'+(i===SI?' selected':'')+'>'+x.n+'</option>').join('');
 $('sensnote').textContent=sensLabel();
 el.onchange=()=>{SI=+el.value;$('sensnote').textContent=sensLabel();
  renderTrend();gauge(sel);renderCards(sel);applyRanking();if(SD)drawStock();};}
// init
buildCards();buildIndBar();buildSens();setupSearch();syncInputs();markBtns(732);renderTrend();gauge(b);renderCards(b);applyRanking();
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
    # 嵌入 TAIEX 日收盤(供個股 beta 計算,與脆弱度共用時間軸)
    try:
        tx=d["idx"]
        app["taiex"]={"dates":[str(x.date()) for x in tx.index],"close":[round(float(v),2) for v in tx.values]}
    except Exception:
        app["taiex"]={"dates":[],"close":[]}
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
