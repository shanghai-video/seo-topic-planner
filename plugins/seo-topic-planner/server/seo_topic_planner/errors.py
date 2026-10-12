"""Public, non-secret-bearing domain errors."""


class TopicPlannerError(Exception):
    """Base error returned as an MCP tool error."""


class ValidationError(TopicPlannerError):
    """Input or persisted-object validation failed."""


class NotFoundError(TopicPlannerError):
    """An object is absent or belongs to a different workspace."""


class StateError(TopicPlannerError):
    """The requested action is not allowed in the current research state."""


class BudgetError(TopicPlannerError):
    """A research run would exceed the approved budget."""


class AuthError(TopicPlannerError):
    """The local DataForSEO authorization is missing, expired, or rejected.

    Messages never contain tokens, authorization codes, or response bodies.
    """


class ProviderError(TopicPlannerError):
    """A provider failed without exposing credentials or response bodies.

    ``cost_uncertain`` is true when the request may have reached the provider
    and been billed but no usable response came back (a transport timeout,
    connection reset, or an unparseable body).  The caller must not silently
    re-issue such a request: the cost ledger records it as ``UNKNOWN_COST``
    instead of assuming the request was free.
    """

    def __init__(self, message: str = "", *, cost_uncertain: bool = False) -> None:
        super().__init__(message)
        self.cost_uncertain = cost_uncertain
