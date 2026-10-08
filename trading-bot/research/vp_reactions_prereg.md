# POC / VAH / VAL reakcijos — išankstinė registracija

Užrašyta PRIEŠ paleidžiant tyrimą. Nieko čia nekeičiama pamačius rezultatus;
jei norime kitokių variantų, jie yra NAUJAS tyrimas su nauja registracija
(ir griežtesniu slenksčiu).

## Duomenys
- Evedex 15m žvakės, visos tradable crypto perp poros (`get_liquid_pairs`),
  ~120 dienų atgal. Poros be pilnos istorijos naudojamos tiek, kiek turi.
- Profilis: **ankstesnės UTC dienos** 15m žvakės, `compute_volume_profile`
  (24 bin'ai, 70% value area) — tas pats, ką naudoja botas.
- Įvykiai ieškomi tik **kitą UTC dieną** (profilis fiksuotas, be look-ahead).
- Vienas įvykis kiekvieno tipo per porą per dieną (pirmas).

## Įvykiai (visi įėjimai — kitos žvakės open, po signalinės žvakės uždarymo)
1. **VAH_REJ (short):** žvakės high > VAH, close < VAH, ankstesnės close < VAH.
   SL = signalinės žvakės high. 
2. **VAL_REJ (long):** veidrodis: low < VAL, close > VAL, ankstesnės close > VAL.
   SL = signalinės žvakės low.
3. **VAH_ACC (long):** dvi iš eilės žvakės uždaro virš VAH (pirmai ankstesnė
   close <= VAH). Įėjimas po antros. SL = VAH.
4. **VAL_ACC (short):** veidrodis. SL = VAL.

## Tikslai (iš anksto, tik šie)
- REJ įvykiams: (a) **TP = POC** (pagrindinis), (b) TP = 2R.
- ACC įvykiams: TP = 2R (pagrindinis ir vienintelis).
- Iš viso **6 testai**.

## Taisyklės
- Min. stop 0.25% nuo įėjimo (siauresnis nei ~3× fee — netradable); eilutės
  su siauresniu stop'u atmetamos.
- Intra-candle: SL ir TP toje pačioje žvakėje = SL.
- Langas 96 žvakės (24h) nuo įėjimo; neišspręstas — mark-to-close pagal
  paskutinę žvakę, R = (close − entry)/risk. Įvykiai be pilno 96 žvakių lango
  (per šviežios) atmetami.
- Fee 0.08% notional round-trip (R vienetais: 0.0008 / stop%).

## Statistika ir sėkmės kriterijus
- Koreliacija: visos poros juda su BTC tą pačią dieną, todėl įvykiai nėra
  nepriklausomi. Vienetas — **diena**: kiekvienai dienai vidutinis net R per
  visus tos dienos įvykius, tada t-testas per dienas.
- Testas **praeina**, tik jei VISI trys:
  1. dienų-klasterizuotas net R vidurkis > 0 su **t > 2.6** (~99%, 6 testų
     pataisa);
  2. net R vidurkis > 0 **abiejose** laiko pusėse (pirma / antra pusė pagal
     datas);
  3. ≥ 100 įvykių ir ≥ 40 dienų.
- Jei praeina 0 iš 6 → POC/VA reakcijos šiuo pavidalu edge neturi; tyrimas
  baigtas, nė vieno varianto netaisome ant tų pačių duomenų.
- Jei praeina ≥1 → tik tada gyvas paper-stebėjimas (forward, naujų duomenų)
  prieš bet kokį realų kapitalą.
