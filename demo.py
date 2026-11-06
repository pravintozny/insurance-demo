"""Run: python3 demo.py  -- prints reports from the mock data set."""
import json
from decimal import Decimal

from insurance.mock_data import seed


def default(o):
    return str(o) if isinstance(o, Decimal) else str(o)


if __name__ == "__main__":
    system = seed()
    print(json.dumps(system.reports.summary(), indent=2, default=default))
    print("audit entries:", len(system.audit.entries()), "chain valid:", system.audit.verify())
