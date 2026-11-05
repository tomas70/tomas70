# Sonic „valdymo apvalkalo“ simuliacija be signalo — registracija

Užrašyta PRIEŠ paleidžiant simuliaciją. Tikslas: kiek Sonic statistikos (win rate, PF, mažas DD,
retai pralaimimos dienos) duoda TIK valdymas (stopas, išėjimas, dienos tikslas), o kiek — signalas.
Tai iliustracinė simuliacija, ne strategijos testas; jokių pelno kriterijų nėra.

## Duomenys
Evedex XAUTUSD 1 min atidarymo kainos, 2026-04-07 → 2026-10-30, pirmadienis–penktadienis, įėjimai
tik 03:00–13:59 UTC. Kainų kelias — minutės atidarymai; stopas/tikslas suveikia pirmos minutės
atidarymo kaina už lygio (tas pats visiems variantams).

## Apvalkalo parametrai (paimti iš `sonic_reverse_findings.md`, NEderinti pagal rezultatą)
- Viena pozicija vienu metu; 1 oz; P&L USD/oz.
- Stopas 2.0 USD nuo įėjimo. Laiko stopas 35 min (≈ p90 laikymas).
- Dienos tikslas: pasiekus +6.0 USD/oz dienos P&L, tą dieną nebeatidaroma. Po pralaimėjimo naujas
  įėjimas ne anksčiau kaip po 170 min (≈ 2.8 val.). Po laimėjimo apribojimo nėra.
- Įėjimų dažnis: tikslas ~1.4 sandorio per aktyvią dieną (geometrinis laukimas per tinkamas minutes).
- Išėjimo variantai: **E1** fiksuotas tikslas +4.0 / stopas −2.0; **E2** slenkantis: stopas −2.0,
  nuo +2.0 MFE išėjimas, kai kaina atsitraukia 1.0 nuo maksimumo.

## Įėjimo variantai
- **V0** atsitiktinė kryptis.
- **V1** kontrarinė: tik minutės, kai 5 min judesys ≥ 1.0 USD; kryptis prieš tą judesį
  (Sonic požymis, kuris anksčiau davė ~0).
- **V2(q)** „signalo tikslumo ratukas“: kryptis = tikroji 3 min ateities judesio kryptis su
  tikimybe q, kitaip priešinga. q ∈ {0.5, 0.6, 0.7, 0.8, 0.85}. TAI APLEIDŽIA ATEITĮ SĄMONINGAI —
  tai tik skalė „koks signalo tikslumas reikalingas“, ne taisyklė.

## Kaštai (USD/oz round trip)
- K1 0.15 (IB micro gold tipo); K2 3.6 (Evedex taker 0.04 %/pusę ≈ 3.2 + spread 0.4).

## Metrika (200 atsitiktinių sėklų kiekvienam deriniui; mediana ir 5–95 %)
Sandorių sk., win rate, PF (K1 ir K2), pralaimimų dienų dalis, max DD (USD/oz, K1), vid. dienos P&L.
Palyginimas su Sonic po starto: win 87 %, PF 12.5 (x1), pralaimimų dienų 3 %, 1.4 sand./d.
