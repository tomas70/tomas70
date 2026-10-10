# ETH DVOL: „parduok premiją tik kai IV aukštas“ — išankstinė registracija

Užrašyta PRIEŠ atsisiunčiant ETH DVOL ir skaičiuojant bet kokį ETH rezultatą.
Tai pakartojimas pirmojo BTC tyrimo (`options_vrp_prereg.md`), kuriame
BTC pagal IV terciles po fakto atrodė: žemas IV → straddle -2.3%, aukštas
IV → +4.5%/30 d. Šis stebėjimas buvo aprašomasis, ne registruotas — todėl
čia tikrinamas ant kito instrumento.

## Svarbus apribojimas (pripažįstamas iš anksto)
ETH DVOL **nėra nepriklausoma imtis**: tas pats kalendorius (2021–2026) ir
aukšta ETH/BTC koreliacija, vol režimai sutampa. Tai nepriklausomumas tik
pagal instrumentą (kita kaina, kita IV, kitas tail). Teigiamas rezultatas
būtų silpnesnis įrodymas nei tikrai skirtinga imtis; neigiamas — stiprus
prieš idėją.

## Duomenys
- ETH DVOL (Deribit, `currency=ETH`, 1D), ETHUSDT dienos uždarymai (Binance).
- Visi apibrėžimai, P&L proxy, kaštai (3% premijos) ir NW statistika —
  identiški pirmam tyrimui (`research_options_vrp.py`).

## Taisyklė (užšaldyta; nenaudoja ETH rezultatų)
- **IV percentilis (point-in-time):** dienos t DVOL uždarymo dalis tarp
  paskutinių 365 dienų (įskaitant t), kuriose DVOL ≤ DVOL_t. Be look-ahead.
- **Aukštas IV:** percentilis ≥ 0.667. **Žemas IV:** percentilis < 0.333.
- Tai atitinka IB rodomą „IV percentile 52 savaičių“, todėl taisyklė
  pritaikoma gyvai.
- Pirmos 365 dienos tik percentiliui formuoti (nėra prekybos).

## Hipotezės ir sėkmės kriterijai (ETH)
- **H4a:** aukšto IV dienomis straddle pardavimo net P&L vidurkis > 0,
  NW t > 2.5, IR teigiamas bent 3 iš metų, turinčių ≥ 60 aukšto IV dienų.
- **H4b:** (aukštas − žemas IV) straddle P&L skirtumas > 0, NW t > 2.5.
- **Praeina,** jei H4a IR H4b.

## Aprašomieji (ne sprendimui)
- Tas pats BTC su point-in-time percentiliu (ar pirmojo tyrimo
  „tercilių“ rezultatas išlieka be look-ahead).
- Vidurinė tercilė; ATM put pardavimo P&L pagal IV būseną; kaštų jautrumas
  0/3/10%; ne persidengiantys 30 d. blokai (n, t); 5 procentilis ir blogiausias
  rezultatas aukšto IV dienomis; ETH unconditional VRP (H1 replikacija).
- Kiek aukšto IV dienų/epizodų yra (klasterizacija: tai nedaug nepriklausomų
  epizodų).

## Sprendimas
- Praeina → forward stebėjimas pagal IB IV percentilį (≥ 67%) prieš bet kokį
  realų sandorį; taisyklė netaisoma.
- Nepraeina → „parduok premiją tik kai IV aukštas“ netvirtinama; kripto
  opcijų kryptį baigiame.
