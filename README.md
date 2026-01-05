# RSS to WordPress Automation

Automatically monitors RSS feeds and publishes AP-style articles to WordPress with featured images.

## Quick Start (Local)

```bash
# Install dependencies
pip install -r requirements.txt

# Configure credentials
cp .env.example .env
# Edit .env with your API keys

# Run once
python main.py

# Run continuously
python main.py --schedule
```

## GitHub Actions Deployment

For automated 24/7 operation, deploy to GitHub Actions:

### 1. Create a GitHub Repository

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/rss-to-wordpress.git
git push -u origin main
```

### 2. Add Repository Secrets

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these secrets:

| Secret Name | Value |
|-------------|-------|
| `OPENAI_API_KEY` | Your OpenAI API key |
| `WP_URL` | `https://msnewsgroup.com` |
| `WP_USERNAME` | `myersgrouponline` |
| `WP_APP_PASSWORD` | Your WordPress app password |
| `RSS_FEEDS` | All RSS feed URLs (comma-separated) |
| `NOTIFY_EMAIL` | Email to receive notifications |
| `SMTP_USERNAME` | Gmail address for sending |
| `SMTP_PASSWORD` | Gmail App Password |

### 3. Enable GitHub Actions

The workflow at `.github/workflows/rss-automation.yml` will:
- Run every 30 minutes automatically
- Process new RSS entries only
- Publish to WordPress
- Send email notifications
- Cache the processed database between runs

### Email Setup (Gmail)

1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
2. Create an App Password for "Mail"
3. Use your Gmail address as `SMTP_USERNAME`
4. Use the generated password as `SMTP_PASSWORD`

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key |
| `WP_URL` | Yes | WordPress site URL |
| `WP_USERNAME` | Yes | WordPress username |
| `WP_APP_PASSWORD` | Yes | WordPress application password |
| `RSS_FEEDS` | Yes | Comma-separated RSS feed URLs |
| `NOTIFY_EMAIL` | No | Email for notifications |
| `SMTP_USERNAME` | No | Gmail address |
| `SMTP_PASSWORD` | No | Gmail App Password |
| `POLL_INTERVAL_MINUTES` | No | Polling interval (default: 30) |

## Files

- `main.py` - Main orchestration script
- `config.py` - Configuration loading
- `database.py` - SQLite entry tracking
- `feed_parser.py` - RSS parsing
- `image_handler.py` - Image extraction/DALL-E generation
- `ai_rewriter.py` - GPT-4.1 Nano AP-style rewriting
- `wordpress_api.py` - WordPress REST API
- `email_notifier.py` - Email notifications
