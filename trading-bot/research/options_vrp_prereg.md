# BTC opcijų premijos (VRP) ir T50 filtras — išankstinė registracija

Užrašyta PRIEŠ atsisiunčiant IV istoriją ir skaičiuojant bet kokį rezultatą.
Taisyklės po rezultatų nekeičiamos.

## Klausimas
Ar BTC opcijų pardavimas turi teigiamą tikėtiną grąžą (variance risk premium,
VRP), ir ar T50 trendo būsena (BTC dienos uždarymas virš / po SMA50) atskiria
geresnį laiką parduoti nuo blogesnio?

## Duomenys
- **IV:** Deribit DVOL (BTC 30 d. implied vol indeksas), dienos uždarymas,
  nuo ~2021-03 iki dabar. Šaltinis: viešas API
  (`/api/v2/public/get_volatility_index_data`, domenas `www.deribit.com`).
- **Kaina / realizuotas vol / T50:** Binance BTCUSDT dienos uždarymai
  (jau turimi). T50 būsena žinoma dienos t uždarymu.
- Dabartinė būsena (IBIT, per IB jungtį): IV 35.7%, HV30 36.3%, IV
  procentilis 52% (13 sav.) / 19% (52 sav.) — informacinė, netestuojama.

## Apibrėžimai (visi metiniai, dienos t uždarymu)
- IV_t = DVOL_t.
- RV_t = std(log grąžų t+1…t+30) × √365.
- VRP_t = IV_t − RV_t (vol punktais).
- Pardavimo P&L proxy (BS, r=0, ATM, 30 d. iki išpirkimo, laikoma iki galo,
  IV = DVOL_t, plokščia šypsena, **skew ignoruojamas**), % nuo S_t:
  - **Straddle (pagrindinis):** premija = 0.7979 × IV × √(30/365);
    išmokėjimas = |S_{t+30}/S_t − 1|.
  - **Cash-secured put ATM:** premija = BS put (K=S_t); išmokėjimas =
    max(1 − S_{t+30}/S_t, 0).
- Kaštai: 3% nuo premijos (spread + komisinės).

## Imtys
- **VRP (H1):** visos dienos su pilnu 30 d. langu (persidengiantys langai) —
  Newey-West t (lag 30).
- **P&L (H2, H3):** persidengiančios dienos su Newey-West t (lag 30); papildomai
  ne persidengiantys 30 d. blokai kaip patikra (n ≈ 60 — žemo galingumo,
  atvirai pažymima).

## Hipotezės ir sėkmės kriterijai
- **H1:** vidutinis VRP > 0, NW t > 3.0.
- **H2:** vidutinis straddle net P&L > 0, NW t > 3.0, IR teigiamas bent 4 iš
  ~6 kalendorinių metų.
- **H3 (T50 filtras):** put'o net P&L (T50=LONG) − P&L (T50=FLAT) > 0,
  NW t > 2.5; plius aprašomieji: 5-as procentilis ir blogiausias rezultatas
  kiekvienoje būsenoje.
Pagrindinis sprendimas: pasiūlymas „parduok premiją BTC“ turi praeiti H1 ir H2.
H3 tik tada sprendžia, ar naudoti T50 kaip filtrą.

## Aprašomieji (ne sprendimui)
- Kaštų jautrumas (0% / 3% / 10% premijos).
- VRP pagal metus ir pagal IV procentilį (ar didesnė IV → didesnė VRP).
- Kuo sąlygiškai skiriasi IBIT IV nuo DVOL (tik dabartinis snapshot).

## Apribojimai (iš anksto pripažįstami)
- ~5 metai, ~60 nepriklausomų mėnesių: statistinis galingumas mažas, rezultatai
  stipriai priklauso nuo 2021–22 krizių.
- Skew ignoruojamas (tikros OTM put premijos didesnės, vertinimas gali būti
  konservatyvus put'ams).
- Tai BTC (Deribit) proxy, ne tiesiogiai IBIT opcionai (kitas likvidumas,
  JAV sesija, ex-dividend nėra).
- Nėra tikrų istorinių bid/ask.

## Sprendimas
- H1 ir H2 praeina → VRP yra; toliau tik forward stebėjimas pagal IB duomenis
  (IV vs HV, T50) prieš bet kokį realų sandorį.
- Nepraeina → BTC volatilumo pardavimas edge neįrodo; opcijų kryptį
  baigiame.
