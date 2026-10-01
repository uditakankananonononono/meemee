# Free, local operation

This profile runs a single API on your computer with SQLite in a named volume.
It does not provision cloud services, collect credentials, generate production
keys, or enable paid/hosted model fallback. No subscription is needed for this
profile; hardware, electricity, storage and downloaded model weights are still
required. Check the license of any model you choose. This is not a verified
public free-host deployment or a zero-cost uptime guarantee.

## Required runtime environment

- `MEEMEE_VAULT_KEY` is required even for API startup. Supply your existing key
  from your own secure store: URL-safe base64 that decodes to exactly 32 bytes.
  Do not put it in the Dockerfile, build arguments, repository, or an image.
  Keep the same key with the same persistent data. Losing it can make encrypted
  records unreadable. Back it up securely; use the supported rotation procedure
  rather than replacing it on a populated volume.
- `MEEMEE_API_TOKEN` is required by this Compose profile as a bootstrap bearer
  token. Supply an existing strong token privately at runtime. Never publish it.
- `MEEMEE_MODEL_BASE_URL` must point to a reachable local OpenAI-compatible
  model server, including `/v1`. `MEEMEE_MODEL_NAME` must name a model already
  available there. No model server or weights are included in the image.
- `MEEMEE_ALLOW_PAID_MODELS=false`, `MEEMEE_HF_FALLBACK=false` and
  `MEEMEE_SHARED_ALLOW_HOSTED=false` disable hosted fallback in this profile.
  Do not add hosted/evaluation-provider credentials or remote model routes.

This patch intentionally provides no key-generation command or sample secret.
If you do not already have the required runtime credentials, startup is blocked
until you supply them through your own secure provisioning process. The native
verification uses only a disposable in-memory test key, never a real vault key.

## Docker Compose

Prerequisite: Docker with Compose v2. Run these commands at the repository root
with the two required secrets already exported in your shell, or use a private,
owner-readable env file outside the repository (the path below is illustrative):

```sh
docker compose --env-file /secure/meemee-runtime.env build
docker compose --env-file /secure/meemee-runtime.env up -d
docker compose --env-file /secure/meemee-runtime.env ps
curl --fail --show-error http://127.0.0.1:8787/health
curl --fail --show-error http://127.0.0.1:8787/ready
curl --fail --show-error http://127.0.0.1:8787/app/ >/dev/null
curl --fail --show-error http://127.0.0.1:8787/product/ >/dev/null
curl --fail --show-error http://127.0.0.1:8787/console/ >/dev/null
```

Only `127.0.0.1:8787` is published. The image listens internally on port 8787.
The named `meemee-data` volume persists `/home/meemee/.meemee`; do not run
`docker compose down -v` unless you intend to delete that data. Keep secure
backups outside the image. Docker environment values can be seen by operators
with Docker access. Do not paste `docker compose config` or inspect output
containing secrets into logs or reports.

The default model URL uses `host.docker.internal`, with a `host-gateway` mapping
for Linux. A host model server bound only to host loopback may still be
unreachable from a Linux bridge container. Configure a private reachable
interface/firewall or an explicit private model-container URL. Do not expose an
unauthenticated model server to the public internet. The application container's
`127.0.0.1` is not the host's loopback address.

Set `MEEMEE_READINESS_REQUIRE_MODEL=true` for inference acceptance. The default
is `false`, so `/ready` can be ready while its `components.model.ok` is false.
Even when required, the current model probe accepts responses below HTTP 500;
HTTP 401/404 can pass. Verify an authenticated actual generation separately.

This Compose file starts only the API. Queued jobs, webhook delivery, reflection
and companion checks need their corresponding worker commands and a separate
operational test. Browser automation needs the browser extra and Chromium;
neither is installed by this Dockerfile. PostgreSQL needs its optional extra,
a configured database and separate acceptance. These are not accepted by an
API import or health response. Signup is disabled in this local profile.

## Native alternative (no Docker)

Python 3.10+ and a local model server are required. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
export MEEMEE_ALLOW_PAID_MODELS=false
export MEEMEE_HF_FALLBACK=false
export MEEMEE_SHARED_ALLOW_HOSTED=false
export MEEMEE_SIGNUP_ENABLED=false
export MEEMEE_MODEL_BASE_URL=http://127.0.0.1:11434/v1
export MEEMEE_MODEL_NAME=qwen2.5-coder:14b
export MEEMEE_DATA_DIR="$HOME/.meemee"
# Required secrets must already be provided privately in this shell.
.venv/bin/meemee serve --host 127.0.0.1 --port 8787
```

The native default uses host loopback rather than a container gateway. Do not
reuse disposable verification data for real operation.

## Acceptance status and regression check

Docker acceptance: **UNVERIFIED**. Docker and Podman were unavailable in the
patch environment. No image build, container startup, Compose execution,
non-root volume-permission check, image healthcheck, or restart-persistence
check was executed there. Native checks cannot substitute for those checks.

The Dockerfile now copies `product_site` and `webapp`, alongside `meemee`,
`meemee_persist_pg` and `console`, before building the installed package.
`tests/test_docker_runtime_packages.py` parses all imports in `meemee.api`,
checks every repository-local top-level package against a reconstructed COPY
subset, and verifies the wheel package list plus the three HTML entry points.
Third-party imports are installed by `pip install .`, not copied from the repo.

```sh
python -m pytest -q tests/test_docker_runtime_packages.py tests/test_deployment_assets.py
```

Before calling Docker accepted, record the exact source revision, successful
build output, healthy running container, `/health`, `/ready` and all three UI
paths. Check an authenticated actual run against the selected local model,
worker behavior when used, correct non-root volume permissions, restart
persistence with the same vault key, and that runtime secrets are absent from
build context/image history. No public/free-host compatibility is claimed;
fixed port 8787, durable storage and local model access need their own design.
