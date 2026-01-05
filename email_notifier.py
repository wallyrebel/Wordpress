"""
Email notification module.
Sends email alerts when new articles are published.
"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PublishedArticle:
    """Represents a published article for notification."""
    headline: str
    source_url: str
    wordpress_url: str
    post_id: int


def send_notification_email(
    articles: List[PublishedArticle],
    smtp_server: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    from_email: str,
    to_email: str,
    use_tls: bool = True
) -> bool:
    """
    Send an email notification about newly published articles.
    
    Args:
        articles: List of published articles.
        smtp_server: SMTP server hostname.
        smtp_port: SMTP server port.
        smtp_username: SMTP authentication username.
        smtp_password: SMTP authentication password.
        from_email: Sender email address.
        to_email: Recipient email address.
        use_tls: Whether to use TLS encryption.
        
    Returns:
        True if email sent successfully, False otherwise.
    """
    if not articles:
        logger.info("No articles to notify about")
        return True
    
    try:
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"📰 {len(articles)} New Article(s) Published to WordPress"
        msg['From'] = from_email
        msg['To'] = to_email
        
        # Create plain text version
        text_content = f"New Articles Published ({len(articles)} total)\n"
        text_content += "=" * 50 + "\n\n"
        
        for i, article in enumerate(articles, 1):
            text_content += f"{i}. {article.headline}\n"
            text_content += f"   Source: {article.source_url}\n"
            text_content += f"   Published: {article.wordpress_url}\n"
            text_content += f"   Post ID: {article.post_id}\n\n"
        
        # Create HTML version
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .header {{ background: #2563eb; color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
                .content {{ padding: 20px; background: #f8fafc; }}
                .article {{ background: white; padding: 15px; margin: 10px 0; border-radius: 8px; border-left: 4px solid #2563eb; }}
                .article h3 {{ margin: 0 0 10px 0; color: #1e40af; }}
                .links {{ margin-top: 10px; }}
                .links a {{ display: inline-block; margin-right: 15px; color: #2563eb; text-decoration: none; }}
                .links a:hover {{ text-decoration: underline; }}
                .footer {{ padding: 15px; background: #e2e8f0; border-radius: 0 0 8px 8px; font-size: 12px; color: #64748b; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>📰 {len(articles)} New Article(s) Published</h1>
            </div>
            <div class="content">
        """
        
        for article in articles:
            html_content += f"""
                <div class="article">
                    <h3>{article.headline}</h3>
                    <div class="links">
                        <a href="{article.source_url}">📄 Original Source</a>
                        <a href="{article.wordpress_url}">🌐 View on WordPress</a>
                    </div>
                </div>
            """
        
        html_content += """
            </div>
            <div class="footer">
                This is an automated notification from RSS to WordPress Automation.
            </div>
        </body>
        </html>
        """
        
        # Attach both versions
        msg.attach(MIMEText(text_content, 'plain'))
        msg.attach(MIMEText(html_content, 'html'))
        
        # Send email
        logger.info(f"Sending notification email to {to_email}")
        
        if use_tls:
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port)
        
        server.login(smtp_username, smtp_password)
        server.sendmail(from_email, to_email, msg.as_string())
        server.quit()
        
        logger.info(f"Notification email sent successfully to {to_email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send notification email: {e}")
        return False


def send_github_actions_notification(
    articles: List[PublishedArticle],
    to_email: str,
    smtp_username: str,
    smtp_password: str
) -> bool:
    """
    Send notification using Gmail SMTP (common for GitHub Actions).
    
    Args:
        articles: List of published articles.
        to_email: Recipient email address.
        smtp_username: Gmail address.
        smtp_password: Gmail App Password.
        
    Returns:
        True if email sent successfully, False otherwise.
    """
    return send_notification_email(
        articles=articles,
        smtp_server="smtp.gmail.com",
        smtp_port=587,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        from_email=smtp_username,
        to_email=to_email,
        use_tls=True
    )
