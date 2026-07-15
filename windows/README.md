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

## 注意事項

- 排程在 **使用者登入時** 執行，所以請讓這台機器 **保持登入**（螢幕可以鎖，但別登出/關機）。
- 紙上模擬帳本（`paper_state/ledger.json`）是跟著 repo 走的，所以它會**接續** Mac 之前跑到的狀態，不會從頭開始。
- 這台只要別跟 Mac 同時跑就好（Mac 端已經停掉了）。
