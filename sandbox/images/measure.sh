#!/bin/sh
# /opt/devteam/measure - DevTeam AI sandbox launcher (Part E; platform-owned, baked into both images).
#
# Runs the recipe's fixed argument list exactly as given ("$@": no string is ever evaluated), then records
# the container's own cgroup v2 counters so the platform can report peak memory and recognise out-of-memory
# kills even for very short processes:
#   /work/.devteam/sandbox-metrics.json = {"peak_bytes": <memory.peak>, "oom_kills": <memory.events oom_kill>}
# The command's exit status is returned unchanged. The metrics are informational: they never turn a
# failure into a success.
"$@"
status=$?
peak=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null)
oom=$(sed -n 's/^oom_kill //p' /sys/fs/cgroup/memory.events 2>/dev/null)
mkdir -p /work/.devteam 2>/dev/null
printf '{"peak_bytes": %s, "oom_kills": %s}\n' "${peak:-null}" "${oom:-null}" > /work/.devteam/sandbox-metrics.json 2>/dev/null
exit "$status"
