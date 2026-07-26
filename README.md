# 台股脆弱度儀表板 — GitHub Actions 每日自動版

每個交易日收盤後自動抓資料、重算脆弱度、含**美國 VIX**(伺服器端跑 Python,無瀏覽器 CORS 限制),
把 `index.html` 與歷史分數 `fragility_history.csv` 提交回 repo,由 **GitHub Pages** 發佈成固定網址。
含**脆弱度歷史趨勢線**與融資追繳壓力測試。純風控壓力計,非投資建議。

## 一次性設定(約 5 分鐘)

1. 建一個新的 GitHub repository(Public),把本資料夾所有檔案上傳:
   - `fragility_dashboard.py`、`requirements.txt`、`index.html`、`fragility_history.csv`
   - `.github/workflows/daily.yml`
2. **開啟 Pages**:repo → Settings → Pages → Source 選「Deploy from a branch」→ 分支 `main`、資料夾 `/ (root)` → Save。
   稍等一兩分鐘,網址為 `https://<你的帳號>.github.io/<repo名>/`。
3. **確認 Actions 有寫入權限**:Settings → Actions → General → 「Workflow permissions」選 **Read and write permissions** → Save。
4. (可選)**提高 FinMind 上限**:Settings → Secrets and variables → Actions → New repository secret,
   Name = `FINMIND_TOKEN`,Value = 你的 FinMind token(免費註冊 finmindtrade.com)。
5. 先手動跑一次驗證:repo → Actions → 選「daily-fragility-dashboard」→ Run workflow。
   跑完後打開你的 Pages 網址就會看到最新儀表板。

之後不用管它:每個工作日 UTC 09:30(台灣 17:30)自動更新,`fragility_history.csv` 會逐日累積,
歷史趨勢線也會越來越長(頁面另有「回溯」歷史線,第一天就有多年資料可看)。

## 調整
- 改觸發時間:編輯 `.github/workflows/daily.yml` 的 `cron`(UTC 時間)。
- 改指標權重/門檻:編輯 `fragility_dashboard.py` 上方的 `weights` 與各指標。

## 資料源
- FinMind:融資餘額 / 加權指數+成交值 / 三大法人(外資)/ USD-TWD
- FRED:美國 VIX(VIXCLS)/ S&P500

## 免責
本專案為風險分析框架,所有數據僅供研究參考,**非投資建議**。

---

## 產業子分析(新增)

儀表板中段的「產業子分析」提供 AI/熱度題材籃子按鈕(AI伺服器代工、PCB載板、被動元件、晶圓代工封測、IC設計、矽智財IP、散熱、光通訊CPO、記憶體、重電電力、網通設備、電源連接、機殼機構 + 全市場)。點選任一產業後,下方 8 個燈號會**依「該指標對該產業未來 20 日報酬的預測力(Spearman rank-IC)」在目前選定的日期區間內重新排序**,並標出每個指標的 IC 值與名次。切換日期區間(1/3/5年或自訂)會即時重算 IC。

- 產業報酬 = 每題材籃子成分股(可於 `build_industry.py` 的 `THEMES` 自行增修)(還原股價)的**等權日報酬指數**(`build_industry.py` 產生 `industry_returns.json`)。
- `industry_returns.json` 已含在 repo,`fragility_dashboard.py` 會自動嵌入;若檔案不存在則自動隱藏此區塊。
- **每週自動更新**:`.github/workflows/weekly-industry.yml` 每週六重建並提交 `industry_returns.json`(較重,約抓 276 檔;建議設定 `FINMIND_TOKEN` secret)。
- rank-IC 為描述性統計、樣本有限,**非投資建議**,亦非嚴謹的因子回測(未做 point-in-time / 多重檢定校正)。
