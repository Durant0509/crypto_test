"""
Build docs/research.js — the data behind the research dashboard tabs
(多幣種 / 參數優化 / 槓桿安全 / 未來方向).

Reads the JSON artifacts produced by the research scripts, adds a BTC
baseline-vs-tuned equity overlay, bundles a ranked idea backlog, and writes one
inlined `window.RESEARCH = {...}` so the page stays standalone (no server/CORS).

Run the research scripts first, then this:
    python research/multi_coin.py
    python research/param_sweep.py
    python research/leverage_safety.py
    python scripts/build_research.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.engine import BacktestConfig, run          # noqa: E402
from src.backtest.metrics import summary                     # noqa: E402
from src.strategy.signal import Params                       # noqa: E402

DATA = ROOT / "data" / "hourly.parquet"
RESULTS = ROOT / "results"
OUT = ROOT / "docs" / "research.js"
WHITELIST = ["BTCUSDT", "ADAUSDT", "DOGEUSDT"]


def exit_summary():
    """Exit redesign: baseline vs normalize (.40-.60, 5d cap), in-sample + OOS."""
    from research.exit_variants import evalv, walk_forward
    from research.lib import build_hourly
    rows = []
    for sym in WHITELIST:
        df = build_hourly(sym)
        base_is = evalv(df, "time", 3, 0.40, 0.60)
        norm_is = evalv(df, "normalize", 5, 0.40, 0.60)
        base_oos = walk_forward(df, "time", 3, 0.40, 0.60)["oos_sharpe"]
        norm_oos = walk_forward(df, "normalize", 5, 0.40, 0.60)["oos_sharpe"]
        adopt = norm_oos is not None and base_oos is not None and norm_oos > base_oos
        rows.append({
            "coin": sym.replace("USDT", ""),
            "base_sharpe": base_is["sharpe"], "base_oos": base_oos, "base_maxdd": base_is["maxdd_pct"],
            "norm_sharpe": norm_is["sharpe"], "norm_oos": norm_oos, "norm_maxdd": norm_is["maxdd_pct"],
            "verdict": "採用正規化出場" if adopt else "維持固定3天",
        })
    return rows


def research_log():
    """Honest record of what we tested and the verdict (realism-first)."""
    return [
        {"idea": "縮短 lookback 90→45 天", "status": "已採用", "impact": "高",
         "evidence": "樣本外 BTC 1.58→(45d基準)、ADA/DOGE 同樣 45d 樣本外皆優於 90d；訓練窗自動反覆選中 45d。非過擬合。"},
        {"idea": "正規化出場（回中性40-60%平倉，5天上限）", "status": "部分採用", "impact": "中",
         "evidence": "walk-forward：BTC 1.58→1.73、ADA 1.68→1.82（採用）；DOGE 1.15→0.44（否決，維持3天）。"},
        {"idea": "多因子共振（散戶 + 大戶多空比）", "status": "已否決", "impact": "—",
         "evidence": "IC/IR 顯示大戶與散戶同向（非正交）；共振閘門把 Sharpe 腰斬（BTC 1.55→0.73）。樣本內就失敗。"},
        {"idea": "Coinbase Premium 方向濾網", "status": "已否決", "impact": "—",
         "evidence": "溢價正交、正 IC，但當濾網無法提升樣本外 Sharpe（BTC 1.58→1.56、ADA 打平）。唯 DOGE 回撤 -42.6%→-28.5% 可留意。"},
        {"idea": "資金費率 funding（極端/累積）", "status": "待試", "impact": "高",
         "evidence": "有 data.binance.vision 完整歷史可離線回測；美元成本口徑，與散戶人數口徑半正交。下一個候選。"},
        {"idea": "OKX 頂級交易者多空比", "status": "待試（需養資料）", "impact": "高",
         "evidence": "跨交易所聰明錢，最正交；但只有即時、無歷史 → 需架採集器累積數月才能回測。"},
        {"idea": "強平潮 forceOrders", "status": "待試（需養資料）", "impact": "最高",
         "evidence": "理論 alpha 最高（投降式流動性=反轉燃料）；但幣安歷史 dump 已下架，只剩即時 WS。"},
    ]


def factor_ic_table():
    """Merge factor IC (retail/top-trader/taker/OI) + Coinbase premium, BTC as headline."""
    fic = load("factor_ic.json") or {}
    cbp = load("coinbase_premium_ic.json") or []
    rows = []
    btc = (fic.get("BTCUSDT") or {}).get("factors", [])
    label_zh = {"retail L/S (accounts)": "散戶多空比（在用）",
                "top-trader L/S (accounts)": "大戶多空比·帳戶",
                "top-trader L/S (positions)": "大戶多空比·持倉",
                "taker buy/sell vol ratio": "吃單買賣量比",
                "open interest (Δ%)": "未平倉量 Δ%"}
    for r in btc:
        ic = r.get("ic_full")
        rows.append({"factor": label_zh.get(r["label"], r["label"]), "ic": ic,
                     "verdict": _icv(ic)})
    cb_btc = next((c for c in cbp if c["symbol"] == "BTCUSDT"), None)
    if cb_btc:
        rows.append({"factor": "Coinbase Premium（新）", "ic": cb_btc["ic_full"],
                     "verdict": _icv(cb_btc["ic_full"])})
    return rows


def _icv(ic):
    if ic is None:
        return "—"
    a = abs(ic)
    return "紅旗>0.1" if a > 0.1 else ("強" if a > 0.05 else ("有效" if a > 0.03 else "弱/無"))


def load(name):
    p = RESULTS / name
    return json.loads(p.read_text()) if p.exists() else None


def clean(obj):
    """Recursively replace non-finite floats with None so JSON.parse is happy."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def daily_return_curve(params: Params) -> tuple[list[str], list[float]]:
    df = pd.read_parquet(DATA)
    res = run(df, BacktestConfig(start="2022-01-01", end="2026-06-30", params=params))
    base = 1000.0
    daily = res.equity.resample("1D").last().dropna()
    labels = [d.strftime("%Y-%m-%d") for d in daily.index]
    vals = [round(float(v / base - 1.0) * 100, 2) for v in daily.values]
    return labels, vals


# ---- ranked idea backlog (from the quant books + research findings) --------
IDEAS = [
    {"rank": 1, "title": "跨截面市場中性一籃子", "impact": "高", "effort": "高",
     "why": "每小時對 ~10 檔流動永續的多空比百分位排名，做空最擁擠多、做多最擁擠空，美元中性。分散單幣爆倉（XRP/TRX）、移除方向 beta，就是報告暗示的『互補策略』。",
     "tag": "分散/中性"},
    {"rank": 2, "title": "幣種白名單過濾", "impact": "高", "effort": "低",
     "why": "只做有持續散戶邊際的幣（BTC/ADA，或小量 DOGE），避開機構主導（BNB）與特異暴走（XRP/TRX/DOT/PEPE，最慘 MAE −78~−117%）。回測直接證明多數 alt 開槓桿必爆。",
     "tag": "風控/白名單"},
    {"rank": 3, "title": "縮短 lookback 至 45 天", "impact": "中", "effort": "低",
     "why": "樣本內 Sharpe 1.31→1.55、回撤 −24.5%→−18.2%、最慘 MAE −16.4%→−13.0% 同時改善。但這是樣本內最佳化，上線前必須 walk-forward 驗證（90d 是報告的保守選擇）。",
     "tag": "參數"},
    {"rank": 4, "title": "災難性硬止損（非緊止損）", "impact": "中", "effort": "中",
     "why": "緊 3–5% 止損會把 +74% 變 −20%（已證），但無停損＝單筆無上限（XRP −116%）。折衷：約 −20% 遠端災難止損或同根 K −15% 崩盤保護，只砍尾端不傷 alpha。需掃描找甜蜜點。",
     "tag": "風控"},
    {"rank": 5, "title": "改用 2h / 4h 週期", "impact": "中", "effort": "低",
     "why": "同樣 ~1.3 Sharpe，但 4h 最大回撤僅 −15.8%、勝率 56%——同樣的邊際、更平滑的體驗。可做低回撤變體或 1h+4h 組合。",
     "tag": "參數"},
    {"rank": 6, "title": "多因子共振（資料已在手）", "impact": "高", "effort": "中",
     "why": "metrics 檔已含未用欄位：大戶多空比、吃單多空比、未平倉量。只在『散戶擁擠 AND 大戶背離』時進場。可再疊資金費率傾斜與鏈上 SOPR/NUPL。各因子 Z-score 標準化後加權。",
     "tag": "訊號增強"},
    {"rank": 7, "title": "正規化出場（取代固定 3 天）", "impact": "中", "effort": "中",
     "why": "當多空比百分位回到中性（40–60%）就平倉，而非死等 3 天。反轉早完成就早釋放資金。對稱鏡像出場最貼近均值回歸精神。",
     "tag": "出場設計"},
    {"rank": 8, "title": "ADX 趨勢環境開關", "impact": "中", "effort": "中",
     "why": "均值回歸只在震盪市有效，強趨勢會被連續套牢（『地板下還有地下室』）。ADX<25 才啟用反轉，趨勢明確時停手。",
     "tag": "環境過濾"},
    {"rank": 9, "title": "ML meta-labeling 濾網", "impact": "高", "effort": "高",
     "why": "保留規則進場，另訓練分類器預測『這次反轉會不會成功』，只在規則觸發 AND 模型看好 AND do_predict==1（市況離群自動否決）時進場。特徵：LSR z-score、波動、量能、大盤相關。",
     "tag": "ML 增強"},
    {"rank": 10, "title": "穩健性驗證套件", "impact": "基礎", "effort": "中",
     "why": "任何改參數前，先跑 walk-forward（180訓練/30測試滾動）+ 蒙地卡羅打亂交易順序 1000 次看尾端回撤 + IC/IR 因子檢驗（IC>0.03 有效、>0.1 疑過擬合）。這是分辨『真有效』與『事後硬湊』的關卡。",
     "tag": "驗證"},
]


def main():
    multi = load("multi_coin.json") or {}
    sweep = load("param_sweep.json") or {}
    lev = load("leverage_safety.json") or {}
    wf = load("walk_forward.json") or {}

    # BTC baseline vs tuned (45d) equity overlay
    labels, base_curve = daily_return_curve(Params())
    _, tuned_curve = daily_return_curve(Params(lookback_hours=45 * 24))

    payload = clean({
        "generated": labels[-1] if labels else None,
        "multi_coin": multi,
        "param_sweep": sweep,
        "leverage": lev,
        "walk_forward": wf,
        "btc_overlay": {
            "labels": labels,
            "baseline": base_curve,   # 90d lookback (live config)
            "tuned": tuned_curve,     # 45d lookback (best in-sample)
        },
        "ideas": IDEAS,
        "whitelist": WHITELIST,
        "exit": exit_summary(),
        "research_log": research_log(),
        "factor_ic": factor_ic_table(),
    })

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("window.RESEARCH = " + json.dumps(payload, default=str) + ";\n")
    kb = OUT.stat().st_size / 1024
    print(f"wrote {OUT}  ({len(labels)} equity points, "
          f"{len(multi.get('baseline', []))} coins, {len(IDEAS)} ideas, {kb:.0f} KB)")


if __name__ == "__main__":
    main()
