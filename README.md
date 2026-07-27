# 台股 / 美股脆弱度儀表板 — GitHub Actions 每日自動版

兩張儀表板,**台灣時間每個交易日早上 07:50(開盤前)自動更新**:

| 頁面 | 內容 |
|---|---|
| `index.html` | 台股脆弱度(融資餘額 / 三大法人 / 台幣 / 加權指數 + 美股韓股 VIX 外部因素) |
| `us.html` | 美股脆弱度(**S&P 500 · Nasdaq · 費城半導體 SOX** + FINRA 全市場融資餘額) |

兩頁互相有切換連結,方法論相同:10 項指標一律轉成 **PIT 擴張百分位**(第 t 日只用當日及以前的資料,
無前視偏誤),加權合成 0–100 脆弱度;含共用釘選游標、個股 K 線 + 月/季/半年線。純風控壓力計,非投資建議。

## 為什麼排在早上 07:50(開盤前)

- **台股**:前一交易日的融資餘額/三大法人約在前一晚 21:00 公布 → 這時跑可拿到**完整的前一日收盤資料**。
- **美股**:UTC 23:50 = 美東 19:50,美股 16:00 已收盤 → 拿到的是**昨夜美股收盤**。

所以你八點開盤前看到的,是「台股前一日收盤 + 美股昨夜收盤」,正好是開盤前決定要不要降槓桿該看的東西。

## 一次性設定(約 5 分鐘)

1. 建一個新的 GitHub repository(Public),把本資料夾所有檔案上傳:
   - 台股:`fragility_dashboard.py`、`index.html`、`fragility_history.csv`
   - 美股:`us_fragility_dashboard.py`、`us.html`、`us_fragility_history.csv`
   - 共用:`requirements.txt`、`.github/workflows/daily.yml`
2. **開啟 Pages**:repo → Settings → Pages → Source 選「Deploy from a branch」→ 分支 `main`、資料夾 `/ (root)` → Save。
   稍等一兩分鐘,網址為 `https://<你的帳號>.github.io/<repo名>/`。
3. **確認 Actions 有寫入權限**:Settings → Actions → General → 「Workflow permissions」選 **Read and write permissions** → Save。
4. (可選)**提高 FinMind 上限**:Settings → Secrets and variables → Actions → New repository secret,
   Name = `FINMIND_TOKEN`,Value = 你的 FinMind token(免費註冊 finmindtrade.com)。
5. 先手動跑一次驗證:repo → Actions → 選「daily-fragility-dashboards」→ Run workflow。
   跑完後打開 `https://<帳號>.github.io/<repo>/`(台股)與 `.../us.html`(美股)。

之後不用管它:**台灣時間每個交易日 07:50** 自動更新兩張表,歷史分數 csv 會逐日累積。
(GitHub 排程在尖峰時段可能延遲 5~20 分鐘,屬正常;頁面本身已內建多年回溯歷史線,第一天就有得看。)

## 排程說明

`.github/workflows/daily.yml` 的 cron 是 **`50 23 * * 0-4`**。GitHub cron 一律用 UTC:

- 台灣 = UTC+8 → 台灣 07:50 = UTC 23:50(**前一天**)
- 星期也要跟著往前挪一天 → UTC 週日~週四 (`0-4`) = **台灣 週一~週五**

台股與美股合併在同一個 job(避免兩個工作流同時 `git push` 打架),任一邊失敗會自動重試 3 次,
且**不影響另一邊**;兩邊都失敗時 workflow 才會顯示紅字。

## 調整
- 改觸發時間:編輯 `.github/workflows/daily.yml` 的 `cron`(記得換算 UTC,跨午夜要順便挪星期)。
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
- **每週自動更新**:`.github/workflows/weekly-industry.yml` 每週六重建並提交 `industry_returns.json`(較重,約抓 276 檔;建議設定 `FINMIND_TOKEN` secret)。
- rank-IC 為描述性統計、樣本有限,**非投資建議**,亦非嚴謹的因子回測(未做 point-in-time / 多重檢定校正)。
