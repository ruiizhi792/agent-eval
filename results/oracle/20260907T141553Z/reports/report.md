# AgentEval report

- **backend**: `oracle`
- **tasks**: 20
- **runs per task**: 1
- **total runs**: 20
- **sessions**: 20260907T141553Z

> Read this first: a pass rate is only meaningful next to its variance. Every number below was computed from the JSON files in `results/` — no browser was involved, so you can recompute it with `python run_eval.py analyze`.

## Headline

| metric | value |
| --- | --- |
| overall pass rate | 95.0% (19/20) |
| Bernoulli sd of a single run | 0.218 |
| standard error of the pass rate | 0.049 |
| run-to-run suite pass rate (mean) | 95.0% |
| run-to-run suite pass rate (sd) | 0.000 |
| stable tasks (stable) | 19 |
| **flaky tasks** (flaky) | **0** |
| always-failing tasks (always-fail) | 1 |
| consistency (1 - flaky/tasks) | 1.000 |

Suite pass rate by repetition — run 1: 95.0%.

## By category

| category | tasks | runs | passed | pass rate | flaky | always-fail |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `auth` | 5 | 5 | 5 | 100.0% | 0 | 0 |
| `nav` | 4 | 4 | 4 | 100.0% | 0 | 0 |
| `form` | 3 | 3 | 3 | 100.0% | 0 | 0 |
| `table` | 3 | 3 | 3 | 100.0% | 0 | 0 |
| `dynamic` | 3 | 3 | 3 | 100.0% | 0 | 0 |
| `frame` | 2 | 2 | 1 | 50.0% | 0 | 1 |

## Per task

| task | category | difficulty | passes | pass rate | status | mean ms |
| --- | --- | --- | --- | ---: | --- | ---: |
| `inet_iframe_type_text` | frame | medium | 0/1 | 0.0% | always-fail | 4221 |
| `inet_dropdown_select` | form | easy | 1/1 | 100.0% | stable | 3536 |
| `inet_dynamic_loading_1` | dynamic | easy | 1/1 | 100.0% | stable | 8848 |
| `inet_js_confirm_accept` | dynamic | medium | 1/1 | 100.0% | stable | 3487 |
| `inet_login_invalid_password` | auth | easy | 1/1 | 100.0% | stable | 4388 |
| `inet_login_success` | auth | easy | 1/1 | 100.0% | stable | 3900 |
| `inet_nested_frames_read` | frame | hard | 1/1 | 100.0% | stable | 7266 |
| `inet_table_read_cell` | table | medium | 1/1 | 100.0% | stable | 3400 |
| `inet_table_row_count` | table | easy | 1/1 | 100.0% | stable | 3475 |
| `inet_table_sort_last_name` | table | hard | 1/1 | 100.0% | stable | 3521 |
| `sauce_add_to_cart_single` | nav | easy | 1/1 | 100.0% | stable | 3674 |
| `sauce_add_to_cart_three` | nav | medium | 1/1 | 100.0% | stable | 3715 |
| `sauce_cart_remove_item` | nav | medium | 1/1 | 100.0% | stable | 3816 |
| `sauce_checkout_happy_path` | form | hard | 1/1 | 100.0% | stable | 3886 |
| `sauce_checkout_missing_postal` | form | medium | 1/1 | 100.0% | stable | 3869 |
| `sauce_login_locked_user` | auth | easy | 1/1 | 100.0% | stable | 3661 |
| `sauce_login_success` | auth | easy | 1/1 | 100.0% | stable | 3661 |
| `sauce_login_wrong_password` | auth | easy | 1/1 | 100.0% | stable | 3737 |
| `sauce_logout` | nav | easy | 1/1 | 100.0% | stable | 4608 |
| `sauce_sort_price_low_high` | dynamic | medium | 1/1 | 100.0% | stable | 3741 |

## Flaky tasks (the point of the whole exercise)

None observed. Either the backend is deterministic on this suite, or N is too small — raise `--runs` before concluding anything.

## Always-failing tasks

- `inet_iframe_type_text` (frame/medium) — 0/1
  - last failure: 1/2 checks failed (first: iframe_text_matches -> iframe_text='Your content goes here.' expected='AgentEval was here')

## How to reproduce

```bash
python run_eval.py run --backend oracle --runs 1
python run_eval.py analyze --backend oracle
```

Raw evidence: 20 JSON files under `results/`. Each file contains the task id, the run index, the backend, the seed, the full action sequence, the final URL, the per-assertion grading result and the duration.
