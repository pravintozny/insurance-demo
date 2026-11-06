"""Customer management."""
from datetime import date

from .errors import ValidationError
from .models import Customer
from .security import require
from .util import EMAIL_RE, PHONE_RE, age_on


class CustomerService:
    def __init__(self, ctx):
        self.ctx = ctx

    def _validate(self, first_name, last_name, dob, email, phone, address, national_id):
        cfg = self.ctx.config
        if not first_name or not first_name.strip() or not last_name or not last_name.strip():
            raise ValidationError("first and last name are required")
        if not isinstance(dob, date) or dob > self.ctx.today():
            raise ValidationError("date of birth must be a past date")
        age = age_on(dob, self.ctx.today())
        if not (cfg["min_customer_age"] <= age <= cfg["max_customer_age"]):
            raise ValidationError(
                f"customer age {age} outside {cfg['min_customer_age']}-{cfg['max_customer_age']}")
        self._validate_contact(email, phone, address)
        if not national_id or not national_id.strip():
            raise ValidationError("national id is required")

    @staticmethod
    def _validate_contact(email, phone, address):
        if not email or not EMAIL_RE.match(email):
            raise ValidationError(f"invalid email: {email!r}")
        if not phone or not PHONE_RE.match(phone):
            raise ValidationError(f"invalid phone: {phone!r}")
        if not address or not address.strip():
            raise ValidationError("address is required")

    def create(self, actor, first_name, last_name, date_of_birth, email, phone,
               address, national_id) -> Customer:
        require(actor, "customer.manage")
        self._validate(first_name, last_name, date_of_birth, email, phone, address, national_id)
        email = email.strip().lower()
        for c in self.ctx.customers.values():
            if c.national_id == national_id:
                raise ValidationError("a customer with this national id already exists")
            if c.email == email:
                raise ValidationError("a customer with this email already exists")
        cust = Customer(self.ctx.ids.next("CUS"), first_name.strip(), last_name.strip(),
                        date_of_birth, email, phone, address.strip(), national_id)
        self.ctx.customers[cust.id] = cust
        self.ctx.audit.record(actor, "customer.created", "customer", cust.id,
                              {"name": f"{cust.first_name} {cust.last_name}"})
        return cust

    def get(self, customer_id):
        return self.ctx.get(self.ctx.customers, customer_id, "customer")

    def update_contact(self, actor, customer_id, email=None, phone=None, address=None):
        require(actor, "customer.manage")
        c = self.get(customer_id)
        new_email = email.strip().lower() if email else c.email
        new_phone, new_addr = phone or c.phone, address or c.address
        self._validate_contact(new_email, new_phone, new_addr)
        if any(o.email == new_email and o.id != c.id for o in self.ctx.customers.values()):
            raise ValidationError("a customer with this email already exists")
        changes = {k: [old, new] for k, old, new in
                   (("email", c.email, new_email), ("phone", c.phone, new_phone),
                    ("address", c.address, new_addr)) if old != new}
        c.email, c.phone, c.address = new_email, new_phone, new_addr
        self.ctx.audit.record(actor, "customer.contact_updated", "customer", c.id, changes)
        return c

    def deactivate(self, actor, customer_id):
        require(actor, "customer.manage")
        c = self.get(customer_id)
        c.active = False
        self.ctx.audit.record(actor, "customer.deactivated", "customer", c.id)
        return c

    def policies_of(self, customer_id):
        """Policy ownership: all policies held by the customer."""
        self.get(customer_id)
        return [p for p in self.ctx.policies.values() if p.customer_id == customer_id]
