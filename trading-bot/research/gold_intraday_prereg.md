# Aukso intraday paieška (15 min – 4 val.) su IB micro gold kaštais — registracija

Užrašyta PRIEŠ atsisiunčiant PAXG 15 min duomenis ir PRIEŠ bet kokį skaičiavimą. Tęsinys po
`sonic_reverse_findings.md` ir `sonic_shell_sim_results.txt`: Sonic signalas iš kainos/laiko
neatkuriamas, bet trumpas auksas su pigiais kaštais netestuotas ilgesniame horizonte.

## Sąžiningumo pastabos
- **Mano išankstinis lūkestis: tikriausiai NEpraeis.** Ankstesni intraday signalai šiame projekte
  (SWEEP/ICT, POC, 1h pullback, Telegram) neturėjo pranašumo po kaštų. Registruoju 4 paprastas,
  literatūroje žinomas taisykles, ne ieškau tinkamiausių.
- Proxy: PAXGUSDT (tokenizuotas auksas, Binance spot, 24/7, plonas likvidumas ~3–16 oz/15 min).
  Ne tikras XAUUSD; naudojami tik Pirmadienio–Penktadienio langai. Proxy tinkamumo sąlyga žemiau.
- 4 taisyklės × 1 kryptis = 4 testai. Dev slenkstis t > 3.0 (≈ Bonferroni).

## Duomenys
- PAXGUSDT 15 min žvakės (open/high/low/close), data-api.binance.vision, 2020-08-28 → 2026-10-30.
- **Dev:** 2021-01-01 → 2024-06-30. **Test (vienkartinis):** 2024-07-01 → 2026-10-30.
- **Proxy sąlyga:** dienos grąžų koreliacija PAXG vs IB XAUUSD (London Gold) per paskutines ~120
  prekybos dienų ≥ 0.95; kitaip rezultatai „neaiškūs“.

## Taisyklės (visos UTC, simetriškos long/short, ne daugiau 1 sandorio per dieną, tik pr–pn)
Vykdymas: signalas po signalinės žvakės uždarymo, įėjimas kitos žvakės OPEN, išėjimas nurodytos
žvakės OPEN; stopas — jei žvakės high/low pasiekia lygį, išėjimas lygyje (arba open, jei gap).
- **R1 Londono proveržis (ORB):** range = 07:00–07:59 high/low. Nuo 08:00 pirma 15 min žvakė, kuri
  UŽSIDARO už range → įėjimas kryptimi. Stopas — priešinga range pusė. Išėjimas 12:00 open.
- **R2 Intraday momentum:** signalas = grąža nuo 00:00 open iki 08:00 open. Įėjimas 08:00 open ta
  kryptimi, išėjimas 12:00 open. Be stopo.
- **R3 Pirmos sesijos atsisukimas:** signalas = grąža 08:00→12:00. Įėjimas 12:00 open PRIEŠ ją,
  išėjimas 16:00 open. Be stopo.
- **R4 Valandos impulso atsisukimas:** jei paskutinės 1 val. (4 žvakių) grąža |r| ≥ 0.40 %,
  įėjimas PRIEŠ ją kitos žvakės open, išėjimas po 2 val. (8 žvakės). Stopas 0.40 % nuo įėjimo.
  Tik 03:00–13:59 UTC.

## Kaštai (round trip, % nuo kainos)
K1 = 0.015 % (≈ 1.5 bps; IB micro gold ~$1.5/kontraktą + spread), K2 = 0.040 % (stresas).

## Kriterijai (kiekvienai taisyklei; visi privalo būti išpildyti)
1. **Dev:** vidutinis grynasis sandorio rezultatas (K1) > 0 ir dienų grupuotas **t > 3.0**.
2. **Test:** vidutinis grynasis (K1) > 0 ir **t > 2.0**.
3. **Stresas (K2) Test:** vidutinis > 0.
4. **Dažnis:** ≥ 150 sandorių Dev ir ≥ 80 Test (kitaip „mažai duomenų“).

## Aprašomieji
Win rate, PF, vid. rezultatas bps, break-even kaštai, rezultatas pagal metus, sandorių/metus.

## Ko nedarau
- Neieškau parametrų (range lango, ribų, laikymo trukmės, valandų) po rezultato.
- Jei kuri nors taisyklė neigiama su dideliu |t| — NEapverčiu krypties ir netikrinu kaip naujos
  taisyklės be naujos registracijos.
- Praėjimas = tik „verta forward paper testo“, ne prekybos signalas.
