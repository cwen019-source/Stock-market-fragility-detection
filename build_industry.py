#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""建立台股各產業「等權還原報酬指數」→ industry_returns.json(供儀表板嵌入)。
取樣每產業約 8 檔,還原股價(±10%漲跌幅法)後計算日報酬等權平均,累積成產業指數。
可重複執行:個股還原價快取於 industry_px_cache.json,續抓不重複。"""
import os, json
from collections import defaultdict
import pandas as pd, numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from screener import fetch, adj_close, _lock

CACHE="industry_px_cache.json"

# AI / 熱度題材子產業籃子(等權)。可自行增修成分股。
THEMES={
"AI伺服器代工":["2317","2382","3231","6669","2356","2376","2377","4938"],
"PCB載板":["3037","8046","3189","2383","2368","3044","6274","2313"],
"被動元件":["2327","2492","3026","2478","2375","2456"],
"晶圓代工封測":["2330","2303","6770","3711","6239","2449","3583","3131"],
"IC設計":["2454","3034","2379","4966","3443","3661","8016"],
"矽智財IP":["3529","3661","3443","6643","3035","6533"],
"散熱":["3324","3017","2421","8996","3653"],
"光通訊CPO":["3081","3163","4977","3450","4979"],
"記憶體":["2408","2344","8299","3260","4967","2451"],
"重電電力":["1519","1503","1513","1514","1504"],
"網通設備":["2345","3704","5388","4906","2419"],
"電源連接":["2308","6412","3023"],
"機殼機構":["8210","3693","2059"],
}

def members():
    return {k:list(v) for k,v in THEMES.items()}

def load_cache():
    if os.path.exists(CACHE):
        return {k:pd.Series(v["val"],index=pd.to_datetime(v["idx"])) for k,v in json.load(open(CACHE)).items()}
    return {}

def main():
    sel=members()
    need=sorted({s for ids in sel.values() for s in ids})
    cache=load_cache()
    todo=[s for s in need if s not in cache]
    print(f"產業 {len(sel)} 個, 需個股 {len(need)}, 已快取 {len(cache)}, 待抓 {len(todo)}")
    def work(sid):
        pr=fetch("TaiwanStockPrice",sid)
        if pr is None: return sid,None,"rate"
        if not pr: return sid,None,"empty"
        s=adj_close(pr)
        return sid,(s if s is not None else None),"ok"
    got=0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs={ex.submit(work,s):s for s in todo}
        for f in as_completed(futs):
            sid,s,st=f.result()
            if s is not None and len(s):
                with _lock: cache[sid]=s; got+=1
            if got and got%50==0: print(f"  抓到 {got}/{len(todo)}")
    # 存快取
    json.dump({k:{"idx":[str(d.date()) for d in v.index],"val":[round(float(x),4) for x in v.values]}
               for k,v in cache.items()}, open(CACHE,"w"))
    # master 交易日 = TAIEX
    tx=fetch("TaiwanStockPrice","TAIEX")
    tix=pd.DataFrame(tx); tix["date"]=pd.to_datetime(tix["date"]); master=tix.sort_values("date")["date"]
    master=pd.DatetimeIndex(master.unique())
    out_dates=[str(d.date()) for d in master]
    industries={}
    for ind,ids in sel.items():
        rets=[]
        for sid in ids:
            if sid not in cache: continue
            s=cache[sid].reindex(master).ffill()
            r=s.pct_change()
            rets.append(r)
        if len(rets)<3: continue
        eqw=pd.concat(rets,axis=1).mean(axis=1)          # 等權日報酬
        eqw=eqw.clip(-0.15,0.15)                          # 去極端(殘留異常)
        lvl=(1+eqw.fillna(0)).cumprod()*100              # 指數化
        industries[ind]=[round(float(x),3) for x in lvl.reindex(master).values]
    # 全市場: TAIEX
    txc=tix.sort_values("date").set_index("date")["close"].astype(float).reindex(master).ffill()
    industries["全市場"]=[round(float(x),3) for x in txc.values]
    json.dump({"dates":out_dates,"industries":industries,"members":sel},
              open("industry_returns.json","w"),ensure_ascii=False)
    print(f"完成:{len(industries)} 個產業序列 → industry_returns.json")

if __name__=="__main__":
    main()
