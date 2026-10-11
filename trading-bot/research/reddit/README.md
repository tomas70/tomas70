# Reddit strategijų lentelės patikra (BTCUSDT)

- Registracija: `reddit_strategies_prereg.md` (parašyta prieš testą).
- Kodas: `../../research_reddit_strats.py`; testai: `../../tests/test_reddit_strats.py`.
- Rezultatai: `results.txt`. Pine v5: `pine/` (TradingView'e **nepaleista**).
- Pagal tavo kriterijus (PF > 1.3, DD < 25%, ≥ 30 sandorių) „РАБОТАЕТ“: **S3** (RSI-2 + 150 EMA) ir
  **S11** (ATR proveržis + FreeRoll) — bet PF apatinė riba (bootstrap 5%) < 1 abiem, o BTC buy&hold
  tame pačiame lange davė +160%.
- Netestuota (neįgyvendinama pagal pateiktas taisykles): lentelės eil. 2, 4, 5, 6, 9, 10, 13, 14.

## Akcijos (IB dienos duomenys)
- `results_stocks.txt`, `data/*_1d_ib.csv`, kodas `../../research_reddit_stocks.py`.
- Pagal lentelę nurodyti instrumentai: S3/SPY „РАБОТАЕТ“ (PF 1.63, 45 sand., DD 3.4%, +5.9%), bet hold-out PF 0.93
  (13 sand.) → „развалилась“, PF 5% < 1; S8/NVDA ir S8/SAP — МАЛО ДАННЫХ (26 ir 9 sand.).
- S7 (META/ORCL 1H) netestuota — per didelė 3 m. 1H istorija per IB.
- Buy&hold pagrindiniame lange: SPY +57%, NVDA +306%, SAP +25%.
