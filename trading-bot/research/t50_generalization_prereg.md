# T50 apibendrinimas: ETH ir top-10 krepšelis — išankstinė registracija

Užrašyta PRIEŠ skaičiuojant bet kokią T50 statistiką ETH ar krepšeliui. Šaltinis: nepriklausomo
audito (`audit_2026-10-13_slice3_crosscutting.md`) 1 rekomendacija.

## Kodėl
- BTC T50 yra vienintelis išlikęs kandidatas, bet po forking-paths korekcijos jo pranašumas
  silpnas (~0.3 Sharpe virš B&H, ~65–85 % tikimybė, 84 % pranašumo iš 3 mėnesių, nuo 2023 nereikšmingas).
- BTC jau išnaudotas: daugiau BTC testų informacijos nepadeda. **Vienintelis „švarus“ patikrinimas —
  ta pati užšaldyta taisyklė kituose instrumentuose.**

## Sąžiningumo pastabos (prieš testą)
- **T50 nebuvo skaičiuotas nei ETH, nei krepšeliui** — jokios strategijos statistikos (Sharpe,
  DD, alfa) šiems instrumentams neturiu. ETH kainos projekte buvo naudotos kituose tyrimuose
  (1h pullback, alt momentum universas, DVOL), todėl žinau, kad ETH 2017–2026 kilo ir krito
  panašiai kaip BTC; tai ne T50 rezultatas, bet koreliacija (~0.8) reiškia, kad **ETH nėra
  nepriklausomas** nuo BTC (tos pačios krizės). Patvirtinimas silpnesnis nei dviejų nesusijusių
  turtų.
- Auditoriaus galios įvertis: jei ETH elgiasi kaip BTC, ETH testas turi ~0.8 galią (nepatikrinta
  mano; naudoju kaip orientyrą, ne kriterijų).
- 2 testai (ETH, krepšelis) — krepšeliui griežtesnis slenkstis.
- Taisyklė **užšaldyta**: nekeičiu SMA ilgio (50), neieškau 20–200 šeimos, nederinu kaštų.

## Taisyklė (užšaldyta)
Long, kai dienos close > SMA(50) (SMA įskaitant šios dienos close), kitu atveju grynieji.
Pozicija, nustatyta t dienos close, taikoma t→t+1 grąžai. Kaštai **0.10 % per pusę** nuo pozicijos
pokyčio (kaip `research_multi_cycle.py`: `strat_returns`, `SIDE_COST`). SMA šildymas — nuo pilnos
istorijos.

## Testas 1 (PIRMINIS): ETH
- Duomenys: Binance spot ETHUSDT dienos close, nuo listingo (2017-08-17) iki paskutinės pilnos
  dienos esamame cache faile `binance_cache/ETHUSDT.pkl` (nesikeičia vykdant).
- Palyginimas: ETH buy&hold tuose pačiuose dienose.

### Kriterijai (visi privalo būti išpildyti)
1. **Sharpe(T50) > Sharpe(B&H)** ir 30 d. blokų bootstrap (bendras, ta pati blokų seka abiem
   serijoms, 10 000 resample'ų, seed 1) vienpusė **P(skirtumas ≤ 0) ≤ 0.05**.
2. **Beta-suderinta alfa:** regresija T50 dienos grąža ~ ETH B&H dienos grąža; alfa (metinė)
   > 0 ir Newey-West (lag 5) vienpusė **t > 2.0**.
3. **Max DD(T50) < Max DD(B&H).**
4. **Sharpe(T50) > Sharpe(B&H) bent 2 iš 3 laikotarpių:** P1 2018-01→2020-12, P2 2021-01→2022-12,
   P3 2023-01→pabaiga (tie patys kaip multi-cycle).
5. **Kaštų stresas:** su 0.25 % per pusę Sharpe(T50) vis dar > Sharpe(B&H).

### Verdiktų žemėlapis (iš anksto)
- **Visi 1–5:** T50 apibendrinasi — antras turtas patvirtina. Pasitikėjimas BTC rezultatu
  didėja, bet (dėl koreliacijos) ne iki „nepriklausomai patvirtinta“. Atskiras sprendimas, ar
  pridėti ETH T50 į paper trackerį.
- **1 nepraeina, bet Sharpe skirtumas > 0 ir P ≤ 0.20:** neutrali, nepakankamai galinga.
- **Sharpe skirtumas ≤ 0:** BTC T50 rezultatas silpnėja stipriai (BTC specifika arba imties laimė);
  paper trackeris lieka tik kaip įgyvendinimo patikra, ne kaip tikėtinas edge.

## Testas 2 (ANTRINIS): point-in-time top-10 krepšelis
- Universas kiekvieną mėnesio pirmą UTC dieną (naudojant duomenis iki praėjusios dienos close):
  **top-10 pagal 30 d. vidutinį quote volume** tarp Binance USDT spot porų su ≥ 90 d. istorijos
  ir kaina t dieną; BTC ir ETH įtraukti. Išskiriami: `STABLES` iš `research_binance_data.py` +
  **papildomi stablai U, RLUSD, USDS, USDSOLD, USDSB, BKRW, KGST** + leveraged tokenai
  (galūnė UP/DOWN kai ilgis > 4, **ir tikslūs BULL, BEAR**) — auditoriaus rastų universe klaidų
  ištaisymas. Lygiagrečiai universe sudaro delistuotos monetos (point-in-time).
- Svoriai: 10 slotų po 10 %; kiekvienas slotas laikomas, jei monetos close > jos SMA(50),
  kitaip grynieji (dalis krepšelio gali būti grynuosiuose).
- Delistingas/duomenų pabaiga mėnesio viduryje: slotas uždaromas paskutiniu close, likusi
  mėnesio dalis grynieji.
- Palyginimas: **tas pats universas, mėnesio rebalansas, visada long** (B&H krepšelis).
- Kaštai: 0.10 % per pusę nuo pozicijos pokyčio (T50 flip'ai) ir nuo svorio pokyčio
  rebalansuojant.
- Kriterijai S1–S5 = 1–5 kriterijai viršuje, **tik bootstrap slenkstis griežtesnis: P ≤ 0.025.**
  P1/P2/P3 laikotarpiams reikia ≥ 10 monetų universe, kitaip diena praleidžiama.

Verdikto žemėlapis kaip viršuje. Kadangi krepšelio B&H apima daug monetų, kurios beveik
visos krito 2018 ir 2022 m., **dalis „pranašumo“ gali būti paprastas bear-market išvengimas,
o ne selektyvus signalas** — tai būtų pats BTC pranašumo mechanizmas, ir tai aprašysiu, nepaversdamas
„naujo edge“.

## Aprašomieji (verdikto nekeičia)
- Sharpe, metinė grąža, DD, dienų dalis long, apsisukimų skaičius per metus.
- Kaip pasiskirsto pranašumas pagal mėnesius (kiek % iš top-3 mėnesių).
- ETH T50 su ilgiu 100 ir 200 **tik rodomi be verdikto** (patikrinimas, kad rezultatas nėra
  vienos SMA ilgio laimė; taisyklė lieka 50).
- Koreliacija tarp BTC T50 ir ETH T50 dienos grąžų.

## Ko nedarau
- Nekeičiu SMA ilgio, kaštų ar universo taisyklių po pirmo rezultato.
- Neieškau papildomų filtrų (volume, volatility), neįtraukiu naujų monetų grupių.
- Jei ETH nepraeina — neieškau „tinkamo“ ETH variantų.
