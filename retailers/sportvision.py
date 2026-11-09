from retailers.nbshop import NBShopAdapter


class SportVisionAdapter(NBShopAdapter):
    key = "sportvision"
    name = "Sport Vision"
    base_url = "https://www.sportvision.rs"
    sitemap_urls = ["https://www.sportvision.rs/files/sitemap/SRB_rs/product.xml"]
    running_category_urls = ["https://www.sportvision.rs/patike/trcanje"]

    def is_shoe_url(self, url: str) -> bool:
        return "/patike/" in url.lower()
