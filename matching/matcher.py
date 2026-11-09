"""Uparivanje oglasa sa nezavisnim katalogom. Greška na stranu opreznosti: bolje u ručnu proveru nego spajanje."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ђ": "dj", "е": "e", "ж": "z", "з": "z", "и": "i", "ј": "j",
    "к": "k", "л": "l", "љ": "lj", "м": "m", "н": "n", "њ": "nj", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "ћ": "c", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "c", "џ": "dz", "ш": "s",
}
BRAND_ALIASES = {
    "asics": "ASICS", "nike": "Nike", "adidas": "Adidas", "new balance": "New Balance", "newbalance": "New Balance",
    "nb": "New Balance", "hoka": "Hoka", "hoka one one": "Hoka", "puma": "Puma", "saucony": "Saucony",
    "brooks": "Brooks", "mizuno": "Mizuno", "on": "On", "on running": "On", "skechers": "Skechers",
    "361": "361 Degrees", "361 degrees": "361 Degrees", "361°": "361 Degrees", "altra": "Altra",
    "salomon": "Salomon", "reebok": "Reebok", "under armour": "Under Armour", "merrell": "Merrell",
    "topo": "Topo Athletic", "topo athletic": "Topo Athletic", "diadora": "Diadora", "joma": "Joma",
    "ua": "Under Armour",
}
# Reči koje na srpskim sajtovima opisuju proizvod, a nisu deo naziva modela.
NOISE = set("""patike patika obuca muske zenske muski zenski musko zensko muskarci zene unisex za trcanje trcanj tracanje trening
sportske sport running shoes shoe m w u men women mens womens wns wmns""".split())
COLORS = set("""crna crne crni bela bele beli siva sive sivi svetlosiva svetlosive tamnosiva tamnosive plava plave plavi
svetloplava svetloplave tamnoplava tamnoplave crvena crvene crveni zelena zelene zeleni zuta zute zuti narandzasta
narandzaste roze ljubicasta ljubicaste braon bez krem ciklama limeta zlatna zlatne srebrna srebrne bordo teget tirkizna
tirkizne koral boje boja multikolor multicolor crno belo sivo plavo zeleno crveno zuto maslina maslinaste maslinasta
svetlozelene svetlozelena tamnozelene tamnozelena""".split())
# Šifra proizvođača u naslovu ("HV9272 002") ne menja model.
PRODUCT_CODE = re.compile(r"[a-z]{1,3}\d{4,}|\d{3}")
KIDS = set("decije deca decu decak decake devojcice djecje jr junior youth gs ps td inf kids kid".split())
# Adidas dečje oznake: "EL I", "EL C", "J", "K" na kraju naziva.
ADIDAS_KIDS = {"el", "j", "k", "c", "i"}
PREFIX_PHRASES = [("fresh", "foam", "x"), ("fresh", "foam"), ("fuelcell",), ("air", "zoom"), ("zoom", "x"), ("zoomx",),
                  ("air",), ("zoom",), ("zm",), ("react", "x"), ("reactx",), ("react",), ("adizero",)]
# Tokeni koji menjaju proizvod: bez kataloškog modela sa tim tokenom oglas nije taj model.
DIFFERENT_MODEL = set("""trail tr pro elite max plus lite ls utl sl evo speed tempo sp next nitro x flyknit tokyo
premium element edge rc ultra""".split())
# Tokeni koji ostavljaju isti model, ali drugačiji proizvod: nikad se ne porede sa osnovnim oglasom.
VARIANT_QUALIFIERS = {
    "gtx": "GTX", "goretex": "GTX", "gore": "GTX", "wide": "WIDE", "2e": "WIDE", "4e": "XWIDE", "se": "SE",
    "shield": "SHIELD", "flyease": "FLYEASE", "wp": "WP", "waterproof": "WP",
}


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    low = "".join(CYRILLIC.get(ch, ch) for ch in text.lower())
    low = low.replace("đ", "dj")
    low = unicodedata.normalize("NFKD", low)
    low = "".join(ch for ch in low if not unicodedata.combining(ch))
    return low


def tokenize(text: str | None) -> list[str]:
    low = normalize_text(text)
    low = re.sub(r"gore[\s-]?tex", "gtx", low)
    # 1080v14 -> 1080 14 | v5 -> 5 | sl2 ostaje
    low = re.sub(r"(\d{3,4})v(\d+)", r"\1 \2", low)
    low = re.sub(r"\b(\d+)\.0\b", r"\1", low)  # Runfalcon 3.0 -> Runfalcon 3
    low = re.sub(r"\bpatike(?=[a-z])", "", low)  # Patikepuma Trinity 2 -> puma Trinity 2
    low = re.sub(r"\b([a-z]{4,})(\d{1,2})(gtx)?\b", r"\1 \2 \3", low)  # Arahi9 -> Arahi 9 | Clifton11gtx -> Clifton 11 gtx
    tokens = re.findall(r"[a-z0-9]+", low)
    return [t[1:] if re.fullmatch(r"v\d+", t) else t for t in tokens]


def normalize_brand(brand: str | None) -> str | None:
    if not brand:
        return None
    key = " ".join(tokenize(brand))
    if key.startswith("adidas"):  # npr. "adidas Originals", "Adidas Performance"
        return "Adidas"
    return BRAND_ALIASES.get(key, brand.strip())


def strip_prefixes(tokens: list[str]) -> list[str]:
    out = list(tokens)
    changed = True
    while changed:
        changed = False
        for phrase in PREFIX_PHRASES:
            n = len(phrase)
            if tuple(out[:n]) == phrase and len(out) > n:
                out = out[n:]
                changed = True
    return out


def model_core(model_name: str) -> list[str]:
    return strip_prefixes(tokenize(model_name))


def _adidas_kids_marker(tokens: list[str]) -> bool:
    """'EL' bilo gde; J, K, C, I samo na kraju naziva, jer je 'i' u sredini naslova običan veznik."""
    if "el" in tokens:
        return True
    words = [t for t in tokens if t not in COLORS]
    return bool(words) and words[-1] in ADIDAS_KIDS


def _find(needle: list[str], haystack: list[str]) -> int:
    for i in range(len(haystack) - len(needle) + 1):
        if haystack[i:i + len(needle)] == needle:
            return i
    return -1


@dataclass
class Match:
    status: str  # MATCHED | AMBIGUOUS | NO_MATCH | EXCLUDED
    model_id: str | None = None
    confidence: str = "none"
    reason: str = ""
    qualifiers: list[str] = field(default_factory=list)


@dataclass
class CatalogEntry:
    id: str
    brand: str
    model_name: str

    @property
    def core(self) -> list[str]:
        return model_core(self.model_name)


class Matcher:
    def __init__(self, entries: list[CatalogEntry]):
        self.by_brand: dict[str, list[CatalogEntry]] = {}
        for entry in entries:
            key = normalize_brand(entry.brand) or entry.brand
            self.by_brand.setdefault(key.lower(), []).append(entry)
        for group in self.by_brand.values():
            group.sort(key=lambda e: len(e.core), reverse=True)

    def candidates_in_slug(self, slug: str, entries: list[CatalogEntry] | None = None) -> bool:
        """Da li URL verovatno pripada nekom modelu iz kataloga (brend + naziv). Bira šta se preuzima."""
        tokens = tokenize(slug)
        for brand_key, group in self.by_brand.items():
            if not all(t in tokens for t in tokenize(brand_key)):
                continue
            for entry in group:
                core = entry.core
                if core and _find(core, tokens) >= 0:
                    return True
                stem = core[:-1] if core and core[-1].isdigit() else None
                if stem and _find(stem, tokens) >= 0:
                    return True
        return False

    def match(self, brand: str | None, title: str, description: str = "") -> Match:
        canonical = normalize_brand(brand)
        tokens = tokenize(title)
        if any(t in KIDS for t in tokens) or (canonical == "Adidas" and _adidas_kids_marker(tokens)):
            return Match("EXCLUDED", reason="dečji model")
        group = self.by_brand.get((canonical or "").lower())
        if not group:
            return Match("NO_MATCH", reason=f"brend '{brand}' nije u katalogu")
        # Svi nazivi istog brenda: "NB", "Hoka One One"...
        brand_tokens = set(tokenize(canonical))
        for alias, target in BRAND_ALIASES.items():
            if target == canonical:
                brand_tokens.update(tokenize(alias))
        clean = [t for t in tokens if t not in NOISE and t not in brand_tokens]
        clean = strip_prefixes(clean)

        hits: list[tuple[CatalogEntry, int]] = []
        for entry in group:
            position = _find(entry.core, clean)
            if position >= 0:
                hits.append((entry, position))
        if not hits:
            return self._no_exact(group, clean, title)

        longest = len(hits[0][0].core)
        best = [h for h in hits if len(h[0].core) == longest]
        if len(best) > 1:
            names = ", ".join(h[0].model_name for h in best)
            return Match("AMBIGUOUS", reason=f"više modela iste dužine: {names}")
        entry, position = best[0]
        core = entry.core
        before, after = clean[:position], clean[position + len(core):]
        extras = before + after
        # "Duramo SL2" nije "Adizero SL2": reč ispred naziva modela znači da je to verovatno drugi model.
        if before:
            return Match("AMBIGUOUS", reason=f"reč '{before[-1]}' ispred naziva menja model: {title}")

        following = clean[position + len(core)] if position + len(core) < len(clean) else None
        if following and following.isdigit() and not core[-1].isdigit():
            return Match("AMBIGUOUS", reason=f"generacija '{following}' nije u katalogu: {title}")

        different = [t for t in extras if t in DIFFERENT_MODEL]
        if different:
            return Match("AMBIGUOUS", reason=f"token '{different[0]}' menja model, a takav model nije u katalogu: "
                                             f"{title}")
        unknown = [t for t in extras if t not in VARIANT_QUALIFIERS and t not in COLORS and t != "tex"
                   and not PRODUCT_CODE.fullmatch(t)]
        if unknown:
            return Match("AMBIGUOUS", reason=f"nepoznat dodatak '{' '.join(unknown)}' u naslovu: {title}")
        qualifiers = sorted({VARIANT_QUALIFIERS[t] for t in extras if t in VARIANT_QUALIFIERS})
        for token in tokenize(description):
            if token in ("gtx", "goretex") and "GTX" not in qualifiers and "gtx" not in core:
                qualifiers.append("GTX")
        confidence = "high" if core[-1].isdigit() or len(core) >= 2 else "medium"
        reason = f"tačno poklapanje naziva: {entry.brand} {entry.model_name}"
        if qualifiers:
            reason += f" (varijanta: {', '.join(qualifiers)})"
        return Match("MATCHED", entry.id, confidence, reason, qualifiers)

    def _no_exact(self, group: list[CatalogEntry], clean: list[str], title: str) -> Match:
        """Naslov bez broja generacije ili sa nepoznatom generacijom ide u ručnu proveru."""
        stems = {}
        for entry in group:
            core = entry.core
            if core and core[-1].isdigit() and len(core) >= 2 and _find(core[:-1], clean) >= 0:
                stems.setdefault(" ".join(core[:-1]), []).append(entry.model_name)
        if stems:
            stem, names = max(stems.items(), key=lambda kv: len(kv[0]))
            return Match("AMBIGUOUS", reason=f"generacija nije u katalogu ili nije navedena; poznate: "
                                             f"{', '.join(sorted(names)[:5])} | oglas: {title}")
        return Match("NO_MATCH", reason=f"nema modela u katalogu: {title}")


@dataclass
class ObservedModel:
    """Model koji prodavac vodi pod trčanjem, a nije u nezavisnom katalogu. Nema ocenu i ne spaja se sa katalogom."""
    id: str
    brand: str
    name: str
    surface: str | None
    qualifiers: list[str]


OBSERVED_TRAIL = {"trail", "tr", "atr", "terrex", "hiker", "hiking"}
# N Sport i Intersport sve patike vode pod "patike za trčanje", pa oznaka prodavca nije dovoljna za ove lifestyle linije.
NOT_RUNNING_LINES = re.compile(r"\b(?:dunk|air force|blazer|cortez|jordan|vl court|court legend|breaknet|samba|gazelle"
                               r"|campus|superstar|stan smith|x plrpath|x ray|st runner|anzarun|r78|retro runner"
                               r"|tensaur|run ?[5-8]0s|dropset)\b")


def observed_model(brand: str | None, title: str) -> ObservedModel | None:
    """Identitet iz naslova. Svi tokeni naziva ostaju, pa se generacije i podmodeli nikad ne spajaju."""
    canonical = normalize_brand(brand) or "Nepoznat brend"
    brand_tokens = set(tokenize(canonical))
    for alias, target in BRAND_ALIASES.items():
        if target == canonical:
            brand_tokens.update(tokenize(alias))
    words = [t for t in tokenize(title) if t not in NOISE and t not in brand_tokens and t not in COLORS
             and t != "tex" and not PRODUCT_CODE.fullmatch(t)]
    if NOT_RUNNING_LINES.search(" ".join(words)):
        return None
    qualifiers = sorted({VARIANT_QUALIFIERS[t] for t in words if t in VARIANT_QUALIFIERS})
    core = strip_prefixes([t for t in words if t not in VARIANT_QUALIFIERS])
    if not core:
        return None
    name = " ".join(t.upper() if len(t) <= 3 and t.isalpha() else t.capitalize() for t in core)
    slug = "-".join(tokenize(canonical) + core)
    surface = "trail" if OBSERVED_TRAIL & set(words) else None
    return ObservedModel(f"observed-{slug}", canonical, name, surface, qualifiers)


def detect_gender(title: str, url: str = "", description: str = "", sku: str | None = None,
                  brand: str | None = None, explicit: str | None = None) -> tuple[str, str]:
    """Vraća (men|women|unisex|kids|unknown, izvor)."""
    if explicit:
        low = normalize_text(explicit).strip()
        low = low[3:] if low.startswith("za ") else low  # "Za muškarce", "Za žene", "Za decu"
        if low.startswith("mus"):
            return "men", "atribut prodavca"
        if low.startswith("zen"):
            return "women", "atribut prodavca"
        if low.startswith("unisex"):
            return "unisex", "atribut prodavca"
        if low.startswith(("dec", "dje")):
            return "kids", "atribut prodavca"
    if sku and normalize_brand(brand) == "ASICS":
        if sku.startswith("1011"):
            return "men", "ASICS SKU 1011"
        if sku.startswith("1012"):
            return "women", "ASICS SKU 1012"
        if sku.startswith("1014"):
            return "kids", "ASICS SKU 1014 (GS)"
    for source, text in (("naslov", title), ("URL", url)):
        tokens = tokenize(text)
        if any(t in KIDS for t in tokens):
            return "kids", source
        if {"muske", "muski", "musko", "men", "mens"} & set(tokens):
            return "men", source
        if {"zenske", "zenski", "zensko", "women", "womens", "wns", "wmns"} & set(tokens):
            return "women", source
        if "unisex" in tokens:
            return "unisex", source
    title_tokens = tokenize(title)
    if title_tokens and title_tokens[-1] == "m":
        return "men", "naslov (sufiks M)"
    if title_tokens and title_tokens[-1] == "w":
        return "women", "naslov (sufiks W)"
    desc = set(tokenize(description)[:60])
    if {"muske", "muski", "musku", "muskarce"} & desc and not {"zenske", "zenski"} & desc:
        return "men", "opis"
    if {"zenske", "zenski", "zene"} & desc and not {"muske", "muski"} & desc:
        return "women", "opis"
    if "unisex" in desc or "uniseks" in desc:
        return "unisex", "opis"
    return "unknown", "nije navedeno"


def url_is_other_gender(url: str, profile_gender: str) -> bool:
    tokens = set(tokenize(url.rsplit("/", 1)[-1] if not url.rstrip("/").split("/")[-1].isdigit() else url))
    if profile_gender == "men":
        return bool({"zenske", "zenski", "wns"} & tokens) or url.rstrip("/").endswith(("-w", "-wns"))
    return bool({"muske", "muski"} & tokens) or url.rstrip("/").endswith("-m")
