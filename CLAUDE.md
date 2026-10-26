# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Flask portfolio page (`app.py`) deployed to AWS ECS Fargate via a gated GitHub Actions pipeline. The app is intentionally small; the repo is mainly about the CI/CD path and AWS setup (see README.md for architecture, IAM roles, and past failures).

## Commands

```bash
pip install -r requirements-dev.txt        # flask, gunicorn, boto3, pytest, ruff
ruff check .                               # lint (config in pyproject.toml; CI runs it before pytest)
AWS_DEFAULT_REGION=us-east-1 pytest        # all tests
AWS_DEFAULT_REGION=us-east-1 pytest test_app.py::test_ask_returns_answer   # one test

# Run locally (DynamoDB counter and /ask use your local AWS credentials)
docker build -t myapp .
docker run -p 8080:8080 -e AWS_REGION=us-east-1 -v ~/.aws:/root/.aws:ro myapp

# LLM evals for /ask (promptfoo) against a running app; ASK_URL defaults to http://localhost:8080/ask
# The Nova Pro grader needs the two extra -p packages. --no-cache after prompt changes (responses are cached),
# --max-concurrency 1 because Nova Pro throttles the grader at higher concurrency (shows up as ERROR).
npx -y -p promptfoo -p @aws-sdk/client-bedrock-runtime -p @smithy/node-http-handler promptfoo eval --no-cache --repeat 3 --max-concurrency 1
# Full local suite: the same checks plus a groundedness judge on every row (not in CI until calibrated; ~2x slower)
npx -y -p promptfoo -p @aws-sdk/client-bedrock-runtime -p @smithy/node-http-handler promptfoo eval -c promptfoo-groundedness.yaml --no-cache --max-concurrency 1
# Generated red team (local only): generate attacks with Nova Pro, then run them with plain `eval`.
# `promptfoo redteam eval` (and `eval` on a file that still has the `redteam:` key) stops at an
# interactive email prompt, so delete that key from the generated file first.
PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION=true npx -y -p promptfoo -p @aws-sdk/client-bedrock-runtime -p @smithy/node-http-handler promptfoo redteam generate -c promptfoo-redteam.yaml -o redteam-generated.yaml
```

`evals/golden_set.ipynb` runs the same questions by hand (VS Code or JupyterLab) against the local app.

`app.py` creates boto3 clients at import time, so tests need a region set (CI sets `AWS_DEFAULT_REGION`). Tests never hit AWS: they monkeypatch `app.is_rate_limited`, `app.reserve_tokens`, `app.settle_tokens`, `app.bedrock.converse`, `app.table`, etc. Follow that pattern for new tests.

For local eval runs, raise `ASK_LIMIT_PER_WINDOW` (env var) so the per-IP rate limit doesn't throttle the suite; in promptfoo, 429/503 responses show up as ERROR, not FAIL.

## Architecture

- **Routes** (`app.py`): `GET /` renders `templates/index.html` with env/version/hostname and the thumbs-up count; `POST /like` increments the counter; `POST /ask` answers questions via Bedrock; `GET /health`.
- **One DynamoDB table** (`COUNTER_TABLE`, default `thumbs-up-counter`) holds everything, distinguished by `id`: `counter`, `ratelimit#<ip>#<window>`, and the daily token-budget item. Rate-limit/budget items use TTL on `expires_at`. The task role only has `GetItem`/`UpdateItem` on this table and `bedrock:InvokeModel` on one model, so new features must fit those permissions (no Scan/Query/PutItem, no other tables) or need an IAM change made by hand in the console.
- **`/ask` flow** (order matters, it's the cost guardrail): validate question (≤300 chars) → per-IP rate limit (`request.remote_addr`; there's no load balancer, so X-Forwarded-For is deliberately not trusted) → `reserve_tokens()` does one conditional write reserving the worst-case `TOKEN_RESERVATION` against `DAILY_TOKEN_BUDGET` → Bedrock `converse` (Nova Lite, cached system prompt) → `settle_tokens()` swaps the reservation for actual usage (0 on failure). Any limiter error fails closed (503, no Bedrock call). The model returns the `OFF_TOPIC` sentinel for off-topic questions, which the app replaces with a fixed reply. Each request logs one JSON line (`log_ask`) to stdout/CloudWatch, intentionally without IP.
- **Prompt**: `SYSTEM_PROMPT` embeds `prompts/resume_facts.md` (the only source of facts, plus confidentiality rules); `build_user_message` repeats key rules next to the question because the small model follows them better there. Prompt wording was tuned with `evals/questions.csv` + `promptfooconfig.yaml`; see the AI section of README.md for why (e.g. a classify-first prompt rejected real questions, Nova Micro missed facts). Re-check `DAILY_TOKEN_BUDGET` math if `BEDROCK_MODEL_ID` changes.
- **Timeouts**: boto3 timeouts are short on purpose so slow AWS calls can't hold a gunicorn worker past its 30s timeout (2 workers in the Dockerfile).
- **New runtime files** must be added to the explicit `COPY` lines in the `Dockerfile`, which copies only `app.py`, `static/`, `templates/`, `prompts/`.

## Deployment

Push to `main` triggers `.github/workflows/deploy.yml`: `test` → `build-push` (image tagged with the commit SHA to ECR) → `deploy-staging` → manual approval (`production` environment) → `deploy-prod` → `scripts/health_check.py`. Auth is via OIDC (`AWS_ROLE_ARN` secret); `APP_ENV`/`APP_VERSION` are injected at render-task-definition time. Each job reconstructs the image URI itself instead of passing it as a job output, because GitHub drops outputs containing the masked account ID. Images built locally on Apple Silicon need `--platform linux/amd64` for Fargate.

`scripts/RUNBOOK.md` describes how to respond to each CloudWatch alarm.
