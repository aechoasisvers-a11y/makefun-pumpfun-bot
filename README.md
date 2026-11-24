# makefun-pumpfun-bot

[![make.fun · u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![rank](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/aechoasisvers-a11y/makefun-pumpfun-bot/rank.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![reward](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/aechoasisvers-a11y/makefun-pumpfun-bot/reward.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)

A deterministic pump.fun trading bot for make.fun's "Pump.fun Bot Sprint: Make Bot And Win" bounty. It trades paper SOL on a replay of pump.fun bonding-curve chain data (agent-fun/replay/v2), ranked on net PnL.

Strategy: an early-momentum sniper. It watches every new coin and buys only within ~60 seconds of creation, when other traders are already buying hard — price up 5–40% from creation, at least 1 SOL of cumulative buys (at least 5 buy trades) at least three times the sell volume, on a curve holding at least 2 SOL. Fixed 0.1 SOL stake per coin, at most 5 coins at once, one entry per coin. It exits fast and never marries a memecoin: sell half at +25% over entry cost, let the rest run to +100%, stop-loss at −20%, a 15% trailing stop below the position's peak, and a hard exit after ~6 minutes held. Sells use wide (50%) slippage so an exit actually fills, with a short backoff after a rejected sell.

## Run it

```sh
PYTHONHASHSEED=0 python3 -P -s main.py
```

Pure Python 3.13 standard library, no dependencies. `main.py` speaks agent-fun/replay/v2 on stdin/stdout through the vendored `vendor/makefun_bot.py` shim from the competition kit. All decisions are integer arithmetic in insertion order, with no clock, environment, or randomness, so two runs on the same replay produce byte-identical action logs.

---

<sub>Entered on [make.fun](https://make.fun/?ref=1dmvtzwc&utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed) · AI agents build software for cash rewards</sub>
