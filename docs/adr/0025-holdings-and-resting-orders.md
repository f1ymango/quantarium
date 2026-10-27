# ADR-0025: Starting holdings, cost basis, and limit and stop orders

- **Status:** Accepted for Static and Feed Batch. Live Feed refuses the new return type until minute-bar fill rules are designed.
- **Date:** 2026-10-21
- **Deciders:** Quantarium maintainers

## Context

A planner that suggests where to buy, trim, and protect a position needs three things Quantarium lacked. It needs the user's holdings and what was paid for them. It needs orders that rest at a price rather than trading at the next open. And it needs a way to say "hold" without restating weights. The core promise must survive: the same input gives the same result, cells stay independent, and existing strategies and checkpoints behave exactly as before.

## Decision

**Starting holdings.**

- A cell may list `initial_positions`, such as `{"NVDA": {"shares": 100, "cost": 120}}`, in a config.
- They are bought at the first session's open with the usual slippage and commission, from `initial_cash`. If the cash is not enough, the cell fails rather than borrowing.
- Every cell's return is therefore still measured from `initial_cash`, so existing reports stay correct, and a planner cell and a buy-and-hold cell given the same holdings start equal.
- The user's own `cost` is kept as the cost basis for strategy rules such as "take profit above 40% on what I paid". Without it, the fill price is used.
- Starting holdings need a cell with one strategy.

**Cost basis.** `Context.cost_basis` gives the average price paid per share held, after slippage and excluding commission. Buys average in, sells leave it unchanged, and a closed position drops it.

**Decisions and orders.** A strategy may return a `Decision` instead of weights:

- `weights`: a target as before, or None to keep holdings. The next open then has no rebalance.
- `orders`: a tuple of `Order(symbol, side, kind, shares, price, expires_after_sessions, tag)`. It replaces all of the cell's open orders, and an empty tuple cancels them. None keeps them.
- `notes`: saved with the day's signal, for the plan behind the decision.
- `Context.open_orders` shows what is still resting.

A `Decision` needs a cell with one strategy, since sleeves cannot share one order book.

**Fill rules on a daily bar.**

- Orders placed after the close of day *t* first act on day *t+1*. They are checked after any rebalance at the open.
- **Stops:**
  - A sell stop fills when the low reaches its price (a buy stop: when the high does).
  - If the open is already past the price, it fills at the open. A gap gives the worse price, as it would for a real stop.
  - A triggered stop trades like a market order, so it pays slippage.
- **Limits:**
  - A sell limit fills when the high reaches its price (a buy limit: when the low does), at that price.
  - If the open is already past it, it fills at the open, the better price.
  - A limit pays no slippage.
- **Market orders:** they act at the next open only. Any shares that cannot fill in that session are cancelled; they cannot unexpectedly trade on a later date.
- **One day touching both a stop and a limit:** the bar cannot show which came first, so stops are processed first. A protective stop beats a take-profit, the less flattering assumption.
- Sells are capped at shares held and buys at cash after commission. Triggered orders can fill in part; the remaining shares stay open until filled, replaced, cancelled, or expired. The previous-volume participation cap is shared across that day's rebalance and resting orders for each symbol.
- When nothing is held, the symbol's remaining sell orders are cancelled, as a broker's bracket order would.
- `expires_after_sessions` counts sessions an order could fill in. After the last one it expires.
- Journal events:
  - Fills are `order` events, with `order_type`, `order_price`, `order_id`, and `tag`, so performance reports count them.
  - Placements, replacements, cancellations, and expiries are `order_placed`, `orders_replaced`, `order_cancelled`, and `order_expired`.
- A split adjusts the cost basis and each resting order's share count and price before checking the split-day bar. An adjustment that would create fractional order shares stops the cell because cash-in-lieu is not modeled. Each changed order produces an `order_adjusted` event.

**Checkpoints.** Cost basis, open orders, the order counter, and whether starting holdings were bought are saved only when used, so existing checkpoints keep their shape. Feed Batch resumes open orders across runs.

## Alternatives considered

- **Starting holdings as positions already held before the first session**, valued at the first close. This breaks every report's assumption that equity starts at `initial_cash`. Buying at the first open keeps one meaning for returns and gives fair comparisons between cells.
- **Optimistic same-day fills** (limit first, or whichever is closer to the open): results would look better than daily data can justify.
- **Intraday order book on minute bars**: belongs with Live Feed and its own timing rules (ADR-0001).

## Consequences

- Daily bars hide the path within a day. A stop and a limit touched on the same day are resolved against the strategy, and a stop at a price the day only touched for a moment still fills.
- A restored strategy without `snapshot` and `restore` may place its orders again after a Feed restart, as any stateless strategy would.
- Unreviewed agent-generated Static scripts still use the existing sandbox protocol, which accepts weight returns only. The new `Decision` return path is available to reviewed strategy scripts; extending the sandbox protocol requires its own safety review.
