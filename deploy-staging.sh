#!/bin/bash

################################################################################
# AI Newsletter Staging Environment Deployment
#
# Creates staging infrastructure:
# - Staging DynamoDB table
# - Staging Lambda function
# - Staging S3 structure (folder + empty RSS)
#
# Usage:
#   ./deploy-staging.sh
#   ./deploy-staging.sh --region us-east-1
#
# Prerequisites:
#   - Production infrastructure must exist (deploy.sh --full already run)
#   - AWS CLI configured
#   - In project directory with lambda_function.py
################################################################################

set -e
set -o pipefail

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Default values
AWS_REGION="${AWS_REGION:-eu-central-1}"
DRY_RUN=false

# Deployment tracking
DEPLOY_LOG="deploy-staging-$(date +%Y%m%d-%H%M%S).log"

################################################################################
# Helper Functions
################################################################################

log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $*" | tee -a "$DEPLOY_LOG"
}

success() {
    echo -e "${GREEN}✓${NC} $*" | tee -a "$DEPLOY_LOG"
}

error() {
    echo -e "${RED}✗ ERROR:${NC} $*" | tee -a "$DEPLOY_LOG"
    exit 1
}

warn() {
    echo -e "${YELLOW}⚠ WARNING:${NC} $*" | tee -a "$DEPLOY_LOG"
}

show_help() {
    cat << EOF
AI Newsletter Staging Deployment

Usage:
    ./deploy-staging.sh [OPTIONS]

Options:
    --region REGION     AWS region (default: eu-central-1)
    --dry-run           Show what would be created without making changes
    --help              Show this help message

Examples:
    # Deploy to eu-central-1 (default)
    ./deploy-staging.sh

    # Deploy to us-east-1
    ./deploy-staging.sh --region us-east-1

    # Preview changes without deploying
    ./deploy-staging.sh --dry-run
EOF
    exit 0
}

################################################################################
# Argument Parsing
################################################################################

while [[ $# -gt 0 ]]; do
    case $1 in
        --region)
            AWS_REGION="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
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

    # Check AWS CLI
    if ! command -v aws &> /dev/null; then
        error "AWS CLI is not installed"
    fi
    success "AWS CLI found: $(aws --version)"

    # Verify AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        error "AWS credentials not configured"
    fi
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    success "AWS credentials validated (Account: $ACCOUNT_ID, Region: $AWS_REGION)"

    # Check required files
    if [ ! -f "lambda_function.py" ]; then
        error "lambda_function.py not found"
    fi
    if [ ! -f "requirements.txt" ]; then
        error "requirements.txt not found"
    fi
    success "Required files found"

    # Verify production infrastructure exists
    log "Checking production infrastructure..."

    # Check Lambda function to get IAM role
    PROD_LAMBDA_CONFIG=$(aws lambda get-function --function-name ai-newsletter-podcast-creator --region "$AWS_REGION" 2>/dev/null || echo "")
    if [ -z "$PROD_LAMBDA_CONFIG" ]; then
        error "Production Lambda 'ai-newsletter-podcast-creator' not found. Deploy production first."
    fi

    IAM_ROLE_ARN=$(aws lambda get-function --function-name ai-newsletter-podcast-creator --region "$AWS_REGION" --query 'Configuration.Role' --output text)
    success "Production Lambda exists, using IAM role: ${IAM_ROLE_ARN}"

    # Get S3 bucket from production Lambda env vars
    S3_BUCKET=$(aws lambda get-function-configuration --function-name ai-newsletter-podcast-creator --region "$AWS_REGION" --query 'Environment.Variables.PODCAST_S3_BUCKET' --output text 2>/dev/null || echo "")
    if [ -z "$S3_BUCKET" ] || [ "$S3_BUCKET" = "None" ]; then
        error "Could not determine S3 bucket from production Lambda"
    fi
    if ! aws s3 ls "s3://${S3_BUCKET}" &>/dev/null; then
        error "S3 bucket '${S3_BUCKET}' not accessible"
    fi
    success "S3 bucket exists: ${S3_BUCKET}"

    # Get SQS queue URL from production Lambda env vars
    QUEUE_URL=$(aws lambda get-function-configuration --function-name ai-newsletter-podcast-creator --region "$AWS_REGION" --query 'Environment.Variables.EMAIL_QUEUE_URL' --output text 2>/dev/null || echo "")
    if [ -z "$QUEUE_URL" ] || [ "$QUEUE_URL" = "None" ]; then
        error "Could not determine SQS queue URL from production Lambda"
    fi
    success "SQS queue exists: ${QUEUE_URL}"

    # Get SNS topic ARN from production Lambda env vars
    SNS_TOPIC_ARN=$(aws lambda get-function-configuration --function-name ai-newsletter-podcast-creator --region "$AWS_REGION" --query 'Environment.Variables.SNS_TOPIC_ARN' --output text 2>/dev/null || echo "")
    if [ -z "$SNS_TOPIC_ARN" ] || [ "$SNS_TOPIC_ARN" = "None" ]; then
        error "Could not determine SNS topic ARN from production Lambda"
    fi
    success "SNS topic exists: ${SNS_TOPIC_ARN}"

    # Get Claude API key from production Lambda env vars
    CLAUDE_API_KEY=$(aws lambda get-function-configuration --function-name ai-newsletter-podcast-creator --region "$AWS_REGION" --query 'Environment.Variables.CLAUDE_API_KEY' --output text 2>/dev/null || echo "")
    if [ -z "$CLAUDE_API_KEY" ] || [ "$CLAUDE_API_KEY" = "None" ]; then
        # Try environment variable as fallback
        if [ -n "$CLAUDE_API_KEY" ]; then
            success "Claude API key found in environment variable"
        else
            error "CLAUDE_API_KEY not found in production Lambda or environment"
        fi
    else
        success "Claude API key retrieved from production Lambda configuration"
    fi
}

################################################################################
# Create Staging DynamoDB Table
################################################################################

create_staging_dynamodb_table() {
    log "Creating staging DynamoDB table..."

    TABLE_NAME="ai_daily_news_staging"

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would create DynamoDB table: ${TABLE_NAME}"
        return
    fi

    # Check if table exists
    if aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$AWS_REGION" &>/dev/null; then
        success "Staging DynamoDB table already exists"
        return
    fi

    # Create table
    aws dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions AttributeName=date,AttributeType=S \
        --key-schema AttributeName=date,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region "$AWS_REGION" \
        --tags Key=Environment,Value=staging Key=Project,Value=ai-newsletter || error "Failed to create staging DynamoDB table"

    log "Waiting for table to be active..."
    aws dynamodb wait table-exists --table-name "$TABLE_NAME" --region "$AWS_REGION" || warn "Table creation timed out"

    success "Staging DynamoDB table created: ${TABLE_NAME}"
}

################################################################################
# Initialize S3 Staging Structure
################################################################################

initialize_staging_s3_structure() {
    log "Initializing S3 staging structure..."

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would create S3 staging structure in ${S3_BUCKET}"
        return
    fi

    # Create staging/podcasts/ folder (by uploading empty placeholder)
    log "Creating staging/podcasts/ folder..."
    echo "" | aws s3 cp - "s3://${S3_BUCKET}/staging/podcasts/.keep" \
        --region "$AWS_REGION" || warn "Could not create staging folder"

    # Create empty staging RSS feed
    log "Creating empty staging RSS feed..."
    cat > /tmp/feed-staging-init.xml << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
    <channel>
        <title>Daily AI, by AI (Staging)</title>
        <link>https://BUCKET.s3.amazonaws.com/feed-staging.xml</link>
        <language>en-us</language>
        <itunes:author>Daily AI, by AI (Staging)</itunes:author>
        <description>Staging environment for Daily AI podcast. This feed is for testing purposes only.</description>
        <itunes:explicit>false</itunes:explicit>
        <itunes:category text="Technology"/>
        <itunes:category text="News"/>
    </channel>
</rss>
EOF

    # Replace BUCKET placeholder with actual bucket name
    sed -i.bak "s|BUCKET|${S3_BUCKET}|g" /tmp/feed-staging-init.xml

    # Upload staging RSS feed
    aws s3 cp /tmp/feed-staging-init.xml "s3://${S3_BUCKET}/feed-staging.xml" \
        --content-type "application/rss+xml" \
        --region "$AWS_REGION" || error "Failed to create staging RSS feed"

    # Cleanup
    rm -f /tmp/feed-staging-init.xml /tmp/feed-staging-init.xml.bak

    success "S3 staging structure initialized"
    log "  - Folder: s3://${S3_BUCKET}/staging/podcasts/"
    log "  - RSS feed: s3://${S3_BUCKET}/feed-staging.xml"
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
# Deploy Staging Lambda Function
################################################################################

deploy_staging_lambda() {
    log "Deploying staging Lambda function..."

    STAGING_FUNCTION_NAME="ai-newsletter-podcast-creator-staging"
    # IAM_ROLE_ARN already set from preflight_checks

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would deploy Lambda function: ${STAGING_FUNCTION_NAME}"
        return
    fi

    # Create environment variables JSON file
    ENV_JSON="/tmp/staging-env-vars-$$.json"
    cat > "$ENV_JSON" <<EOF
{
    "Variables": {
        "EMAIL_QUEUE_URL": "${QUEUE_URL}",
        "SNS_TOPIC_ARN": "${SNS_TOPIC_ARN}",
        "CLAUDE_API_KEY": "${CLAUDE_API_KEY}",
        "PODCAST_S3_BUCKET": "${S3_BUCKET}",
        "DYNAMODB_TABLE_NAME": "ai_daily_news_staging",
        "ENVIRONMENT": "staging",
        "TEST_MODE": "true",
        "S3_KEY_PREFIX": "staging/",
        "RSS_FEED_NAME": "feed-staging.xml",
        "NOTIFICATION_PREFIX": "[STAGING] ",
        "PODCAST_TITLE": "Daily AI, by AI (Staging)",
        "PODCAST_TITLE_SHORT": "Daily AI Staging",
        "MAX_MESSAGES": "50",
        "MAX_LINKS_PER_EMAIL": "5",
        "GENERATE_AUDIO": "true",
        "UPDATE_RSS_FEED": "true",
        "POLLY_VOICE": "Joanna",
        "POLLY_RATE": "medium",
        "CLAUDE_RPM_LIMIT": "5",
        "CLAUDE_MAX_RETRIES": "6",
        "CLAUDE_BASE_DELAY": "10"
    }
}
EOF

    # Check if function exists
    if aws lambda get-function --function-name "$STAGING_FUNCTION_NAME" --region "$AWS_REGION" &>/dev/null; then
        log "Updating existing staging function..."
        aws lambda update-function-code \
            --function-name "$STAGING_FUNCTION_NAME" \
            --zip-file fileb://deployment-package.zip \
            --region "$AWS_REGION" || error "Failed to update staging function code"

        aws lambda update-function-configuration \
            --function-name "$STAGING_FUNCTION_NAME" \
            --environment "file://${ENV_JSON}" \
            --region "$AWS_REGION" || warn "Failed to update staging function config"
    else
        log "Creating new staging function..."
        aws lambda create-function \
            --function-name "$STAGING_FUNCTION_NAME" \
            --runtime python3.10 \
            --handler lambda_function.lambda_handler \
            --role "$IAM_ROLE_ARN" \
            --zip-file fileb://deployment-package.zip \
            --timeout 600 \
            --memory-size 1024 \
            --region "$AWS_REGION" \
            --environment "file://${ENV_JSON}" \
            --description "Staging environment for AI Newsletter podcast creator" \
            --tags Environment=staging,Project=ai-newsletter || error "Failed to create staging function"
    fi

    # Cleanup env vars file
    rm -f "$ENV_JSON"

    # Wait for function to be ready
    log "Waiting for function to be ready..."
    aws lambda wait function-updated --function-name "$STAGING_FUNCTION_NAME" --region "$AWS_REGION" || warn "Wait timed out"

    success "Staging Lambda function deployed: ${STAGING_FUNCTION_NAME}"
}

################################################################################
# Test Staging Function
################################################################################

test_staging_function() {
    log "Testing staging function..."

    STAGING_FUNCTION_NAME="ai-newsletter-podcast-creator-staging"

    if [ "$DRY_RUN" = true ]; then
        log "[DRY RUN] Would test staging function"
        return
    fi

    log "You can test the staging function with:"
    echo ""
    echo "  aws lambda invoke \\"
    echo "    --function-name ${STAGING_FUNCTION_NAME} \\"
    echo "    --region ${AWS_REGION} \\"
    echo "    response.json"
    echo ""
    log "Test now? (y/N)"
    read -r -t 10 RESPONSE || RESPONSE="n"

    if [[ "$RESPONSE" =~ ^[Yy]$ ]]; then
        log "Invoking staging function..."
        aws lambda invoke \
            --function-name "$STAGING_FUNCTION_NAME" \
            --region "$AWS_REGION" \
            response.json || warn "Function invocation failed"

        if [ -f response.json ]; then
            log "Response:"
            cat response.json | jq '.' 2>/dev/null || cat response.json
            rm response.json
        fi
    else
        log "Skipping test (test manually later)"
    fi
}

################################################################################
# Display Summary
################################################################################

display_summary() {
    echo ""
    echo "=========================================================================="
    echo "              STAGING DEPLOYMENT SUMMARY"
    echo "=========================================================================="
    echo ""
    echo "Region:              $AWS_REGION"
    echo "Account ID:          $ACCOUNT_ID"
    echo ""
    echo "Resources Created:"
    echo "  DynamoDB Table:    ai_daily_news_staging"
    echo "  Lambda Function:   ai-newsletter-podcast-creator-staging"
    echo "  S3 Structure:      s3://${S3_BUCKET}/staging/"
    echo "  RSS Feed:          s3://${S3_BUCKET}/feed-staging.xml"
    echo ""
    echo "Configuration:"
    echo "  Environment:       staging"
    echo "  Test Mode:         true (SQS messages not deleted)"
    echo "  S3 Prefix:         staging/"
    echo "  Notification:      [STAGING] prefix"
    echo ""
    echo "Test Staging:"
    echo "  aws lambda invoke \\"
    echo "    --function-name ai-newsletter-podcast-creator-staging \\"
    echo "    --region ${AWS_REGION} \\"
    echo "    response.json"
    echo ""
    echo "Update Staging:"
    echo "  ./update-lambda.sh --staging"
    echo ""
    echo "View Outputs:"
    echo "  S3: aws s3 ls s3://${S3_BUCKET}/staging/podcasts/"
    echo "  RSS: aws s3 cp s3://${S3_BUCKET}/feed-staging.xml -"
    echo "  DB: aws dynamodb scan --table-name ai_daily_news_staging --limit 5"
    echo ""
    echo "Deployment log: $DEPLOY_LOG"
    echo "=========================================================================="
}

################################################################################
# Main Execution
################################################################################

main() {
    echo ""
    echo "=========================================================================="
    echo "       AI Newsletter Staging Environment Deployment"
    echo "=========================================================================="
    echo ""

    if [ "$DRY_RUN" = true ]; then
        warn "DRY RUN MODE - No changes will be made"
    fi

    preflight_checks
    create_staging_dynamodb_table
    initialize_staging_s3_structure
    build_deployment_package
    deploy_staging_lambda
    test_staging_function
    display_summary

    success "Staging deployment completed successfully!"

    # Cleanup
    if [ -f deployment-package.zip ]; then
        rm deployment-package.zip
    fi
}

main
