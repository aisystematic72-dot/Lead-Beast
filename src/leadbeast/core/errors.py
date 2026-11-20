class LeadBeastError(Exception):
    code = "error"


class NotFound(LeadBeastError):
    code = "not_found"


class ValidationFailed(LeadBeastError):
    code = "validation_failed"


class PolicyDenied(LeadBeastError):
    code = "policy_denied"

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class BudgetExceeded(LeadBeastError):
    code = "budget_exceeded"


class CircuitOpen(LeadBeastError):
    code = "circuit_open"


class RobotsDisallowed(LeadBeastError):
    code = "robots_disallowed"


class HostCapReached(LeadBeastError):
    code = "host_cap_reached"
