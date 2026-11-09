from retailers.base import RetailerAdapter


class PlanetaSportAdapter(RetailerAdapter):
    """Cloudflare vraća 'you have been blocked' za iskren User-Agent (provereno 2026-11-05).

    Blokada se ne zaobilazi, pa adapter uvek prijavljuje NOT VERIFIED. To NE znači da prodavac nema proizvod.
    """
    key = "planetasport"
    name = "Planeta Sport"
    base_url = "https://planetasport.rs"
    platform = "Magento (iza Cloudflare zaštite)"
    supported = False
    note = "Sajt blokira automatski pristup (Cloudflare 403); blokada se ne zaobilazi"
