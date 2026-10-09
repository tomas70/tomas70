# Telegram signalų grupės vertinimas — išankstinė registracija

Užrašyta PRIEŠ skaičiuojant bet kokį rezultatą. Jau padaryta tik techninė
dalis: eksporto parsinimas, žvakių atsisiuntimas ir laiko juostos nustatymas
(eksporto laikas = UTC+3, patikrinta pagal tai, ar signalo zona sutampa su
tuo metu buvusia žvake: 99.3% sutapimas). Jokių grąžų ar TP/SL rezultatų
dar neskaičiuota. Taisyklės po rezultatų nekeičiamos.

## Duomenys
- Eksportuota grupė „Закрытая Трейдинг Группа | Томас Кралов“,
  2026-05-03 → 2026-10-09: 3 435 signalai (po 5 TP ir SL), 8 281
  atnaujinimas (TP pasiekimai, uždarymai).
- Kainos: Evedex 15m žvakės pagal instrumentą. Signalai be žvakių
  (BABYDOGE, AVNT, MON) atmetami ir suskaičiuojami.
- Signalo laikas T = eksporto laikas − 3 h. Signalas generuojamas uždarius
  15m žvakę; **pirmas galimas veiksmas = kitos žvakės (žyma T) open**.

## Signalo šablonas (pastebėtas, ne vertinimo dalis)
Zonos vidurys ± 0.1R; SL = 1R (4H swing high/low); TP1–TP5 = 0.4R, 0.6R,
1.0R, 1.8R, 3.4R nuo vidurio. Be krypties pranašumo toks šablonas yra
martingalas (P(TP1 prieš SL) ≈ 71% vien dėl geometrijos).

## Vykdymo modeliai (tik šie)
- **M0 (pagrindinis):** rinkos įėjimas kitos žvakės open.
- **M1:** rinkos įėjimas dar viena žvake vėliau (15 min reakcija).
- **L:** limit zonos viduryje, galioja 12 žvakių (3 h) ir iki priešingo
  signalo; neužpildyta, jei TP1 kaina pasiekta anksčiau (praleista) arba
  SL anksčiau; užpildymo žvakėje tikrinamas tik SL.
Įėjimo žvakė imtinai. Jei įėjimo kaina jau už TP1 arba už SL — signalas
praleidžiamas (suskaičiuojama).

## Pozicijos valdymas (grupės šablonas, svoriai mūsų prielaida)
- 5 dalys po 20%; kiekviena išeina savo TP kainoje; SL ant likusių.
- Likusi dalis uždaroma kito PRIEŠINGO signalo tam pačiam instrumentui
  žvakės open (grupė pati taip uždaro), maks. 7 d., tada mark-to-close.
- Be SL perkėlimo į breakeven (variantas B: SL → įėjimas po TP1, antrinis).
- Tos pačios žvakės SL ir TP → SL (pesimistiškai); gap per SL → išėjimas
  open kaina.
- R = |įėjimas − SL| (iš tikrojo įėjimo). Svertinis R = Σ 0.2 × tranšo R.

## Kaštai
0.06% už pusę (fee + slippage) nuo notional įėjimui ir kiekvienai išėjimo
daliai + 10%/m. funding pakaitalas už laikymo laiką, viskas dalinama iš
stop % (R vienetai).

## Testai ir kriterijai
Imties vienetas — **diena** (visos kriptovaliutos juda kartu; signalų ~22/d.):
vidutinė net R per dieną, t-testas per dienas.

**Pagrindinis (M0), visi kartu:**
1. net vidutinis R per signalą > 0 ir dienų-klasterizuotas **t > 3.0**;
2. net vidurkis > 0 **kiekvienoje iš 3 laiko dalių** (05-03→06-30,
   07-01→08-31, 09-01→10-09);
3. krypties įgūdis: dalims TP3 (1R atstumas) — Σ(pasiekta − p_geo) /
   √Σp(1−p) **z > 3.0**, kur p_geo = risk / (risk + reward_TP3) yra
   atsitiktinio klaidžiojimo tikimybė pasiekti TP prieš SL (skaičiuojama
   tik išspręstiems signalams).

**Aprašomieji (ne sprendimui):**
- M1, L, variantas B; z visiems TP1–TP5;
- Kontrolė „anti-signalas“: tie patys lygiai atspindėti per įėjimo kainą,
  priešinga kryptis — jei grupės kryptis turi įgūdį, signalas > anti;
- pagal pusę (long/short), pagal klasę (kripto vs akcijos/ žaliavos),
  pagal mėnesį; viršutinių 10 dienų dalis; sutapimas su BTC diena;
- palyginimas su grupės pačios paskelbtu „Прибыль“ (TP ir uždarymų suma).

## Sprendimas
- Pagrindinis praeina → forward stebėjimas (paper) prieš bet kokį kapitalą.
- Nepraeina → grupės signalai edge neįrodo; nieko netaisome ant tų pačių
  duomenų.
