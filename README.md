# ECS CI/CD Pipeline

[![Deploy](https://github.com/liliyapetillo/ecs-cicd-pipeline/actions/workflows/deploy.yml/badge.svg)](https://github.com/liliyapetillo/ecs-cicd-pipeline/actions/workflows/deploy.yml)

My portfolio page, deployed to AWS ECS Fargate through a gated CI/CD
pipeline. The app is deliberately simple; the point was to learn the whole
path a container takes from my laptop to production: tests that block bad
code, a registry, staging, and a human approval before anything goes live.
The same shape as a real team's process, scaled down to one person.

The same image, promoted — staging on the left, production on the right:

<img src="docs/images/staging.png" alt="The app running in staging: Environment STAGING in the footer" width="45%"> <img src="docs/images/prod.png" alt="The same app running in production: Environment PRODUCTION in the footer" width="45%">

## Add-on: "Ask about my experience" with Bedrock

A question box on the page, answered by Amazon Bedrock (Nova Lite) from
`prompts/resume_facts.md`. Since every request costs money on a public
endpoint, it shipped with a per-IP rate limit, a hard daily token cap
(~$2/day) with a 25% alarm, and an off-topic guard.
Details and what testing caught: [docs/ask-feature.md](docs/ask-feature.md).

<img src="docs/images/bedrock_answer.png" alt="Question about Liliya answered from the facts file: She speaks English, Ukrainian, and Russian" width="48%"> <img src="docs/images/bedrock_off_topic.png" alt="Off-topic question about boiling an egg gets the fixed reply: I can only answer questions about Liliya's experience, skills, and projects" width="48%">

*(Not kept running continuously, to control AWS cost — the screenshots above are it live in both environments.)*

## Architecture

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

Three IAM identities, one per actor:

- **The deploy role**, used by GitHub Actions via OIDC.
- **`ecsTaskExecutionRole`**, used by ECS to pull the image and write logs.
- **`myapp-task-role`**, used by the app: `GetItem`/`UpdateItem` on one
  table and `bedrock:InvokeModel` on one model, nothing else.

## Design decisions

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

## What failed while building this, and how it was diagnosed

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

## What's next

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

## Setup

```bash
docker build -t myapp .
docker run -p 8080:8080 -e AWS_REGION=us-east-1 -v ~/.aws:/root/.aws:ro myapp
```

Then open `http://localhost:8080`. The counter and `/ask` use your local AWS
credentials, hence the `~/.aws` mount.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Endpoints

- `GET /` - Portfolio page with a DynamoDB-backed thumbs-up counter
- `POST /like` - Increments the counter, returns the new count as JSON
- `POST /ask` - Answers a question about my background via Bedrock, as JSON
- `GET /health` - Health check

## Scripts

- `scripts/health_check.py` - Fails if the prod service isn't running as
  many tasks as it wants. Runs right after `deploy-prod`.
