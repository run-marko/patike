from retailers.nsport_platform import NSportPlatformAdapter


class NSportAdapter(NSportPlatformAdapter):
    """Tačan domen je n-sport.net (nsport.rs ima neispravan TLS sertifikat i nije korišćen)."""
    key = "nsport"
    name = "N Sport"
    base_url = "https://www.n-sport.net"
    sitemap_urls = ["https://www.n-sport.net/UserFiles/sitemap.xml"]
