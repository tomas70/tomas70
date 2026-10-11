# Reddit strategijų lentelės patikra (BTCUSDT)

- Registracija: `reddit_strategies_prereg.md` (parašyta prieš testą).
- Kodas: `../../research_reddit_strats.py`; testai: `../../tests/test_reddit_strats.py`.
- Rezultatai: `results.txt`. Pine v5: `pine/` (TradingView'e **nepaleista**).
- Pagal tavo kriterijus (PF > 1.3, DD < 25%, ≥ 30 sandorių) „РАБОТАЕТ“: **S3** (RSI-2 + 150 EMA) ir
  **S11** (ATR proveržis + FreeRoll) — bet PF apatinė riba (bootstrap 5%) < 1 abiem, o BTC buy&hold
  tame pačiame lange davė +160%.
- Netestuota (neįgyvendinama pagal pateiktas taisykles): lentelės eil. 2, 4, 5, 6, 9, 10, 13, 14.
