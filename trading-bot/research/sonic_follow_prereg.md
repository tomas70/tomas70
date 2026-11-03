# Sonic AI (XAUUSD) sandorių sekimas — išankstinė registracija

Užrašyta PRIEŠ skaičiuojant bet kokį sekimo rezultatą. Šaltinis: viešas monitoriaus repo
`sonicdesk-live/sonicdesk` (`trades_raw.txt`, `trades_new.csv`), analizė — `scratchpad/sonic/`.

## Ką žinome ir ko ne
- Žinome: 645 publikuoti uždaryti sandoriai (2024-11 → 2026-10), pirminė analizė parodė
  87 % win rate ir PF 12.5 po „viešo starto“ (2025-09-18), bet su vienu sugadintu įrašu
  (92 d. sandoris, P&L neatitinka kainų) ir režimo lūžiu starto metu.
- **Nežinome**, ar paskyra tikra. Šis tyrimas to netikrina. Jis atsako į kitą klausimą:
  **jei sandoriai tikri, ar juos galima pelningai kopijuoti ant Evedex?**

## Hipotezė
Sekėjas, kuris atidaro Sonic sandorį su vėlavimu d ir uždaro jį su tuo pačiu vėlavimu d,
Evedex XAUTUSD perp uždirba teigiamą grynąjį lūkestį po kaštų.

## Mano išankstinis lūkestis (kad nesakyčiau „žinojau“ po fakto)
- Vidutinis Sonic judesys po starto ≈ 3.5 punkto (~8.7 bps). Taker fee 0.04 % per pusę =
  ~8 bps round trip + pusė spread'o ≈ 1 bps. **Tikėtina, kad grynasis lūkestis po Evedex kaštų
  bus apie nulį ar neigiamas net be vėlavimo.** Tai nėra kriterijus, tik prielaida.

## Duomenys
- Sonic sandoriai: tik laikai ir kryptis (open_time, close_time, side). Kainos ir loto dydis
  ignoruojami (kitas brokeris, kitos kainos).
- Vykdymo kainos: **Evedex XAUTUSD 1 min žvakės** (vartotojo birža; 24/7 perp; istorija nuo
  2026-04-06 listingo). Įėjimas = ŽVAKĖS OPEN, kurioje yra (open_time + d); išėjimas = žvakės
  OPEN, kurioje yra (close_time + d). Tas pats d įėjimui ir išėjimui (kopijuoja ir uždarymą).
- Imtis: Sonic sandoriai, kurių open_time ≥ 2026-04-07 00:00 UTC ir laikymas ≤ 12 h, ir
  abi žvakės yra. Neįtraukti sandoriai išvardijami su priežastimi.

## Parametrai
- Vėlavimai: **d ∈ {0, 1, 3, 5} min.** d = 0 — viršutinė riba (nerealu). **Pirminis d = 3.**
- Kaštai, pirminis scenarijus: **0.04 % kainos per pusę** (Evedex taker, kaip
  `analysis/paper_tracker.py`) **+ 0.20 USD/oz per pusę** (pusė spread'o, išmatuota gyvai
  2026-11-03: bid 3896.3 / ask 3896.7). Papildomi scenarijai (aprašomieji): tik spread'as
  (be fee), 0.02 %/pusę (maker-panašu, NEPATIKRINTA), 0.10 USD/oz round trip (CFD brokerio
  tipo).
- Kryptis: kaip Sonic. Dydis: 1 oz; rezultatas bps nuo įėjimo kainos.

## Kriterijai (visi privalo būti išpildyti, d = 3 pirminiuose kaštuose)
1. Vidutinis grynasis rezultatas > 0 ir **dienų grupuotas t > 3.0** (dienos suma kaip viena
   stebėjimas).
2. d = 5 vidutinis grynasis rezultatas > 0.
3. Laikotarpio trečdaliai (pagal sandorių skaičių, chronologiškai): teigiami ≥ 2 iš 3.
4. Duomenų tikrumas (sąlyga, ne kriterijus): d = 0 sekėjo bruto judesio ir Sonic paskelbto
   judesio koreliacija (Pearson) > 0.8; kitaip XAUT nėra tinkamas proxy ir išvada „neaišku“.

PRAEINA = „sekimas Evedex ekonomiškai įmanomas, jei Sonic tikras“. Ne = „net jei tikras, ant
Evedex nesekama“. Nė viena išvada nepatvirtina paties Sonic autentiškumo.

## Aprašomieji
- Bruto ir grynasis vidurkis, win rate, PF pagal d ir kaštų scenarijų.
- Palyginimas: Sonic paskelbtas vidutinis judesys vs sekėjo d = 0 (slippage/proxy skirtumas).
- Break-even kaštai: kiek bps round trip leistų nulinį vidurkį kiekvienam d.
- Laikymo trukmės ir dienos laiko poveikis (tik aprašyti).

## Forward
Po šio istorinio testo tos pačios taisyklės taikomos NAUJIEMS sandoriams, kai tik jie
pasirodo repo (`trades_new.csv`): boto modulis kasdien paima naujus sandorius, apskaičiuoja
rezultatą pagal d ∈ {0,1,3,5} ir pirminį kaštų scenarijų ir kaupia `logs/sonic_follow.csv`.
Repo skelbia sandorius tik jiems pasibaigus, todėl tai ne tiesioginė kopija, o forward
ataskaita apie tai, ar sekimas veiktų.

## Ko nedarau
- Neieškau geresnių d, filtrų (valanda, kryptis, loto dydis) ar kaštų po pirmo rezultato.
- Nekeičiu kriterijų. Jei nepraeina — nesiūlau „pataisyto“ varianto be naujos registracijos.

---

## Priedas (užrašytas po pirmo istorinio bėgimo): laiko juosta

**Kas nutiko.** Pirmas bėgimas (`sonic_follow_results_utc_misread.txt`), laikus skaitant kaip UTC,
davė duomenų tikrumo sąlygos pažeidimą: Sonic paskelbto judesio ir rinkos judesio koreliacija
−0.02 (sekėjas d = 0: +0.11 vs Sonic +4.01 USD/oz). Verdiktas buvo „neaišku“.

**Tyrinėjamoji diagnostika (NE iš anksto registruota, `research_sonic_tzdiag.py`).** Išbandyti
sveikų valandų poslinkiai −4…+5 val.: tik **+3** davė koreliaciją **0.83** (sekėjas +4.21 vs Sonic
+4.01 USD/oz); visi kiti |corr| ≤ 0.04. Papildomas patikrinimas 2026-10-26…30 (Europa jau žiemos
laiku nuo spalio 25, JAV dar ne): +3 corr 0.99, +2 corr 0.07 (n = 4). Išvada: publikuojamas laikas
yra brokerio serverio laikrodis — **UTC+3, kol Niujorke galioja vasaros laikas, kitaip UTC+2**.
Perėjimas 2026-11-01 dar nestebėtas; forward ataskaita turi tai tikrinti (žr. žemiau).

**Dėmesio.** Poslinkis parinktas pagal Sonic paskelbtų ir rinkos judesių koreliaciją, o ne pagal
sekėjo P&L, bet ant TOS PAČIOS imties, todėl pakartotas testas laiko juostos požiūriu nėra
nepriklausomas. Tai duomenų formato pataisymas, ne našumo parametras.

**Pakartojimas.** Visos taisyklės, vėlavimai, kaštai ir kriterijai LIEKA TOKIE PATYS; keičiasi tik
publikuotų laikų konvertavimas į UTC. Pirmas (neteisingas) bėgimas paliktas faile kaip įrašas.

**Forward tikrumo patikra.** `/sonic` ataskaita rodys paskutinių ≥ 8 sandorių koreliaciją tarp
Sonic judesio ir sekėjo d = 0 judesio; jei < 0.5, ataskaita įspėja, kad laiko konvertavimas
(arba šaltinis) nebetinka.
