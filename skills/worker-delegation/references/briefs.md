# Brief templates

A good brief lets a cheap executor do the work right the first time and keeps its reply short. Both save
main-loop tokens.

## General frame (all executors)

```text
GOAL: <one sentence describing the end result>
CONTEXT: <only the facts needed: stack, decisions already made, why>
SCOPE: may change <exact files/dirs>. DO NOT touch <files/dirs/areas>. Do not commit, push or deploy.
CONSTRAINTS: <versions, code style, and any coding-standards skill the project has, which you must follow>
DONE WHEN: <measurable acceptance criteria>
VERIFY: run <test/build/lint command> and report the result as it is.
STOP IF: <stop conditions, e.g. you need to change a file outside the scope, tests fail twice
  for an unclear reason, or a design decision is needed> -> report it, do not guess.
OUTPUT (at most ~15 lines):
  1. Status: done / partial / failed
  2. Files changed (paths only)
  3. Commands run + result (pass/fail, short numbers)
  4. Anything you are unsure about or assumptions you made
  Do not paste file contents, the full diff, or long logs.
```

## Variants per executor

**Mid-size Claude subagent (STANDARD)**: use the general frame as is. Add an example of an existing
pattern in the repo ("follow the pattern in `src/service/foo.go`") so it does not invent a new style.

**Small Claude model / Explore (MECHANICAL, read-only)**: replace SCOPE with "read-only, change
nothing". Ask for output as a list of `path:line - one-line finding`, at most N items.

**Largest-model subagent (research or planning while the main loop runs a smaller model)**: write out all
constraints and context, because the subagent cannot see the conversation. Ask for a recommendation +
reasons + risks, not an essay.

**Codex**: already strong at coding. Still give a firm SCOPE and STOP IF. Codex's interactive questions
cannot be answered through the tools, so tell it to choose a safe default and report it in point 4. For a
cross review: "Review the diff `git diff <base>..HEAD` for correctness bugs. Do not change files.
Output: at most 10 findings, each as `file:line - problem - failing scenario`."

**Qwen Code (per token when it uses an API key)**: always set `cwd` to a narrow folder or worktree. Add:
"Limit: at most ~30 tool steps. Do not read files over 2,000 lines whole; use grep or line ranges."
Leave `model` empty unless the user has approved a more expensive model.

**OpenCode (free models)**: only for non-sensitive material. Do not include secrets, personal data or
client code in the brief. Small, clear tasks. If it fails or hits a rate limit, try one other free model
once before changing executor.

## Continuing after a quota stop

When you move to another executor mid-task, check `git status` / `git diff --stat` first, then add this
to the brief:

```text
PREVIOUS PROGRESS (another executor stopped because of quota):
- Done: <summary, based on the diff you checked yourself>
- Not done: <remaining steps>
Do not redo what is already finished. Do not revert existing changes without a strong reason.
```
