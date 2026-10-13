# Hedge'intos opcijų premijos struktūros — išankstinė registracija

Užrašyta PRIEŠ rašant skaičiavimo kodą ir PRIEŠ skaičiuojant bet kokį šio tyrimo rezultatą.
Tai tęsinys po `options_vrp_prereg.md` (BTC) ir `options_vrp_eth_prereg.md` (ETH) bei
nepriklausomo audito (`audit_2026-10-12.md`).

## Kodėl šis tyrimas
- Anksčiau: BTC „volatility risk premium“ egzistuoja (IV − realizuotas, +8.4 vol pt, NW t 5.4),
  bet **plikas** 30 d. straddle pardavimas neuždirba (+0.87 %/30d, t 0.89). Auditas: plikas
  straddle sumaišo premiją su |30 d. judėjimo| triukšmu.
- Neištirta: ar premiją galima paimti **struktūra, kuri pašalina kryptį / apriboja uodegą**.
  Tai nauja hipotezė, ne to paties testo perskaitymas iš naujo.

## Sąžiningumo pastabos (prieš testą)
- **BTC nėra nepriklausoma imtis idėjai.** Auditas jau paskaičiavo grubų delta-hedged proxy
  (vega × (IV − RV) − 3 % kaštų): +1.5 %/30d, t 4.35. Čia testuojama kita, kelio priklausoma
  simuliacija su hedge'o kaštais, bet tie patys duomenys. Todėl: griežtesnis slenkstis (t > 3.0)
  ir **ETH kaip papildomas patvirtinimas** (ETH hedge'intų variantų niekas nebandė).
  ETH ir BTC koreliuoja (~0.8), todėl tai ne pilnai nepriklausomas įrodymas.
- **Tai proxy, ne realios kotiruotės.** Neturiu istorinių opcionų kainų, tik DVOL (Deribit 30 d.
  IV indeksas) ir spot. Todėl net PRAĖJUS — rezultatas tik „kandidatas tikrinti su realiomis
  kotiruotėmis“, ne „strategija veikia“.
- Žinomi proxy šališkumai (didina galimybę „praeiti“ — kompensuojama kaštais ir stresu):
  DVOL yra model-free indeksas su sparnais, paprastai **virš** ATM IV → straddle premija
  pervertinta; nėra skew / bid-ask pagal realius strikes; nėra maržos kaštų; perp funding
  ignoruojamas.
- 2 struktūros = 2 testai; slenkstis t > 3.0 (griežtesnis nei įprastas ~2) tai kompensuoja.
- Mano išankstinis lūkestis: A struktūra tikriausiai bus teigiama (VRP egzistuoja), bet po
  hedge'o kaštų gali neišlikti. B nežinau. Tai ne kriterijus, tik pastaba, kad nepasakyčiau
  „žinojau“ po fakto.

## Duomenys
- BTC: Deribit DVOL 1D (`close`), Binance spot BTCUSDT 1d close; kaip `research_options_vrp.py`
  (ta pati datų lygiuotė: DVOL reikšmė dienai d = d dienos eilutė). Imtis nuo DVOL pradžios
  (2021-03-24) iki paskutinio pilno 30 d. lango.
- ETH: DVOL ETH + Binance ETHUSDT 1d close, ta pati procedūra.
- Visi įėjimai: **kiekviena diena t** (persidengiantys langai, kaip ankstesniuose tyrimuose);
  inferencija — Newey-West, lag 30.

## Struktūros (1 vienetas spot nominalo, 30 d. europietiška, r = 0, strike K = S_t)
Kainodara: Black-Scholes, σ = DVOL_t / 100 įėjimo dieną t; vienodas σ visiems ATM strikams.
P&L išreiškiamas **dalimi S_t**.

### A — delta-hedged trumpas ATM straddle
- Parduodu ATM call + put. Premija = BS(σ=DVOL_t).
- Kiekvienos dienos k = 0…29 close: laikau ilgą spot poziciją h_k = 2·Φ(d1_k) − 1, kur
  d1_k = [ln(S_{t+k}/K) + ½σ_k²T_k]/(σ_k√T_k), σ_k = DVOL_{t+k}/100, T_k = (30 − k)/365
  (tai trumpo straddle delta neutralizavimas). Pozicija laikoma nuo k iki k+1.
- Hedge P&L = Σ_k h_k·(S_{t+k+1} − S_{t+k}). Galiojimo pabaigoje (k = 30) hedge uždaromas.
- Settlement: − |S_{t+30} − K|.
- **Grynasis P&L / S_t** = premija·(1 − c_opt) − |S_T − K|/S_t + hedge P&L/S_t − hedge kaštai/S_t,
  hedge kaštai = c_hedge · Σ |h_k − h_{k−1}| · S_{t+k} (h_{−1} = 0, plius galutinis uždarymas |h_29|·S_T).
- Hedge'inama kartą per parą (dienos close) — mažesnis dažnis nei realus gamma scalping;
  tai prielaida, ne optimizacija.

### B — nehedge'intas „iron butterfly“ (apibrėžta rizika)
- Parduodu ATM straddle, perku apsauginį put ties 0.85·S_t ir call ties 1.15·S_t.
- Sparnų σ_w = 1.15 · DVOL_t / 100 (skew priedas; plokščias σ pigintų sparnus ir šališkai
  gerintų rezultatą).
- Grynasis P&L / S_t = (straddle premija − sparnų premija) − c_opt·(straddle premija + sparnų premija)
  − |S_T − K|/S_t + (max(0.85S_t − S_T, 0) + max(S_T − 1.15S_t, 0))/S_t.

## Kaštų parametrai
| | Pagrindinis | Stresas |
|---|---|---|
| c_opt (nuo kiekvienos opcijų kojos premijos) | 3 % | 8 % |
| c_hedge (nuo hedge sandorio nominalo, perp taker) | 5 bps | 15 bps |
| Sparnų σ daugiklis (tik B) | 1.15 | 1.30 |

## Sprendimo kriterijai (visi privalo būti išpildyti struktūrai „praeiti“)
1. **BTC, pagrindiniai parametrai:** vidutinis grynas P&L > 0 ir NW t > 3.0.
2. **Metai:** BTC ≥ 4 iš 6 kalendorinių metų (2021–2026, dalinės imtys skaičiuojamos) teigiami.
3. **Kaštų stresas:** BTC su streso parametrais vidurkis vis dar > 0.
4. **ETH patvirtinimas, pagrindiniai parametrai:** vidurkis > 0 ir NW t > 2.0.
5. **Uodega / drawdown:** nepersidengiančių 30 d. blokų (30 fazių poslinkių) kompaundinta
   kreivė su 1× nominalu (nominalas = kapitalas): **mediana per fazes maks. DD < 30 %** ir
   **blogiausia fazė < 45 %** (BTC, pagrindiniai parametrai).

Rezultatas: struktūra PRAEINA tik jei išpildyti 1–5. „PRAEINA“ reiškia tik „verta tikrinti su
realiomis Deribit kotiruotėmis“.

## Aprašomieji (verdikto nekeičia)
- Vidurkis, p5, blogiausias 30 d., teigiamų dienų dalis; pagal metus.
- Ne persidengiantys įrašai (kas 30-a eilutė).
- Kaštų jautrumas (0 / pagrindinis / stresas atskirai c_opt ir c_hedge).
- A su filtru „įeiti tik kai T50 = LONG“ — **tik aprašomasis** (po ETH IV-percentilio patirties
  papildomų sąlyginių taisyklių netikriname verdiktui).
- Realizuotas P&L skaidinys A: premija, settlement, hedge P&L, kaštai.

## Ko nedarau
- Neieškau geresnių strikų, galiojimo trukmių, hedge dažnių ar sparnų atstumų po pirmo
  rezultato. Jei nepraeina — šios struktūros uždaromos; jei praeina — kitas žingsnis yra
  realių kotiruočių validacija, ne parametrų derinimas.
