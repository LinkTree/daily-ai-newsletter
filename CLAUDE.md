# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AWS Lambda function (`ai-newsletter-podcast-creator`) that processes AI newsletters into dual-format outputs: executive summaries and podcast scripts with audio. Runs daily on a scheduled trigger.

## Core Architecture

### Main Components

1. **`ClaudeNewsletterProcessor`** (lambda_function.py:38-1722)
   - Daily newsletter processing from SQS queue
   - Dual-format generation: executive reports + podcast scripts
   - Podcast audio generation via AWS Polly with intro/outro
   - RSS feed management for podcast distribution
   - DynamoDB storage for daily summaries
   - Invoked via `lambda_handler()`

2. **`LocalNewsletterProcessor`** (local_processor.py:16-344)
   - Local development version for testing
   - Reads sample emails from JSON files
   - Saves all outputs locally (MP3, XML, reports)
   - Bypasses SQS/S3/SNS, uses Claude API + Polly remotely

### Processing Strategies

The system uses **hybrid processing** based on token estimation:

- **Single Context Processing**: When estimated tokens ≤ 800,000 (max_tokens_per_batch), sends all content in one Claude API call
- **Batch Processing**: Creates smart batches when exceeding limits, processes each batch separately, then generates meta-summary

Token estimation uses tiktoken if available, otherwise falls back to character-based estimation (4 chars ≈ 1 token).

### Dual-Format Output

Every processing run generates TWO parallel outputs:

1. **Executive Report Format**:
   - Executive Summary, Key Themes, Breaking News
   - Technical Insights, Market Impact, Notable Links
   - Structured for business leaders

2. **Podcast Script Format**:
   - Top News Headlines (5-6 concise items)
   - Deep Dive Analysis (technical, financial, market, cultural, action plan)
   - Designed for audio delivery

Both formats use separate prompts (lambda_function.py:96-236) but process the same source content.

### Audio Generation Pipeline

1. **Text Preparation** (_prepare_text_for_speech, lambda_function.py:1388-1465):
   - Removes markdown formatting
   - Escapes SSML-breaking characters (&, <, >, %, $)
   - Converts smart quotes to regular quotes
   - Adds SSML breaks for natural pacing
   - Injects date-based intro/outro with synthetic host identity

2. **Speech Synthesis** (_convert_text_to_speech, lambda_function.py:1270-1308):
   - Chunks text at sentence boundaries (max 2800 chars per chunk)
   - Calls AWS Polly with SSML + prosody rate control
   - Concatenates audio chunks into single MP3

3. **Distribution** (_upload_audio_to_s3, lambda_function.py:1310-1340):
   - Uploads MP3 to S3 with metadata
   - Generates presigned URL (7-day expiry)
   - Updates RSS feed with new episode

### Rate Limiting & Retry Logic

Claude API calls implement exponential backoff:
- Enforces minimum request interval (60 / CLAUDE_RPM_LIMIT seconds)
- On 429 (rate limit): retries with delay = CLAUDE_BASE_DELAY * (2^retry_count)
- Max retries: CLAUDE_MAX_RETRIES (default: 6)
- See _call_claude_api (lambda_function.py:1071-1117)

### Content Enhancement

Extracts and fetches content from newsletter links:
1. **Link Extraction** (_extract_links, lambda_function.py:562-591):
   - Regex-based URL extraction
   - Filters tracking/unsubscribe links
   - Limits to MAX_LINKS_PER_EMAIL per newsletter

2. **Web Scraping** (_fetch_single_url, lambda_function.py:612-673):
   - Fetches HTML with requests + BeautifulSoup
   - Tries semantic selectors (main, article, .content)
   - Truncates to 3000 chars per article
   - Falls back to body if no semantic markup

## Development Workflows

### Local Development

```bash
# Set required environment variables
export CLAUDE_API_KEY="your-key"
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
export AWS_DEFAULT_REGION="us-east-1"

# Install dependencies
pip install -r requirements.txt

# Run with sample emails
python run_local.py

# Custom directories
python run_local.py --samples my_emails --output my_output

# Verbose logging
python run_local.py --verbose
```

Local mode outputs to `output/` directory:
- `output/audio/` - MP3 files
- `output/reports/` - JSON reports, TXT scripts, email summaries
- `output/rss/` - XML podcast feeds

### Lambda Deployment

The codebase is packaged as `ai-newsletter-lambda.zip` with all dependencies vendored in.
- Lambda handler: `lambda_handler` - Daily processing (triggered by EventBridge schedule)
- Function name: `ai-newsletter-podcast-creator`

### Test Mode

Set `TEST_MODE=true` or pass `{"test": true}` in event JSON to prevent SQS message deletion. Useful for testing without consuming queue messages.

RSS feed creation can be disabled with `{"create_rss": false}` in event JSON.

## Environment Variables

### Required
- `CLAUDE_API_KEY` - Anthropic API key
- `EMAIL_QUEUE_URL` - SQS queue URL
- `SNS_TOPIC_ARN` - SNS topic for notifications

### Optional with Defaults
- `MAX_MESSAGES=50` - Max SQS messages per run
- `MAX_LINKS_PER_EMAIL=5` - Links to fetch per newsletter
- `DYNAMODB_TABLE_NAME=ai_daily_news` - Table for daily summaries
- `PODCAST_S3_BUCKET=ai-newsletter-podcasts` - S3 bucket for audio
- `POLLY_VOICE=Joanna` - AWS Polly voice ID
- `POLLY_RATE=medium` - Speech rate (slow/medium/fast)
- `GENERATE_AUDIO=true` - Enable/disable audio generation
- `PODCAST_IMAGE_URL` - RSS feed artwork URL
- `CLAUDE_RPM_LIMIT=5` - Max Claude API requests per minute
- `CLAUDE_MAX_RETRIES=6` - Max retry attempts for 429 errors
- `CLAUDE_BASE_DELAY=10` - Base delay (seconds) for exponential backoff
- `TEST_MODE=false` - Enable test mode (no message deletion)

## Key Configuration

- **Claude Model**: `claude-sonnet-4-20250514` (configurable at lambda_function.py:77)
- **Token Limit**: 800,000 tokens per batch (lambda_function.py:85)
- **API Timeout**: 60 seconds (lambda_function.py:1097)
- **Content Truncation**: 3000 chars for web content, 5000 chars for email content in batches
- **Presigned URL Expiry**: 7 days (lambda_function.py:1332)

## Newsletter Source Recognition

Pattern-based detection in _identify_newsletter (lambda_function.py:504-525):
- TLDR AI, Ben's Bites, AI Secret, AI Israel Weekly
- Aftershoot AI, The Rundown AI, AI Breakfast, Import AI
- Falls back to "Other AI Newsletter" for unknown sources

## Important Implementation Details

### SSML Safety
Text-to-speech preparation must escape characters that break SSML: `& < > % $` and smart quotes. The _prepare_text_for_speech method handles this at lambda_function.py:1388-1465.

### Batch Size Calculation
Smart batching uses 70% of max_tokens_per_batch as safety margin (_create_smart_batches, lambda_function.py:860-889). Single oversized emails are truncated.

### Prompt Templates
All prompts centralized in _init_prompts (lambda_function.py:96-236) with separate templates for:
- Executive comprehensive/batch/meta-summary
- Podcast comprehensive/batch/meta-summary

### RSS Feed Management
RSS feed is updated with each podcast episode. Episodes include episode title, description, audio URL, publication date, and duration (default: 10 minutes).

### Cleanup Behavior
Messages only deleted from SQS when test_mode=False (lambda_function.py:304-307). This prevents data loss during development/debugging.
