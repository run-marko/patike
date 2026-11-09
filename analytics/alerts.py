"""Lokalni alarmi iz liste praćenja. Prikazuju se na zahtev ili posle skena; nema pozadinskog nadzora."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.deal import rsd
from analytics.engine import resolve_models
from database.models import Alert, utcnow


def _add(session: Session, created: list[Alert], key: str, rule: str, model_id: str, listing_id: str | None,
         message: str) -> None:
    exists = session.scalars(select(Alert).where(Alert.dedupe_key == key)).first()
    if exists:
        return
    alert = Alert(dedupe_key=key, rule_type=rule, model_id=model_id, listing_id=listing_id, message=message,
                  created_at=utcnow())
    session.add(alert)
    created.append(alert)


def _describe(offer: dict) -> str:
    return (f"{offer['brand']} {offer['model']} kod {offer['retailer_name']}: "
            f"{rsd(offer['personal_price'])} ({offer['url']})")


def evaluate_alerts(session: Session, config: dict, offers: list[dict]) -> list[Alert]:
    created: list[Alert] = []
    gender = config.get("gender", "men")
    for rule in config.get("watchlist") or []:
        rule_type = rule.get("type")
        models = resolve_models(session, rule.get("model", ""))
        for model in models[:1]:
            pool = [o for o in offers if o["model_id"] == model.id and o["gender"] in (gender, "unisex")
                    and not o["qualifiers"] and not o["stale"] and o["price_verified"]]
            purchasable = [o for o in pool if o["availability"] == "AVAILABLE"]
            confirmed = [o for o in purchasable if o["size_status"] == "CONFIRMED"]

            if rule_type == "below_price":
                for offer in confirmed:
                    if offer["personal_price"] < rule["target_rsd"]:
                        _add(session, created, f"below|{offer['listing_id']}|{rule['target_rsd']}|"
                                               f"{offer['personal_price']:.0f}",
                             rule_type, model.id, offer["listing_id"],
                             f"Ispod ciljne cene {rsd(rule['target_rsd'])}: {_describe(offer)}")
            elif rule_type == "pct_drop":
                for offer in purchasable:
                    change = offer["history"]["change_pct"]
                    if change is not None and change <= -abs(rule["percent"]) / 100:
                        _add(session, created, f"drop|{offer['listing_id']}|{offer['history']['previous']}|"
                                               f"{offer['price']}",
                             rule_type, model.id, offer["listing_id"],
                             f"Pad cene {change * 100:.1f}% od poslednjeg opažanja: {_describe(offer)}")
            elif rule_type == "historical_min":
                for offer in purchasable:
                    history = offer["history"]
                    enough = history["observations"] >= config["history"]["min_observations_for_average"]
                    earlier_min = min((p["price"] for p in offer["history_points"][:-1]), default=None)
                    if enough and earlier_min is not None and offer["price"] < earlier_min:
                        _add(session, created, f"min|{offer['listing_id']}|{offer['price']}",
                             rule_type, model.id, offer["listing_id"],
                             f"Istorijski minimum (ranije {rsd(earlier_min)}): {_describe(offer)}")
            elif rule_type == "size_available":
                for offer in confirmed:
                    sizes = ",".join(sorted(str(s) for s in offer["matched_sizes"]))
                    _add(session, created, f"size|{offer['listing_id']}|{sizes}", rule_type, model.id,
                         offer["listing_id"], f"Dostupne željene veličine {sizes}: {_describe(offer)}")
            elif rule_type == "cheapest_confirmed":
                if confirmed:
                    best = min(confirmed, key=lambda o: o["personal_price"])
                    retailers = {o["retailer"] for o in confirmed}
                    if len(retailers) >= 2:
                        _add(session, created, f"cheapest|{model.id}|{best['listing_id']}|"
                                               f"{best['personal_price']:.0f}",
                             rule_type, model.id, best["listing_id"],
                             f"Najjeftinija potvrđena ponuda među {len(retailers)} prodavaca: {_describe(best)}")
    session.commit()
    return created


def list_alerts(session: Session, unseen_only: bool = False, mark_seen: bool = True) -> list[Alert]:
    query = select(Alert).order_by(Alert.created_at.desc())
    if unseen_only:
        query = query.where(Alert.seen.is_(False))
    rows = list(session.scalars(query))
    if mark_seen:
        for row in rows:
            row.seen = True
        session.commit()
    return rows
