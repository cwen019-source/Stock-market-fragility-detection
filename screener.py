#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股「本益比超高 → 暴跌 → 回到原高 / 永遠沉寂」篩選器
=====================================================
資料來源: FinMind 開放 API (免費層, 單檔查詢)
- TaiwanStockPER   : 每日本益比 (PER) / 股價淨值比 / 殖利率, 約 2008 起
- TaiwanStockPrice : 每日未還原收盤價

因免費層無法取得還原股價, 本程式利用台股「每日 ±10% 漲跌幅限制」自行還原:
任何單日 |報酬| > 13% 幾乎必為除權/減資/分割等公司行為(而非真實成交),
將其視為調整日並剔除跳空, 重建連續的還原價格序列。真實崩跌是連續多根跌停,
會被完整保留。

篩選邏輯 (每檔股票):
在還原價格序列上偵測所有「崩跌事件」——某個價格高點之後下跌 >= 60% 到低點;
每個事件同時要求:
  (1) 估值: 高點前 180 日內最高本益比 > 60          → 「本益比超高」
  (2) 資金行情: 高點 >= 前 2 年(約500交易日)最低價 * 1.5 → 排除因獲利崩壞造成的假高本益比
  (3) 崩跌: 高點到低點跌幅 >= 60%                    → 「暴跌」
分類: 崩跌後股價是否曾再度回到高點的 90% 以上
  → 是: 「回到原高」    否: 「永遠沉寂」
"""
import os, json, time, threading, random
import requests, pandas as pd, numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed

API = "https://api.finmindtrade.com/api/v4/data"
START, END = "2005-01-01", "2026-07-24"

# ---- 可調參數 --------------------------------------------------------------
PE_TH    = 60      # 本益比「超高」門檻
DD_TH    = 0.60    # 崩跌門檻 (跌幅 >= 60%)
RECOV_TH = 0.90    # 回到原高門檻 (回到峰值的 90%)
RUNUP    = 1.5     # 資金行情門檻 (峰值 >= 前2年低點 * 1.5)
CA_GAP   = 0.13    # 單日 |log報酬| 超過此值視為公司行為(還原用)
WORKERS  = 3
# ---------------------------------------------------------------------------

_session = requests.Session()
_lock = threading.Lock()

def fetch(dataset, sid, retries=3):
    """單檔抓取。回傳 list(可能為空)=成功; None=遇速率限制(本回合放棄, 下回合重試)。"""
    for a in range(retries):
        try:
            r = _session.get(API, params={"dataset":dataset,"data_id":sid,
                                          "start_date":START,"end_date":END}, timeout=60)
            if r.status_code == 200:
                return r.json().get("data", [])
            msg = ""
            try: msg = r.json().get("msg","")
            except Exception: pass
            if r.status_code in (402,429) or "limit" in msg.lower():
                return None          # 速率限制 → 立即放棄(不睡), 交由回合間長休眠處理
            return []                # 其他狀況視為無資料
        except Exception:
            time.sleep(2*(a+1))      # 僅網路例外才短暫重試
    return None

def adj_close(rows):
    p = pd.DataFrame(rows)
    if not len(p): return None
    p['date']  = pd.to_datetime(p['date'])
    p['close'] = pd.to_numeric(p['close'], errors='coerce')
    p = p[p['close']>0].sort_values('date').reset_index(drop=True)
    if len(p) < 60: return None
    logr = np.log(p['close']/p['close'].shift(1))
    ca   = logr.abs() > CA_GAP                     # 公司行為 / 壞資料
    real = logr.where(~ca, 0.0).fillna(0.0)
    p['adj'] = p['close'].iloc[0]*np.exp(real.cumsum())
    return p.set_index('date')['adj']

def find_episodes(v):
    """回傳 [(peak_idx, trough_idx, recovered_bool)] , 崩跌後重置以偵測多次泡沫。"""
    n=len(v); out=[]; i=0; cur=0
    while i<n:
        if v[i]>=v[cur]: cur=i
        if v[i] <= (1-DD_TH)*v[cur]:
            pk=cur; j=i; tr=i
            while j<n and v[j] < RECOV_TH*v[pk]:
                if v[j]<v[tr]: tr=j
                j+=1
            out.append((pk,tr, j<n))
            cur=tr; i=tr+1; continue
        i+=1
    return out

def analyze(sid, per_rows, price_rows):
    s = adj_close(price_rows)
    if s is None: return None
    v = s.values; idx = s.index
    pe = pd.DataFrame(per_rows)
    pe['date']=pd.to_datetime(pe['date']); pe['PER']=pd.to_numeric(pe['PER'],errors='coerce')
    per = pe.set_index('date')['PER']
    best=None
    for pk,tr,recov in find_episodes(v):
        pkd, pkpx = idx[pk], v[pk]
        lo = v[max(0,pk-500):pk+1].min()                        # 前2年低點
        if pkpx < RUNUP*lo: continue                            # 需有資金行情
        w = per[(per.index>=pkd-pd.Timedelta(days=180)) & (per.index<=pkd+pd.Timedelta(days=15))]
        pe_pk = w[w>0].max() if len(w) else np.nan
        if not (pd.notna(pe_pk) and pe_pk>PE_TH): continue      # 需本益比超高
        dd = 1 - v[tr]/pkpx
        cur = v[-1]
        rebound = v[tr:].max()
        rec = dict(stock_id=sid, peak_date=str(idx[pk].date()), peak_price=round(float(pkpx),2),
                   peak_pe=round(float(pe_pk),1), trough_date=str(idx[tr].date()),
                   trough_price=round(float(v[tr]),2), max_dd=round(float(dd)*100,1),
                   best_recover_pct=round(float(rebound/pkpx)*100,1),
                   cur_price_rel_peak=round(float(cur/pkpx)*100,1),
                   recovered=bool(recov), status=("回到原高" if recov else "永遠沉寂"), _dd=float(dd))
        if best is None or rec['_dd']>best['_dd']: best=rec
    if best:
        best.pop('_dd')
        # 附上供繪圖的月線 (還原價 + 本益比)
        sm = s.resample('ME').last()
        pm = per.resample('ME').last()
        best['spark_dates'] = [str(d.date()) for d in sm.index]
        best['spark_px']    = [None if pd.isna(x) else round(float(x),3) for x in sm.values]
        best['spark_pe']    = [None if pd.isna(x) else round(float(x),1) for x in pm.reindex(sm.index).values]
    return best

def worker(u):
    sid=u['stock_id']
    per_rows = fetch("TaiwanStockPER", sid)
    if per_rows is None: return ('retry', sid, None)
    if not per_rows:     return ('done', sid, None)
    per = pd.to_numeric(pd.DataFrame(per_rows).get('PER', pd.Series(dtype=float)), errors='coerce')
    if not (per>PE_TH).any(): return ('done', sid, None)        # 從未有超高本益比 → 略過抓價
    price_rows = fetch("TaiwanStockPrice", sid)
    if price_rows is None: return ('retry', sid, None)
    if not price_rows:     return ('done', sid, None)
    res = analyze(sid, per_rows, price_rows)
    return ('done', sid, res)

def load_done():
    done=set()
    if os.path.exists("results.jsonl"):
        for line in open("results.jsonl"):
            line=line.strip()
            if not line: continue
            done.add(json.loads(line)['stock_id'])
    return done

def one_pass(todo, out):
    t0=time.time(); n=0; hits=0; retry=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs={ex.submit(worker,u):u for u in todo}
        for f in as_completed(futs):
            u=futs[f]; sid=u['stock_id']
            try: tag,_,res=f.result()
            except Exception: tag,res='retry',None
            if tag=='retry': retry.append(u); continue
            if res:
                res.update(name=u['name'], type=u['type'], industry=u['industry']); hits+=1
            with _lock:
                out.write(json.dumps({'stock_id':sid,'result':res},ensure_ascii=False)+"\n"); out.flush()
            n+=1
            if n%100==0:
                print(f"  處理 {n}  命中 {hits}  用時 {time.time()-t0:.0f}s", flush=True)
    return n, hits, retry

def main(max_passes=40):
    uni = json.load(open("universe.json"))
    out=open("results.jsonl","a")
    for p in range(max_passes):
        done=load_done()
        todo=[u for u in uni if u['stock_id'] not in done]
        print(f"[pass {p}] universe={len(uni)} 已完成={len(done)} 待處理={len(todo)}", flush=True)
        if not todo:
            print("=== 全數完成 ===", flush=True); break
        n,hits,retry=one_pass(todo,out)
        print(f"[pass {p}] 本輪成功 {n} 檔, 命中 {hits}, 因速率限制延後 {len(retry)}", flush=True)
        if retry:                       # 被速率限制擋下 → 等額度回補再跑下一輪
            time.sleep(75)
    out.close()

if __name__=="__main__":
    main()
