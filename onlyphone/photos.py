"""Photo officielle Apple de chaque modèle (page « Identifier votre modèle d'iPhone »).

Les images ne sont PAS livrées avec l'app : téléchargées une fois depuis le serveur d'images
d'Apple (cdsassets.apple.com uniquement), puis gardées en cache local (hors ligne ensuite).
"""
import os
import urllib.request
from urllib.parse import urlparse

from . import history

BASE = "https://cdsassets.apple.com/live/7WUAS350/images/iphone/"
_IMG = {
    ("iPhone8,1",): "iphone-6s-colors.jpg", ("iPhone8,2",): "iphone-6splus-colors.jpg", ("iPhone8,4",): "iphone-se/iphone-se-colors.jpg",
    ("iPhone9,1", "iPhone9,3"): "iphone-7/iphone7-colors.jpg", ("iPhone9,2", "iPhone9,4"): "iphone-7-plus/iphone7plus-colors.jpg",
    ("iPhone10,1", "iPhone10,4"): "iphone-8/iphone-8-colors.jpg", ("iPhone10,2", "iPhone10,5"): "iphone-8-plus/iphone-8plus-colors.jpg",
    ("iPhone10,3", "iPhone10,6"): "iphone-x-colors.jpg", ("iPhone11,2",): "iphone-xs-colors.jpg",
    ("iPhone11,4", "iPhone11,6"): "iphone-xs-max-colors.jpg", ("iPhone11,8",): "iphone-xr/identify-iphone-xr-colors.jpg",
    ("iPhone12,1",): "identify-iphone-11-colors.jpg", ("iPhone12,3",): "identify-iphone-11pro.jpg",
    ("iPhone12,5",): "identify-iphone-11pro-max.jpg", ("iPhone12,8",): "iphone-se/iphone-se-2nd-gen-colors.jpg",
    ("iPhone13,1",): "2021-iphone12-mini-colors.png", ("iPhone13,2",): "2021-iphone12-colors.png",
    ("iPhone13,3",): "iphone-12-pro/iphone12-pro-colors.jpg", ("iPhone13,4",): "iphone-12-pro-max/iphone12-pro-max-colors.jpg",
    ("iPhone14,4",): "2022-iphone13-mini-colors.png", ("iPhone14,5",): "2022-spring-iphone13-colors.png",
    ("iPhone14,2",): "2022-spring-iphone13-pro-colors.png", ("iPhone14,3",): "2022-spring-iphone13-pro-max-colors.png",
    ("iPhone14,6",): "iphone-se-3rd-gen-colors.png", ("iPhone14,7",): "iphone-14-colors-spring-2023.png",
    ("iPhone14,8",): "iphone-14-plus-colors-spring-2023.png", ("iPhone15,2",): "iphone-14-pro-colors.png",
    ("iPhone15,3",): "iphone-14-pro-max-colors.png", ("iPhone15,4",): "fall-2023-iphone-colors-iphone-15.png",
    ("iPhone15,5",): "fall-2023-iphone-colors-iphone-15-plus.png", ("iPhone16,1",): "fall-2023-iphone-colors-iphone-15-pro.png",
    ("iPhone16,2",): "fall-2023-iphone-colors-iphone-15-pro-max.png", ("iPhone17,3",): "iphone-16-colors.png",
    ("iPhone17,4",): "iphone-16-plus-colors.png", ("iPhone17,1",): "iphone-16-pro-colors.png",
    ("iPhone17,2",): "iphone-16-pro-max-colors.png", ("iPhone17,5",): "iphone-16e/iphone-16e-colors.png",
}
PHOTOS = {pt: BASE + img for pts, img in _IMG.items() for pt in pts}
MAX_BYTES = 6 * 1024 * 1024


def cache_dir():
    d = os.path.join(os.path.dirname(history.data_dir()), "photos")
    os.makedirs(d, exist_ok=True)
    return d


def _allowed(url):
    u = urlparse(url)
    return u.scheme == "https" and u.hostname == "cdsassets.apple.com"


def photo(pt, fetch=True):
    """ProductType -> chemin local de la photo (téléchargée au besoin), ou None (modèle inconnu, hors ligne)."""
    url = PHOTOS.get(pt or "")
    if not url or not _allowed(url):
        return None
    path = os.path.join(cache_dir(), url.rsplit("/", 1)[-1])
    if os.path.isfile(path):
        return path
    if not fetch:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OnlyTools"})
        with urllib.request.urlopen(req, timeout=15) as r:
            if not _allowed(r.geturl()) or not (r.headers.get("Content-Type") or "").startswith("image/"):
                return None
            data = r.read(MAX_BYTES + 1)
        if not data or len(data) > MAX_BYTES:
            return None
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return path
    except Exception:
        return None
