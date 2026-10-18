#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""建置後處理:替每張頁面蓋上 build-id、掛上 autoupdate.js,並產生 version.json。

頁面開著時,autoupdate.js 會定期比對 version.json;某頁內容變了才會提示重新整理。
可重複執行(冪等):已蓋過的標記會先移除再重蓋。
"""
import datetime as dt
import hashlib
import json
import os
import re

PAGES = ["index.html", "us.html", "industry_heat.html", "asset_pricing.html", "methodology.html"]
MARK = re.compile(r'<meta name="build-id"[^>]*>\s*(?:<script src="i18n\.js[^"]*"></script>\s*)?<script src="autoupdate\.js[^"]*" defer></script>\s*')
ASOF = re.compile(r"更新於\s*(\d{4}-\d{2}-\d{2})")
DAY = re.compile(r'"(\d{4}-\d{2}-\d{2})"')
MON = re.compile(r'"(\d{4}-\d{2})"')


def file_hash(path, n=12):
    return hashlib.sha1(open(path, "rb").read()).hexdigest()[:n]


def data_asof(html):
    m = ASOF.search(html)
    if m:
        return m.group(1)
    today = dt.date.today().isoformat()
    days = [d for d in DAY.findall(html) if d <= today]
    if days:
        return max(days)
    mons = [d for d in MON.findall(html) if d <= today[:7]]
    return max(mons) if mons else None


def main():
    js_v = file_hash("autoupdate.js", 8) if os.path.exists("autoupdate.js") else "0"
    i18n_v = file_hash("i18n.js", 8) if os.path.exists("i18n.js") else None
    pages = {}
    for p in PAGES:
        if not os.path.exists(p):
            continue
        html = open(p, encoding="utf-8").read()
        html = MARK.sub("", html)
        bid = hashlib.sha1(html.encode("utf-8")).hexdigest()[:12]
        i18n = f'<script src="i18n.js?v={i18n_v}"></script>' if i18n_v else ""
        tag = f'<meta name="build-id" content="{bid}">{i18n}<script src="autoupdate.js?v={js_v}" defer></script>\n'
        html = html.replace("</head>", tag + "</head>", 1)
        open(p, "w", encoding="utf-8").write(html)
        pages[p] = {"id": bid, "asof": data_asof(html)}
        print(f"  {p:22} build-id={bid}  資料至 {pages[p]['asof']}")
    ver = {"built": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), "pages": pages}
    json.dump(ver, open("version.json", "w"), ensure_ascii=False, indent=1)
    print("→ version.json")


if __name__ == "__main__":
    main()
