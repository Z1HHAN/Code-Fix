#!/usr/bin/env bash
set -euo pipefail
cd /opt/codefix
test -s /opt/codefix-data/access.json
test "$(docker info --format '{{.OSType}}')" = linux
runner_image="$(docker image inspect codefix-test:local --format '{{.Id}}')"
service_image="$(docker image inspect codefix-execution:local --format '{{.Id}}')"
# Source/evidence paths must be identical on host and controller: sibling Docker
# containers receive host bind-mount paths, not paths private to the controller.
docker run -d --name codefix-execution --restart unless-stopped \
  --read-only --tmpfs /tmp:rw,nosuid,size=67108864 \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 512m --memory-swap 512m --cpus 0.5 --pids-limit 128 \
  --log-opt max-size=10m --log-opt max-file=2 \
  -p 127.0.0.1:8780:8780 \
  --mount type=bind,src=/opt/codefix,dst=/opt/codefix,readonly \
  --mount type=bind,src=/opt/codefix-data,dst=/opt/codefix-data \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  -e CODE_FIX_EXECUTION_APPROVED=1 \
  -e "CODE_FIX_DOCKER_IMAGE=$runner_image" \
  -e CODE_FIX_EXECUTION_ROOT=/opt/codefix-data/jobs \
  -e CODE_FIX_EXECUTION_TOKENS_FILE=/opt/codefix-data/access.json \
  "$service_image"
printf '%s\n' 'Service bound only to 127.0.0.1:8780. Configure HTTPS before client access.'
