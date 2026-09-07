# SELF_REPORT — what I built, what broke, and what you must review

Author: the coding agent (Kou / 寇豆码), per Zongqi Han's explicit request:
*"明确标出：你写的代码里有哪些是我应该 review 的、有哪些是跑挂了又被你补好的——我想统计你的产出里有多少需要我改。"*

Read this before you read the report. It is a list of my own defects.

---

## 0. One-paragraph summary

The harness works: 20 tasks, 3 backends, hard timeouts, one JSON per run, offline
reproducible analysis. **But the first versions did not work**, and the only reason I
know is that I actually ran the oracle calibration instead of declaring success. Six
concrete defects were found and patched, and one defect is **still open and cannot be
fixed by me** (a third-party site outage). Roughly **35–40% of this codebase is code I
believe you should read yourself** before you rely on a number it produces — details in
§4.

Everything below is reproducible from `results/`.

> **Post-delivery repair (2026-09-07).** The original `browser-use` adapter launched
> its own browser while the worker graded a different, untouched Playwright page. That
> could only produce false failures for a real agent. The adapter now exposes its CDP
> endpoint and the worker reconnects to the agent's actual page before verification;
> a missing endpoint is reported as `unavailable`, never as a score. This integration
> remains unexecuted until `browser-use[core]` and a real model credential are supplied.

---

## 1. What broke, and what I patched

These are real failures I hit and fixed. I am not listing them to look diligent; I am
listing them because each one changed a number that I would otherwise have reported
wrong.

### 1.1 The CLI ran **zero tasks** and reported success

```
backend=oracle  tasks=0  runs_per_task=1  total_runs=0
oracle: 0/0 runs passed in 0.0s
```

**Cause.** The task registry is populated as an import side effect (`register_task()`
runs at module import in `aeval/tasks/*.py`). `run_eval.py` and `aeval/runner.py`
imported `aeval.schema` and `aeval.backends` but never `aeval.tasks`, so the registry
was empty. Nothing raised — the sweep just quietly did nothing and printed a happy
"0/0 runs passed".

**Patch.** `aeval/__init__.py` now imports `aeval.tasks`, so `import aeval` always has
a populated registry. Guarded by `tests/test_schema.py` (which itself failed until I
added `import aeval.tasks` to `tests/conftest.py` — the same bug, in the tests).

**Why this matters more than it looks.** This is exactly the failure mode the whole
project exists to prevent: *a pipeline that reports a number without having measured
anything.* If I had not run it, I would have told you "the harness runs".

### 1.2 Wrong expected value in `inet_table_sort_last_name` — I invented "Bachman"

The table's row is **"Bach"** (Frank Bach). I wrote `("Bachman", "Conway", "Doe",
"Smith")` from memory of the site. The oracle failed with:

```
last_names_ascending -> observed=['Bach', 'Conway', 'Doe', 'Smith']
                         expected=['Bachman', 'Conway', 'Doe', 'Smith']
```

**Patch.** Constant corrected. **Lesson, and the reason I'm flagging it:** every
literal in `aeval/tasks/*.py` was written from recollection of these two sites, not
from a captured DOM dump. This one was caught; assume others of the same species are
still hiding in tasks the oracle happens to pass for the wrong reason.

### 1.3 `frame_texts()` — one unreadable frame emptied the whole map

`helpers.frame_texts()` wrapped the entire loop in a single `safe()`. On
`/nested_frames`, `frame-top` is a `<frameset>` host document with **no `<body>`**, so
`inner_text("body")` raised, `safe()` caught it, and the function returned `{}` —
losing left/middle/right/bottom too.

**Patch.** Per-frame try/except; an unreadable frame records `""` instead of
destroying the result.

### 1.4 `inet_nested_frames_read` hung for the full 60 s and was killed

```
[FAIL] inet_nested_frames_read run 1/1  61402ms (timeout)
```

**Cause.** The oracle implemented `frame_read` with
`page.frame_locator('frame[name="frame-left"]')`. `frame_locator` only sees iframes in
the **host** document; `frame-left` is nested *inside* `frame-top`, so the locator
never resolved and each of the 4 steps burned ~10 s, then the hard timeout killed the
worker.

**Patch.** `_op_frame_read` now resolves frames through `page.frames` (which lists the
whole frame tree) via a new `_frame_name()` helper, matching what the `verify` does.

**Silver lining:** this is the hard timeout working exactly as designed — a hung run
became a recorded `error_type="timeout"` failure instead of a dead process. That path
is now empirically verified, not just asserted.

### 1.5 `inet_iframe_type_text` burned 24 s failing to click a contenteditable body

Plain `locator.click()` timed out; `locator.fill()` raised
*"Element is not an `<input>`, `<textarea>` or `[contenteditable]` element"*.

**Patch.** `_op_frame_fill` now uses `force=True` for the click and falls back to
`fill() → click + Ctrl+A + type`. The step degrades to "typed nothing" in ~4 s instead
of hanging for 24 s. (It still fails — see §2, which is a site problem, not a code
problem.)

### 1.6 Pinned dependency versions that do not exist for Python 3.13

I first wrote `playwright==1.47.2` and `matplotlib==3.9.2`. Neither is installable on
3.13 (`1.47.2` was never released; `3.9.2` has no cp313 wheel). `pip install -r
requirements.txt` failed outright.

**Patch.** Re-resolved to `playwright==1.49.1` and `matplotlib==3.10.1`, both verified
to install on CPython 3.13.12. `browser-use` is deliberately **unpinned** — see §3.4.

### 1.7 A test asserted the wrong arithmetic

`test_render_markdown_contains_the_variance_story` asserted `"10/15" or "50.0%"`. The
fixture actually produces 8 passes / 15 runs = 53.3%. **Patch.** Assertions corrected
to `8/15` and `53.3%`. (A test that "passes" by matching a number I guessed is worse
than no test.)

---

## 2. The one defect I could **not** fix (site-side, not code-side)

**`inet_iframe_type_text` cannot pass right now, for anyone, from anywhere.**

The page logs this in the browser console:

```
error: All created TinyMCE editors are configured to be read-only.
warning: TinyMCE is in read-only mode because you have no more editor loads
         available this month. Upgrade yo…
```

Confirmed by DOM inspection:

```html
<body id="tinymce" class="mce-content-body mce-content-readonly"
      contenteditable="false">
```

the-internet has exhausted its TinyMCE cloud quota. The editor is **not editable**, so
no agent — scripted, LLM-driven, or human — can type into it. The oracle therefore
fails this task, and **it is right to**.

**What I did:** kept the task (it is the correct canonical frame task and the outage is
monthly), and marked it in three places: the task's `notes` field, this file, and a
README caveat. Re-run `python run_eval.py run --backend oracle --runs 1` after the
quota resets; if it passes, delete the caveat.

**What you should do:** treat **any** claim about the `frame` category as
un-interpretable until this is resolved. See §3.2 for a second, independent reason the
frame category is currently weak.

---

## 3. Design decisions I made, and what they cost you

### 3.1 Oracle and Naive share the same reference plan

Both deterministic backends execute `TaskSpec.plan`; they differ only in *how*
(robust waits vs. impatient force-clicks). I chose this over giving the naive backend
its own plan because it makes the comparison clean — same knowledge, different
execution discipline — and because one plan per task halves the maintenance surface as
the sites drift.

**Cost:** `NaiveBackend` has a *perfect planner*. A real LLM agent fails at planning
too, so the naive backend's pass rate is **neither an upper nor a lower bound** on LLM
performance. I put a 20-line warning at the top of `aeval/backends/naive.py` saying
exactly this. **Do not let this number leak into a write-up as if it were an agent
result.**

**Cost 2:** a task's `plan` is an *answer key*. `TaskSpec.for_agent()` strips it, and
`BrowserUseBackend` never reads it — but if you write your own backend, you must honour
that convention yourself. Nothing enforces it.

### 3.2 `inet_nested_frames_read` is a **weak task** and I knew it

The browser attaches frames automatically. So a backend that does *nothing at all*
still passes, because `verify` reads `page.frames` and the frames are simply there. The
naive backend, which explicitly fails every one of its `frame_read` steps on this task,
**still passes**.

This is not a bug I can patch with the two allowed sites: there is no iframe on either
saucedemo or the-internet whose content an agent can *change* right now (the only
candidate is the read-only TinyMCE editor). I left the task for category coverage and
labelled it `WEAK TASK` in its `notes`.

**This is the single most important thing for you to fix when you curate the task set.**
A good frame task needs an editable iframe — that probably means adding a third
public automation-friendly site, which is a decision I did not want to make for you.

### 3.3 One subprocess per run (hard timeouts)

Each (task, repetition) runs in its own `python -m aeval.worker` process, killed by
`subprocess.run(timeout=...)`. This is the only way to get a *true* hard timeout — a
synchronous Playwright call can block forever and no in-process watchdog can safely
interrupt it.

**Cost:** ~1 s of interpreter + browser startup per run. A 20×5 sweep takes ~8–10
minutes. I judged correctness worth the latency; if you disagree, batching runs into a
long-lived worker is the obvious optimisation, at the cost of the timeout guarantee.

### 3.4 `browser-use` backend: written, never executed

**I have not run this backend.** No package installed, no API key, and the package has
moved its LLM classes between versions. `_load_llm_class()` probes four import paths
(`browser_use.ChatBrowserUse`, `browser_use.llm.ChatOpenAI`,
`browser_use.llm.openai.ChatOpenAI`, `langchain_openai.ChatOpenAI`) and
`_trajectory_from_history()` handles three different history shapes. **All of that is
defensive guessing.** It is the most likely module to need a fix on first contact. I
left `browser-use` **unpinned** in `requirements.txt` rather than fake a version I had
verified.

### 3.5 Grading snapshots the page the instant the backend returns

`verify` reads `page.url` immediately. An agent that fires a click with
`no_wait_after=True` and returns will often be graded against the *pre-navigation* URL
and fail. That is a defensible definition ("you said you were done"). In the current
naive-backend report this mechanism did **not** produce flakiness — every task came out
either 5/5 or 0/5 — but it will become a source of variance once an LLM agent is
attached, because the agent's return timing is non-deterministic. It is a **choice**,
and it interacts with the 4 s verify timeout. If you think the grader should be more
patient, change `VERIFY_PHASE_TIMEOUT_MS` in `aeval/worker.py` and say so in the
write-up, because it moves the numbers.

---

## 4. What you should review yourself (the honest estimate)

Line counts are from `wc -l` at the time of writing.

| file | lines | verdict |
| --- | ---: | --- |
| `aeval/tasks/saucedemo.py` | ~380 | **Review fully.** Every selector and every expected literal is an assertion about a third-party site, written from memory. |
| `aeval/tasks/theinternet.py` | ~420 | **Review fully.** Same, plus the two known-weak tasks in §2 and §3.2. |
| `aeval/backends/browser_use.py` | ~280 | **Review fully.** Never executed. See §3.4. |
| `aeval/backends/naive.py` | ~250 | **Review the time constants.** `OP_TIMEOUT_MS=2500`, jitter ranges, `FINAL_SETTLE_MIN/MAX_S` — all guessed, all of them move the variance. |
| `aeval/backends/oracle.py` | ~230 | **Skim.** Verified at 19/20, but `_op_frame_fill`'s fallbacks are heuristics. |
| `aeval/analyzer.py` | ~560 | **Review the definitions**, not the code: flaky = `0 < k < N`; `consistency = 1 - flaky/n`. Unit-tested against fixtures; the *definitions* are a judgement call that belongs to you. |
| `aeval/runner.py`, `aeval/worker.py` | ~640 | **Skim.** The subprocess/timeout protocol is the riskiest machinery; empirically verified (§1.4 killed a hung run correctly). |
| `aeval/schema.py` | ~570 | **Skim.** Boring by design. |
| `run_eval.py`, `tests/` | ~800 | **Skim.** CLI is verified against `--help`; tests pass. |
| `README.md` | — | **Spot-check.** I verified every CLI command against the real parser, but re-read the task/backend authoring examples. |

**My estimate: ~35–40% of the code deserves your own eyes**, concentrated in the task
definitions and the browser-use backend. The plumbing below it is tested and, more
importantly, has been *run*.

---

## 5. What I verified vs. what I did not

### Verified (I ran it, output is on disk)

| claim | evidence |
| --- | --- |
| `python -m pytest tests/ -q` → **33 passed** | test run, this session |
| `python run_eval.py --help`, `list-tasks`, `list-backends`, `analyze --help` | run; all four work with no browser installed |
| `python -c "import aeval"` | works in a bare interpreter with neither playwright nor matplotlib installed |
| `pip install -r requirements.txt` | succeeded on CPython 3.13.12 (after §1.6) |
| `playwright install chromium` | succeeded (Chromium 131.0.6778.33) |
| **Oracle sweep, 20 tasks × 1 run → 19/20 (95%)** | `results/oracle/<session>/` |
| Hard timeout works | `inet_nested_frames_read` was killed at 60 s and recorded as `error_type="timeout"` (§1.4) |
| Graceful browser-use skip | `list-backends` prints `NO` + a hint; no crash |
| Offline analysis path | `analyze` recomputes the report from JSON with no browser |
| Every README command matches the CLI | checked against `run --help` / `analyze --help` output |

### NOT verified

* **`browser-use` backend** — never executed (§3.4).
* **WSL** — everything above was done on Windows. The code paths are OS-agnostic, but I
  did not test them.
* **The 20 tasks' selectors over time** — they were correct on the day I ran the oracle.
  Assume they rot.
* **Any LLM agent number** — I never ran one.
* **Cost / token / wall-clock efficiency metrics** — not implemented.

---

## 6. Known limitations, stated plainly

1. **Do not read `NaiveBackend`'s pass rate as an LLM pass rate.** It is a harness
   fixture with a perfect planner. (Also stated in the module docstring and README.)
2. **The `frame` category is currently uninterpretable**: one task is blocked by a site
   outage (§2), the other passes for a do-nothing agent (§3.2).
3. **20 tasks is a starter set, not a benchmark.** `aeval/tasks/__init__.py` is marked
   `STARTER_SET` for exactly this reason.
4. **Time constants are guessed.** They were chosen with the *intention* of producing
   visible variance, but the naive backend's report came out with **zero flaky tasks**
   (every task was either 5/5 or 0/5). This is because the naive backend is a perfect
   planner — its only variance source is execution timing, and the time constants
   (`OP_TIMEOUT_MS=2500`, jitter ≤ 0.18 s, `FINAL_SETTLE` ≤ 1.6 s) were not large enough
   to flip any task across the pass/fail boundary. Real flakiness comes from *planning*
   non-determinism (an LLM agent choosing a different action each run), which the naive
   backend does not have. The flaky-detection logic itself is correct — verified with a
   synthetic 3/5 fixture, which the analyzer labels `flaky` as expected. To see real
   flakiness you need to run `--backend browser-use`.
5. **No cost tracking** (tokens, money, wall clock per successful task).
6. **Single browser, single viewport, no proxy/geo variation.**
7. **`--headed` mode was never run.**

---

## 7. If I had another day

1. Replace the `frame` tasks with a real editable-iframe task (probably needs a third
   sandbox site).
2. Dump and pin the actual DOM of both sites as fixtures, so selector rot fails a test
   instead of silently changing a number.
3. Run the oracle 3× and the naive backend 10× per task, to separate "flaky" from
   "my N was too small".
4. Actually install `browser-use` and get one real LLM number on the board — that is
   the number this project is actually for.
5. Add a `--parallel N` flag; the subprocess design makes it a small change.

---

## 8. Independent QA verification (by the delivery director, not the engineer)

The QA agent ran out of quota before producing a final report. The delivery director
(齐活林) picked up the remaining verification by hand. Everything below was run
directly, not relayed.

| check | result |
| --- | --- |
| `pytest tests/` | **33 passed**, 0 failed (14 matplotlib pyparsing warnings, harmless) |
| `python run_eval.py --help` / `run --help` / `analyze --help` / `list-tasks --help` | all work, no browser needed |
| README commands vs actual CLI | **all match** — `--backend`, `--runs`, `--task`, `--category`, `--headed`, `--session`, `--out-dir`, `--no-chart` all exist |
| Offline report rebuild (B) | deleted `reports/`, ran `analyze` only → **identical numbers** (85.0%, 85/100, flaky 0, stable 17, always-fail 3). No browser involved. |
| **Flaky detection (C)** | **Not a bug.** Fed a synthetic 3/5 fixture to the analyzer → correctly labelled `flaky`, generated the "Flaky tasks" section with pattern `PPP..`. The naive backend's 0 flaky is a **data property**: it is a perfect planner whose timing jitter never crosses a pass/fail boundary. Real flakiness requires planning non-determinism (an LLM agent). |
| browser-use graceful skip (E3) | `--backend browser-use` with no package installed → clean error + hint + "skipping gracefully". No crash, no misleading number. |
| Naive-as-LLM misattribution (E1) | grep of report.md / README / analyzer output templates → **no instance** of naive numbers described as LLM results |
| Naive docstring warning (E2) | still present ("engineered, not observed" + "neither upper nor lower bound") |
| Task domains (E4) | only `saucedemo.com` and `the-internet.herokuapp.com`. No WaterlooWorks/LinkedIn/other. |

**Two corrections made to this file by the director:** §3.5 and §6.4 both claimed
flakiness was "real" / "you'll see in the report", which contradicted the actual 0-flaky
naive report. Both passages now state the truth: the naive backend produced no flakiness,
and the detection logic is verified correct against a synthetic fixture.

**Verdict: IS_PASS = YES.** The harness is sound. The one open item (frame category
weakened by a site-side TinyMCE outage + a do-nothing-passes task) is documented in §2
and §3.2 and does not affect the other 18 tasks or the analysis pipeline.
