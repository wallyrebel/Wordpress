"""
WordPress REST API module.
Handles all interactions with WordPress including posts, media, categories, and tags.
"""

import os
import logging
import mimetypes
from typing import List, Optional, Dict, Tuple
from urllib.parse import urljoin
import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

# Request timeout
REQUEST_TIMEOUT = 60


class WordPressAPI:
    """WordPress REST API client."""
    
    def __init__(self, wp_url: str, username: str, app_password: str):
        """
        Initialize the WordPress API client.
        
        Args:
            wp_url: WordPress site URL (e.g., https://example.com)
            username: WordPress username
            app_password: WordPress application password
        """
        self.wp_url = wp_url.rstrip('/')
        self.api_base = f"{self.wp_url}/wp-json/wp/v2"
        self.auth = HTTPBasicAuth(username, app_password)
        self.session = requests.Session()
        self.session.auth = self.auth
        
        # Cache for categories and tags
        self._category_cache: Dict[str, int] = {}
        self._tag_cache: Dict[str, int] = {}
    
    def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Optional[requests.Response]:
        """
        Make an authenticated request to the WordPress API.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (relative to api_base)
            **kwargs: Additional arguments for requests
            
        Returns:
            Response object or None if request failed.
        """
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
        
        kwargs.setdefault('timeout', REQUEST_TIMEOUT)
        
        try:
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            logger.error(f"WordPress API request failed: {method} {url}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response body: {e.response.text[:500]}")
            return None
    
    def get_or_create_category(self, name: str) -> Optional[int]:
        """
        Get an existing category ID or create a new one.
        
        Args:
            name: Category name.
            
        Returns:
            Category ID or None if failed.
        """
        name = name.strip()
        if not name:
            return None
        
        # Check cache
        name_lower = name.lower()
        if name_lower in self._category_cache:
            return self._category_cache[name_lower]
        
        # Search for existing category
        response = self._request('GET', 'categories', params={'search': name, 'per_page': 100})
        if response:
            categories = response.json()
            for cat in categories:
                if cat.get('name', '').lower() == name_lower:
                    self._category_cache[name_lower] = cat['id']
                    logger.debug(f"Found existing category: {name} (ID: {cat['id']})")
                    return cat['id']
        
        # Create new category
        response = self._request('POST', 'categories', json={'name': name})
        if response:
            cat_id = response.json().get('id')
            self._category_cache[name_lower] = cat_id
            logger.info(f"Created new category: {name} (ID: {cat_id})")
            return cat_id
        
        logger.error(f"Failed to get or create category: {name}")
        return None
    
    def get_or_create_tag(self, name: str) -> Optional[int]:
        """
        Get an existing tag ID or create a new one.
        
        Args:
            name: Tag name.
            
        Returns:
            Tag ID or None if failed.
        """
        name = name.strip()
        if not name:
            return None
        
        # Check cache
        name_lower = name.lower()
        if name_lower in self._tag_cache:
            return self._tag_cache[name_lower]
        
        # Search for existing tag
        response = self._request('GET', 'tags', params={'search': name, 'per_page': 100})
        if response:
            tags = response.json()
            for tag in tags:
                if tag.get('name', '').lower() == name_lower:
                    self._tag_cache[name_lower] = tag['id']
                    logger.debug(f"Found existing tag: {name} (ID: {tag['id']})")
                    return tag['id']
        
        # Create new tag
        response = self._request('POST', 'tags', json={'name': name})
        if response:
            tag_id = response.json().get('id')
            self._tag_cache[name_lower] = tag_id
            logger.info(f"Created new tag: {name} (ID: {tag_id})")
            return tag_id
        
        logger.error(f"Failed to get or create tag: {name}")
        return None
    
    def get_category_and_tag_ids(
        self,
        category: str,
        tags: List[str]
    ) -> Tuple[List[int], List[int]]:
        """
        Get IDs for a category and list of tags, creating if needed.
        
        Args:
            category: Category name.
            tags: List of tag names.
            
        Returns:
            Tuple of (category_ids, tag_ids).
        """
        category_ids = []
        tag_ids = []
        
        # Get category
        if category:
            cat_id = self.get_or_create_category(category)
            if cat_id:
                category_ids.append(cat_id)
        
        # Get tags
        for tag in tags:
            tag_id = self.get_or_create_tag(tag)
            if tag_id:
                tag_ids.append(tag_id)
        
        return category_ids, tag_ids
    
    def upload_media(
        self,
        file_path: str,
        alt_text: str = "",
        caption: str = ""
    ) -> Optional[int]:
        """
        Upload a media file to WordPress.
        
        Args:
            file_path: Local path to the media file.
            alt_text: Alt text for the media.
            caption: Caption for the media.
            
        Returns:
            Media ID or None if upload failed.
        """
        if not os.path.exists(file_path):
            logger.error(f"Media file not found: {file_path}")
            return None
        
        filename = os.path.basename(file_path)
        
        # Determine content type
        content_type, _ = mimetypes.guess_type(file_path)
        if not content_type:
            content_type = 'image/jpeg'  # Default
        
        try:
            with open(file_path, 'rb') as f:
                file_data = f.read()
            
            headers = {
                'Content-Type': content_type,
                'Content-Disposition': f'attachment; filename="{filename}"',
            }
            
            url = f"{self.api_base}/media"
            
            response = self.session.post(
                url,
                data=file_data,
                headers=headers,
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            
            media_data = response.json()
            media_id = media_data.get('id')
            
            # Update alt text and caption if provided
            if alt_text or caption:
                update_data = {}
                if alt_text:
                    update_data['alt_text'] = alt_text
                if caption:
                    update_data['caption'] = caption
                
                self._request('POST', f'media/{media_id}', json=update_data)
            
            logger.info(f"Uploaded media: {filename} (ID: {media_id})")
            return media_id
            
        except requests.RequestException as e:
            logger.error(f"Failed to upload media {file_path}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text[:500]}")
            return None
        except IOError as e:
            logger.error(f"Failed to read media file {file_path}: {e}")
            return None
    
    def create_post(
        self,
        title: str,
        content: str,
        status: str = "publish",
        category_ids: List[int] = None,
        tag_ids: List[int] = None,
        featured_media_id: int = None,
        excerpt: str = ""
    ) -> Optional[int]:
        """
        Create a new WordPress post.
        
        Args:
            title: Post title.
            content: Post content (HTML).
            status: Post status (publish, draft, etc.).
            category_ids: List of category IDs.
            tag_ids: List of tag IDs.
            featured_media_id: ID of the featured image.
            excerpt: Post excerpt.
            
        Returns:
            Post ID or None if creation failed.
        """
        post_data = {
            'title': title,
            'content': content,
            'status': status,
        }
        
        if category_ids:
            post_data['categories'] = category_ids
        
        if tag_ids:
            post_data['tags'] = tag_ids
        
        if featured_media_id:
            post_data['featured_media'] = featured_media_id
        
        if excerpt:
            post_data['excerpt'] = excerpt
        
        response = self._request('POST', 'posts', json=post_data)
        
        if response:
            post_id = response.json().get('id')
            post_url = response.json().get('link', '')
            logger.info(f"Created post: {title[:50]}... (ID: {post_id}, URL: {post_url})")
            return post_id
        
        logger.error(f"Failed to create post: {title}")
        return None
    
    def test_connection(self) -> bool:
        """
        Test the WordPress API connection.
        
        Returns:
            True if connection is successful, False otherwise.
        """
        try:
            # Try to access the users/me endpoint (requires authentication)
            response = self._request('GET', 'users/me')
            if response:
                user_data = response.json()
                logger.info(f"WordPress connection successful. Logged in as: {user_data.get('name')}")
                return True
        except Exception as e:
            logger.error(f"WordPress connection test failed: {e}")
        
        return False
