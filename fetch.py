#!/usr/bin/env python3
"""Nightly data pull for the "ABD Hisse Keşif" site.
Reads the US ticker list, then asks Finnhub (free plan, ~55 calls/min) for each liquid mid/large cap:
same-day quote, basic financial metrics and the last earnings surprises; plus the earnings calendar.
Writes JSON under data/. Needs the FINNHUB_KEY environment variable (a GitHub Actions secret)."""
import json, os, re, sys, time, urllib.request, urllib.error, urllib.parse
from datetime import date, datetime, timedelta, timezone

KEY = os.environ.get("FINNHUB_KEY", "").strip()
BASE = "https://finnhub.io/api/v1"
LIST = "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/{ex}/{ex}_full_tickers.json"
MIN_CAP, MIN_PRICE, PAUSE = 2e9, 5.0, 1.1
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "300"))
KEEP = re.compile(r"^(pe|ps|pb|pfcf|forwardPE|peg|ev)|Growth|Margin|^ro[aei]|roic|totalDebt/totalEquity|currentRatio|52Week|^beta$|marketCapitalization|dividendYield", re.I)
JUNK = re.compile(r"\b(warrants?|units?|rights?|preferred|notes? due|debentures?)\b", re.I)
t0 = time.time(); calls = 0; errors = {}; off = set()

def num(s):
    try: return float(str(s).replace("$", "").replace(",", ""))
    except ValueError: return 0.0

def http(url, tries=5):
    global calls
    for k in range(tries):
        try:
            calls += 1
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "hisse-veri"}), timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(20 * (k + 1)); continue
            return e.code, None
        except Exception:
            time.sleep(5 * (k + 1))
    return 0, None

def fh(path, **q):
    """One Finnhub call. An endpoint that answers 401/403 is switched off for the rest of the run."""
    if path in off: return None
    q["token"] = KEY
    code, body = http(f"{BASE}{path}?{urllib.parse.urlencode(q)}")
    time.sleep(PAUSE)
    if code in (401, 403): off.add(path)
    if code != 200: errors[f"{path} {code}"] = errors.get(f"{path} {code}", 0) + 1; return None
    return body

def universe():
    out = {}
    for ex in ("nasdaq", "nyse", "amex"):
        code, rows = http(LIST.format(ex=ex))
        for r in rows or []:
            s = r["symbol"].strip()
            if "^" in s or "/" in s or JUNK.search(r.get("name", "")): continue
            mc, p = num(r.get("marketCap")), num(r.get("lastsale"))
            if mc >= MIN_CAP and p >= MIN_PRICE: out[s] = mc
    return [s for s, _ in sorted(out.items(), key=lambda kv: -kv[1])]

def main():
    if not KEY: sys.exit("FINNHUB_KEY is not set")
    os.makedirs("data/history", exist_ok=True)
    syms = universe()
    extra = [s.strip().upper() for s in open("watchlist.txt").read().split()] if os.path.exists("watchlist.txt") else []
    syms = list(dict.fromkeys(extra + syms))
    today = datetime.now(timezone.utc).date().isoformat()
    quotes, fund, sample_keys, stopped = {}, {}, None, None
    for i, s in enumerate(syms):
        if (time.time() - t0) / 60 > MAX_MINUTES: stopped = i; break
        q = fh("/quote", symbol=s)
        if q and q.get("c"): quotes[s] = [q["c"], q.get("pc"), q.get("dp"), q.get("h"), q.get("l"), q.get("o"), q.get("t")]
        m = fh("/stock/metric", symbol=s, metric="all")
        e = fh("/stock/earnings", symbol=s)
        rec = {}
        if m and m.get("metric"):
            if sample_keys is None: sample_keys = sorted(m["metric"].keys())
            rec["m"] = {k: v for k, v in m["metric"].items() if v is not None and KEEP.search(k)}
        if isinstance(e, list) and e:
            rec["e"] = [{k: x.get(k) for k in ("period", "actual", "estimate", "surprisePercent")} for x in e[:4]]
        if rec: fund[s] = rec
        if len(off) >= 3: break            # key rejected everywhere: stop early
    cal = fh("/calendar/earnings", **{"from": today, "to": (date.fromisoformat(today) + timedelta(days=30)).isoformat()})
    cal = [{k: x.get(k) for k in ("symbol", "date", "hour", "epsEstimate", "revenueEstimate")} for x in (cal or {}).get("earningsCalendar", []) if x.get("symbol") in set(syms)]
    dump = lambda name, obj: json.dump(obj, open(name, "w"), separators=(",", ":"))
    if quotes:
        dump("data/quotes.json", {"date": today, "items": quotes}); dump(f"data/history/quotes-{today}.json", {"date": today, "items": quotes})
    if fund:
        dump("data/fundamentals.json", {"date": today, "items": fund})
        if date.fromisoformat(today).weekday() == 0: dump(f"data/history/fundamentals-{today}.json", {"date": today, "items": fund})
    if cal: dump("data/calendar.json", {"date": today, "items": cal})
    status = {"date": today, "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"), "minutes": round((time.time() - t0) / 60, 1),
              "symbols": len(syms), "quotes": len(quotes), "fundamentals": len(fund), "with_metrics": sum(1 for v in fund.values() if "m" in v),
              "with_earnings": sum(1 for v in fund.values() if "e" in v), "calendar": len(cal), "calls": calls, "errors": errors,
              "endpoints_off": sorted(off), "stopped_at": stopped, "metric_keys_sample": sample_keys}
    json.dump(status, open("data/status.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in status.items() if k != "metric_keys_sample"}))
    if not quotes and not fund: sys.exit("no data fetched - check the FINNHUB_KEY secret")

if __name__ == "__main__":
    main()
