# 4H bias + 1h pullback, platus ATR stop, dydis pagal signalo stiprumą — išankstinė registracija

Užrašyta PRIEŠ skaičiuojant bet kokias grąžas (1h duomenys dar neatsisiųsti).
Hipotezė paimta iš viešai matyto indikatoriaus ELGESIO (bias filtras, 3–5%
stop, dydis pagal score), ne iš jo kodo ir ne iš duomenų. Parametrai
fiksuoti; po rezultatų nekeičiami. Kiti variantai = naujas tyrimas su
griežtesniu slenksčiu.

## Duomenys
Binance spot BTCUSDT ir ETHUSDT, 1h žvakės, 2017-08 → dabar
(`data-api.binance.vision`). Visi signalai naudoja tik UŽBAIGTAS žvakes.

## Bias (4H)
4H žvakės = 1h agregatas UTC lentelėje (00,04,08,...). Bias žinomas tik po
4H žvakės uždarymo.
- LONG bias: 4H close > 4H EMA(50) (span 50, adjust=False).
- SHORT bias: 4H close < 4H EMA(50).

## Įėjimo trigeris (1h)
Pullback-resume, tik bias kryptimi, 1h uždarymu:
- LONG: bias LONG IR 1h close > 1h EMA(20) IR ankstesnė 1h close ≤ ankstesnė
  1h EMA(20) (kryžminimas aukštyn).
- SHORT: veidrodis.
Įėjimas: **kitos 1h žvakės open**. Viena pozicija per turtą vienu metu.
Pyramidavimo („Add“) nėra.

## Stop
Stop atstumas = 2.5 × 4H ATR(14) (paprastas rolling mean of true range),
išreikštas % nuo įėjimo kainos, **apribotas [3%, 5%]**. Stop kaina = įėjimas
∓ tas atstumas. 1R = stop atstumas.

## Išėjimo variantai (tik šie 2)
- **E1:** TP = 2R; kitaip stop; maks. laikymas 14 d. (336 žvakės), tada
  mark-to-close.
- **E2:** be TP; išeiti kitos 1h žvakės open, kai (po 1h uždarymo) 4H bias
  apsiverčia; stop lieka; maks. laikymas 14 d.
Tos pačios žvakės SL ir TP / SL ir bias → SL (pesimistiškai). Stop tikrinamas
nuo įėjimo žvakės imtinai.

## Signalo stiprumas ir dydis (score pakaitalas)
- dist = |4H close − 4H EMA(50)| / 4H ATR(14)
- D1 sutampa: paskutinis užbaigtas 1d close virš 1d SMA(50) (LONG) /
  žemiau (SHORT).
- Tier A: dist ≥ 1 IR D1 sutampa → dydis 1.0 R
- Tier B: tiksliai viena sąlyga → 0.5 R
- Tier C: nei viena → 0.25 R
Visi signalai imami; tier keičia tik dydį.

## Kaštai
0.10% už pusę (fee + slippage) ir 10%/m. funding/laikymo pakaitalas nuo
notional per laikymo laiką. R vienetais: cost_R = (0.002 +
valandos × 0.10/8760) / stop_pct.

## Laikotarpiai
P1 2018-01→2020-12, P2 2021-01→2022-12, P3 2023→dabar.

## Testai (4)
BTC-E1, BTC-E2, ETH-E1, ETH-E2.

## Sėkmės kriterijai (kiekvienam testui, visi kartu)
1. ≥ 300 sandorių;
2. net vidutinis R/sandorį > 0 su **t > 3.0**;
3. net vidurkis > 0 bent 2 iš 3 laikotarpių IR P3 > 0;
4. dydžiu pasvertos grąžos (rizika 1% equity už 1R, svoris pagal tier)
   net metinis Sharpe > 0.8 (dienos grąžos pagal išėjimo dieną).

## Aprašomieji (ne sprendimui)
- Vidutinis R pagal Tier A/B/C (ar score iš tikrųjų atskiria geresnius).
- Pasvertas Sharpe vs vienodo dydžio (ar score dydis ką nors prideda).
- Kontrolė: tie patys stop/išėjimai, bet įėjimas pirmą 1h žvakę po bias
  nustatymo be pullback trigerio (ar trigeris kažką prideda).
- BTC buy&hold Sharpe tuo pačiu laikotarpiu.

## Sprendimas
- 0 praėjo → ši indikatoriaus idėja šiame formate edge neturi; nieko
  netaisome ant tų pačių duomenų.
- ≥ 1 praėjo → forward paper stebėjimas prieš bet kokį kapitalą.
