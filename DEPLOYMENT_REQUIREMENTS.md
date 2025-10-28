# Deployment Requirements

## AWS Services Required

### 1. Lambda Function
- **Function Name**: `ai-newsletter-podcast-creator`
  - Runtime: Python 3.10
  - Handler: `lambda_function.lambda_handler`
  - Timeout: 600 seconds (10 minutes)
  - Memory: 1024 MB
  - Architecture: x86_64 or arm64
  - Description: Daily AI newsletter processor with podcast generation

### 2. SQS Queue
- **Name**: `ai-newsletter-emails`
- **Type**: Standard queue
- **Visibility Timeout**: 900 seconds (15 minutes)
- **Message Retention**: 14 days
- **Receive Wait Time**: 20 seconds (long polling)
- **Dead Letter Queue**: Recommended for failed messages

### 3. SNS Topic
- **Name**: `ai-newsletter-notifications`
- **Type**: Standard
- **Subscriptions**: Email addresses for receiving summaries
- **Display Name**: "AI Newsletter Notifications"

### 4. S3 Bucket
- **Name**: `ai-newsletter-podcasts` (or custom name)
- **Region**: Same as Lambda function
- **Purpose**: Store podcast MP3 files and RSS feed
- **Versioning**: Enabled (recommended)
- **Public Access**:
  - Block all public access: NO
  - Files need to be publicly accessible for podcast distribution
- **CORS Configuration**: Required for web players
- **Lifecycle Policy**: Optional - archive old podcasts after 90 days

### 5. DynamoDB Table
- **Name**: `ai_daily_news`
- **Partition Key**: `date` (String) - format: YYYY-MM-DD
- **Billing Mode**: On-demand or Provisioned (1 RCU, 1 WCU)
- **Point-in-time Recovery**: Enabled (recommended)
- **Encryption**: AWS managed key

### 6. SES (Simple Email Service)
- **Purpose**: Receive newsletter emails
- **Configuration**:
  - Verify domain or email address
  - Create receipt rule set
  - Add rule to forward emails to SQS queue
  - S3 bucket for email storage (optional)

### 7. EventBridge Rule
- **Daily Trigger**:
  - Name: `ai-newsletter-daily-trigger`
  - Schedule: `cron(0 10 * * ? *)` - Daily at 10:00 UTC
  - Target: `ai-newsletter-podcast-creator` Lambda function
  - State: ENABLED

## IAM Permissions

### Lambda Execution Role
The Lambda function requires the following permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
        "sqs:DeleteMessageBatch"
      ],
      "Resource": "arn:aws:sqs:REGION:ACCOUNT_ID:ai-newsletter-emails"
    },
    {
      "Effect": "Allow",
      "Action": [
        "sns:Publish"
      ],
      "Resource": "arn:aws:sns:REGION:ACCOUNT_ID:ai-newsletter-notifications"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:PutObjectAcl",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::BUCKET_NAME/*",
        "arn:aws:s3:::BUCKET_NAME"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem"
      ],
      "Resource": "arn:aws:dynamodb:REGION:ACCOUNT_ID:table/ai_daily_news"
    },
    {
      "Effect": "Allow",
      "Action": [
        "polly:SynthesizeSpeech"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:REGION:ACCOUNT_ID:*"
    }
  ]
}
```

### SQS Queue Policy
Allow SES to send messages to the queue:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ses.amazonaws.com"
      },
      "Action": "sqs:SendMessage",
      "Resource": "arn:aws:sqs:REGION:ACCOUNT_ID:ai-newsletter-emails",
      "Condition": {
        "StringEquals": {
          "aws:SourceAccount": "ACCOUNT_ID"
        }
      }
    }
  ]
}
```

## Environment Variables

### Required
- `EMAIL_QUEUE_URL`: SQS queue URL (from SQS console)
- `SNS_TOPIC_ARN`: SNS topic ARN (from SNS console)
- `CLAUDE_API_KEY`: Anthropic Claude API key (from console.anthropic.com)

### Optional (with defaults)
- `MAX_MESSAGES=50`: Maximum SQS messages to process per run
- `MAX_LINKS_PER_EMAIL=5`: Maximum links to fetch per newsletter
- `DYNAMODB_TABLE_NAME=ai_daily_news`: DynamoDB table name
- `PODCAST_S3_BUCKET=ai-newsletter-podcasts`: S3 bucket for audio
- `POLLY_VOICE=Joanna`: AWS Polly voice ID
- `POLLY_RATE=medium`: Speech rate (slow/medium/fast)
- `GENERATE_AUDIO=true`: Enable/disable audio generation
- `UPDATE_RSS_FEED=true`: Enable/disable RSS feed updates
- `PODCAST_IMAGE_URL`: URL to podcast artwork (optional)
- `CLAUDE_RPM_LIMIT=5`: Max Claude API requests per minute
- `CLAUDE_MAX_RETRIES=6`: Max retry attempts for rate limits
- `CLAUDE_BASE_DELAY=10`: Base delay for exponential backoff (seconds)
- `TEST_MODE=false`: Enable test mode (no SQS message deletion)

## Python Dependencies

From `requirements.txt`:
- boto3==1.34.0
- requests==2.31.0
- beautifulsoup4==4.12.2

Optional (not in requirements.txt but beneficial):
- tiktoken (for accurate token counting with Claude)
- lxml (for enhanced HTML parsing)

## External Dependencies

### Anthropic Claude API
- **Service**: Claude AI API
- **Endpoint**: https://api.anthropic.com/v1/messages
- **Model**: claude-sonnet-4-5-20250929
- **Authentication**: API key required
- **Rate Limits**: Configurable (default: 5 requests/minute)
- **Pricing**: Pay per token (input + output)

## Network Requirements

### Outbound Internet Access
Lambda function requires outbound internet access for:
- Claude API calls (api.anthropic.com)
- Web scraping newsletter links (various domains)

**Options**:
1. **Public subnet with Internet Gateway** (simpler, recommended for this use case)
2. **Private subnet with NAT Gateway** (more secure but more expensive)

### VPC Configuration (if using VPC)
- Subnet: Public or Private with NAT
- Security Group: Allow outbound HTTPS (443)
- VPC Endpoints: Optional for S3, DynamoDB, SQS, SNS (reduces NAT costs)

## Deployment Artifacts

### Lambda Deployment Package
- **Format**: ZIP file
- **Contents**:
  - Python code (lambda_function.py)
  - Python dependencies in root directory
- **Size**: ~14 MB (based on production)
- **Build Process**:
  ```bash
  pip install -r requirements.txt -t .
  zip -r function.zip .
  ```

### S3 Bucket Setup
Required files in S3:
- `podcasts/*.mp3` - Generated podcast episodes
- `feed.xml` - RSS podcast feed
- `podcast.png` - Podcast artwork (optional but recommended)

## Cost Estimates

### Monthly Costs (approximate, based on moderate usage)
- **Lambda**: $5-10 (execution time for processing)
- **SQS**: $0.40 (40,000 requests)
- **SNS**: $0.50 (email notifications)
- **S3**: $1-5 (storage + requests)
- **DynamoDB**: $0.25 (on-demand pricing)
- **Polly**: $4-16 (100K-400K characters)
- **Data Transfer**: $1-5 (S3 egress for podcast downloads)
- **Claude API**: $10-50 (depends on newsletter volume and size)

**Total estimated**: $22-92/month

## Regional Considerations

### Recommended Regions
- **us-east-1** (N. Virginia): Lowest cost, all services available
- **eu-central-1** (Frankfurt): Current production region, GDPR compliant
- **us-west-2** (Oregon): Good alternative to us-east-1

### Service Availability
All required services are available in most regions. Check:
- AWS Polly voice availability (Joanna is available in most regions)
- SES receipt rules (only available in certain regions)

## Security Best Practices

1. **Secrets Management**: Store CLAUDE_API_KEY in AWS Secrets Manager or SSM Parameter Store
2. **S3 Bucket Security**:
   - Enable bucket logging
   - Use bucket policies to restrict access
   - Enable versioning for podcast files
3. **Lambda Security**:
   - Principle of least privilege for IAM roles
   - Enable X-Ray tracing for debugging
   - Use VPC endpoints when possible
4. **DynamoDB**: Enable point-in-time recovery
5. **Monitoring**: Set up CloudWatch alarms for errors and throttles

## Monitoring & Logging

### CloudWatch Metrics
- Lambda invocations, errors, duration, throttles
- SQS queue depth, age of oldest message
- DynamoDB consumed capacity
- S3 bucket size

### CloudWatch Alarms (Recommended)
- Lambda errors > 5 in 5 minutes
- SQS messages in queue > 100 for 30 minutes
- Lambda duration approaching timeout
- DynamoDB throttling events

### CloudWatch Logs
- Lambda execution logs (automatic)
- Log retention: 30 days (configurable)
- Log insights queries for debugging

## Backup & Disaster Recovery

1. **DynamoDB**: Point-in-time recovery enabled
2. **S3**: Versioning enabled, lifecycle policies for archival
3. **Lambda**: Code stored in GitHub (version control)
4. **Configuration**: Infrastructure as Code (deployment script)

## Pre-Deployment Checklist

- [ ] AWS account with appropriate permissions
- [ ] Claude API key obtained from Anthropic
- [ ] Domain/email verified in SES (if using email ingestion)
- [ ] S3 bucket name chosen (must be globally unique)
- [ ] Email addresses for SNS notifications prepared
- [ ] Region selected for deployment
- [ ] Cost estimates reviewed and approved
- [ ] Podcast artwork prepared (PNG/JPG, recommended 3000x3000px)
- [ ] Test newsletters available for initial testing
