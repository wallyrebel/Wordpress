"""
Image Handler module.
Extracts images from feed entries or fetches stock photos from Pexels.
"""

import os
import re
import logging
import hashlib
import mimetypes
from typing import Optional
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Timeout for image downloads
REQUEST_TIMEOUT = 30

# Pexels API for keyword-matched stock photos
PEXELS_API_URL = "https://api.pexels.com/v1/search"
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")


def extract_image_url(raw_entry: dict) -> Optional[str]:
    """
    Extract an image URL from a feed entry.
    
    Checks in order:
    1. media_content
    2. media_thumbnail
    3. First <img> tag in summary/content
    
    Args:
        raw_entry: Dictionary with raw feed entry data.
        
    Returns:
        Image URL if found, None otherwise.
    """
    # Check media_content
    media_content = raw_entry.get('media_content')
    if media_content and len(media_content) > 0:
        url = media_content[0].get('url')
        if url:
            logger.debug(f"Found image in media_content: {url}")
            return url
    
    # Check media_thumbnail
    media_thumbnail = raw_entry.get('media_thumbnail')
    if media_thumbnail and len(media_thumbnail) > 0:
        url = media_thumbnail[0].get('url')
        if url:
            logger.debug(f"Found image in media_thumbnail: {url}")
            return url
    
    # Parse HTML content for <img> tags
    html_content = raw_entry.get('content') or raw_entry.get('summary', '')
    if html_content:
        url = _extract_img_from_html(html_content)
        if url:
            logger.debug(f"Found image in HTML content: {url}")
            return url
    
    logger.debug("No image found in feed entry")
    return None


def _extract_img_from_html(html: str) -> Optional[str]:
    """
    Extract the first image URL from HTML content.
    
    Args:
        html: HTML content to parse.
        
    Returns:
        Image URL if found, None otherwise.
    """
    try:
        soup = BeautifulSoup(html, 'html.parser')
        img_tag = soup.find('img')
        if img_tag:
            src = img_tag.get('src')
            if src and src.startswith(('http://', 'https://')):
                return src
            # Handle data-src for lazy-loaded images
            data_src = img_tag.get('data-src')
            if data_src and data_src.startswith(('http://', 'https://')):
                return data_src
    except Exception as e:
        logger.warning(f"Failed to parse HTML for images: {e}")
    
    return None


def download_image(image_url: str, image_dir: str) -> Optional[str]:
    """
    Download an image and save it locally.
    
    Args:
        image_url: URL of the image to download.
        image_dir: Directory to save the image.
        
    Returns:
        Local file path if successful, None otherwise.
    """
    try:
        logger.info(f"Downloading image: {image_url}")
        
        response = requests.get(
            image_url,
            timeout=REQUEST_TIMEOUT,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) RSS-Bot/1.0'},
            allow_redirects=True
        )
        response.raise_for_status()
        
        # Determine file extension
        content_type = response.headers.get('Content-Type', '')
        ext = _get_extension_from_content_type(content_type)
        if not ext:
            # Try to get from URL
            parsed = urlparse(image_url)
            ext = os.path.splitext(parsed.path)[1]
        if not ext:
            ext = '.jpg'  # Default to jpg
        
        # Generate unique filename based on URL hash
        url_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]
        filename = f"image_{url_hash}{ext}"
        filepath = os.path.join(image_dir, filename)
        
        # Save the image
        with open(filepath, 'wb') as f:
            f.write(response.content)
        
        logger.info(f"Image saved to: {filepath}")
        return filepath
        
    except requests.RequestException as e:
        logger.error(f"Failed to download image {image_url}: {e}")
        return None
    except IOError as e:
        logger.error(f"Failed to save image: {e}")
        return None


def _get_extension_from_content_type(content_type: str) -> Optional[str]:
    """Get file extension from Content-Type header."""
    content_type = content_type.split(';')[0].strip()
    ext = mimetypes.guess_extension(content_type)
    
    # Fix common issues
    if ext == '.jpe':
        ext = '.jpg'
    
    return ext


def create_search_query(title: str) -> str:
    """
    Create a search query for stock photo APIs based on article title.
    
    Args:
        title: Article title.
        
    Returns:
        A search query string with key terms.
    """
    # Common stop words to filter out
    stop_words = {
        'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
        'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'need',
        'this', 'that', 'these', 'those', 'it', 'its', 'as', 'if', 'when',
        'where', 'how', 'what', 'which', 'who', 'whom', 'why', 'so', 'than',
        'too', 'very', 'just', 'also', 'now', 'here', 'there', 'then', 'some',
        'any', 'all', 'both', 'each', 'few', 'more', 'most', 'other', 'into',
        'over', 'after', 'before', 'between', 'under', 'again', 'further',
        'once', 'during', 'out', 'up', 'down', 'off', 'about', 'only', 'same',
        'new', 'says', 'said', 'announces', 'announced', 'reports', 'reported'
    }
    
    # Extract words, filter stop words, take first 3 meaningful words
    words = re.findall(r'\b[a-zA-Z]{3,}\b', title.lower())
    keywords = [w for w in words if w not in stop_words][:3]
    
    if not keywords:
        # Fallback to "news" if no keywords found
        return "news"
    
    return " ".join(keywords)


def fetch_pexels_image(title: str, image_dir: str) -> Optional[str]:
    """
    Fetch a keyword-matched stock photo from Pexels API.
    
    Args:
        title: Article title to derive search keywords.
        image_dir: Directory to save the image.
        
    Returns:
        Local file path if successful, None otherwise.
    """
    if not PEXELS_API_KEY:
        logger.warning("PEXELS_API_KEY not set, skipping Pexels image fetch")
        return None
    
    try:
        query = create_search_query(title)
        logger.info(f"Searching Pexels for: {query}")
        
        # Search Pexels API
        response = requests.get(
            PEXELS_API_URL,
            params={
                "query": query,
                "per_page": 1,
                "orientation": "landscape"
            },
            headers={
                "Authorization": PEXELS_API_KEY,
                "User-Agent": "RSS-Bot/1.0"
            },
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        
        data = response.json()
        photos = data.get("photos", [])
        
        if not photos:
            logger.warning(f"No Pexels images found for: {query}")
            return None
        
        # Get the large image URL
        image_url = photos[0].get("src", {}).get("large")
        if not image_url:
            logger.warning("Pexels photo missing image URL")
            return None
        
        logger.info(f"Found Pexels image: {image_url}")
        
        # Download the image
        img_response = requests.get(
            image_url,
            timeout=REQUEST_TIMEOUT,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) RSS-Bot/1.0'}
        )
        img_response.raise_for_status()
        
        # Generate filename based on title hash
        title_hash = hashlib.md5(title.encode()).hexdigest()[:12]
        content_type = img_response.headers.get('Content-Type', '')
        ext = _get_extension_from_content_type(content_type) or '.jpg'
        filename = f"pexels_{title_hash}{ext}"
        filepath = os.path.join(image_dir, filename)
        
        with open(filepath, 'wb') as f:
            f.write(img_response.content)
        
        logger.info(f"Pexels image saved to: {filepath}")
        return filepath
        
    except requests.RequestException as e:
        logger.error(f"Failed to fetch Pexels image: {e}")
        return None
    except (KeyError, IndexError) as e:
        logger.error(f"Failed to parse Pexels response: {e}")
        return None
    except IOError as e:
        logger.error(f"Failed to save Pexels image: {e}")
        return None


def get_or_create_image(
    raw_entry: dict,
    title: str,
    content: str,
    image_dir: str
) -> Optional[str]:
    """
    Get an image for an article - extract from feed or fetch from Pexels.
    
    Args:
        raw_entry: Raw feed entry data for image extraction.
        title: Article title for Pexels search.
        content: Article content (unused, kept for compatibility).
        image_dir: Directory to save images.
        
    Returns:
        Local file path of the image, or None if failed.
    """
    os.makedirs(image_dir, exist_ok=True)
    
    # Try to extract existing image from RSS feed
    image_url = extract_image_url(raw_entry)
    if image_url:
        filepath = download_image(image_url, image_dir)
        if filepath:
            return filepath
        logger.warning("Failed to download extracted image, falling back to Pexels")
    
    # Fetch keyword-matched stock photo from Pexels
    return fetch_pexels_image(title, image_dir)
