# modal-orchestrator 中文运行手册

> 配套项目：https://github.com/gakkistar/Auto-Run-Prl
>
> 本文档面向使用者，覆盖：**怎么跑起来 → 各项参数 → 状态文件与续跑 → Dashboard → 常见问题 → 修改建议 → 测试盲区**。

---

## 0. 前置条件

- **Python 3.10+**（开发用的 3.13，更低版本未测）
- **Windows 11 + PowerShell** 是主测平台。Linux/macOS 理论可用（asyncio、subprocess、signal 都做了跨平台处理），但**未实测**。
- 一份 Modal workspace token 的 CSV，每行 `token_id,token_secret`。
- 一个 `modal run` 能直接吃的 config（Python 文件，里面定义 `@app.function(gpu="H100")` 或相应 GPU 类型）。
- 网络能正常访问 modal.com。

---

## 1. 安装

只做一次。

```powershell
git clone https://github.com/gakkistar/Auto-Run-Prl.git auto-run-prl
cd auto-run-prl
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,dashboard]"
```

校验：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m modal_orchestrator --help
modal --version
```

应该看到：`42 passed`、`run` 和 `status` 两个子命令、modal 版本号。

如果以后**不需要 Dashboard**，安装时可以省掉那个 extra：`pip install -e ".[dev]"`，跑 `--dashboard` 会直接报错提示装 extra。

---

## 2. 准备两个文件

### 2.1 `tokens.csv`

**格式**：每行一个 token 对，逗号分隔。

```csv
# 注释行以 # 开头会被跳过
# 空行会被跳过
# 可选表头：token_id,token_secret 会被自动识别并跳过
ak-XXXXXXXXXXXXXXXX,as-YYYYYYYYYYYYYYYY
ak-AAAAAAAAAAAAAAAA,as-BBBBBBBBBBBBBBBB
```

注意：
- **token_id（`ak-...`）和 token_secret（`as-...`）必须成对**。Modal 仪表盘只能看到 `ak-...`；`as-...` 只在创建那一刻显示一次，没保存就只能重建 token。
- 重复的 `token_id` 会被自动去重（保留第一次出现的）。
- 字段两侧空格会自动去掉。
- 支持 UTF-8 BOM（PowerShell 5.1 的 `Set-Content -Encoding utf8` 默认会加 BOM，已兼容）。
- **建议**放在项目根目录命名为 `tokens.csv`，已经在 `.gitignore` 里，不会误传 git。

### 2.2 `config`（要在 H100 上跑的工作负载）

最小示例已经在 `examples/smoke-h100.py`：

```python
import subprocess
import modal

app = modal.App("orchestrator-smoke")
image = modal.Image.debian_slim(python_version="3.11")

@app.function(gpu="H100", image=image, timeout=120)
def check_gpu() -> str:
    r = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
         "--format=csv,noheader"],
        capture_output=True, text=True, check=False,
    )
    line = r.stdout.strip() or r.stderr.strip()
    print(f"GPU info: {line}")
    return line

@app.local_entrypoint()
def main() -> None:
    print(check_gpu.remote())
```

跑一次大约消耗 **<\$0.01** credit，专门用来验证管线。

**写你自己的 config 时**：
- 用 `@app.function(gpu="H100")` 或者 `gpu="H100:8"`（多卡）等。
- 用 `@app.local_entrypoint()` 标记 `modal run` 的入口函数。
- 容器里的输出（S3 / HF Hub 等）需要在 config 自己处理——orchestrator 不收集输出。
- timeout 不要设太长——免费额度大约 1-2 小时 H100 就用完了。

---

## 3. 跑

### 3.1 最小命令

```powershell
.\.venv\Scripts\python.exe -m modal_orchestrator run `
    --tokens tokens.csv `
    --config examples\smoke-h100.py
```

默认行为：
- 并发 20 张卡同时跑
- 状态写到 `./state.json`
- 每张卡的日志写到 `./logs/<token_id>.log`
- 用 `modal run <config>` 启动子进程

跑完会打印类似：

```
Final counts:
  used_ok: 18
  used_failed: 2
```

### 3.2 带 Dashboard

```powershell
.\.venv\Scripts\python.exe -m modal_orchestrator run `
    --tokens tokens.csv `
    --config examples\smoke-h100.py `
    --dashboard
```

启动后 stderr 会打印 `Dashboard: http://127.0.0.1:8000/`，浏览器开就行。

每 2 秒自动刷新，能看：
- 顶部各状态计数
- 每个 token 的状态/时间戳/退出码
- 点 Log 看日志末尾 200 行
- 按状态筛选

### 3.3 所有可选参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--tokens FILE` | (必填) | CSV 路径 |
| `--config X` | (必填) | 传给 `modal run` 的参数（文件路径或模块名）|
| `--state FILE` | `state.json` | 持久化状态文件 |
| `--logs DIR` | `logs/` | 每 token 的日志目录 |
| `--max-parallel N` | `20` | 最大同时运行的卡数 |
| `--cmd X`（可多次）| `["modal","run","{config}"]` | 自定义启动命令，`{config}` 是占位符 |
| `--retry-aborted` | off | 重新启用上次崩溃时 in_flight 的 token（**不**重试 used_aborted 的）|
| `--shutdown-grace S` | `60.0` | Ctrl+C 后等待运行中子进程的秒数 |
| `--dashboard` | off | 启用 Web Dashboard |
| `--dashboard-host H` | `127.0.0.1` | Dashboard 监听地址 |
| `--dashboard-port P` | `8000` | Dashboard 端口 |
| `-v`/`--verbose` | off | 详细日志 |

### 3.4 自定义启动命令

如果你不是用默认的 `modal run config.py`，比如想用模块入口点：

```powershell
... --cmd modal --cmd run --cmd -m --cmd "{config}::entrypoint"
```

每个 `--cmd` 追加一个 argv 元素。字面量 `{config}` 会被 `--config` 的值替换。

例：用 Python 直跑 fake_runner（这就是测试用的）：

```powershell
... --cmd .\.venv\Scripts\python.exe --cmd .\tests\fixtures\fake_runner.py --cmd "{config}"
```

---

## 4. 状态文件与续跑

### 4.1 状态机

每个 token 的状态：

| status | 含义 |
|---|---|
| `available` | 在池子里，没动过 |
| `in_flight` | 当前有子进程在跑 |
| `used_ok` | 跑完，exit code = 0 |
| `used_failed` | 跑完，exit code ≠ 0（额度耗尽、认证失败、config bug 等都算）|
| `used_aborted` | 被中断（Ctrl+C、超时、崩溃）|

**终态**：`used_ok` / `used_failed` / `used_aborted`。终态的 token 重跑时不会再被碰。

### 4.2 续跑

直接用同一个 `--state` 文件再跑一次：

```powershell
.\.venv\Scripts\python.exe -m modal_orchestrator run `
    --tokens tokens.csv `
    --config examples\smoke-h100.py
```

逻辑：
- 状态文件里**终态**的 token：跳过，不再调度。
- 状态文件里 `available` 的 token：调度运行。
- 状态文件里 `in_flight` 的 token：**默认**当成 `used_aborted`（保守起见，避免重复烧额度）。加 `--retry-aborted` 可以重置回 `available`。
- 全新的 token（CSV 里有但 state.json 里没有）：自动 ensure_available 加入池子。

### 4.3 查进度

不进 Dashboard 也行：

```powershell
.\.venv\Scripts\python.exe -m modal_orchestrator status --state state.json
```

输出：

```
State file: state.json
Total tokens: 50
  used_ok: 30
  used_failed: 3
  in_flight: 5
  available: 12
```

只显示非零的计数。

### 4.4 想重新跑一些 token？

| 想做的事 | 怎么做 |
|---|---|
| 重跑某个 used_failed 的 token | 编辑 state.json，把那个 token 的 status 改回 `"available"`（手动） |
| 重跑全部 | 删 state.json（重新调度全部 token），或换 `--state` 路径 |
| 重新启用上次崩的 in_flight | 加 `--retry-aborted` |
| 看哪些 token 失败了 | `Get-Content state.json | ConvertFrom-Json` 里筛 used_failed |

> **没有 retry-failed 命令**（YAGNI）。如果你经常需要，建议加（见第 7 节）。

---

## 5. 故障排查

### 5.1 安装阶段

| 现象 | 处理 |
|---|---|
| `pip install` 卡在 modal | 网络问题。换源 `pip install ... -i https://pypi.tuna.tsinghua.edu.cn/simple` |
| `Activate.ps1` 被 PS 阻止 | 单次：`Set-ExecutionPolicy -Scope Process Bypass`，或不激活直接用 `.\.venv\Scripts\python.exe ...` |
| `modal --version` 报命令找不到 | 没激活 venv，或者激活了但是是不同的 venv |

### 5.2 跑起来后

| 现象 | 处理 |
|---|---|
| 所有 token 都秒变 used_failed | 看 `logs/<token_id>.log`。常见原因：modal CLI 未装、config 文件路径错、网络无法访问 modal.com、token 字段错位（id/secret 写反了）|
| Final counts 全是 used_failed: N | 先用单 token 直接 `modal run` 验证 config 没问题，再用 orchestrator |
| Dashboard 起不来 | 8000 端口被占了。`--dashboard-port 8001` 或别的，或者看 stderr 警告：bind 失败时 orchestrator **不会**中止，只是没 dashboard |
| Ctrl+C 之后还在等 | 默认 grace 是 60s。`--shutdown-grace 5` 缩短。或者再按一次 Ctrl+C，asyncio 会强杀 |
| 状态显示 in_flight 但实际进程不在 | 上次崩了。再跑一次会自动转 used_aborted。或者 `--retry-aborted` 重置 |
| 日志文件出现 `# token_id=﻿ak-...`（前面有个隐形字符）| BOM 没被吃掉？检查 tokens.py 是否是最新版（应该用 utf-8-sig） |

### 5.3 真在 Modal 上跑时

> ⚠️ 下面几个**未在真实 Modal 环境下验证**，先放假设：

- **额度耗尽**：假设 `modal run` 会以非零退出码 + stderr 报错退出。orchestrator 会记为 `used_failed`，继续下一个。
  - 真实情况未测，可能 modal 是阻塞超时还是立即报错，行为可能不一样。
- **认证失败**（token 写错了）：假设 `modal run` 立即报错退出，记为 `used_failed`。也未测。
- **modal 服务端 5xx**：未测。
- **网络抖动**：subprocess 不会自动重试。失败就失败。

---

## 6. Dashboard 细节

启动后 `http://127.0.0.1:8000/` 三个端点：

- `GET /` — HTML 页面
- `GET /api/state` — 当前所有 token 状态的 JSON 快照
- `GET /api/log/<token_id>` — 该 token 日志末尾 200 行（纯文本）

**安全提示**：只绑 127.0.0.1。如果你 `--dashboard-host 0.0.0.0` 暴露到公网，orchestrator 会 log warning 但**不会拒绝**。Dashboard 没有任何鉴权，不要这么干。

---

## 7. 修改 / 改进建议

按"我猜你迟早会需要"排序。

### 7.1 容易加，价值高

1. **`validate-tokens` 子命令**：
   `python -m modal_orchestrator validate-tokens --tokens tokens.csv`
   只解析 CSV、报数量、查重复，不调 Modal。一行 `load_tokens()` 包装。

2. **`retry-failed` 选项**：
   `python -m modal_orchestrator run ... --retry-failed`
   把 used_failed 的 token 重置回 available。当 Modal 出过临时故障想全员重试时有用。

3. **额度安全阀**：
   `--max-token-burn 30`——预先粗略统计已 used_failed 的"额度估算"，超阈值就不再启新的。防止脚本失控烧 credit。

4. **每 token 启动间隔**：
   `--stagger-seconds 1`——不要一秒内冲 20 个 modal CLI，给 Modal 那边一点喘息。
   现在虽然有 semaphore 限制并发，但启动是齐头并进的。

5. **Dashboard 加时间序列图**：
   每分钟 throughput / 累计成功/失败折线。现在只看截面快照。

### 7.2 中等价值

6. **热加载 token 池**：
   运行中往 tokens.csv 追加几行，orchestrator 自动 pick up。
   现在是启动时一次性 snapshot。

7. **失败原因聚类**：
   读所有 used_failed token 的日志末尾，按 stderr 关键词聚类（"credit exhausted" vs "auth failed" vs "timeout"）。Dashboard 显示。

8. **真实 Modal 集成测试**：
   一个标记为 `@pytest.mark.modal` 的测试套件，需要环境变量 `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`，跑 `examples/smoke-h100.py`。CI 默认 skip，本地手动开。

9. **Linux/macOS 验证**：
   现在代码写了平台分支但只在 Windows 测过。在 Linux CI 上跑一次完整 pytest，至少确认 `loop.add_signal_handler` 路径走通。

10. **State JSON 文件加锁**：
    现在假设单进程使用。如果用户同时开两个 orchestrator 指向同一 state.json，会互相覆盖。加 fcntl/msvcrt 排他锁是稳妥做法。

### 7.3 大改造

11. **持久化运行 / 后台模式**：
    现在是前台进程，关了终端就停。`--daemon` + systemd/Windows Service 集成。

12. **重新设计为生产者-消费者队列**：
    Critical reviewer 当初提到过：现在是上来一次创建 N 个 asyncio.Task。N=10000 时是 10000 个 Task 全在 event loop 里 idle。换成 `asyncio.Queue` + 固定 worker 数就常数 task。但对 N≤几百的现实规模没意义。

13. **多机调度**：
    一个 orchestrator 拆成 coordinator + 多 worker 节点。完全不是当前架构能容纳的，要重写。需求够强再考虑。

14. **结果聚合**：
    现在 orchestrator 不收集 token 输出（按设计是 config 自理）。要加，就得规定一个返回值约定（比如 stdout 最后一行是 JSON）。

---

## 8. 测试盲区（**未覆盖**）

诚实说当前 42 个测试覆盖了什么、**没覆盖**什么。

### 8.1 测了的

- ✅ tokens.py 全部解析逻辑（含 BOM、注释、空行、表头、去重、错误格式）
- ✅ state.py 全部状态机、原子写、resume 语义、损坏 JSON 处理
- ✅ runner.py 子进程启动、env 隔离、退出码、日志捕获、取消传播
- ✅ scheduler.py semaphore 并发上限、失败不影响其他 worker、跳过已完成、shutdown 在首个 worker 完成后仍生效
- ✅ cli.py 端到端跑通、status 子命令、续跑幂等、tokens 文件缺失报错
- ✅ dashboard.py 三个端点、端口占用 fallback、HTML 渲染

### 8.2 **没测**——使用时要心里有数

#### 真实 Modal 集成

| 没测的事 | 真要踩坑会怎样 |
|---|---|
| 真实 `modal run` 子进程 | 所有测试都用 `fake_runner.py`。如果 modal CLI 行为/退出码语义和我们假设的不一样，整个失败映射可能错位 |
| Modal 免费额度耗尽时的真实退出码 | 我们假设 non-zero exit，但具体码值未知。日志里能看到 stderr 就好 |
| `modal run` 写错 token 时的退出码 | 同上 |
| Modal 服务端 5xx / 长尾延迟 | 子进程会一直等还是会 timeout？看 modal CLI 自己怎么处理 |
| 网络断了 | subprocess 会阻塞直到 TCP 超时，可能上百秒 |
| 多 token 同时请求 Modal | 可能触发 Modal 的速率限制（429）。我们没做退避 |

#### 规模

| 没测的事 | 风险 |
|---|---|
| 大规模并发（>50 张卡同时跑）| asyncio.Task 数量、Modal 那边限流、Windows 进程数上限。具体上限不知道 |
| 大量 token（>1000）| state.json 写盘成本上升。每次状态变化都全文写。1000 个 token 应该还行，10000 个可能要分片 |
| 长时间运行（>4 小时）| 内存有没有泄漏？日志文件目录会不会堆爆？asyncio 长跑稳定性？没跑过 |
| 大日志文件（>1MB/token）| `/api/log` 现在是 `read_text` 然后取最后 200 行——其实是全文读再截尾。大文件慢，应该用 `tail`-style 反向读 |

#### 平台

| 没测的事 | 状态 |
|---|---|
| Linux | 代码写了 POSIX 分支，**未实跑** |
| macOS | 同上 |
| Windows 10 | 只在 Windows 11 测过。Windows 10 的 PowerShell 5.1 行为可能有差异 |
| WSL | 没测 |

#### 边角

| 没测的事 | 风险 |
|---|---|
| Ctrl+C 真的按下去（不是 mock） | smoke test 6 用的是 `Stop-Process -Force`，不等同 SIGINT。理论上 cli.py 的信号处理逻辑是对的，但真实交互未验证 |
| Dashboard 的真实浏览器渲染 | 用 aiohttp 测试客户端测了端点，没用真实浏览器测过页面 |
| Dashboard 在 token 池很大时的渲染 | 表格 >1000 行可能卡。没加分页 |
| state.json 损坏后的恢复 | 测试只覆盖了 JSON decode 错误。如果文件被截断到一半（os.replace 不应发生）、或权限丢失、或磁盘满，行为未知 |
| 两个 orchestrator 同时指向同一 state.json | 无锁。会互相覆盖，可能写出非法状态 |
| token_id 里有奇怪字符（中文、emoji、空格） | `_safe_log_name` 会过滤，但池里仍是原值。CSV 解析时未限制字符集 |

---

## 9. 出问题怎么定位

按顺序看：

1. **Final counts 都 used_failed** → 看 `logs/<token_id>.log` 末尾。基本就能看出是 modal CLI 报什么错。
2. **某个 token 卡 in_flight 很久** → 看那个 token 的 log 末尾，看是 modal 在等什么。如果是 modal 自己的 bug，Ctrl+C 等 grace 跳过。
3. **orchestrator 自己崩了** → `--verbose` 重跑，stderr 上会有 logging。
4. **Dashboard 不显示新数据** → F12 看 `/api/state` 返回是否更新；端口、host 检查。
5. **续跑没跳过已完成的** → `python -m modal_orchestrator status --state state.json` 看一下 state 文件是不是真的有这些 token 记录。

---

## 10. 一次完整的"准备 → 真跑"流程示例

假设你有 30 个 token、要跑 `my_inference.py` 这个真实 workload。

```powershell
# 1. 把 CSV 放好（建议命名 tokens.csv，已 gitignore）
# 编辑 tokens.csv，每行 ak-...,as-...

# 2. 干跑验证 CSV 没问题（不调 modal）
.\.venv\Scripts\python.exe -c "from modal_orchestrator.tokens import load_tokens; ts = load_tokens('tokens.csv'); print(f'loaded {len(ts)} tokens')"

# 3. 拿 1 个 token 直接 modal run 验证 config 没问题（不走 orchestrator）
$env:MODAL_TOKEN_ID = "ak-..."
$env:MODAL_TOKEN_SECRET = "as-..."
modal run my_inference.py
# 看到正常退出后，清掉环境变量
Remove-Item env:MODAL_TOKEN_ID, env:MODAL_TOKEN_SECRET

# 4. 用 orchestrator 拿前 3 个 token 小规模试跑
.\.venv\Scripts\python.exe -m modal_orchestrator run `
    --tokens tokens.csv `
    --config my_inference.py `
    --max-parallel 3 `
    --dashboard

# 浏览器打开 dashboard 观察，三个都跑完后看 state.json 全是 used_ok 才安全

# 5. 满量跑
.\.venv\Scripts\python.exe -m modal_orchestrator run `
    --tokens tokens.csv `
    --config my_inference.py `
    --max-parallel 20 `
    --dashboard

# 中途 Ctrl+C 也安全：再跑同样命令自动续

# 6. 跑完后
.\.venv\Scripts\python.exe -m modal_orchestrator status --state state.json
```

---

## 11. 文件结构速查

```
auto-run-prl/
├── src/modal_orchestrator/
│   ├── tokens.py        # CSV 加载
│   ├── state.py         # JSON 状态文件 + 原子写
│   ├── runner.py        # 单 token subprocess
│   ├── scheduler.py     # asyncio pool + semaphore + 优雅关停
│   ├── dashboard.py     # aiohttp 服务 + 内嵌 HTML
│   ├── cli.py           # argparse 入口
│   └── __main__.py      # `python -m modal_orchestrator`
├── tests/               # 42 个测试
├── examples/
│   └── smoke-h100.py    # 占位 H100 workload（nvidia-smi）
├── docs/
│   ├── superpowers/plans/2026-05-29-modal-token-orchestrator.md
│   └── usage-zh.md      # 本文档
├── README.md            # 英文简要文档
├── pyproject.toml
├── tokens.example.csv   # 样本 token 文件（占位）
└── .gitignore           # tokens.csv / state.json / logs/ 已忽略
```
