# Dienos faktorių paieška — išankstinė registracija

Užrašyta PRIEŠ paleidžiant. Hipotezės pasirinktos iš literatūroje aprašytų
kriptovaliutų anomalijų (trend following, cross-sectional momentum, trumpo
horizonto reversal), NE iš šių duomenų. Parametrai fiksuoti dabar; po
rezultatų nekeičiami. Jei nieko nepraeina — tyrimas baigtas, daugiau
variantų ant tų pačių duomenų netikrinama.

## Duomenys
- Evedex 1d žvakės (UTC), uždarymo kainos.
- BTC: nuo 2019-09. Alt'ai: nuo 2025-03 (≈19 mėn.). Universas 2 tyrimui —
  visos poros, kurių pirma 1d žvakė ≤ 2025-03-05 (31 moneta su BTC).
- Žinomi apribojimai: survivorship (tik dabar listinamos monetos), funding
  nemodeliuojamas, trumpinimo kaina (borrow) nemodeliuojama.

## Kaštai
0.04% notional už pusę sandorio (0.08% round-trip), taikoma pozicijos
pokyčiui / apyvartai.

## Tyrimas 1 — BTC trend following (long / flat), 4 variantai
Signalas dienos uždarymu, pozicija kitos dienos grąžai.
- T50, T100, T200: long jei close > SMA(n), kitaip grynieji.
- D55: long, kai close > aukščiausio per ankstesnes 55 d. (be šiandienos);
  išeiti, kai close < žemiausio per ankstesnes 20 d.
Palyginimas: BTC buy&hold. Laikotarpis dalinamas: IS 2019-09 → 2022-12,
OOS 2023-01 → dabar.
**Praeina**, jei: (a) net metinis Sharpe > B&H Sharpe **abiejuose** periodo
dalyse; (b) Sharpe > 0.8 abiejose dalyse; (c) max drawdown OOS mažesnis nei
B&H OOS. (Sharpe skirtumo reikšmingumas silpnas — tai atvirai pažymima.)

## Tyrimas 2 — Cross-sectional momentum / reversal (alt'ų krepšelis)
- Perbalansavimas: kiekvieno sekmadienio uždarymu; laikymas 7 d.
- Rangas pagal L dienų grąžą, L ∈ {7, 14, 28}.
- Top 6 ir bottom 6 monetos; po 1/6 svorio kiekvienoje pusėje.
- MOM: long top, short bottom. REV: atvirkščiai.
- Testai: MOM7, MOM14, MOM28, REV7, REV14, REV28.
- Papildomas testas 7: REV1 — rangas pagal 1d grąžą, perbalansavimas kasdien,
  laikymas 1 d., long bottom 6 / short top 6.
- Apyvarta: Σ|w_new − w_old| × 0.04%.
**Praeina**, jei VISI:
  1. savaitinių (REV1 — dienos) net grąžų vidurkis > 0 su **t > 2.7**
     (~13 testų pataisa visame tyrime);
  2. vidurkis > 0 **abiejose** laiko pusėse;
  3. net metinis Sharpe > 1.0;
  4. ≥ 40 periodų.

## Sprendimas
- Praeina 0 iš 11 → neradome; nieko daugiau ant šių duomenų.
- Praeina ≥ 1 → tik forward paper-stebėjimas naujais duomenimis prieš bet
  kokį kapitalą. Statistinis galingumas 19 mėn. imčiai mažas: nepraėjimas
  nereiškia, kad edge neegzistuoja — reiškia, kad čia jo neįrodome.
