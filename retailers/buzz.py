from retailers.nbshop import NBShopAdapter


class BuzzAdapter(NBShopAdapter):
    """Pronađen tokom implementacije; prodaje i trkačke modele (npr. Hoka)."""
    key = "buzz"
    name = "Buzz Sneakers"
    base_url = "https://www.buzzsneakers.rs"
    sitemap_urls = ["https://www.buzzsneakers.rs/files/sitemap/SRB_rs/product.xml"]
    running_category_urls = ["https://www.buzzsneakers.rs/proizvodi/buzz-running-offer-men/",
                             "https://www.buzzsneakers.rs/proizvodi/buzz-running-offer-women/"]

    def is_shoe_url(self, url: str) -> bool:
        return "/patike/" in url.lower()
