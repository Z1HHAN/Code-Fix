#!/usr/bin/env bash
set -euo pipefail
package_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
# Do not stop another user's active test. No credentials are read.
docker exec codefix-execution python -B -c '
import json, pathlib
active = [p for p in pathlib.Path("/opt/codefix-data/jobs").glob("job-*/job.json")
          if json.loads(p.read_text())["status"] not in {"completed","failed","cancelled","expired","interrupted"}]
if active: raise SystemExit("Execution jobs are active; wait until they finish, then rerun this update.")
'
docker stop --time 90 codefix-execution >/dev/null
trap 'docker start codefix-execution >/dev/null' EXIT
docker run --rm --pull=never --network none --user 0:0 \
  --mount type=bind,src=/opt/codefix,dst=/opt/codefix \
  --mount type=bind,src=/opt/codefix-data,dst=/opt/codefix-data \
  --mount "type=bind,src=$package_dir,dst=/upgrade,readonly" \
  --entrypoint python python:3.14-slim -B /upgrade/update_rpc_103.py
docker start codefix-execution >/dev/null
trap - EXIT
printf '%s\n' 'RPC103 source installed; execution service restarted. Run the qualification command next.'
docker exec codefix-execution python -B -m app.execution_server qualify --project qb-shortest_path_lengths
