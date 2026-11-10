#!/usr/bin/env bash
# Run from the uploaded, verified source package on the dedicated Linux host.
set -euo pipefail
cd /opt/codefix
test -f app/remote_execution.py
docker info --format '{{.OSType}}' | grep -qx linux
# Verify via a disposable trusted Python image; no candidate code is imported.
docker run --rm --network none --read-only \
  --mount type=bind,src=/opt/codefix,dst=/opt/codefix,readonly \
  --entrypoint python python:3.14-slim /opt/codefix/scripts/verify_execution_package.py
docker build -f docker/runner.Dockerfile -t codefix-test:local docker
docker build -f docker/execution-service.Dockerfile -t codefix-execution:local .
mkdir -p /opt/codefix-data
chmod 700 /opt/codefix-data
docker image inspect codefix-test:local codefix-execution:local --format '{{.Id}}'
printf '%s\n' 'Images built. Next create individual access codes, then start the service.'
