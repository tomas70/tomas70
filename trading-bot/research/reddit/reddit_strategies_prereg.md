# Reddit strategijų lentelės patikra — išankstinė registracija

Užrašyta PRIEŠ skaičiuojant bet kokį rezultatą. Šaltinis: Google Doc „Анализ
торговых стратегий Reddit“ (15 strategijų, visų aiškumas ≥ 3).

**Šaltinio kokybės pastaba:** dokumentas atrodo sugeneruotas (pvz. pateikta
Google Sheets nuoroda su išgalvotu ID, Reddit ID, kurie neatitinka tikrų
formatų). Reddit įrašų patikrinti negaliu — testuojamos lentelėje užrašytos
taisyklės, ne tikrieji įrašai.

## Ką testuoju
Python backteris (TradingView Strategy Tester čia nepasiekiamas), Binance
spot duomenys. Pine Script v5 failai parašyti pagal tas pačias taisykles,
bet TradingView'e **nepaleisti** — rezultatai gali šiek tiek skirtis.

Testuojamos 7 strategijos: **S1, S3, S7, S8, S11, S12, S15** (numeracija pagal
lentelės eilutes: 1 Supply&Demand, 3 RSI-2, 7 QS-1, 8 QS-2, 11 ATR proveržis,
12 BB+200EMA, 15 8 EMA).

**Netestuojamos (negalima įgyvendinti tiksliai pagal pateiktas taisykles):**
2 (reikia 1m duomenų ir nepasiekiamo „B4 MACD/MADX“), 4 (akcijų sesijos
ORB + 5m istorija 3 m.), 5 (vizualinis ILS), 6 (akcijų ekrano atranka ir
vizualinis proveržis), 9 (nenurodyti įėjimo parametrai), 10 (Level 2 / tape),
13 (S/R lygiai neapibrėžti, 5 kontraktų valdymas), 14 (sesijos atidarymas ir
nenurodyti slenksčiai).

## Instrumentas ir laikotarpis
- Lentelė nurodo akcijas/indeksus (SPY, ES, META, NVDA…), kurių duomenų čia
  neturiu. Pagal tavo taisyklę: **BTCUSDT**; laiko intervalas — kaip lentelėje:
  S1 ir S7 → **1h**, kiti (S3, S8, S11, S12, S15) → **1d**.
- **Pagrindinis testas:** 2023-10-10 → 2026-04-10 (2.5 m.). **Hold-out:** 2026-04-10
  → 2026-10-10 (6 mėn., į pagrindinį testą neįeina). Kartu tai ~3 metai.
- Indikatoriai skaičiuojami nuo pilnos istorijos (warm-up), prekyba tik lange.

## Vykdymas
- Signalas pagal uždarytą žvakę → įėjimas **kitos žvakės open**; stop/TP
  intrabar; vienoje žvakėje ir SL, ir TP → SL (pesimistiškai); gap per stop →
  išėjimas open kaina.
- Komisija 0.1% už pusę; slippage 2 tick (BTCUSDT tick = 0.01 → 0.02 USDT)
  nepalankia kryptimi įėjimui ir išėjimui.
- Pradinis depozitas **$1000** kiekvienam langui; kompaundinimas.
- Rizika 1% kapitalo = stop atstumas; kiekis = rizika / stop atstumas, bet
  nominalas ≤ 100% kapitalo (be svertų). Strategijai be stop'o (S15) —
  fiksuotas dydis 100% kapitalo.
- Viena pozicija vienu metu; ilgalaikė žvakių pozicija langui baigiantis
  uždaroma paskutinio bar'o close.

## Operacionalizacijos ten, kur taisyklė neapibrėžta (nieko „negerinu“)
- **S1:** „paliečia 200 EMA“ = touch bar: low ≤ EMA ≤ high. „Stipri žvakė“ =
  korpusas ≥ 50% žvakės aukščio ir kryptis (long: close>open, close>EMA;
  short atvirkščiai). Prieš tai buvusi touch žvakė + 2 stiprios žvakės iš eilės;
  įėjimas po antros; SL = pirmos patvirtinimo žvakės low/high; TP = artimiausias
  ankstesnis swing high (long) / swing low (short), pivot 5/5; jei jo nėra —
  signalas praleidžiamas.
- **S3:** ATR(14); „po 1 d. nuostolyje“ = pirmo uždarymo metu, jei pozicija
  nuostolinga → išeiti; „po 6 d. pelne“ = pasiekus 6 dienų laikymą su pelnu →
  išeiti; „EMA kyla“ = EMA150[t] > EMA150[t−1]; tik long.
- **S7:** tik long (short nenurodytas); ADX(13), DI ilgis 13; EMA nuo close,
  SMA nuo low.
- **S8:** tik long; „GTC“ SL/TP intrabar; WMA100 uždarymo išėjimas — kitos
  žvakės open.
- **S11:** ATR(14); proveržis = stop-buy ties ankstesnio baro korpuso High
  × (1 + 0.25%) (iš „25–50 bps“ imu apatinę ribą); FreeRoll ties +1.5 ATR
  (SL → įėjimas); +3 ATR → SL = įėjimas + 1.5 ATR, toliau trailing =
  aukščiausias high − 1.5 ATR; pakeitimai galioja nuo kitos žvakės;
  ATR fiksuojamas signalo metu.
- **S12:** „EMA200 kyla“ = EMA200[t] > EMA200[t−1]; BB(20,2); „kerta apatinę
  juostą“ = low < apatinė juosta bet kuriame iš 3 ankstesnių barų; „bulių
  apsisukimo žvakė“ = close > open IR close > ankstesnio baro high; įėjimo kaina
  turi būti virš EMA200; SL = 1 ATR(14) (max 5% nuo įėjimo); TP = artimesnis iš
  1.5R ir viršutinės BB juostos; tik long.
- **S15:** „kylantis trendas“ = EMA8 > EMA20; įėjimas: close > EMA8; išėjimas:
  low ≤ ankstesnio baro EMA20 (kaina paliečia) — išėjimo kaina = tas EMA20 lygis
  (arba open, jei gap); be stop → fiksuotas dydis.

## Sprendimo kriterijai (tavo)
- **РАБОТАЕТ:** PF > 1.3 IR max DD < 25% IR sandorių ≥ 30 (pagrindiniame teste).
- **МАЛО ДАННЫХ:** sandorių < 30.
- **НЕ РАБОТАЕТ:** kitaip.
- **Overfit patikra** „РАБОТАЕТ“ strategijoms: hold-out; „развалилась“, jei
  hold-out PF < 1 arba grynasis rezultatas neigiamas. Hold-out dažnai turės
  < 30 sandorių — tai pažymima.
- Papildomai (informacijai): PF 5-as procentilis (bootstrap per sandorius).

## Atsargumas
7 strategijos = 7 testai; 30 sandorių ir PF > 1.3 yra silpnas slenkstis,
dalis „РАБОТАЕТ“ gali būti atsitiktinė. Strategijos sukurtos akcijoms, o
testuojamos BTC — tai kitas instrumentas.

---

## Priedas (parašytas prieš akcijų testą): akcijų duomenys per IB

Stooq nepasiekiamas (egress politika), todėl akcijų dienos žvakės paimtos per IB
jungtį: SPY, NVDA, SAP (NYSE ADR), po 1000 dienos žvakių (2022-10-14 → 2026-10-09),
failai `data/*_1d_ib.csv`. Kainos atrodo pakoreguotos splitams (NVDA didžiausias
dienos šuolis 24% — pelno ataskaita, ne splitas). 15 eilučių, kur high/low šiek tiek
neatitiko open/close (skirtingi duomenų šaltiniai), pataisyta: high = max(high, o, c),
low = min(low, o, c). Dividendai nekoreguojami.

- **Pagal lentelę nurodyti instrumentai (skaičiuojami kaip tikras testas):**
  S3 → SPY (lentelė: S&P 500/ES/SPY, daily); S8 → NVDA ir SAP (lentelė: NVDA, SAP, daily).
- **S7 (META/ORCL, 1H)** šioje dalyje netestuojama: 3 metų 1H istorija per IB per daug
  didelė (>5000 žvakių kiekvienam).
- **Informaciniai priedai (NE tikras testas):** S3, S8, S11, S12, S15 ant visų trijų
  akcijų, kad matytume, ar strategijos, sukurtos akcijoms, elgiasi kitaip nei BTC.
  Lentelė jų instrumentų nenurodo (S11, S12, S15 — „akcijų swing“ be tikro simbolio),
  todėl šie rezultatai verdiktui neįskaitomi.
- Tos pačios operacionalizacijos, langai (main 2023-10-10→2026-04-10, hold-out
  →2026-10-10), kaštai (0.1%, slippage 2 tick = $0.02), 1% rizika, $1000.
- Papildomai pateikiu buy&hold tame pačiame lange.
