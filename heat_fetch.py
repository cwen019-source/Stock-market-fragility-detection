#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""產業熱度雷達 — 股價抓取層(可重複執行, 遇速率限制自動續抓)。
每個官方產業別取若干代表股(股號小→大, 為成立較久/規模較大的粗略代理),
抓還原股價 + 成交值 → heat_px_cache.json。抓完再依實際成交值篩掉冷門股。"""
import os, json, time
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from screener import fetch, adj_close, _lock

CACHE = "heat_px_cache.json"
PER_IND = 18          # 每產業先抓這麼多檔(之後再依成交值留前10)
MIN_MEMB = 5

# 官方產業別變體合併(…類 / …業 視為同一產業)
MERGE = {
    "綠能環保類": "綠能環保", "數位雲端類": "數位雲端", "居家生活類": "居家生活",
    "運動休閒類": "運動休閒", "金融業": "金融保險", "其他電子業": "其他電子類",
    "化學生技醫療": "化學工業", "文化創意事業": "文化創意業",
}
DROP = {"存託憑證", "其他", "大盤", ""}     # 非產業/雜項

def norm_ind(x):
    x = (x or "").strip()
    return MERGE.get(x, x)

def pick_universe():
    uni = json.load(open("universe.json"))
    by = {}
    for u in uni:
        ind = norm_ind(u.get("industry"))
        if ind in DROP or not ind:
            continue
        by.setdefault(ind, []).append(u)
    sel = {}
    for ind, lst in by.items():
        if len(lst) < MIN_MEMB:
            continue
        lst = sorted(lst, key=lambda x: (x["type"] != "twse", x["stock_id"]))  # 上市優先, 股號小優先
        sel[ind] = lst[:PER_IND]
    return sel

def load_cache():
    if os.path.exists(CACHE):
        try:
            return json.load(open(CACHE))
        except Exception:
            return {}
    return {}

def save_cache(c):
    tmp = CACHE + ".tmp"
    json.dump(c, open(tmp, "w"))
    os.replace(tmp, CACHE)

def seed_from_theme_cache(cache):
    """沿用先前 AI 題材已抓好的還原股價, 不重抓。"""
    p = "industry_px_cache.json"
    if not os.path.exists(p):
        return 0
    try:
        old = json.load(open(p))
    except Exception:
        return 0
    n = 0
    for sid, v in old.items():
        if sid not in cache:
            cache[sid] = {"idx": v["idx"], "val": v["val"], "tv": None}
            n += 1
    return n

def work(sid):
    rows = fetch("TaiwanStockPrice", sid)
    if rows is None:
        return sid, None, "rate"
    if not rows:
        return sid, None, "empty"
    s = adj_close(rows)
    if s is None or not len(s):
        return sid, None, "bad"
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    tv = df.sort_values("date").set_index("date")["Trading_money"].astype(float)
    tv = tv.reindex(pd.to_datetime(s.index)).fillna(0)
    return sid, {"idx": [str(d.date()) for d in pd.to_datetime(s.index)],
                 "val": [round(float(x), 4) for x in s.values],
                 "tv":  [round(float(x) / 1e6, 2) for x in tv.values]}, "ok"

def main(max_passes=40):
    sel = pick_universe()
    need = sorted({u["stock_id"] for lst in sel.values() for u in lst})
    json.dump({k: [u["stock_id"] for u in v] for k, v in sel.items()},
              open("heat_members.json", "w"), ensure_ascii=False)
    cache = load_cache()
    got = seed_from_theme_cache(cache)
    if got:
        save_cache(cache)
    print(f"產業 {len(sel)} 個 / 需股票 {len(need)} 檔 / 已快取 {len(cache)}(沿用題材快取 {got})", flush=True)
    import datetime as _dt
    stale_before = str((_dt.date.today() - _dt.timedelta(days=4)))
    def is_stale(sid):
        d = cache.get(sid)
        if d is None: return True
        idx = d.get("idx") or []
        if not idx: return False              # 已知無資料者不重試
        return idx[-1] < stale_before         # 最後一筆過舊 -> 重抓
    for p in range(max_passes):
        todo = [s for s in need if is_stale(s)]
        print(f"[pass {p}] 待抓 {len(todo)}", flush=True)
        if not todo:
            print("=== 全數完成 ===", flush=True)
            break
        ok = rate = 0
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(work, s): s for s in todo}
            for f in as_completed(futs):
                try:
                    sid, data, st = f.result()
                except Exception:
                    continue
                if st == "rate":
                    rate += 1
                    continue
                if data is None:
                    with _lock:
                        cache[sid] = {"idx": [], "val": [], "tv": []}   # 記為已處理的空檔
                    continue
                with _lock:
                    cache[sid] = data
                ok += 1
                if ok % 40 == 0:
                    save_cache(cache)
                    print(f"   已抓 {ok} / 本回合 {len(todo)}(速率限制 {rate})", flush=True)
        save_cache(cache)
        print(f"[pass {p}] 完成 {ok} 檔, 速率限制 {rate} 次", flush=True)
        if rate:
            time.sleep(90)
    save_cache(cache)
    print(f"快取共 {len([k for k,v in cache.items() if v.get('val')])} 檔有效股價", flush=True)

if __name__ == "__main__":
    main()
