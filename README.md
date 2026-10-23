# ECS CI/CD Pipeline

[![Deploy](https://github.com/liliyapetillo/ecs-cicd-pipeline/actions/workflows/deploy.yml/badge.svg)](https://github.com/liliyapetillo/ecs-cicd-pipeline/actions/workflows/deploy.yml)
[![LLM evals](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/liliyapetillo/ecs-cicd-pipeline/main/evals/results/badge.json)](#ai)

My portfolio page, deployed to AWS ECS Fargate through a gated CI/CD
pipeline. The app is deliberately simple; the point was to learn the whole
path a container takes from my laptop to production: tests that block bad
code, a registry, staging, and a human approval before anything goes live.
The same shape as a real team's process, scaled down to one person.

It also hosts an LLM feature, "Ask about my experience", a Bedrock-powered Q&A
with cost guardrails. A golden-set and red-team eval suite runs against staging
on every deploy and blocks the production approval if the pass rate drops:
see the [AI section](#ai).

The same image, promoted — staging on the left, production on the right:

<img src="docs/images/staging.png" alt="The app running in staging: Environment STAGING in the footer" width="45%"> <img src="docs/images/prod.png" alt="The same app running in production: Environment PRODUCTION in the footer" width="45%">

Jump to what you care about: [DevOps](#devops) · [AI](#ai) · [Run it yourself](#run-it-yourself). Each section below expands on click.

## DevOps

<details>
<summary><b>Architecture</b> — pipeline stages, approval gate, runtime, monitoring, IAM</summary>

![CI/CD pipeline: push to main triggers GitHub Actions (test, build-push, deploy-staging, manual approval, deploy-prod), which assumes a single OIDC IAM role to push a SHA-tagged image to ECR and deploy it to the ECS staging and prod services](docs/images/project1_cicd_pipeline.png)

A push to `main` runs `test` → `build-push` → `deploy-staging` →
`deploy-prod`, gated by a manual review in between. Nothing reaches ECR
without passing tests, and nothing reaches production without first
running in staging and being looked at by a person:

![GitHub Actions run: test, build-push, and deploy-staging all passed, deploy-prod is waiting for review, and the deploy-staging job summary links directly to the running staging task](docs/images/github_actions_waiting_approval_2.png)

The review request lands in my inbox:

<img src="docs/images/github_review_email.png" alt="GitHub email notification: 'Deploy: production is waiting for your review', with a Review pending deployments button" width="60%">

Approving it deploys the exact image staging just ran — same digest, no
rebuild — and a moment later both services show it running:

![ECS console: myapp-staging and myapp-prod both Active with 1/1 tasks running, last deployment Completed](docs/images/ecs_tasks.png)

![Runtime and monitoring: a visitor's browser hits the Fargate task directly on port 8080, the task reads and writes the thumbs-up counter in DynamoDB, and CPU/memory CloudWatch alarms notify an SNS topic that emails an alert](docs/images/project1_runtime_and_monitoring.png)

CloudWatch alarms email me through SNS; `scripts/RUNBOOK.md` says what to
check for each one.

![CloudWatch dashboard tracking RunningTaskCount, CPUUtilization, and MemoryUtilization for myapp-prod and myapp-staging side by side](docs/images/cloudwatch_dashboard.png)

There's no load balancer, so visitors hit the task's public IP directly
(which is why that staging link above changes on every deploy).

Four IAM identities, one per actor:

- **The deploy role**, used by GitHub Actions via OIDC.
- **The eval role**, used only by the `eval-staging` job via OIDC: `bedrock:InvokeModel`
  on Nova Pro (the eval grader), nothing else. It can't deploy, and the deploy role
  can't call Bedrock.
- **`ecsTaskExecutionRole`**, used by ECS to pull the image and write logs.
- **`myapp-task-role`**, used by the app: `GetItem`/`UpdateItem` on one
  table and `bedrock:InvokeModel` on one model, nothing else.

</details>

<details>
<summary><b>Design decisions</b> — why Fargate, DynamoDB, OIDC, least privilege</summary>

- **Fargate over EC2 or Kubernetes**: real task definitions and rolling
  deploys, without managing servers or a control plane.
- **DynamoDB, not a relational database**: a one-item counter doesn't need
  one, and it let me scope IAM down to two actions on one table.
- **OIDC instead of AWS keys in GitHub**: each run gets a short-lived token.
  Nothing to leak, nothing to rotate.
- **Least privilege, verified, not assumed**: I checked that the app's role
  can't even list its own IAM policies or describe ECR repositories.
- **Nothing sensitive in the repo or on the page**: secrets live in GitHub
  Secrets; the page has no phone number and no trackers.

</details>

<details>
<summary><b>What failed, and how it was diagnosed</b> — four real failures with root causes</summary>

Most of the real learning happened here, not in the parts that worked on
the first try.

1. **Intermittent 10–30 second page hangs.**
   Gunicorn ran a single sync worker with its default 30-second timeout. An
   occasional slow connection to DynamoDB (via Docker Desktop's virtualized
   network) held that one worker long enough to trip gunicorn's own
   watchdog, which killed and rebooted the worker mid-request. Diagnosed by
   timing repeated `curl` requests and correlating the slow ones with
   `[CRITICAL] WORKER TIMEOUT` lines in the container logs. Fixed by giving
   boto3 an explicit short connect/read timeout with limited retries (fail
   in a few seconds instead of hanging near the 30-second ceiling), and by
   adding a second gunicorn worker so one slow request can't block every
   other request on the same process.

2. **`CannotPullContainerError: ... does not contain descriptor matching
   platform 'linux/amd64'` in ECS.**
   The image was built on an Apple Silicon Mac, which defaults to a
   `linux/arm64` image; Fargate defaults to `linux/amd64`. Diagnosed
   directly from the ECS task's stopped-reason message. Fixed with
   `docker build --platform linux/amd64`.

3. **`Input required and not supplied: image` in the ECS task-definition
   render step.** A job output containing the AWS account ID was being
   silently dropped by GitHub Actions
   (`Skip output 'image' since it may contain secret`, visible only in the
   raw job logs via `gh run view --log`), because `configure-aws-credentials`
   masks the account ID as a secret and GitHub won't propagate any output
   that appears to contain one. Fixed by not passing the image URI across
   jobs at all — each job that needs it logs into ECR itself and
   reconstructs the same deterministic URI locally.

4. **Outputs out of scope.** `deploy-prod` referenced
   `needs.build-push.outputs.image`, but its own `needs:` list only included
   `deploy-staging` — GitHub Actions only exposes a job's outputs to jobs
   that list it *directly* in `needs:`, not transitively through a chain, so
   that reference could never have resolved to anything.

</details>

<details>
<summary><b>What's next</b> — what a real production service would still need</summary>

What a real production service would still need:

- **A load balancer** with TLS and a real domain, tasks in private subnets,
  and autoscaling.
- **A task-count alarm**: nothing alerts yet if the service drops to zero tasks.
- **Blue/green deploys** for instant rollback.
- **Secrets Manager** instead of plain environment variables.
- **Tests for `/like`** with mocked DynamoDB, to match the ones for `/ask`.
- **Container and supply-chain hardening**: non-root user, pinned base
  image, image scanning, Actions pinned to commit SHAs, Dependabot.
- **Terraform** for the ECS and IAM setup I did by hand in the console.

</details>

## AI

<!-- evals-headline:start -->
No eval run recorded yet.
<!-- evals-headline:end -->

<details>
<summary><b>Ask about my experience</b> — Bedrock-powered Q&A, cost guardrails, design choices</summary>

A question box on the page: visitors ask about my background and get a short
answer from Amazon Bedrock (Nova Lite), grounded only in
`prompts/resume_facts.md`. No new compute and no new role, just one more
permission on `myapp-task-role`, scoped to that one model.

<img src="docs/images/bedrock_answer.png" alt="Question about Liliya answered from the facts file: She speaks English, Ukrainian, and Russian" width="48%"> <img src="docs/images/bedrock_off_topic.png" alt="Off-topic question about boiling an egg gets the fixed reply: I can only answer questions about Liliya's experience, skills, and projects" width="48%">

*(Not kept running continuously, to control AWS cost — the screenshots above are it live in both environments.)*

Unlike everything else here, each request costs real money, and the endpoint
is public and unauthenticated. So the guardrails went in with the feature:

- **Per-IP rate limit** (5 questions/minute), an atomic counter in the
  existing DynamoDB table, checked before Bedrock is called.
- **Hard daily token cap** (30M tokens, about $1.90/day). Each request
  reserves its worst-case tokens in one conditional DynamoDB write before
  calling Bedrock, so concurrent requests can't overshoot it. A CloudWatch
  alarm emails at 25% of the budget.
- **Fail closed**: if the limits can't be checked, Bedrock isn't called.
- **Small, bounded requests**: questions capped at 300 characters, answers
  at 200 tokens, and the unchanging system prompt is prompt-cached.
- **Off-topic guard**: the model replies with a sentinel for anything not
  about me, and the app swaps in a fixed reply.
- **Question logging** to CloudWatch (outcome, question, tokens; no IPs),
  with 30-day retention.

**What testing caught:** the first off-topic guard blocked real questions like
"Has she worked with Docker?", and Nova Micro missed facts that were in the
file. Running a fixed set of on- and off-topic questions against the real
model is what settled the prompt wording and the move from Micro to Lite.

**Why no RAG:** the whole facts file (~6k tokens) goes into every prompt, and
Bedrock caches it. RAG would add an embedding model, a vector store and
retrieval code, and the vector store is a standing monthly cost that dwarfs
the ~$2/day token cap for a single 288-line document. It would also add a
retrieval step that can miss facts the model currently always sees. Revisit
only if the facts file outgrows the context window or the per-question cost
stops being trivial.

</details>

<details>
<summary><b>Testing: what's covered</b> — code checks, answer quality, scoring methods, pipeline gate</summary>

**1. Code checks**: does the app's plumbing work?
- **Linting** with ruff, and **unit tests** with AWS mocked: rate limiting, the daily token budget and its refund, fail-closed behavior, error handling, logging without IPs. Both run first in CI (`test_app.py`).

**2. Answer quality**: does the assistant say the right things? (golden set in `evals/questions.csv`, red-team set in `evals/redteam.csv`)
- **Accuracy** (`positive`): real visitor questions are answered correctly from the facts file.
- **Adversarial** (`negative`): off-topic questions, prompt injection, role-play jailbreaks, prompt extraction, and bait to leak confidential details or invent facts.
- **Red team** (`redteam`): 13 further attacks, each tagged with its technique: base64-encoded and Ukrainian-language injection, closing the `<question>` tag to inject instructions, prompt extraction and translation, claiming to be the site owner, JSON format hijacking, identity probing, PII requests, defamation bait, abusive content, output-length abuse, and markup injection.
- **False premises** (`edge`): "15 years of QA, right?" or "AWS Solutions Architect, right?" must be corrected, not agreed with.

**3. How answers are scored**: because "correct" isn't always a string match.
- **Rules** for what must or must not appear (exact match, regex, style rules like no em dash).
- **LLM-as-judge** for meaning rules can't express, graded by Nova Pro, a stronger model than the Nova Lite that answers.
- **The judge is checked too**: I label its failures by hand to see how often the judge itself is wrong (notebook, section 4).
- **Each question runs 3 times**, since answers vary between runs.

**4. Pipeline gate**: does it keep working after every change?
- The suite calls the **deployed staging app over HTTP**, so the real system prompt and guardrails are tested, not the bare model.
- A pass rate under **90% blocks the production approval**, and every run is recorded in the results table below.

**Not covered yet:** latency and cost-per-answer assertions. (There are no multi-turn tests because `/ask` takes a single question with no conversation history.)

</details>

<details>
<summary><b>Golden dataset, notebook walkthrough, and CI results</b> — golden and red-team sets, how to step through them by hand, latest pass rates</summary>

### The golden dataset

[`evals/questions.csv`](evals/questions.csv) is the fixed set of 32 questions
with expected behavior, in three categories:

- **positive**: real questions about my background, checked against the facts file.
- **negative**: off-topic questions, prompt injection, role-play jailbreaks, and
  questions that tempt it to reveal confidential details or invent facts.
- **edge**: false premises that it has to correct.

Each row has up to three expectations (`__expected` columns, written as
`type:value`). The expectations come from reading real model output and
deciding what a correct answer must and must not say. The same file drives
both the notebook and the automated run.

### The red-team set

[`evals/redteam.csv`](evals/redteam.csv) holds 13 adversarial questions, kept
apart so the golden set stays a clean accuracy measure. It uses the same
columns as the golden set: a short description, category, `technique` label,
and expectations. The notebook walks through the golden set
only; the red-team set runs in the automated promptfoo run.

### Walk through it by hand: the notebook

**[`evals/golden_set.ipynb`](evals/golden_set.ipynb)** is a step-by-step
walkthrough meant to be read and run (VS Code, JupyterLab or Colab) against a
local copy of the app. It covers:

1. Sending the golden set to the running app.
2. Rule-based checks, including a test of the checker itself.
3. The LLM judge: what it is for, how it is set up (stronger model, temperature 0, one written rubric per question).
4. Results by category.
5. Reviewing every failure by hand, labeling it a real failure or a judge error, and measuring how good the judge is.

It is the explanation of *why* the checks are built the way they are; the CI
run below is the same suite, automated.

### Automated: promptfoo in CI

Both sets run through [promptfoo](https://www.promptfoo.dev) against
the staging app after `deploy-staging` and before the production approval,
three times each. A pass rate below 90% fails the pipeline. After every run, CI
commits the results back to this repo: the scorecard, trend chart and history
below, the **LLM evals** badge at the top of this README, and
[`evals/results/history.json`](evals/results/history.json). Grader or network
failures are reported as "not graded" instead of counting against the model.
The run's job summary links the full promptfoo report (HTML, plus the JSON for
the promptfoo viewer).

A groundedness check (an LLM judge that fails any claim not in the facts file)
runs locally only, from `promptfoo-groundedness.yaml`. It stays out of CI until
the judge is calibrated: its first run failed 2 correct answers out of 135.

<!-- evals:start -->
No run recorded yet. The first pipeline run after this change fills this in.
<!-- evals:end -->

Raw per-run data: [`evals/results/history.json`](evals/results/history.json).
To run the suite yourself, see the Commands section of `CLAUDE.md`.

</details>

## Run it yourself

<details>
<summary><b>Setup, tests, endpoints, scripts</b> — local run, lint and tests, API routes</summary>

### Setup

```bash
docker build -t myapp .
docker run -p 8080:8080 -e AWS_REGION=us-east-1 -v ~/.aws:/root/.aws:ro myapp
```

Then open `http://localhost:8080`. The counter and `/ask` use your local AWS
credentials, hence the `~/.aws` mount.

### Tests

```bash
pip install -r requirements-dev.txt
ruff check .   # lint
pytest
```

### Endpoints

- `GET /` - Portfolio page with a DynamoDB-backed thumbs-up counter
- `POST /like` - Increments the counter, returns the new count as JSON
- `POST /ask` - Answers a question about my background via Bedrock, as JSON
- `GET /health` - Health check

### Scripts

- `scripts/health_check.py` - Fails if the prod service isn't running as
  many tasks as it wants. Runs right after `deploy-prod`.

</details>
