#!/usr/bin/env python
"""Running Shoe Deal Finder Serbia - komandna linija."""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select, text

from analytics.alerts import list_alerts
from analytics.engine import (DealFilters, OfferOptions, apply_msrp_rate, available_sizes, build_offers, compare_model,
                              last_scan_time, latest_retailer_status, rank_deals, resolve_models)
from catalog.loader import MANUAL_FILE, load_catalog, read_import_file, read_json, upsert_entries, validate_entry
from common.config import DEFAULT_DB, DATA_DIR, load_config
from common.fx import load_rate, refresh_rate
from database.models import CatalogModel, Listing, PriceObservation, make_session_factory
from matching.matcher import tokenize
from retailers.registry import build_adapters
from scanner import ScanFilters, fill_missing_images, run_scan

log = logging.getLogger("main")


def setup_logging(verbose: bool) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(DATA_DIR / "app.log", encoding="utf-8")]
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.ERROR)
    handlers.append(console)
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.0f}".replace(",", ".") + " RSD"


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.1f}%"


def parse_sizes(raw: str | None) -> list[float] | None:
    return [float(part.strip().replace(",", ".")) for part in raw.split(",")] if raw else None


def add_filter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--brand")
    parser.add_argument("--model")
    parser.add_argument("--category")
    parser.add_argument("--max-price", type=float)
    parser.add_argument("--sizes", help="EU veličine odvojene zarezom, npr. 45,45.5,46")
    parser.add_argument("--min-discount", type=float, help="minimalni popust koji prodavac tvrdi, u procentima")
    parser.add_argument("--retailer")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--sort", choices=sorted(SORT_KEYS), help="redosled ponuda; podrazumevano klasa, pa skor")
    parser.add_argument("--all-sizes", action="store_true", help="ne filtriraj po željenim veličinama")


def _descending(field: str):
    # Bez vrednosti ide na kraj; to nije isto što i nula.
    return lambda o: (o[field] is None, -(o[field] or 0), o["personal_price"])


SORT_KEYS = {"score": _descending("score"), "discount": _descending("claimed_discount"),
             "review": _descending("review_score"), "price": lambda o: o["personal_price"]}


def sorted_deals(offers: list[dict], args) -> list[dict]:
    return sorted(offers, key=SORT_KEYS[args.sort]) if getattr(args, "sort", None) else offers


@dataclass
class ShownModel:
    id: str
    brand: str
    name: str
    review_score: float | None
    review_source: str | None


def deal_filters(args) -> DealFilters:
    return DealFilters(brand=args.brand, model=args.model, category=args.category, max_price=args.max_price,
                       min_discount=args.min_discount, retailer=args.retailer, all_sizes=args.all_sizes)


def offer_options(args) -> OfferOptions:
    return OfferOptions(sizes=parse_sizes(args.sizes), all_sizes=args.all_sizes)


def print_deals(offers: list[dict], top: int) -> None:
    apply_msrp_rate(offers, load_rate())
    known =[o for o in offers if o["review_score"] is not None][:top]
    unknown = [o for o in offers if o["review_score"] is None][:top]
    if not known and not unknown:
        print("Nema ponuda koje prolaze filtere. Probaj --all-sizes ili smanji minimum_deal_confidence.")
        return

    def row(rank: int, o: dict) -> None:
        tags = o["qualifiers"] + (["unisex"] if o["gender"] == "unisex" else [])
        name = f"{o['brand']} {o['model']}" + (f" ({', '.join(tags)})" if tags else "")
        print(f"{rank:>3}. [{o['classification']}] skor {o['score']} | pouzdanost {o['confidence']} | {name}")
        seller = f" / {o['seller']}" if o["seller"] else ""
        print(f"     {money(o['personal_price'])} kod {o['retailer_name']}{seller} | "
              f"veličine: {', '.join(available_sizes(o)) or 'nepoznato'}"
              f"{' | SKU ' + o['sku'] if o['sku'] else ''}"
              f"{' | još ' + str(o['other_offers']) + ' ponuda ovog modela' if o.get('other_offers') else ''}")
        market = "nema poređenja" if o["market_advantage"] is None else \
            f"{pct(o['market_advantage'])} prema medijani {money(o['market_median'])} ({o['market_retailers']} drugih)"
        history = o["history"]
        history_text = pct(history["advantage"]) if history["advantage"] is not None else "INSUFFICIENT DATA"
        claim = "nema" if o["claimed_discount"] is None else \
            f"{o['claimed_discount'] * 100:.0f}% (tvrdnja prodavca: {o['claim_status']})"
        print(f"     tržište: {market} | istorija: {history_text} | popust: {claim}")
        review = f"{o['review_score']:.0f}/100 ({o['review_source']})" if o["review_score"] is not None else "nema"
        print(f"     ocena: {review} | provereno: {o['observed_at']:%Y-%m-%d %H:%M} UTC")
        if o["msrp_usd"]:
            local = f" = {money(o['msrp_rsd'])}, ova cena {pct(o['price'] / o['msrp_rsd'] - 1)}" if o.get("msrp_rsd") else ""
            print(f"     preporučena cena u SAD: ${o['msrp_usd']:.0f}{local}")
        print(f"     {o['url']}")
        for store in o.get("other_stores") or []:
            sizes = f" (veličine: {', '.join(store['sizes'])})" if store["sizes"] else ""
            print(f"     takođe: {store['retailer_name']} {money(store['price'])}{sizes} {store['url']}")

    for rank, offer in enumerate(known, 1):
        row(rank, offer)
    if unknown:
        print("\nBez nezavisne ocene (niža pouzdanost dokaza):")
        for rank, offer in enumerate(unknown, 1):
            row(rank, offer)


def cmd_scan(args, config, factory) -> int:
    with factory() as session:
        if session.scalar(select(func.count()).select_from(CatalogModel)) == 0:
            load_catalog(session)
        models = session.scalar(select(func.count()).select_from(CatalogModel))
    if not models:
        print("Katalog je prazan. Pokreni: python main.py catalog update")
        return 1
    filters = ScanFilters(brand=args.brand, model=args.model, category=args.category,
                          retailers=[args.retailer] if args.retailer else None,
                          max_pages_per_retailer=args.max_pages)

    def progress(key: str, index: int, total: int) -> None:
        print(f"  {key}: {index}/{total}", flush=True)

    print("Sken je u toku. Svaka prodavnica se čita sporim tempom, pa pun sken traje dugo.")
    summary = run_scan(factory, config, filters, progress=progress)
    print(f"\nSken #{summary.scan_id}: modela u opsegu {summary.models_in_scope}; "
          f"sačuvano oglasa {summary.saved}, upareno {summary.matched}, za ručnu proveru {summary.ambiguous}, "
          f"bez modela {summary.no_match}")
    print("\nProdavnice:")
    for result in sorted(summary.results, key=lambda r: r.retailer):
        print(f"  {result.retailer:<14} {result.status:<13} ok {result.pages_ok}, neuspešno {result.pages_failed}"
              f"{' | ' + result.message if result.message else ''}")
    if summary.new_alerts:
        print(f"\nNovi alarmi ({len(summary.new_alerts)}):")
        for alert in summary.new_alerts:
            print(f"  - {alert.message}")
    with factory() as session:
        offers = build_offers(session, config, offer_options(args))
    print("\nNajbolje ponude:")
    print_deals(sorted_deals(rank_deals(offers, config, deal_filters(args)), args), args.top)
    return 0


def cmd_deals(args, config, factory) -> int:
    with factory() as session:
        offers = build_offers(session, config, offer_options(args))
        scanned = last_scan_time(session)
    if scanned is None:
        print("Još nema skena. Pokreni: python main.py scan")
        return 1
    print(f"Poslednji sken: {scanned:%Y-%m-%d %H:%M} UTC. Ovo nije živa cena; proveri je na linku pre kupovine.\n")
    print_deals(sorted_deals(rank_deals(offers, config, deal_filters(args)), args), args.top)
    return 0


def find_model(session, config, query: str, offers: list[dict]) -> ShownModel | None:
    """Prvo katalog, pa modeli koje prodavnice vode van kataloga. Više pogodaka se ispisuje, bez nagađanja."""
    models = resolve_models(session, query)
    if len(models) == 1:
        m = models[0]
        return ShownModel(m.id, m.brand, m.model_name, m.review_score, m.review_source)
    if not models:
        wanted = tokenize(query)
        seen = {o["model_id"]: o for o in offers if o["catalog_status"] == "OBSERVED"
                and all(t in tokenize(f"{o['brand']} {o['model']}") for t in wanted)}
        if len(seen) == 1:
            model_id, o = next(iter(seen.items()))
            return ShownModel(model_id, o["brand"], o["model"], None, None)
        if not seen:
            print(f"Model '{query}' nije ni u katalogu ni među skeniranim patikama za trčanje.")
            return None
        names = [f"{o['brand']} {o['model']}" for o in seen.values()]
    else:
        names = [f"{m.brand} {m.model_name}" for m in models]
    print("Upit odgovara za više modela, navedi tačnije:")
    for name in sorted(names)[:15]:
        print(f"  {name}")
    return None


def cmd_compare(args, config, factory) -> int:
    with factory() as session:
        offers = build_offers(session, config, OfferOptions(sizes=parse_sizes(args.sizes)))
        model = find_model(session, config, args.query, offers)
        if model is None:
            return 1
        statuses = latest_retailer_status(session)
    print(f"{model.brand} {model.name} | ocena: "
          f"{model.review_score if model.review_score is not None else 'nema'}"
          f"{' (' + model.review_source + ')' if model.review_source else ''}")
    groups = compare_model(offers, statuses, model.id, config)
    if not groups:
        print("Nema zabeleženih ponuda. Prodavnice bez skena se ne računaju kao da model nemaju.")
        return 0
    for group in groups:
        variant = f" | varijanta: {', '.join(group['qualifiers'])}" if group["qualifiers"] else ""
        print(f"\n--- pol: {group['gender']}{variant} ---")
        for offer in group["offers"]:
            seller = f" / {offer['seller']}" if offer["seller"] else ""
            sizes = ", ".join(str(s) for s in offer["matched_sizes"]) or offer["size_status"]
            print(f"  {money(offer['price']):>12}  {offer['retailer_name']}{seller} | SKU {offer['sku'] or '-'} | "
                  f"{offer['availability']} | tvoja veličina: {sizes} | {offer['observed_at']:%Y-%m-%d}")
            print(f"                {offer['url']}")
        cheapest, second = group["cheapest_available"], group["second_cheapest"]
        if cheapest:
            print(f"  Najjeftinije dostupno: {cheapest['retailer_name']} {money(cheapest['price'])}")
        else:
            print("  Nema potvrđeno dostupne ponude.")
        if second:
            print(f"  Sledeća prodavnica: {second['retailer_name']} {money(second['price'])} "
                  f"(razlika {money(group['difference_rsd'])}, {pct(group['difference_pct'])})")
        confirmed = group["cheapest_confirmed_size"]
        print("  Najjeftinije sa potvrđenom veličinom: "
              + (f"{confirmed['retailer_name']} {money(confirmed['personal_price'])}" if confirmed else "nema"))
        print(f"  Provereno prodavnica: {group['retailers_verified']} | imaju model: {group['retailers_carrying']} | "
              f"imaju veličinu: {group['retailers_with_size']}")
        if group["not_verified"]:
            print(f"  NOT VERIFIED (nepoznato, ne znači da nemaju): {', '.join(group['not_verified'])}")
    return 0


def cmd_history(args, config, factory) -> int:
    with factory() as session:
        offers = build_offers(session, config)
        model = find_model(session, config, args.query, offers)
        if model is None:
            return 1
        offers = [o for o in offers if o["model_id"] == model.id]
    print(f"{model.brand} {model.name}")
    if not offers:
        print("Nema zabeleženih opažanja: INSUFFICIENT DATA")
        return 0
    for offer in sorted(offers, key=lambda o: (o["retailer"], o["gender"] or "", o["sku"] or "")):
        history = offer["history"]
        seller = f" / {offer['seller']}" if offer["seller"] else ""
        print(f"\n{offer['retailer_name']}{seller} | pol {offer['gender']} | SKU {offer['sku'] or '-'} | {offer['url']}")
        for point in offer["history_points"]:
            print(f"  {point['at'][:16].replace('T', ' ')}  {money(point['price'])}")
        if history["status"] == "INSUFFICIENT DATA":
            print(f"  Istorija: INSUFFICIENT DATA ({history['observations']} opažanje)")
            continue
        print(f"  Prethodna: {money(history['previous'])} | promena {pct(history['change_pct'])} | "
              f"minimum {money(history['historical_min'])} ({history['historical_min_date']}) | "
              f"opažanja {history['observations']}")
        print(f"  Prosek 7d {money(history['avg_7d'])} ({pct(history['vs_avg_7d_pct'])}) | "
              f"30d {money(history['avg_30d'])} ({pct(history['vs_avg_30d_pct'])}) | "
              f"90d {money(history['avg_90d'])} ({pct(history['vs_avg_90d_pct'])})")
    return 0


def cmd_alerts(args, config, factory) -> int:
    with factory() as session:
        rows = list_alerts(session, unseen_only=not args.all)
    if not rows:
        print("Nema novih alarma." if not args.all else "Nema alarma.")
        return 0
    for alert in rows:
        print(f"{alert.created_at:%Y-%m-%d %H:%M} [{alert.rule_type}] {alert.message}")
    return 0


def cmd_thumbs(args, config, factory) -> int:
    from dashboard.thumbs import fetch
    from reports.export import OUTPUT_DIR

    offers = json.loads((OUTPUT_DIR / "offers.json").read_text(encoding="utf-8"))["offers"]
    stats = fetch([o.get("image_url") for o in offers if o.get("availability") == "AVAILABLE"])
    print(f"sličice: {stats['new']} novih, {stats['failed']} neuspešnih")
    return cmd_report(args, config, factory)


def cmd_report(args, config, factory) -> int:
    from reports.export import write_reports

    refresh_rate()
    with factory() as session:
        paths = write_reports(session, config)
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


def cmd_dashboard(args, config, factory) -> int:
    from dashboard.build import serve
    from reports.export import OUTPUT_DIR, write_reports

    refresh_rate()
    with factory() as session:
        write_reports(session, config)
    serve(OUTPUT_DIR, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_review(args, config, factory) -> int:
    with factory() as session:
        rows = list(session.scalars(select(Listing).where(Listing.match_status == args.status)
                                    .order_by(Listing.retailer, Listing.title)))
    print(f"{args.status}: {len(rows)} oglasa")
    for row in rows[:args.limit]:
        print(f"[{row.retailer}] {row.title}\n    {row.match_reason}\n    {row.url}")
    return 0


def cmd_rematch(args, config, factory) -> int:
    from matching.matcher import CatalogEntry, Matcher
    from pricing.store import rematch_all

    with factory() as session:
        load_catalog(session)
        models = session.scalars(select(CatalogModel))
        matcher = Matcher([CatalogEntry(m.id, m.brand, m.model_name) for m in models])
        print(f"Ponovo upareno bez novih zahteva: {rematch_all(session, matcher)}")
    return 0


def cmd_images(args, config, factory) -> int:
    print("Tražim slike za ponude na stanju koje ih nemaju. Cene se ne menjaju.")
    counts = fill_missing_images(factory, config, progress=lambda key, done, total: print(f"  {key}: {done}/{total}"))
    for retailer, row in sorted(counts.items()):
        print(f"{retailer}: pronađeno {row['found']}, bez slike ili neuspelo {row['failed']}")
    if not counts:
        print("Sve ponude na stanju već imaju sliku.")
    return 0


def cmd_foreign(args, config, factory) -> int:
    from foreign.scan import run_foreign_scan

    with factory() as session:
        load_catalog(session)
    print("Čitam cene iz stranih prodavnica sporim tempom. One ne utiču na skor srpskih ponuda.")
    results = run_foreign_scan(factory, config, max_pages=args.max_pages,
                               progress=lambda key, done, total: print(f"  {key}: {done}/{total}"))
    for result, saved in sorted(results, key=lambda r: r[0].retailer):
        print(f"{result.retailer}: {result.status} | stranica {result.pages_ok}, neuspelih {result.pages_failed}, "
              f"upisano {saved}" + (f" | {result.message}" if result.message else ""))
    return 0


def cmd_catalog(args, config, factory) -> int:
    if args.action == "update":
        from catalog.update import update_seed

        print("Čitam RunRepeat sporim tempom. Prvi put traje dugo; kasnije se detalji čitaju samo za nove modele.")
        result = update_seed(delay_s=config["scan"]["default_delay_s"], full=args.full)
        print(f"Na listi: {result['listed']}, novih: {result['new']}, ukupno: {result['total']}, "
              f"grešaka: {len(result['failures'])}")
        for failure in result["failures"][:20]:
            print("  -", failure)
    elif args.action == "import":
        entries = read_import_file(Path(args.file))
        bad = [(e, validate_entry(e)) for e in entries if validate_entry(e)]
        if bad:
            print("Uvoz je odbijen. Svaki unos mora imati brend, model i proverljiv izvorni URL:")
            for entry, problems in bad[:20]:
                print(f"  {entry.get('brand')} {entry.get('model_name')}: {'; '.join(problems)}")
            return 1
        existing = {(e["brand"], e["model_name"]): e for e in read_json(MANUAL_FILE)}
        existing.update({(e["brand"], e["model_name"]): e for e in entries})
        import json
        MANUAL_FILE.write_text(json.dumps(list(existing.values()), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Uvezeno {len(entries)} unosa u {MANUAL_FILE.name}")
    with factory() as session:
        count = load_catalog(session)
        if args.action == "show":
            models = list(session.scalars(select(CatalogModel).order_by(CatalogModel.brand, CatalogModel.model_name)))
            for model in models:
                if args.brand and model.brand.lower() != args.brand.lower():
                    continue
                print(f"{model.brand} {model.model_name} | {model.surface or '?'} | "
                      f"{','.join(model.categories or []) or '?'} | ocena {model.review_score} | {model.source_url}")
    print(f"Katalog u bazi: {count} unosa")
    return 0


def cmd_doctor(args, config, factory) -> int:
    problems = 0
    print(f"Python {sys.version.split()[0]}" + ("" if sys.version_info >= (3, 11) else "  PROBLEM: treba 3.11+"))
    problems += sys.version_info < (3, 11)
    for name in ("httpx", "bs4", "lxml", "sqlalchemy", "yaml"):
        try:
            __import__(name)
            print(f"  zavisnost {name}: OK")
        except ImportError:
            print(f"  zavisnost {name}: NEDOSTAJE")
            problems += 1
    try:
        with factory() as session:
            session.execute(text("SELECT 1"))
            models = session.scalar(select(func.count()).select_from(CatalogModel))
            observations = session.scalar(select(func.count()).select_from(PriceObservation))
            triggers = session.scalar(text("SELECT count(*) FROM sqlite_master WHERE type='trigger' "
                                           "AND name LIKE 'price_obs_no_%'"))
        print(f"Baza: OK | modela {models} | opažanja {observations} | zaštita od izmene: "
              f"{'OK' if triggers == 2 else 'NEDOSTAJE'}")
        problems += triggers != 2
    except Exception as exc:
        print(f"Baza: GREŠKA {exc}")
        problems += 1
    print("Prodavnice (živi test):")
    for adapter in build_adapters(config):
        status, message = adapter.check()
        print(f"  {adapter.key:<14} {status:<13} {message}")
    print(f"Greške u okruženju: {problems}")
    return 0 if problems == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Running Shoe Deal Finder Serbia")
    parser.add_argument("--config", help="putanja do config.yaml")
    parser.add_argument("--db", help="putanja do SQLite baze")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="osvežava cene i upisuje opažanja")
    add_filter_args(scan)
    scan.add_argument("--max-pages", type=int, help="ograničenje broja stranica po prodavnici")
    scan.set_defaults(func=cmd_scan)

    deals = sub.add_parser("deals", help="najbolje ponude")
    add_filter_args(deals)
    deals.set_defaults(func=cmd_deals)

    compare = sub.add_parser("compare", help="cene po prodavnicama")
    compare.add_argument("query")
    compare.add_argument("--sizes")
    compare.set_defaults(func=cmd_compare)

    history = sub.add_parser("history", help="zabeležene cene")
    history.add_argument("query")
    history.set_defaults(func=cmd_history)

    alerts = sub.add_parser("alerts", help="prikaz alarma")
    alerts.add_argument("--all", action="store_true")
    alerts.set_defaults(func=cmd_alerts)

    report = sub.add_parser("report", help="generiše HTML, CSV i JSON izveštaj")
    report.set_defaults(func=cmd_report)

    thumbs = sub.add_parser("thumbs", help="preuzima sličice koje fale za artifakt, pa ponovo pravi izveštaj")
    thumbs.set_defaults(func=cmd_thumbs)

    dashboard = sub.add_parser("dashboard", help="generiše i otvara lokalni dashboard")
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.add_argument("--no-browser", action="store_true")
    dashboard.set_defaults(func=cmd_dashboard)

    review = sub.add_parser("review", help="oglasi za ručnu proveru uparivanja")
    review.add_argument("--status", default="AMBIGUOUS", choices=["AMBIGUOUS", "NO_MATCH"])
    review.add_argument("--limit", type=int, default=100)
    review.set_defaults(func=cmd_review)

    rematch = sub.add_parser("rematch", help="ponovo uparuje sačuvane oglase, npr. posle dopune kataloga")
    rematch.set_defaults(func=cmd_rematch)

    images = sub.add_parser("images", help="dopunjuje slike ponuda na stanju, bez novih cena")
    images.set_defaults(func=cmd_images)

    foreign = sub.add_parser("foreign", help="osvežava cene iz stranih prodavnica")
    foreign.add_argument("--max-pages", type=int, help="ograničenje broja stranica po prodavnici")
    foreign.set_defaults(func=cmd_foreign)

    catalog = sub.add_parser("catalog", help="nezavisni katalog")
    catalog.add_argument("action", choices=["update", "load", "show", "import"])
    catalog.add_argument("file", nargs="?", help="JSON/CSV za import")
    catalog.add_argument("--full", action="store_true", help="ponovo pročitaj detalje svih modela")
    catalog.add_argument("--brand")
    catalog.set_defaults(func=cmd_catalog)

    doctor = sub.add_parser("doctor", help="provera baze, adaptera, zavisnosti i konekcije")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def apply_overrides(args, config: dict) -> dict:
    config = copy.deepcopy(config)
    sizes = parse_sizes(getattr(args, "sizes", None))
    if sizes:
        config["preferred_eu_sizes"] = sizes
        config["filter_preferred_size"] = True  # izričito tražene veličine se uvek poštuju
    return config


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    config = apply_overrides(args, load_config(args.config))
    factory = make_session_factory(args.db or DEFAULT_DB)
    return args.func(args, config, factory)


if __name__ == "__main__":
    raise SystemExit(main())
