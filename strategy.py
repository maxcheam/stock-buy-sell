"""Buy/Sell percentage ladder from the Stock Buy Sell strategy screenshot.

Unwritten rule for the stock market (reference: input/Stock Buy Sell.png):

  Drops from cost basis:
    10% drop → hold
    20% drop → add 10% of current shares
    30% drop → add 30%
    40% drop → add 30%
    50% drop → add 50%

  Rises from cost basis:
    10% rise → hold
    20% rise → hold
    30% rise → sell 10% of current shares
    40% rise → sell 20%
    50% rise → sell 30%
    60% rise → sell 40%
    100% rise → sell everything
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ActionKind = Literal["HOLD", "ADD", "SELL", "SELL_ALL", "FLAT", "NO_PRICE"]


# Highest matching threshold wins. Tiers are ordered ascending by |move| %.
# (min_abs_move_pct, action_kind, size_pct_of_position)
# size_pct is ignored for HOLD / SELL_ALL.
DROP_TIERS: list[tuple[float, ActionKind, float]] = [
    (10.0, "HOLD", 0.0),
    (20.0, "ADD", 10.0),
    (30.0, "ADD", 30.0),
    (40.0, "ADD", 30.0),
    (50.0, "ADD", 50.0),
]

RISE_TIERS: list[tuple[float, ActionKind, float]] = [
    (10.0, "HOLD", 0.0),
    (20.0, "HOLD", 0.0),
    (30.0, "SELL", 10.0),
    (40.0, "SELL", 20.0),
    (50.0, "SELL", 30.0),
    (60.0, "SELL", 40.0),
    (100.0, "SELL_ALL", 100.0),
]


@dataclass(frozen=True)
class StrategyRecommendation:
    symbol: str
    shares: float
    avg_cost: float
    current_price: float | None
    change_pct: float | None
    action: ActionKind
    action_pct: float
    """Percent of *current position* to add or sell (0 for HOLD)."""
    shares_delta: float
    """Positive = buy shares, negative = sell shares."""
    dollar_delta: float
    """Approx cash impact at current price (buy positive, sell negative)."""
    target_price: float | None
    """Price at the active tier threshold (or next meaningful level)."""
    rule_label: str
    rationale: str
    next_level_label: str
    next_level_price: float | None


def _match_tier(
    abs_move_pct: float,
    tiers: list[tuple[float, ActionKind, float]],
) -> tuple[float, ActionKind, float] | None:
    """Return the highest threshold that abs_move_pct meets, or None if below all."""
    matched: tuple[float, ActionKind, float] | None = None
    for threshold, kind, size in tiers:
        if abs_move_pct + 1e-12 >= threshold:
            matched = (threshold, kind, size)
    return matched


def next_tier_info(
    change_pct: float,
) -> tuple[str, float | None]:
    """Describe the next ladder rung from current move % (relative to cost)."""
    if change_pct < 0:
        drop = -change_pct
        for threshold, kind, size in DROP_TIERS:
            if drop < threshold:
                if kind == "HOLD":
                    return f"At −{threshold:.0f}%: hold", None
                return f"At −{threshold:.0f}%: add {size:.0f}% of position", None
        return "At max drop tier (add 50%)", None

    rise = change_pct
    for threshold, kind, size in RISE_TIERS:
        if rise < threshold:
            if kind == "HOLD":
                return f"At +{threshold:.0f}%: hold", None
            if kind == "SELL_ALL":
                return f"At +{threshold:.0f}%: sell everything", None
            return f"At +{threshold:.0f}%: sell {size:.0f}% of position", None
    return "At max rise tier (sell everything)", None


def price_for_threshold(avg_cost: float, threshold_pct: float, *, drop: bool) -> float:
    if drop:
        return avg_cost * (1.0 - threshold_pct / 100.0)
    return avg_cost * (1.0 + threshold_pct / 100.0)


def recommend_for_position(
    symbol: str,
    shares: float,
    avg_cost: float,
    current_price: float | None,
) -> StrategyRecommendation:
    """Apply the ladder to a single open position."""
    if shares <= 1e-9:
        return StrategyRecommendation(
            symbol=symbol,
            shares=0.0,
            avg_cost=avg_cost,
            current_price=current_price,
            change_pct=None,
            action="FLAT",
            action_pct=0.0,
            shares_delta=0.0,
            dollar_delta=0.0,
            target_price=None,
            rule_label="No open position",
            rationale="Net shares are zero after imports.",
            next_level_label="",
            next_level_price=None,
        )

    if current_price is None or current_price <= 0 or avg_cost <= 0:
        return StrategyRecommendation(
            symbol=symbol,
            shares=shares,
            avg_cost=avg_cost,
            current_price=current_price,
            change_pct=None,
            action="NO_PRICE",
            action_pct=0.0,
            shares_delta=0.0,
            dollar_delta=0.0,
            target_price=None,
            rule_label="Price unavailable",
            rationale="Could not fetch a live price; cannot evaluate the ladder.",
            next_level_label="",
            next_level_price=None,
        )

    change_pct = (current_price - avg_cost) / avg_cost * 100.0
    next_label, _ = next_tier_info(change_pct)
    next_price: float | None = None

    # Below first tier on either side → hold (small move).
    if abs(change_pct) < 10.0 - 1e-12:
        if change_pct < 0:
            next_price = price_for_threshold(avg_cost, 20.0, drop=True)
            next_label = "At −20%: add 10% of position"
        else:
            next_price = price_for_threshold(avg_cost, 30.0, drop=False)
            next_label = "At +30%: sell 10% of position"
        return StrategyRecommendation(
            symbol=symbol,
            shares=shares,
            avg_cost=avg_cost,
            current_price=current_price,
            change_pct=change_pct,
            action="HOLD",
            action_pct=0.0,
            shares_delta=0.0,
            dollar_delta=0.0,
            target_price=None,
            rule_label="Within ±10% of cost — hold",
            rationale=(
                f"Price is {change_pct:+.1f}% vs avg cost ${avg_cost:.2f}. "
                "Under the ±10% band the rule is to hold."
            ),
            next_level_label=next_label,
            next_level_price=next_price,
        )

    if change_pct < 0:
        drop = -change_pct
        matched = _match_tier(drop, DROP_TIERS)
        assert matched is not None
        threshold, kind, size = matched
        target = price_for_threshold(avg_cost, threshold, drop=True)

        # Next deeper drop tier for guidance
        next_price = None
        next_label = "At max drop tier (add 50%)"
        for t, k, s in DROP_TIERS:
            if t > threshold:
                next_price = price_for_threshold(avg_cost, t, drop=True)
                if k == "HOLD":
                    next_label = f"At −{t:.0f}%: hold"
                else:
                    next_label = f"At −{t:.0f}%: add {s:.0f}% of position"
                break

        if kind == "HOLD":
            return StrategyRecommendation(
                symbol=symbol,
                shares=shares,
                avg_cost=avg_cost,
                current_price=current_price,
                change_pct=change_pct,
                action="HOLD",
                action_pct=0.0,
                shares_delta=0.0,
                dollar_delta=0.0,
                target_price=target,
                rule_label=f"Drop ≥{threshold:.0f}% — hold",
                rationale=(
                    f"Down {drop:.1f}% from avg cost ${avg_cost:.2f}. "
                    f"Rule: if price drops {threshold:.0f}%, just hold."
                ),
                next_level_label=next_label,
                next_level_price=next_price,
            )

        shares_to_add = shares * (size / 100.0)
        dollars = shares_to_add * current_price
        return StrategyRecommendation(
            symbol=symbol,
            shares=shares,
            avg_cost=avg_cost,
            current_price=current_price,
            change_pct=change_pct,
            action="ADD",
            action_pct=size,
            shares_delta=shares_to_add,
            dollar_delta=dollars,
            target_price=target,
            rule_label=f"Drop ≥{threshold:.0f}% — add {size:.0f}%",
            rationale=(
                f"Down {drop:.1f}% from avg cost ${avg_cost:.2f}. "
                f"Rule: if price drops {threshold:.0f}%, add {size:.0f}% of current shares "
                f"(≈ {shares_to_add:.4g} sh @ ${current_price:.2f})."
            ),
            next_level_label=next_label,
            next_level_price=next_price,
        )

    # Rise side
    rise = change_pct
    matched = _match_tier(rise, RISE_TIERS)
    assert matched is not None
    threshold, kind, size = matched
    target = price_for_threshold(avg_cost, threshold, drop=False)

    next_price = None
    next_label = "At max rise tier (sell everything)"
    for t, k, s in RISE_TIERS:
        if t > threshold:
            next_price = price_for_threshold(avg_cost, t, drop=False)
            if k == "HOLD":
                next_label = f"At +{t:.0f}%: hold"
            elif k == "SELL_ALL":
                next_label = f"At +{t:.0f}%: sell everything"
            else:
                next_label = f"At +{t:.0f}%: sell {s:.0f}% of position"
            break

    if kind == "HOLD":
        return StrategyRecommendation(
            symbol=symbol,
            shares=shares,
            avg_cost=avg_cost,
            current_price=current_price,
            change_pct=change_pct,
            action="HOLD",
            action_pct=0.0,
            shares_delta=0.0,
            dollar_delta=0.0,
            target_price=target,
            rule_label=f"Rise ≥{threshold:.0f}% — hold",
            rationale=(
                f"Up {rise:.1f}% from avg cost ${avg_cost:.2f}. "
                f"Rule: if price rises {threshold:.0f}%, still hold."
            ),
            next_level_label=next_label,
            next_level_price=next_price,
        )

    if kind == "SELL_ALL":
        shares_to_sell = shares
        dollars = -shares_to_sell * current_price
        return StrategyRecommendation(
            symbol=symbol,
            shares=shares,
            avg_cost=avg_cost,
            current_price=current_price,
            change_pct=change_pct,
            action="SELL_ALL",
            action_pct=100.0,
            shares_delta=-shares_to_sell,
            dollar_delta=dollars,
            target_price=target,
            rule_label="Rise ≥100% — sell everything",
            rationale=(
                f"Up {rise:.1f}% from avg cost ${avg_cost:.2f}. "
                f"Rule: if price rises 100%, sell everything "
                f"({shares_to_sell:.4g} sh @ ${current_price:.2f})."
            ),
            next_level_label=next_label,
            next_level_price=next_price,
        )

    shares_to_sell = shares * (size / 100.0)
    dollars = -shares_to_sell * current_price
    return StrategyRecommendation(
        symbol=symbol,
        shares=shares,
        avg_cost=avg_cost,
        current_price=current_price,
        change_pct=change_pct,
        action="SELL",
        action_pct=size,
        shares_delta=-shares_to_sell,
        dollar_delta=dollars,
        target_price=target,
        rule_label=f"Rise ≥{threshold:.0f}% — sell {size:.0f}%",
        rationale=(
            f"Up {rise:.1f}% from avg cost ${avg_cost:.2f}. "
            f"Rule: if price rises {threshold:.0f}%, sell {size:.0f}% of current shares "
            f"(≈ {shares_to_sell:.4g} sh @ ${current_price:.2f})."
        ),
        next_level_label=next_label,
        next_level_price=next_price,
    )


def ladder_reference_table() -> list[dict[str, str]]:
    """Human-readable ladder for the UI."""
    rows: list[dict[str, str]] = []
    for threshold, kind, size in DROP_TIERS:
        if kind == "HOLD":
            action = "Hold"
        else:
            action = f"Add {size:.0f}% of position"
        rows.append(
            {
                "Side": "Drop",
                "Move from cost": f"−{threshold:.0f}%",
                "Action": action,
            }
        )
    for threshold, kind, size in RISE_TIERS:
        if kind == "HOLD":
            action = "Hold"
        elif kind == "SELL_ALL":
            action = "Sell everything"
        else:
            action = f"Sell {size:.0f}% of position"
        rows.append(
            {
                "Side": "Rise",
                "Move from cost": f"+{threshold:.0f}%",
                "Action": action,
            }
        )
    return rows
