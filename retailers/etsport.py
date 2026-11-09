from retailers.nbshop import NBShopAdapter


class ETSportAdapter(NBShopAdapter):
    key = "etsport"
    name = "ET Sport"
    base_url = "https://www.etsport.rs"
    sitemap_urls = ["https://www.etsport.rs/files/sitemap/SRB_rs/product.xml"]
    running_category_urls = ["https://www.etsport.rs/patike-za-trcanje/muskarci/", "https://www.etsport.rs/patike-za-trcanje/zene/"]

    def is_shoe_url(self, url: str) -> bool:
        return "/patike" in url.lower()
