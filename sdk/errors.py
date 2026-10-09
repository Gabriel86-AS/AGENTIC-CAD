"""SDK build-time failures carry a worker-format signal (harness.protocol.Signal fields)."""


def signal(level, gate, cause, location="", action=""):
    return {"level": level, "gate": gate, "cause": cause, "location": location, "action": action}


class SDKError(Exception):
    """Raised when a call can't produce a valid, fully constrained feature. `.signal` is the failure."""

    def __init__(self, gate, cause, location="", action=""):
        super().__init__(cause)
        self.signal = signal("failure", gate, cause, location, action)
