"""Plans and their limits, from data/plans.json, the same file the public site reads (web/src/site/plans.ts). A plan's
paid periods are the ones it has a price for; each is sold as the billing product `<plan>_<period>` (POLAR_PRODUCTS
keys). A per camera plan is bought for a number of watched cameras, up to its watched_cameras."""
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PLANS_FILE = Path(__file__).resolve().parents[1] / "data" / "plans.json"
PERIODS = ("month", "year")


@dataclass(frozen=True)
class Limits:
    watched_cameras: int | None  # the most a plan watches (a per camera plan: the most one can buy); None: agreed
    retention_days: int  # footage is deleted this long after it was saved; agreed: the longest any plan keeps
    ai_checks_day: int | None  # AI requests a day; per watched camera on a per camera plan; None: agreed
    enrolled_staff: int | None  # None: agreed
    min_cameras: int | None = None


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    limits: Limits
    prices: dict[str, int]  # period to rupees, per watched camera on a per camera plan; only the periods sold
    per_camera: bool
    contact: bool  # sold by talking to us, not by checkout

    @property
    def free(self) -> bool:
        return not self.contact and not any(self.prices.values())

    @property
    def sold(self) -> bool:
        """Bought through checkout."""
        return not self.contact and any(self.prices.values())


def product_key(plan: str, period: str) -> str:
    return f"{plan}_{period}"


@dataclass(frozen=True)
class Catalog:
    default: str  # the plan of a society without a paid subscription
    plans: dict[str, Plan]
    refund_days_yearly: int

    def rank(self, plan_id: str | None) -> int:
        """Higher for a bigger plan: data/plans.json lists plans smallest first."""
        return list(self.plans).index(plan_id) if plan_id in self.plans else -1

    def get(self, plan_id: str | None) -> Plan:
        return self.plans.get(plan_id or "") or self.plans[self.default]

    def sold(self) -> list[Plan]:
        return [p for p in self.plans.values() if p.sold]

    def by_product_key(self) -> dict[str, tuple[str, str]]:
        return {product_key(p.id, period): (p.id, period) for p in self.sold() for period in p.prices}

    def next_up(self, plan_id: str | None) -> Plan | None:
        """The smallest plan above this one that checkout sells."""
        return next((p for p in self.sold() if self.rank(p.id) > self.rank(plan_id)), None)


def parse(data: dict) -> Catalog:
    raw = data["plans"]
    longest = max(p["limits"]["retention_days"] for p in raw if p["limits"]["retention_days"])
    plans = {}
    for p in raw:
        lim = p["limits"]
        prices = {period: p.get(f"price_{period}_inr") for period in PERIODS}
        plans[p["id"]] = Plan(p["id"], p["name"],
                              Limits(lim["watched_cameras"], lim["retention_days"] or longest, lim["ai_checks_day"],
                                     lim["enrolled_staff"], lim.get("min_cameras")),
                              {k: v for k, v in prices.items() if v}, bool(p.get("per_camera")),
                              bool(p.get("contact")))
    default = data.get("default_plan") or next(p.id for p in plans.values() if p.free)
    return Catalog(default, plans, int(data["refund_days_yearly"]))


@lru_cache
def catalog(path: Path = PLANS_FILE) -> Catalog:
    return parse(json.loads(Path(path).read_text()))
