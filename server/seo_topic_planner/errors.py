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
    """A provider failed without exposing credentials or response bodies."""
