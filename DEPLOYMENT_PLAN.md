# Deployment Plan

## Overview

This document outlines the step-by-step deployment process for the AI Newsletter Processing system to AWS. The deployment script will automate most of these steps.

## Deployment Strategy

### Phase 1: Prerequisites & Preparation
**Duration**: 15-30 minutes

1. **Obtain External Credentials**
   - Get Claude API key from console.anthropic.com
   - Store securely (will add to AWS Secrets Manager)

2. **Prepare AWS Account**
   - Ensure AWS CLI is configured with appropriate credentials
   - Verify permissions to create IAM roles, Lambda, S3, etc.
   - Choose deployment region (recommend: eu-central-1, production default)

3. **Prepare Deployment Artifacts**
   - Build Lambda deployment package with dependencies
   - Prepare podcast artwork (optional but recommended)
   - Prepare test newsletter emails for validation

### Phase 2: Core Infrastructure
**Duration**: 10-15 minutes

4. **Create S3 Bucket**
   - Bucket name: `ai-newsletter-podcasts-${ACCOUNT_ID}` (globally unique)
   - Enable versioning
   - Configure CORS for web podcast players
   - Upload podcast artwork (if available)
   - Set public read policy for podcast files

5. **Create DynamoDB Table**
   - Table name: `ai_daily_news`
   - Partition key: `date` (String)
   - On-demand billing mode
   - Enable point-in-time recovery

6. **Create SQS Queue**
   - Queue name: `ai-newsletter-emails`
   - Standard queue type
   - Visibility timeout: 900 seconds
   - Enable long polling (20 seconds)
   - Optional: Create dead letter queue

7. **Create SNS Topic**
   - Topic name: `ai-newsletter-notifications`
   - Subscribe email addresses for notifications
   - Confirm subscriptions

### Phase 3: IAM & Security
**Duration**: 5-10 minutes

8. **Create Secrets Manager Entry**
   - Secret name: `ai-newsletter/claude-api-key`
   - Store Claude API key securely
   - Grant Lambda execution role access

9. **Create IAM Execution Role**
   - Role name: `ai-newsletter-lambda-role`
   - Trust policy: Allow Lambda service
   - Attach inline policy with required permissions:
     - SQS: ReceiveMessage, DeleteMessage, GetQueueAttributes
     - SNS: Publish
     - S3: GetObject, PutObject, ListBucket
     - DynamoDB: GetItem, PutItem
     - Polly: SynthesizeSpeech
     - Secrets Manager: GetSecretValue
     - CloudWatch Logs: CreateLogGroup, CreateLogStream, PutLogEvents

10. **Configure SQS Queue Policy**
    - Allow SES to send messages to queue
    - Restrict to specific AWS account

### Phase 4: Lambda Function
**Duration**: 5-10 minutes

11. **Deploy Lambda Function**
    - Function name: `ai-newsletter-podcast-creator`
    - Runtime: Python 3.10
    - Handler: `lambda_function.lambda_handler`
    - Memory: 1024 MB
    - Timeout: 600 seconds
    - Execution role: `ai-newsletter-lambda-role`
    - Upload deployment package (ZIP)
    - Configure environment variables

### Phase 5: Event Trigger
**Duration**: 5 minutes

12. **Create EventBridge Rule**
    - Daily rule: Triggers processor at 10:00 UTC
    - Grant EventBridge permission to invoke Lambda

### Phase 6: Email Ingestion (Optional)
**Duration**: 15-20 minutes

13. **Configure SES Receipt Rules**
    - Verify domain or email address in SES
    - Create receipt rule set (if not exists)
    - Add rule to forward emails to SQS queue
    - Test email forwarding

### Phase 7: Testing & Validation
**Duration**: 15-20 minutes

14. **Test Lambda Function**
    - Manual invocation with test event
    - Verify SQS message processing
    - Check Claude API integration
    - Verify Polly audio generation
    - Confirm S3 upload of MP3 and RSS feed
    - Validate DynamoDB storage
    - Check SNS notification

15. **End-to-End Test**
    - Send test newsletter via email (if SES configured)
    - Wait for scheduled trigger or invoke manually
    - Verify complete workflow

### Phase 8: Monitoring & Cleanup
**Duration**: 10 minutes

16. **Configure Monitoring**
    - CloudWatch dashboard for key metrics
    - Alarms for errors and throttling
    - Log insights queries for debugging

17. **Clean Up Test Resources**
    - Remove test messages from SQS
    - Delete test podcast files (if needed)
    - Clear test DynamoDB entries (if needed)

18. **Documentation**
    - Document all resource ARNs
    - Save configuration for disaster recovery
    - Create runbook for operations

## Deployment Order (Script Execution)

The deployment script will execute in this order:

```
1. Validate prerequisites (AWS CLI, region, Claude API key)
2. Create S3 bucket
3. Create DynamoDB table
4. Create SQS queue
5. Create SNS topic
6. Store Claude API key in Secrets Manager
7. Create IAM execution role with policies
8. Update SQS queue policy
9. Build Lambda deployment package
10. Deploy Lambda function (ai-newsletter-podcast-creator)
11. Create EventBridge rule (daily trigger at 10:00 UTC)
12. Test Lambda function
13. Display deployment summary
```

## Rollback Strategy

If deployment fails:

1. **Partial Deployment**: Script will track created resources and offer cleanup
2. **Manual Rollback**: Delete resources in reverse order:
   - EventBridge rule
   - Lambda function
   - IAM role
   - Secrets Manager secret
   - SNS topic (unsubscribe emails first)
   - SQS queue
   - DynamoDB table
   - S3 bucket (empty first)

3. **CloudFormation Alternative**: Consider using CloudFormation template for atomic deployment

## Post-Deployment Configuration

After successful deployment:

1. **Subscribe Email Addresses**
   - Confirm SNS topic subscriptions from email
   - Add additional subscribers if needed

2. **Configure SES** (if using email ingestion)
   - Verify domain/email
   - Set up receipt rules
   - Test email forwarding

3. **Upload Podcast Artwork**
   - Create/obtain podcast cover image (3000x3000px recommended)
   - Upload to S3 bucket as `podcast.png`
   - Update PODCAST_IMAGE_URL environment variable

4. **Test with Real Newsletters**
   - Forward actual newsletters to SQS queue
   - Monitor first few executions
   - Adjust timeout/memory if needed

5. **Tune Configuration**
   - Adjust CLAUDE_RPM_LIMIT based on API tier
   - Configure MAX_MESSAGES for batch size
   - Tune POLLY_VOICE and POLLY_RATE for audio quality

## Deployment Verification Checklist

After deployment, verify:

- [ ] S3 bucket created and accessible
- [ ] DynamoDB table exists with correct schema
- [ ] SQS queue created with proper visibility timeout
- [ ] SNS topic created and email subscriptions confirmed
- [ ] Claude API key stored in Secrets Manager
- [ ] IAM role has all required permissions
- [ ] Lambda function deployed with correct configuration
- [ ] EventBridge rule created and enabled
- [ ] Test invocation succeeds
- [ ] Audio file generated and uploaded to S3
- [ ] RSS feed created/updated in S3
- [ ] DynamoDB entry created with summary
- [ ] SNS notification received via email
- [ ] CloudWatch logs show successful execution
- [ ] No errors in CloudWatch metrics

## Estimated Total Deployment Time

- **Automated (using script)**: 15-20 minutes
- **Manual**: 60-90 minutes
- **Including SES setup**: +30 minutes
- **Including testing**: +30 minutes

**Total**: 1.5 - 2.5 hours for complete setup with testing

## Prerequisites for Script Execution

Before running the deployment script:

1. **AWS CLI installed and configured**
   ```bash
   aws --version  # Should be 2.x
   aws sts get-caller-identity  # Verify credentials
   ```

2. **Required permissions**
   - IAM: Create roles and policies
   - Lambda: Create functions
   - S3: Create and configure buckets
   - DynamoDB: Create tables
   - SQS: Create queues
   - SNS: Create topics
   - Secrets Manager: Create secrets
   - EventBridge: Create rules
   - CloudWatch: Create log groups

3. **Environment ready**
   - Python 3.10+ installed
   - pip available for installing dependencies
   - zip utility available
   - Sufficient disk space for building deployment package

4. **Configuration prepared**
   - Claude API key obtained
   - Deployment region chosen
   - Email addresses for notifications ready
   - (Optional) Podcast artwork ready

## Script Usage

```bash
# Full deployment (requires --full flag)
./deploy.sh --full --claude-api-key "sk-ant-..."

# With custom configuration
./deploy.sh --full \
  --region eu-central-1 \
  --claude-api-key "sk-ant-..." \
  --bucket-name "my-custom-bucket" \
  --notification-email "admin@example.com" \
  --daily-schedule "cron(0 9 * * ? *)"

# Dry run (show what would be created)
./deploy.sh --full --claude-api-key "sk-ant-..." --dry-run

# Cleanup/destroy all resources
./deploy.sh --destroy
```

## Next Steps

After successful deployment:

1. Review the [Operations Guide](OPERATIONS.md) (to be created)
2. Set up monitoring dashboards
3. Configure cost alerts
4. Schedule regular backups
5. Plan for scaling if needed
6. Document any customizations
