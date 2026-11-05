# Sonic AI: įėjimų ir išėjimų atvirkštinė analizė (TYRINĖJAMOJI, ne testas)

Skriptas: `research_sonic_reverse.py` (+ bazinė linija inline). Duomenys: viešas sandorių sąrašas
(laikai konvertuoti į UTC), Evedex XAUTUSD 1 min atidarymo kainos (238 sandoriai 2026-04-07 → 10-30).
Kainų kelias — minutės atidarymai, todėl neapima judesių minutės viduje.

## Elgsena (po „viešo starto“, 473 sandoriai)
- Įėjimai daugiausia 03–13 UTC (pikas 05–07), ~1.4 sandorio per aktyvią dieną, medianinis tarpas
  15 val., 43 % tą pačią dieną kaip ankstesnis. Savaitės dienos tolygios.
- Laimėjimai: mediana +3.98 USD/oz (5–95 %: 0.98…8.91), laikymo mediana 8 min. Pralaimėjimai:
  mediana −1.99 (−4.49…−0.52), laikymo mediana **3 min**. Fiksuoto TP/SL klasterių nėra.
- Prieš įėjimą kaina judėjo PRIEŠ sandorio kryptį: 5 min −0.98 USD (t −3.7), 30 min −1.91 (t −3.0)
  → įėjimai prieš trumpą judesį (pirkti po kritimo, parduoti po kilimo).
- Po įėjimo (sandorio kryptimi): +1 min +0.79 (64 % teigiamų), +3 min +2.20 (82 %), +5 min +2.80
  (84 %), +10 min +3.24. Mediana MAE = 0.0; 72 % sandorių MAE ≥ −0.5 USD.

## Ar tai pakartojama iš kainos?
Bazinė linija (visos minutės 03–13 UTC, pirmadienis–penktadienis, 98 340 min.): kontrarinė taisyklė
„pirk po ≥ X USD kritimo per 5 min / parduok po kilimo“ (X = 0.5, 1, 2, 3):
vidutinis judesys po 1–10 min ≈ **−0.03…+0.01 USD, teigiamų 45–50 %** (atsitiktinė kryptis: tas pats).
Sonic tomis pačiomis sąlygomis: **+2.2 USD per 3 min, 82 % teigiamų.**

**Išvada:** Sonic įėjimų atrankoje yra informacijos, kurios nėra pastarojo kainos judesio ir paros
laiko požymiuose. Kainos+laiko taisyklė jos neatkartos (grynasis lūkestis ≈ 0 prieš kaštus).
Galimi šaltiniai (iš duomenų neatskiriami): (a) sandorių laikai/kainos užfiksuoti po fakto,
(b) tikra papildoma informacija (srautas, naujienos, brokerio kainų vėlavimas), (c) labai greitas
reagavimas, kurio pranašumas išnyksta per 3–5 min.

## Papildymas: dydis, dienos taisyklės, išėjimo forma (po starto, 476 sandoriai, x1)
Skaičiuota inline iš sandorių sąrašo (lotai, P&L) + Evedex XAUT minutės kelias.

- **Dydis:** 87 % sandorių lygiai 0.10 lot; 13 % 0.2–0.5. Didesni lotai turi TRUMPESNĮ stopą
  (pralaimėjimo vid. 1.0 pt prie ≥0.2 lot vs 2.3 pt prie <0.2) → rizika vienam sandoriui
  beveik pastovi (~$20–25 ≈ 0.02 % ekvity): lotas ≈ fiksuota rizika / stopo atstumas. Dideli lotai
  laimi dažniau (94 % vs 86 %). Po pralaimėjimo lotas NEDIDĖJA (0.118 vs 0.118); ankstesnis
  „didina po nuostolio“ radinys galiojo tik prieš viešą startą.
- **Dienos taisyklė:** sandorių per dieną 1: 131, 2: 89, 3: 37, 4: 10, 5+: 3; dienos P&L mediana
  $58 (5–95 %: $14…$138), pralaimėjimo dienų 7 iš 270. Po laimėjimo kitas sandoris tą pačią dieną
  tik 38 %, po pralaimėjimo **76 %** (vid. po ~2.8 val.). Kai dienos P&L > $100, tolesnės prekybos
  tikimybė 16 %; > $200 — beveik 0. Atitinka „prekiauti iki mažo dienos tikslo, po nuostolio bandyti
  vėl“.
- **Išėjimai:** pralaimėjimai uždaromi arti blogiausio taško (pts − MAE mediana −0.30), 75 % jų
  niekada nebuvo pliuse po 1 min. Laimėjimų efektyvumas (realizuota / maksimali nauda) mediana 0.84.
- **Valandos / dienos / pusės:** vid. P&L vienam sandoriui $32–41 visose valandų grupėse, visose
  savaitės dienose ir abiejose pusėse — pranašumas nepriklauso nuo sesijos. Specialaus naujienų
  laiko klasterio nėra (~21 % įėjimų ±2 min nuo :00/:30, atsitiktinai būtų 17 %).
