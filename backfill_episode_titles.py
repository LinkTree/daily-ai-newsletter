#!/usr/bin/env python3
"""
Backfill episode titles for existing DynamoDB records.

This script scans the DynamoDB table for records missing episode_title field,
generates titles using Claude Haiku 4.5, and updates the records.

Usage:
    # Dry run (preview changes)
    python backfill_episode_titles.py --dry-run

    # Limit to 5 records
    python backfill_episode_titles.py --dry-run --limit 5

    # Run on staging table
    python backfill_episode_titles.py --table-name ai_daily_news_staging

    # Run on production table
    python backfill_episode_titles.py --table-name ai_daily_news
"""

import argparse
import boto3
import os
import time
import requests
from datetime import datetime
from typing import Dict, Any, Optional

# Configure AWS
dynamodb = boto3.resource('dynamodb')

# Claude API configuration
CLAUDE_API_KEY = os.environ.get('CLAUDE_API_KEY')
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-haiku-4-5"


def generate_episode_title(podcast_script: str, podcast_title_short: str = "Daily AI") -> str:
    """Generate episode title using Claude Haiku 4.5"""
    try:
        print("  → Calling Claude Haiku 4.5 to generate title...")

        prompt = f"""You are an expert podcast editor creating compelling episode titles for "Daily AI, by AI" - a daily podcast covering the latest developments in artificial intelligence.

Your task: Create ONE compelling episode title based on the podcast script below.

Requirements:
- 6-12 words maximum
- Newsworthy and informative (clearly states what happened)
- Catchy and engaging (makes people want to listen)
- Focus on the most important or surprising development
- Professional but accessible tone
- Do NOT use clickbait or sensationalism
- Do NOT use questions or "How to..." format

Good examples:
- "OpenAI Launches GPT-5 with Revolutionary Reasoning"
- "EU Parliament Passes Landmark AI Regulations"
- "Google's Gemini 2.0 Defeats GPT-4 in Coding Tests"
- "Microsoft Acquires Major AI Startup for $10 Billion"
- "AI Models Show Unexpected Self-Improvement Capabilities"

Bad examples:
- "You Won't Believe What AI Did Today!" (clickbait)
- "How AI is Changing Everything" (too vague)
- "Today's AI News" (not specific)
- "The Future of Artificial Intelligence is Here" (generic)

Return ONLY the title - no quotes, no explanation, no punctuation at the end.

---

PODCAST SCRIPT:
{podcast_script}

---

EPISODE TITLE:"""

        headers = {
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }

        payload = {
            "model": CLAUDE_MODEL,
            "max_tokens": 50,
            "temperature": 0.7,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }

        response = requests.post(CLAUDE_API_URL, json=payload, headers=headers, timeout=60)
        response.raise_for_status()

        result = response.json()
        title = result['content'][0]['text'].strip().strip('"').strip("'").strip()

        print(f"  → Generated title: {title}")
        return title

    except Exception as e:
        print(f"  ⚠️  Error generating title: {str(e)}")
        # Fallback to date-based title
        current_date = datetime.now().strftime('%B %d, %Y')
        fallback_title = f"{podcast_title_short} Summary - {current_date}"
        print(f"  → Using fallback title: {fallback_title}")
        return fallback_title


def scan_table(table_name: str, limit: Optional[int] = None) -> list:
    """Scan DynamoDB table for records missing episode_title"""
    print(f"\n📊 Scanning table: {table_name}")

    table = dynamodb.Table(table_name)

    scan_kwargs = {}
    if limit:
        scan_kwargs['Limit'] = limit

    items = []
    response = table.scan(**scan_kwargs)
    items.extend(response.get('Items', []))

    # Handle pagination if no limit
    while 'LastEvaluatedKey' in response and not limit:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        items.extend(response.get('Items', []))

    # Filter for records missing episode_title
    missing_titles = [item for item in items if 'episode_title' not in item]

    print(f"  → Total records: {len(items)}")
    print(f"  → Missing episode_title: {len(missing_titles)}")

    return missing_titles


def update_record(table_name: str, date: str, episode_title: str, dry_run: bool = False) -> bool:
    """Update DynamoDB record with episode title"""
    if dry_run:
        print(f"  → [DRY RUN] Would update record: date={date}, title={episode_title}")
        return True

    try:
        table = dynamodb.Table(table_name)
        table.update_item(
            Key={'date': date},
            UpdateExpression='SET episode_title = :title, generated_at = :timestamp',
            ExpressionAttributeValues={
                ':title': episode_title,
                ':timestamp': datetime.now().isoformat()
            }
        )
        print(f"  ✅ Updated record: date={date}")
        return True
    except Exception as e:
        print(f"  ❌ Error updating record: {str(e)}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Backfill episode titles in DynamoDB')
    parser.add_argument('--table-name', default='ai_daily_news_staging', help='DynamoDB table name')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without updating')
    parser.add_argument('--limit', type=int, help='Limit number of records to process')
    parser.add_argument('--podcast-title-short', default='Daily AI', help='Short podcast title for fallback')

    args = parser.parse_args()

    # Validate API key
    if not CLAUDE_API_KEY:
        print("❌ Error: CLAUDE_API_KEY environment variable not set")
        return 1

    print("=" * 60)
    print("📝 Episode Title Backfill Script")
    print("=" * 60)
    print(f"Table: {args.table_name}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE UPDATE'}")
    print(f"Limit: {args.limit if args.limit else 'None (process all)'}")
    print("=" * 60)

    # Scan table
    records = scan_table(args.table_name, args.limit)

    if not records:
        print("\n✅ No records need backfilling!")
        return 0

    # Confirm before proceeding
    if not args.dry_run:
        response = input(f"\n⚠️  About to update {len(records)} records. Continue? (yes/no): ")
        if response.lower() != 'yes':
            print("❌ Cancelled by user")
            return 0

    # Process records
    print(f"\n🚀 Processing {len(records)} records...\n")

    success_count = 0
    failure_count = 0

    for i, record in enumerate(records, 1):
        date = record['date']
        text = record.get('text', '')

        print(f"[{i}/{len(records)}] Processing date: {date}")

        if not text:
            print("  ⚠️  No text field found, skipping")
            failure_count += 1
            continue

        # Generate title
        episode_title = generate_episode_title(text, args.podcast_title_short)

        # Update record
        if update_record(args.table_name, date, episode_title, args.dry_run):
            success_count += 1
        else:
            failure_count += 1

        # Rate limiting: wait 2 seconds between requests
        if i < len(records):
            print("  ⏱️  Waiting 2 seconds (rate limiting)...")
            time.sleep(2)

        print()

    # Summary
    print("=" * 60)
    print("📊 SUMMARY")
    print("=" * 60)
    print(f"✅ Successful: {success_count}")
    print(f"❌ Failed: {failure_count}")
    print(f"📝 Total: {len(records)}")

    if args.dry_run:
        print("\n💡 This was a DRY RUN. No records were actually updated.")
        print("   Remove --dry-run flag to apply changes.")

    print("=" * 60)

    return 0 if failure_count == 0 else 1


if __name__ == '__main__':
    exit(main())
