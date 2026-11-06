"""Domain exceptions."""


class InsuranceError(Exception):
    """Base class for all domain errors."""


class ValidationError(InsuranceError):
    """Input failed a business validation rule."""


class NotFoundError(InsuranceError):
    """Referenced entity does not exist."""


class StateError(InsuranceError):
    """Operation is not allowed in the entity's current status."""


class PermissionDenied(InsuranceError):
    """Actor lacks the role needed, or violates segregation of duties."""
