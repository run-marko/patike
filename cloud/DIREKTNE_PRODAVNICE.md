# Đak i Planeta u istom skenu

Sakupljač na tvom Windows računaru čita isključivo `www.djaksport.com` i
`planetasport.rs` postojećim adapterima, bez Ananasa ili posredničkih cena.
GitHub i dalje obrađuje ostale prodavnice i objavljuje isti dashboard.

## Prvo pokretanje

1. Ažuriraj lokalni folder projekta (`git pull` na grani `main`), ili preuzmi novi ZIP repozitorijuma i raspakuj ga.
2. Dvoklik na `cloud/pokreni-direktne-prodavnice.cmd`.
3. Pokretač prvo proverava pristup sa tvog računara. Ako dobije blokadu, neće tvrditi da sakupljanje radi.
4. Ako nedostaju Node.js ili GitHub CLI, prikazaće njihove `winget` komande. Python 3.12 je isti zahtev kao za postojeću aplikaciju.
5. Posle uspešne provere unesi GitHub ključ i postojeću lozinku dashboarda u lokalni prozor. GitHub fine-grained ključ mora biti ograničen na `run-marko/patike`, sa **Contents: Read and write** i **Actions: Read-only**. Ključ za dugme na dashboardu ima drugačije dozvole i nije dovoljan za slanje rezultata.
6. Čuvanje pristupa je opciono. Ako ga potvrdiš, Windows DPAPI šifruje podatke za tvoj Windows nalog, u `%LOCALAPPDATA%/PatikeCollector/credentials.dpapi`. Ne ulaze u Git, izveštaje ili javni dnevnik skena.
7. Kada piše da je sakupljač povezan, ostavi prozor otvoren. Koristi postojeće dugme **Pokreni sken**, ili sačekaj redovni jutarnji sken.

Računar i ovaj prozor moraju biti uključeni kada počinje sken. To je stvarno ograničenje ove metode. Posle restarta ponovo otvori isti CMD; ako je pristup zapamćen, nema ponovnog unosa. Zatvaranje prozora ili Ctrl+C zaustavlja sakupljač.

## Kako podaci stižu u dashboard

- Sakupljač jednom u minutu objavi samo signal da radi.
- GitHub sken proverava da li je signal svež. Ako jeste, šalje zahtev sa jedinstvenim ID-em skena.
- Lokalni sakupljač prihvata samo pravi aktivni sken repozitorijuma na grani `main`. Ne izvršava naredbe ili kod iz zahteva.
- Đak i Planeta se čitaju paralelno; unutar prodavnice ostaju postojeće pauze, pravila robots.txt i prekid na blokadi.
- Rezultati sadrže direktni URL, cenu, dostupne brojeve i status očitavanja. Šifruju se postojećim AES-GCM postupkom.
- GitHub prihvata samo rezultat odgovarajućeg skena, ispravnu šifru i URL-ove odgovarajuće prodavnice. Marketplace prodavci nisu prihvaćeni.
- Rezultati prolaze kroz isti upis u bazu, uparivanje, istoriju, rangiranje i izveštaj kao ostale prodavnice. Ne kopira se niti zamenjuje cela lokalna baza.
- Zahtev i šifrovani odgovor se uklanjaju kada se razmena završi. Glavno `stanje.enc` i istorija ostaju u postojećem postupku.

Ako lokalni sakupljač nije povezan, ostale prodavnice nastavljaju postojeći sken, a dnevnik jasno kaže da lokalno očitavanje nije povezano. Ako rezultat ne stigne u roku od 90 minuta, Đak i Planeta dobijaju `NOT_VERIFIED`, bez izmišljenih cena. Lokalna blokada se ne zaobilazi; za takav slučaj ova metoda ne daje potvrđene podatke.

Ovo je sakupljač podataka, a ne GitHub self-hosted runner. Javni repozitorijum ne dobija mogućnost da pokreće proizvoljne komande na tvom računaru.

## Provera bez povezivanja

Iz postojećeg Python okruženja:

```powershell
.\.venv\Scripts\python.exe -m cloud.direct_collector probe
```

Ova komanda radi ograničenu probu direktnih sajtova i ne menja objavljenu bazu.

## Brisanje zapamćenog pristupa

Zatvori sakupljač i obriši `%LOCALAPPDATA%/PatikeCollector/credentials.dpapi`.
Za trajni prestanak pristupa opozovi njegov zaseban GitHub ključ u GitHub podešavanjima. Ostali skenovi i ključ dugmeta na dashboardu ostaju nezavisni.
