# modal-orchestrator

Run a Modal GPU workload across many Modal workspace tokens in parallel. Each
token runs the same config file once on a fresh container; per-token state is
persisted to disk so the run is resumable.

## When to use this

You have N Modal workspace tokens (each with its own free credit pool) and a
single Modal config file that defines a `@app.function(gpu="H100")`. You want
to launch the same workload on as many workspaces as possible, run them until
each token's credit is exhausted (or each finishes successfully), and not have
to babysit the rotation.

## Install

```powershell
git clone <your-fork-url> auto-run-prl
cd auto-run-prl
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

You also need the modal CLI on PATH:

```powershell
modal --version
```

(it's installed by `pip install -e .` as a transitive dependency).

## Token file format

`tokens.csv`:

```
# Lines starting with # are skipped.
# Blank lines are skipped.
# An optional header row "token_id,token_secret" is recognized and skipped.
ak-XXXXXXXX,as-YYYYYYYY
ak-AAAAAAAA,as-BBBBBBBB
```

Duplicate `token_id`s are deduplicated (first wins). UTF-8 with or without BOM
(`utf-8-sig`) is accepted, so `Set-Content -Encoding utf8` from PowerShell 5.1
works.

## Run

```powershell
python -m modal_orchestrator run `
    --tokens tokens.csv `
    --config path\to\your_modal_app.py `
    --max-parallel 20
```

Default state file is `./state.json` and default log directory is `./logs/`.
Each token gets one log file at `logs/<token-id>.log` capturing the full
subprocess stdout+stderr.

To use a custom launch command (default is `modal run {config}`):

```powershell
python -m modal_orchestrator run `
    --tokens tokens.csv `
    --config myapp `
    --cmd modal --cmd run --cmd -m --cmd "{config}::entrypoint"
```

Each `--cmd` appends one argv element. The literal `{config}` is replaced with
the value of `--config`.

## Resume

Re-running the command with the same `--state` file picks up where the previous
run left off. Tokens in terminal states (`used_ok`, `used_failed`,
`used_aborted`) are not retouched. Tokens that were `in_flight` at the time of
a crash are marked `used_aborted` (we cannot tell whether the previous run
actually consumed credit, so the safe default is to not retry). Pass
`--retry-aborted` to override that: in-flight tokens are reset to `available`.

## Status

```powershell
python -m modal_orchestrator status --state state.json
```

Prints the count of tokens in each non-zero status.

## Failure model

Modal's free credit is around $30 per workspace and runs out after roughly 1-2
hours of H100 time. When credit is exhausted, `modal run` exits non-zero with
an error in its log; the orchestrator records `used_failed` and moves on.
There is no retry — a failed token stays failed for the lifetime of the
state file.

## Shutdown

Ctrl+C stops dispatching new tokens and waits up to `--shutdown-grace` seconds
(default 60) for in-flight subprocesses to terminate. Anything still running
after that is force-killed and marked `used_aborted`. The state file is
written after every transition so an unclean kill still leaves resumable state.

## Layout

- `src/modal_orchestrator/tokens.py` — load CSV
- `src/modal_orchestrator/state.py` — JSON state file, atomic writes
- `src/modal_orchestrator/runner.py` — launch one subprocess per token
- `src/modal_orchestrator/scheduler.py` — async pool, semaphore, signals
- `src/modal_orchestrator/cli.py` — argparse entry point

## Tests

```powershell
pytest
```

Tests use a fake `modal` script (`tests/fixtures/fake_runner.py`) so they
don't touch Modal's servers.
