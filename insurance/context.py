"""Shared state and collaborators for all services."""
from datetime import datetime

from .audit import AuditLog
from .config import RulesConfig
from .errors import NotFoundError
from .util import IdGenerator


class Context:
    def __init__(self, config=None, clock=None):
        self.config = config or RulesConfig.from_dict()
        self.clock = clock or datetime.now
        self.audit = AuditLog(self.clock)
        self.ids = IdGenerator()
        self.customers = {}
        self.products = {}
        self.policies = {}
        self.claims = {}
        self.settlements = {}
        self.premium_payments = {}
        self._policy_seq = {}

    def today(self):
        return self.clock().date()

    @staticmethod
    def get(store: dict, key: str, label: str):
        try:
            return store[key]
        except KeyError:
            raise NotFoundError(f"{label} {key} not found") from None
