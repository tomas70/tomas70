# TREND-3: BTC + ETH + auksas, trend filtras ir atvirkštinė volatilumo svoris — registracija

Užrašyta PRIEŠ atsisiunčiant PAXG dienos kainas ir PRIEŠ skaičiuojant bet kokį portfelio rezultatą.
Tai ne naujas „signalas“, o iš projekto rezultatų sudėliotas kandidatas.

## Kodėl būtent tai (pamokos iš visų ankstesnių rezultatų)
1. **Kaštai nugali trumpą prekybą** (Evedex ~9 bps round trip; Sonic, intraday auksas, SWEEP — viskas
   žlugo). Lieka mažo apyvartumo taisyklės.
2. **Vienintelis išlikęs kandidatas — BTC T50** (≈0.3 Sharpe virš B&H po korekcijų): pranašumas — kritimų
   išvengimas. ETH ir top-10 krepšelis tą patį palaikė kryptimi, bet statistiškai ne (P 0.078 / 0.063).
3. **Diversifikacija yra vienintelė „nemokama“ idėja**: auksas (XAUT jau yra Evedex) yra silpnai
   koreliuotas su kriptovaliutomis; trendo filtras jam netestuotas. Atvirkštinis volatilumo svoris
   apsaugo, kad aukso mažas vol neišnyktų portfelyje.
4. **Funding yra paslėpta kaina**: BTC funding T50 LONG dienomis ≈ +14 %/m., FLAT dienomis ≈ −0.5 %/m.
   (`funding_carry_results.txt`). Jei T50 įgyvendinamas perp'u (Evedex), ilgos pozicijos MOKA funding.
   Dabartinis paper trackeris to neįskaito. Čia įskaitoma.

## Strategija (užšaldyta)
- Turtai: BTCUSDT, ETHUSDT, PAXGUSDT (Binance spot dienos close). Langas: nuo PAXG pradžios
  (2020-08-28) + 60 d. šildymas → paskutinė pilna diena.
- Signalas kiekvienam turtui: **close > SMA(50) → ilgas, kitaip grynieji** (kaip BTC T50; nekeičiama).
- Svoris: w_i = signal_i · (1/σ_i) / Σ_j(1/σ_j), j = visi 3 turtai (ne tik signalą turintys). σ_i = 60 d.
  dienos grąžų st. nuokrypis (iki t imtinai) · √365. Todėl suma ≤ 1 (be svertų); kai signalų nėra — grynieji.
- Svoriai perskaičiuojami kasdien t close, taikomi t→t+1 grąžai.
- Kaštai: **0.10 % per pusę nuo |Δw|**.
- **Funding (perp įgyvendinimas, PIRMINIS režimas):** BTC ir ETH ilgos pozicijos moka dienos Deribit
  funding F_d (kai F_d > 0; kai < 0, gauna), proporcingai svoriui. Aukso kojai funding pirminiame
  režime = 0 (nežinomas; XAUT Evedex funding neįvertintas). **Spot režimas** (funding = 0) — aprašomasis.

## Palyginimai (tame pačiame lange)
- **IV-B&H:** tie patys inverse-vol svoriai, bet visada ilgas (be signalo), tie patys kaštai.
- **EW-B&H:** 1/3 kiekvienam, mėnesio rebalansas.
- **BTC T50:** tik BTC pagal T50 (spot/perp pagal tą patį režimą).

## Kriterijai (PIRMINIS režimas; visi privalo būti išpildyti)
1. Sharpe(TREND-3) > Sharpe(IV-B&H) ir 30 d. blokų bootstrap (10 000, seed 1) vienpusė P(skirt.≤0) ≤ 0.05.
2. Sharpe(TREND-3) ≥ Sharpe(BTC T50) tame pačiame lange (diversifikacija prideda vertės).
3. Max DD(TREND-3) < Max DD(IV-B&H) IR < 30 %.
4. Sharpe(TREND-3) > Sharpe(IV-B&H) bent 2 iš 3 chronologinių trečdalių.
5. Kaštų stresas: 0.25 % per pusę — Sharpe vis dar > Sharpe(IV-B&H).
6. Aukso funding stresas: aukso kojai 5 %/m. kaina, kai ilga — kriterijus 1 išlieka.

## Sąžiningumo pastabos
- **Mano lūkestis:** 2–4 kriterijai greičiausiai praeis (aukso trendas 2020–2026 buvo stiprus), bet
  ~6 metų imtis maža, o BTC/ETH dalis jau žinoma (neneprilausoma). Gali būti per optimistiška aukso
  atžvilgiu: auksas 2020-08 → 2026-10 kilo nuo ~1 900 iki ~4 000 su dideliu trendu.
- PAXG ≠ spot auksas (koreliacija su IB XAUUSD buvo 0.995 dienos grąžoms).
- Praėjimas = „verta forward paper testo“, ne prekybos signalas. Forward: paper tracker išplėstų.

## Ko nedarau
- Neieškau SMA ilgio, vol lango, turtų rinkinio ar svorio schemos po rezultato.
- Nepridedu kitų turtų (SOL, BNB ir t.t.) po rezultato.
