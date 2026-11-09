from retailers.base import RetailerAdapter


class DjakSportAdapter(RetailerAdapter):
    """Cloudflare vraća 'you have been blocked', uključujući sitemap i feed (provereno 2026-11-05).

    Blokada se ne zaobilazi, pa adapter uvek prijavljuje NOT VERIFIED. To NE znači da prodavac nema proizvod.
    """
    key = "djaksport"
    name = "Đak Sport"
    base_url = "https://www.djaksport.com"
    platform = "Magento (iza Cloudflare zaštite)"
    supported = False
    note = "Sajt blokira automatski pristup (Cloudflare 403); blokada se ne zaobilazi"
