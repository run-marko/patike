from retailers.nsport_platform import NSportPlatformAdapter


class IntersportAdapter(NSportPlatformAdapter):
    key = "intersport"
    name = "Intersport"
    base_url = "https://www.intersport.rs"
    sitemap_urls = ["https://www.intersport.rs/UserFiles/sitemap.xml"]
