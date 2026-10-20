#!/usr/bin/env python3
"""一次性:只保留台股脆弱度單頁,刪除美股、產業熱度、資產定價頁與其程式/資料。執行一次後會自動刪除本檔。"""
import os, re, shutil

if os.path.exists("index.html"):
    s = open("index.html", encoding="utf-8").read()
    t = re.sub(r'<nav class="tabs">.*?</nav>\n?', "", s, count=1)
    if t != s:
        open("index.html", "w", encoding="utf-8").write(t); print("已移除 index.html 分頁列")
FILES = ["us.html", "us_fragility_dashboard.py", "us_fragility_history.csv",
         "industry_heat.html", "industry_heat.py", "heat_fetch.py", "heat_members.json",
         "build_industry.py", "industry_returns.json", "screener.py", "universe.json", "ci_check.py",
         "asset_pricing.html", "ap_lab.py", "ap_fetch.py", "ap_px_fetch.py", "ap_factors.py", "ap_factors.json",
         "heat_px_cache.json", "industry_px_cache.json", "ap_px_cache.json", "ap_fund_cache.json"]
for f in FILES:
    if os.path.exists(f):
        os.remove(f); print("已刪除", f)
for d in ["us_cache"]:
    if os.path.isdir(d):
        shutil.rmtree(d); print("已刪除資料夾", d)
os.remove(__file__); print("完成。")
