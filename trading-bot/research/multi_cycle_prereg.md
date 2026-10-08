# Daugiaciklis tyrimas (Binance spot, 2017–2026) — išankstinė registracija

Užrašyta PRIEŠ skaičiuojant bet kokias grąžas. Duomenų atsisiuntimas
(`research_binance_data.py`) jau vyko, bet nei kainų, nei grąžų statistikos
neperžiūrėta. Po rezultatų nieko nekeičiame; kiti variantai = naujas tyrimas
su griežtesniu slenksčiu.

## Kodėl šis tyrimas
Ankstesnis (Evedex, 19 mėn.) buvo vienas rinkos režimas ir tik šiandien
gyvos monetos. Čia: ~8 metai, keli ciklai (2018 meškos, 2020–21 bulius,
2022 krizė, 2023–26), ir universas **point-in-time su išlistintomis
monetomis** (LUNA, FTT, SRM ir t.t.), kad survivorship bias neiškreiptų.

## Duomenys
- Binance spot USDT poros per `data-api.binance.vision`, 1d uždarymas ir
  USDT quote volume. Įtraukti ir išlistinti simboliai (status BREAK).
- Atmesta pagal taisyklę `tradable_universe` (stablecoin'ai, fiat, wrapped,
  leveraged tokenai UP/DOWN/BULL/BEAR).

## Universas kiekvieną perbalansavimo dieną t (point-in-time)
- Tinkama moneta: ≥ 90 dienų istorijos iki t IR yra kaina dienai t.
- Universas = 30 tinkamų monetų su didžiausiu 30 d. vidutiniu USDT quote
  volume iki t (imtinai).
- Prekiauti pradedama, kai tinkamų ≥ 18.
- Jei laikoma moneta išnyksta iš duomenų (delist), jos grąža laikotarpiui =
  paskutinė kaina / įėjimo kaina.

## Tyrimas A — cross-sectional momentum / reversal
- Perbalansavimas: kiekvieno sekmadienio dienos uždarymu, laikymas 7 d.
- Rangas pagal L dienų grąžą, L ∈ {7, 14, 28, 56}.
- Long top 6, short bottom 6, po 1/6 svorio (kiekviena pusė = 100% equity).
- Testai: MOM7, MOM14, MOM28, MOM56. REV = tas pats su atvirkštiniu ženklu
  (ne atskiras testas; ženklas rodo kryptį). Iš viso **4 dvipusiai testai**.
- Kaštai (pagrindiniai, nuo jų priklauso išvada): **0.10% už pusę** sandorio
  (0.04% fee + 0.06% slippage alt'ams) pagal apyvartą + **10% metinių**
  šorto kojos kaina (borrow/funding pakaitalas) = 0.192%/savaitę už short koją.
  Funding istorijos neturime, todėl tai konservatyvi prielaida.
- Jautrumas (informatyvus, NE sprendimui): 0.04%/pusę be šorto kaštų.

## Tyrimas B — BTC trend following (long/flat), BTCUSDT 2017-08 →
- T50, T100, T200 (close > SMA(n)) ir D55/20 Donchian (kaip anksčiau).
- Kaštai 0.10% už pusę.
- Palyginimas: BTC buy&hold.

## Laikotarpiai (režimai) — tos pačios ribos A ir B
- P1: 2018-01 → 2020-12; P2: 2021-01 → 2022-12; P3: 2023-01 → dabar.
  (A pradeda, kai universas pakankamas; P1 gali būti trumpesnis.)

## Sėkmės kriterijai (visi turi būti įvykdyti)
**A (kiekvienam iš 4 testų, tinkama kryptis = vidurkio ženklas):**
1. |t| > 2.7 savaitinių net grąžų vidurkiui (≈ 4 testų pataisa ir dar atsargos);
2. net vidurkis ta kryptimi teigiamas **bent 2 iš 3** laikotarpių, ir
   **P3 teigiamas**;
3. net metinis Sharpe > 0.8 visoje imtyje;
4. ≥ 200 savaitinių periodų.

**B (kiekvienam variantui):**
1. Sharpe(strategija) > Sharpe(B&H) **bent 2 iš 3** laikotarpių;
2. Sharpe visoje imtyje > 0.8;
3. max drawdown visoje imtyje mažesnis nei B&H.

## Papildomi (aprašomieji, ne sprendimui)
- Evedex 2025–26 langas vs Binance tas pats langas (ar rezultatai sutampa).
- Dalis grąžos iš top 10 didžiausių savaičių (ar viską neša keli įvykiai).

## Sprendimas
- 0 praėjo → daugiaciklio tyrimo prasme edge neįrodytas; paper tracker
  nuimamas arba paliekamas tik kaip BTC beta stebėjimas.
- ≥ 1 praėjo → paper tracker tęsiamas su ta pačia taisykle; realus kapitalas
  tik po forward patvirtinimo ir tik po realių kaštų (funding, min. dydžiai).
