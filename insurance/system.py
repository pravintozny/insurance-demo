"""Facade wiring all services over one shared context."""
from .claims import ClaimService
from .context import Context
from .customers import CustomerService
from .payments import PaymentService
from .policies import PolicyService
from .products import ProductService
from .reporting import ReportService


class InsuranceSystem:
    def __init__(self, config=None, clock=None, gateway=None):
        self.ctx = Context(config, clock)
        self.customers = CustomerService(self.ctx)
        self.products = ProductService(self.ctx)
        self.policies = PolicyService(self.ctx)
        self.claims = ClaimService(self.ctx)
        self.payments = PaymentService(self.ctx, gateway)
        self.reports = ReportService(self.ctx)

    @property
    def audit(self):
        return self.ctx.audit
