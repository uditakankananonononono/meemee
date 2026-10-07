"""Backend-independent queued-job fencing errors."""


class LeaseLostError(RuntimeError):
    """The worker's claimed lease no longer authorizes terminal publication."""
