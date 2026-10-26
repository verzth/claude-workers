#!/usr/bin/env python3
"""Quota and cost summary for every executor (token-saving, read-only).

Usage:
  python3 -I <skill-folder>/scripts/quota.py          # 4 lines of text
  python3 -I <skill-folder>/scripts/quota.py --dash   # dash/v1 JSON (compatible with the bi-dash plugin)

Reads local files only. Never calls a model, never prints secrets (it reports the NAMES of environment
variables that signal per-token billing, never their values). Standard library only.
Claude's own quota cannot be read from here: use the get_usage tool (Claude Code Desktop) or /usage (CLI).

Environment:
  QWEN_DAILY_BUDGET   daily spend guard rail in USD for the Qwen estimate (default 2)
"""
import glob
import json
import os
import sys
import time
from datetime import datetime

HOME = os.path.expanduser("~")
NOW = time.time()

# ---------------------------------------------------------------------------
# EDIT THIS TABLE. It is the single source of prices for this skill.
# Example list prices, USD per 1M tokens: check your provider's current pricing and adjust.
# Each entry is a list of (max_input_tokens, input_price, output_price) tiers; the first tier whose
# limit is >= the request's input tokens is used. Model names must match the "model" field of Qwen
# Code's local usage log. Models missing from the table are listed as "no price" in the report.
# ---------------------------------------------------------------------------
QWEN_PRICES = {
    "qwen3.7-flash": [(32_000, 0.03, 0.13), (256_000, 0.10, 0.40), (10**12, 0.20, 0.80)],
    "qwen3.7-plus": [(256_000, 0.40, 1.60), (10**12, 1.20, 4.80)],
    "qwen3.7-max": [(10**12, 2.50, 7.50)],
    "qwen3.8-flash": [(10**12, 0.15, 0.47)],
    "qwen3.8-max": [(10**12, 2.00, 6.00)],
}
# Cached input is billed at this fraction of the input price. Models NOT listed here are billed at full
# input price for cached tokens, so their estimate is an upper bound.
QWEN_CACHE_RATIO = {"qwen3.7-flash": 0.2, "qwen3.7-plus": 0.2, "qwen3.7-max": 0.2}


def _budget():
    try:
        value = float(os.environ.get("QWEN_DAILY_BUDGET", "2"))
        return value if value > 0 else 2.0
    except ValueError:
        return 2.0


QWEN_DAILY_BUDGET = _budget()  # USD per day; matches the default guard rail in SKILL.md


def fmt_reset(ts):
    if not ts:
        return "?"
    left = ts - NOW
    if left <= 0:
        return "already reset"
    h, m = int(left // 3600), int(left % 3600 // 60)
    when = datetime.fromtimestamp(ts).strftime("%a %H:%M")
    return f"{when} (+{h}h{m:02d}m)" if h < 48 else f"{when} (+{h // 24} days)"


def level(pct, warn, bad):
    if pct is None:
        return "info"
    return "bad" if pct >= bad else "warn" if pct >= warn else "ok"


# ---------- data collectors (return dicts, no formatting) ----------

# Environment variables whose presence means Claude in this process is billed per token.
# Only the names are ever reported, never the values.
BILLING_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")


def read_claude():
    names = [k for k in BILLING_VARS if os.environ.get(k)]
    base = os.environ.get("ANTHROPIC_BASE_URL", "")
    return {"billing_vars": names, "gateway": bool(base) and "api.anthropic.com" not in base}


def read_codex():
    files = sorted(glob.glob(f"{HOME}/.codex/sessions/*/*/*/*.jsonl"), key=os.path.getmtime)
    for f in reversed(files[-5:]):
        with open(f, errors="ignore") as fh:
            text = fh.read()
        idx = text.rfind('"rate_limits":')
        if idx == -1:
            continue
        try:
            rl, _ = json.JSONDecoder().raw_decode(text[idx + len('"rate_limits":'):].lstrip())
        except ValueError:
            continue
        windows = {}
        for key in ("primary", "secondary"):
            w = rl.get(key) or {}
            used, reset = w.get("used_percent"), w.get("resets_at")
            if reset and reset <= NOW:
                used = 0  # the window has passed, so the old number is stale
            windows[key] = {"used": used, "resets_at": reset}
        return {
            "windows": windows,
            "credits": (rl.get("credits") or {}).get("balance", "?"),
            "age_min": int((NOW - os.path.getmtime(f)) // 60),
        }
    return None


def qwen_cost(model, inp, cached, out):
    tiers = QWEN_PRICES.get(model)
    if not tiers:
        return None
    for limit, pin, pout in tiers:
        if inp <= limit:
            break
    ratio = QWEN_CACHE_RATIO.get(model, 1.0)
    return ((inp - cached) * pin + cached * pin * ratio + out * pout) / 1e6


def read_qwen():
    month = datetime.now().strftime("%Y-%m")
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(f"{HOME}/.qwen/settings.json") as fh:
            settings = json.load(fh)
        default_model = (settings.get("model") or {}).get("name", "?")
        auth = ((settings.get("security") or {}).get("auth") or {}).get("selectedType", "?")
    except Exception:
        default_model, auth = "?", "?"
    # An OAuth sign-in is not billed per token by this script's estimate; any other auth type
    # (API key / OpenAI-compatible / other providers) is. "?" means we could not tell.
    metered = auth not in ("qwen-oauth", "?")
    data = {"model": default_model, "metered": metered, "auth": auth, "has_log": False,
            "per_model": {}, "per_day": {}, "month": 0.0, "today": 0.0, "unknown": []}
    path = f"{HOME}/.qwen/usage/token-usage-{month}.jsonl"
    if not os.path.exists(path):
        return data
    data["has_log"] = True
    unknown = set()
    with open(path, errors="ignore") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            c = qwen_cost(r.get("model"), r.get("inputTokens", 0), r.get("cachedTokens", 0), r.get("outputTokens", 0))
            if c is None:
                unknown.add(r.get("model"))
                continue
            data["per_model"][r["model"]] = data["per_model"].get(r["model"], 0) + c
            day = r.get("localDate", "?")
            data["per_day"][day] = data["per_day"].get(day, 0) + c
            data["month"] += c
            if day == today:
                data["today"] += c
    data["unknown"] = sorted(u for u in unknown if u)
    return data


def read_opencode():
    try:
        with open(f"{HOME}/.config/opencode/opencode.json") as fh:
            cfg = json.load(fh)
    except Exception:
        return None
    model = cfg.get("model", "?")
    return {"model": model, "free": str(model).endswith("-free")}


def upper_bound(q):
    """True when some model in the estimate has no cache discount listed, so cached tokens are priced in full."""
    return any(m not in QWEN_CACHE_RATIO for m in q["per_model"])


# ---------- text output (used by the skill) ----------

def text_report():
    c = read_claude()
    warn = []
    if c["billing_vars"]:
        warn.append(f"{'/'.join(c['billing_vars'])} set -> Claude in this process is BILLED PER TOKEN")
    if c["gateway"]:
        warn.append("ANTHROPIC_BASE_URL points to a non-Anthropic host (gateway; check billing)")
    status = "; ".join(warn) if warn else "subscription auth (no per-token variables in env)"
    print(f"Claude  : {status} | quota percentages -> get_usage tool (Desktop) / /usage (CLI)")

    x = read_codex()
    if not x:
        print("Codex   : no rate_limits found in the latest sessions")
    else:
        parts = [f"{label} {x['windows'][k]['used'] if x['windows'][k]['used'] is not None else '?'}% "
                 f"(resets {fmt_reset(x['windows'][k]['resets_at'])})"
                 for k, label in (("primary", "5h"), ("secondary", "weekly"))]
        print(f"Codex   : {' | '.join(parts)} | credits {x['credits']} | data {x['age_min']} min old  "
              "[plan quota, not money; an API key bills per token]")

    q = read_qwen()
    if q["metered"]:
        billing = f"billed per token (auth={q['auth']})"
    elif q["auth"] == "?":
        billing = "billing unknown (settings not readable)"
    else:
        billing = f"auth={q['auth']}"
    top = ", ".join(f"{m} ${v:.2f}" for m, v in sorted(q["per_model"].items(), key=lambda kv: -kv[1])[:3])
    note = f" | no price for: {', '.join(q['unknown'])} (edit QWEN_PRICES)" if q["unknown"] else ""
    if upper_bound(q):
        note += " | some models have no cache discount listed, so this is an UPPER BOUND"
    if q["has_log"]:
        spend = (f"estimated today ${q['today']:.2f} of ${QWEN_DAILY_BUDGET:.2f}/day, "
                 f"this month ${q['month']:.2f} ({top or '-'})")
    else:
        spend = "no usage log found for this month"
    print(f"Qwen    : default {q['model']} | {billing} | {spend}{note}")

    o = read_opencode()
    if not o:
        print("OpenCode: config not readable")
    else:
        tag = "$0 (free model: provider may use your data, rate limits apply)" if o["free"] else "CHECK BILLING for this provider"
        print(f"OpenCode: default {o['model']} | {tag}")


# ---------- dash/v1 output (read by the bi-dash plugin) ----------

def dash_report():
    w = []
    c = read_claude()
    if c["billing_vars"] or c["gateway"]:
        w.append({"type": "note", "status": "bad",
                  "text": "Claude in this process is billed per token (API key or gateway in env)."})

    x = read_codex()
    if x:
        for key, label in (("primary", "Codex 5h"), ("secondary", "Codex weekly")):
            win = x["windows"][key]
            w.append({"type": "gauge", "group": "Codex", "title": label, "percent": win["used"],
                      "status": level(win["used"], 70, 90), "sub": f"resets {fmt_reset(win['resets_at'])}"})
        w.append({"type": "kpi", "group": "Codex", "title": "Codex credits", "value": str(x["credits"]),
                  "status": "info", "sub": f"plan quota · data {x['age_min']} min old"})
    else:
        w.append({"type": "note", "group": "Codex", "status": "info", "text": "Codex: no rate limit data yet."})

    q = read_qwen()
    upper = upper_bound(q)
    w.append({"type": "kpi", "group": "Qwen", "title": "Qwen today", "value": f"${q['today']:.2f}",
              "status": level(q["today"] / QWEN_DAILY_BUDGET * 100, 50, 100),
              "sub": f"budget ${QWEN_DAILY_BUDGET:g}/day"})
    w.append({"type": "kpi", "group": "Qwen", "title": "Qwen this month", "value": f"${q['month']:.2f}",
              "status": "warn" if q["month"] > QWEN_DAILY_BUDGET * 10 else "ok",
              "sub": "estimated upper bound" if upper else "estimated"})
    w.append({"type": "kpi", "group": "Qwen", "title": "Default model", "value": q["model"],
              "status": "warn" if q["metered"] else "info",
              "sub": "billed per token" if q["metered"] else q["auth"]})
    if q["per_day"]:
        now = datetime.now()
        days = [f"{now:%Y-%m}-{d:02d}" for d in range(1, now.day + 1)]  # every date, empty = 0
        w.append({"type": "line", "group": "Qwen", "title": "Qwen cost per day (this month)", "unit": "$",
                  "points": [{"label": d[8:], "value": round(q["per_day"].get(d, 0), 4)} for d in days]})
    if q["per_model"]:
        w.append({"type": "bar", "group": "Qwen", "title": "Qwen cost per model (this month)", "unit": "$",
                  "items": [{"label": m, "value": round(v, 4)}
                            for m, v in sorted(q["per_model"].items(), key=lambda kv: -kv[1])]})
    if q["unknown"]:
        w.append({"type": "note", "group": "Qwen", "status": "warn",
                  "text": "No price for: " + ", ".join(q["unknown"]) + ". Add them to QWEN_PRICES in quota.py."})
    if upper:
        w.append({"type": "note", "group": "Qwen", "status": "warn",
                  "text": "Some models have no cache discount listed, so cached tokens are priced in full (upper bound)."})

    o = read_opencode()
    if o:
        w.append({"type": "kpi", "group": "OpenCode", "title": "OpenCode default", "value": str(o["model"]).split("/")[-1],
                  "status": "ok" if o["free"] else "warn",
                  "sub": "$0 · free model" if o["free"] else "check provider billing"})

    print(json.dumps({"schema": "dash/v1", "generatedAt": int(NOW * 1000), "widgets": w}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        dash_report() if "--dash" in sys.argv else text_report()
    except Exception as e:  # never crash without a message
        if "--dash" in sys.argv:
            print(json.dumps({"schema": "dash/v1", "widgets": [
                {"type": "note", "status": "bad", "text": f"quota.py failed: {e.__class__.__name__}: {e}"}]}))
        else:
            print(f"quota.py failed: {e.__class__.__name__}: {e}")
