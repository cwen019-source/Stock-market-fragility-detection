# 台股 / 美股脆弱度儀表板 — GitHub Actions 每日自動版

四張頁面,**台灣時間每個交易日早上 07:50(開盤前)自動更新**,頁面開著也會提示新資料:

上方共用分頁列,可直接切換:

| 頁面 | 內容 |
|---|---|
| `index.html` | 台股脆弱度(融資餘額 / 三大法人 / 台幣 / 加權指數 + 美股韓股 VIX 外部因素) |
| `us.html` | 美股脆弱度(**S&P 500 · Nasdaq · 費城半導體 SOX** + FINRA 全市場融資餘額) |
| `industry_heat.html` | 產業熱度雷達(33 個官方產業別的 **pairwise 相關性**,判斷該選股還是買籃子) |
| `asset_pricing.html` | 資產定價實驗室(台股自建 Fama-French / q-factor 因子,月頻,每月更新) |

兩頁互相有切換連結,方法論相同:10 項指標一律轉成 **PIT 擴張百分位**(第 t 日只用當日及以前的資料,
無前視偏誤),加權合成 0–100 脆弱度;含共用釘選游標、個股 K 線 + 月/季/半年線。純風控壓力計,非投資建議。

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
   Name = `FINMIND_TOKEN`。免 token 的匿名額度很快用完,產業熱度(每日 548 檔)與資產定價會抓不完。
5. 手動跑一次:Actions → `update-and-deploy` → Run workflow → scope 選 `all`(第一次會比較久)。

## 自動更新怎麼運作

| 時間(台灣) | 範圍 | 內容 |
|---|---|---|
| 週一~五 07:50 | daily | 台股脆弱度、美股脆弱度、產業熱度雷達;資產定價快取小量續抓 |
| 週六 10:17 | weekly | 題材產業報酬 `industry_returns.json` → 重建台股頁 |
| 每月 1 日 11:23 | monthly | 資產定價實驗室(全市場 2145 檔) |
| push 到 main | deploy-only | 只重新部署,不抓資料 |

- 每張表各自重試、互不影響;抓資料有時間預算(`FETCH_BUDGET_MIN`),被速率限制時不會卡死整個 job。
- 快取覆蓋率不足 90% 時(`ci_check.py`)**沿用上一版頁面**,不會用殘缺資料覆蓋。
- 建置完 `stamp.py` 會替每頁蓋上 build-id 並產生 `version.json`。
- **頁面開著也會更新**:`autoupdate.js` 每 10 分鐘、切回分頁、恢復網路時檢查 `version.json`;
  有新資料 → 右下角提示「重新整理」;離開分頁超過 30 分鐘再回來 → 直接載入新版。
  若建置超過 4 天沒跑,頁面會顯示「自動更新可能停擺」。
- Service worker 對本站頁面採 network-first + 重新驗證(資料沒變只回 304),離線時退回快取。

GitHub cron 一律用 UTC(台灣 07:50 = UTC 前一天 23:50,所以星期也往前挪一天:`50 23 * * 0-4`);
尖峰時段可能延遲 5~20 分鐘,屬正常。

## 中 / English 切換

每頁右上角(主題按鈕旁)有 **EN / 中文** 按鈕,選擇會記住並套用到所有頁面;也可用網址 `?lang=en` 直接開英文。
- 預設中文。英文由 `i18n.js` 在瀏覽器端依字典翻譯(含各頁 JS 動態產生的內容);公司名稱為專有名詞,保持原文。
- v2 面板、未來三個月行事曆、`methodology.html` 直接輸出中英兩版(翻譯更精準)。
- 修正或新增翻譯:編輯 `i18n_overrides.json`(`{"中文片語": "English"}`),CI 每次建置會執行 `build_i18n.py` 重新產生 `i18n.js`。
- 警報信維持中文。

## 主動減碼日(v2 圖上紅色 ▼)

規則(事前固定):**結構等級 ≥2 且快速觸發成立**(跌破長期均線:美股 200 日 / 台股 240 日,或 VIX 5 日漲幅 ≥85 百分位);
中斷 ≥20 個交易日後再成立才算新的一次。方法論頁列出每一個減碼日及之後 1/3/6/12 個月表現,並比較「減碼期間持有 50%」vs 買進持有。
回測結論:減碼日之後的最深跌幅確實較深,依訊號減碼主要降低最大回撤與波動,但年化報酬略低 —— 是風險控管工具,不是賺錢工具。

## 結構風險 v2(建議以此為主;詳見 `methodology.html`)

舊版 6 燈有三個問題:融資三燈同源重複計票(有效獨立資訊約 3 個)、沒有「價格是否有實體支撐」的檢查、沒有定義預測目標也沒量過準度。
v2 依專業框架重建(`fragility_v2.py`、`v2_data.py`、`v2_render.py`):

| 作法 | 來源 | 內容 |
|---|---|---|
| 四類脆弱性,每類一票 | Fed 金融穩定報告 | V 估值 vs 基本面 / L 槓桿與信用 / M 景氣循環 / S 市場壓力 |
| 估值與實體經濟 | Goldman 熊市指標 | 美:CAPE、市值/GDP、市值/企業盈餘、失業率、殖利率曲線、工業生產;台:權值股加權本益比、股價漲幅 vs **實際營收成長** |
| 相關性感知綜合 | ECB CISS | (w∘s)ᵀCₜ(w∘s),多類同時偏高才高 |
| 紅區(信用×價格) | Greenwood-Shleifer 2022 | 信用 3 年成長前 20% 且股價 3 年漲幅前 1/3 |
| 尾端風險 | IMF Growth-at-Risk | 未來 12 個月報酬第 10 百分位(分量迴歸,擴張重估) |
| 準度評估 | Kaminsky-Reinhart 訊號法 | 事件=12 個月內跌 ≥20%;命中率、假警報、NSR,並與舊版同期比較 |

- 全部 PIT:只用當時已公布資料(含公布落後),美股回測 1960 起、台股 2007 起。
- 警示等級事前固定:0 平穩 / 1 留意 / 2 結構脆弱(紅區或 ≥2 類含估值/槓桿 ≥80)/ 3 警戒(結構 ≥1 且快速觸發)。
- **警報信改用 v2**:升到 ≥2 寄、維持 ≥2 每週一提醒、等級 3 每日、降回 <2 寄解除;`ALERT_RULE=v1`(Actions Variable)可改回舊規則。
- 快取在 `v2_cache/`(Actions cache);`v2_seed/` 是進版控的長歷史種子(S&P 500 1927 起、CAPE),來源暫時擋 CI 時備援。

## 警報信(舊版規則說明)

每次台股/美股頁重建後執行 `alert.py`:結構層 6 個燈號中有 **≥4 個危險度 ≥75(亮紅)** 就寄信,
規則與頁面上「亮紅 x/6」相同。信件內含燈號表,附件 `fragility_stats_<日期>.xlsx`
(摘要 / 各燈號最新值與危險度 / 近 60 日綜合分數、亮紅數、觸發與各燈號危險度)。同一市場同一資料日只寄一次。

設定(Gmail):
1. 寄件的 Gmail 帳號開啟兩步驟驗證 → 到 <https://myaccount.google.com/apppasswords> 產生「應用程式密碼」。
2. repo → Settings → Secrets and variables → Actions → **Secrets**:`SMTP_USER` = 寄件 Gmail,`SMTP_PASS` = 應用程式密碼。
3. (可選)同頁 **Variables**:`ALERT_TO` 改收件人(預設 s12332167890@gmail.com,可逗號分隔多位)、`ALERT_MIN_RED` 改門檻(預設 4)。
4. 測試:Actions → Run workflow → 勾選 `force_alert`(scope 可選 deploy-only)→ 會寄一封信,未達門檻時標示 [測試]。

## 未來三個月:美國關鍵數據與 FOMC(不確定性行事曆)

台股、美股頁的儀表下方會顯示「以最新資料日起算三個月內」的美國重大事件(台灣時間、倒數天數),
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
- 改指標權重/門檻:編輯 `fragility_dashboard.py` / `us_fragility_dashboard.py` 上方的 `WEIGHTS`、`INVERT`、`ORDER`。

## 資料源
**台股**
- FinMind:融資餘額 / 加權指數+成交值 / 三大法人(外資)/ USD-TWD
- FRED:美國 VIX(VIXCLS)/ Nasdaq / KOSPI

**美股**(多來源自動切換,頁面會據實標示當次實際採用的來源)
- FRED:SP500 / NASDAQCOM / VIXCLS / NFCI / 高收益債利差 / 名目GDP(首選)
- 備援:Cboe 官方 VIX、芝加哥聯準會 NFCI、SPY/ONEQ/SOXX ETF 還原價代理、世界銀行 GDP
- FINRA:全市場融資餘額(Margin Statistics,月頻,依發布落後 25 天 PIT 對齊)
- stockanalysis.com:SOXX(費半 SOX 代理)與個股搜尋(支援 CORS)

> 費城半導體(SOX)指數本身無免費授權資料源,頁面以 **iShares SOXX ETF 還原價代理**,
> 且 SOXX 於 2021 年由 PHLX SOX 改追蹤 ICE 半導體指數,長期比較請留意。

## 免責
本專案為風險分析框架,所有數據僅供研究參考,**非投資建議**。

---

## 產業子分析(新增)

儀表板中段的「產業子分析」提供 AI/熱度題材籃子按鈕(AI伺服器代工、PCB載板、被動元件、晶圓代工封測、IC設計、矽智財IP、散熱、光通訊CPO、記憶體、重電電力、網通設備、電源連接、機殼機構 + 全市場)。點選任一產業後,下方 8 個燈號會**依「該指標對該產業未來 20 日報酬的預測力(Spearman rank-IC)」在目前選定的日期區間內重新排序**,並標出每個指標的 IC 值與名次。切換日期區間(1/3/5年或自訂)會即時重算 IC。

- 產業報酬 = 每題材籃子成分股(可於 `build_industry.py` 的 `THEMES` 自行增修)(還原股價)的**等權日報酬指數**(`build_industry.py` 產生 `industry_returns.json`)。
- `industry_returns.json` 已含在 repo,`fragility_dashboard.py` 會自動嵌入;若檔案不存在則自動隱藏此區塊。
- **每週自動更新**:`update.yml` 的 weekly 排程每週六重建並提交 `industry_returns.json`(較重,約抓 276 檔;建議設定 `FINMIND_TOKEN` secret)。
- rank-IC 為描述性統計、樣本有限,**非投資建議**,亦非嚴謹的因子回測(未做 point-in-time / 多重檢定校正)。

---

## 產業熱度雷達(`industry_heat.html`)

以產業內**平均兩兩相關 ρ** 偵測資金主題化。**回測否定了原始假設**:ρ 對未來產業報酬的橫斷面
rank-IC ≈ 0(未來20日 −0.009 t=−1.25;未來60日 −0.001 t=−0.13),Δρ 甚至微弱負向。
但 ρ 能預測未來個股離散度(高相關組 1.46 vs 低相關組 1.66),因此正確用法是**兩段式**:

- **要不要參與** → 看動能(近60日籃子報酬,IC +0.051 **t=6.95**)與離散度(IC −0.051 **t=−5.75**)
- **選股還是買籃子** → 看 ρ(高 ρ = 個股同步、選股空間小)

成分股:每官方產業別先取股號較小者最多 18 檔,再依中位成交值留前 10 檔;非市值加權正式指數。
IC 效果量小(0.03–0.05),當排序參考而非訊號。**非投資建議。**
