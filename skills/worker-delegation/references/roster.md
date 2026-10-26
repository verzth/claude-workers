# Executor roster

Generic reference. Models, prices and billing terms change, and every user's setup differs. When
something here disagrees with the output of `scripts/quota.py` or with the CLI's own config, trust the
config. Model choice is the user's decision: read the CLI's current defaults (commands at the bottom)
instead of assuming a model name.

## Summary

| Executor | How to call | Billing modes | Quota / cost signal lives in | Strong at | Cautions |
|---|---|---|---|---|---|
| Claude main loop | (you) | Claude plan (OAuth sign-in), or per token with an API key / gateway / extra usage | `get_usage` tool on Claude Code Desktop, `/usage` in the CLI | Decisions, design, review, verification | Most expensive per token. Keep it for thinking |
| Claude subagent, largest model (e.g. Opus) | `Agent` with `model: opus` (or the tier name your session offers) | Same Claude plan quota as the main loop | `get_usage` / `/usage` | Deep research, planning, heavy review | Expensive. Keep only a few in parallel |
| Claude subagent, mid model (e.g. Sonnet) | `Agent` with `model: sonnet` | Same Claude plan quota | `get_usage` / `/usage` | Coding to spec, refactors, tests | Needs a complete brief. Can run several in parallel on disjoint scopes |
| Claude subagent, small model / Explore (e.g. Haiku) | `Agent` with `model: haiku`, or `subagent_type: Explore` | Same Claude plan quota, cheapest per token | `get_usage` / `/usage` | Broad search, summaries, inventories | Not for decisions |
| **Codex** | `worker_start` with `kind: codex` | ChatGPT sign-in: ChatGPT plan limits, separate from Claude. API key: per token | `scripts/quota.py` (rate limits from the latest session log) | Large coding tasks, refactors, tests, cross review from a different model family | The short window can run out fast. Via the workers plugin it runs in a `workspace-write` sandbox with `on-request` approvals |
| **Qwen Code** | `worker_start` with `kind: qwen` | Per token when it uses an API key (check `security.auth.selectedType`). Other sign-in types may be free or plan-based | `scripts/quota.py` (estimate from Qwen Code's local token usage log, if your version writes one) | Cheap mechanical/standard work, long context | Native permission policy may auto-approve actions: use a narrow `cwd` or a worktree. Top-tier models are expensive |
| **OpenCode** | `worker_start` with `kind: opencode` | Free models: $0. Any paid provider or API key: per token | No meter. A failure looks like a 429 or a free-tier error | Mechanical, non-sensitive work | Lower quality. Provider may use your data for training: never send secrets, personal data or client code. Free tiers can be unavailable or rate limited |

Same plan quota means: if Claude is red, every Claude subagent is red too. Some accounts also show a
separate weekly window for the top model. Read `get_usage` or `/usage` for the exact windows on your plan.

## Prices

Per-token prices differ by provider and change often. The only price table this skill uses is
`QWEN_PRICES` at the top of `scripts/quota.py` (example list prices, USD per 1M tokens). Check your
provider's current pricing and edit that table, so the estimates stay correct and no second copy can drift.
Reasoning ("thinking") tokens count as output tokens. Cache hits are usually cheaper; the script's
`QWEN_CACHE_RATIO` table says by how much for each model.

## Models that need the user's approval

Do not pick these on your own. Ask, naming the model, the estimated tokens and the estimated cost:
- Any top-tier model on a per-token route (Qwen Code with an API key, a paid OpenCode provider).
- Any OpenCode provider that is not a free model: direct API providers, subscriptions, self-hosted
  routers, and the `anthropic` provider (that is Claude billed per token).
- Any model the user did not configure as that CLI's default.

For OpenCode, list what exists with `opencode models`. Free models are usually named with a `-free`
suffix; if the default fails or is rate limited, try one other free model once before changing executor.

## How to read quota signals

- **Claude**: `get_usage` returns `plan.windows[]` (for example a 5-hour window, a weekly all-models
  window, sometimes a weekly top-model window), each with `percentUsed` and `resetsIn`.
  `plan.status = "not_applicable"` means this session uses an API key or gateway, which is **billed per
  token**. `extraUsage.enabled = true` means going over the plan is billed. In the CLI use `/usage`.
- **Codex**: `rate_limits.primary` (a 300-minute window) and `rate_limits.secondary` (a 10080-minute
  window) in the newest session log under `~/.codex/sessions/YYYY/MM/DD/*.jsonl`. The data can be stale:
  the script treats a window as 0% once its `resets_at` has passed, and prints how old the data is.
- **Qwen Code**: no hard quota on the local side. What limits you is the provider account balance. A
  balance error usually reads like an overdue-payment message or an HTTP 402/403. Use the script's
  estimate as the brake.
- **OpenCode free models**: no meter. Rate limits show up as a 429 or a free-tier error. Switch to
  another free model, or stop.

## Quick re-verify (if the roster looks stale)

```bash
codex --version; qwen --version; opencode --version
grep -E '^(model|model_reasoning_effort) ' ~/.codex/config.toml
python3 -I -c "import json,os;d=json.load(open(os.path.expanduser('~/.qwen/settings.json')));print(d.get('model'),d.get('security',{}).get('auth',{}).get('selectedType'))"
python3 -I -c "import json,os;print(json.load(open(os.path.expanduser('~/.config/opencode/opencode.json'))).get('model'))"
codex login status
```

These print the user's current defaults and how each CLI is signed in (no secrets). Config files can live
elsewhere if the user relocated them; if a file is missing, ask rather than guess.
