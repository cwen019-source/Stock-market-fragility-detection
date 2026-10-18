#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""產生 i18n.js = 英文字典(i18n_en.json + i18n_overrides.json)+ i18n_engine.js。
新增或修正翻譯:編輯 i18n_overrides.json({"中文片語": "English"}),再執行本檔(CI 每次建置都會跑)。"""
import json
import os

d = json.load(open("i18n_en.json", encoding="utf-8")) if os.path.exists("i18n_en.json") else {}
if os.path.exists("i18n_overrides.json"):
    d.update(json.load(open("i18n_overrides.json", encoding="utf-8")))
d = {k: v for k, v in d.items() if k and isinstance(v, str)}
eng = open("i18n_engine.js", encoding="utf-8").read()
open("i18n.js", "w", encoding="utf-8").write(
    "window.I18N_EN=" + json.dumps(d, ensure_ascii=False, separators=(",", ":")) + ";\n" + eng)
print(f"i18n.js:{len(d)} 條翻譯")
