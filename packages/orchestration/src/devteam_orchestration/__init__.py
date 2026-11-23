"""DevTeam AI LangGraph orchestration (HC-07, roadmap §6).

Only this package (and the worker that runs it) may import ``langgraph``. Agents never see the graph;
the API never compiles or runs it. Checkpoint D.1 provides the local-only safety guard
(:mod:`devteam_orchestration.safety`); the graph itself is built in D.3.
"""
