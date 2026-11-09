# Running Shoe Deal Finder Serbia

Lokalna aplikacija koja traži stvarno dobre ponude patika za trčanje u srpskim online prodavnicama. Poredi cene među
prodavnicama, čuva istoriju cena, proverava veličine i koristi nezavisne ocene modela. Ne veruje procentima popusta koje
prodavac prikazuje.

## Instalacija na Windows-u

Potreban je Python 3.11 ili noviji. U PowerShell-u, iz foldera projekta:

```powershell
winget install --id Python.Python.3.12 --scope user --override "/quiet InstallAllUsers=0 PrependPath=0 Include_launcher=1"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py doctor
```

Instalacija ne menja podrazumevani `python` na računaru. Sve komande ispod koriste `.\.venv\Scripts\python.exe`.

## Komande

```powershell
.\.venv\Scripts\python.exe main.py scan                                   # svi modeli i sve prodavnice
.\.venv\Scripts\python.exe main.py scan --brand ASICS
.\.venv\Scripts\python.exe main.py scan --model "Novablast 5"
.\.venv\Scripts\python.exe main.py scan --category long_run --max-price 12000
.\.venv\Scripts\python.exe main.py deals --top 20
.\.venv\Scripts\python.exe main.py compare "Nike Vomero 18"
.\.venv\Scripts\python.exe main.py history "ASICS Novablast 5"
.\.venv\Scripts\python.exe main.py alerts
.\.venv\Scripts\python.exe main.py report                                 # HTML, CSV i JSON u reports\output
.\.venv\Scripts\python.exe main.py dashboard                              # http://127.0.0.1:8765/dashboard.html
.\.venv\Scripts\python.exe main.py review                                 # oglasi za ručnu proveru uparivanja
.\.venv\Scripts\python.exe main.py rematch                                # ponovo uparuje sačuvane oglase, bez novih zahteva
.\.venv\Scripts\python.exe main.py images                                 # dopunjuje slike ponuda na stanju, bez novih cena
.\.venv\Scripts\python.exe main.py foreign                                # osvežava cene iz stranih prodavnica
.\.venv\Scripts\python.exe main.py catalog update                         # osvežava katalog iz RunRepeat-a
.\.venv\Scripts\python.exe main.py catalog import moji_modeli.csv         # ručni uvoz sa izvornim URL-om
.\.venv\Scripts\python.exe main.py doctor
.\.venv\Scripts\python.exe -m pytest
```

Filteri za `scan` i `deals`: `--brand --model --category --max-price --sizes 45,45.5,46 --min-discount --retailer --top
--all-sizes --sort score|discount|review|price`. Profil, težine ocene i lista praćenja su u [config.yaml](config.yaml).

Primer, najveći popusti od bar 20%:

```powershell
.\.venv\Scripts\python.exe main.py deals --min-discount 20 --sort discount
```

## Gde šta vidiš u dashboardu

- **Slike:** ispred imena je slika tačno te ponude, iz polja `og:image` na stranici proizvoda. U bazi se čuva samo adresa, a
  sliku učitava browser sa sajta prodavnice, pa je za prikaz potreban internet. Ako ponuda nema sliku, prikazuje se slika istog
  modela iz druge ponude, moguće u drugoj boji. Sken ih osvežava sam. Slike pripadaju prodavnicama i brendovima, pa za
  javno objavljivanje, npr. u videu, proveri da li smeš da ih koristiš.
- **Proveri: Đak Sport / Planeta Sport:** ove prodavnice blokiraju automatsku proveru, pa dugme samo otvara Google pretragu
  njihovog sajta za taj model. Cena se ne upisuje i ne ulazi u poređenje.
- **Linkovi:** svaki model ima po jedan link ka svakoj prodavnici koja ga ima na stanju, sa cenom i veličinama. Link vodi na
  najjeftiniju dostupnu ponudu te prodavnice. Sve boje i ponude su u detaljima modela. U CLI-ju su to redovi `takođe:`, a u
  `deals.csv` kolona `other_stores`.
- **Veličine:** rang i skor važe za sve veličine. U filteru `Veličine` možeš da štikliraš više brojeva odjednom, npr. 45,
  45 1/3 i 46. Model ostaje ako bar jedna prodavnica ima bar jedan od njih. Red tada prikazuje najjeftiniju ponudu sa tim brojevima, a skor,
  popust i pouzdanost važe za tu ponudu, pa pouzdanost može biti i niska. Kolona za sortiranje tada menja samo redosled modela. Za stari način, gde rang prikazuje samo željene veličine, stavi `filter_preferred_size: true` u `config.yaml` ili
  u CLI-ju dodaj `--sizes 45,46`.
- **Popust:** kolona u svim prikazima, filter `Min. popust %`, a u CSV-u kolona `discount_pct`. To je popust koji prodavac
  tvrdi, a ne potvrđena ušteda.
- **Sortiranje:** klikni na naziv kolone (Model, Cena, Popust, Tržište, Veličine, Ocena, Skor); drugi klik obrće smer. Prazna
  vrednost je uvek na kraju. Red modela prikazuje njegovu najbolju ponudu po toj koloni, npr. po popustu onu sa najvećim
  popustom.
- **Cena u SAD:** američka preporučena cena (MSRP) sa RunRepeat-a, preračunata po dnevnom kursu ExchangeRate-API-ja. Procenat
  poredi cenu ponude sa tom cenom, a `precrtana` isto za precrtanu cenu prodavca. To nije ulična cena, nego preporučena cena.
  MSRP ne sadrži američki porez, a cene u Srbiji sadrže 20% PDV-a.
  Modeli van kataloga nemaju ovaj podatak. U CSV-u: `msrp_usd`, `msrp_rsd`, `vs_msrp_pct`. Kurs se čuva u `data/fx_usd_rsd.json`.
- **Cena u EU:** najniža stvarna cena iz stranih prodavnica za isti model, pol i varijantu, samo za veličine na stanju. Negativan
  procenat znači da je kod nas jeftinije. `ista šifra` znači ista šifra proizvođača, a `druga boja` isti model u drugoj boji. Sa
  izabranim brojem gledaju se samo strane ponude koje ga imaju. Cena sadrži PDV te zemlje, a ne sadrži dostavu ni eventualnu
  carinu do Srbije. Sve strane ponude su u detaljima modela. U CSV-u: `eu_price_eur`, `eu_price_rsd`, `eu_source`, `vs_eu_pct`.
  Ove cene ne ulaze u tržište, skor ni rang. Ne prikazuje se cena starija od 7 dana.
- **Svi modeli:** svi modeli za tvoj pol, i kada nisu dovoljno pouzdani za Top ili nemaju ocenu.
- **Top 20:** ulazi samo ponuda sa bar 4 različita broja na stanju, pa ponuda bez podataka o brojevima ne ulazi. Važi i kad je izabrana veličina:
  red je tada najjeftinija ponuda te veličine među ponudama sa bar 4 broja. Ostale kartice i pretraga nemaju ovaj uslov.
- **Nije za trčanje:** dugme na kraju reda sklanja ceo model, sa svim polovima i varijantama, iz svih kartica, pretrage, `deals` i `deals.csv`.
  Vraća se u kartici `Nije za trčanje`. Oznake su u `data/not_running.json` i ostaju posle skena; cene i istorija se i dalje beleže.
  Dugme radi samo kad je dashboard otvoren preko `main.py dashboard`.
- **Pol:** filter `Pol` bira muške ili ženske modele, a unisex se vidi u oba. Svaki pol ima svoj spisak brojeva, rang i cene u EU; polovi se nikad ne mešaju.
  `gender` u `config.yaml` određuje šta se prvo prikazuje i važi za `deals`, `deals.csv` i alarme.
- **Promene cena:** red nosi oznaku `Nova najniža cena`, `Cena snižena` ili `Cena podignuta` 3 dana od promene, a kartica `Promene cena` ih sve prikazuje na jednom mestu.
  Računaju se samo naša očitavanja istog oglasa, pa „najniža“ znači najniža od početka praćenja tog oglasa.
- **Pretraga po imenu:** u rang-karticama (Top 20, Najjeftinije, Duge staze, Svakodnevne, Tempo i trka) gleda sve modele na stanju
  i nema ograničenja broja redova, pa nalazi i modele koji nisu u rangu. Filter `Min. pouzdanost` se tada ne primenjuje.

## Dnevni sken

Zadatak `Running Shoe Deal Finder - dnevni sken` u Windows Task Scheduler-u pokreće [daily_scan.cmd](daily_scan.cmd) svakog
dana u 07:00: sken, pa osvežavanje izveštaja u `reports\output`. Radi samo dok si prijavljen na Windows; ako je računar u to
vreme isključen, pokreće se čim bude dostupan. Rezultat je u `data\daily_scan.log`.

```powershell
Get-ScheduledTaskInfo -TaskName "Running Shoe Deal Finder - dnevni sken"                     # poslednje i sledeće pokretanje
Start-ScheduledTask -TaskName "Running Shoe Deal Finder - dnevni sken"                       # pokreni odmah
Disable-ScheduledTask -TaskName "Running Shoe Deal Finder - dnevni sken"                     # privremeno isključi
Unregister-ScheduledTask -TaskName "Running Shoe Deal Finder - dnevni sken" -Confirm:$false  # trajno ukloni
```

## Kako radi

1. **Katalog** nastaje iz nezavisnog izvora, nikad iz srpskih prodavnica. Nalazi se u `catalog/seed_catalog.json`. On daje
   ocene i specifikacije, ali ne odlučuje koji modeli postoje.
2. **Sken** čita sitemap i kategorije trčanja svake prodavnice i otvara svaku stranicu proizvoda koju prodavac vodi pod
   trčanjem. Pretraga sajta se ne koristi jer je `?search=` zabranjen u robots.txt-u kod više prodavnica. Rasprodati oglasi se
   ne otvaraju ponovo `scan.recheck_out_of_stock_days` dana (podrazumevano 7), pa se povratak na stanje može primetiti sa
   toliko kašnjenja.
3. **Uparivanje** koristi brend, tačan token generacije, pol i varijantu. Oglas koji nije u katalogu povezuje se preko istog
   SKU-a, a ako ga nema, prikazuje se kao **opaženi model** pod nazivom prodavca, bez ocene. Generacije se ne spajaju.
   Sve što nije sigurno ide u `review`, a ne u rang.
4. **Opažanja** su nepromenljiva: SQLite trigeri blokiraju UPDATE i DELETE nad `price_observations`.
5. **Ocena** se računa iz tržišta, istorije, kvaliteta, veličine i, sa malom težinom, popusta koji prodavac tvrdi.

### Nivoi identiteta

| Nivo | Primer | Značenje |
|---|---|---|
| Model | `asics-novablast-5` | Generacija iz kataloga |
| Opaženi model | `observed-adidas-...` | Prodavnica ga vodi pod trčanjem, a nije u katalogu; nema ocenu ni specifikacije |
| Varijanta | `ASICS:1011B974-001` | Jedna boja, po SKU-u proizvođača |
| Oglas | hash prodavnice i URL-a | Konkretna stranica; na Ananasu i stvarni prodavac |

Poređenje se radi unutar iste kombinacije modela, pola i varijante (npr. GTX). Različite boje su odvojene ponude istog modela.

### Klase ponuda

Klasa zavisi samo od **dokaza o ceni**: prednosti u odnosu na medijanu drugih prodavaca (60%) i na istoriju oglasa (40%).

| Klasa | Uslov (podrazumevano, menja se u config.yaml) |
|---|---|
| EXCEPTIONAL | prednost ≥ 20%, skor ≥ 80, bar 3 prodavca, nezavisna ocena ≥ 75 |
| VERY GOOD | prednost ≥ 12%, skor ≥ 65, bar 3 prodavca ili istorija |
| GOOD | prednost ≥ 5% |
| NORMAL | prednost od -5% do 5% |
| OVERPRICED | prednost ispod -5% |
| INSUFFICIENT DATA | nema ni drugih prodavaca ni dovoljno istorije |

Popust prodavca ima status `CLAIMED`, `CONTRADICTED` ili `SUPPORTED`. Potvrđena ušteda postoji samo kada smo referentnu cenu
sami ranije zabeležili.

Istorijska prednost traži bar 5 opažanja u rasponu od bar 14 dana. Do tada piše `INSUFFICIENT DATA`.

### Veličine

- Veličina je `CONFIRMED` samo ako prodavac izričito prikazuje tu EU veličinu kao dostupnu.
- US, UK i cm vrednosti se čuvaju kako ih prodavac navodi. Aplikacija nikad sama ne konvertuje US u EU.
- Razlomljena veličina (Adidas, Hoka: `45 1/3`) računa se kao približno poklapanje ako je unutar `eu_size_tolerance` i
  jasno je označena. Vrednost `46 2/3` se ne računa kao 46.
- Na nekim sajtovima cena zavisi od veličine, pa se za rang koristi cena tvoje veličine.

## Izvori kataloga

Provereno 5. novembra 2026.

| Izvor | Automatsko preuzimanje | Razlog |
|---|---|---|
| RunRepeat | Da, spor tempo, lična upotreba | robots.txt dozvoljava katalog; pravna stranica nema klauzulu o prikupljanju. Sajt nudi i plaćeno licenciranje podataka, pa je ovo siva zona: nije izričito dozvoljeno, samo nije zabranjeno |
| Running Warehouse | Ne | Uslovi izričito zabranjuju crawling i data mining |
| Believe in the Run | Ne | Uslovi zabranjuju pristup van njihovog interfejsa |
| RoadTrailRun | Ne | Za nepoznate botove robots.txt ima i `Allow: /` i `Disallow: /`; signali zabranjuju AI obuku |
| Doctors of Running | Ne | robots.txt blokira više AI botova; nije nam potreban za osnovni rad |
| Runnea | Ne | robots.txt dozvoljava, ali pravna stranica zabranjuje reprodukciju i ponovnu upotrebu bez pisanog odobrenja |

Model iz drugih izvora može se dodati ručno preko `catalog import`, uz obavezan izvorni URL.

Kategorije `long_run` i `recovery` RunRepeat ne daje direktno. One su **izvedene** iz njegovih laboratorijskih oznaka, a
pravilo piše uz svaki model (polje `category_basis`). Godina izlaska i pena ostaju prazne jer nisu pouzdano navedene:
RunRepeat polje "Technology" meša penu, uložak (Ortholite) i membranu (Gore-Tex), pa se čuva samo kao tekst uz opis
amortizacije. Posle dopune kataloga pokreni `rematch`: oglasi se ponovo uparuju, a stara opažanja cena ostaju nepromenjena.

## Prodavnice

| Prodavnica | Domen | Način | Status |
|---|---|---|---|
| Sport Vision | sportvision.rs | Sitemap + kategorija trčanja + NBShop stranice | Radi |
| Run 'n More | runnmore.com | Sitemap + NBShop stranice | Radi |
| ET Sport | etsport.rs | Sitemap + kategorija trčanja + NBShop stranice | Radi |
| Extra Sports | extrasports.com | Sitemap + kategorija trčanja + NBShop stranice | Radi |
| Buzz Sneakers | buzzsneakers.rs | Sitemap + kategorija trčanja + NBShop stranice | Radi (pronađen tokom izrade) |
| Intersport | intersport.rs | Sitemap + stranice proizvoda | Radi |
| N Sport | n-sport.net | Sitemap + stranice proizvoda | Radi |
| Ananas | ananas.rs | Sitemap + stranice proizvoda, po prodavcu | Radi |
| Planeta Sport | planetasport.rs | Cloudflare blokira pristup | NOT VERIFIED |
| Đak Sport | djaksport.com | Cloudflare blokira pristup | NOT VERIFIED |

Proverene, a nisu dodate:

- **Decathlon:** uslovi zabranjuju "delimičnu ili totalnu eksploataciju sadržaja" bez pisanog pristanka. Veličine ni stanje
  nisu u HTML-u.
- **Office Shoes:** sitemap je prazan, nema kategoriju trčanja, a pretraga ide preko POST forme.
- **Sport Reality:** domen sportreality.rs je parkiran.
- **Sportland:** sportland.rs je sportski centar, a ne prodavnica. **Intermarket:** intermarket.rs se ne otvara.
- **Ananas** nema kategoriju trčanja. Oglas sa Ananasa ulazi ako ga prepozna katalog ili ako je isti naziv neka druga prodavnica
  vodila pod trčanjem. Zato se model koji prodaje samo Ananas, a nije u katalogu, ne vidi.
- **Nike.rs:** uslovi izričito zabranjuju automatski pristup.

Đak i Planeta Sport se delimično vide preko Ananasa, kada tamo prodaju kao marketplace prodavci. To je samo ponuda na
Ananasu, ne njihov ceo asortiman ni nužno cena na njihovom sajtu.

`NOT VERIFIED` znači da sajt nije proveren, a ne da proizvod ne postoji. Blokade se ne zaobilaze: aplikacija se predstavlja
istim User-Agent-om, poštuje robots.txt i Crawl-delay, ne menja identitet i ne rešava CAPTCHA. Playwright nije potreban jer
svi podržani sajtovi šalju podatke u HTML-u.

## Strane prodavnice

Provereno 2026-11-06. Ovo nije spisak svih cena u inostranstvu, nego izvori kod kojih robots.txt dozvoljava pristup, a u uslovima
nismo pronašli zabranu automatskog čitanja. To je ista siva zona kao kod RunRepeat-a: nije izričito dozvoljeno, samo nije zabranjeno.
Pretraga uslova rađena je po ključnim rečima, a nije pravni savet.

`main.py foreign` otvara samo modele koji su na stanju kod nas. Svaka stranica se upisuje odmah, a stranica pročitana u poslednjih
20 sati se preskače, pa se prekinut sken nastavlja gde je stao. Na HTTP 429 program čeka i usporava, a na drugi uzastopni 429 za
istu stranicu taj izvor staje, sa statusom PARTIAL.

| Izvor | Automatsko čitanje | Razlog |
|---|---|---|
| Top4Running.de | Da, 3 s između stranica, lična upotreba | robots.txt dozvoljava (`Content-Signal` samo zabranjuje AI obuku); cene iz JSON-LD-a. Na 1,5 s je vratio HTTP 429, pa je tempo usporen |
| Bergfreunde | Da, spor tempo, lična upotreba | robots.txt dozvoljava; cene po boji i veličini iz JSON-LD-a; mali izbor patika za trčanje |
| Idealo | Ne | Uslovi izričito zabranjuju automatski pristup i scraping |
| I-Run | Ne | Uslovi zabranjuju izvlačenje sadržaja |
| Runnerinn / Tradeinn | Ne | Pravno obaveštenje traži pisano odobrenje za reprodukovanje sadržaja |
| Geizhals, Alltricks | Ne | Sajt odbija pristup (HTTP 403) |
| Billiger, Heureka, Skroutz, Árukereső, Compari, Intersport.de, ASICS, HOKA | Ne | Naslovna strana odbija pristup (HTTP 403) |

## Ograničenja

- Prvi sken nema istoriju, pa klase zavise samo od poređenja među prodavnicama. Model koji prodaje jedna prodavnica ostaje
  `INSUFFICIENT DATA`.
- Cena je tačna u trenutku skena. Pre kupovine proveri je na linku. Između dnevnih skenova nema nadzora.
- Cena u EU postoji samo za modele koje imaju dva strana izvora, pa prazna kolona ne znači da model nije prodavan u inostranstvu.
  Čitaju se cene sa njihovih sajtova za Nemačku i EU; dostava do Srbije nije proverena.
- Medijana "tržišta" nije potpuno nezavisna: neke prodavnice dele vlasnika ili platformu i često imaju iste cene.
- Popusti po kartici, kuponu i prijavljenom nalogu vide se samo ako ih stranica prikazuje neprijavljenom posetiocu.
- Dostava je informativna i ne ulazi u rang. Kod NBShop sajtova vrednost 0 iz strukturisanih podataka može zavisiti od korpe.
- N Sport i Intersport skoro sve patike vode kao "patike za trčanje". Očigledne lifestyle linije (Dunk, Air Force, VL Court,
  X-Ray...) su isključene listom `NOT_RUNNING_LINES` u `matching/matcher.py`, ali lista nije potpuna, pa u `Svi modeli` može
  ostati poneka patika koja nije za trčanje.
- Ocene postoje samo za modele koje RunRepeat ima. Opaženi modeli nemaju ocenu, a naziv im dolazi od prodavca, pa dva
  različito napisana naziva iste patike mogu ostati odvojena dok ih ne poveže isti SKU.
- Ako sajt promeni HTML, parser može da prestane da nalazi cene. `doctor` proverava pristupnost, a `scan` prijavljuje
  neuspešne stranice.

## Sken na GitHubu (nezavisno od računara)
- Javni repozitorijum `run-marko/patike`: `.github/workflows/dnevni-sken.yml` svako jutro (05:00 UTC) radi sken, cene u inostranstvu, izveštaj i sličice. Javni repozitorijumi nemaju ograničenje minuta.
- Stanje (baza, keš, kurs, oznake, sličice) je `stanje.enc` u izdanju `stanje`, šifrovano lozinkom (`cloud/crypt.mjs`, AES-256-GCM + PBKDF2, samo ugrađeni Node moduli); `python -m cloud.state pack|unpack`.
- Stranica za telefon (https://run-marko.github.io/patike/) se šifruje istom lozinkom (`cloud/encrypt_page.mjs`) i objavljuje na grani `gh-pages`. Nešifrovana stranica i baza nikad nisu javne.
- Jedina tajna je `PAGE_PASSWORD`. Postavlja je i šalje prvu šifrovanu bazu vlasnik: `cloud/postavi-lozinku.ps1` (lozinku kuca sam).
- Ručno: Actions > Dnevni sken > Run workflow; `proba` samo proverava koje prodavnice odgovaraju sa GitHuba.
- Oznake "Nije za trčanje" se na GitHubu ne menjaju; lokalne izmene se prenose ponovnim pokretanjem `postavi-lozinku.ps1`.
