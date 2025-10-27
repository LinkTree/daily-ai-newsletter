# Local Development Setup

This guide helps you run the AI Newsletter Processor locally for development and testing.

## Quick Start

1. **Set Environment Variables**:
   ```bash
   export CLAUDE_API_KEY="your-claude-api-key"
   export AWS_ACCESS_KEY_ID="your-aws-access-key"
   export AWS_SECRET_ACCESS_KEY="your-aws-secret-key"
   export AWS_DEFAULT_REGION="us-east-1"
   ```

2. **Install Dependencies**:
   ```bash
   pip install boto3==1.34.0 requests==2.31.0 beautifulsoup4==4.12.2
   ```

3. **Run Local Processing**:
   ```bash
   python run_local.py
   ```

## What Happens

### Input
- Reads sample emails from `sample_emails/` directory
- Three sample newsletters included: TLDR AI, Ben's Bites, The Rundown AI

### Processing
- **Remote Services Used**:
  - Claude API for generating summaries and podcast scripts
  - AWS Polly for text-to-speech conversion
- **Local Operations**:
  - Email parsing and content enhancement
  - File I/O operations
  - Output generation

### Output
All files saved to `output/` directory:

```
output/
├── audio/
│   └── ai_newsletter_podcast_2024-08-22.mp3
├── reports/
│   ├── executive_report_2024-08-22_14-30-15.json
│   ├── podcast_report_2024-08-22_14-30-15.json
│   ├── podcast_script_2024-08-22.txt
│   └── summary_email_2024-08-22_14-30-15.txt
└── rss/
    └── podcast_feed_2024-08-22.xml
```

## File Descriptions

### Audio Files
- **`ai_newsletter_podcast_*.mp3`**: Generated podcast audio using AWS Polly
- Natural speech with intro/outro, pauses, and professional delivery

### Report Files
- **`executive_report_*.json`**: Structured business summary with insights and links
- **`podcast_report_*.json`**: Podcast script with headlines and deep dive analysis
- **`podcast_script_*.txt`**: Clean text prepared for speech synthesis
- **`summary_email_*.txt`**: Email format summary combining both report types

### RSS Feed
- **`podcast_feed_*.xml`**: Standard podcast RSS feed for distribution
- Includes episode metadata, descriptions, and local file references

## Customization

### Add Your Own Sample Emails

Create JSON files in `sample_emails/` with this structure:

```json
{
  "message_id": "unique_id",
  "from": "sender@example.com",
  "subject": "Email Subject",
  "date": "2024-08-22T08:00:00Z",
  "content": "Email content here...",
  "newsletter_type": "Newsletter Name",
  "enhanced": false,
  "web_content": [],
  "extracted_links": ["https://example.com/link1"]
}
```

### Custom Directories

```bash
# Use custom input/output directories
python run_local.py --samples my_emails --output my_output
```

### Verbose Logging

```bash
# Enable detailed logging
python run_local.py --verbose
```

## Development Notes

### Local vs Production Differences

| Feature | Local Mode | Production Mode |
|---------|------------|-----------------|
| Email Source | JSON files | AWS SQS |
| Audio Storage | Local files | AWS S3 |
| RSS Feed | Local XML | S3-hosted XML |
| Notifications | Local files | AWS SNS |
| Claude API | ✅ Remote | ✅ Remote |
| Polly TTS | ✅ Remote | ✅ Remote |

### Testing Different Scenarios

1. **Single Context Processing**: Use smaller sample emails (will use single Claude API call)
2. **Batch Processing**: Use larger sample emails or add more files (will use multiple API calls with meta-summary)
3. **Error Handling**: Temporarily provide invalid API keys to test error paths

### Cost Considerations

Local development still incurs costs for:
- Claude API calls (~$0.01-0.10 per processing run)
- AWS Polly text-to-speech (~$0.004 per 1000 characters)

The sample emails generate approximately:
- 3,000-5,000 tokens for Claude API
- 2,000-3,000 characters for Polly TTS

## Troubleshooting

### Common Issues

1. **Missing Environment Variables**:
   ```
   ❌ Missing required environment variables:
      - CLAUDE_API_KEY
   ```
   **Solution**: Set all required environment variables

2. **AWS Permissions**:
   ```
   ❌ Error calling Polly: Access Denied
   ```
   **Solution**: Ensure AWS credentials have Polly permissions

3. **No Sample Emails**:
   ```
   ❌ No JSON files found in: sample_emails
   ```
   **Solution**: Create sample email JSON files in the directory

4. **Network Issues**:
   ```
   ❌ Error calling Claude API: Connection timeout
   ```
   **Solution**: Check internet connection and API endpoint availability

### Debugging

Use verbose mode for detailed logging:
```bash
python run_local.py --verbose
```

This shows:
- Individual file processing steps
- API call details
- File generation progress
- Error stack traces

## Next Steps

After successful local testing:

1. Deploy to AWS Lambda with production environment variables
2. Set up SQS queue for email ingestion
3. Configure S3 bucket for audio and RSS hosting
4. Set up SNS topic for notifications
5. Schedule processing with EventBridge/CloudWatch Events