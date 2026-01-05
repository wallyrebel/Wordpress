"""
Image Handler module.
Extracts images from feed entries or generates them using DALL-E.
"""

import os
import re
import logging
import hashlib
import mimetypes
from typing import Optional, Tuple
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Timeout for image downloads
REQUEST_TIMEOUT = 30


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
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) RSS-Bot/1.0'}
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


def generate_image_with_dalle(
    prompt: str,
    openai_client,
    image_dir: str,
    size: str = "1024x1024"
) -> Optional[str]:
    """
    Generate an image using DALL-E 3.
    
    Args:
        prompt: Text prompt for image generation.
        openai_client: Initialized OpenAI client.
        image_dir: Directory to save the generated image.
        size: Image size (DALL-E 3 supports: 1024x1024, 1024x1792, 1792x1024).
        
    Returns:
        Local file path if successful, None otherwise.
    """
    try:
        logger.info(f"Generating image with DALL-E: {prompt[:100]}...")
        
        # DALL-E 3 only supports specific sizes, use closest match
        # Requested 1024x600 is not supported, using 1024x1024 instead
        valid_sizes = ["1024x1024", "1024x1792", "1792x1024"]
        if size not in valid_sizes:
            size = "1024x1024"
        
        response = openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size=size,
            quality="standard",
            n=1,
        )
        
        image_url = response.data[0].url
        
        # Download the generated image
        img_response = requests.get(image_url, timeout=REQUEST_TIMEOUT)
        img_response.raise_for_status()
        
        # Generate filename
        prompt_hash = hashlib.md5(prompt.encode()).hexdigest()[:12]
        filename = f"dalle_{prompt_hash}.png"
        filepath = os.path.join(image_dir, filename)
        
        with open(filepath, 'wb') as f:
            f.write(img_response.content)
        
        logger.info(f"DALL-E image saved to: {filepath}")
        return filepath
        
    except Exception as e:
        logger.error(f"Failed to generate image with DALL-E: {e}")
        return None


def create_image_prompt(title: str, content: str) -> str:
    """
    Create a descriptive prompt for DALL-E based on article content.
    
    Args:
        title: Article title.
        content: Article content or summary.
        
    Returns:
        A prompt suitable for image generation.
    """
    # Clean HTML from content
    soup = BeautifulSoup(content, 'html.parser')
    clean_text = soup.get_text()[:500]  # Limit length
    
    # Create a news-appropriate prompt
    prompt = f"""A professional news photograph for an article titled "{title}". 
    The image should be photorealistic, well-lit, and suitable for a news publication. 
    Context from the article: {clean_text[:200]}
    Style: Professional news photography, no text or watermarks."""
    
    return prompt


def get_or_create_image(
    raw_entry: dict,
    title: str,
    content: str,
    openai_client,
    image_dir: str
) -> Optional[str]:
    """
    Get an image for an article - either extract from feed or generate with DALL-E.
    
    Args:
        raw_entry: Raw feed entry data for image extraction.
        title: Article title for DALL-E prompt.
        content: Article content for DALL-E prompt.
        openai_client: Initialized OpenAI client.
        image_dir: Directory to save images.
        
    Returns:
        Local file path of the image, or None if failed.
    """
    os.makedirs(image_dir, exist_ok=True)
    
    # Try to extract existing image
    image_url = extract_image_url(raw_entry)
    if image_url:
        filepath = download_image(image_url, image_dir)
        if filepath:
            return filepath
        logger.warning("Failed to download extracted image, falling back to DALL-E")
    
    # Generate image with DALL-E
    prompt = create_image_prompt(title, content)
    return generate_image_with_dalle(prompt, openai_client, image_dir)
