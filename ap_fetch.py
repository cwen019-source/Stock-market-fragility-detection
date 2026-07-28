#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""資產定價實驗室 — 基本面抓取層(可重複執行, 遇速率限制自動續抓)。
每檔股票抓 4 個資料集, 只保留建構因子所需欄位 → ap_fund_cache.json
  TaiwanStockPER                 PBR / PER / 殖利率(取月底) → B/M、E/P、D/P
  TaiwanStockFinancialStatements EPS/營收/毛利/營益/稅後淨利/母公司權益 → RMW、ROE
  TaiwanStockBalanceSheet        總資產/權益/股本 → CMA(資產成長)、I/A
  TaiwanStockShareholding        發行股數 → 市值(SMB)
"""
import os, json, time, sys
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from screener import fetch, _lock

CACHE = "ap_fund_cache.json"
FS_KEEP = {"EPS", "Revenue", "GrossProfit", "OperatingIncome", "IncomeAfterTaxes",
           "EquityAttributableToOwnersOfParent", "PreTaxIncome"}
BS_KEEP = {"TotalAssets", "TotalEquity", "CapitalStock", "TotalLiabilities",
           "EquityAttributableToOwnersOfParent", "TotalNonCurrentLiabilities"}

def load_cache():
    if os.path.exists(CACHE):
        try: return json.load(open(CACHE))
        except Exception: return {}
    return {}

def save_cache(c):
    tmp = CACHE + ".tmp"; json.dump(c, open(tmp, "w")); os.replace(tmp, CACHE)

def q_pivot(rows, keep):
    """長格式(date,type,value) → {type: {季末日: 值}},只留需要的科目。"""
    out = {}
    for r in rows:
        t = r.get("type")
        if t not in keep: continue
        try: v = float(r.get("value"))
        except (TypeError, ValueError): continue
        out.setdefault(t, {})[r["date"]] = v
    return out

def work(sid):
    res = {}
    per = fetch("TaiwanStockPER", sid)
    if per is None: return sid, None, "rate"
    if per:
        df = pd.DataFrame(per); df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        m = df[["PBR", "PER", "dividend_yield"]].apply(pd.to_numeric, errors="coerce").resample("ME").last()
        res["val"] = {"m": [str(d.date())[:7] for d in m.index],
                      "pbr": [None if pd.isna(x) else round(float(x), 3) for x in m["PBR"]],
                      "per": [None if pd.isna(x) else round(float(x), 2) for x in m["PER"]],
                      "dy":  [None if pd.isna(x) else round(float(x), 3) for x in m["dividend_yield"]]}
    fs = fetch("TaiwanStockFinancialStatements", sid)
    if fs is None: return sid, None, "rate"
    if fs: res["fs"] = q_pivot(fs, FS_KEEP)
    bs = fetch("TaiwanStockBalanceSheet", sid)
    if bs is None: return sid, None, "rate"
    if bs: res["bs"] = q_pivot(bs, BS_KEEP)
    sh = fetch("TaiwanStockShareholding", sid)
    if sh is None: return sid, None, "rate"
    if sh:
        try:
            df = pd.DataFrame(sh); df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")
            s = pd.to_numeric(df["NumberOfSharesIssued"], errors="coerce").dropna()
            if len(s):
                idx = df.loc[s.index, "date"]
                mm = pd.Series(s.values, index=idx).resample("ME").last().dropna()
                res["sh"] = {"m": [str(d.date())[:7] for d in mm.index],
                             "n": [float(x) for x in mm.values]}
        except Exception: pass
    return sid, res, "ok"

def main(max_passes=40):
    uni = json.load(open("universe.json"))
    need = sorted({u["stock_id"] for u in uni})          # 全市場 2145 檔
    cache = load_cache()
    print(f"需 {len(need)} 檔基本面 / 已快取 {len(cache)}", flush=True)
    for p in range(max_passes):
        todo = [s for s in need if s not in cache]
        print(f"[pass {p}] 待抓 {len(todo)}", flush=True)
        if not todo:
            print("=== 全數完成 ===", flush=True); break
        ok = rate = 0
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(work, s): s for s in todo}
            for f in as_completed(futs):
                try: sid, data, st = f.result()
                except Exception: continue
                if st == "rate": rate += 1; continue
                with _lock: cache[sid] = data or {}
                ok += 1
                if ok % 50 == 0:
                    save_cache(cache); print(f"   已抓 {ok}/{len(todo)}(速率限制 {rate})", flush=True)
        save_cache(cache)
        print(f"[pass {p}] 完成 {ok}, 速率限制 {rate}", flush=True)
        if rate: time.sleep(150)
    save_cache(cache)
    n_val = len([1 for v in cache.values() if v.get("val")])
    n_fs  = len([1 for v in cache.values() if v.get("fs")])
    n_bs  = len([1 for v in cache.values() if v.get("bs")])
    n_sh  = len([1 for v in cache.values() if v.get("sh")])
    print(f"完成: 估值{n_val} 損益{n_fs} 資產負債{n_bs} 股數{n_sh}", flush=True)

if __name__ == "__main__":
    main()
