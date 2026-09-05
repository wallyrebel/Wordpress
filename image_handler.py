"""Only reuse explicitly permitted source images; never guess stock news photos."""
import hashlib
import io
import warnings
from pathlib import Path
from dataclasses import dataclass
from bs4 import BeautifulSoup
from PIL import Image
from safe_http import fetch_bytes

@dataclass
class NewsImage:
    path: str
    credit: str
    source_url: str

def extract_image_url(raw):
    return next(iter(image_candidates(raw)), None)

def image_candidates(raw):
    urls = []
    for key in ("media_content", "media_thumbnail"):
        for item in raw.get(key) or []:
            if item.get("url"):
                urls.append(item["url"])
    for key in ("content", "summary"):
        for node in BeautifulSoup(raw.get(key) or "", "html.parser").find_all("img"):
            candidates = node.get("srcset", "").split(",")
            for candidate in reversed(candidates):
                if candidate.strip():
                    urls.append(candidate.strip().split()[0])
            urls.extend([node.get("src"), node.get("data-src")])
    return list(dict.fromkeys(url for url in urls if url and url.startswith(("https://", "http://"))))[:5]

def get_source_image(raw, policy, image_dir):
    if not policy.image_reuse_allowed or not policy.image_credit.strip():
        return None
    for url in image_candidates(raw):
        try:
            image = download_candidate(url, policy, image_dir)
            if image:
                return image
        except (ValueError, OSError):
            continue
    return None

def download_candidate(url, policy, image_dir):
    data, mime, final = fetch_bytes(url, max_bytes=10_000_000)
    if not mime.lower().startswith("image/"):
        raise ValueError("Not an image response")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            if image.width < 1200 or image.height < 400 or image.width / image.height > 3.5:
                return None
            image = image.convert("RGB")
            image.thumbnail((2400, 2400))
            path = Path(image_dir) / (hashlib.sha256(data).hexdigest()[:24] + ".jpg")
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save(path, "JPEG", quality=88, optimize=True)
    return NewsImage(str(path), policy.image_credit, final)
