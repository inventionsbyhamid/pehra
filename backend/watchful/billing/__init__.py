"""Plans, limits and payments per watched camera. `retention_days(workspace)` is how long a society's footage and
records are kept, the one source every retention job reads."""
from watchful.billing.limits import retention_days

__all__ = ["retention_days"]
