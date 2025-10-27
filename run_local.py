#!/usr/bin/env python3
"""
Local Development Runner for AI Newsletter Processor

This script provides an easy way to run the newsletter processor locally
with sample data, generating all outputs (MP3, XML, reports) locally
while still using remote AWS services (Claude API, Polly).

Usage:
    python run_local.py
    
    # Or with custom directories:
    python run_local.py --samples custom_emails --output custom_output
"""

import os
import sys
import argparse
import logging
from pathlib import Path

# Setup logging with nice formatting
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

def check_environment():
    """Check if required environment variables are set"""
    required_vars = [
        'CLAUDE_API_KEY',
        'AWS_ACCESS_KEY_ID',
        'AWS_SECRET_ACCESS_KEY',
        'AWS_DEFAULT_REGION'
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.environ.get(var):
            missing_vars.append(var)
    
    if missing_vars:
        logger.error("❌ Missing required environment variables:")
        for var in missing_vars:
            logger.error(f"   - {var}")
        logger.error("\nPlease set these variables before running:")
        logger.error("export CLAUDE_API_KEY='your-claude-api-key'")
        logger.error("export AWS_ACCESS_KEY_ID='your-aws-access-key'")
        logger.error("export AWS_SECRET_ACCESS_KEY='your-aws-secret-key'")
        logger.error("export AWS_DEFAULT_REGION='us-east-1'")
        return False
    
    return True

def setup_environment():
    """Setup environment variables for local development"""
    # Set default values for variables that aren't needed locally
    env_defaults = {
        'EMAIL_QUEUE_URL': 'local://mock-queue',
        'SNS_TOPIC_ARN': 'local://mock-topic',
        'DYNAMODB_TABLE_NAME': 'local_ai_daily_news',
        'PODCAST_S3_BUCKET': 'local-ai-newsletter-podcasts',
        'POLLY_VOICE': 'Joanna',
        'POLLY_RATE': 'medium',
        'GENERATE_AUDIO': 'true',
        'MAX_MESSAGES': '10',
        'MAX_LINKS_PER_EMAIL': '3',
        'TEST_MODE': 'true'
    }
    
    for key, value in env_defaults.items():
        if not os.environ.get(key):
            os.environ[key] = value
            logger.info(f"🔧 Set {key}={value}")

def main():
    parser = argparse.ArgumentParser(description='Run AI Newsletter Processor locally')
    parser.add_argument('--samples', default='sample_emails', 
                       help='Directory containing sample email JSON files (default: sample_emails)')
    parser.add_argument('--output', default='output',
                       help='Output directory for generated files (default: output)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    logger.info("🤖 AI Newsletter Processor - Local Development Mode")
    logger.info("="*60)
    
    # Check environment
    if not check_environment():
        sys.exit(1)
    
    # Setup environment for local development
    setup_environment()
    
    # Check if sample emails exist
    samples_dir = Path(args.samples)
    if not samples_dir.exists():
        logger.error(f"❌ Sample emails directory not found: {samples_dir}")
        logger.error("Please create sample email JSON files in the specified directory")
        sys.exit(1)
    
    json_files = list(samples_dir.glob("*.json"))
    if not json_files:
        logger.error(f"❌ No JSON files found in: {samples_dir}")
        logger.error("Please add sample email JSON files to process")
        sys.exit(1)
    
    logger.info(f"📧 Found {len(json_files)} sample email files:")
    for json_file in json_files:
        logger.info(f"   - {json_file.name}")
    
    try:
        # Import and run local processor
        from local_processor import LocalNewsletterProcessor
        
        # Initialize processor
        processor = LocalNewsletterProcessor(
            sample_emails_dir=args.samples,
            output_dir=args.output
        )
        
        logger.info("🚀 Starting newsletter processing...")
        
        # Process newsletters
        result = processor.process_newsletter_queue()
        
        # Save summary
        processor.send_summary_email(result)
        
        # Display results
        print("\n" + "="*60)
        print("📋 PROCESSING COMPLETE")
        print("="*60)
        print(f"✅ Status: {result['status']}")
        print(f"📧 Emails Processed: {result.get('total_emails', 0)}")
        print(f"🔧 Strategy Used: {result.get('processing_strategy', 'Unknown')}")
        
        if result.get('audio_generated'):
            print(f"🎵 Audio Generated: {result['local_audio_path']}")
            print(f"📏 Audio Size: {result.get('audio_size', 0):,} bytes")
        else:
            print("🎵 Audio: Not generated")
        
        output_dir = Path(args.output)
        print(f"\n📁 Output Files:")
        print(f"   📊 Reports: {output_dir / 'reports'}")
        print(f"   🎵 Audio: {output_dir / 'audio'}")
        print(f"   📡 RSS: {output_dir / 'rss'}")
        
        # List generated files
        if output_dir.exists():
            all_files = []
            for subdir in ['reports', 'audio', 'rss']:
                subdir_path = output_dir / subdir
                if subdir_path.exists():
                    all_files.extend(list(subdir_path.glob("*")))
            
            if all_files:
                print(f"\n📄 Generated {len(all_files)} files:")
                for file_path in sorted(all_files):
                    size = file_path.stat().st_size
                    if size > 1024*1024:
                        size_str = f"{size/(1024*1024):.1f}MB"
                    elif size > 1024:
                        size_str = f"{size/1024:.1f}KB"
                    else:
                        size_str = f"{size}B"
                    print(f"   - {file_path.name} ({size_str})")
        
        print(f"\n⏰ Completed at: {result.get('processed_at')}")
        print("\n🎉 Local processing successful!")
        
        return 0
        
    except KeyboardInterrupt:
        logger.info("\n⚠️ Processing interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"❌ Processing failed: {str(e)}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())