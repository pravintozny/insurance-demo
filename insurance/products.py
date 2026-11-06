"""Insurance product management."""
from decimal import Decimal

from .errors import ValidationError
from .models import Coverage, Exclusion, Product, ProductType
from .security import require
from .util import D, money


class ProductService:
    def __init__(self, ctx):
        self.ctx = ctx

    def create(self, actor, code, name, ptype, base_annual_premium, coverages,
               exclusions=(), allowed_durations_months=(12,)) -> Product:
        require(actor, "product.manage")
        ptype = ProductType(ptype)
        if not code or any(p.code == code for p in self.ctx.products.values()):
            raise ValidationError(f"product code {code!r} missing or already used")
        base = money(base_annual_premium)
        if base <= 0:
            raise ValidationError("base annual premium must be positive")
        if not coverages:
            raise ValidationError("a product needs at least one coverage")
        codes = [c.code for c in coverages]
        if len(set(codes)) != len(codes):
            raise ValidationError("duplicate coverage codes")
        if not any(c.mandatory for c in coverages):
            raise ValidationError("a product needs at least one mandatory coverage")
        for c in coverages:
            if D(c.limit) <= 0:
                raise ValidationError(f"coverage {c.code}: limit must be positive")
            if D(c.deductible) < 0 or D(c.deductible) >= D(c.limit):
                raise ValidationError(f"coverage {c.code}: deductible must be >= 0 and < limit")
            if not (0 <= D(c.copay_pct) <= 100):
                raise ValidationError(f"coverage {c.code}: copay must be 0-100")
            if c.waiting_period_days < 0:
                raise ValidationError(f"coverage {c.code}: waiting period must be >= 0")
            if not c.mandatory and D(c.additional_premium) <= 0:
                raise ValidationError(f"optional coverage {c.code} needs an additional premium")
        durations = sorted(set(allowed_durations_months))
        if not durations or any(d <= 0 for d in durations):
            raise ValidationError("durations must be positive months")
        prod = Product(self.ctx.ids.next("PRD"), code, name, ptype, base, list(coverages),
                       list(exclusions), durations)
        self.ctx.products[prod.id] = prod
        self.ctx.audit.record(actor, "product.created", "product", prod.id,
                              {"code": code, "type": ptype.value, "base": str(base)})
        return prod

    def get(self, product_id) -> Product:
        return self.ctx.get(self.ctx.products, product_id, "product")

    def set_base_premium(self, actor, product_id, new_premium):
        """Premium configuration change; affects only newly priced policies."""
        require(actor, "product.manage")
        p = self.get(product_id)
        new = money(new_premium)
        if new <= 0:
            raise ValidationError("base annual premium must be positive")
        self.ctx.audit.record(actor, "product.premium_changed", "product", p.id,
                              {"old": str(p.base_annual_premium), "new": str(new)})
        p.base_annual_premium = new
        return p

    def set_active(self, actor, product_id, active: bool):
        require(actor, "product.manage")
        p = self.get(product_id)
        p.active = active
        self.ctx.audit.record(actor, "product.activated" if active else "product.retired",
                              "product", p.id)
        return p

    def list(self, ptype=None, active_only=True):
        return [p for p in self.ctx.products.values()
                if (ptype is None or p.type == ProductType(ptype)) and (p.active or not active_only)]
