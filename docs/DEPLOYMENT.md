# Deployment

The CLI and the API ship as a single image. `serve` runs uvicorn against the same FastAPI app,
so there is one process and one artifact to deploy.

## Building

```bash
docker build -t gcp-explorer .
```

The image is a multi-stage `python:3.13-slim` build. It runs as a non-root user (`explorer`,
uid 10001), uses `gcpe` as its entrypoint, and runs `serve --host 0.0.0.0 --port 8000`
by default.

## Running locally

Mount your local ADC file read-only:

```bash
# The API
docker run --rm -p 8000:8000 \
    -v "$HOME/.config/gcloud/application_default_credentials.json:/adc.json:ro" \
    -e GOOGLE_APPLICATION_CREDENTIALS=/adc.json \
    -e GOOGLE_CLOUD_QUOTA_PROJECT=<your-quota-project> \
    gcp-explorer

# Any CLI command: replace the default arguments
docker run --rm \
    -v "$HOME/.config/gcloud/application_default_credentials.json:/adc.json:ro" \
    -e GOOGLE_APPLICATION_CREDENTIALS=/adc.json \
    -e GOOGLE_CLOUD_QUOTA_PROJECT=<your-quota-project> \
    gcp-explorer search projects/my-project --type bucket
```

`GOOGLE_CLOUD_QUOTA_PROJECT` sets the project the Cloud Asset API must be enabled on (see
[GCP setup](GCP_SETUP.md)). To use a [site config](CONFIGURATION.md), mount it as well and set
`GCP_EXPLORER_CONFIG`.

## Cloud Run and GKE

**No credentials are baked into the image**, and CI fails the build if a credential-shaped
file appears in it. On Cloud Run or GKE, ADC comes from the metadata server:

1. Run the service as a dedicated service account.
2. Grant that account `roles/cloudasset.viewer` on the scopes it should search.
3. Enable the Cloud Asset API on the project that owns the service account, which is where
   service-account calls are billed, or set `GOOGLE_CLOUD_QUOTA_PROJECT` to another project
   that has it enabled. If the API is missing, the error names the project to fix.

No mount and no code change are needed.

## Health probes

| Endpoint | Use as | Behaviour |
|:---|:---|:---|
| `/healthz` | Liveness | Always `200 {"status": "ok"}`. Makes no external calls, so a Google outage never restarts the container. |
| `/readyz` | Readiness | `200` when ADC resolves and the CAI client can be built, otherwise `503` with the reason. It never calls CAI, so frequent probes from every pod don't use up quota. |

The image's `HEALTHCHECK` polls `/healthz`.

## Sizing

Each request is served on a worker thread (Starlette's default pool of 40). The CAI client is
created once per process and shared. Response caching is off unless a request sets
`cache_ttl`, and the cache is per process and limited to 256 entries.
