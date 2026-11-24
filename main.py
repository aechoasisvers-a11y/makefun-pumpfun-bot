"""Pump.fun early-momentum sniper (agent-fun/replay/v2, Python 3.13, stdlib only).

Strategy, written from scratch for this bounty:
  * Watch every new coin. Enter early (within ~60 s of creation) only when other
    traders are already buying it hard: the price has risen 5%-40% from its
    creation price, at least 1 SOL of cumulative buys (at least 5 buy trades)
    at least three times the sell volume, on a curve holding at least 2 SOL
    of real quote reserves (so the 1%-of-reserves order cap lets us buy a real
    stake). One entry per coin, fixed 0.1 SOL stake, at most 5 coins at once.
  * Exit fast, never marry a memecoin: sell half at +25% over entry cost, let
    the rest run to +100%, stop-loss at -20%, a 15% trailing stop below the
    position's peak value (the peak resets when the half-sale lands, so the
    remainder is judged on its own value), and a hard exit after ~6 minutes
    held. A losing coin is sold, not averaged. Sells use wide (50%) slippage
    so an exit actually fills instead of being rejected while the coin keeps
    falling, and a rejected sell backs off 5 slots before retrying.
  * Everything is integer arithmetic on lamports and reserves, iterated in
    insertion order, with no clock, environment or randomness: two runs on the
    same replay produce byte-identical replies.

Fees: a round trip costs ~1.25% curve fees each way plus 105,000 lamports of
tx fees per order, so the +50% / -30% exits need a real edge per trade; the
edge comes from entering only coins whose first minute already shows net
buying, and from cutting losers at -30% while letting winners run to +50%.

The grader runs this as ``python3 -P -s /bot/main.py`` with PYTHONHASHSEED=0.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bot  # noqa: E402  (after the sys.path line: -P leaves the script dir off sys.path)

SOL = makefun_bot.LAMPORTS_PER_SOL

# --- entry parameters -------------------------------------------------------
STAKE_LAMPORTS = SOL // 10            # 0.1 SOL per coin
MIN_STAKE_LAMPORTS = SOL // 100       # 0.01 SOL: the guardrail's minimum round trip
MAX_AGE_SLOTS = 150                   # enter only within ~60 s of creation
MIN_REAL_SOL = 2 * SOL                # curve must hold >= 2 SOL real quote
ENTRY_MIN_RISE_BPS = 500              # price must be up >= 5% from creation
ENTRY_MAX_RISE_BPS = 4000             # ... but not already up > 40% (do not chase)
MIN_BUY_VOL_LAMPORTS = SOL            # >= 1 SOL of cumulative buys
MIN_BUY_TRADES = 5
BUY_SELL_MIN_NUM = 3                  # buys must be >= 3x sells
BUY_SELL_MIN_DEN = 1

# --- exit parameters --------------------------------------------------------
TAKE_HALF_BPS = 2500                  # sell half at +25% over entry cost
TAKE_PROFIT_BPS = 10000               # sell the rest at +100% over entry cost
STOP_LOSS_BPS = 2000                  # sell at -20% under entry cost
TRAIL_DROP_BPS = 1500                 # sell 15% below the position's peak value
MAX_HOLD_SLOTS = 900                  # hard time exit after ~6 minutes
BUY_SLIPPAGE_BPS = 1000               # 10% slippage allowance (2-slot latency)
SELL_SLIPPAGE_BPS = 5000              # 50%: an exit must fill, not be rejected
SELL_RETRY_SLOTS = 5                  # back off this long after a rejected sell

BPS = 10_000


def _price_num_den(curve):
    """Price (quote per token) as a numerator/denominator pair of ints."""
    return curve.virtual_quote_reserves, curve.virtual_token_reserves


def _rise_bps(p0_num, p0_den, p1_num, p1_den):
    """(p1 / p0 - 1) in basis points, integer math, or None if undefined."""
    if p0_num <= 0 or p0_den <= 0 or p1_den <= 0:
        return None
    # (p1_num * p0_den - p0_num * p1_den) * BPS / (p0_num * p1_den)
    num = p1_num * p0_den - p0_num * p1_den
    den = p0_num * p1_den
    if den <= 0:
        return None
    return num * BPS // den


class Coin:
    """Per-coin tape read from this run's Create/Trade events (insertion ordered)."""

    __slots__ = (
        "created_slot", "p0_num", "p0_den", "buy_sol", "sell_sol",
        "buy_trades", "sell_trades", "decided", "buy_pending",
    )

    def __init__(self, created_slot, p0_num, p0_den):
        self.created_slot = created_slot
        self.p0_num = p0_num
        self.p0_den = p0_den
        self.buy_sol = 0
        self.sell_sol = 0
        self.buy_trades = 0
        self.sell_trades = 0
        self.decided = False      # entered, or skipped for good
        self.buy_pending = False  # a buy is in flight for this coin


class Position:
    """Our own record of a held position (entry slot + running peak value)."""

    __slots__ = (
        "entry_slot", "peak_value", "half_taken", "sell_pending",
        "pending_kind", "retry_at", "rejects",
    )

    def __init__(self, entry_slot, peak_value):
        self.entry_slot = entry_slot
        self.peak_value = peak_value
        self.half_taken = False
        self.sell_pending = False
        self.pending_kind = None      # "half" or "all" while a sell is in flight
        self.retry_at = 0             # earliest slot for the next sell attempt
        self.rejects = 0              # consecutive rejected sells


coins = {}        # mint -> Coin, first-seen order
positions = {}    # mint -> Position, first-held order
pending_buy = {}  # client_id -> mint (buys we sent, awaiting their fill)


def on_batch(state, batch):
    orders = []
    caps = state.hello["caps"]
    tx = state.tx_fee_lamports()
    slot = state.slot

    # --- 1. Fold this batch's fills into our own records. -------------------
    for fill in batch["fills"]:
        cid = fill["client_id"]
        mint = fill["mint"]
        status = fill["status"]
        if cid in pending_buy:
            del pending_buy[cid]
            coin = coins.get(mint)
            if coin is not None:
                coin.buy_pending = False
                if status == "filled":
                    coin.decided = True
                    holding = state.positions.get(mint)
                    if holding is not None:
                        cost = holding.cost_lamports
                        value = _value_now(state, mint, holding)
                        positions[mint] = Position(slot, max(cost, value))
                elif status == "rejected":
                    coin.decided = True  # ran away / too big: skip for good
                # status "failed" (seeded 2% tx failure): leave undecided so the
                # entry rule may try once more while the coin is still young.
        elif mint in positions and fill["side"] == "sell":
            pos = positions[mint]
            pos.sell_pending = False
            kind = pos.pending_kind
            pos.pending_kind = None
            if status == "filled":
                pos.rejects = 0
                if kind == "half":
                    # The position just halved: its value halved with it. Reset
                    # the trailing-stop peak to the remainder's own value, or
                    # the stale pre-sale peak would sell it at once.
                    holding = state.positions.get(mint)
                    if holding is not None:
                        pos.peak_value = _value_now(state, mint, holding)
            elif status == "rejected" and mint in state.positions:
                # A full sell can exceed the curve's real quote reserves when a
                # coin rugged, and a fast crash can breach slippage: back off a
                # few slots, then retry smaller instead of spamming every slot.
                pos.rejects += 1
                pos.retry_at = slot + SELL_RETRY_SLOTS
                if kind == "half":
                    pos.half_taken = False  # the half-sale never landed: retry it

    # Drop positions the harness closed (complete/flatten/writeoff fills).
    for mint in list(positions):
        if mint not in state.positions:
            del positions[mint]

    # --- 2. Read this batch's events into the per-coin tape. ---------------
    touched = []
    for e in batch["events"]:
        mint = e["mint"]
        kind = e["event"]
        if kind == "CreateEvent":
            if mint not in coins:
                coins[mint] = Coin(
                    int(e["slot"]),
                    int(e["virtual_quote_reserves"]),
                    int(e["virtual_token_reserves"]),
                )
                touched.append(mint)
        elif kind == "TradeEvent":
            coin = coins.get(mint)
            if coin is None:
                # First sight through a trade: creation price unknown, so this
                # coin can never pass the rise check; track it only for exits.
                coin = Coin(int(e["slot"]), 0, 0)
                coins[mint] = coin
            amount = int(e["sol_amount"])
            if e["is_buy"]:
                coin.buy_sol += amount
                coin.buy_trades += 1
            else:
                coin.sell_sol += amount
                coin.sell_trades += 1
            if mint not in touched:
                touched.append(mint)
        else:  # CompleteEvent
            coin = coins.get(mint)
            if coin is not None:
                coin.decided = True

    # --- 3. Exits first: free slots and cut losers before buying more. -----
    free = state.free_cash_lamports()
    for mint in list(positions):
        pos = positions[mint]
        holding = state.positions.get(mint)
        if holding is None or pos.sell_pending or slot < pos.retry_at:
            continue
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            continue
        value = _value_now(state, mint, holding)
        if value <= 0:
            continue
        if value > pos.peak_value:
            pos.peak_value = value
        cost = holding.cost_lamports
        kind = None
        if not pos.half_taken and value * BPS >= cost * (BPS + TAKE_HALF_BPS):
            kind = "half"
        elif value * BPS >= cost * (BPS + TAKE_PROFIT_BPS):
            kind = "all"
        elif value * BPS <= cost * (BPS - STOP_LOSS_BPS):
            kind = "all"
        elif value * BPS <= pos.peak_value * (BPS - TRAIL_DROP_BPS):
            kind = "all"
        elif slot - pos.entry_slot >= MAX_HOLD_SLOTS:
            kind = "all"
        if kind is None:
            continue
        tokens = holding.token_amount
        if kind == "half":
            tokens //= 2
        elif pos.rejects >= 2:
            tokens = max(1, tokens // 2)
        if tokens <= 0 or free < tx:
            continue
        orders.append({
            "side": "sell", "mint": mint, "token_amount": tokens,
            "max_slippage_bps": SELL_SLIPPAGE_BPS,
        })
        pos.sell_pending = True
        pos.pending_kind = kind
        if kind == "half":
            pos.half_taken = True
        free -= tx
        if len(orders) >= caps["max_orders_per_batch"]:
            return orders

    # --- 4. Entries: young coins with net buying, in event order. -----------
    open_slots = caps["max_open_positions"] - state.committed_position_count()
    for mint in touched:
        if open_slots <= 0 or len(orders) >= caps["max_orders_per_batch"]:
            break
        coin = coins.get(mint)
        if coin is None or coin.decided or coin.buy_pending:
            continue
        if mint in state.positions or mint in positions:
            coin.decided = True
            continue
        if slot - coin.created_slot > MAX_AGE_SLOTS:
            coin.decided = True
            continue
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            coin.decided = True
            continue
        if curve.real_quote_reserves < MIN_REAL_SOL:
            continue  # too small yet: may qualify on a later batch
        rise = _rise_bps(
            coin.p0_num, coin.p0_den,
            curve.virtual_quote_reserves, curve.virtual_token_reserves,
        )
        if rise is None or rise < ENTRY_MIN_RISE_BPS or rise > ENTRY_MAX_RISE_BPS:
            continue  # no momentum yet, or already chased too far
        if (coin.buy_sol < MIN_BUY_VOL_LAMPORTS
                or coin.buy_trades < MIN_BUY_TRADES
                or coin.buy_sol * BUY_SELL_MIN_DEN
                < coin.sell_sol * BUY_SELL_MIN_NUM):
            continue
        cap = state.max_buy_lamports(mint)
        amount = min(cap, STAKE_LAMPORTS)
        if amount < MIN_STAKE_LAMPORTS:
            continue
        if free < amount + tx:
            continue
        cid = state.client_id()
        orders.append({
            "side": "buy", "client_id": cid, "mint": mint,
            "quote_lamports": amount, "max_slippage_bps": BUY_SLIPPAGE_BPS,
        })
        pending_buy[cid] = mint
        coin.buy_pending = True
        free -= amount + tx
        open_slots -= 1
    return orders


def _value_now(state, mint, holding):
    """Current lamport value of a holding from the curve's latest reserves."""
    curve = state.curves.get(mint)
    if curve is None or curve.virtual_token_reserves <= 0:
        return 0
    return holding.token_amount * curve.virtual_quote_reserves // curve.virtual_token_reserves


if __name__ == "__main__":
    makefun_bot.run(on_batch)
