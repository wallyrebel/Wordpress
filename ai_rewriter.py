"""
AI Rewriter module.
Uses GPT-4 to rewrite articles in Associated Press (AP) style.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class RewrittenArticle:
    """Represents a rewritten article with AP-style formatting."""
    headline: str
    body: str
    category: str
    tags: List[str]


# System prompt for AP-style rewriting
AP_STYLE_SYSTEM_PROMPT = """You are a professional news editor at a major wire service. Your task is to rewrite articles in strict Associated Press (AP) style, producing comprehensive, publication-ready news articles.

## Core AP Style Guidelines:
- **Inverted Pyramid Structure**: Lead with the most newsworthy information (who, what, when, where, why, how), then provide supporting details in descending order of importance
- **Active Voice**: Use strong, active verbs throughout
- **Short Paragraphs**: Keep paragraphs to 1-3 sentences for readability
- **Headlines**: Use present tense, active voice; omit articles (a, an, the) when possible
- **Attribution**: Clearly attribute all sources and quotes; use "said" as the primary verb for attribution
- **Numbers**: Spell out one through nine; use numerals for 10 and above; always use numerals for ages, percentages, and measurements
- **Titles**: Capitalize formal titles only when used directly before a name
- **Objectivity**: Maintain neutral, factual language without editorializing

## Article Length & Depth Requirements:
- **Minimum Article Length**: Produce articles of AT LEAST 120 words. This is the absolute minimum.
- **Expand with Context**: When source information is limited, add relevant background context such as:
  - Who/what the subject is and their significance
  - Historical context or previous related events
  - Why this news matters to readers
  - Implications or what happens next
  - Relevant statistics or facts that enhance understanding

## CRITICAL - Accuracy Rules:
- **NEVER fabricate quotes, statistics, specific facts, or any information not present in the source material**
- **Only add verifiable, general background context** (e.g., what an organization is known for, general location information)
- **When details are limited or the story is developing**: End the article with a closing sentence such as "We will provide more information as it becomes available." or "This is a developing story and will be updated as more details emerge."
- **Accuracy is paramount**: It is better to have a shorter, accurate article than a longer article with fabricated details

## Response Format:
You must respond with a valid JSON object containing exactly these keys:
- "headline": A concise, AP-style headline (max 100 characters, present tense, active voice)
- "body": The full rewritten article in AP style with proper HTML paragraph tags (<p></p>). Must be at least 120 words. Use 3-6 paragraphs.
- "category": A single category that best fits the article (e.g., "News", "Politics", "Business", "Technology", "Sports", "Entertainment", "Health", "Science", "Education", "Local")
- "tags": An array of 3-5 relevant tags as lowercase strings

Important: Return ONLY the JSON object, no additional text or markdown formatting."""


def rewrite_article(
    title: str,
    content: str,
    link: str,
    openai_client,
    model: str = "gpt-4.1-nano"
) -> Optional[RewrittenArticle]:
    """
    Rewrite an article in AP style using GPT-4.
    
    Args:
        title: Original article title.
        content: Original article content (may contain HTML).
        link: URL of the original article.
        openai_client: Initialized OpenAI client.
        model: OpenAI model to use.
        
    Returns:
        RewrittenArticle object or None if rewriting fails.
    """
    try:
        # Clean HTML from content
        clean_content = _strip_html(content)
        
        if not clean_content.strip():
            logger.warning("Article content is empty after cleaning")
            clean_content = title  # Fall back to title
        
        # Truncate very long content to avoid token limits
        max_content_length = 8000
        if len(clean_content) > max_content_length:
            clean_content = clean_content[:max_content_length] + "..."
            logger.info("Truncated content due to length")
        
        # Determine if source content is limited
        source_word_count = len(clean_content.split())
        content_guidance = ""
        if source_word_count < 100:
            content_guidance = """
NOTE: The source material is brief. Please expand this into an article of at least 120 words by:
- Adding relevant background context about the subject/organization
- Explaining the significance of this news
- Providing any general context that helps readers understand the story
- If details are limited, end with: "We will provide more information as it becomes available."
CRITICAL: Do NOT fabricate specific quotes, statistics, or facts not in the source."""
        elif source_word_count < 200:
            content_guidance = """
NOTE: Please ensure the rewritten article is at least 120 words with proper context and background. Do NOT fabricate any facts."""

        user_prompt = f"""Please rewrite the following article in AP style:

Title: {title}
Source URL: {link}
Source word count: approximately {source_word_count} words
{content_guidance}

Content:
{clean_content}

Remember to respond with only a valid JSON object."""

        logger.info(f"Rewriting article: {title[:60]}...")
        
        response = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": AP_STYLE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            response_format={"type": "json_object"}
        )
        
        response_text = response.choices[0].message.content
        
        # Parse the JSON response
        article_data = _parse_json_response(response_text)
        
        if not article_data:
            logger.error("Failed to parse GPT response as JSON")
            return None
        
        # Validate required fields
        headline = article_data.get('headline', title)
        body = article_data.get('body', '')
        category = article_data.get('category', 'News')
        tags = article_data.get('tags', [])
        
        # Ensure tags is a list
        if isinstance(tags, str):
            tags = [tags]
        tags = [str(tag).lower().strip() for tag in tags if tag]
        
        # Format body with HTML paragraphs if not already formatted
        body = _ensure_html_paragraphs(body)
        
        logger.info(f"Article rewritten successfully: {headline[:60]}...")
        
        return RewrittenArticle(
            headline=headline,
            body=body,
            category=category,
            tags=tags
        )
        
    except Exception as e:
        logger.error(f"Failed to rewrite article: {e}")
        return None


def _strip_html(html: str) -> str:
    """Remove HTML tags and decode entities."""
    if not html:
        return ""
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
        
        # Get text and normalize whitespace
        text = soup.get_text(separator=' ')
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    except Exception:
        # Fallback: simple regex
        return re.sub(r'<[^>]+>', '', html)


def _parse_json_response(response_text: str) -> Optional[dict]:
    """
    Parse JSON from GPT response, handling common formatting issues.
    
    Args:
        response_text: Raw response text from GPT.
        
    Returns:
        Parsed dictionary or None if parsing fails.
    """
    if not response_text:
        return None
    
    # Try direct parsing first
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        pass
    
    # Try to extract JSON from markdown code blocks
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    
    # Try to find JSON object in response
    json_match = re.search(r'\{[\s\S]*\}', response_text)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass
    
    logger.error(f"Could not parse JSON from response: {response_text[:200]}...")
    return None


def _ensure_html_paragraphs(text: str) -> str:
    """
    Ensure text has proper HTML paragraph formatting.
    
    Args:
        text: Article body text.
        
    Returns:
        Text with HTML paragraph tags.
    """
    if not text:
        return ""
    
    # If already has HTML tags, return as-is
    if '<p>' in text.lower() or '<div>' in text.lower():
        return text
    
    # Split by double newlines and wrap in <p> tags
    paragraphs = re.split(r'\n\s*\n', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    
    if not paragraphs:
        return f"<p>{text}</p>"
    
    return '\n'.join(f'<p>{p}</p>' for p in paragraphs)
