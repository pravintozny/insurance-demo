"""Roles and permissions (role-based access + segregation of duties)."""
from dataclasses import dataclass
from enum import Enum

from .errors import PermissionDenied


class Role(str, Enum):
    ADMIN = "ADMIN"
    AGENT = "AGENT"
    UNDERWRITER = "UNDERWRITER"
    ADJUSTER = "ADJUSTER"
    APPROVER = "APPROVER"
    FINANCE = "FINANCE"


@dataclass(frozen=True)
class Actor:
    name: str
    role: Role


PERMISSIONS = {
    "customer.manage": {Role.AGENT, Role.UNDERWRITER},
    "product.manage": set(),                       # ADMIN only
    "policy.issue": {Role.AGENT, Role.UNDERWRITER},
    "policy.approve": {Role.APPROVER, Role.UNDERWRITER},
    "policy.cancel": {Role.UNDERWRITER},
    "premium.collect": {Role.FINANCE, Role.AGENT},
    "claim.register": {Role.AGENT, Role.ADJUSTER},
    "claim.assess": {Role.ADJUSTER},
    "claim.approve": {Role.APPROVER},
    "claim.settle": {Role.FINANCE},
}

SYSTEM = Actor("system", Role.ADMIN)


def require(actor: Actor, permission: str) -> None:
    if actor is None:
        raise PermissionDenied("an actor is required")
    if actor.role == Role.ADMIN:
        return
    if actor.role not in PERMISSIONS.get(permission, set()):
        raise PermissionDenied(f"role {actor.role.value} may not perform {permission}")
