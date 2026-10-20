#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脆弱度警報信:停買燈號。

寄信規則
  ‧ 台股:四個停買燈號(融資綜合、外資流向、台幣、VIX)中 ≥3 燈危險度 ≥75 → 當日寄一封(同一資料日不重複寄)
  ‧ 美股:六燈中 ≥4 燈 ≥75
  狀態記在 alert_state.json。

環境變數
  SMTP_USER / SMTP_PASS   寄件帳號 / Gmail 應用程式密碼
  ALERT_TO                收件人(逗號分隔),預設 SMTP_USER
  SMTP_HOST / SMTP_PORT   預設 smtp.gmail.com / 465
  SITE_URL                儀表板網址
  ALERT_MIN_RED_TW / ALERT_MIN_RED_US   覆寫門檻(預設:台股取頁面的 stopn=3,美股 4)
  ALERT_LEVEL             紅燈門檻(預設 75)
  DRY_RUN=1               不寄,存成 alert_preview.eml
  FORCE=1                 強制寄一封(未達條件時標示 [測試])
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

STATE = "alert_state.json"
LEVEL_V1 = float(os.environ.get("ALERT_LEVEL", "75"))
PAGE = {"TW": "index.html", "US": "us.html"}



def upcoming(days=None):
    try:
        from macro_calendar import short_ref
        evs = json.load(open("macro_calendar.json", encoding="utf-8"))["events"]
        evs = [{**e, "ref": short_ref(e.get("ref", ""))} for e in evs]
    except Exception:
        return []
    today = dt.date.today().isoformat()
    end = (dt.date.today() + dt.timedelta(days=days)).isoformat() if days else "9999"
    return [e for e in evs if today <= e["date"] <= end]


# ====================================================================== 停買燈號
def v1_market(code, path):
    s = open(path, encoding="utf-8").read()
    i = s.index("const D=") + len("const D=")
    d, _ = json.JSONDecoder().raw_decode(s, i)
    t = len(d["dates"]) - 1
    red = sum(1 for k in d["slow"] if d["inds"][k]["dng"][t] is not None and d["inds"][k]["dng"][t] >= LEVEL_V1)
    need = int(os.environ.get(f"ALERT_MIN_RED_{code}") or d.get("stopn") or (3 if code == "TW" else 4))
    rows = pd.DataFrame([{"指標": d["inds"][k]["label"], "最新值": d["inds"][k]["val"][t], "危險度": d["inds"][k]["dng"][t],
                          "計入停買燈號": k in d["slow"]} for k in d["order"]])
    return {"code": code, "name": "台股" if code == "TW" else "美股", "asof": d["dates"][t], "red": red,
            "n": len(d["slow"]), "need": need, "comp": d["comp"][t], "rows": rows}


# ====================================================================== 寄送
def send(msg):
    user = os.environ.get("SMTP_USER", "")
    if os.environ.get("DRY_RUN") == "1":
        open("alert_preview.eml", "wb").write(bytes(msg))
        print(f"DRY_RUN:已存成 alert_preview.eml(收件人 {msg['To']})")
        return True
    if not (user and os.environ.get("SMTP_PASS")):
        print("::warning::達寄信條件,但未設定 SMTP_USER / SMTP_PASS,無法寄信")
        return False
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
            s.login(user, os.environ["SMTP_PASS"]); s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=ctx); s.login(user, os.environ["SMTP_PASS"]); s.send_message(msg)
    print(f"已寄出警報給 {msg['To']}")
    return True


def xlsx(sheets):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for n, df in sheets.items():
            df.to_excel(xw, sheet_name=n[:31], index=False)
        for ws in xw.book.worksheets:
            for c in ws.columns:
                w = max(len(str(x.value or "")) for x in c)
                ws.column_dimensions[c[0].column_letter].width = min(max(8, w * 1.5), 60)
            ws.freeze_panes = "A2"
    return buf.getvalue()


def events_html():
    ev = upcoming(14)
    if not ev:
        return ""
    rows = "".join(f"<tr><td>{escape(e['tw'])}</td><td style='font-weight:{700 if e['tier'] == 5 else 400};"
                   f"color:{'#c0392b' if e['tier'] == 5 else '#333'}'>{escape(e['name'])}</td><td>{escape(e.get('ref', ''))}</td></tr>" for e in ev)
    return ("<h3 style='margin:18px 0 6px'>未來兩週美國關鍵數據 / FOMC(台灣時間)</h3>"
            "<table cellpadding='5' style='border-collapse:collapse;font-size:13px' border='1' bordercolor='#ddd'>"
            "<tr style='background:#f4f4f2'><th>時間</th><th>事件</th><th>期間</th></tr>" + rows + "</table>")


def events_sheet():
    ev = upcoming()
    return pd.DataFrame([{"台灣時間": e["tw"], "美東日期": e["date"], "美東時間": e["et"], "事件": e["name"],
                          "參考期間": e.get("ref", ""), "重要度(5最高)": e["tier"], "來源": e["src"]} for e in ev]) if ev else None


def main():
    try:
        state = json.load(open(STATE))
    except Exception:
        state = {}
    force = os.environ.get("FORCE") == "1"
    user = os.environ.get("SMTP_USER", "")
    site = os.environ.get("SITE_URL", "")
    msg = EmailMessage()
    msg["From"] = f"脆弱度儀表板 <{user or 'noreply@example.com'}>"
    msg["To"] = os.environ.get("ALERT_TO") or user or "noreply@example.com"

    hits = []
    for code, page in PAGE.items():
        if not os.path.exists(page):
            continue
        h = v1_market(code, page)
        print(f"[{code}] {h['asof']} 亮紅 {h['red']}/{h['n']}(門檻 {h['need']})")
        prev = state.get(code) if isinstance(state.get(code), str) else (state.get(code) or {}).get("v1_sent")
        if (h["red"] >= h["need"] and prev != h["asof"]) or force:
            hits.append(h)
    if not hits:
        print("未達門檻或今日已寄過,不寄信。")
        return 0
    test = all(h["red"] < h["need"] for h in hits)
    msg["Subject"] = ("[測試] 燈號現況:" if test else "⛔ 停買警報:") + ";".join(
        f"{h['name']} 亮紅 {h['red']}/{h['n']}" + ("(停買)" if h["red"] >= h["need"] else "") for h in hits) + f" · {max(h['asof'] for h in hits)}"
    msg.set_content("\n".join(f"{h['name']} {h['asof']} 亮紅 {h['red']}/{h['n']}" for h in hits))
    intro = ("<p>台股規則:前一交易日收盤四個停買燈號中 ≥3 燈達 75 分 → 隔日不新買進(賣出、停損照常)。"
             + (f"<a href=\"{escape(site)}\">儀表板</a>" if site else "") + "</p>")
    msg.add_alternative("<html><body>" + intro + "".join(f"<h3>{h['name']} 亮紅 {h['red']}/{h['n']}(門檻 {h['need']})· {h['asof']}</h3>" + h["rows"].to_html(index=False)
                                                 for h in hits) + events_html() + "</body></html>", subtype="html")
    msg.add_attachment(xlsx({f"{h['name']}燈號": h["rows"] for h in hits}), maintype="application",
                       subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=f"fragility_lights_{max(h['asof'] for h in hits)}.xlsx")
    sent = send(msg)
    if sent and os.environ.get("DRY_RUN") != "1" and not test:
        for h in hits:
            state[h["code"]] = {**(state.get(h["code"]) if isinstance(state.get(h["code"]), dict) else {}), "v1_sent": h["asof"]}
    json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=1)
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
