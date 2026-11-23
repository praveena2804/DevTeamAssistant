"""DevTeam AI run worker (LG-07, D-41).

The worker is the only process that executes runs; the API only queues jobs and sets control flags.
``python -m devteam_worker`` starts it (``main``); ``worker.Worker`` holds the single-worker lease,
heartbeat, startup recovery and the job loop.
"""
