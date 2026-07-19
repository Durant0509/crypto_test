# Windows 部署（讓一台常開的 Windows 每小時自動跑紙上模擬）

## 懶人版：一行指令

在 Windows 上打開 **PowerShell**（開始 → 打「PowerShell」→ 直接開一般的即可），貼這行按 Enter：

```powershell
irm https://raw.githubusercontent.com/Durant0509/crypto_test/main/windows/bootstrap.ps1 | iex
```

它會自動：
1. 裝好 Git 與 Python（用 winget，若還沒裝）
2. 把專案 clone 到 `C:\crypto_test`
3. 建立 venv、裝套件
4. **問你一次 GitHub PAT**（貼上即可，存本機讓排程能自動 push）
5. 跑一次測試 tick
6. 註冊「每小時自動跑」的排程 `CryptoPaperTick`

跑完就結束了。之後打開 https://durant0509.github.io/crypto_test/ 就看得到，每小時更新。

> 若貼完出現「Git 抓不到、請重開 PowerShell」，就把 PowerShell 關掉、重開一個，再貼一次同一行即可（winget 裝完的 PATH 要新視窗才生效）。

---

## 手動版（如果一行指令卡住）

```powershell
winget install -e --id Git.Git
winget install -e --id Python.Python.3.12
# 關掉 PowerShell、重開一個，然後：
git clone https://github.com/Durant0509/crypto_test.git C:\crypto_test
cd C:\crypto_test
powershell -ExecutionPolicy Bypass -File windows\setup.ps1
```

---

## 常用操作

| 想做的事 | 指令（在 `C:\crypto_test` 裡） |
|---|---|
| 看目前狀態 | 開 https://durant0509.github.io/crypto_test/ |
| 看本機日誌 | `Get-Content data\paper_tick.log -Tail 20` |
| 手動立刻跑一次 | `powershell -ExecutionPolicy Bypass -File windows\paper_tick.ps1` |
| 暫停自動跑 | `schtasks /Change /TN CryptoPaperTick /DISABLE` |
| 恢復自動跑 | `schtasks /Change /TN CryptoPaperTick /ENABLE` |
| 完全移除排程 | `schtasks /Delete /TN CryptoPaperTick /F` |
| 看排程狀態 | `schtasks /Query /TN CryptoPaperTick` |

## 跑哪些實驗？

每小時一次的 tick 會**同時**跑 6 個**獨立**的紙上模擬（各自虛擬 1000 U）：

| 實驗 | 幣種 | 參數 | 出場 | 槓桿上限 | 帳本檔 |
|---|---|---|---|---|---|
| 基準（原本在跑） | BTC | lookback **90天** | 固定3天 | ≤3× | `ledger.json` |
| 調優 | ADA | **45天** | 固定3天 | ≤2× | `exp_ada-tuned.json` |
| 調優 | BTC | **45天** | 固定3天 | ≤3× | `exp_btc-tuned.json` |
| 調優 | DOGE | **45天** | 固定3天 | ≤2× | `exp_doge-tuned.json` |
| 調優·A/B | ADA | **45天** | **正規化出場** | ≤2× | `exp_ada-tuned-norm.json` |
| 調優·A/B | BTC | **45天** | **正規化出場** | ≤3× | `exp_btc-tuned-norm.json` |

- 45 天 lookback 是「前推驗證」樣本外最佳（ADA/BTC/DOGE 皆優於 90 天）。
- 最後兩檔是**出場改造 A/B**：跟固定3天版並排跑「正規化出場」（百分位回中性 40–60% 就平倉），驗證樣本外的改善（BTC 1.58→1.73、ADA 1.68→1.82）在實盤是否重現。DOGE 不做正規化（樣本外會變差）。
- 網頁「實時模擬」分頁會把 4 檔並排成卡片，每張卡標明**該實驗的參數類別**（幣種 / lookback / 門檻 / 持有 / 槓桿上限）。
- 首次啟動時 ADA/DOGE 會各自從 Binance 公開資料下載約 110 天歷史暖機（約 2–4 分鐘），之後每小時自動更新。

## 清算資料收集器（養新策略的資料，一次性設定）

免費的幣安清算資料**只有即時 WebSocket、沒有歷史**，所以要「現在開始收、養幾個月」才能回測清算因子。這台常開的 Windows 是收集的最佳位置。

**一次性設定**（在 `C:\crypto_test` 裡跑一次）：
```powershell
git pull
powershell -ExecutionPolicy Bypass -File windows\setup_liq_collector.ps1
```

它會：裝 `websocket-client`、註冊一個**常駐**排程 `CryptoLiqCollector`（登入時自動啟動、崩潰自動重連，跟每小時的 `CryptoPaperTick` 分開）、立刻開始收全市場清算事件。

- 資料每小時落一個檔：`data\liquidations\liq-YYYY-MM-DD.parquet`（原始事件：幣種/方向/價格/數量/名目/時間戳）
- 每小時的 paper tick 會順便把這些 parquet commit+push 上 GitHub（所以 Mac 端回測讀得到）
- 看是否在收：`Get-Content data\liq_collector.log -Tail 15` 和 `dir data\liquidations`
- 暫停：`schtasks /Change /TN CryptoLiqCollector /DISABLE`

> 注意：清算是稀疏事件，市場平靜時可能幾分鐘沒半筆，波動時每秒數十筆——log 裡看到 "flushed N events" 就是正常運作。

## 注意事項

- 排程在 **使用者登入時** 執行，所以請讓這台機器 **保持登入**（螢幕可以鎖，但別登出/關機）。
- 紙上模擬帳本（`paper_state/*.json`）是跟著 repo 走的，所以會**接續** Mac 之前跑到的狀態，不會從頭開始。
- 這台只要別跟 Mac 同時跑就好（Mac 端已經停掉了）。
- `windows\paper_tick.ps1` 一次跑完 4 檔並一起 commit/push，用的還是原本的排程 `CryptoPaperTick`，不需要重新註冊。
