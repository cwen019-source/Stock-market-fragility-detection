#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""資產定價實驗室 — 全市場月頻股價抓取(2145 檔)。
因子模型(FF/Carhart/q-factor)標準頻率為月頻, 故只存月底還原價與月成交值,
相較日頻可把快取縮小約 30 倍。已有的日頻快取會自動重取樣沿用, 不重抓。
→ ap_px_cache.json  {sid: {m:[YYYY-MM], px:[還原月底價], tv:[月成交值(百萬)]}}
"""
import os, json, time
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from screener import fetch, adj_close, _lock

CACHE = "ap_px_cache.json"

def load_cache():
    if os.path.exists(CACHE):
        try: return json.load(open(CACHE))
        except Exception: return {}
    return {}

def save_cache(c):
    tmp = CACHE + ".tmp"; json.dump(c, open(tmp, "w")); os.replace(tmp, CACHE)

def to_monthly(s_daily, tv_daily=None):
    m = s_daily.resample("ME").last().dropna()
    out = {"m": [str(d.date())[:7] for d in m.index],
           "px": [round(float(x), 4) for x in m.values]}
    if tv_daily is not None:
        t = tv_daily.resample("ME").sum().reindex(m.index).fillna(0)
        out["tv"] = [round(float(x), 2) for x in t.values]
    return out

def seed_from_daily(cache):
    """沿用產業熱度的日頻快取(624 檔), 重取樣成月頻。"""
    p = "heat_px_cache.json"
    if not os.path.exists(p): return 0
    try: old = json.load(open(p))
    except Exception: return 0
    n = 0
    for sid, v in old.items():
        if sid in cache or not v.get("val"): continue
        idx = pd.to_datetime(v["idx"])
        s = pd.Series(v["val"], index=idx).sort_index()
        tv = pd.Series(v["tv"], index=idx).sort_index() if v.get("tv") else None
        cache[sid] = to_monthly(s, tv); n += 1
    return n

def work(sid):
    rows = fetch("TaiwanStockPrice", sid)
    if rows is None: return sid, None, "rate"
    if not rows: return sid, {}, "empty"
    s = adj_close(rows)
    if s is None or not len(s): return sid, {}, "bad"
    s.index = pd.to_datetime(s.index)
    df = pd.DataFrame(rows); df["date"] = pd.to_datetime(df["date"])
    tv = df.sort_values("date").set_index("date")["Trading_money"].astype(float) / 1e6
    tv = tv.reindex(s.index).fillna(0)
    return sid, to_monthly(s, tv), "ok"

def main(max_passes=60):
    uni = json.load(open("universe.json"))
    need = sorted({u["stock_id"] for u in uni})
    cache = load_cache()
    got = seed_from_daily(cache)
    if got: save_cache(cache)
    print(f"全市場 {len(need)} 檔 / 已快取 {len(cache)}(自日頻沿用 {got})", flush=True)
    # 月頻資料:上個月底之後就該有新的一筆,否則視為過期需重抓
    import datetime as _dt
    _t = _dt.date.today()
    _prev = (_t.replace(day=1) - _dt.timedelta(days=1)).strftime("%Y-%m")
    def is_stale(sid):
        d = cache.get(sid)
        if d is None: return True
        m = d.get("m") or []
        if not m: return False                 # 已知無資料者不重試
        return m[-1] < _prev                   # 最後一個月早於上月 -> 重抓
    for p in range(max_passes):
        todo = [s for s in need if is_stale(s)]
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
                with _lock: cache[sid] = data
                ok += 1
                if ok % 100 == 0:
                    save_cache(cache); print(f"   {ok}/{len(todo)}(限制 {rate})", flush=True)
        save_cache(cache)
        print(f"[pass {p}] 完成 {ok}, 速率限制 {rate}", flush=True)
        if rate: time.sleep(150)
    save_cache(cache)
    print(f"完成: {len([1 for v in cache.values() if v.get('px')])} 檔月頻股價", flush=True)

if __name__ == "__main__":
    main()
