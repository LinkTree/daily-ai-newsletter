# AI Newsletter Processing System

AWS Lambda-based system that automatically processes AI newsletters into dual-format outputs: executive summaries for business leaders and podcast scripts with generated audio.

**Lambda Function**: `ai-newsletter-podcast-creator` (daily processor)

## Features

- **Dual-Format Generation**: Produces both executive reports and podcast scripts from the same content
- **AI-Generated Episode Titles**: Uses Claude Haiku 4.5 to create engaging, newsworthy podcast titles
- **Automated Audio Generation**: Uses AWS Polly to create podcast audio with professional intro/outro
- **RSS Feed Management**: Maintains podcast RSS feed for distribution with AI-generated titles
- **Smart Content Processing**: Hybrid batch processing for large newsletter volumes
- **Rate Limiting & Retry Logic**: Robust Claude API integration with exponential backoff
- **Local Development Mode**: Test without AWS infrastructure
- **Scheduled Daily Execution**: Triggered automatically via EventBridge

## Architecture

### Processing Flow
1. Newsletters arrive via SQS queue (from SES email forwarding)
2. Lambda function processes emails with Claude AI
3. Generates executive summary + podcast script
4. Creates audio file via AWS Polly
5. Uploads MP3 and RSS feed to S3
6. Stores summary in DynamoDB
7. Sends notification via SNS
8. Triggered daily at 10:00 UTC via EventBridge

## Quick Start

### Prerequisites

- AWS account with appropriate permissions
- AWS CLI configured with credentials
- Python 3.10+ installed
- Claude API key from [console.anthropic.com](https://console.anthropic.com)
- Domain or email verified in SES (for email ingestion)

### Initial Deployment (One Time)

Deploy complete infrastructure with one command:

```bash
./deploy.sh --full --claude-api-key "sk-ant-api03-..."
```

This will create all AWS resources (S3, DynamoDB, SQS, SNS, Lambda, EventBridge, IAM, Secrets Manager).

**Default region**: `eu-central-1` (production region)

**Options**:
```bash
# Full deployment with custom configuration
./deploy.sh --full \
  --claude-api-key "sk-ant-..." \
  --region us-east-1 \
  --bucket-name "my-custom-bucket" \
  --notification-email "admin@example.com" \
  --daily-schedule "cron(0 9 * * ? *)"

# Dry run (preview what will be created)
./deploy.sh --full --claude-api-key "sk-ant-..." --dry-run

# Destroy all resources
./deploy.sh --destroy
```

### Updating Lambda Function (Regular Updates)

After initial deployment, use the quick update script:

```bash
# Update Lambda function (most common use case)
./update-lambda.sh

# Update in different region
./update-lambda.sh --region us-east-1

# Skip build step (use existing package)
./update-lambda.sh --skip-build
```

**This is what you'll use 95% of the time** - it only updates Lambda code, skipping infrastructure checks (completes in ~2 minutes).

## Staging Environment

Test new features safely without affecting production using a complete staging environment.

### Key Features

- **Non-Destructive Testing**: Reads from production SQS queue without deleting messages
- **Isolated Outputs**: Separate S3 folder (`staging/`), RSS feed, and DynamoDB table
- **Manual Invocation Only**: No EventBridge schedule (test when you want)
- **Clear Identification**: All outputs marked with `[STAGING]` prefix
- **Same Infrastructure**: Uses production queue, SNS, and S3 bucket (cost-efficient)

### Deploying Staging Environment

**Prerequisites**: Production infrastructure must exist first (run `./deploy.sh --full` first)

```bash
# Deploy complete staging environment
./deploy-staging.sh

# Deploy to different region
./deploy-staging.sh --region us-east-1

# Preview what will be created
./deploy-staging.sh --dry-run
```

**Creates**:
- DynamoDB table: `ai_daily_news_staging`
- Lambda function: `ai-newsletter-podcast-creator-staging`
- S3 structure: `s3://[bucket]/staging/podcasts/`
- RSS feed: `s3://[bucket]/feed-staging.xml`

### Testing Staging

```bash
# Invoke staging function manually
aws lambda invoke \
  --function-name ai-newsletter-podcast-creator-staging \
  --region eu-central-1 \
  response.json

# Check staging outputs
aws s3 ls s3://ai-newsletter-podcasts-[ACCOUNT_ID]/staging/podcasts/
aws s3 cp s3://ai-newsletter-podcasts-[ACCOUNT_ID]/feed-staging.xml -
aws dynamodb scan --table-name ai_daily_news_staging --limit 5
```

### Updating Staging Code

After making changes, update staging Lambda:

```bash
# Update staging environment
./update-lambda.sh --staging

# Update staging in different region
./update-lambda.sh --staging --region us-east-1
```

### Staging vs Production

| Aspect | Production | Staging |
|--------|-----------|---------|
| Lambda Function | `ai-newsletter-podcast-creator` | `ai-newsletter-podcast-creator-staging` |
| DynamoDB Table | `ai_daily_news` | `ai_daily_news_staging` |
| S3 Prefix | `podcasts/` | `staging/podcasts/` |
| RSS Feed | `feed.xml` | `feed-staging.xml` |
| SQS Messages | Deleted after processing | **Not deleted** (test mode) |
| EventBridge | Daily at 10:00 UTC | No schedule (manual only) |
| SNS Notifications | Normal | `[STAGING]` prefix |
| RSS GUIDs | `daily-ai-YYYYMMDD` | `staging-daily-ai-YYYYMMDD` |
| Podcast Title | "Daily AI, by AI" | "Daily AI, by AI (Staging)" |
| Audio Intro | Normal | "This is a staging test..." |

### Environment Variables (Staging)

Staging-specific environment variables set automatically by `deploy-staging.sh`:

```bash
ENVIRONMENT=staging
TEST_MODE=true                  # Prevents SQS message deletion
S3_KEY_PREFIX=staging/
RSS_FEED_NAME=feed-staging.xml
NOTIFICATION_PREFIX=[STAGING]
PODCAST_TITLE=Daily AI, by AI (Staging)
PODCAST_TITLE_SHORT=Daily AI Staging
DYNAMODB_TABLE_NAME=ai_daily_news_staging
```

### Testing Workflow

1. **Make Changes**: Edit `lambda_function.py` with new features
2. **Update Staging**: `./update-lambda.sh --staging`
3. **Test**: `aws lambda invoke --function-name ai-newsletter-podcast-creator-staging ...`
4. **Verify Outputs**: Check S3 staging folder and RSS feed
5. **Review**: Confirm functionality is correct
6. **Deploy Production**: `./update-lambda.sh`

### Important Notes

- **Non-Destructive**: Staging reads from production queue but doesn't delete messages, so production can still process them
- **Cost-Efficient**: Uses same S3 bucket, SQS queue, and SNS topic (minimal additional cost)
- **Data Isolation**: Separate DynamoDB table ensures no production data contamination
- **Manual Testing**: No automatic triggers - invoke when you want to test

## Local Development

Test the system locally without deploying to AWS:

```bash
# Set environment variables
export CLAUDE_API_KEY="your-claude-api-key"
export AWS_ACCESS_KEY_ID="your-aws-key"
export AWS_SECRET_ACCESS_KEY="your-aws-secret"
export AWS_DEFAULT_REGION="us-east-1"

# Install dependencies
pip install -r requirements.txt

# Run with sample emails
python run_local.py

# Use custom directories
python run_local.py --samples my_emails --output my_output

# Verbose logging
python run_local.py --verbose
```

See [local_setup.md](local_setup.md) for detailed local development guide.

## Documentation

- **[DEPLOYMENT_REQUIREMENTS.md](DEPLOYMENT_REQUIREMENTS.md)** - Complete infrastructure requirements
- **[DEPLOYMENT_PLAN.md](DEPLOYMENT_PLAN.md)** - Step-by-step deployment strategy
- **[CLAUDE.md](CLAUDE.md)** - Architecture guide for Claude Code
- **[local_setup.md](local_setup.md)** - Local development setup
- **[PRODUCT_SPECIFICATION.md](PRODUCT_SPECIFICATION.md)** - Product specification

## Components

### Lambda Function

- **`lambda_function.py`** - Daily newsletter processor (`ai-newsletter-podcast-creator`)
  - Handler: `lambda_function.lambda_handler`
  - Processes SQS queue of newsletters
  - Generates executive summaries and podcast scripts
  - Creates audio files via Polly
  - Manages RSS feed
  - Stores summaries in DynamoDB

### Deployment Scripts

- **`deploy.sh`** - Full infrastructure deployment
  - One-time setup of all AWS resources
  - Creates S3, DynamoDB, SQS, SNS, Lambda, EventBridge, IAM
  - Requires `--full` flag for safety
  - Default region: `eu-central-1`
  - Time: ~15-20 minutes

- **`deploy-staging.sh`** - Staging environment deployment
  - Creates complete staging infrastructure
  - Requires production infrastructure first
  - Separate DynamoDB table, Lambda function, S3 structure
  - Non-destructive testing (reads but doesn't delete SQS messages)
  - Time: ~5-10 minutes

- **`update-lambda.sh`** - Lambda-only updates
  - Quick updates to Lambda function code
  - Skips infrastructure checks
  - Supports both production and staging (`--staging` flag)
  - Time: ~2 minutes
  - **Use this for regular deployments**

### Local Development Tools

- **`local_processor.py`** - Local version of processor
  - Reads from JSON files instead of SQS
  - Saves outputs locally instead of S3/SNS
  - Still uses Claude API and Polly

- **`run_local.py`** - CLI for local testing
  - Command-line interface for local mode
  - Automatic environment setup
  - Progress reporting

## Configuration

### Required Environment Variables

- `EMAIL_QUEUE_URL` - SQS queue URL
- `SNS_TOPIC_ARN` - SNS topic for notifications
- `CLAUDE_API_KEY` - Anthropic Claude API key

### Optional Environment Variables

- `MAX_MESSAGES=50` - Max SQS messages per run
- `MAX_LINKS_PER_EMAIL=5` - Links to fetch per newsletter
- `DYNAMODB_TABLE_NAME=ai_daily_news` - DynamoDB table name
- `PODCAST_S3_BUCKET=ai-newsletter-podcasts` - S3 bucket
- `POLLY_VOICE=Joanna` - AWS Polly voice
- `POLLY_RATE=medium` - Speech rate
- `GENERATE_AUDIO=true` - Enable audio generation
- `UPDATE_RSS_FEED=true` - Enable RSS updates
- `CLAUDE_RPM_LIMIT=5` - API requests per minute
- `TEST_MODE=false` - Test mode (no SQS deletion)

See [DEPLOYMENT_REQUIREMENTS.md](DEPLOYMENT_REQUIREMENTS.md) for complete list.

## AWS Services Used

- **Lambda** - Serverless compute for processing (`ai-newsletter-podcast-creator`)
- **SQS** - Queue for incoming newsletter emails
- **SNS** - Notifications for completed summaries
- **S3** - Storage for podcast audio files and RSS feed
- **DynamoDB** - Storage for daily summaries
- **Polly** - Text-to-speech synthesis for podcast audio generation (no separate resource needed, IAM permissions only)
- **EventBridge** - Scheduled trigger (daily at 10:00 UTC)
- **SES** - Email receipt (optional, for email ingestion from newsletters)
- **Secrets Manager** - Secure storage for Claude API key
- **IAM** - Execution role and permissions for Lambda function
- **CloudWatch** - Logs, metrics, and monitoring

## Cost Estimates

Monthly costs (moderate usage):
- Lambda: $5-10
- SQS: $0.40
- SNS: $0.50
- S3: $1-5
- DynamoDB: $0.25
- Polly: $4-16
- Claude API: $10-50
- **Total: $22-92/month**

See [DEPLOYMENT_REQUIREMENTS.md](DEPLOYMENT_REQUIREMENTS.md#cost-estimates) for detailed breakdown.

## Newsletter Sources

The system automatically recognizes newsletters from:
- TLDR AI
- Ben's Bites
- AI Secret
- AI Israel Weekly
- Aftershoot AI
- The Rundown AI
- AI Breakfast
- Import AI

Unknown sources are labeled as "Other AI Newsletter".

## Monitoring

### CloudWatch Metrics
- Lambda invocations, errors, duration
- SQS queue depth
- DynamoDB consumed capacity

### CloudWatch Alarms (Recommended)
- Lambda errors > 5 in 5 minutes
- SQS messages > 100 for 30 minutes
- Lambda duration approaching timeout

### Logs
- Lambda execution logs in CloudWatch
- Deployment logs in `deployment-*.log`
- Local execution logs in console

## Testing

### Test Lambda Function

```bash
# Via AWS CLI
aws lambda invoke \
  --function-name ai-newsletter-podcast-creator \
  --payload '{"test": true, "create_rss": false}' \
  response.json

# Check response
cat response.json
```

**Test mode options:**
- `{"test": true}` - Prevents SQS message deletion
- `{"create_rss": false}` - Skips RSS feed update

## Troubleshooting

### Common Issues

**Lambda timeout errors**
- Increase timeout in Lambda configuration (current: 600s)
- Reduce `MAX_MESSAGES` to process fewer emails per run

**Claude API rate limiting**
- Adjust `CLAUDE_RPM_LIMIT` based on your API tier
- Increase `CLAUDE_BASE_DELAY` for longer backoff

**Polly synthesis errors**
- Check for SSML-breaking characters in content
- Reduce text length if hitting Polly limits

**No emails in queue**
- Verify SES receipt rule is forwarding to SQS
- Check SQS queue permissions allow SES

### Debug Mode

Enable test mode to prevent SQS message deletion:

```bash
# Via environment variable
export TEST_MODE=true

# Via event JSON
{"test": true}
```

## Security Best Practices

1. **Secrets**: Claude API key stored in AWS Secrets Manager
2. **IAM**: Least-privilege permissions for Lambda role
3. **S3**: Public access only for podcast files, not entire bucket
4. **Encryption**: DynamoDB encryption at rest enabled
5. **Versioning**: S3 versioning enabled for podcast files
6. **Backups**: DynamoDB point-in-time recovery enabled

## Deployment Verification

After deployment, verify:
- [ ] S3 bucket created and accessible
- [ ] DynamoDB table exists
- [ ] SQS queue operational
- [ ] SNS subscriptions confirmed
- [ ] Lambda functions deployed
- [ ] EventBridge rules enabled
- [ ] Test invocation succeeds
- [ ] Audio generated and uploaded
- [ ] RSS feed created
- [ ] Email notification received

## Support

- **Issues**: [GitHub Issues](https://github.com/LinkTree/daily-ai-newsletter/issues)
- **Documentation**: See docs in this repository
- **Logs**: Check CloudWatch Logs for execution details

## License

See LICENSE file for details.

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Changelog

### v1.0.0 (2025-10-27)
- Initial release with dual-format generation
- Production deployment from AWS Lambda
- RSS feed control and newer Claude model
- Comprehensive deployment automation

---

Built with [Claude Code](https://claude.com/claude-code)
