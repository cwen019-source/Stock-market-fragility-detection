#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股脆弱度儀表板 (每日更新)
================================
彙整槓桿、估值、情緒、資金流、波動度等訊號, 正規化後合成「脆弱度分數」,
並附壓力測試(敏感度分析)。純風控壓力計, 不是擇時工具, 非投資建議。

資料源(皆免費, 無需金鑰):
  FinMind : 融資餘額 / 加權指數+成交值 / 三大法人 / USD-TWD
  FRED    : 美國 VIX(VIXCLS) / S&P500

用法:
  pip install requests pandas numpy
  python3 fragility_dashboard.py            # 產生 fragility_dashboard.html + 追加 history csv
  (可選) 設環境變數 FINMIND_TOKEN 以提高速率上限
每天跑一次即可(見檔尾「每日排程」說明)。
"""
import os, sys, json, math, datetime as dt
import requests, pandas as pd, numpy as np

FINMIND="https://api.finmindtrade.com/api/v4/data"
TOKEN=os.environ.get("FINMIND_TOKEN","")
START="2012-01-01"
HIST_CSV="fragility_history.csv"
OUT_HTML=os.environ.get("OUT_HTML","index.html")   # GitHub Pages 用 index.html

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
        url=f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd=2012-01-01"
        r=requests.get(url,timeout=40)
        df=pd.read_csv(pd.io.common.StringIO(r.text))
        df.columns=["date","val"]; df["date"]=pd.to_datetime(df["date"])
        df["val"]=pd.to_numeric(df["val"],errors="coerce")
        return df.dropna().set_index("date")["val"]
    except Exception:
        return pd.Series(dtype=float)

# ---------- 取數 ----------
def get_data():
    d={}
    # 融資餘額
    m=fm("TaiwanStockTotalMarginPurchaseShortSale")
    if len(m):
        mm=m[m["name"]=="MarginPurchaseMoney"].copy()
        mm["date"]=pd.to_datetime(mm["date"]); mm["v"]=pd.to_numeric(mm["TodayBalance"])/1e8
        d["margin"]=mm.sort_values("date").set_index("date")["v"]
    # 加權指數 + 成交值
    t=fm("TaiwanStockPrice", data_id="TAIEX")
    if len(t):
        t["date"]=pd.to_datetime(t["date"])
        d["idx"]=t.sort_values("date").set_index("date")["close"].astype(float)
        d["turn"]=(pd.to_numeric(t.set_index(t["date"])["Trading_money"])/1e8).sort_index()
    # 三大法人(外資淨買賣, 億元)
    ii=fm("TaiwanStockTotalInstitutionalInvestors")
    if len(ii):
        f=ii[ii["name"]=="Foreign_Investor"].copy()
        f["date"]=pd.to_datetime(f["date"]); f["net"]=(pd.to_numeric(f["buy"])-pd.to_numeric(f["sell"]))/1e8
        d["foreign"]=f.sort_values("date").set_index("date")["net"]
    # 匯率 USD/TWD
    fx=fm("TaiwanExchangeRate", data_id="USD")
    if len(fx):
        fx["date"]=pd.to_datetime(fx["date"])
        d["usdtwd"]=fx.sort_values("date").set_index("date")["spot_sell"].astype(float)
    # VIX & S&P (FRED)
    d["vix"]=fred("VIXCLS"); d["spx"]=fred("SP500")
    return d

# ---------- 指標計算 ----------
def pct_rank(s, v):           # v 在 s 歷史的百分位(0-100)
    s=s.dropna()
    return float((s<v).mean()*100) if len(s) else np.nan

def zscore(s, v):
    s=s.dropna(); return float((v-s.mean())/s.std()) if s.std()>0 else 0.0

def compute(d):
    R={}
    idx=d.get("idx"); margin=d.get("margin"); turn=d.get("turn")
    # 對齊
    base=pd.DataFrame({"idx":idx,"margin":margin,"turn":turn}).dropna()
    # 1) 融資去趨勢殘差 z (log margin ~ log idx + log turn_ma) — 剔除市場規模/量能自然成長
    #    刻意不加時間趨勢: 時間趨勢會把「指數級擴張」當常態, 且回檔後殘差反轉為負而誤判
    if len(base)>300:
        b=base.copy(); b["tma"]=b["turn"].rolling(60).mean().bfill()
        X=np.column_stack([np.log(b["idx"]),np.log(b["tma"]),np.ones(len(b))])
        y=np.log(b["margin"]); beta,_,_,_=np.linalg.lstsq(X,y,rcond=None)
        resid=y-X@beta; zr=(resid-resid.mean())/resid.std()
        R["margin_resid_z"]=dict(val=float(zr.iloc[-1]), series=zr, unit="σ",
            label="融資超額水位", note="剔除指數/量能後,融資多出幾個σ(去趨勢殘差)")
    # 2) 融資成長背離 (margin YoY - idx YoY) — 對污染最穩健
    if len(base)>260:
        myoy=base["margin"].pct_change(244)*100; iyoy=base["idx"].pct_change(244)*100
        div=(myoy-iyoy)
        R["margin_yoy_div"]=dict(val=float(div.iloc[-1]), series=div, unit="pp",
            label="融資成長背離(YoY)", note="融資年增率 − 指數年增率;正越大=槓桿跑贏價值")
    # 3) 融資擴張速度: 半年ROC百分位 (與分母無關, 抓拋物線噴出)
    if len(base)>150:
        roc=base["margin"].pct_change(126)*100
        R["margin_roc"]=dict(val=float(roc.iloc[-1]), series=roc, unit="%",
            label="融資半年擴張", note="融資餘額近126日變化;拋物線噴出=froth")
    # 4) VIX 自滿(低VIX+高槓桿最危險) — 用「低VIX百分位」當脆弱度
    vix=d.get("vix")
    if len(vix):
        R["vix_level"]=dict(val=float(vix.iloc[-1]), series=vix, unit="",
            label="VIX 波動度", note="低=自滿(脆弱累積), 高=恐慌(壓力已至)")
        R["vix_spike"]=dict(val=float(vix.iloc[-1]-vix.iloc[-6]) if len(vix)>6 else 0.0,
            series=vix.diff(5), unit="", label="VIX 5日跳升", note="正跳升=避險情緒轉向")
    # 5) 外資流向(20日累計淨買賣, 負=賣超)
    fo=d.get("foreign")
    if len(fo):
        roll=fo.rolling(20).sum()
        R["foreign_flow"]=dict(val=float(roll.iloc[-1]), series=roll, unit="億",
            label="外資20日淨流向", note="大額賣超=資金撤離/去槓桿確認")
    # 6) 匯率壓力(USD/TWD 20日變動, 貶值=外流壓力)
    fx=d.get("usdtwd")
    if len(fx):
        chg=(fx/fx.shift(20)-1)*100
        R["fx_pressure"]=dict(val=float(chg.iloc[-1]), series=chg, unit="%",
            label="台幣20日貶值幅度", note="台幣走貶=外資匯出/資金外流壓力")
    # 7) 大盤趨勢健康(距年線 %)
    if idx is not None and len(idx)>240:
        ma240=idx.rolling(240).mean(); dev=(idx/ma240-1)*100
        R["trend_health"]=dict(val=float(dev.iloc[-1]), series=dev, unit="%",
            label="指數距年線", note="過高=乖離過大易回檔;跌破=趨勢轉弱")
    return R, base

# ---------- 危險度轉換 (0-100, 越高越危險) + 燈號 ----------
def danger(key, R):
    r=R[key]; s=r["series"].dropna(); v=r["val"]
    if key=="vix_level":
        # 低VIX = 自滿(脆弱累積) → 危險; 這裡採「越低越危險」但極高也標記
        p=pct_rank(s,v); return 100-p   # 低百分位→高危險(自滿)
    if key=="foreign_flow":
        p=pct_rank(s,v); return 100-p   # 越賣超(低)→越危險
    if key=="trend_health":
        # 乖離過大(高)或跌破年線(負)都危險 → 取兩尾
        p=pct_rank(s,v); return max(p, 100-p) if v>0 else 80.0 if v<-5 else 60.0
    # 其餘: 越高越危險
    return pct_rank(s,v)

def light(score):
    return "red" if score>=75 else "warn" if score>=55 else "ok"

def comp_history(d, R, weights):
    """回溯歷史脆弱度分數(全樣本百分位):各指標 reindex 到指數交易日, 依方向轉危險度後加權。"""
    master=d["idx"].index
    dmat=pd.DataFrame(index=master); wsum=0.0
    for k,r in R.items():
        s=r["series"]
        if not isinstance(s,pd.Series): continue
        s=s.reindex(master.union(s.index)).sort_index().ffill().reindex(master)
        rk=s.rank(pct=True)*100
        if k in ("vix_level","foreign_flow"): rk=100-rk
        w=weights.get(k,1.0); dmat[k]=rk*w; wsum+=w
    comp=dmat.sum(axis=1)/wsum if wsum else pd.Series(dtype=float)
    return comp.dropna()

# ---------- 壓力測試 / 敏感度分析 ----------
def stress_test(d):
    """簡化的融資追繳連鎖敏感度: 假設平均維持率 ~160%, 斷頭線 130%。
    指數每跌 X%, 逼近斷頭的融資部位比例上升。給出情境估計(示意性)。"""
    margin=d.get("margin")
    cur=float(margin.iloc[-1]) if margin is not None and len(margin) else np.nan
    rows=[]
    # 假設整體融資部位維持率呈常態分佈, 均值160%, 標準差25%; 斷頭130%
    mu, sd, call=160, 25, 130
    from math import erf, sqrt
    def frac_below(threshold_ratio):
        # 指數跌 x% → 維持率約 *(1-x)/1 ... 近似: 新維持率 = 舊 *(1+r_change); 這裡用維持率直接位移
        z=(call-threshold_ratio)/sd
        return 0.5*(1+erf(z/sqrt(2)))
    for x in [5,10,15,20,25]:
        eff_mu=mu*(1-x/100)               # 指數跌 x% → 抵押品縮水, 平均維持率下移
        z=(call-eff_mu)/sd
        frac=0.5*(1+erf(z/math.sqrt(2)))  # 低於斷頭線比例
        at_risk=cur*frac if cur==cur else np.nan
        rows.append(dict(drop=x, avg_ratio=round(eff_mu), pct_call=round(frac*100,1),
                         at_risk=round(at_risk)))
    return cur, rows

# ---------- HTML ----------
# NBER 美國景氣循環衰退期(peak→trough);資料起於~2013,故實務上僅 COVID(2020)落在範圍內
NBER=[["1990-07-01","1991-03-31"],["2001-03-01","2001-11-30"],
      ["2007-12-01","2009-06-30"],["2020-02-01","2020-04-30"]]

def trend_chart(comp):
    s=comp.dropna()
    if len(s)<5: return "<div style='color:var(--muted);padding:20px'>歷史資料累積中…</div>"
    data=[[str(d.date()), round(float(v),1)] for d,v in s.items()]
    js=TREND_JS.replace("__DATA__",json.dumps(data)).replace("__REC__",json.dumps(NBER))
    return ('<div id="tc"><svg id="tcsvg"></svg><div id="tctip"></div></div>'
            '<div style="display:flex;justify-content:space-between;color:var(--muted);font-size:10.5px;margin-top:4px">'
            '<span>滑鼠移過可看當日數值</span><span>灰帶=NBER 美國衰退期 · 紅區≥75 橙區≥55(全樣本百分位)</span></div>'
            f'<script>{js}</script>')

TREND_JS=r"""(function(){
 const DATA=__DATA__, REC=__REC__;
 const svg=document.getElementById('tcsvg'), box=document.getElementById('tc'), tip=document.getElementById('tctip');
 const H=180, padL=30, padR=12, padT=12, padB=22;
 const TS=DATA.map(d=>Date.parse(d[0])); const n=DATA.length;
 let W=800, plotW=0, plotH=0;
 const X=i=>padL+(n<2?0:i/(n-1)*plotW), Y=v=>padT+(1-v/100)*plotH;
 function idxForTs(t){let lo=0,hi=n-1;if(t<=TS[0])return 0;if(t>=TS[hi])return hi;
   while(lo<hi){const m=(lo+hi)>>1;if(TS[m]<t)lo=m+1;else hi=m;}
   return (lo>0&&Math.abs(TS[lo-1]-t)<Math.abs(TS[lo]-t))?lo-1:lo;}
 function render(){
   W=box.clientWidth||800; plotW=W-padL-padR; plotH=H-padT-padB;
   svg.setAttribute('width',W); svg.setAttribute('height',H); svg.setAttribute('viewBox','0 0 '+W+' '+H);
   let g='';
   g+='<rect x="'+padL+'" y="'+Y(100)+'" width="'+plotW+'" height="'+(Y(75)-Y(100))+'" fill="var(--red)" opacity="0.08"/>';
   g+='<rect x="'+padL+'" y="'+Y(75)+'" width="'+plotW+'" height="'+(Y(55)-Y(75))+'" fill="var(--warn)" opacity="0.10"/>';
   REC.forEach(r=>{const s=Date.parse(r[0]),e=Date.parse(r[1]); if(e<TS[0]||s>TS[n-1])return;
     const xs=X(idxForTs(Math.max(s,TS[0]))), xe=X(idxForTs(Math.min(e,TS[n-1])));
     g+='<rect x="'+xs+'" y="'+padT+'" width="'+Math.max(2,xe-xs)+'" height="'+plotH+'" fill="var(--muted)" opacity="0.30"/>';
     g+='<text x="'+((xs+xe)/2)+'" y="'+(padT+10)+'" font-size="9" fill="var(--ts)" text-anchor="middle">NBER衰退</text>';});
   [75,55].forEach(v=>{g+='<line x1="'+padL+'" y1="'+Y(v)+'" x2="'+(W-padR)+'" y2="'+Y(v)+'" stroke="var(--border)" stroke-dasharray="3 3"/>'
     +'<text x="'+(padL-4)+'" y="'+(Y(v)+3)+'" font-size="9" fill="var(--muted)" text-anchor="end">'+v+'</text>';});
   let p=''; for(let i=0;i<n;i++)p+=(i?'L':'M')+X(i).toFixed(1)+' '+Y(DATA[i][1]).toFixed(1)+' ';
   g+='<path d="'+p+'" fill="none" stroke="var(--series-1)" stroke-width="1.5"/>';
   g+='<circle cx="'+X(n-1)+'" cy="'+Y(DATA[n-1][1])+'" r="3" fill="var(--series-1)"/>';
   g+='<line id="tcx" y1="'+padT+'" y2="'+(padT+plotH)+'" stroke="var(--tp)" opacity="0"/><circle id="tcd" r="3.5" fill="var(--series-1)" opacity="0"/>';
   // 年份刻度
   let lastY=null;
   for(let i=0;i<n;i++){const yr=DATA[i][0].slice(0,4); if(yr!==lastY){lastY=yr;
     if(+yr%2===0)g+='<text x="'+X(i)+'" y="'+(H-6)+'" font-size="9" fill="var(--muted)" text-anchor="middle">'+yr+'</text>';}}
   svg.innerHTML=g;
 }
 function move(ev){const rect=svg.getBoundingClientRect(); const mx=(ev.touches?ev.touches[0].clientX:ev.clientX)-rect.left;
   let i=Math.round((mx-padL)/plotW*(n-1)); i=Math.max(0,Math.min(n-1,i));
   const x=X(i),y=Y(DATA[i][1]); const xl=document.getElementById('tcx'),dot=document.getElementById('tcd');
   xl.setAttribute('x1',x);xl.setAttribute('x2',x);xl.setAttribute('opacity','0.35');
   dot.setAttribute('cx',x);dot.setAttribute('cy',y);dot.setAttribute('opacity','1');
   tip.style.opacity='1'; tip.innerHTML='<b>'+DATA[i][0]+'</b><br>脆弱度 '+DATA[i][1];
   let tx=x+12; if(tx>W-96)tx=x-96; tip.style.left=Math.max(0,tx)+'px'; tip.style.top='4px';}
 function leave(){document.getElementById('tcx').setAttribute('opacity','0');document.getElementById('tcd').setAttribute('opacity','0');tip.style.opacity='0';}
 render(); svg.addEventListener('mousemove',move); svg.addEventListener('mouseleave',leave);
 svg.addEventListener('touchmove',function(e){move(e);},{passive:true});
 let rt; window.addEventListener('resize',function(){clearTimeout(rt);rt=setTimeout(render,150);});
})();"""

def build_html(R, base, comp, stress_cur, stress_rows, asof, comp_hist=None):
    def spark(series, w=150, h=34, danger_high=True):
        s=series.dropna().iloc[-180:]
        if len(s)<3: return ""
        v=s.values; lo,hi=v.min(),v.max()
        xs=[i/(len(v)-1)*w for i in range(len(v))]
        ys=[h-2-(x-lo)/((hi-lo) or 1)*(h-4) for x in v]
        pts=" ".join(f"{'M' if i==0 else 'L'}{xs[i]:.1f} {ys[i]:.1f}" for i in range(len(v)))
        return f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" preserveAspectRatio="none"><path d="{pts}" fill="none" stroke="var(--series-1)" stroke-width="1.3"/><circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="2.2" fill="var(--series-1)"/></svg>'
    order=["margin_resid_z","margin_yoy_div","margin_roc","vix_level","vix_spike",
           "foreign_flow","fx_pressure","trend_health"]
    cards=""
    for k in order:
        if k not in R: continue
        r=R[k]; sc=danger(k,R); lt=light(sc)
        val=r["val"]; unit=r["unit"]
        disp=f"{val:+.1f}{unit}" if k in ("margin_resid_z","margin_yoy_div","vix_spike","fx_pressure","trend_health") else f"{val:.1f}{unit}"
        if k=="foreign_flow": disp=f"{val:+.0f}{unit}"
        cards+=f'''<div class="card {lt}">
<div class="ct">{r['label']}</div>
<div class="cv">{disp}</div>
<div class="cbarwrap"><div class="cbar" style="width:{sc:.0f}%"></div></div>
<div class="cp">危險度 {sc:.0f}/100</div>
<div class="cs">{spark(r['series'])}</div>
<div class="cn">{r['note']}</div></div>'''
    strows="".join(f"<tr><td>指數 −{x['drop']}%</td><td>{x['avg_ratio']}%</td><td class='r'>{x['pct_call']}%</td><td class='r'>{x['at_risk']:,} 億</td></tr>" for x in stress_rows)
    comp_light=light(comp)
    comp_txt="高危(柴火堆頂,宜降曝險)" if comp>=75 else "偏高(留意去槓桿風險)" if comp>=55 else "中性偏低"
    trend=trend_chart(comp_hist) if comp_hist is not None else ""
    return TEMPLATE.replace("__ASOF__",asof).replace("__COMP__",f"{comp:.0f}").replace("__COMPL__",comp_light).replace("__COMPTXT__",comp_txt).replace("__CARDS__",cards).replace("__STRESS__",strows).replace("__MARGIN__",f"{stress_cur:,.0f}").replace("__TREND__",trend)

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
.hero{display:flex;gap:20px;align-items:center;background:var(--surface);border:1px solid var(--border);
 border-radius:16px;padding:20px 24px;margin:18px 0}
.gauge{width:130px;height:130px;border-radius:50%;display:grid;place-items:center;flex:none;
 background:conic-gradient(var(--gc) calc(var(--v)*1%),var(--border) 0)}
.gauge .inner{width:104px;height:104px;border-radius:50%;background:var(--surface);display:grid;place-items:center;text-align:center}
.gauge .num{font-size:34px;font-weight:700;line-height:1}.gauge .lb{font-size:10px;color:var(--muted)}
.hero .txt .big{font-size:19px;font-weight:650}.hero .txt .d{color:var(--ts);font-size:13px;margin-top:5px;max-width:560px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
@media(max-width:860px){.grid{grid-template-columns:repeat(2,1fr)}}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:13px 14px}
.card.red{border-left:3px solid var(--red)}.card.warn{border-left:3px solid var(--warn)}.card.ok{border-left:3px solid var(--ok)}
.ct{font-size:12px;color:var(--ts)}.cv{font-size:23px;font-weight:680;margin:3px 0;font-variant-numeric:tabular-nums}
.cbarwrap{height:5px;background:var(--border);border-radius:3px;overflow:hidden}
.cbar{height:100%;background:var(--red)}
.card.ok .cbar{background:var(--ok)}.card.warn .cbar{background:var(--warn)}
.cp{font-size:11px;color:var(--muted);margin:4px 0 2px}.cs svg{display:block;margin:2px 0}
.cn{font-size:10.5px;color:var(--muted);line-height:1.35;margin-top:4px}
.trend{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px;margin-bottom:12px}
.trend .th{font-size:13px;color:var(--ts);margin-bottom:6px;font-weight:600}
#tc{position:relative;width:100%}#tcsvg{display:block;width:100%;cursor:crosshair}
#tctip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--border);border-radius:6px;
 padding:5px 8px;font-size:11px;line-height:1.4;opacity:0;transition:opacity .1s;box-shadow:0 2px 8px rgba(0,0,0,.18);white-space:nowrap;z-index:3}
h2{font-size:15px;margin:26px 0 10px}
table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden}
th,td{padding:9px 12px;font-size:13px;border-bottom:1px solid var(--border);text-align:left}
th{color:var(--muted);font-size:11.5px}td.r{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.note{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--series-1);border-radius:8px;
 padding:12px 15px;margin-top:18px;font-size:12px;color:var(--ts);line-height:1.6}
button{position:fixed;top:14px;right:14px;background:var(--surface);color:var(--tp);border:1px solid var(--border);border-radius:8px;padding:6px 11px;cursor:pointer}
</style></head><body><button id="th">◐</button><div class="wrap">
<h1>台股脆弱度儀表板</h1><div class="sub">資料 FinMind + FRED · 更新於 __ASOF__ · 壓力計非擇時工具 · 非投資建議</div>
<div class="hero"><div class="gauge" style="--v:__COMP__;--gc:var(--__COMPL__)"><div class="inner">
<div><div class="num">__COMP__</div><div class="lb">脆弱度 / 100</div></div></div></div>
<div class="txt"><div class="big">綜合研判:__COMPTXT__</div>
<div class="d">分數由下列各燈號的歷史百分位加權合成。分數高≠馬上崩,而是「柴火堆高、系統脆弱」——用來事前降曝險,不用來擇時。</div></div></div>
<div class="trend"><div class="th">脆弱度歷史趨勢</div>__TREND__</div>
<div class="grid">__CARDS__</div>
<h2>壓力測試 / 敏感度分析(融資追繳連鎖,示意性)</h2>
<table><thead><tr><th>情境</th><th>估計平均維持率</th><th>逼近斷頭比例</th><th>潛在追繳部位</th></tr></thead>
<tbody>__STRESS__</tbody></table>
<div class="note"><b>方法與限制:</b>各指標一律轉成「歷史百分位」再合成,以適應結構性變化(避免固定門檻失真)。融資背離採<b>去趨勢殘差</b>與<b>成長率背離(vs 指數)</b>雙軌——刻意<b>不</b>用「融資/指數」比率,因該比率在指數同步噴高時會被分母污染而低估風險(2026/7 即為一例)。壓力測試假設整體融資維持率約常態(均值160%、斷頭130%)推估,僅為敏感度示意,非精算。VIX 為美國 VIX(全球風險代理);低 VIX + 高槓桿的組合最危險(自滿),故低 VIX 計入脆弱度。目前融資餘額約 __MARGIN__ 億。<b>本儀表板為風險框架,非投資建議。</b></div>
</div><script>
const b=document.getElementById('th'),r=document.documentElement;
b.onclick=()=>r.setAttribute('data-theme',r.getAttribute('data-theme')==='dark'?'light':'dark');
</script></body></html>"""

def main():
    d=get_data()
    if "margin" not in d or "idx" not in d:
        print("資料抓取失敗(可能遇速率限制),稍後重試"); sys.exit(1)
    R, base=compute(d)
    # 合成分數(加權)
    weights={"margin_resid_z":1.4,"margin_yoy_div":1.3,"margin_roc":1.0,"vix_level":0.9,
             "vix_spike":0.7,"foreign_flow":1.1,"fx_pressure":0.8,"trend_health":1.0}
    num=den=0
    for k in R:
        w=weights.get(k,1.0); num+=danger(k,R)*w; den+=w
    comp=num/den if den else 0
    ch=comp_history(d,R,weights)
    stress_cur, stress_rows=stress_test(d)
    asof=str(d["idx"].index[-1].date())
    html=build_html(R,base,comp,stress_cur,stress_rows,asof,comp_hist=ch)
    open(OUT_HTML,"w").write(html)
    # 追加歷史
    row={"date":asof,"composite":round(comp,1)}
    for k in R: row[k]=round(R[k]["val"],3)
    hist=pd.DataFrame([row])
    if os.path.exists(HIST_CSV):
        old=pd.read_csv(HIST_CSV); hist=pd.concat([old[old["date"]!=asof],hist],ignore_index=True)
    hist.to_csv(HIST_CSV,index=False)
    print(f"OK  {asof}  脆弱度={comp:.0f}/100  → {OUT_HTML}")
    for k in R: print(f"   {R[k]['label']:16} {R[k]['val']:+.2f}{R[k]['unit']}  危險度 {danger(k,R):.0f}")

if __name__=="__main__":
    main()
