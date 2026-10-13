# Perp funding (cash-and-carry) — išankstinė registracija

Užrašyta PRIEŠ atsisiunčiant bet kokias funding reikšmes ir PRIEŠ skaičiuojant rezultatą.
Šaltinis: audito (`audit_2026-10-13_slice3_crosscutting.md`) 3 rekomendacija.

## Kodėl
- Visos kryptinės idėjos projekte nepraėjo arba lieka silpnos. Funding carry — **struktūrinė
  premija, ne prognozė**: ilga spot + trumpa perp, kai funding teigiamas, pelnas = funding.
  Mažai koreliuoja su kryptimi, gali užpildyti T50 „grynųjų“ laikotarpius.
- Duomenų prieinamumas patikrintas (tik pasiekiamumas, ne reikšmės): Deribit viešas
  `get_funding_rate_history` grąžina valandines reikšmes `interest_1h` BTC-PERPETUAL ir
  ETH-PERPETUAL nuo ~2020-03 (2019 m. kovui tuščia). Binance fapi šiame sandbox'e
  nepasiekiamas.

## Sąžiningumo pastabos (prieš testą)
- **Funding rezultatų neturiu.** Nežinau nei vidutinio funding, nei jo ženklo pagal metus.
- **Birža neatitinka tavo borso.** Testas — Deribit perp funding (referencinė rinka). Tu
  prekiauji Evedex; jų funding gali skirtis (ir dydžiu, ir pastovumu). Net PRAĖJUS, tai tik
  „premija egzistuoja referencinėje rinkoje“. Prieš bet kokį naudojimą reikia Evedex funding
  forward logo (Evedex funding prieinamumas — atskiras patikrinimas, ne verdikto dalis).
- **Backtest NEMODELIUOJA:** bazės (spot–perp) judėjimo mark-to-market, delta rebalansavimo,
  likvidacijos rizikos trumpoje perp pozicijoje squeeze'o metu, kontrahento/biržos rizikos,
  perp ↔ spot kolaterolo skirtumo. Tai realūs rizikos šaltiniai, ne triukšmas.
- 2 variantai × 2 aktyvai = 4 skaičiavimai; slenkstis t > 3.0 BTC, 2.0 ETH.

## Duomenys
- Deribit `get_funding_rate_history`, `interest_1h`, BTC-PERPETUAL ir ETH-PERPETUAL, nuo
  pirmos turimos reikšmės iki paskutinės pilnos UTC dienos atsisiuntimo momentu (valandinės
  eilutės pasiimamos gabalais, išsaugomos cache).
- Dienos funding F_d = suma `interest_1h` per UTC dienos valandas (trumpa perp gauna F_d, kai
  teigiamas, moka, kai neigiamas — nominalo dalimi). Dienos su < 24 eilutėmis žymimos; jei jų
  > 2 %, pranešu ir tokios dienos praleidžiamos (be interpoliacijos).

## Variantai (po 1× nominalą, delta-neutralu, grynasis nuo kaštų)
- **C0 (visada įjungtas):** trumpa perp + ilga spot nuo pirmos dienos; kaštai tik įėjimo ir
  pabaigos.
- **C1 (sąlyginis):** įjungtas dienai d, jei **vidutinis F per paskutines 7 dienas (iki d−1
  imtinai) > 0**; kitaip išjungtas. Įjungimas/išjungimas atsiskaito kaštus.
- Kaštai: **0.15 % nominalo už įjungimą** (spot 0.10 % + perp 0.05 %) ir tiek pat už
  išjungimą (viso 0.30 % round trip).
- Kapitalo bazė: **1.25 × nominalas** (maržos buferis). Metinė grąža skaičiuojama nuo šios
  bazės; palyginimo „hurdle“ = **4 % per metus** (grynųjų alternatyva).

## Kriterijai (kiekvienam variantui; visi privalo būti išpildyti)
1. **BTC:** grynoji metinė grąža nuo kapitalo bazės > 4 % ir dienos pertekliaus (grynoji −
   4 %/365) vidurkio **Newey-West (lag 7) t > 3.0**.
2. **Metai:** pilni kalendoriniai metai 2021–2025: grynoji grąža > 0 bent **4 iš 5**; **2024–2025**
   vidurkis ≥ 4 % (neatitirpusi premija).
3. **Stresas:** funding sumažintas 30 % ir kaštai ×2 — grynoji grąža vis dar > 0.
4. **ETH replikacija** (pagrindiniai parametrai): grynoji metinė grąža > 4 % ir NW t > 2.0.

Variantas PRAEINA tik jei išpildyti 1–4. PRAĖJIMAS = „premija egzistuoja Deribit ir aplenkia
grynuosius po kaštų“, ne „strategija paruošta“.

## Aprašomieji (verdikto nekeičia)
- Funding pasiskirstymas pagal metus: vidurkis, dalis neigiamų dienų, ilgiausia neigiama
  serija; blogiausia 30 d. suma.
- C0 ir C1 skirtumas (ar sąlyga ką nors prideda).
- Funding tik T50 FLAT dienomis (BTC) — ar tai praktiškai užpildo tuščius laikotarpius.
- Koreliacija su BTC dienos grąža (turėtų būti ~0).
- Antros pusės vs pirmos pusės vidurkis (premijos nykimas).

## Ko nedarau
- Neieškau geresnių sąlygų (7 d. langas, slenksčių, daugiau aktyvų) po pirmo rezultato.
- Nemodeliuoju ir nedarau išvadų apie bazės riziką be atskiro prereg ir duomenų.
- Jei praeina — kitas žingsnis yra Evedex funding forward logas, o ne prekyba.
