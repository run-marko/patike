from retailers.nbshop import NBShopAdapter


class RunNMoreAdapter(NBShopAdapter):
    key = "runnmore"
    name = "Run 'n More"
    base_url = "https://www.runnmore.com"
    sitemap_urls = ["https://www.runnmore.com/files/sitemap/SRB_rs/product.xml"]
    sitemap_is_running_store = True

    def is_shoe_url(self, url: str) -> bool:
        return "/patike/" in url.lower()
