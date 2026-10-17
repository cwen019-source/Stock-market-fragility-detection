#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 防呆:快取覆蓋率不足(例如被速率限制、快取被 GitHub 清掉)時,不要用殘缺資料重建頁面。
用法: python ci_check.py heat|ap   → 覆蓋率 >= 門檻 exit 0,否則 exit 1(沿用上一版頁面)。"""
import json
import os
import sys

MIN_COVER = float(os.environ.get("MIN_COVER", "0.9"))


def ratio(cache_file, need, ok):
    if not os.path.exists(cache_file):
        return 0.0
    c = json.load(open(cache_file))
    return sum(1 for s in need if s in c and ok(c[s])) / max(len(need), 1)


def main(which):
    if which == "heat":
        if not os.path.exists("heat_members.json"):
            return 0.0
        import datetime as dt
        recent = (dt.date.today() - dt.timedelta(days=7)).isoformat()
        need = {s for v in json.load(open("heat_members.json")).values() for s in v}
        # 已知無資料(空序列)算已處理;有資料者需在 7 天內更新過
        return ratio("heat_px_cache.json", need, lambda v: not v.get("idx") or v["idx"][-1] >= recent)
    if which == "ap":
        need = {u["stock_id"] for u in json.load(open("universe.json"))}
        a = ratio("ap_px_cache.json", need, lambda v: "m" in v or v == {})
        b = ratio("ap_fund_cache.json", need, lambda v: True)
        return min(a, b)
    raise SystemExit(f"unknown: {which}")


if __name__ == "__main__":
    r = main(sys.argv[1])
    print(f"{sys.argv[1]} cache coverage {r:.1%} (need >= {MIN_COVER:.0%})")
    sys.exit(0 if r >= MIN_COVER else 1)
