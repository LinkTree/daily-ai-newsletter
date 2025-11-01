#!/bin/bash

################################################################################
# AI Newsletter Lambda Update Script
#
# Quick deployment script for updating Lambda function code only.
# Use this for most deployments after initial infrastructure setup.
#
# IMPORTANT: This project ONLY manages ai-newsletter-podcast-creator (daily processor)
#
# Usage:
#   ./update-lambda.sh
#   ./update-lambda.sh --region eu-central-1
#   ./update-lambda.sh --staging
#
# Options:
#   --region REGION        AWS region (default: eu-central-1)
#   --skip-build           Skip building package (use existing)
#   --staging              Update staging environment instead of production
#   --help                 Show this help message
#
# Examples:
#   # Update production Lambda function in eu-central-1
#   ./update-lambda.sh
#
#   # Update staging Lambda function
#   ./update-lambda.sh --staging
#
#   # Update in different region
#   ./update-lambda.sh --region us-east-1
#
#   # Skip build step (use existing package)
#   ./update-lambda.sh --skip-build
#
################################################################################

set -e  # Exit on error
set -o pipefail  # Exit on pipe failure

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
AWS_REGION="eu-central-1"
SKIP_BUILD=false
STAGING=false
LAMBDA_NAME="ai-newsletter-podcast-creator"

# Deployment tracking
UPDATE_LOG="lambda-update-$(date +%Y%m%d-%H%M%S).log"

################################################################################
# Helper Functions
################################################################################

log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $*" | tee -a "$UPDATE_LOG"
}

success() {
    echo -e "${GREEN}✓${NC} $*" | tee -a "$UPDATE_LOG"
}

error() {
    echo -e "${RED}✗ ERROR:${NC} $*" | tee -a "$UPDATE_LOG"
    exit 1
}

warn() {
    echo -e "${YELLOW}⚠ WARNING:${NC} $*" | tee -a "$UPDATE_LOG"
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
        --region)
            AWS_REGION="$2"
            shift 2
            ;;
        --skip-build)
            SKIP_BUILD=true
            shift
            ;;
        --staging)
            STAGING=true
            LAMBDA_NAME="ai-newsletter-podcast-creator-staging"
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

    # Log environment
    if [ "$STAGING" = true ]; then
        warn "Deploying to STAGING environment"
    else
        log "Deploying to PRODUCTION environment"
    fi

    # Check AWS CLI
    if ! command -v aws &> /dev/null; then
        error "AWS CLI is not installed. Please install it first."
    fi
    success "AWS CLI found: $(aws --version)"

    # Verify AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        error "AWS credentials are not configured. Run 'aws configure' first."
    fi
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    success "AWS credentials validated (Account: $ACCOUNT_ID, Region: $AWS_REGION)"

    # Check if files exist (only if building)
    if [ "$SKIP_BUILD" = false ]; then
        if [ ! -f "lambda_function.py" ]; then
            error "lambda_function.py not found in current directory"
        fi
        if [ ! -f "requirements.txt" ]; then
            error "requirements.txt not found in current directory"
        fi
        success "Required files found"

        # Check Python and pip
        if ! command -v python3 &> /dev/null; then
            error "Python 3 is not installed. Please install it first."
        fi
        if ! command -v pip3 &> /dev/null; then
            error "pip3 is not installed. Please install it first."
        fi
        success "Python and pip3 found"
    else
        if [ ! -f "deployment-package.zip" ]; then
            error "deployment-package.zip not found. Cannot skip build."
        fi
        success "Existing deployment package found"
    fi

    # Verify Lambda function exists
    if ! aws lambda get-function --function-name "$LAMBDA_NAME" --region "$AWS_REGION" &>/dev/null; then
        error "Lambda function '$LAMBDA_NAME' not found in region $AWS_REGION. Run full deployment first."
    fi
    success "Lambda function exists"
}

################################################################################
# Build Lambda Deployment Package
################################################################################

build_deployment_package() {
    if [ "$SKIP_BUILD" = true ]; then
        log "Skipping build (using existing package)"
        return
    fi

    log "Building Lambda deployment package..."

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
# Update Lambda Function
################################################################################

update_lambda_function() {
    local FUNCTION_NAME=$1
    local FUNCTION_TYPE=$2

    log "Updating Lambda function: $FUNCTION_NAME..."

    # Get current configuration
    CURRENT_HASH=$(aws lambda get-function \
        --function-name "$FUNCTION_NAME" \
        --region "$AWS_REGION" \
        --query 'Configuration.CodeSha256' \
        --output text)

    # Update function code
    NEW_HASH=$(aws lambda update-function-code \
        --function-name "$FUNCTION_NAME" \
        --zip-file fileb://deployment-package.zip \
        --region "$AWS_REGION" \
        --query 'CodeSha256' \
        --output text) || error "Failed to update Lambda function $FUNCTION_NAME"

    # Check if code actually changed
    if [ "$CURRENT_HASH" = "$NEW_HASH" ]; then
        warn "$FUNCTION_TYPE function code unchanged (same hash: $NEW_HASH)"
    else
        success "$FUNCTION_TYPE function updated successfully"
        log "  Old hash: $CURRENT_HASH"
        log "  New hash: $NEW_HASH"
    fi

    # Wait for function to be ready
    log "Waiting for function to be ready..."
    aws lambda wait function-updated \
        --function-name "$FUNCTION_NAME" \
        --region "$AWS_REGION" || warn "Wait timed out, but update may have succeeded"

    # Get function info
    LAST_MODIFIED=$(aws lambda get-function \
        --function-name "$FUNCTION_NAME" \
        --region "$AWS_REGION" \
        --query 'Configuration.LastModified' \
        --output text)

    CODE_SIZE=$(aws lambda get-function \
        --function-name "$FUNCTION_NAME" \
        --region "$AWS_REGION" \
        --query 'Configuration.CodeSize' \
        --output text)

    log "  Last modified: $LAST_MODIFIED"
    log "  Code size: $(numfmt --to=iec-i --suffix=B $CODE_SIZE 2>/dev/null || echo ${CODE_SIZE}B)"

    success "$FUNCTION_TYPE Lambda function ready"
}

################################################################################
# Display Summary
################################################################################

display_summary() {
    local ENV_NAME="PRODUCTION"
    if [ "$STAGING" = true ]; then
        ENV_NAME="STAGING"
    fi

    echo ""
    echo "=========================================================================="
    echo "                    LAMBDA UPDATE SUMMARY"
    echo "=========================================================================="
    echo ""
    echo "Environment:         $ENV_NAME"
    echo "Region:              $AWS_REGION"
    echo "Account ID:          $ACCOUNT_ID"
    echo ""
    echo "Lambda Function:     $LAMBDA_NAME (UPDATED)"
    echo ""
    echo "Next Steps:"
    if [ "$STAGING" = true ]; then
        echo "  1. Test staging function: aws lambda invoke \\"
        echo "       --function-name $LAMBDA_NAME \\"
        echo "       --region $AWS_REGION response.json"
        echo "  2. Monitor CloudWatch logs for any errors"
        echo "  3. Check staging outputs:"
        echo "       S3: aws s3 ls s3://ai-newsletter-podcasts-${ACCOUNT_ID}/staging/podcasts/"
        echo "       RSS: aws s3 cp s3://ai-newsletter-podcasts-${ACCOUNT_ID}/feed-staging.xml -"
    else
        echo "  1. Test the updated function manually if needed"
        echo "  2. Monitor CloudWatch logs for any errors"
        echo "  3. Scheduled trigger will use new code automatically"
    fi
    echo ""
    echo "Update log saved to: $UPDATE_LOG"
    echo "=========================================================================="
}

################################################################################
# Main Execution
################################################################################

main() {
    echo ""
    echo "=========================================================================="
    echo "       AI Newsletter Lambda Update - Quick Deployment"
    echo "=========================================================================="
    echo ""

    # Run pre-flight checks
    preflight_checks

    # Build deployment package
    build_deployment_package

    # Update Lambda function
    update_lambda_function "$LAMBDA_NAME" "Lambda"

    # Display summary
    display_summary

    success "Lambda update completed successfully!"

    # Cleanup deployment package if build was done
    if [ "$SKIP_BUILD" = false ]; then
        log "Cleaning up deployment package..."
        rm -f deployment-package.zip
    fi
}

# Run main function
main
