#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股脆弱度儀表板 (每日更新, 互動版) — S&P 500 / Nasdaq / 費城半導體(SOX)
=====================================================================
台股版 fragility_dashboard.py 的美股姊妹版。沿用同一套方法論:
  * 各指標一律轉為 PIT 擴張百分位(第 t 日只用當日及以前的分佈) → 無前視偏誤
  * 內部(槓桿/趨勢) vs 外部(波動/信用/金融環境) 分組, 加權合成 0–100 脆弱度
  * 同時進紅區項數 6→10 的高壓叢集標示、共用釘選游標、個股 K 線 + 月/季/半年線

資料源(皆免費, 無需金鑰):
  FRED   : SP500 / NASDAQCOM / VIXCLS / NFCI / BAMLH0A0HYM2(高收益債利差) / GDP(名目, 季)
  FINRA  : margin-statistics.xlsx — 全市場融資餘額(Debit Balances), 月頻, 1997 至今
  stockanalysis.com : SOXX ETF(SOX 代理) 與個股/ETF 日線(還原價, 支援 CORS)

用法:
  pip install requests pandas numpy openpyxl
  python3 us_fragility_dashboard.py      # 產生 us.html + 追加 us_fragility_history.csv
"""
import os, sys, io, json, math, time
import requests, pandas as pd, numpy as np

OUT_HTML=os.environ.get("US_OUT_HTML","us.html")
HIST_CSV="us_fragility_history.csv"
START="2012-01-01"
UA={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

INVERT={"vix_level","nfci","hy_oas","credit_env"}    # 越低越危險(自滿 / 信用過鬆)
# ── 雙層架構(2026-07 依回測改版,與台股版一致)────────────────────────
# 慢層 SLOW:結構脆弱度 =「萬一出事會多慘」→ 只收槓桿上限, 不當出場訊號。
# 快層 TRIGGER:「已經開始了」→ 真正降到 1x 的觸發(VIX跳升 / 跌破200日線)。
# 動能 MOMO:乖離類。以本頁 2018–2026 樣本測其危險度 vs 未來60日最大回撤,
#   Spearman IC 全為負(費半 −0.136、Nasdaq −0.109、S&P −0.053):
#   漲多預測的是後續回撤較淺而非較深, 計分會誤殺仍在噴出的標的, 故移出計分。
SLOW={"margin_resid_z","margin_yoy_div","margin_roc","vix_level","nfci","hy_oas","credit_env"}
TRIGGER={"vix_spike"}                      # 另有「跌破200日線」由 sp_trend 值判定
MOMO={"sp_trend","ndx_trend","sox_trend"}
GROUP={**{k:"內部槓桿" for k in ("margin_resid_z","margin_yoy_div","margin_roc")},
       **{k:"外部資金情緒" for k in ("vix_level","nfci","hy_oas","credit_env")},
       **{k:"觸發層" for k in TRIGGER},
       **{k:"動能參考" for k in MOMO}}
TRIG_PCT=85
INTERNAL=SLOW
WEIGHTS={"margin_resid_z":1.4,"margin_yoy_div":1.3,"margin_roc":1.0,
         "sp_trend":1.0,"ndx_trend":0.9,"sox_trend":1.0,
         "vix_level":0.9,"vix_spike":0.7,"nfci":1.1,"hy_oas":0.9,"credit_env":0.9}
FMT={"margin_resid_z":[1,1,"σ"],"margin_yoy_div":[1,1,"pp"],"margin_roc":[0,1,"%"],
     "sp_trend":[1,1,"%"],"ndx_trend":[1,1,"%"],"sox_trend":[1,1,"%"],
     "vix_level":[0,1,""],"vix_spike":[1,1,""],"nfci":[1,2,""],"hy_oas":[0,2,"%"],"credit_env":[1,2,""]}
ORDER=["margin_resid_z","margin_yoy_div","margin_roc","sp_trend","ndx_trend","sox_trend",
       "vix_level","vix_spike","nfci","hy_oas","credit_env"]
NBER=[["1990-07-01","1991-03-31"],["2001-03-01","2001-11-30"],
      ["2007-12-01","2009-06-30"],["2020-02-01","2020-04-30"]]

# ---------------- 抓取層 ----------------
CACHE_DIR=os.environ.get("US_CACHE_DIR","us_cache")
CACHE_MAX_AGE=float(os.environ.get("US_CACHE_HOURS","12"))*3600   # 快取新鮮度(小時)

def _log(*a): print(*a,flush=True)

def _cache_path(name):
    os.makedirs(CACHE_DIR,exist_ok=True); return os.path.join(CACHE_DIR,name)

def _cache_fresh(p):
    return os.path.exists(p) and (time.time()-os.path.getmtime(p))<CACHE_MAX_AGE

FAIL_TTL=float(os.environ.get("US_FAIL_TTL_MIN","45"))*60   # 失敗後多久內不再重試(避免被節流時空轉)

def _recent_fail(name):
    p=_cache_path(f".fail_{name}")
    return os.path.exists(p) and (time.time()-os.path.getmtime(p))<FAIL_TTL

def _mark_fail(name):
    try: open(_cache_path(f".fail_{name}"),"w").write(str(time.time()))
    except Exception: pass

def fred(series, cosd=START):
    """FRED CSV,附磁碟快取 + 失敗退避(被節流時直接走替代來源,不空轉)。"""
    p=_cache_path(f"fred_{series}.csv")
    if not _cache_fresh(p) and _recent_fail(series):
        _log(f"   [skip]  {series}(近期抓取失敗,暫時改用替代來源)")
        return pd.Series(dtype=float)
    if _cache_fresh(p):
        try:
            df=pd.read_csv(p); df.columns=["date","val"]
            df["date"]=pd.to_datetime(df["date"]); df["val"]=pd.to_numeric(df["val"],errors="coerce")
            s=df.dropna().set_index("date")["val"]
            if len(s): _log(f"   [cache] {series} rows={len(s)}"); return s
        except Exception: pass
    for attempt in range(3):
        try:
            r=requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={cosd}",
                           headers=UA,timeout=(10,25))
            df=pd.read_csv(io.StringIO(r.text)); df.columns=["date","val"]
            df["date"]=pd.to_datetime(df["date"]); df["val"]=pd.to_numeric(df["val"],errors="coerce")
            s=df.dropna().set_index("date")["val"]
            if len(s):
                try: open(p,"w").write(r.text)
                except Exception: pass
                _log(f"   [net]   {series} rows={len(s)}")
                return s
        except Exception as e:
            _log(f"   [retry] {series} #{attempt+1} {repr(e)[:60]}")
        time.sleep(2)
    if os.path.exists(p):      # 抓不到就退回舊快取
        try:
            df=pd.read_csv(p); df.columns=["date","val"]
            df["date"]=pd.to_datetime(df["date"]); df["val"]=pd.to_numeric(df["val"],errors="coerce")
            s=df.dropna().set_index("date")["val"]
            _log(f"   [stale] {series} rows={len(s)}"); return s
        except Exception: pass
    _mark_fail(series)
    _log(f"   [FAIL]  {series}")
    return pd.Series(dtype=float)

def sa_history(sym, kind="e", rng="10Y"):
    """stockanalysis.com 日線(還原價 a)。回傳 DataFrame[date, o,h,l,c,a,v]。附磁碟快取。"""
    p=_cache_path(f"sa_{sym}.json"); d=None
    if _cache_fresh(p):
        try: d=json.load(open(p)); _log(f"   [cache] {sym} rows={len(d)}")
        except Exception: d=None
    if d is None:
        for attempt in range(3):
            try:
                r=requests.get(f"https://stockanalysis.com/api/symbol/{kind}/{sym}/history?range={rng}",
                               headers=UA,timeout=(10,30))
                d=r.json().get("data") or []
                if d:
                    try: json.dump(d,open(p,"w"))
                    except Exception: pass
                    _log(f"   [net]   {sym} rows={len(d)}"); break
            except Exception as e:
                _log(f"   [retry] {sym} #{attempt+1} {repr(e)[:60]}")
            time.sleep(2)
    if not d and os.path.exists(p):
        try: d=json.load(open(p)); _log(f"   [stale] {sym} rows={len(d)}")
        except Exception: d=None
    if not d: return pd.DataFrame()
    df=pd.DataFrame(d).rename(columns={"t":"date"})
    df["date"]=pd.to_datetime(df["date"])
    for c in ["o","h","l","c","a","v"]:
        if c in df: df[c]=pd.to_numeric(df[c],errors="coerce")
    return df.sort_values("date").set_index("date")

def cboe_vix():
    """Cboe 官方 VIX 日線(1990 至今),不依賴 FRED。"""
    p=_cache_path("cboe_vix.csv")
    try:
        if _cache_fresh(p): txt=open(p).read(); _log("   [cache] Cboe VIX")
        else:
            r=requests.get("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
                           headers=UA,timeout=(10,40)); txt=r.text
            if len(txt)>1000: open(p,"w").write(txt); _log("   [net]   Cboe VIX")
        df=pd.read_csv(io.StringIO(txt))
        df["DATE"]=pd.to_datetime(df["DATE"],format="%m/%d/%Y",errors="coerce")
        s=df.dropna(subset=["DATE"]).set_index("DATE")["CLOSE"].astype(float).sort_index()
        return s[s.index>=pd.Timestamp(START)]
    except Exception as e:
        _log(f"   [FAIL]  Cboe VIX {repr(e)[:60]}"); return pd.Series(dtype=float)

def chicagofed_nfci():
    """芝加哥聯準會 NFCI(週頻)含 Credit / Leverage 子指數,不依賴 FRED。"""
    p=_cache_path("nfci.csv")
    try:
        if _cache_fresh(p): txt=open(p).read(); _log("   [cache] ChicagoFed NFCI")
        else:
            r=requests.get("https://www.chicagofed.org/-/media/publications/nfci/nfci-data-series-csv.csv",
                           headers=UA,timeout=(10,40)); txt=r.text
            if len(txt)>1000: open(p,"w").write(txt); _log("   [net]   ChicagoFed NFCI")
        df=pd.read_csv(io.StringIO(txt))
        dc=df.columns[0]
        df[dc]=pd.to_datetime(df[dc],format="%m/%d/%Y",errors="coerce")
        df=df.dropna(subset=[dc]).set_index(dc).sort_index()
        out={}
        for col,key in [("NFCI","nfci"),("Credit","credit"),("Leverage","leverage")]:
            if col in df: out[key]=pd.to_numeric(df[col],errors="coerce").dropna()
        return out
    except Exception as e:
        _log(f"   [FAIL]  ChicagoFed NFCI {repr(e)[:60]}"); return {}

def worldbank_gdp_yoy(cap_year):
    """世界銀行 美國名目GDP(年頻)年增率,僅取 <= cap_year 的已實現年份(PIT)。"""
    p=_cache_path("wb_gdp.json")
    try:
        if _cache_fresh(p): j=json.load(open(p)); _log("   [cache] WorldBank GDP")
        else:
            r=requests.get("https://api.worldbank.org/v2/country/US/indicator/NY.GDP.MKTP.CD?format=json&per_page=80",
                           headers=UA,timeout=(10,30)); j=r.json()
            json.dump(j,open(p,"w")); _log("   [net]   WorldBank GDP")
        lvl={int(x["date"]):float(x["value"]) for x in j[1] if x.get("value") is not None}
        return {y:(lvl[y]/lvl[y-1]-1)*100 for y in lvl if (y-1) in lvl and lvl[y-1]>0 and y<=cap_year} or None
    except Exception as e:
        _log(f"   [FAIL]  WorldBank GDP {repr(e)[:60]}"); return None

def finra_margin():
    """FINRA 全市場融資餘額(Debit Balances in Customers' Securities Margin Accounts), 月頻(百萬美元)。
    回傳 (Series[月底 -> 餘額], Series[月底 -> 自由信用餘額合計])。"""
    url="https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
    p=_cache_path("finra_margin.xlsx")
    try:
        if _cache_fresh(p):
            content=open(p,"rb").read(); _log("   [cache] FINRA margin xlsx")
        else:
            r=requests.get(url,headers=UA,timeout=(10,60)); content=r.content
            open(p,"wb").write(content); _log("   [net]   FINRA margin xlsx")
        df=pd.read_excel(io.BytesIO(content))
        cols=list(df.columns)
        ym=cols[0]; debit=cols[1]
        df[ym]=df[ym].astype(str).str.strip()
        df=df[df[ym].str.match(r"^\d{4}-\d{2}$")].copy()
        idx=pd.PeriodIndex(df[ym],freq="M").to_timestamp(how="end").normalize()
        deb=pd.Series(pd.to_numeric(df[debit],errors="coerce").values,index=idx).sort_index().dropna()
        cred=None
        if len(cols)>=4:
            cc=pd.to_numeric(df[cols[2]],errors="coerce").fillna(0)+pd.to_numeric(df[cols[3]],errors="coerce").fillna(0)
            cred=pd.Series(cc.values,index=idx).sort_index().dropna()
        return deb, cred
    except Exception as e:
        print("   (FINRA 融資餘額抓取失敗:%s)"%repr(e)[:70])
        return pd.Series(dtype=float), None

def pit_monthly_to_daily(mser, master, pub_lag_days=25):
    """把月頻資料以『發布落後』對齊到日頻:第 t 日只看得到 (月底 + pub_lag) <= t 的月份。"""
    if mser is None or not len(mser): return pd.Series(index=master,dtype=float)
    avail=pd.Series(mser.values,index=mser.index+pd.Timedelta(days=pub_lag_days)).sort_index()
    return avail.reindex(avail.index.union(master)).sort_index().ffill().reindex(master)

def gdp_yoy_pit(master, pub_lag_days=30):
    """美國名目 GDP 年增率, 依發布落後對齊 → PIT 無前視。
    首選 FRED GDP(季頻 SAAR);不可得時退回世界銀行年頻(僅取已實現年份)。回傳 (Series, 來源說明)。"""
    g=fred("GDP","2009-01-01")
    if len(g):
        yoy=((g/g.shift(4)-1)*100).dropna()
        if len(yoy):
            qend=pd.Index([(x+pd.offsets.QuarterEnd(0)) for x in yoy.index])
            avail=pd.Series(yoy.values,index=qend+pd.Timedelta(days=pub_lag_days)).sort_index()
            return avail.reindex(avail.index.union(master)).sort_index().ffill().reindex(master), "FRED 名目GDP(季頻,發布落後30天)"
    wb=worldbank_gdp_yoy(int(master[-1].year)-1)      # 年頻退路:只用前一年已實現值
    if wb:
        years=sorted(wb); vals=[]
        for y in master.year:
            ks=[k for k in years if k<=y-1]
            vals.append(wb[ks[-1]] if ks else np.nan)
        return pd.Series(vals,index=master,dtype=float).ffill(), "世界銀行 名目GDP(年頻,僅取已實現年份)"
    return None, None

def get_data():
    """多來源抓取:FRED 為首選,不可得時自動退回獨立來源(Cboe / 芝加哥聯準會 / ETF 代理 / 世界銀行)。
    d['src'] 記錄每個序列實際採用的來源, 會標示在頁面上。"""
    d={"src":{}}
    _log("抓取 VIX(Cboe 官方) …")
    d["vix"]=cboe_vix(); d["src"]["vix"]="Cboe 官方"
    if not len(d["vix"]):
        d["vix"]=fred("VIXCLS"); d["src"]["vix"]="FRED VIXCLS"
    _log("抓取 NFCI(芝加哥聯準會) …")
    nf=chicagofed_nfci()
    d["nfci"]=nf.get("nfci",pd.Series(dtype=float)); d["nfci_credit"]=nf.get("credit")
    d["src"]["nfci"]="芝加哥聯準會"
    if not len(d["nfci"]):
        d["nfci"]=fred("NFCI"); d["src"]["nfci"]="FRED NFCI"
    _log("抓取 指數:S&P500 / Nasdaq …")
    sp=fred("SP500"); d["src"]["sp500"]="FRED SP500(指數)"
    if not len(sp):
        x=sa_history("SPY","e","10Y")
        sp=x["a"] if len(x) else pd.Series(dtype=float); d["src"]["sp500"]="SPY ETF 還原價(代理)"
    d["sp500"]=sp
    nq=fred("NASDAQCOM"); d["src"]["nasdaq"]="FRED NASDAQCOM(指數)"
    if not len(nq):
        x=sa_history("ONEQ","e","10Y")
        nq=x["a"] if len(x) else pd.Series(dtype=float); d["src"]["nasdaq"]="ONEQ ETF 還原價(那斯達克綜合指數代理)"
    d["nasdaq"]=nq
    _log("抓取 SOXX(費半 SOX 代理) …")
    sx=sa_history("SOXX","e","10Y")
    d["sox"]=sx["a"] if len(sx) else pd.Series(dtype=float); d["src"]["sox"]="iShares SOXX ETF 還原價(代理)"
    _log("抓取 高收益債利差 …")
    hy=fred("BAMLH0A0HYM2")
    if len(hy): d["hyoas"]=hy; d["src"]["credit"]="FRED ICE BofA HY OAS"
    else:
        d["hyoas"]=pd.Series(dtype=float)
        d["src"]["credit"]="芝加哥聯準會 NFCI-Credit 子指數(HY OAS 不可得時替代)"
    _log("抓取 FINRA 融資餘額 …")
    deb,cred=finra_margin(); d["margin_m"]=deb; d["credit_m"]=cred
    d["src"]["margin"]="FINRA Margin Statistics(月頻)"
    _log(f"   SP500={len(d['sp500'])} NASDAQ={len(d['nasdaq'])} SOX={len(d['sox'])} VIX={len(d['vix'])} "
         f"NFCI={len(d['nfci'])} HY={len(d['hyoas'])} FINRA={len(deb)}")
    return d

# ---------------- 指標 ----------------
def expanding_resid_z(y, x, min_obs=252):
    """擴張視窗迴歸 y ~ x + t + 1 的『當期殘差 z』。
    第 t 期的係數、殘差平均與標準差,全部只用第 0..t 期資料(遞迴最小平方)→ 無前視偏誤。
    以累積交叉乘積遞推, 每期只解一個 3x3 方程, 成本 O(n)。"""
    n=len(y); out=np.full(n,np.nan)
    k=3
    A=np.zeros((k,k)); bvec=np.zeros(k)
    rs=0.0; rss=0.0; rn=0                      # 殘差的累積和/平方和(用於擴張標準化)
    for i in range(n):
        xi=np.array([x[i], float(i), 1.0])
        A+=np.outer(xi,xi); bvec+=xi*y[i]
        if i+1<min_obs: continue
        try:
            beta=np.linalg.solve(A+np.eye(k)*1e-8, bvec)
        except np.linalg.LinAlgError:
            continue
        r=y[i]-xi@beta                          # 當期殘差(係數僅用到今天)
        rs+=r; rss+=r*r; rn+=1
        if rn>30:
            mu=rs/rn; var=max(rss/rn-mu*mu,1e-12)
            out[i]=(r-mu)/math.sqrt(var)
    return out

def compute(d, master):
    R={}
    sp=d["sp500"].reindex(master).ffill()
    nq=d["nasdaq"].reindex(master).ffill()
    sox=d["sox"].reindex(master).ffill()
    mg=pit_monthly_to_daily(d["margin_m"],master)          # 融資餘額(PIT 發布落後)
    # 1) 融資超額水位:log(margin) ~ log(S&P) + 時間趨勢 的殘差 z
    #    ★ 擴張視窗迴歸:第 t 日的迴歸係數與 z 標準化都只用「第 t 日及以前」的資料,
    #      不是全樣本一次迴歸(全樣本會把未來資訊洩漏進過去的殘差 → 前視偏誤)。
    base=pd.DataFrame({"m":mg,"sp":sp}).dropna()
    if len(base)>400:
        z=expanding_resid_z(np.log(base["m"].values), np.log(base["sp"].values), min_obs=252)
        z=pd.Series(z,index=base.index).dropna()
        if len(z)>200:
            R["margin_resid_z"]=dict(val=float(z.iloc[-1]),series=z,unit="σ",label="融資超額水位",
                note="去趨勢後融資多出幾個σ(FINRA月頻)")
    # 2) 融資成長背離 vs 名目 GDP
    if len(base)>300:
        m_yoy=mg.pct_change(252)*100
        gy,gsrc=gdp_yoy_pit(master)
        if gy is not None and gy.notna().sum()>200:
            div=(m_yoy-gy).dropna()
            lbl="融資成長背離(vs名目GDP)"
            note="融資年增 − 名目GDP年增(PIT對齊)"
            d.setdefault("src",{})["gdp"]=gsrc
        else:
            div=(m_yoy-(sp.pct_change(252)*100)).dropna()
            lbl="融資成長背離(vs S&P)"
            note="融資年增率 − S&P500 年增率(GDP源不可得時退回)"
        if len(div)>200:
            R["margin_yoy_div"]=dict(val=float(div.iloc[-1]),series=div,unit="pp",label=lbl,note=note)
    # 3) 融資半年擴張
    roc=(mg.pct_change(126)*100).dropna()
    if len(roc)>200:
        R["margin_roc"]=dict(val=float(roc.iloc[-1]),series=roc,unit="%",label="融資半年擴張",
            note="FINRA 融資餘額近半年變化")
    # 4~6) 三大指數距 200 日均線
    for key,ser,lab,nt in [
        ("sp_trend",sp,"S&P500 距200日線","距200日均(不計分)"),
        ("ndx_trend",nq,"Nasdaq 距200日線","距200日均(不計分)"),
        ("sox_trend",sox,"費半(SOXX)距200日線","距200日均·AI熱度(不計分)")]:
        s=ser.dropna()
        if len(s)>210:
            dev=(s/s.rolling(200).mean()-1)*100
            dev=dev.dropna()
            R[key]=dict(val=float(dev.iloc[-1]),series=dev,unit="%",label=lab,note=nt)
    # 7~8) VIX
    vx=d["vix"].reindex(master).ffill().dropna()
    if len(vx)>30:
        R["vix_level"]=dict(val=float(vx.iloc[-1]),series=vx,unit="",label="VIX 波動度",
            note="低=自滿(反向計分)")
        R["vix_spike"]=dict(val=float(vx.iloc[-1]-vx.iloc[-6]),series=vx.diff(5),unit="",
            label="VIX 5日跳升",note="正跳升=避險轉向")
    # 9) NFCI 金融環境
    nf=d["nfci"]
    if len(nf)>60:
        nfd=nf.reindex(nf.index.union(master)).sort_index().ffill().reindex(master).dropna()
        R["nfci"]=dict(val=float(nfd.iloc[-1]),series=nfd,unit="",label="金融環境鬆緊(NFCI)",
            note="週頻;負=寬鬆(反向計分)")
    # 10) 信用面:高收益債利差(首選) / NFCI-Credit 子指數(退路)
    hy=d.get("hyoas",pd.Series(dtype=float))
    if len(hy)>120:
        hyd=hy.reindex(hy.index.union(master)).sort_index().ffill().reindex(master).dropna()
        R["hy_oas"]=dict(val=float(hyd.iloc[-1]),series=hyd,unit="%",label="高收益債利差",
            note="利差過窄=信用自滿(反向計分)")
    else:
        cr=d.get("nfci_credit")
        if cr is not None and len(cr)>60:
            crd=cr.reindex(cr.index.union(master)).sort_index().ffill().reindex(master).dropna()
            R["credit_env"]=dict(val=float(crd.iloc[-1]),series=crd,unit="",label="信用環境(NFCI-Credit)",
                note="週頻;負=信用過鬆(反向計分)")
    return R

PIT_WARMUP=252
def pit_pct(vals, invert=False, min_periods=PIT_WARMUP):
    """PIT 擴張百分位:第 t 日的危險度只用「第 t 日及以前」的分佈計算,絕不偷看未來。"""
    import bisect
    out=[]; hist=[]
    for v in vals:
        if v is None or (isinstance(v,float) and math.isnan(v)):
            out.append(None); continue
        bisect.insort(hist, v)
        if len(hist) < min_periods:
            out.append(None); continue
        r = bisect.bisect_right(hist, v)/len(hist)*100.0
        out.append(100.0-r if invert else r)
    return out

def build_app_data(R, master, _SP=None):
    aligned={}; dmat=pd.DataFrame(index=master)
    for k,r in R.items():
        s=r["series"]
        if not isinstance(s,pd.Series): continue
        sa=s.reindex(master.union(s.index)).sort_index().ffill().reindex(master)
        dvals=pit_pct(sa.values, invert=(k in INVERT))
        aligned[k]=dict(label=r["label"],unit=r["unit"],note=r["note"],fmt=FMT.get(k,[0,1,r["unit"]]),
            group=GROUP.get(k,"外部資金情緒"),
            val=[None if pd.isna(x) else round(float(x),2) for x in sa.values],
            dng=[None if x is None else int(round(x)) for x in dvals])
        if k in SLOW:                      # ★ 只有慢層計入結構脆弱度
            dmat[k]=pd.Series(dvals,index=master,dtype="float64")*WEIGHTS.get(k,1.0)
    cols=list(dmat.columns); wvec=np.array([WEIGHTS.get(k,1.0) for k in cols])
    present=dmat.notna()
    wsum_row=(present.values*wvec).sum(axis=1)
    with np.errstate(invalid='ignore',divide='ignore'):
        comp_vals=np.nansum(dmat.values,axis=1)/np.where(wsum_row>0,wsum_row,np.nan)
    need=max(4,int(0.8*len(cols)))
    comp_vals=np.where(present.sum(axis=1).values>=need, comp_vals, np.nan)
    comp=pd.Series(comp_vals,index=master); mask=comp.notna()
    dates=[str(x.date()) for x in master[mask]]
    inds={k:{**v,"val":[v["val"][i] for i in range(len(mask)) if mask.iloc[i]],
                    "dng":[v["dng"][i] for i in range(len(mask)) if mask.iloc[i]]} for k,v in aligned.items()}
    def _col(k,f):
        if k not in aligned: return [None]*int(mask.sum())
        src=aligned[k][f]; return [src[i] for i in range(len(mask)) if mask.iloc[i]]
    _ix=_SP.reindex(master).ffill()
    _ma60=_ix.rolling(60).mean(); _br=[1 if (a is not None and b is not None and a<b) else 0
        for a,b in zip(_ix.values,_ma60.values)]
    _r20=((_ix/_ix.shift(20)-1)*100)
    ma60br=[_br[i] for i in range(len(mask)) if mask.iloc[i]]
    r20=[None if pd.isna(x) else round(float(x),2) for x in _r20.values]
    r20=[r20[i] for i in range(len(mask)) if mask.iloc[i]]
    vs=_col("vix_spike","dng"); th=_col("sp_trend","val")
    trig_vix=[1 if (x is not None and x>=TRIG_PCT) else 0 for x in vs]
    trig_ma =[1 if (x is not None and x<0) else 0 for x in th]
    trig=[1 if (a or b) else 0 for a,b in zip(trig_vix,trig_ma)]
    return dict(dates=dates, comp=[round(float(x),1) for x in comp[mask].values],
                inds=inds, order=[k for k in ORDER if k in inds],
                trig=trig, trigvix=trig_vix, trigma=trig_ma, trigpct=TRIG_PCT,
                ma60br=ma60br, r20=r20,
                slow=[k for k in ORDER if k in inds and k in SLOW],
                momo=[k for k in ORDER if k in inds and k in MOMO])

def hist_table(app, d, master):
    """歷史對照(樣本內描述統計):脆弱度分數區間 → S&P500 未來 20/60/120 日報酬。"""
    sp=d["sp500"].reindex(master).ffill()
    dates=pd.to_datetime(app["dates"]); comp=pd.Series(app["comp"],index=dates)
    px=sp.reindex(dates).ffill()
    rows=[]
    buckets=[(0,40,"< 40 低壓"),(40,55,"40–55"),(55,70,"55–70"),(70,85,"70–85"),(85,101,"≥ 85 極高壓")]
    for lo,hi,lab in buckets:
        m=(comp>=lo)&(comp<hi)
        if m.sum()<20: rows.append((lab,int(m.sum()),None,None,None)); continue
        out=[]
        for H in (20,60,120):
            fwd=(px.shift(-H)/px-1)*100
            v=fwd[m].dropna()
            out.append((float(np.median(v)),float(np.percentile(v,5))) if len(v)>10 else None)
        rows.append((lab,int(m.sum()),out[0],out[1],out[2]))
    html=""
    for lab,n,a20,a60,a120 in rows:
        def cell(x):
            if x is None: return "<td class='r'>–</td>"
            med,p5=x
            col="var(--red)" if med<0 else ("var(--ok)" if med>3 else "var(--ts)")
            return f"<td class='r'><b style='color:{col}'>{med:+.1f}%</b><br><span style='font-size:10px;color:var(--muted)'>最差5% {p5:+.1f}%</span></td>"
        html+=f"<tr><td>{lab}</td><td class='r'>{n:,}</td>{cell(a20)}{cell(a60)}{cell(a120)}</tr>"
    return html

def build_html(app, asof, tbl, marg_txt, src):
    js=APP_JS.replace("__APPDATA__",json.dumps(app,ensure_ascii=False)).replace("__REC__",json.dumps(NBER))
    srctxt=" · ".join(f"{k}={v}" for k,v in [
        ("S&P500",src.get("sp500")),("Nasdaq",src.get("nasdaq")),("費半",src.get("sox")),
        ("VIX",src.get("vix")),("金融環境",src.get("nfci")),("信用",src.get("credit")),
        ("融資",src.get("margin")),("GDP錨",src.get("gdp"))] if v)
    return (TEMPLATE.replace("__ASOF__",asof).replace("__HIST__",tbl)
            .replace("__MARGIN__",marg_txt).replace("__SRC__",srctxt).replace("__APPJS__",js))

TEMPLATE=r"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>美股脆弱度儀表板</title>
<link rel="manifest" href="manifest.webmanifest"><meta name="theme-color" content="#111110">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="美股脆弱度"><link rel="apple-touch-icon" href="icon-192.png">
<style>
:root{--bg:#f4f4f2;--surface:#fcfcfb;--border:#e2e2dd;--tp:#0b0b0b;--ts:#52514e;--muted:#8a8981;
 --series-1:#2a78d6;--ok:#0f9d63;--warn:#eda100;--red:#e34948;color-scheme:light}
:root[data-theme=dark]{--bg:#111110;--surface:#1a1a19;--border:#33332f;--tp:#fff;--ts:#c3c2b7;--muted:#8f8e85;
 --series-1:#3987e5;--ok:#25b878;--warn:#e0a83a;--red:#e66767;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tp);
 font-family:-apple-system,"PingFang TC","Microsoft JhengHei",Segoe UI,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:24px 18px 70px}
h1{font-size:21px;margin:0 0 3px}.sub{color:var(--ts);font-size:12.5px}
.xlink{display:inline-block;margin-top:8px;font-size:12px;color:var(--series-1);text-decoration:none;border:1px solid var(--border);border-radius:7px;padding:4px 10px}
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
details.note summary::-webkit-details-marker{color:var(--muted)}
.tabs{display:flex;gap:6px;margin:10px 0 4px;flex-wrap:wrap}
.tabs a{font-size:12.5px;text-decoration:none;color:var(--ts);background:var(--surface);border:1px solid var(--border);border-radius:20px;padding:5px 13px}
.tabs a.on{background:var(--s1,var(--series-1));color:#fff;border-color:var(--s1,var(--series-1))}
#th{position:fixed;top:12px;right:12px;background:var(--surface);color:var(--tp);border:1px solid var(--border);border-radius:8px;padding:6px 11px;cursor:pointer}
</style></head><body><button id="th">◐</button><div class="wrap">
<h1>美股脆弱度儀表板 <span style="font-size:11px;color:var(--ok);border:1px solid var(--border);border-radius:6px;padding:1px 6px">PIT 無前視偏誤</span></h1>
<div class="sub">S&amp;P 500 · Nasdaq · 費城半導體(SOX) — 資料 FRED + FINRA + stockanalysis · 更新於 __ASOF__ · 危險度=PIT擴張百分位(只用當日及以前) · 壓力計非擇時工具 · 非投資建議</div>
<nav class="tabs"><a href="index.html">台股脆弱度</a><a href="us.html" class="on">美股脆弱度</a><a href="industry_heat.html">產業熱度雷達</a></nav>
<div class="hero"><div class="gauge" id="gauge"><div class="inner"><div><div class="num" id="cnum">–</div><div class="lb">脆弱度 / 100</div></div></div></div>
 <div class="txt"><div class="big" id="cjudge">–</div>
 <div class="d">慢層<b>結構脆弱度</b>定槓桿上限,快層<b>觸發</b>(VIX跳升/跌破均線)才真的降到 1x。脆弱但未觸發=不加碼、不出場。</div>
 <div class="vd" id="viewdate">游標移過同步各燈號;點按固定、雙擊解除</div>
 <div class="vd" id="rednow" style="color:var(--red);font-weight:650"></div></div></div>
<div class="trend"><div class="th"><span>個股 / ETF 搜尋 <span style="font-weight:400;color:var(--muted)">代號或公司名</span></span></div>
 <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
  <input id="q" list="stocklist" placeholder="例:NVDA 或 Nvidia" style="font:inherit;background:var(--bg);color:var(--tp);border:1px solid var(--border);border-radius:8px;padding:7px 11px;min-width:220px">
  <datalist id="stocklist"></datalist>
  <button id="qbtn" style="font:inherit;background:var(--series-1);color:#fff;border:0;border-radius:8px;padding:7px 14px;cursor:pointer">查詢</button>
  <span id="qmsg" style="font-size:12px;color:var(--muted)"></span></div>
 <div id="scwrap" style="display:none;margin-top:10px"><div id="sctitle" style="font-size:14px;font-weight:650;margin-bottom:4px"></div>
  <div class="ctrl" id="scranges" style="margin:0 0 6px"><button data-d="252">1年</button><button data-d="756">3年</button><button data-d="1260">5年</button><button data-d="0" class="on">全部</button>
   <span style="color:var(--muted);font-size:11px">(日期範圍見下方脆弱度圖,兩圖共用)</span></div>
  <div id="scstats" class="scstats"></div>
  <div id="scmnote" style="font-size:10.5px;color:var(--muted);margin-top:4px"></div>
  <div id="scsuit" style="font-size:12.5px;margin-top:8px;padding:8px 11px;background:var(--bg);border:1px solid var(--border);border-radius:9px"></div>
  <div id="sc" style="position:relative"><svg id="scsvg"></svg><div id="sctip"></div></div>
  <div style="display:flex;justify-content:space-between;color:var(--muted);font-size:10.5px;margin-top:2px"><span id="scstart"></span><span>還原股價(對數)· K線綠漲紅跌 · 背景=剎車狀態 · 兩圖共用游標/縮放</span><span id="scend"></span></div></div></div>
<div class="trend"><div class="th"><span>脆弱度歷史趨勢 <span style="font-weight:400;color:var(--muted)">｜觸發敏感度 </span><select id="sens" style="font:inherit;font-size:11.5px;background:var(--bg);color:var(--tp);border:1px solid var(--border);border-radius:7px;padding:3px 6px"></select><div id="sensnote" style="font-weight:400;font-size:10.5px;color:var(--muted);margin-top:3px"></div></span>
 <span class="ctrl" id="ranges"><button data-d="252">1年</button><button data-d="756">3年</button><button data-d="1260">5年</button><button data-d="0">全部</button>
 <input type="date" id="d0"><span style="color:var(--muted)">~</span><input type="date" id="d1"></span></div>
 <div id="tc"><svg id="tcsvg"></svg><div id="tctip"></div></div>
 <div style="display:flex;justify-content:space-between;color:var(--muted);font-size:10.5px;margin-top:4px">
 <span>移過看數值 · 滾輪縮放 · 點按釘選</span><span>深紅=踩剎車 · 淺紅=僅觸發 · 橙=僅脆弱 · 灰=NBER衰退</span></div></div>
<h2 id="indh" style="margin-bottom:6px">指數子分析 <span style="font-size:11px;font-weight:400;color:var(--muted)">依對該指數報酬的預測力(rank-IC)重排</span></h2>
<div id="indbar" class="indbar"></div>
<div class="indcap" id="indcap"></div>
<div class="gtitle" style="color:var(--series-1)">■ 慢層 · 內部槓桿 <span style="font-weight:400;color:var(--muted)">計分</span></div>
<div class="grid" id="grp-internal"></div>
<div class="gtitle" style="color:var(--series-1)">■ 慢層 · 外部資金與信用 <span style="font-weight:400;color:var(--muted)">計分</span></div>
<div class="grid" id="grp-external"></div>
<div class="gtitle" style="color:var(--red)">■ 快層 · 觸發 <span style="font-weight:400;color:var(--muted)">亮起才踩剎車</span></div>
<div class="grid" id="grp-trigger"></div>
<div class="gtitle" style="color:var(--muted)">■ 動能參考 <span style="font-weight:400;color:var(--muted)">不計分(乖離越大→後續回撤反而越淺)</span></div>
<div class="grid" id="grp-momo"></div>
<h2>歷史對照 — 脆弱度分數 vs S&amp;P500 未來報酬(樣本內描述統計)</h2>
<table><thead><tr><th>脆弱度區間</th><th class="r">樣本天數</th><th class="r">未來20日</th><th class="r">未來60日</th><th class="r">未來120日</th></tr></thead><tbody>__HIST__</tbody></table>
<details class="note"><summary style="cursor:pointer;font-weight:650;color:var(--tp)">方法、資料源與已知限制(點開)</summary><div style="margin-top:8px">各指標一律轉成 <b>PIT 擴張百分位</b>(第 t 日危險度只用「當日及以前」的分佈計算,<b>無前視偏誤</b>;需滿一年暖身才起算)再加權合成。融資面採 <b>FINRA 全市場融資餘額</b>(Debit Balances,月頻,依<b>發布落後 25 天</b>對齊,避免用到當下尚未公布的數字),分為去趨勢殘差(<b>擴張視窗迴歸</b>:每日係數與標準化只用當日及以前資料,已消除「全樣本一次迴歸」會造成的前視洩漏)、成長背離(vs <b>美國名目GDP</b>,依發布落後 PIT 對齊,實際來源見文末)與半年擴張三軌。<b>費城半導體(SOX)指數本身無免費授權資料源,本頁以 iShares SOXX ETF 還原價代理</b>;SOXX 於 2021 年由 PHLX SOX 改追蹤 ICE 半導體指數,長期比較請留意。VIX 水位、NFCI、高收益債利差採<b>反向</b>計分(過低=自滿/信用過鬆=脆弱累積),故本錶在平靜的多頭末端會偏紅、在崩跌當下反而轉綠——這是<b>事前脆弱度</b>而非事後壓力。高收益債利差受 ICE BofA 授權限制,FRED 公開下載僅約近三年,故該項較晚才進入計分。下方歷史對照表為<b>樣本內描述統計</b>(用了全期資料回顧),僅供理解分數含義,<b>不可視為預測或回測績效</b>。目前 FINRA 融資餘額約 __MARGIN__。<br><b>本次實際採用的資料來源:</b>__SRC__。(系統為多來源設計:FRED 不可得時自動改用 Cboe / 芝加哥聯準會 / ETF 還原價代理 / 世界銀行,頁面一律據實標示。)<br><b>2026-07 改版(依回測修正):</b>以本頁 2018–2026 樣本測各指標危險度與<b>未來60日最大回撤</b>的 Spearman IC,乖離類全為負(費半 −0.136、Nasdaq −0.109、S&amp;P500 −0.053),融資類在美股樣本亦為負(半年擴張 −0.166、成長背離 −0.143、超額水位 −0.088),僅 <b>VIX 5日跳升 +0.087</b> 方向正確;原合成脆弱度整體 IC 為 <b>−0.100</b>,且紅燈後 60 日跌逾 10% 的機率為 0.0%、綠燈反而 5.1%——換言之原設計把「漲多」當危險,會系統性誤殺仍在上漲的標的。故改為雙層:慢層只收上限、快層才觸發。<b>須注意指標方向跨市場與跨期並不穩定</b>(融資超額水位在台股 2020–26 為 +0.112、美股同期為 −0.088),本樣本又僅約 8 年且以多頭為主,<b>此改版意在降低誤殺,不保證提升績效</b>。<b>本頁為風險框架,非投資建議。</b></div></details></div>
</div>
<script>__APPJS__</script>
<script>document.getElementById('th').onclick=()=>{const r=document.documentElement;r.setAttribute('data-theme',r.getAttribute('data-theme')=='dark'?'light':'dark');if(window.__redraw)window.__redraw();};
if('serviceWorker' in navigator){window.addEventListener('load',()=>navigator.serviceWorker.register('service-worker.js').catch(()=>{}));}</script>
</body></html>"""

APP_JS=r"""
const D=__APPDATA__, REC=__REC__;
const N=D.dates.length, TS=D.dates.map(s=>Date.parse(s));
const REDN=8;
const DPOS={};for(let _i=0;_i<N;_i++)DPOS[D.dates[_i]]=_i;
const $=id=>document.getElementById(id);
let a=Math.max(0,N-756), b=N-1, sel=N-1, pinned=false, pinIdx=null;
const light=s=>s>=75?'red':s>=55?'warn':'ok';
function fmtVal(v,fmt){if(v==null||isNaN(v))return '–';let s=(+v).toFixed(fmt[1]);if(fmt[0]&&v>=0)s='+'+s;return s+fmt[2];}
// ---- gauge ----
const NSLOW=(D.slow||D.order).length;
function redCount(i){let c=0;for(const k of (D.slow||D.order)){const dg=D.inds[k].dng[i];if(dg!=null&&dg>=75)c++;}return c;}
// 雙層:慢層(結構脆弱度)只收槓桿上限;快層(觸發)才真的降到 1x
function levCap(c){return c>=75?1.2:(c>=55?1.35:1.5);}
function levNow(i){return trigAt(i)?1.0:levCap(D.comp[i]);}
// == 觸發敏感度旋鈕:使用者可即時調整「多容易踩剎車」,所有燈號/色帶/曝險即時重算 ==
// 各檔數字為全期回測(訊號延後1日、融資成本6%),樣本以多頭為主,僅供比較鬆緊度的取捨。
const VIXD=D.inds['vix_spike']?D.inds['vix_spike'].dng:[];
const TRV=D.inds['sp_trend']?D.inds['sp_trend'].val:[];
const SENS=[
 {n:'保守',d:'VIX跳升>=85分位 或 跌破200日線',v:85,f:i=>(VIXD[i]!=null&&VIXD[i]>=85)||(TRV[i]!=null&&TRV[i]<0)},
 {n:'標準',d:'VIX跳升>=75分位 或 跌破200日線',v:75,f:i=>(VIXD[i]!=null&&VIXD[i]>=75)||(TRV[i]!=null&&TRV[i]<0)},
 {n:'敏感',d:'VIX>=70 或 破200日線 或 破60日線',v:70,f:i=>(VIXD[i]!=null&&VIXD[i]>=70)||(TRV[i]!=null&&TRV[i]<0)||(D.ma60br&&D.ma60br[i]===1)},
 {n:'最敏感',d:'VIX>=60 或 破季線 或 20日跌逾3%',v:60,f:i=>(VIXD[i]!=null&&VIXD[i]>=60)||(D.ma60br&&D.ma60br[i]===1)||(D.r20&&D.r20[i]!=null&&D.r20[i]<-3)}
];
let SI=1;                                   // 預設「標準」:回測覆蓋率與報酬皆優於原設定,回撤相同
function trigAt(i){try{return SENS[SI].f(i)?1:0;}catch(e){return (D.trig&&D.trig[i])?1:0;}}
function brakeState(i){const c=D.comp[i],t=trigAt(i);
 if(t&&c>=75)return 'brake'; if(t)return 'trig'; if(c>=75)return 'fragile'; return 'ok';}
function gauge(i){const c=D.comp[i];$('gauge').style.setProperty('--v',c.toFixed(0));
 $('gauge').style.setProperty('--gc','var(--'+light(c)+')');$('cnum').textContent=c.toFixed(0);
 const cap=levCap(c),st=brakeState(i),tg=trigAt(i);
 const tv=tg&&VIXD[i]!=null&&VIXD[i]>=SENS[SI].v;
 const tm=tg&&TRV[i]!=null&&TRV[i]<0;
 const tb=tg&&SI>=2&&D.ma60br&&D.ma60br[i]===1;
 const td=tg&&SI>=3&&D.r20&&D.r20[i]!=null&&D.r20[i]<-3;
 const why=[tv?'VIX跳升':null,tm?'跌破200日線':null,tb?'跌破60日線':null,td?'20日跌逾3%':null].filter(Boolean).join('＋')||'趨勢轉弱';
 var cj=$('cjudge');
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
// ---- 指數預測力(rank-IC) ----
let curInd=(D.indorder&&D.indorder.length)?D.indorder[0]:null;
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
let SCG=null;
function syncStockCursor(ds){if(!SD||!SCG)return;const j=scIdxForDate(ds);
 if(j<SCG.SA||j>SCG.SB){$('scx').setAttribute('opacity','0');$('scd').setAttribute('opacity','0');$('sctip').style.opacity='0';return;}
 const x=SCG.X(j),y=SCG.Y(SCG.ly[j]);
 $('scx').setAttribute('x1',x);$('scx').setAttribute('x2',x);$('scx').setAttribute('opacity',pinned?'0.6':'0.35');
 $('scd').setAttribute('cx',x);$('scd').setAttribute('cy',y);$('scd').setAttribute('opacity','1');
 const tp=$('sctip');tp.style.opacity='1';const pf=v=>(v==null||v<=0)?'–':(v>=100?v.toFixed(0):v.toFixed(2));
 tp.innerHTML=(pinned?'📌 ':'')+'<b>'+SD.dates[j]+'</b><br>收盤 '+pf(SD.raw[j])+' <span style="color:var(--muted)">(還原'+pf(SD.adj[j])+')</span>'
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
 syncStockCursor(D.dates[i]);}
function move(ev){if(pinned)return;showAt(idxAt(ev));}
function leave(){if(pinned){showAt(pinIdx);return;}
 $('tcx').setAttribute('opacity','0');$('tcd').setAttribute('opacity','0');tip.style.opacity='0';
 sel=b;$('viewdate').textContent='最新('+D.dates[b]+')— 點一下線圖可固定數值';gauge(b);renderCards(b);hideStockCursor();}
function unpin(){pinned=false;pinIdx=null;$('tcx').setAttribute('opacity','0');$('tcd').setAttribute('opacity','0');tip.style.opacity='0';
 sel=b;$('viewdate').textContent='最新('+D.dates[b]+')— 點一下線圖可固定數值';gauge(b);renderCards(b);hideStockCursor();}
function onClick(ev){const i=idxAt(ev);if(pinned&&Math.abs(i-pinIdx)<=1){unpin();}else{pinned=true;pinIdx=i;showAt(i);}}
function stockFragIdx(clientX){const r=$('scsvg').getBoundingClientRect();const g=SCG;if(!g)return null;
 let j=g.SA+Math.round(((clientX-r.left)-g.pl)/g.plotW*(g.SB-g.SA));j=Math.max(g.SA,Math.min(g.SB,j));
 const ds=SD.dates[j];const fi=(DPOS[ds]!=null?DPOS[ds]:idxForTs(Date.parse(ds)));return Math.max(curA,Math.min(curB,fi));}
function syncInputs(){$('d0').value=D.dates[a];$('d1').value=D.dates[b];}
// ---- 共用時間軸 ----
function markBtns(days){document.querySelectorAll('#ranges button,#scranges button').forEach(x=>x.classList.toggle('on',+x.dataset.d===days));}
function clearBtns(){document.querySelectorAll('#ranges button,#scranges button').forEach(x=>x.classList.remove('on'));}
function applyWindow(d0,d1){
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
document.querySelectorAll('#ranges button').forEach(btn=>btn.onclick=()=>sharedDays(+btn.dataset.d));
$('d0').onchange=()=>sharedDates($('d0').value,$('d1').value);
$('d1').onchange=()=>sharedDates($('d0').value,$('d1').value);
svg.addEventListener('wheel',e=>wheelZoom(e,ev=>idxAt(ev)),{passive:false});
svg.addEventListener('mousemove',move);svg.addEventListener('mouseleave',leave);
svg.addEventListener('click',onClick);svg.addEventListener('dblclick',unpin);
svg.addEventListener('touchmove',e=>move(e),{passive:true});
let rt;window.addEventListener('resize',()=>{clearTimeout(rt);rt=setTimeout(renderTrend,150);});
// ---- 個股搜尋(stockanalysis.com, 支援 CORS) ----
const POPULAR=[["NVDA","NVIDIA","s"],["AAPL","Apple","s"],["MSFT","Microsoft","s"],["GOOGL","Alphabet","s"],
 ["AMZN","Amazon","s"],["META","Meta","s"],["TSLA","Tesla","s"],["AVGO","Broadcom","s"],["AMD","AMD","s"],
 ["TSM","台積電ADR","s"],["MU","Micron","s"],["ASML","ASML","s"],["ARM","Arm","s"],["INTC","Intel","s"],
 ["MRVL","Marvell","s"],["SMCI","Supermicro","s"],["DELL","Dell","s"],["ORCL","Oracle","s"],["PLTR","Palantir","s"],
 ["COIN","Coinbase","s"],["SOXX","半導體ETF","e"],["SMH","半導體ETF","e"],["SPY","S&P500 ETF","e"],
 ["QQQ","Nasdaq100 ETF","e"],["IWM","小型股 ETF","e"],["VOO","S&P500 ETF","e"],["TQQQ","3xQQQ","e"]];
function buildDatalist(){$('stocklist').innerHTML=POPULAR.map(x=>'<option value="'+x[0]+'">'+x[0]+' '+x[1]+'</option>').join('');}
async function saFetch(sym,kind){const r=await fetch('https://stockanalysis.com/api/symbol/'+kind+'/'+sym+'/history?range=10Y');
 if(!r.ok)throw new Error('HTTP '+r.status);const j=await r.json();return j.data||[];}
async function saSearch(q){const r=await fetch('https://stockanalysis.com/api/search?q='+encodeURIComponent(q));
 if(!r.ok)throw new Error('HTTP '+r.status);const j=await r.json();
 return (j.data||[]).filter(x=>x.t==='s'||x.t==='e');}
function saAdj(rows){        // stockanalysis:新→舊, a=還原收盤
 rows=rows.filter(r=>+r.c>0).sort((x,y)=>x.t<y.t?-1:1);
 if(rows.length<2)return null;
 const dates=rows.map(r=>r.t),raw=rows.map(r=>+r.c),adj=rows.map(r=>+(r.a!=null?r.a:r.c)),vol=rows.map(r=>+r.v||0);
 const rt=adj.map((v,i)=>raw[i]>0?v/raw[i]:1);
 const aopen=rows.map((r,i)=>+r.o>0?+r.o*rt[i]:null),ahigh=rows.map((r,i)=>+r.h>0?+r.h*rt[i]:null),alow=rows.map((r,i)=>+r.l>0?+r.l*rt[i]:null);
 return {dates,raw,adj,vol,aopen,ahigh,alow};}
function sma(arr,n){const out=new Array(arr.length).fill(null);let s=0;for(let i=0;i<arr.length;i++){const v=arr[i]||0;s+=v;if(i>=n)s-=(arr[i-n]||0);if(i>=n-1)out[i]=s/n;}return out;}
function pctileAt(arr,t){const v=arr[t];if(v==null||isNaN(v))return null;let cnt=0,less=0;
 for(let i=0;i<=t&&i<arr.length;i++){const x=arr[i];if(x==null||isNaN(x))continue;cnt++;if(x<v)less++;}
 return cnt?less/cnt*100:null;}
function stLight(p){return p==null?'':p>=75?'red':p>=55?'warn':'ok';}
let _spmap=null;
function spMap(){if(_spmap)return _spmap;_spmap=new Map();const t=D.bench||{dates:[],close:[]};
 for(let i=0;i<t.dates.length;i++)_spmap.set(t.dates[i],t.close[i]);return _spmap;}
function computeBetaAt(win,t){
 if(!SD||!D.bench||!D.bench.dates.length)return null;const tm=spMap();const rs=[],rm=[];
 const start=Math.max(1,t-win+1);
 for(let i=start;i<=t;i++){const m=tm.get(SD.dates[i]),mp=tm.get(SD.dates[i-1]);const sp=SD.adj[i-1],sc=SD.adj[i];
  if(m==null||mp==null||sp<=0||sc<=0||mp<=0||m<=0)continue;rs.push(Math.log(sc/sp));rm.push(Math.log(m/mp));}
 if(rs.length<60)return null;
 const mmn=rm.reduce((x,y)=>x+y,0)/rm.length,smn=rs.reduce((x,y)=>x+y,0)/rs.length;
 let cov=0,vm=0;for(let i=0;i<rs.length;i++){cov+=(rs[i]-smn)*(rm[i]-mmn);vm+=(rm[i]-mmn)**2;}
 return vm>0?cov/vm:null;}
let SM=null;
function buildStockMetrics(){        // 逐日序列:美股個股可得的風險指標(無個股融資公開資料)
 const n=SD.dates.length,adj=SD.adj,vol=SD.vol;
 const lr=[null];for(let i=1;i<n;i++)lr.push((adj[i]>0&&adj[i-1]>0)?Math.log(adj[i]/adj[i-1]):null);
 const vola=new Array(n).fill(null);
 for(let i=0;i<n;i++){if(i<60)continue;let s=0,c=0;for(let j=i-59;j<=i;j++){if(lr[j]!=null){s+=lr[j]*lr[j];c++;}}
  if(c>40)vola[i]=Math.sqrt(s/c*252)*100;}
 const dd=new Array(n).fill(null),hi52=new Array(n).fill(null);
 for(let i=0;i<n;i++){const st=Math.max(0,i-251);let mx=0;for(let j=st;j<=i;j++)if(adj[j]>mx)mx=adj[j];
  hi52[i]=mx;dd[i]=mx>0?(adj[i]/mx-1)*100:null;}
 const ma200=sma(adj,200);
 const dev=adj.map((v,i)=>(ma200[i]&&ma200[i]>0)?(v/ma200[i]-1)*100:null);
 const v20=sma(vol,20),v60=sma(vol,60);
 const vr=v20.map((v,i)=>(v!=null&&v60[i]>0)?v/v60[i]*100:null);
 return {vola,dd,dev,vr,n};}
function renderStockCardsAt(t){if(!SM||!SD)return;t=Math.max(0,Math.min(SM.n-1,t));
 const box=$('scstats');const {vola,dd,dev,vr}=SM;
 const f1=x=>x==null?'–':(x>=0?'+':'')+x.toFixed(1);
 const beta=computeBetaAt(252,t);
 function tile(label,val,sub,p){const lt=stLight(p);
  return '<div class="stile '+lt+'"><div class="l">'+label+'</div><div class="v">'+val+'</div><div class="s">'+(sub||'')+'</div></div>';}
 let html='';
 html+=tile('市場Beta(1年)',beta==null?'–':beta.toFixed(2),'對 S&P500 敏感度'+(beta!=null&&beta>=1.2?' · 高':''),beta==null?null:Math.min(100,Math.max(0,(beta-0.5)/1.5*100)));
 html+=tile('年化波動度',vola[t]==null?'–':vola[t].toFixed(0)+'%','近60日已實現',pctileAt(vola,t));
 html+=tile('距52週高點',dd[t]==null?'–':f1(dd[t])+'%','回撤幅度',dd[t]==null?null:100-(pctileAt(dd,t)||0));
 html+=tile('距200日線',dev[t]==null?'–':f1(dev[t])+'%','趨勢乖離',pctileAt(dev,t));
 html+=tile('量能比',vr[t]==null?'–':vr[t].toFixed(0)+'%','20日均量/60日均量',pctileAt(vr,t));
 box.innerHTML=html;
 const marketDi=DPOS[SD.dates[t]];const marketRed=(marketDi!=null&&D.comp[marketDi]>=75);
 const priority=(beta!=null&&beta>=1.2)&&(marketRed||(dev[t]!=null&&dev[t]>20));
 const isLatest=(t===SM.n-1);
 $('scmnote').innerHTML='<b>'+SD.dates[t]+'</b>'+(isLatest?'(最新)':'(游標點,前推視窗)')+' · '+SD.code+' 個股指標,危險度=該股至此日百分位'
   +(priority?' · <b style="color:var(--red)">⚠ 高Beta+乖離偏高,優先降本檔</b>':'')
   +'';
}
let SD=null,SA=0,SB=0;
async function doSearch(){const q=$('q').value.trim();if(!q){$('qmsg').textContent='請輸入代號或公司名';return;}
 $('qmsg').textContent='搜尋中…';
 try{
  let sym=q.toUpperCase(),kind=null,nm='';
  const hit=POPULAR.find(x=>x[0]===sym);
  if(hit){kind=hit[2];nm=hit[1];}
  else{const res=await saSearch(q);
   if(!res.length){$('qmsg').textContent='查無此代號/公司';return;}
   const exact=res.find(x=>x.s.toUpperCase()===sym)||res[0];
   sym=exact.s.toUpperCase();kind=exact.t;nm=exact.n||'';}
  $('qmsg').textContent='抓取中…';
  const rows=await saFetch(sym,kind);
  if(!rows.length){$('qmsg').textContent='查無價格資料';$('scwrap').style.display='none';return;}
  SD=saAdj(rows);if(!SD){$('qmsg').textContent='資料不足';return;}
  SD.code=sym;SD.name=sym+(nm?' '+nm:'')+(kind==='e'?' · ETF':'');
  SD.ma20=sma(SD.adj,20);SD.ma60=sma(SD.adj,60);SD.ma120=sma(SD.adj,120);
  $('sctitle').textContent=SD.name;$('scwrap').style.display='';$('qmsg').textContent='';
  SA=scIdxForDate(D.dates[a]);SB=scIdxForDate(D.dates[b]);
  SM=buildStockMetrics();
  drawStock();
 }catch(e){let m=e.message;
   if(location.protocol==='file:')m='此頁是用「檔案(file://)」開啟,瀏覽器會擋住跨站抓取。請改用網址開啟(GitHub Pages/Netlify),或在此資料夾執行 python3 -m http.server 後開 http://localhost:8000/us.html';
   else if(/fetch/i.test(m))m='抓取失敗——可能是網路或廣告封鎖擴充套件擋了 stockanalysis.com';
   $('qmsg').textContent='⚠ '+m;}}
function drawStock(){if(!SD)return;const svg=$('scsvg'),wrap=$('sc'),tip=$('sctip');
 const W=wrap.clientWidth||800,H=210,pl=52,pr=12,pt=12,pb=22,plotW=W-pl-pr,plotH=H-pt-pb;
 const ly=SD.adj.map(x=>Math.log10(x));const span=SB-SA;
 let vmn=Infinity,vmx=-Infinity;const psh=v=>{if(v!=null&&v>0){if(v<vmn)vmn=v;if(v>vmx)vmx=v;}};
 for(let i=SA;i<=SB;i++){psh(SD.ahigh[i]!=null?SD.ahigh[i]:SD.adj[i]);psh(SD.alow[i]!=null?SD.alow[i]:SD.adj[i]);psh(SD.ma20[i]);psh(SD.ma60[i]);psh(SD.ma120[i]);}
 if(!isFinite(vmn)){vmn=Math.min(...SD.adj.slice(SA,SB+1));vmx=Math.max(...SD.adj.slice(SA,SB+1));}
 const lo=Math.log10(vmn),hi=Math.log10(vmx);
 const X=i=>pl+(span<1?0:(i-SA)/span*plotW),Y=v=>pt+(hi-v)/((hi-lo)||1)*plotH;const Yl=pv=>Y(Math.log10(pv));
 svg.setAttribute('width',W);svg.setAttribute('height',H);svg.setAttribute('viewBox','0 0 '+W+' '+H);
 let g='';[0,0.25,0.5,0.75,1].forEach(t=>{const lv=lo+(hi-lo)*t,pv=Math.pow(10,lv);
  g+='<line x1="'+pl+'" y1="'+Y(lv)+'" x2="'+(W-pr)+'" y2="'+Y(lv)+'" stroke="var(--border)" stroke-dasharray="3 3"/>'
   +'<text x="'+(pl-4)+'" y="'+(Y(lv)+3)+'" font-size="9" fill="var(--muted)" text-anchor="end">'+(pv>=100?pv.toFixed(0):pv.toFixed(2))+'</text>';});
 const cw=plotW/Math.max(1,span);
 for(let i=SA;i<=SB;i++){const di=DPOS[SD.dates[i]];if(di==null)continue;const st=brakeState(di);
  if(st==='ok')continue;
  const col=(st==='fragile')?'var(--warn)':'var(--red)';
  const op=(st==='brake')?'0.24':(st==='trig'?'0.12':'0.09');
  g+='<rect x="'+(X(i)-cw/2).toFixed(1)+'" y="'+pt+'" width="'+Math.max(1,cw+0.6).toFixed(1)+'" height="'+plotH+'" fill="'+col+'" opacity="'+op+'"/>';}
 const showMonth=span<=500;let lastL=null;for(let i=SA;i<=SB;i++){const lab=showMonth?SD.dates[i].slice(0,7):SD.dates[i].slice(0,4);
  if(lab!==lastL){lastL=lab;if(showMonth||(+lab%(span>2600?3:1)==0))g+='<text x="'+X(i)+'" y="'+(H-6)+'" font-size="9" fill="var(--muted)" text-anchor="middle">'+lab+'</text>';}}
 // K線(美股慣例:綠漲紅跌);過密則退回還原收盤線
 const slot=plotW/Math.max(1,span+1),drawK=slot>=2.2;
 if(drawK){const bw=Math.max(1,Math.min(9,slot*0.62));
  for(let i=SA;i<=SB;i++){const o=SD.aopen[i],c=SD.adj[i],h=SD.ahigh[i],l=SD.alow[i];if(!(c>0))continue;
   const x=X(i),up=(o!=null?c>=o:true),col=up?'var(--ok)':'var(--red)';
   if(h>0&&l>0)g+='<line x1="'+x.toFixed(1)+'" y1="'+Yl(h).toFixed(1)+'" x2="'+x.toFixed(1)+'" y2="'+Yl(l).toFixed(1)+'" stroke="'+col+'" stroke-width="1"/>';
   if(o!=null&&o>0){const yo=Yl(o),yc=Yl(c),top=Math.min(yo,yc),ht=Math.max(1,Math.abs(yo-yc));
    g+='<rect x="'+(x-bw/2).toFixed(1)+'" y="'+top.toFixed(1)+'" width="'+bw.toFixed(1)+'" height="'+ht.toFixed(1)+'" fill="'+col+'"/>';}}
 }else{let p='';for(let i=SA;i<=SB;i++)p+=(i==SA?'M':'L')+X(i).toFixed(1)+' '+Y(ly[i]).toFixed(1)+' ';
  g+='<path d="'+p+'" fill="none" stroke="var(--ts)" stroke-width="1.2" opacity="0.75"/>';}
 function maPath(ma){let p='',on=false;for(let i=SA;i<=SB;i++){const v=ma[i];if(v==null||v<=0){on=false;continue;}p+=(on?'L':'M')+X(i).toFixed(1)+' '+Yl(v).toFixed(1)+' ';on=true;}return p;}
 const MAS=[[SD.ma20,'#2a78d6','月線20'],[SD.ma60,'#8b5cf6','季線60'],[SD.ma120,'#0891b2','半年線120']];
 MAS.forEach(m=>{const d=maPath(m[0]);if(d)g+='<path d="'+d+'" fill="none" stroke="'+m[1]+'" stroke-width="1.2" opacity="0.95"/>';});
 let lx=pl+3;MAS.forEach(m=>{g+='<line x1="'+lx+'" y1="'+(pt+7)+'" x2="'+(lx+13)+'" y2="'+(pt+7)+'" stroke="'+m[1]+'" stroke-width="2"/>'
  +'<text x="'+(lx+16)+'" y="'+(pt+10)+'" font-size="9" fill="var(--muted)">'+m[2]+'</text>';lx+=16+m[2].length*9+16;});
 g+='<line id="scx" y1="'+pt+'" y2="'+(pt+plotH)+'" stroke="var(--tp)" opacity="0"/><circle id="scd" r="3.2" fill="var(--series-1)" opacity="0"/>';
 svg.innerHTML=g;
 $('scstart').textContent=SD.dates[SA];$('scend').textContent=SD.dates[SB];
 SCG={X,Y,ly,SA,SB,W,pl,plotW};
 svg.onwheel=ev=>wheelZoom(ev,e=>stockFragIdx(e.clientX));
 svg.onmousemove=ev=>{if(pinned)return;const fi=stockFragIdx(ev.clientX);if(fi!=null)showAt(fi);};
 svg.onmouseleave=()=>{leave();};
 svg.onclick=ev=>{const fi=stockFragIdx(ev.clientX);if(fi==null)return;
  if(pinned&&Math.abs(fi-pinIdx)<=1){unpin();}else{pinned=true;pinIdx=fi;showAt(fi);}};
 svg.ondblclick=unpin;
 if(pinned&&pinIdx>=curA&&pinIdx<=curB)syncStockCursor(D.dates[pinIdx]);
 else if(SM)renderStockCardsAt(SB);
 updateSuit();}
function updateSuit(){const el=$('scsuit');if(!el||!SD)return;
 let cs=[],nb=0,tot=0;
 for(let i=SA;i<=SB;i++){const di=DPOS[SD.dates[i]];if(di==null)continue;tot++;cs.push(D.comp[di]);if(brakeState(di)==='brake')nb++;}
 if(!tot){el.innerHTML='<span style="color:var(--muted)">此區間早於脆弱度資料,無市場脆弱度可對照</span>';return;}
 const avg=cs.reduce((x,y)=>x+y,0)/cs.length,mx=Math.max(...cs),pb=nb/tot*100;
 let v,col;
 if(avg>=70||pb>=15){v='不宜融資持有(系統高壓)';col='var(--red)';}
 else if(avg>=55||pb>=5){v='融資需謹慎';col='var(--warn)';}
 else{v='系統壓力低,相對適合';col='var(--ok)';}
 el.innerHTML='此區間 脆弱度均 <b>'+avg.toFixed(0)+'</b>/峰 '+mx.toFixed(0)+' · 踩剎車 <b>'+pb.toFixed(0)+'%</b> → <b style="color:'+col+'">'+v+'</b>';}
function scIdxForDate(ds){const t=Date.parse(ds);let lo=0,hi=SD.dates.length-1;const T=SD.dates.map(Date.parse);
 if(t<=T[0])return 0;if(t>=T[hi])return hi;while(lo<hi){const m=(lo+hi)>>1;if(T[m]<t)lo=m+1;else hi=m;}return lo;}
function setupSearch(){buildDatalist();$('qbtn').onclick=doSearch;
 $('q').addEventListener('keydown',e=>{if(e.key==='Enter')doSearch();});
 document.querySelectorAll('#scranges button').forEach(btn=>btn.onclick=()=>sharedDays(+btn.dataset.d));
 let st;window.addEventListener('resize',()=>{clearTimeout(st);st=setTimeout(()=>{if(SD)drawStock();},150);});}

// 敏感度旋鈕
const SENSTAT={"0": ["32%", "60%", "+15.6%", "-34%", "x10.6"], "1": ["41%", "61%", "+15.8%", "-34%", "x10.4"], "2": ["50%", "67%", "+15.1%", "-34%", "x9.8"], "3": ["55%", "64%", "+14.0%", "-34%", "x7.9"]};
function sensLabel(){const t=SENSTAT[SI];
 return SENS[SI].d+(t?' · 回測 降1x '+t[0]+' / 覆蓋 '+t[1]+' / CAGR '+t[2]+' / MDD '+t[3]:'');}
function buildSens(){const el=$('sens');if(!el)return;
 el.innerHTML=SENS.map((x,i)=>'<option value="'+i+'"'+(i===SI?' selected':'')+'>'+x.n+'</option>').join('');
 $('sensnote').textContent=sensLabel();
 el.onchange=()=>{SI=+el.value;$('sensnote').textContent=sensLabel();
  renderTrend();gauge(sel);renderCards(sel);applyRanking();if(SD)drawStock();};}
// init
buildCards();buildIndBar();buildSens();setupSearch();syncInputs();markBtns(756);renderTrend();gauge(b);renderCards(b);applyRanking();
"""

def main():
    d=get_data()
    parts=[s for s in (d.get("nasdaq"),d.get("sp500"),d.get("sox")) if s is not None and len(s)]
    if not parts:
        _log("資料抓取失敗:三大指數皆不可得"); sys.exit(1)
    master=parts[0].index
    for s in parts[1:]: master=master.union(s.index)
    master=pd.DatetimeIndex(sorted(set(master)))
    R=compute(d,master)
    _log(f"   指標數={len(R)}: {', '.join(R.keys())}")
    app=build_app_data(R,master,d['sp500'])
    if not app["dates"]:
        _log("合成序列為空(指標暖身不足)"); sys.exit(1)
    # 指數子分析:S&P500 / Nasdaq / 費半(SOXX) 對齊到儀表板日期
    idx=pd.DatetimeIndex(pd.to_datetime(app["dates"]))
    app["industries"]={}; app["indorder"]=[]; app["H"]=20
    for nm,ser in [("S&P 500",d["sp500"]),("Nasdaq",d["nasdaq"]),("費半(SOXX)",d["sox"])]:
        if ser is None or not len(ser): continue
        s=ser.reindex(ser.index.union(idx)).sort_index().ffill().reindex(idx)
        app["industries"][nm]=[None if pd.isna(x) else round(float(x),3) for x in s.values]
        app["indorder"].append(nm)
    # 個股 beta 基準 = S&P500(或其代理)
    spb=d["sp500"]
    app["bench"]={"dates":[str(x.date()) for x in spb.index],"close":[round(float(v),3) for v in spb.values]}
    app["stocks"]={}
    asof=app["dates"][-1]; comp_now=app["comp"][-1]
    tbl=hist_table(app,d,master)
    mg=d.get("margin_m")
    marg_txt=(f"{mg.iloc[-1]/1e6:,.2f} 兆美元({mg.index[-1].date().strftime('%Y-%m')})" if mg is not None and len(mg) else "—")
    open(OUT_HTML,"w").write(build_html(app,asof,tbl,marg_txt,d.get("src",{})))
    row={"date":asof,"composite":round(comp_now,1)}
    for k in R: row[k]=round(R[k]["val"],3)
    hist=pd.DataFrame([row])
    if os.path.exists(HIST_CSV):
        old=pd.read_csv(HIST_CSV); hist=pd.concat([old[old["date"]!=asof],hist],ignore_index=True)
    hist.to_csv(HIST_CSV,index=False)
    _log(f"OK  {asof}  美股脆弱度={comp_now:.0f}/100  → {OUT_HTML}  (樣本 {app['dates'][0]} ~ {asof}, {len(app['dates'])} 日)")
    for k in ORDER:
        if k in R: _log(f"   {R[k]['label']:22} {R[k]['val']:+.2f}{R[k]['unit']}")

if __name__=="__main__":
    main()
