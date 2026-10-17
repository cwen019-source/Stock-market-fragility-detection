#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脆弱度警報信:結構層 6 個燈號中,≥ ALERT_MIN_RED 個亮紅(危險度 ≥ ALERT_LEVEL)→ 寄信並附上統計。

規則與頁面上的「亮紅 x/6」完全相同(讀取頁面內嵌資料,不重算)。
同一市場、同一資料日只寄一次(記錄在 alert_state.json),避免週六重建台股頁時重複寄。

環境變數
  SMTP_USER / SMTP_PASS   寄件帳號 / 應用程式密碼(Gmail 需開兩步驟驗證後產生「應用程式密碼」)
  ALERT_TO                收件人(逗號分隔可多位),預設寄給 SMTP_USER
  SMTP_HOST / SMTP_PORT   預設 smtp.gmail.com / 465
  ALERT_MIN_RED=4  ALERT_LEVEL=75  HIST_DAYS=60
  SITE_URL                儀表板網址(放在信中連結)
  DRY_RUN=1               不寄信,改把信存成 alert_preview.eml
  FORCE=1                 忽略「同一天只寄一次」;若都未達門檻,改寄一封 [測試] 現況信
"""
import datetime as dt
import io
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from html import escape

import pandas as pd

MARKETS = [("TW", "台股", "index.html"), ("US", "美股", "us.html")]
MIN_RED = int(os.environ.get("ALERT_MIN_RED", "4"))
LEVEL = float(os.environ.get("ALERT_LEVEL", "75"))
HIST_DAYS = int(os.environ.get("HIST_DAYS", "60"))
STATE = "alert_state.json"


def load_page_data(path):
    s = open(path, encoding="utf-8").read()
    i = s.index("const D=") + len("const D=")
    d, _ = json.JSONDecoder().raw_decode(s, i)
    return d


def light(x):
    if x is None:
        return "–"
    return "紅" if x >= LEVEL else ("黃" if x >= 55 else "綠")


def red_count(d, i):
    return sum(1 for k in d["slow"] if d["inds"][k]["dng"][i] is not None and d["inds"][k]["dng"][i] >= LEVEL)


def analyse(code, name, path):
    d = load_page_data(path)
    i = len(d["dates"]) - 1
    slow = set(d["slow"])
    rows = []
    for k in d["order"]:
        v = d["inds"][k]
        dg = v["dng"][i]
        rows.append({
            "指標": v["label"], "層": "結構(計入)" if k in slow else v["group"],
            "最新值": v["val"][i], "單位": v["unit"], "危險度(PIT百分位)": dg,
            "燈號": light(dg), "計入6燈": "是" if k in slow else "",
            "說明": v.get("note", ""),
        })
    snap = pd.DataFrame(rows)
    # 近 N 個交易日:綜合分數、亮紅數、觸發、各結構燈號危險度
    lo = max(0, i - HIST_DAYS + 1)
    hist = {"日期": d["dates"][lo:i + 1], "綜合脆弱度": d["comp"][lo:i + 1],
            f"亮紅數(/{len(d['slow'])})": [red_count(d, j) for j in range(lo, i + 1)],
            "快層觸發": d["trig"][lo:i + 1]}
    for k in d["slow"]:
        hist[d["inds"][k]["label"]] = d["inds"][k]["dng"][lo:i + 1]
    hist = pd.DataFrame(hist).iloc[::-1]
    return {
        "code": code, "name": name, "page": path, "asof": d["dates"][i], "comp": d["comp"][i],
        "trig": bool(d["trig"][i]), "red": red_count(d, i), "n": len(d["slow"]),
        "prev_red": red_count(d, i - 1) if i else None, "snap": snap, "hist": hist,
    }


def upcoming(days=None):
    """macro_calendar.json 的未來事件(days=None → 全部三個月)"""
    try:
        from macro_calendar import short_ref
        evs = json.load(open("macro_calendar.json", encoding="utf-8"))["events"]
        evs = [{**e, "ref": short_ref(e.get("ref", ""))} for e in evs]
    except Exception:
        return []
    today = dt.date.today().isoformat()
    end = (dt.date.today() + dt.timedelta(days=days)).isoformat() if days else "9999"
    return [e for e in evs if today <= e["date"] <= end]


def build_xlsx(hits):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        summary = pd.DataFrame([{"市場": h["name"], "資料日": h["asof"], "綜合脆弱度": h["comp"],
                                 "亮紅數": f"{h['red']}/{h['n']}", "前一日亮紅": h["prev_red"],
                                 "快層觸發": "是" if h["trig"] else "否"} for h in hits])
        summary.to_excel(xw, sheet_name="摘要", index=False)
        for h in hits:
            h["snap"].to_excel(xw, sheet_name=f"{h['name']}燈號", index=False)
            h["hist"].to_excel(xw, sheet_name=f"{h['name']}近{HIST_DAYS}日", index=False)
        ev = upcoming()
        if ev:
            pd.DataFrame([{"台灣時間": e["tw"], "美東日期": e["date"], "美東時間": e["et"], "事件": e["name"],
                           "參考期間": e.get("ref", ""), "重要度(5最高)": e["tier"], "備註": e.get("note", ""),
                           "來源": e["src"]} for e in ev]).to_excel(xw, sheet_name="未來三個月事件", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                w = max(len(str(c.value or "")) for c in col)
                ws.column_dimensions[col[0].column_letter].width = min(max(8, w * 1.6), 50)
            ws.freeze_panes = "A2"
    return buf.getvalue()


def html_body(hits, site):
    parts = [f"<p>結構脆弱度燈號{'現況(測試信,未必達門檻)' if all(h['red'] < MIN_RED for h in hits) else '達警戒門檻'}(6 燈中 ≥{MIN_RED} 個危險度 ≥{LEVEL:.0f})。統計明細見附件 Excel。</p>"]
    for h in hits:
        url = f"{site.rstrip('/')}/{h['page']}" if site else ""
        title = f"{h['name']}脆弱度 {h['comp']:.0f}/100 · 亮紅 {h['red']}/{h['n']} · 資料日 {h['asof']}"
        parts.append(f"<h3 style='margin:18px 0 6px'>{escape(title)}</h3>")
        parts.append(f"<p style='margin:0 0 8px;color:#555'>快層觸發:{'<b style=\"color:#c0392b\">是</b>' if h['trig'] else '否'}"
                     f"{' · 前一日亮紅 ' + str(h['prev_red']) if h['prev_red'] is not None else ''}"
                     f"{' · <a href=\"' + escape(url) + '\">開啟儀表板</a>' if url else ''}</p>")
        rows = "".join(
            "<tr>" + f"<td>{escape(str(r['指標']))}</td><td style='text-align:right'>{r['最新值']}{escape(r['單位'] or '')}</td>"
            f"<td style='text-align:right;font-weight:600;color:{'#c0392b' if r['燈號']=='紅' else '#b77c00' if r['燈號']=='黃' else '#1e8449'}'>{r['危險度(PIT百分位)']}</td>"
            f"<td>{escape(r['層'])}</td></tr>"
            for _, r in h["snap"].iterrows())
        parts.append("<table cellpadding='5' style='border-collapse:collapse;font-size:13px' border='1' bordercolor='#ddd'>"
                     "<tr style='background:#f4f4f2'><th>指標</th><th>最新值</th><th>危險度</th><th>層</th></tr>" + rows + "</table>")
    ev = upcoming(14)
    if ev:
        rows = "".join(f"<tr><td>{escape(e['tw'])}</td><td style='font-weight:{700 if e['tier'] == 5 else 400};"
                       f"color:{'#c0392b' if e['tier'] == 5 else '#333'}'>{escape(e['name'])}</td><td>{escape(e.get('ref', ''))}</td></tr>"
                       for e in ev)
        parts.append("<h3 style='margin:18px 0 6px'>未來兩週美國關鍵數據 / FOMC(台灣時間)</h3>"
                     "<table cellpadding='5' style='border-collapse:collapse;font-size:13px' border='1' bordercolor='#ddd'>"
                     "<tr style='background:#f4f4f2'><th>時間</th><th>事件</th><th>期間</th></tr>" + rows + "</table>"
                     "<p style='color:#555;font-size:12px;margin:4px 0'>脆弱度高時遇到這些事件,波動放大的風險較高;完整三個月清單見附件。</p>")
    parts.append("<p style='color:#888;font-size:12px'>危險度=PIT 擴張百分位(只用當日及以前資料)。壓力計非擇時工具,非投資建議。</p>")
    return "<html><body style='font-family:-apple-system,\"PingFang TC\",\"Microsoft JhengHei\",sans-serif'>" + "".join(parts) + "</body></html>"


def main():
    try:
        state = json.load(open(STATE))
    except Exception:
        state = {}
    force = os.environ.get("FORCE") == "1"
    hits, allm = [], []
    for code, name, page in MARKETS:
        if not os.path.exists(page):
            continue
        try:
            h = analyse(code, name, page)
        except Exception as e:
            print(f"[{code}] 無法讀取 {page}: {e}")
            continue
        print(f"[{code}] {h['asof']} 亮紅 {h['red']}/{h['n']}(門檻 {MIN_RED})")
        allm.append(h)
        if h["red"] >= MIN_RED and (force or state.get(code) != h["asof"]):
            hits.append(h)
    test = False
    if not hits and force and allm:
        hits, test = allm, True          # 手動測試:未達門檻也寄一封(標示為測試)
    if not hits:
        print("未達門檻或今日已寄過,不寄信。")
        return 0

    msg = EmailMessage()
    user = os.environ.get("SMTP_USER", "")
    to = os.environ.get("ALERT_TO") or user
    msg["From"] = f"脆弱度儀表板 <{user or 'noreply@example.com'}>"
    msg["To"] = to
    msg["Subject"] = ("[測試] 脆弱度現況:" if test else "⚠ 脆弱度警報:") + ";".join(f"{h['name']} 亮紅 {h['red']}/{h['n']}(分數 {h['comp']:.0f})" for h in hits) \
        + f" · {max(h['asof'] for h in hits)}"
    msg.set_content("\n".join(f"{h['name']} {h['asof']} 脆弱度 {h['comp']:.0f}/100 亮紅 {h['red']}/{h['n']}" for h in hits)
                    + "\n統計明細見附件。非投資建議。")
    msg.add_alternative(html_body(hits, os.environ.get("SITE_URL", "")), subtype="html")
    asof = max(h["asof"] for h in hits)
    msg.add_attachment(build_xlsx(hits), maintype="application",
                       subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       filename=f"fragility_stats_{asof}.xlsx")

    if os.environ.get("DRY_RUN") == "1":
        open("alert_preview.eml", "wb").write(bytes(msg))
        print(f"DRY_RUN:信件已存成 alert_preview.eml(收件人 {to})")
        return 0
    if not (user and os.environ.get("SMTP_PASS")):
        print("::warning::達警報門檻,但未設定 SMTP_USER / SMTP_PASS,無法寄信")
        return 1
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
            s.login(user, os.environ["SMTP_PASS"]); s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=ctx); s.login(user, os.environ["SMTP_PASS"]); s.send_message(msg)
    for h in hits:
        if not test:
            state[h["code"]] = h["asof"]
    json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=1)
    print(f"已寄出警報給 {to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
