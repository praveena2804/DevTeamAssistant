"""DevTeam AI run worker (LG-07, D-41).

The worker is the only process that executes runs; the API only queues jobs and sets control flags.
Part D.1 creates the package so its import rules are active from the start. The worker itself
(job queue, single-worker lease, heartbeat, recovery) is built in checkpoint D.4.
"""
