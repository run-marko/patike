from retailers.nbshop import NBShopAdapter


class ExtraSportsAdapter(NBShopAdapter):
    key = "extrasports"
    name = "Extra Sports"
    base_url = "https://www.extrasports.com"
    sitemap_urls = ["https://www.extrasports.com/files/sitemap/SRB_rs/product.xml"]
    running_category_urls = ["https://www.extrasports.com/SRB_rs/patike/za-muskarce+unisex/trcanje-namena",
                             "https://www.extrasports.com/SRB_rs/patike/za-zene+unisex/trcanje-namena"]

    def is_shoe_url(self, url: str) -> bool:
        return "/patike/" in url.lower()
