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

### Episode Title Generation

Podcast episodes get AI-generated titles using Claude Haiku 4.5 for engagement and discoverability.

**How it works:**
1. After podcast script generation, full script is sent to Claude Haiku 4.5
2. Haiku analyzes the script and generates a newsworthy, catchy title (6-12 words)
3. Title is saved to DynamoDB, used in RSS feed, and included in email notifications
4. Fallback: Date-based title if generation fails (e.g., "Daily AI Summary - October 31, 2025")

**Model & Cost:**
- Model: `claude-haiku-4-5` (Claude Haiku 4.5)
- Cost: ~$1 per million input tokens, ~$5 per million output tokens
- Typical cost per episode: ~$0.0008 (negligible)
- Time added: ~2-3 seconds per episode

**Title Quality Guidelines:**
- Newsworthy and informative (states what happened)
- Catchy and engaging (makes people want to listen)
- Professional but accessible tone
- No clickbait or sensationalism
- 6-12 words maximum

**Implementation:** See `_generate_episode_title` method (lambda_function.py:~1250)

**Backfilling Historical Episodes:**
Use `backfill_episode_titles.py` script to generate titles for existing episodes:

```bash
# Test with 5 records (dry-run)
python backfill_episode_titles.py --dry-run --limit 5

# Run on staging table
python backfill_episode_titles.py --table-name ai_daily_news_staging

# Run on production table
python backfill_episode_titles.py --table-name ai_daily_news
```

The script:
- Scans DynamoDB for records missing `episode_title`
- Generates titles using Claude Haiku 4.5
- Updates records with new titles + `generated_at` timestamp
- Rate limits to 1 request per 2 seconds
- Provides progress logging and summary report

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
   - Updates RSS feed with new episode (using AI-generated title)

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

### Lambda Testing Protocol

**CRITICAL: Long-running Lambda functions require careful async handling:**

1. **Before invoking:** Check for any running executions
   ```bash
   aws logs tail /aws/lambda/FUNCTION_NAME --region REGION --since 5m --format short | grep -E "START RequestId|END RequestId|Processing completed" | tail -20
   ```

2. **Verify no active invocations:** Look for recent "START RequestId" without corresponding "END RequestId" or "Processing completed"

3. **Kill any background bash processes** that might invoke Lambda:
   - Check system reminders for running background processes
   - Kill all background processes before proceeding
   - Verify they are actually stopped

4. **Invoke Lambda with extended timeout:**
   ```bash
   AWS_CLI_READ_TIMEOUT=900 aws lambda invoke --function-name FUNCTION_NAME --region REGION --cli-binary-format raw-in-base64-out --payload '{"test": true, "create_rss": false}' /tmp/response.json
   ```
   - Set AWS_CLI_READ_TIMEOUT=900 (15 minutes) to avoid premature timeout
   - Use --cli-binary-format raw-in-base64-out for JSON payload
   - CLI will wait up to 15 minutes for Lambda to complete
   - If Lambda takes longer, it will still complete (just CLI times out)

5. **Verify completion:**
   - With 15-minute timeout, the invoke command will wait for Lambda to complete
   - If command returns successfully, Lambda completed
   - If command times out, check CloudWatch logs to verify status

6. **Check CloudWatch logs for details:**
   ```bash
   aws logs tail /aws/lambda/FUNCTION_NAME --region REGION --since 5m --format short | grep -E "Processing completed|Generated episode title|Successfully saved" | tail -20
   ```
   - Look for "Processing completed: ✅ Success" message
   - Check for "Generated episode title:" to verify episode title feature
   - Verify "Successfully saved podcast text and title to DynamoDB"

7. **Never re-invoke until verified complete:**
   - Wait for "Processing completed" in logs before any new invocation
   - Don't invoke multiple times for code changes - deploy once, test once
   - Each invocation sends emails and costs money

**Why this matters:**
- Each Lambda invocation costs money (compute + API calls + Polly + Claude API)
- Claude API calls cost ~$0.003-0.015 per newsletter
- Episode title generation costs ~$0.0008 per episode (Claude Haiku 4.5)
- TEST_MODE with SQS means messages are reprocessed repeatedly
- Multiple invocations send duplicate email notifications
- AWS CLI timeout ≠ Lambda function stopped (Lambda keeps running in background)
- User trust depends on following explicit instructions precisely

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

### DynamoDB Schema

Daily summaries are stored in DynamoDB with the following schema:

```python
{
    'date': '2025-10-31',              # Partition key (YYYY-MM-DD)
    'text': '<podcast_script>',         # Full podcast script text
    'episode_title': 'AI-Generated Title',  # AI-generated episode title (NEW)
    'generated_at': '2025-10-31T14:30:00Z'  # ISO timestamp of generation (NEW)
}
```

**New fields (added with episode title generation):**
- `episode_title`: AI-generated title from Claude Haiku 4.5, or fallback date-based title
- `generated_at`: ISO 8601 timestamp of when the record was created

**Backfilling:** Use `backfill_episode_titles.py` to add titles to existing records missing this field.

### RSS Feed Management
RSS feed is updated with each podcast episode. Episodes include AI-generated episode title, description, audio URL, publication date, and duration (default: 10 minutes).

### Cleanup Behavior
Messages only deleted from SQS when test_mode=False (lambda_function.py:304-307). This prevents data loss during development/debugging.
