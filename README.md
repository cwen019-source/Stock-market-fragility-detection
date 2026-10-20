# 台股脆弱度儀表板 — GitHub Actions 每日自動版

單一頁面 `index.html`,**台灣時間每個交易日早上 07:50(開盤前)自動更新**,頁面開著也會提示新資料。
指標:融資(三項合一為「融資綜合」)、外資流向、台幣、加權指數,加上美股 / 韓股 VIX 等外部因素。
各指標一律轉成 **PIT 擴張百分位**(第 t 日只用當日及以前的資料,無前視偏誤)。
停買規則:四個停買燈號(融資綜合、外資、台幣、VIX)中 ≥3 燈危險度 ≥75 → 隔日不新買進。純風控工具,非投資建議。

## 為什麼排在早上 07:50(開盤前)

- **台股**:前一交易日的融資餘額/三大法人約在前一晚 21:00 公布 → 這時跑可拿到**完整的前一日收盤資料**。
- **美股**:UTC 23:50 = 美東 19:50,美股 16:00 已收盤 → 拿到的是**昨夜美股收盤**。

所以你八點開盤前看到的,是「台股前一日收盤 + 美股昨夜收盤」,正好是開盤前決定要不要降槓桿該看的東西。

## 一次性設定(約 5 分鐘)

1. 把本資料夾所有檔案推到 GitHub repository 的 `main` 分支(含 `.github/workflows/update.yml`、`.gitignore`)。
   股價快取(`heat_px_cache.json`、`ap_*_cache.json` 等)**不進版控**,改由 GitHub Actions cache 保存、每天只抓增量。
2. **開啟 Pages**:repo → Settings → Pages → Source 選 **「GitHub Actions」**(不是 Deploy from a branch)。
3. **Actions 寫入權限**:Settings → Actions → General → Workflow permissions → **Read and write permissions** → Save。
4. **FinMind token(強烈建議)**:Settings → Secrets and variables → Actions → New repository secret,
   Name = `FINMIND_TOKEN`。免 token 的匿名額度較少,建議設定。
5. 手動跑一次:Actions → `update-and-deploy` → Run workflow → scope 選 `all`(第一次會比較久)。

## 自動更新怎麼運作

| 時間(台灣) | 範圍 | 內容 |
|---|---|---|
| 週一~五 07:50 | daily | 台股脆弱度頁、警報信 |
| 週六 10:17 | weekly | 重建台股頁 |
| push 到 main | deploy-only | 只重新部署,不抓資料 |

- 抓資料失敗會自動重試;失敗時沿用上一版頁面。
- 建置完 `stamp.py` 會替頁面蓋上 build-id 並產生 `version.json`。
- **頁面開著也會更新**:`autoupdate.js` 每 10 分鐘、切回分頁、恢復網路時檢查 `version.json`;
  有新資料 → 右下角提示「重新整理」;離開分頁超過 30 分鐘再回來 → 直接載入新版。
  若建置超過 4 天沒跑,頁面會顯示「自動更新可能停擺」。
- Service worker 對本站頁面採 network-first + 重新驗證(資料沒變只回 304),離線時退回快取。

GitHub cron 一律用 UTC(台灣 07:50 = UTC 前一天 23:50,所以星期也往前挪一天:`50 23 * * 0-4`);
尖峰時段可能延遲 5~20 分鐘,屬正常。

## 中 / English 切換

每頁右上角(主題按鈕旁)有 **EN / 中文** 按鈕,選擇會記住並套用到所有頁面;也可用網址 `?lang=en` 直接開英文。
- 預設中文。英文由 `i18n.js` 在瀏覽器端依字典翻譯(含各頁 JS 動態產生的內容);公司名稱為專有名詞,保持原文。
- 未來三個月行事曆直接輸出中英兩版(翻譯更精準)。
- 修正或新增翻譯:編輯 `i18n_overrides.json`(`{"中文片語": "English"}`),CI 每次建置會執行 `build_i18n.py` 重新產生 `i18n.js`。
- 警報信維持中文。

## 停買燈號(2026-10 改版)

台股停買規則:**前一交易日收盤,四個停買燈號中 ≥3 燈危險度 ≥75 → 隔日不新買進**(賣出、停損照常)。
四燈 = 融資綜合(融資水位、融資對 GDP 成長、融資 6 個月成長三項,每月以過去資料重算 PCA 權重合一)、外資 20 日流向、台幣 20 日變動、VIX。
融資三項彼此高度相關(Spearman 0.69–0.80),分開計票會讓同一主題主導,所以合併。門檻 75 事前固定,未以交易資料調整。
依據(1,530 筆實際買進,2025/1–2026/10):停買日的買進 106 筆、勝率 7%、−563 萬;其他買進 1,424 筆、勝率 50%、+801 萬。

結構風險 v2(四類脆弱性、CISS、紅區、GaR)已於 2026-10 移除:它的訊號日買進勝率反而較高(結構等級 ≥2:50%、+322 萬;
警戒:55%、+243 萬),結構等級 ≥2 自 2025-10 起持續亮燈、無法作為停買依據,且依其減碼的指數回測幾乎沒有改善(年化 9.1% vs 9.4%)。

## 警報信

每次台股頁重建後執行 `alert.py`:四個停買燈號 **≥3 燈危險度 ≥75** 就寄信,
規則與頁面上「亮紅 x/4」相同。信件內含燈號表,附件 `fragility_stats_<日期>.xlsx`
(摘要 / 各燈號最新值與危險度 / 近 60 日綜合分數、亮紅數、觸發與各燈號危險度)。同一市場同一資料日只寄一次。

設定(Gmail):
1. 寄件的 Gmail 帳號開啟兩步驟驗證 → 到 <https://myaccount.google.com/apppasswords> 產生「應用程式密碼」。
2. repo → Settings → Secrets and variables → Actions → **Secrets**:`SMTP_USER` = 寄件 Gmail,`SMTP_PASS` = 應用程式密碼。
3. (可選)同頁 **Variables**:`ALERT_TO` 改收件人(預設 s12332167890@gmail.com,可逗號分隔多位)、`ALERT_MIN_RED_TW` 改門檻(預設 3)。
4. 測試:Actions → Run workflow → 勾選 `force_alert`(scope 可選 deploy-only)→ 會寄一封信,未達門檻時標示 [測試]。

## 未來三個月:美國關鍵數據與 FOMC(不確定性行事曆)

台股頁的儀表下方會顯示「以最新資料日起算三個月內」的美國重大事件(台灣時間、倒數天數),
上方長條是**每週不確定性密度**(事件重要度加總,紅點 = FOMC 週)。每次每日更新都會重抓官方行事曆(`macro_calendar.py`):

| 重要度 | 事件 | 來源 |
|---|---|---|
| 5 | FOMC 利率決策(含 SEP 點陣圖者另標)、非農就業、CPI | federalreserve.gov、BLS |
| 4 | PCE 物價/個人所得支出、GDP 初值、零售銷售 | BEA JSON、Census |
| 3 | PPI、JOLTS、ECI、GDP 第二/三次估計 | BLS、BEA |

- 某來源抓取失敗時沿用上次 `macro_calendar.json` 裡的日期,並在頁面標示。
- BLS 要求程式請求附聯絡 email:設 Actions **Variable** `BLS_CONTACT`(未設時改用 `SMTP_USER`;都沒有則沿用舊的 BLS 日期)。
- 警報信會附上「未來兩週事件」,Excel 附件多一張「未來三個月事件」。
- 想改成 N 個月:環境變數 `MACRO_MONTHS`。

## 調整
- 改觸發時間:編輯 `.github/workflows/update.yml` 的 `cron`(記得換算 UTC,跨午夜要順便挪星期)。
- 改指標權重/門檻:編輯 `fragility_dashboard.py` 上方的 `WEIGHTS`、`INVERT`、`ORDER`。

## 資料源
**台股**
- FinMind:融資餘額 / 加權指數+成交值 / 三大法人(外資)/ USD-TWD
- FRED:美國 VIX(VIXCLS)/ KOSPI
- Yahoo Finance:費城半導體指數 PHLX SOX(^SOX);抓取失敗時以 iShares SOXX ETF 還原價代理

## 免責
本專案為風險分析框架,所有數據僅供研究參考,**非投資建議**。

