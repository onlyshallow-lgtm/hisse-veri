#!/usr/bin/env python3
"""Ucretsiz-veri backtest'i: modelin fiyat tabanli kismi (momentum + 52h zirve yakinligi + trend filtresi).
Evren: o tarihte S&P 500 uyesi olan hisseler (gecmis uyelik listesi). Aylik yeniden dengeleme, esit agirlik,
sektor basina en fazla 3 hisse, sat tamponu, islem basina %0,2 maliyet."""
import sys, json
import numpy as np, pandas as pd

D = "backtest/data/"
C = pd.read_csv(D + "close.csv.gz", index_col=0, parse_dates=True).sort_index()
V = pd.read_csv(D + "volume_k.csv.gz", index_col=0, parse_dates=True).reindex(C.index) * 1000
tmap = pd.read_csv(D + "ticker_map.csv", index_col=0)["yahoo"].to_dict()
sec = pd.read_csv("backtest/sectors.csv").set_index("Symbol")["GICS Sector"]
sec.index = sec.index.str.replace(".", "-", regex=False)
mem = pd.read_csv("backtest/sp500_members.csv"); mem["date"] = pd.to_datetime(mem["date"])
mem = mem.sort_values("date").reset_index(drop=True)

R = C.pct_change(fill_method=None)            # NaN after the last price -> treated as cash below
DV20 = (C * V).rolling(20, min_periods=15).mean()
MA200 = C.rolling(200, min_periods=180).mean()
HI252 = C.rolling(252, min_periods=240).max()

def members_at(t):
    row = mem[mem["date"] <= t].iloc[-1]
    return {tmap.get(x, x) for x in row["tickers"].split(",")}

month_ends = C.index.to_series().groupby(C.index.to_period("M")).last()
month_ends = month_ends[(month_ends >= "2007-12-01")].tolist()
_mem_cache = {t: members_at(t) for t in month_ends}

def scores(t, lb):
    i = C.index.get_loc(t)
    p = C.iloc[i]; p_skip = C.iloc[i - 21]; p_lb = C.iloc[i - 21 * lb]
    cols = [c for c in _mem_cache[t] if c in C.columns]
    df = pd.DataFrame({"p": p[cols], "mom": (p_skip / p_lb - 1)[cols], "hi": (p / HI252.iloc[i])[cols],
                       "dv": DV20.iloc[i][cols], "ma": MA200.iloc[i][cols]}).dropna()
    df = df[(df.p > 5) & (df.dv > 5e6)]
    df["score"] = 0.5 * df.mom.rank(pct=True) + 0.5 * df.hi.rank(pct=True)
    df["rank"] = df.score.rank(ascending=False, method="first")
    df["trend"] = df.p > df.ma
    return df.sort_values("rank")

SC = {}

def run(lb=12, n=12, buffer=25, trend=True, sector_cap=3, cost=0.002, start="2008-01-01", end=None):
    held, w = [], pd.Series(dtype=float)
    val, out, turn = 1.0, [], []
    rebal = [t for t in month_ends if t >= pd.Timestamp(start) - pd.Timedelta(days=31)]
    if end: rebal = [t for t in rebal if t <= pd.Timestamp(end)]
    for k, t in enumerate(rebal):
        df = SC[(t, lb)] if (t, lb) in SC else SC.setdefault((t, lb), scores(t, lb))
        keep = [s for s in held if s in df.index and df.at[s, "rank"] <= buffer]
        new = keep[:]
        count = pd.Series([sec.get(s, "?" + s) for s in new]).value_counts().to_dict()
        for s, r in df.iterrows():
            if len(new) >= n: break
            if s in new or (trend and not r.trend): continue
            g = sec.get(s, "?" + s)                      # unknown sector -> its own bucket (no cap)
            if count.get(g, 0) >= sector_cap: continue
            new.append(s); count[g] = count.get(g, 0) + 1
        target = pd.Series(1.0 / len(new), index=new) if new else pd.Series(dtype=float)
        tv = target.reindex(w.index.union(target.index), fill_value=0) - w.reindex(w.index.union(target.index), fill_value=0)
        to = tv.abs().sum(); turn.append(to / 2)
        val *= 1 - cost * to
        held, w = new, target
        nxt = rebal[k + 1] if k + 1 < len(rebal) else C.index[-1]
        days = C.index[(C.index > t) & (C.index <= nxt)]
        cash = 1 - w.sum()
        for d in days:
            r = R.loc[d, w.index].fillna(0.0)
            pos = w * (1 + r); total = pos.sum() + cash
            val *= total; w = pos / total; cash = cash / total
            out.append((d, val))
    s = pd.Series(dict(out))
    s = s[s.index >= pd.Timestamp(start)]
    return s / s.iloc[0], np.mean(turn) * 12

def stats(s):
    r = s.pct_change().dropna(); yrs = len(r) / 252
    cagr = s.iloc[-1] ** (1 / yrs) - 1; vol = r.std() * np.sqrt(252)
    dd = (s / s.cummax() - 1).min()
    return {"CAGR %": round(cagr * 100, 1), "Vol %": round(vol * 100, 1), "Sharpe": round(r.mean() / r.std() * np.sqrt(252), 2),
            "MaxDD %": round(dd * 100, 1), "Calmar": round(cagr / -dd, 2)}

def bench(sym, start, end=None):
    s = C[sym].loc[start:end].dropna(); return s / s.iloc[0]

if __name__ == "__main__":
    periods = {"Gelistirme 2008-2018": ("2008-01-01", "2018-12-31"), "Test 2019-bugun": ("2019-01-01", None),
               "Tum donem": ("2008-01-01", None)}
    res = {}
    base, to = run()
    for name, (a, b) in periods.items():
        seg = base.loc[a:b]; seg = seg / seg.iloc[0]
        res[name] = {"Model": stats(seg), "SPY": stats(bench("SPY", a, b)), "RSP": stats(bench("RSP", a, b))}
    res["Yillik devir (turnover)"] = round(to, 2)
    yr = pd.DataFrame({"Model": base.resample("YE").last().pct_change(), "SPY": bench("SPY", "2008-01-01").resample("YE").last().pct_change()})
    yr.iloc[0] = [base.resample("YE").last().iloc[0] - 1, bench("SPY", "2008-01-01").resample("YE").last().iloc[0] - 1]
    res["Yillik %"] = (yr * 100).round(1).set_axis(yr.index.year).to_dict("index")
    grid = []
    for lb in (6, 9, 12):
        for buf in (12, 20, 25, 35):
            for tr in (True, False):
                for n in (12, 20):
                    s, t = run(lb=lb, n=n, buffer=max(buf, n), trend=tr)
                    a = s.loc[:"2018-12-31"]; b = s.loc["2019-01-01":]; b = b / b.iloc[0]
                    grid.append({"lb": lb, "buffer": max(buf, n), "trend": tr, "n": n, "IS_CAGR": stats(a)["CAGR %"],
                                 "IS_Sharpe": stats(a)["Sharpe"], "OOS_CAGR": stats(b)["CAGR %"], "OOS_Sharpe": stats(b)["Sharpe"],
                                 "OOS_MaxDD": stats(b)["MaxDD %"], "turnover": round(t, 2)})
                    print(grid[-1], flush=True)
    res["Saglamlik"] = grid
    pd.DataFrame({"Model": base, "SPY": bench("SPY", "2008-01-01"), "RSP": bench("RSP", "2008-01-01")}).to_csv("backtest/equity.csv")
    json.dump(res, open("backtest/results.json", "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k != "Saglamlik"}, indent=1, default=str))
