#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脆弱度警報信(預設用 v2 結構風險模型;ALERT_RULE=v1 可改回舊版「6 燈 ≥4 亮紅」)。

v2 寄信規則(依 fragility_v2.json 的警示等級;等級定義見 methodology.html):
  ‧ 等級升到 ≥2(結構脆弱)或比上次通知更高 → 寄
  ‧ 等級 3 主動減碼(結構脆弱 ≥2 且快速觸發成立)→ 每個資料日寄
  ‧ 維持 ≥2 → 每週一提醒一次
  ‧ 從 ≥2 降回 <2 → 寄一封「解除」
  狀態記在 alert_state.json(notified_level 只在成功寄出後更新,寄失敗不會漏掉升級)。

環境變數
  SMTP_USER / SMTP_PASS   寄件帳號 / Gmail 應用程式密碼
  ALERT_TO                收件人(逗號分隔),預設 SMTP_USER
  SMTP_HOST / SMTP_PORT   預設 smtp.gmail.com / 465
  SITE_URL                儀表板網址
  ALERT_RULE=v2|v1        v1:舊版規則(ALERT_MIN_RED=4、ALERT_LEVEL=75)
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
RULE = os.environ.get("ALERT_RULE", "v2")
MIN_RED = int(os.environ.get("ALERT_MIN_RED", "4"))
LEVEL_V1 = float(os.environ.get("ALERT_LEVEL", "75"))
PAGE = {"TW": "index.html", "US": "us.html"}
LV = {0: ("平穩", "#1e8449"), 1: ("留意", "#2a78d6"), 2: ("結構脆弱", "#b77c00"), 3: ("主動減碼", "#c0392b")}


def pc(x):
    return "–" if x is None else f"{round(x * 100)}"


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


# ====================================================================== v2
def v2_markets():
    d = json.load(open("fragility_v2.json", encoding="utf-8"))
    return d["markets"]


def v2_decide(m, st, force):
    """回傳 (是否寄, 原因)。st = 該市場狀態 dict。"""
    lvl, asof = m["level"], m["asof"]
    notified = st.get("notified_level", 0)
    last = st.get("last_sent")
    tw_today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    if lvl >= 2 and lvl > notified:
        return True, "等級升高"
    if lvl == 3 and last != asof:
        return True, "已觸發(每日)"
    if lvl >= 2 and tw_today.weekday() == 0 and (not last or (tw_today - dt.date.fromisoformat(last)).days >= 6):
        return True, "每週提醒"
    if lvl < 2 and notified >= 2:
        return True, "解除"
    return (True, "測試") if force else (False, "")


def v2_section(m, reason, site):
    name, color = LV[m["level"]]
    url = f"{site.rstrip('/')}/{PAGE[m['code']]}" if site else ""
    gar = m.get("gar10")
    head = (f"<h3 style='margin:18px 0 4px'>{escape(m['name'])} · <span style='color:{color}'>{name}</span>"
            f"<span style='color:#888;font-weight:400;font-size:13px'>({escape(reason)})· 資料至 {m['asof']}</span></h3>")
    kpi = (f"<p style='margin:0 0 8px;color:#444'>綜合脆弱度 <b>{pc(m.get('composite_pct'))}</b> 百分位 · "
           f"紅區(信用×股價)<b style='color:{'#c0392b' if m.get('redzone') else '#1e8449'}'>{'是' if m.get('redzone') else '否'}</b> · "
           f"12 個月壞情境(第10百分位){'–' if gar is None else f'<b>{gar * 100:+.0f}%</b>'} · "
           f"快速觸發 <b>{'成立' if m.get('trigger') else '未觸發'}</b>"
           f"{' · <a href=\"' + escape(url) + '\">儀表板</a>' if url else ''}</p>")
    rows = ""
    for k, p in m["pillars"].items():
        s = p.get("score")
        c = "#c0392b" if (s or 0) >= 0.8 else "#b77c00" if (s or 0) >= 0.6 else "#1e8449"
        items = "<br>".join(f"{escape(i['label'])}:{escape(i.get('disp', '–'))}({pc(i.get('pct'))})" for i in p["items"])
        rows += (f"<tr><td><b>{k}</b> {escape(p['name'])}</td><td style='text-align:right;font-weight:700;color:{c}'>{pc(s)}</td>"
                 f"<td style='font-size:12px;color:#555'>{items}</td></tr>")
    tbl = ("<table cellpadding='5' style='border-collapse:collapse;font-size:13px' border='1' bordercolor='#ddd'>"
           "<tr style='background:#f4f4f2'><th>類別</th><th>分數</th><th>指標:最新值(百分位)</th></tr>" + rows + "</table>")
    bt = m.get("backtest", {}).get("table", {}).get("v2 警示 ≥2", {})
    note = ""
    if bt:
        note = (f"<p style='color:#666;font-size:12px;margin:4px 0'>回測 {m['backtest'].get('window', '')}:警示≥2 後 12 個月內出現 ≥20% 跌幅的機率 "
                f"{pc(bt.get('p_event_given_signal'))}%(全期 {pc(bt.get('p_event_uncond'))}%)。結構脆弱是「萬一出事會多慘」的尾端風險,非短線擇時。</p>")
    return head + kpi + tbl + note


def v2_sheets(ms):
    sh = {"摘要": pd.DataFrame([{"市場": m["name"], "資料日": m["asof"], "等級": f"{m['level']} {LV[m['level']][0]}",
                                 "綜合脆弱度百分位": m.get("composite_pct"), "紅區": m.get("redzone"),
                                 "信用成長百分位": m.get("credit_growth_pct"), "3年漲幅百分位": m.get("price_growth_pct"),
                                 "12月第10百分位報酬(GaR)": m.get("gar10"), "快速觸發": m.get("trigger")} for m in ms])}
    for m in ms:
        nm = "台股" if m["code"] == "TW" else "美股"
        sh[f"{nm}指標"] = pd.DataFrame([{"類別": f"{k} {p['name']}", "類別分數": p.get("score"), "指標": i["label"],
                                          "最新值": i.get("disp"), "歷史百分位": i.get("pct")}
                                         for k, p in m["pillars"].items() for i in p["items"]])
        h = m["history"]
        hist = pd.DataFrame({"月份": h["dates"], "綜合分數": h["comp"], "綜合百分位": h["comp_pct"], "等級": h["level"],
                             "紅區": h["redzone"], "GaR10": h["gar"], **{k: h[k] for k in ("V", "L", "M", "S")}})
        sh[f"{nm}近36月"] = hist.tail(36).iloc[::-1]
        bt = m.get("backtest", {}).get("table", {})
        if bt:
            sh[f"{nm}回測"] = pd.DataFrame([{"規則": k, **v} for k, v in bt.items()])
    return sh


# ====================================================================== v1(舊版)
def v1_market(code, path):
    s = open(path, encoding="utf-8").read()
    i = s.index("const D=") + len("const D=")
    d, _ = json.JSONDecoder().raw_decode(s, i)
    t = len(d["dates"]) - 1
    red = sum(1 for k in d["slow"] if d["inds"][k]["dng"][t] is not None and d["inds"][k]["dng"][t] >= LEVEL_V1)
    rows = pd.DataFrame([{"指標": d["inds"][k]["label"], "最新值": d["inds"][k]["val"][t], "危險度": d["inds"][k]["dng"][t],
                          "計入6燈": k in d["slow"]} for k in d["order"]])
    return {"code": code, "name": "台股" if code == "TW" else "美股", "asof": d["dates"][t], "red": red,
            "n": len(d["slow"]), "comp": d["comp"][t], "rows": rows}


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

    use_v2 = RULE == "v2" and os.path.exists("fragility_v2.json")
    if use_v2:
        ms = v2_markets()
        picks = []
        for code in ("TW", "US"):
            m = ms.get(code)
            if not m:
                continue
            st = state.get(code) if isinstance(state.get(code), dict) else {}
            ok, why = v2_decide(m, st, force)
            print(f"[{code}] {m['asof']} 等級 {m['level']}(上次通知 {st.get('notified_level', 0)})→ {'寄' if ok else '不寄'} {why}")
            st.setdefault("notified_level", 0)
            state[code] = {**st, "level": m["level"], "asof": m["asof"]}
            if ok:
                picks.append((m, why))
        if not picks:
            json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=1)
            print("未達寄信條件。")
            return 0
        test = all(w == "測試" for _, w in picks)
        tag = "[測試] 脆弱度現況" if test else ("✅ 脆弱度解除" if all(w == "解除" for _, w in picks) else "⚠ 脆弱度警報")
        msg["Subject"] = f"{tag}:" + ";".join(f"{m['name'][:2]} {LV[m['level']][0]}(綜合 {pc(m.get('composite_pct'))})" for m, _ in picks) \
            + f" · {max(m['asof'] for m, _ in picks)}"
        body = "".join(v2_section(m, w, site) for m, w in picks)
        intro = ("<p>結構風險 v2:四類脆弱性(估值/槓桿/景氣/市場壓力)+ 紅區 + 快速觸發。等級定義與回測見 "
                 f"{'<a href=\"' + escape(site.rstrip('/') + '/methodology.html') + '\">方法論</a>' if site else 'methodology.html'}。附件為完整統計。</p>")
        msg.set_content("\n".join(f"{m['name']} {m['asof']} 等級 {m['level']} {LV[m['level']][0]}({w})" for m, w in picks) + "\n非投資建議。")
        msg.add_alternative("<html><body style='font-family:-apple-system,\"PingFang TC\",\"Microsoft JhengHei\",sans-serif'>" + intro + body +
                            events_html() + "<p style='color:#888;font-size:12px'>數字為 PIT 歷史百分位(只用當時已公布資料)。非投資建議。</p></body></html>",
                            subtype="html")
        sheets = v2_sheets([m for m, _ in picks])
        es = events_sheet()
        if es is not None:
            sheets["未來三個月事件"] = es
        asof = max(m["asof"] for m, _ in picks)
        msg.add_attachment(xlsx(sheets), maintype="application", subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           filename=f"fragility_v2_{asof}.xlsx")
        sent = send(msg)
        if sent and os.environ.get("DRY_RUN") != "1" and not test:
            for m, w in picks:
                state[m["code"]].update({"notified_level": m["level"], "last_sent": m["asof"]})
        json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=1)
        return 0 if sent else 1

    # ---------------- v1 舊版規則 ----------------
    hits = []
    for code, page in PAGE.items():
        if not os.path.exists(page):
            continue
        h = v1_market(code, page)
        print(f"[{code}] {h['asof']} 亮紅 {h['red']}/{h['n']}(門檻 {MIN_RED})")
        prev = state.get(code) if isinstance(state.get(code), str) else (state.get(code) or {}).get("v1_sent")
        if (h["red"] >= MIN_RED and prev != h["asof"]) or force:
            hits.append(h)
    if not hits:
        print("未達門檻或今日已寄過,不寄信。")
        return 0
    msg["Subject"] = "⚠ 脆弱度警報(v1):" + ";".join(f"{h['name']} 亮紅 {h['red']}/{h['n']}" for h in hits)
    msg.set_content("\n".join(f"{h['name']} {h['asof']} 亮紅 {h['red']}/{h['n']}" for h in hits))
    msg.add_alternative("<html><body>" + "".join(f"<h3>{h['name']} 亮紅 {h['red']}/{h['n']} · {h['asof']}</h3>" + h["rows"].to_html(index=False)
                                                 for h in hits) + events_html() + "</body></html>", subtype="html")
    msg.add_attachment(xlsx({f"{h['name']}燈號": h["rows"] for h in hits}), maintype="application",
                       subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename="fragility_v1.xlsx")
    sent = send(msg)
    if sent and os.environ.get("DRY_RUN") != "1":
        for h in hits:
            state[h["code"]] = {**(state.get(h["code"]) if isinstance(state.get(h["code"]), dict) else {}), "v1_sent": h["asof"]}
    json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=1)
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
