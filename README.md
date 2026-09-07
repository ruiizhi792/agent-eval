# AgentEval

**Measure how reliably a browser-controlling AI agent completes real web tasks — and
report the variance, not just the average.**

**[English](#english) · [中文](#中文)**

---

<a id="english"></a>

# English

Most agent demos publish one number: "87% success". AgentEval runs every task **N
times** (default 5) and separates three things that a single average hides:

| | meaning |
| --- | --- |
| **stable** | passed every run — trustworthy |
| **flaky** | passed *some* runs — the interesting, dangerous case |
| **always-fail** | never passed — a capability gap |

The flaky column is the point. A task that passes 3/5 is not "a 60% task"; it is an
unreliable task, and it needs a different sentence in your write-up than one that
passes 5/5 or 0/5.

## Why the two test sites (and only these two)

Every task targets one of two **public, automation-friendly** sandboxes:

* `https://www.saucedemo.com` — login, product grid, cart, two-step checkout
* `https://the-internet.herokuapp.com` — login, tables, iframes, nested frames,
  dynamic loading, native JS dialogs

Both exist to be automated. **No task targets a site whose terms of service forbid
automation** — no WaterlooWorks, no LinkedIn, no real accounts. There is a test
(`tests/test_schema.py::test_only_automation_friendly_sites_are_targeted`) that fails
if anyone adds one.

## What "passed" means

Every task carries a `verify(page) -> bool` that asserts **concrete state**: the URL,
an element's exact text, a cart badge number, a table row count. There is no
"looks right" and no LLM judge. When a task fails, the JSON records *which* assertion
broke and what was observed instead.

## Install

Python 3.13 (Windows or WSL — both work).

```bash
# Windows
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium

# WSL / macOS / Linux
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

`playwright install chromium` downloads ~130 MB. If it fails, **nothing else breaks**:
`run_eval.py --help`, `list-tasks`, `list-backends`, `analyze` and `pytest tests/ -q`
all work without a browser. Only `run` needs one.

## The three-step protocol

### Step 1 — calibrate the grader (`oracle`)

Do this first. Always.

```bash
python run_eval.py run --backend oracle --runs 1
```

The oracle replays each task's reference plan with textbook browser hygiene. **It is
not an agent** — it is a test of the *grader*. A healthy run is close to 100%.

Any task where the oracle fails is a bug in the task (stale selector, wrong expected
value) or in the harness. Fix it before you believe any other number.

### Step 2 — run the real sweep

```bash
python run_eval.py                                  # naive backend, 5 runs per task
python run_eval.py run --backend browser-use --runs 5   # the real LLM agent
python run_eval.py run --backend naive --category frame --runs 10
python run_eval.py run --backend naive --task sauce_login_success --runs 20 --headed
```

Each (task × repetition) pair is executed in its **own subprocess** with a hard
wall-clock cap (default 60 s). A hung run is recorded as `error_type="timeout"` and
counted as a failure — hanging is never allowed to be invisible.

### Step 3 — read the report

A report is built automatically after every sweep; to rebuild it offline from the JSON
(needs no browser, no network):

```bash
python run_eval.py analyze --backend naive
python run_eval.py analyze --backend naive --session latest
```

Outputs: `report.md` (tables + flaky list) and `passrate_by_task.png`.

## Backends

| backend | what it is | when to use |
| --- | --- | --- |
| `oracle` | Robust executor of the reference plan. **Not an agent.** | Calibration. Expect ~100%. |
| `naive` | Hand-written **fragile** baseline. **Not an LLM agent.** | Exercising the harness with no API key. |
| `browser-use` | A real LLM-driven browser agent; its own CDP browser is reattached for grading. | Actual agent evaluation. |

`browser-use` owns its browser. AgentEval reconnects to that exact browser through its
CDP endpoint after the run and grades the page the agent actually changed. If the
installed browser-use version cannot expose that endpoint, the run is marked
unavailable rather than silently grading an unrelated blank page. Install the optional
dependency with `pip install 'browser-use[core]'` and run a small sweep before quoting
any LLM result.

> ### ⚠️ Read this before quoting a number from `naive`
>
> `NaiveBackend` is a deterministic script. It makes no inference, it never sees the
> page, and it replays the *correct* plan with deliberately bad browser hygiene.
>
> **Its pass rate says nothing about how often an LLM agent would succeed.** A real
> agent fails at *planning* as well as *execution*; the naive backend always knows the
> right plan. Its numbers are neither an upper nor a lower bound on LLM performance.
>
> It exists so the harness can be exercised end to end and can emit a **real** variance
> report with no API key and no LLM cost. Treat it as a harness fixture, not a subject.

## Adding a task

Tasks live in `aeval/tasks/`. A task is a `TaskSpec`: an instruction, a category, a
difficulty, a **reference plan** (for the deterministic backends) and a **verify
function** (the actual grading).

```python
register_task(TaskSpec(
    task_id="sauce_add_two_items",
    instruction='Log in as "standard_user" / "secret_sauce" and add two items to the cart.',
    category=Category.NAV,
    difficulty=Difficulty.MEDIUM,
    start_url="https://www.saucedemo.com",
    plan=(Step("goto", ...), Step("fill", "#user-name", "standard_user"), ...),
    verify=_verify_cart_badge_is_two,
))
```

Rules for a good task:
1. **The success condition must be programmatic.** "URL equals X", "text equals Y".
2. **Give the oracle a plan.** Without one, `oracle` cannot calibrate.
3. **Negative cases matter.** "submit an invalid form" catches agents that barrel ahead.
4. **Keep it to the two sandboxes** unless the site explicitly allows automation.

## Adding a backend

Subclass `AgentBackend`, implement `run`, register it. Your backend receives a fresh
Playwright page and the `TaskSpec`. Use `task.instruction` as the prompt.
**Never read `task.plan`** — that is the answer key.

## Tests

```bash
python -m pytest tests/ -q
```

The suite is **fully offline**: no browser, no network. It covers the schema, the
registry, the starter task set's invariants, and the analyzer, which is validated
against synthetic JSON fixtures so that every number in the report is provably a pure
function of the files on disk.

## Honest limitations

* **20 tasks is a starter set, not a benchmark.** Review it, replace it, extend it.
* **Selectors are assertions about third-party websites** and will rot. Re-run
  `--backend oracle` regularly; it is the canary.
* **`browser-use` support is unverified in this environment** (no package, no key). See
  `SELF_REPORT.md`.
* **The harness measures execution, not cost.** Tokens, wall-clock and money are not
  tracked yet.

See `SELF_REPORT.md` for the full defect log, design trade-offs, and what to review.

---

<a id="中文"></a>

# 中文

**测量浏览器控制类 AI agent 完成真实网页任务到底有多可靠——报告方差，而不只是平均成功率。**

大多数 agent demo 只公布一个数字："87% 成功率"。AgentEval 把每个任务跑 **N
次**（默认 5 次），然后区分单个平均数掩盖的三种情况：

| | 含义 |
| --- | --- |
| **stable（稳定）** | 每次都过——可信 |
| **flaky（抖动）** | 有时过有时不过——最值得关注的危险情况 |
| **always-fail（总挂）** | 从没过——能力缺口 |

flaky 那一列是整个项目的重点。一个 5 次里过 3 次的任务不是"60% 的任务"，它是一个**不可靠**的任务，在报告里需要跟 5/5 或 0/5 的任务用不同的句子来描述。

## 为什么只用这两个测试站（且只用这两个）

所有任务只指向两个**公开、对自动化友好**的沙箱站：

* `https://www.saucedemo.com` —— 登录、商品列表、购物车、两步结账
* `https://the-internet.herokuapp.com` —— 登录、表格、iframe、嵌套 frame、动态加载、原生 JS 弹窗

这两个站本身就是为自动化练习建的。**没有任何任务指向服务条款禁止自动化的站点**——不碰 WaterlooWorks、不碰 LinkedIn、不碰真实账号。有一个测试
（`tests/test_schema.py::test_only_automation_friendly_sites_are_targeted`）会在有人违规添加站点时失败。

## "通过"是什么意思

每个任务带一个 `verify(page) -> bool`，断言**具体状态**：URL、元素的精确文本、购物车角标数字、表格行数。没有"看起来对"，没有 LLM 当裁判。任务失败时，JSON 会记录**哪条断言挂了**以及实际观察到什么。

## 安装

Python 3.13（Windows 或 WSL 均可）。

```bash
# Windows
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium

# WSL / macOS / Linux
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

`playwright install chromium` 下载约 130 MB。如果失败，**其他功能不受影响**：
`run_eval.py --help`、`list-tasks`、`list-backends`、`analyze` 和 `pytest tests/ -q`
都不需要浏览器。只有 `run` 需要。

## 三步协议

### 第一步——校准评分器（`oracle`）

永远先做这一步。

```bash
python run_eval.py run --backend oracle --runs 1
```

oracle 用教科书式的浏览器操作重放每个任务的参考方案。**它不是 agent**——它是
**评分器**的测试。健康运行应该接近 100%。

oracle 失败的任何任务都是任务本身的 bug（选择器过期、期望值写错）或框架的 bug。
在相信任何其他数字之前，先修它。

### 第二步——跑真实评测

```bash
python run_eval.py                                  # naive 后端，每个任务 5 次
python run_eval.py run --backend browser-use --runs 5   # 真 LLM agent
python run_eval.py run --backend naive --category frame --runs 10
python run_eval.py run --backend naive --task sauce_login_success --runs 20 --headed
```

每个（任务 × 重复）对在**独立子进程**中执行，有硬性墙上时钟上限（默认 60 秒）。
挂起的运行会被记为 `error_type="timeout"` 并算作失败——挂起绝不允许不可见。

### 第三步——读报告

每次评测后自动生成报告；要离线从 JSON 重建（不需要浏览器、不需要网络）：

```bash
python run_eval.py analyze --backend naive
python run_eval.py analyze --backend naive --session latest
```

输出：`report.md`（表格 + flaky 列表）和 `passrate_by_task.png`。

## 三个后端

| 后端 | 是什么 | 何时用 |
| --- | --- | --- |
| `oracle` | 参考方案的稳健执行器。**不是 agent。** | 校准。预期 ~100%。 |
| `naive` | 手写的**脆弱**基线。**不是 LLM agent。** | 没有 API key 时跑通框架。 |
| `browser-use` | 真 LLM 驱动的浏览器 agent；通过 CDP 重连其实际浏览器后评分。 | 真实 agent 评测。 |

`browser-use` 自己管理浏览器。AgentEval 会在运行结束后通过 CDP 重连到**同一个**浏览器，
并对 agent 实际修改过的页面评分。若已安装的 browser-use 版本无法暴露该端点，运行会被明确
标为 unavailable，绝不会静默地对无关的空白页面评分。请用
`pip install 'browser-use[core]'` 安装可选依赖，并先跑一个小规模真实模型评测，再引用结果。

> ### ⚠️ 引用 naive 的数字前必读
>
> `NaiveBackend` 是确定性脚本。它不做推理，不看页面，用故意很差的浏览器操作重放
> **正确**的方案。
>
> **它的通过率不能说明 LLM agent 会成功多少次。** 真 agent 在*规划*和*执行*两个层面
> 都会失败；naive 后端永远知道正确方案。它的数字既不是 LLM 性能的上界也不是下界。
>
> 它存在的意义是让框架能端到端跑通，在没有 API key、没有 LLM 成本的情况下产出
> **真实**的方差报告。把它当框架的夹具，不要当被测对象。

## 添加任务

任务放在 `aeval/tasks/`。一个任务是一个 `TaskSpec`：指令、类别、难度、**参考方案**
（给确定性后端用）和 **verify 函数**（实际评分）。

好任务的规则：
1. **成功条件必须可程序判定。** "URL 等于 X"、"文本等于 Y"。
2. **给 oracle 一个方案。** 没有方案，oracle 无法校准。
3. **负面用例很重要。** "提交无效表单"能抓到只会一路冲的 agent。
4. **只用这两个沙箱站**，除非该站明确允许自动化。

## 添加后端

继承 `AgentBackend`，实现 `run`，注册。你的后端收到一个全新的 Playwright 页面和
`TaskSpec`。用 `task.instruction` 作为提示词。**绝不读 `task.plan`**——那是答案。

## 测试

```bash
python -m pytest tests/ -q
```

测试套件**完全离线**：不需要浏览器、不需要网络。覆盖 schema、注册表、起步任务集
的不变量，以及——最重要的是——分析器，用合成 JSON fixture 验证，保证报告里每个
数字都是磁盘上文件的纯函数。

## 已知局限

* **20 个任务是起步集，不是基准测试。** 审阅它、替换它、扩充它。
* **选择器是对第三方网站的断言**，会随站点改版失效。定期重跑 `--backend oracle`，
  它是金丝雀。
* **`browser-use` 支持在本环境未验证**（没装包、没 key）。见 `SELF_REPORT.md`。
* **框架只测执行，不测成本。** token、墙上时钟和钱还没跟踪。

完整的缺陷日志、设计权衡和需要 review 的内容见 `SELF_REPORT.md`。

## License / ethics

Use it only against sites that permit automated access. The task registry ships with a
guard-rail test that enforces this for the starter set; keep it if you extend the set.

只用在对自动化友好的站点上。任务注册表自带护栏测试，对起步集强制这一点；扩充时请保留。
