def __getattr__(name):
    if name == "AnalyticsAgent":
        from agent.orchestrator import AnalyticsAgent
        return AnalyticsAgent
    raise AttributeError(f"module 'agent' has no attribute {name!r}")

__all__ = ["AnalyticsAgent"]
