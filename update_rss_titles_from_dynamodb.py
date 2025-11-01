#!/usr/bin/env python3
"""
Script to update RSS episode titles from DynamoDB.
Matches episodes by date extracted from GUID and updates titles.
Backs up original feeds before making changes.
"""

import boto3
import sys
from datetime import datetime
from xml.etree.ElementTree import fromstring, tostring, register_namespace

# Register iTunes namespace to preserve it in the XML
register_namespace('itunes', "http://www.itunes.com/dtds/podcast-1.0.dtd")


def extract_date_from_guid(guid: str) -> str:
    """
    Extract date from GUID.
    GUID format: 'daily-ai-YYYYMMDD' or 'staging-daily-ai-YYYYMMDD'
    Returns: 'YYYY-MM-DD' or None if not found
    """
    if not guid:
        return None

    # Remove prefix to get the date part
    date_part = guid.replace('staging-daily-ai-', '').replace('daily-ai-', '')

    # Should be YYYYMMDD (8 digits)
    if len(date_part) == 8 and date_part.isdigit():
        return f"{date_part[0:4]}-{date_part[4:6]}-{date_part[6:8]}"

    return None


def get_titles_from_dynamodb(table_name: str) -> dict:
    """
    Fetch all episode titles from DynamoDB table.

    Args:
        table_name: DynamoDB table name

    Returns:
        Dictionary mapping date (YYYY-MM-DD) to episode_title
    """
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(table_name)

    print(f"📥 Scanning DynamoDB table: {table_name}...")

    titles = {}
    scan_kwargs = {
        'ProjectionExpression': '#d, episode_title',
        'ExpressionAttributeNames': {'#d': 'date'}
    }

    try:
        done = False
        start_key = None
        count = 0

        while not done:
            if start_key:
                scan_kwargs['ExclusiveStartKey'] = start_key

            response = table.scan(**scan_kwargs)
            items = response.get('Items', [])

            for item in items:
                date = item.get('date')
                episode_title = item.get('episode_title')

                if date and episode_title:
                    titles[date] = episode_title
                    count += 1

            start_key = response.get('LastEvaluatedKey', None)
            done = start_key is None

        print(f"   Found {count} records with episode_title")
        return titles

    except Exception as e:
        print(f"❌ Error scanning DynamoDB table: {str(e)}")
        return {}


def update_rss_titles(bucket_name: str, feed_key: str, titles_map: dict, dry_run: bool = False):
    """
    Update episode titles in RSS feed from DynamoDB titles.

    Args:
        bucket_name: S3 bucket name
        feed_key: RSS feed file key
        titles_map: Dictionary mapping date to episode_title
        dry_run: If True, only show what would be changed without updating
    """
    s3_client = boto3.client('s3')

    print(f"\n{'='*60}")
    print(f"Updating Episode Titles: {feed_key}")
    print(f"Bucket: {bucket_name}")
    print(f"{'='*60}\n")

    try:
        # Download existing RSS feed
        print(f"📥 Downloading RSS feed from s3://{bucket_name}/{feed_key}...")
        feed_obj = s3_client.get_object(Bucket=bucket_name, Key=feed_key)
        original_content = feed_obj['Body'].read()

        # Parse XML
        rss = fromstring(original_content)
        channel = rss.find('channel')

        if channel is None:
            print("❌ Error: No <channel> element found in RSS feed")
            return False

        # Get all items
        items = channel.findall('item')
        total_items = len(items)

        print(f"📊 Processing {total_items} episode(s)...")

        updated_count = 0
        not_found_count = 0
        updates = []

        for item in items:
            guid_elem = item.find('guid')
            title_elem = item.find('title')

            if guid_elem is None or title_elem is None:
                continue

            guid = guid_elem.text
            old_title = title_elem.text
            date = extract_date_from_guid(guid)

            if not date:
                print(f"   ⚠️  Could not extract date from GUID: {guid}")
                not_found_count += 1
                continue

            new_title = titles_map.get(date)

            if new_title:
                if old_title != new_title:
                    title_elem.text = new_title
                    updated_count += 1
                    updates.append({
                        'date': date,
                        'guid': guid,
                        'old_title': old_title,
                        'new_title': new_title
                    })
            else:
                not_found_count += 1

        # Show sample updates
        if updates:
            print(f"\n🔄 Title updates (showing first 5):")
            for update in updates[:5]:
                print(f"\n   📅 {update['date']} ({update['guid']}):")
                print(f"      Old: {update['old_title']}")
                print(f"      New: {update['new_title']}")

            if len(updates) > 5:
                print(f"\n   ... and {len(updates) - 5} more")

        print(f"\n📊 Update summary:")
        print(f"   - Total episodes: {total_items}")
        print(f"   - Titles updated: {updated_count}")
        print(f"   - No title found in DB: {not_found_count}")

        if updated_count == 0:
            print("\n✅ No title updates needed")
            return True

        if dry_run:
            print("\n⚠️  DRY RUN MODE - No changes uploaded to S3")
            return True

        # Create backup before uploading
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_key = f"backups/{feed_key.replace('.xml', '')}_titles_backup_{timestamp}.xml"

        print(f"\n💾 Creating backup at s3://{bucket_name}/{backup_key}...")
        s3_client.put_object(
            Bucket=bucket_name,
            Key=backup_key,
            Body=original_content,
            ContentType='application/rss+xml'
        )

        # Convert to XML
        rss_bytes = tostring(rss, encoding="utf-8", xml_declaration=True)

        # Upload updated feed
        print(f"📤 Uploading updated RSS feed to S3...")
        s3_client.put_object(
            Bucket=bucket_name,
            Key=feed_key,
            Body=rss_bytes,
            ContentType='application/rss+xml'
        )

        print(f"✅ Successfully updated episode titles in: {feed_key}")
        print(f"   Backup saved to: {backup_key}")

        return True

    except Exception as e:
        print(f"❌ Error updating RSS feed titles: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main function to update RSS titles from DynamoDB."""
    import argparse

    parser = argparse.ArgumentParser(description='Update RSS episode titles from DynamoDB')
    parser.add_argument('--bucket', default='ai-news-daily-podcast',
                        help='S3 bucket name (default: ai-news-daily-podcast)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be changed without updating')
    parser.add_argument('--staging', action='store_true',
                        help='Update staging RSS feed')
    parser.add_argument('--production', action='store_true',
                        help='Update production RSS feed')
    parser.add_argument('--both', action='store_true',
                        help='Update both staging and production RSS feeds')

    args = parser.parse_args()

    # Determine which feeds to update
    update_staging = args.staging or args.both
    update_production = args.production or args.both

    # If neither specified, ask user
    if not update_staging and not update_production:
        print("Which RSS feed(s) do you want to update?")
        print("1. Staging only")
        print("2. Production only")
        print("3. Both")
        choice = input("Enter choice (1-3): ").strip()

        if choice == '1':
            update_staging = True
        elif choice == '2':
            update_production = True
        elif choice == '3':
            update_staging = True
            update_production = True
        else:
            print("Invalid choice. Exiting.")
            sys.exit(1)

    success_count = 0
    total_count = 0

    # Update staging
    if update_staging:
        total_count += 1
        titles = get_titles_from_dynamodb('ai_daily_news_staging')
        if titles and update_rss_titles(args.bucket, 'feed-staging.xml', titles, args.dry_run):
            success_count += 1

    # Update production
    if update_production:
        total_count += 1
        titles = get_titles_from_dynamodb('ai_daily_news')
        if titles and update_rss_titles(args.bucket, 'feed.xml', titles, args.dry_run):
            success_count += 1

    # Summary
    print(f"\n{'='*60}")
    print(f"Summary: {success_count}/{total_count} RSS feed(s) updated successfully")
    print(f"{'='*60}\n")

    if args.dry_run:
        print("ℹ️  This was a dry run. Run without --dry-run to apply changes.")

    sys.exit(0 if success_count == total_count else 1)


if __name__ == '__main__':
    main()
