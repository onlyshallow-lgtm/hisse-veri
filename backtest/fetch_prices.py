#!/usr/bin/env python3
"""Backtest icin fiyat gecmisi: 2006'dan beri S&P 500'e girmis/cikmis tum hisseler + SPY, RSP.
Duzeltilmis kapanis (temettu/bolunme) ve hacim, backtest/data/ altina gzip CSV olarak yazilir."""
import time, os
import pandas as pd
import yfinance as yf

m = pd.read_csv("backtest/sp500_members.csv")
m = m[pd.to_datetime(m["date"]) >= "2006-01-01"]
raw = set()
for t in m["tickers"]:
    raw |= set(t.split(","))

def yahoo(t):
    base = t.split("-")[0] if "-" in t and t.rsplit("-", 1)[1].isdigit() else t   # "ABC-201503" -> "ABC"
    return base.replace(".", "-")

mapping = {t: yahoo(t) for t in raw}
syms = sorted(set(mapping.values()) | {"SPY", "RSP"})
print(len(syms), "sembol")

closes, vols, missing = [], [], []
for i in range(0, len(syms), 50):
    chunk = syms[i:i + 50]
    for k in range(4):
        try:
            d = yf.download(chunk, start="2006-01-01", auto_adjust=True, progress=False, threads=True, group_by="column")
            break
        except Exception as e:
            print("tekrar", i, e); time.sleep(30 * (k + 1)); d = None
    if d is None or d.empty:
        missing += chunk; continue
    c, v = d["Close"], d["Volume"]
    c = c.dropna(axis=1, how="all"); v = v[c.columns]
    missing += [s for s in chunk if s not in c.columns]
    closes.append(c.astype("float32")); vols.append(v.astype("float32"))
    print(i, len(c.columns), flush=True); time.sleep(2)

os.makedirs("backtest/data", exist_ok=True)
C = pd.concat(closes, axis=1); V = pd.concat(vols, axis=1)
C.round(4).to_csv("backtest/data/close.csv.gz")
(V / 1000).round(0).to_csv("backtest/data/volume_k.csv.gz")
pd.Series(mapping).rename_axis("member").rename("yahoo").to_csv("backtest/data/ticker_map.csv")
open("backtest/data/missing.txt", "w").write("\n".join(sorted(missing)))
print("bitti:", C.shape, "eksik:", len(missing))
