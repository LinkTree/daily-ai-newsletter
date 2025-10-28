#!/bin/bash

################################################################################
# AI Newsletter Processing System - Full Infrastructure Deployment
#
# This script deploys the COMPLETE infrastructure to AWS including:
# Lambda, SQS, SNS, S3, DynamoDB, EventBridge, IAM, Secrets Manager, and Polly permissions.
#
# IMPORTANT:
# - Use this for INITIAL setup only
# - For Lambda updates, use: ./update-lambda.sh
# - This project ONLY manages: ai-newsletter-podcast-creator (daily processor)
#
# Usage:
#   ./deploy.sh --full --claude-api-key "sk-ant-..."
#
# Options:
#   --full                          REQUIRED: Deploy full infrastructure
#   --claude-api-key KEY            Claude API key (required)
#   --region REGION                 AWS region (default: eu-central-1)
#   --bucket-name NAME              Custom S3 bucket name (optional)
#   --notification-email EMAIL      Email for SNS notifications (optional)
#   --daily-schedule CRON           Daily trigger schedule (optional)
#   --dry-run                       Show what would be created without creating
#   --destroy                       Destroy all created resources
#   --help                          Show this help message
#
# Examples:
#   # Full infrastructure deployment (first time)
#   ./deploy.sh --full --claude-api-key "sk-ant-api03-..."
#
#   # Custom configuration
#   ./deploy.sh --full --claude-api-key "sk-ant-..." \
#     --region us-east-1 \
#     --bucket-name "my-podcast-bucket" \
#     --notification-email "admin@example.com"
#
#   # Dry run to preview changes
#   ./deploy.sh --full --claude-api-key "sk-ant-..." --dry-run
#
#   # Destroy all resources
#   ./deploy.sh --destroy
#
#   # For Lambda-only updates (after initial deployment):
#   ./update-lambda.sh
#
################################################################################

set -e  # Exit on error
set -o pipefail  # Exit on pipe failure

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
AWS_REGION="eu-central-1"  # Default to production region
CLAUDE_API_KEY=""
BUCKET_NAME=""
NOTIFICATION_EMAIL=""
DAILY_SCHEDULE="cron(0 10 * * ? *)"  # 10:00 UTC daily
DRY_RUN=false
DESTROY=false
FULL_DEPLOY=false  # Requires --full flag for safety

# Resource names (will be populated from config or generated)
STACK_NAME="ai-newsletter-processor"
SQS_QUEUE_NAME="ai-newsletter-emails"
SNS_TOPIC_NAME="ai-newsletter-notifications"
DYNAMODB_TABLE_NAME="ai_daily_news"
LAMBDA_NAME="ai-newsletter-podcast-creator"
IAM_ROLE_NAME="ai-newsletter-lambda-role"
SECRET_NAME="ai-newsletter/claude-api-key"
DAILY_RULE_NAME="ai-newsletter-daily-trigger"

# Deployment tracking
DEPLOYMENT_LOG="deployment-$(date +%Y%m%d-%H%M%S).log"
CREATED_RESOURCES=()

################################################################################
# Helper Functions
################################################################################

log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $*" | tee -a "$DEPLOYMENT_LOG"
}

success() {
    echo -e "${GREEN}✓${NC} $*" | tee -a "$DEPLOYMENT_LOG"
}

error() {
    echo -e "${RED}✗ ERROR:${NC} $*" | tee -a "$DEPLOYMENT_LOG"
    exit 1
}

warn() {
    echo -e "${YELLOW}⚠ WARNING:${NC} $*" | tee -a "$DEPLOYMENT_LOG"
}

track_resource() {
    CREATED_RESOURCES+=("$1")
}

show_help() {
    grep '^#' "$0" | grep -v '#!/bin/bash' | sed 's/^# //' | sed 's/^#//'
    exit 0
}

################################################################################
# Argument Parsing
################################################################################

while [[ $# -gt 0 ]]; do
    case $1 in
        --full)
            FULL_DEPLOY=true
            shift
            ;;
        --region)
            AWS_REGION="$2"
            shift 2
            ;;
        --claude-api-key)
            CLAUDE_API_KEY="$2"
            shift 2
            ;;
        --bucket-name)
            BUCKET_NAME="$2"
            shift 2
            ;;
        --notification-email)
            NOTIFICATION_EMAIL="$2"
            shift 2
            ;;
        --daily-schedule)
            DAILY_SCHEDULE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --destroy)
            DESTROY=true
            shift
            ;;
        --help)
            show_help
            ;;
        *)
            error "Unknown option: $1. Use --help for usage information."
            ;;
    esac
done

################################################################################
# Pre-flight Checks
################################################################################

preflight_checks() {
    log "Running pre-flight checks..."

    # Require --full flag for infrastructure deployment
    if [ "$FULL_DEPLOY" = false ] && [ "$DESTROY" = false ]; then
        error "This script deploys FULL infrastructure. Use --full flag to confirm.\n\nFor Lambda-only updates, use: ./update-lambda.sh\n\nFor full deployment: ./deploy.sh --full --claude-api-key \"...\"\nFor help: ./deploy.sh --help"
    fi

    # Check AWS CLI
    if ! command -v aws &> /dev/null; then
        error "AWS CLI is not installed. Please install it first."
    fi
    success "AWS CLI found: $(aws --version)"

    # Check Python
    if ! command -v python3 &> /dev/null; then
        error "Python 3 is not installed. Please install it first."
    fi
    success "Python found: $(python3 --version)"

    # Check pip
    if ! command -v pip3 &> /dev/null; then
        error "pip3 is not installed. Please install it first."
    fi
    success "pip3 found"

    # Check zip
    if ! command -v zip &> /dev/null; then
        error "zip utility is not installed. Please install it first."
    fi
    success "zip utility found"

    # Validate Claude API key
    if [ -z "$CLAUDE_API_KEY" ] && [ "$DESTROY" = false ]; then
        error "Claude API key is required. Use --claude-api-key to specify."
    fi

    # Set default bucket name if not provided
    if [ -z "$BUCKET_NAME" ] && [ "$DESTROY" = false ]; then
        ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
        BUCKET_NAME="ai-newsletter-podcasts-${ACCOUNT_ID}"
        log "Using default bucket name: $BUCKET_NAME"
    fi

    # Verify AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        error "AWS credentials are not configured. Run 'aws configure' first."
    fi
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    success "AWS credentials validated (Account: $ACCOUNT_ID)"

    # Check if files exist
    if [ ! -f "lambda_function.py" ]; then
        error "lambda_function.py not found in current directory"
    fi
    if [ ! -f "requirements.txt" ]; then
        error "requirements.txt not found in current directory"
    fi
    success "Required files found"
}

################################################################################
# Build Lambda Deployment Package
################################################################################

build_deployment_package() {
    log "Building Lambda deployment package..."

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would build deployment package"
        return
    fi

    # Create temporary build directory
    BUILD_DIR=$(mktemp -d)
    log "Build directory: $BUILD_DIR"

    # Copy Python files
    cp lambda_function.py "$BUILD_DIR/"
    cp requirements.txt "$BUILD_DIR/"

    # Install dependencies
    log "Installing Python dependencies..."
    pip3 install -r requirements.txt -t "$BUILD_DIR" --quiet || error "Failed to install dependencies"

    # Create ZIP file
    log "Creating deployment package..."
    cd "$BUILD_DIR"
    zip -r9 -q ../deployment-package.zip . || error "Failed to create ZIP file"
    cd - > /dev/null

    # Move to current directory
    mv "${BUILD_DIR}/../deployment-package.zip" ./deployment-package.zip

    # Cleanup
    rm -rf "$BUILD_DIR"

    PACKAGE_SIZE=$(du -h deployment-package.zip | cut -f1)
    success "Deployment package created: deployment-package.zip ($PACKAGE_SIZE)"
}

################################################################################
# Create S3 Bucket
################################################################################

create_s3_bucket() {
    log "Creating S3 bucket: $BUCKET_NAME..."

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would create S3 bucket: $BUCKET_NAME"
        return
    fi

    # Check if bucket exists
    if aws s3 ls "s3://$BUCKET_NAME" --region "$AWS_REGION" 2>/dev/null; then
        warn "S3 bucket $BUCKET_NAME already exists, skipping creation"
        return
    fi

    # Create bucket (different command for us-east-1)
    if [ "$AWS_REGION" = "us-east-1" ]; then
        aws s3api create-bucket \
            --bucket "$BUCKET_NAME" \
            --region "$AWS_REGION" || error "Failed to create S3 bucket"
    else
        aws s3api create-bucket \
            --bucket "$BUCKET_NAME" \
            --region "$AWS_REGION" \
            --create-bucket-configuration LocationConstraint="$AWS_REGION" || error "Failed to create S3 bucket"
    fi

    track_resource "s3:$BUCKET_NAME"

    # Enable versioning
    aws s3api put-bucket-versioning \
        --bucket "$BUCKET_NAME" \
        --versioning-configuration Status=Enabled \
        --region "$AWS_REGION"

    # Configure CORS for podcast players
    cat > /tmp/cors.json <<EOF
{
  "CORSRules": [
    {
      "AllowedOrigins": ["*"],
      "AllowedMethods": ["GET", "HEAD"],
      "AllowedHeaders": ["*"],
      "MaxAgeSeconds": 3000
    }
  ]
}
EOF

    aws s3api put-bucket-cors \
        --bucket "$BUCKET_NAME" \
        --cors-configuration file:///tmp/cors.json \
        --region "$AWS_REGION"

    # Set bucket policy for public read on podcasts
    cat > /tmp/bucket-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadGetObject",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::${BUCKET_NAME}/podcasts/*"
    },
    {
      "Sid": "PublicReadRSSFeed",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::${BUCKET_NAME}/feed.xml"
    }
  ]
}
EOF

    aws s3api put-bucket-policy \
        --bucket "$BUCKET_NAME" \
        --policy file:///tmp/bucket-policy.json \
        --region "$AWS_REGION"

    rm /tmp/cors.json /tmp/bucket-policy.json

    success "S3 bucket created and configured: $BUCKET_NAME"
}

################################################################################
# Create DynamoDB Table
################################################################################

create_dynamodb_table() {
    log "Creating DynamoDB table: $DYNAMODB_TABLE_NAME..."

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would create DynamoDB table: $DYNAMODB_TABLE_NAME"
        return
    fi

    # Check if table exists
    if aws dynamodb describe-table --table-name "$DYNAMODB_TABLE_NAME" --region "$AWS_REGION" &>/dev/null; then
        warn "DynamoDB table $DYNAMODB_TABLE_NAME already exists, skipping creation"
        return
    fi

    aws dynamodb create-table \
        --table-name "$DYNAMODB_TABLE_NAME" \
        --attribute-definitions AttributeName=date,AttributeType=S \
        --key-schema AttributeName=date,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region "$AWS_REGION" > /dev/null || error "Failed to create DynamoDB table"

    track_resource "dynamodb:$DYNAMODB_TABLE_NAME"

    # Wait for table to be active
    log "Waiting for table to be active..."
    aws dynamodb wait table-exists --table-name "$DYNAMODB_TABLE_NAME" --region "$AWS_REGION"

    # Enable point-in-time recovery
    aws dynamodb update-continuous-backups \
        --table-name "$DYNAMODB_TABLE_NAME" \
        --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true \
        --region "$AWS_REGION" > /dev/null

    success "DynamoDB table created: $DYNAMODB_TABLE_NAME"
}

################################################################################
# Create SQS Queue
################################################################################

create_sqs_queue() {
    log "Creating SQS queue: $SQS_QUEUE_NAME..."

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would create SQS queue: $SQS_QUEUE_NAME"
        return
    fi

    # Check if queue exists
    if aws sqs get-queue-url --queue-name "$SQS_QUEUE_NAME" --region "$AWS_REGION" &>/dev/null; then
        warn "SQS queue $SQS_QUEUE_NAME already exists, skipping creation"
        SQS_QUEUE_URL=$(aws sqs get-queue-url --queue-name "$SQS_QUEUE_NAME" --region "$AWS_REGION" --query QueueUrl --output text)
        return
    fi

    SQS_QUEUE_URL=$(aws sqs create-queue \
        --queue-name "$SQS_QUEUE_NAME" \
        --attributes VisibilityTimeout=900,ReceiveMessageWaitTimeSeconds=20,MessageRetentionPeriod=1209600 \
        --region "$AWS_REGION" \
        --query QueueUrl --output text) || error "Failed to create SQS queue"

    track_resource "sqs:$SQS_QUEUE_NAME"

    # Get queue ARN
    SQS_QUEUE_ARN=$(aws sqs get-queue-attributes \
        --queue-url "$SQS_QUEUE_URL" \
        --attribute-names QueueArn \
        --region "$AWS_REGION" \
        --query 'Attributes.QueueArn' --output text)

    success "SQS queue created: $SQS_QUEUE_NAME ($SQS_QUEUE_URL)"
}

################################################################################
# Create SNS Topic
################################################################################

create_sns_topic() {
    log "Creating SNS topic: $SNS_TOPIC_NAME..."

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would create SNS topic: $SNS_TOPIC_NAME"
        return
    fi

    SNS_TOPIC_ARN=$(aws sns create-topic \
        --name "$SNS_TOPIC_NAME" \
        --region "$AWS_REGION" \
        --query TopicArn --output text) || error "Failed to create SNS topic"

    track_resource "sns:$SNS_TOPIC_NAME"

    # Subscribe email if provided
    if [ -n "$NOTIFICATION_EMAIL" ]; then
        log "Subscribing email to SNS topic: $NOTIFICATION_EMAIL"
        aws sns subscribe \
            --topic-arn "$SNS_TOPIC_ARN" \
            --protocol email \
            --notification-endpoint "$NOTIFICATION_EMAIL" \
            --region "$AWS_REGION" > /dev/null
        warn "Please check $NOTIFICATION_EMAIL and confirm the subscription"
    fi

    success "SNS topic created: $SNS_TOPIC_NAME ($SNS_TOPIC_ARN)"
}

################################################################################
# Store Claude API Key in Secrets Manager
################################################################################

store_claude_api_key() {
    log "Storing Claude API key in Secrets Manager..."

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would store Claude API key in Secrets Manager: $SECRET_NAME"
        return
    fi

    # Check if secret exists
    if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$AWS_REGION" &>/dev/null; then
        warn "Secret $SECRET_NAME already exists, updating..."
        aws secretsmanager update-secret \
            --secret-id "$SECRET_NAME" \
            --secret-string "$CLAUDE_API_KEY" \
            --region "$AWS_REGION" > /dev/null
    else
        aws secretsmanager create-secret \
            --name "$SECRET_NAME" \
            --description "Claude API key for AI newsletter processing" \
            --secret-string "$CLAUDE_API_KEY" \
            --region "$AWS_REGION" > /dev/null || error "Failed to store secret"
        track_resource "secret:$SECRET_NAME"
    fi

    success "Claude API key stored in Secrets Manager"
}

################################################################################
# Create IAM Role
################################################################################

create_iam_role() {
    log "Creating IAM execution role: $IAM_ROLE_NAME..."

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would create IAM role: $IAM_ROLE_NAME"
        return
    fi

    # Check if role exists
    if aws iam get-role --role-name "$IAM_ROLE_NAME" &>/dev/null; then
        warn "IAM role $IAM_ROLE_NAME already exists, skipping creation"
        ROLE_ARN=$(aws iam get-role --role-name "$IAM_ROLE_NAME" --query 'Role.Arn' --output text)
        return
    fi

    # Create trust policy
    cat > /tmp/trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

    # Create role
    ROLE_ARN=$(aws iam create-role \
        --role-name "$IAM_ROLE_NAME" \
        --assume-role-policy-document file:///tmp/trust-policy.json \
        --description "Execution role for AI newsletter Lambda functions" \
        --query 'Role.Arn' --output text) || error "Failed to create IAM role"

    track_resource "iam:$IAM_ROLE_NAME"

    # Create and attach inline policy
    cat > /tmp/role-policy.json <<EOF
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
      "Resource": "arn:aws:sqs:${AWS_REGION}:${ACCOUNT_ID}:${SQS_QUEUE_NAME}"
    },
    {
      "Effect": "Allow",
      "Action": [
        "sns:Publish"
      ],
      "Resource": "arn:aws:sns:${AWS_REGION}:${ACCOUNT_ID}:${SNS_TOPIC_NAME}"
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
        "arn:aws:s3:::${BUCKET_NAME}/*",
        "arn:aws:s3:::${BUCKET_NAME}"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem"
      ],
      "Resource": "arn:aws:dynamodb:${AWS_REGION}:${ACCOUNT_ID}:table/${DYNAMODB_TABLE_NAME}"
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
        "secretsmanager:GetSecretValue"
      ],
      "Resource": "arn:aws:secretsmanager:${AWS_REGION}:${ACCOUNT_ID}:secret:${SECRET_NAME}*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:*"
    }
  ]
}
EOF

    aws iam put-role-policy \
        --role-name "$IAM_ROLE_NAME" \
        --policy-name "ai-newsletter-policy" \
        --policy-document file:///tmp/role-policy.json || error "Failed to attach policy to role"

    rm /tmp/trust-policy.json /tmp/role-policy.json

    # Wait for role to propagate
    log "Waiting for IAM role to propagate..."
    sleep 10

    success "IAM role created: $IAM_ROLE_NAME ($ROLE_ARN)"
}

################################################################################
# Update SQS Queue Policy
################################################################################

update_sqs_policy() {
    log "Updating SQS queue policy to allow SES..."

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would update SQS queue policy"
        return
    fi

    cat > /tmp/queue-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ses.amazonaws.com"
      },
      "Action": "sqs:SendMessage",
      "Resource": "${SQS_QUEUE_ARN}",
      "Condition": {
        "StringEquals": {
          "aws:SourceAccount": "${ACCOUNT_ID}"
        }
      }
    }
  ]
}
EOF

    aws sqs set-queue-attributes \
        --queue-url "$SQS_QUEUE_URL" \
        --attributes Policy="$(cat /tmp/queue-policy.json | jq -c .)" \
        --region "$AWS_REGION"

    rm /tmp/queue-policy.json

    success "SQS queue policy updated"
}

################################################################################
# Deploy Lambda Functions
################################################################################

deploy_lambda_function() {
    local FUNCTION_NAME=$1
    local HANDLER=$2
    local DESCRIPTION=$3

    log "Deploying Lambda function: $FUNCTION_NAME..."

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would deploy Lambda function: $FUNCTION_NAME"
        return
    fi

    # Environment variables
    local ENV_VARS="EMAIL_QUEUE_URL=${SQS_QUEUE_URL},SNS_TOPIC_ARN=${SNS_TOPIC_ARN},CLAUDE_API_KEY=${CLAUDE_API_KEY},PODCAST_S3_BUCKET=${BUCKET_NAME},DYNAMODB_TABLE_NAME=${DYNAMODB_TABLE_NAME}"

    # Check if function exists
    if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$AWS_REGION" &>/dev/null; then
        warn "Lambda function $FUNCTION_NAME already exists, updating code..."
        aws lambda update-function-code \
            --function-name "$FUNCTION_NAME" \
            --zip-file fileb://deployment-package.zip \
            --region "$AWS_REGION" > /dev/null

        aws lambda update-function-configuration \
            --function-name "$FUNCTION_NAME" \
            --handler "$HANDLER" \
            --runtime python3.10 \
            --timeout 600 \
            --memory-size 1024 \
            --environment "Variables={${ENV_VARS}}" \
            --region "$AWS_REGION" > /dev/null
    else
        aws lambda create-function \
            --function-name "$FUNCTION_NAME" \
            --runtime python3.10 \
            --role "$ROLE_ARN" \
            --handler "$HANDLER" \
            --zip-file fileb://deployment-package.zip \
            --timeout 600 \
            --memory-size 1024 \
            --environment "Variables={${ENV_VARS}}" \
            --description "$DESCRIPTION" \
            --region "$AWS_REGION" > /dev/null || error "Failed to create Lambda function $FUNCTION_NAME"

        track_resource "lambda:$FUNCTION_NAME"
    fi

    success "Lambda function deployed: $FUNCTION_NAME"
}

################################################################################
# Create EventBridge Rule
################################################################################

create_eventbridge_rule() {
    log "Creating EventBridge rule..."

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would create EventBridge rule"
        return
    fi

    # Daily trigger rule
    log "Creating daily trigger rule..."
    aws events put-rule \
        --name "$DAILY_RULE_NAME" \
        --schedule-expression "$DAILY_SCHEDULE" \
        --state ENABLED \
        --description "Daily trigger for AI newsletter processing" \
        --region "$AWS_REGION" > /dev/null || error "Failed to create daily rule"

    track_resource "events:$DAILY_RULE_NAME"

    # Get Lambda ARN
    LAMBDA_ARN=$(aws lambda get-function \
        --function-name "$LAMBDA_NAME" \
        --region "$AWS_REGION" \
        --query 'Configuration.FunctionArn' --output text)

    # Add Lambda permission
    aws lambda add-permission \
        --function-name "$LAMBDA_NAME" \
        --statement-id "${DAILY_RULE_NAME}-permission" \
        --action lambda:InvokeFunction \
        --principal events.amazonaws.com \
        --source-arn "arn:aws:events:${AWS_REGION}:${ACCOUNT_ID}:rule/${DAILY_RULE_NAME}" \
        --region "$AWS_REGION" &>/dev/null || warn "Permission may already exist"

    # Add target
    aws events put-targets \
        --rule "$DAILY_RULE_NAME" \
        --targets "Id=1,Arn=${LAMBDA_ARN}" \
        --region "$AWS_REGION" > /dev/null

    success "EventBridge rule created: $DAILY_SCHEDULE"
}

################################################################################
# Test Deployment
################################################################################

test_deployment() {
    log "Testing deployment..."

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would test deployment"
        return
    fi

    # Test Lambda function
    log "Testing Lambda function..."
    TEST_EVENT='{"test": true, "create_rss": false}'

    aws lambda invoke \
        --function-name "$LAMBDA_NAME" \
        --payload "$TEST_EVENT" \
        --region "$AWS_REGION" \
        /tmp/lambda-response.json > /dev/null

    if grep -q "Success" /tmp/lambda-response.json; then
        success "Lambda test passed"
    else
        warn "Lambda test may have issues, check logs"
        cat /tmp/lambda-response.json
    fi

    rm /tmp/lambda-response.json
}

################################################################################
# Destroy Resources
################################################################################

destroy_resources() {
    log "Destroying all resources..."

    warn "This will delete ALL resources created by this deployment script."
    read -p "Are you sure you want to continue? (yes/no): " -r
    if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        log "Destruction cancelled"
        exit 0
    fi

    # Delete EventBridge rule
    log "Deleting EventBridge rule..."
    aws events remove-targets --rule "$DAILY_RULE_NAME" --ids 1 --region "$AWS_REGION" &>/dev/null || true
    aws events delete-rule --name "$DAILY_RULE_NAME" --region "$AWS_REGION" &>/dev/null || true

    # Delete Lambda function
    log "Deleting Lambda function..."
    aws lambda delete-function --function-name "$LAMBDA_NAME" --region "$AWS_REGION" &>/dev/null || true

    # Delete IAM role
    log "Deleting IAM role..."
    aws iam delete-role-policy --role-name "$IAM_ROLE_NAME" --policy-name "ai-newsletter-policy" &>/dev/null || true
    aws iam delete-role --role-name "$IAM_ROLE_NAME" &>/dev/null || true

    # Delete secret
    log "Deleting Secrets Manager secret..."
    aws secretsmanager delete-secret --secret-id "$SECRET_NAME" --force-delete-without-recovery --region "$AWS_REGION" &>/dev/null || true

    # Delete SNS topic
    log "Deleting SNS topic..."
    SNS_ARN=$(aws sns list-topics --region "$AWS_REGION" --query "Topics[?contains(TopicArn, '${SNS_TOPIC_NAME}')].TopicArn" --output text)
    [ -n "$SNS_ARN" ] && aws sns delete-topic --topic-arn "$SNS_ARN" --region "$AWS_REGION" &>/dev/null || true

    # Delete SQS queue
    log "Deleting SQS queue..."
    QUEUE_URL=$(aws sqs get-queue-url --queue-name "$SQS_QUEUE_NAME" --region "$AWS_REGION" --query QueueUrl --output text 2>/dev/null)
    [ -n "$QUEUE_URL" ] && aws sqs delete-queue --queue-url "$QUEUE_URL" --region "$AWS_REGION" &>/dev/null || true

    # Delete DynamoDB table
    log "Deleting DynamoDB table..."
    aws dynamodb delete-table --table-name "$DYNAMODB_TABLE_NAME" --region "$AWS_REGION" &>/dev/null || true

    # Empty and delete S3 bucket
    log "Emptying and deleting S3 bucket..."
    if [ -n "$BUCKET_NAME" ]; then
        aws s3 rm "s3://${BUCKET_NAME}" --recursive --region "$AWS_REGION" &>/dev/null || true
        aws s3api delete-bucket --bucket "$BUCKET_NAME" --region "$AWS_REGION" &>/dev/null || true
    fi

    success "All resources destroyed"
}

################################################################################
# Display Summary
################################################################################

display_summary() {
    echo ""
    echo "=========================================================================="
    echo "                    DEPLOYMENT SUMMARY"
    echo "=========================================================================="
    echo ""
    echo "Region:              $AWS_REGION"
    echo "Account ID:          $ACCOUNT_ID"
    echo ""
    echo "Resources Created:"
    echo "  S3 Bucket:         $BUCKET_NAME"
    echo "  DynamoDB Table:    $DYNAMODB_TABLE_NAME"
    echo "  SQS Queue:         $SQS_QUEUE_NAME"
    echo "  SNS Topic:         $SNS_TOPIC_NAME"
    echo "  Secret:            $SECRET_NAME"
    echo "  IAM Role:          $IAM_ROLE_NAME"
    echo "  Lambda Function:   $LAMBDA_NAME"
    echo "  Trigger Schedule:  $DAILY_SCHEDULE"
    echo ""
    echo "Environment Variables (set in Lambda):"
    echo "  EMAIL_QUEUE_URL:         $SQS_QUEUE_URL"
    echo "  SNS_TOPIC_ARN:          $SNS_TOPIC_ARN"
    echo "  PODCAST_S3_BUCKET:      $BUCKET_NAME"
    echo "  DYNAMODB_TABLE_NAME:    $DYNAMODB_TABLE_NAME"
    echo ""
    echo "Next Steps:"
    echo "  1. If you subscribed an email to SNS, confirm the subscription"
    echo "  2. Configure SES to forward emails to the SQS queue"
    echo "  3. Upload podcast artwork to s3://${BUCKET_NAME}/podcast.png"
    echo "  4. Test the Lambda function manually or wait for scheduled trigger"
    echo "  5. Monitor CloudWatch logs for execution details"
    echo "  6. Use ./update-lambda.sh for future code updates"
    echo ""
    echo "Deployment log saved to: $DEPLOYMENT_LOG"
    echo "=========================================================================="
}

################################################################################
# Main Execution
################################################################################

main() {
    echo ""
    echo "=========================================================================="
    echo "       AI Newsletter Processing System - AWS Deployment"
    echo "=========================================================================="
    echo ""

    # Handle destroy mode
    if [ "$DESTROY" = true ]; then
        destroy_resources
        exit 0
    fi

    # Run pre-flight checks
    preflight_checks

    # Build deployment package
    build_deployment_package

    # Create infrastructure
    create_s3_bucket
    create_dynamodb_table
    create_sqs_queue
    create_sns_topic
    store_claude_api_key
    create_iam_role
    update_sqs_policy

    # Deploy Lambda function
    deploy_lambda_function "$LAMBDA_NAME" "lambda_function.lambda_handler" "Daily AI newsletter processor with podcast generation"

    # Create EventBridge rule
    create_eventbridge_rule

    # Test deployment
    test_deployment

    # Display summary
    display_summary

    success "Deployment completed successfully!"
}

# Run main function
main
