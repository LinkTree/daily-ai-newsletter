#!/usr/bin/env python3
"""
Script to remove duplicate episodes and excessive newlines from RSS feeds.
Backs up original feeds before making changes.
"""

import boto3
import sys
from datetime import datetime
from xml.etree.ElementTree import fromstring, tostring, Element, register_namespace
from collections import OrderedDict

# Register iTunes namespace to preserve it in the XML
register_namespace('itunes', "http://www.itunes.com/dtds/podcast-1.0.dtd")


def cleanup_rss_feed(bucket_name: str, feed_key: str, dry_run: bool = False):
    """
    Remove duplicate episodes and clean formatting from RSS feed.

    Args:
        bucket_name: S3 bucket name
        feed_key: RSS feed file key
        dry_run: If True, only show what would be changed without updating
    """
    s3_client = boto3.client('s3')

    print(f"\n{'='*60}")
    print(f"Cleaning RSS Feed: {feed_key}")
    print(f"Bucket: {bucket_name}")
    print(f"{'='*60}\n")

    try:
        # Download existing RSS feed
        print(f"📥 Downloading RSS feed from s3://{bucket_name}/{feed_key}...")
        feed_obj = s3_client.get_object(Bucket=bucket_name, Key=feed_key)
        original_content = feed_obj['Body'].read()
        original_size = len(original_content)

        # Parse XML
        rss = fromstring(original_content)
        channel = rss.find('channel')

        if channel is None:
            print("❌ Error: No <channel> element found in RSS feed")
            return False

        # Get all items
        items = channel.findall('item')
        original_count = len(items)

        print(f"📊 Original feed stats:")
        print(f"   - Total episodes: {original_count}")
        print(f"   - File size: {original_size:,} bytes")

        # Track unique items by GUID
        seen_guids = OrderedDict()
        duplicates = []

        for item in items:
            guid_elem = item.find('guid')
            if guid_elem is not None:
                guid = guid_elem.text
                if guid in seen_guids:
                    duplicates.append(guid)
                else:
                    seen_guids[guid] = item

        # Report duplicates
        if duplicates:
            print(f"\n🔍 Found {len(duplicates)} duplicate episode(s):")
            duplicate_counts = {}
            for guid in duplicates:
                duplicate_counts[guid] = duplicate_counts.get(guid, 1) + 1
            for guid, count in duplicate_counts.items():
                print(f"   - {guid}: {count + 1} copies (will keep 1, remove {count})")
        else:
            print(f"\n✅ No duplicates found")

        # Remove all items from channel
        for item in items:
            channel.remove(item)

        # Add back only unique items (in order they were first seen)
        for guid, item in seen_guids.items():
            channel.append(item)

        unique_count = len(seen_guids)
        removed_count = original_count - unique_count

        # Convert to compact XML (minimal whitespace)
        rss_bytes = tostring(rss, encoding="utf-8", xml_declaration=True)

        # Add basic formatting (one level of indentation only)
        # This is much more compact than minidom.toprettyxml()
        cleaned_xml = rss_bytes.decode('utf-8')

        # Just ensure it's valid XML without excessive newlines
        new_size = len(cleaned_xml.encode('utf-8'))

        print(f"\n📊 Cleaned feed stats:")
        print(f"   - Total episodes: {unique_count}")
        print(f"   - Removed duplicates: {removed_count}")
        print(f"   - File size: {new_size:,} bytes ({original_size - new_size:,} bytes saved)")

        if dry_run:
            print("\n⚠️  DRY RUN MODE - No changes uploaded to S3")
            return True

        # Create backup before uploading
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_key = f"backups/{feed_key.replace('.xml', '')}_dedup_backup_{timestamp}.xml"

        print(f"\n💾 Creating backup at s3://{bucket_name}/{backup_key}...")
        s3_client.put_object(
            Bucket=bucket_name,
            Key=backup_key,
            Body=original_content,
            ContentType='application/rss+xml'
        )

        # Upload cleaned feed
        print(f"📤 Uploading cleaned RSS feed to S3...")
        s3_client.put_object(
            Bucket=bucket_name,
            Key=feed_key,
            Body=cleaned_xml.encode('utf-8'),
            ContentType='application/rss+xml'
        )

        print(f"✅ Successfully cleaned RSS feed: {feed_key}")
        print(f"   Backup saved to: {backup_key}")

        return True

    except Exception as e:
        print(f"❌ Error cleaning RSS feed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main function to clean RSS feeds."""
    import argparse

    parser = argparse.ArgumentParser(description='Remove duplicates and clean RSS feeds')
    parser.add_argument('--bucket', default='ai-news-daily-podcast',
                        help='S3 bucket name (default: ai-news-daily-podcast)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be changed without updating')
    parser.add_argument('--staging', action='store_true',
                        help='Clean staging RSS feed')
    parser.add_argument('--production', action='store_true',
                        help='Clean production RSS feed')
    parser.add_argument('--both', action='store_true',
                        help='Clean both staging and production RSS feeds')

    args = parser.parse_args()

    # Determine which feeds to clean
    clean_staging = args.staging or args.both
    clean_production = args.production or args.both

    # If neither specified, ask user
    if not clean_staging and not clean_production:
        print("Which RSS feed(s) do you want to clean?")
        print("1. Staging only")
        print("2. Production only")
        print("3. Both")
        choice = input("Enter choice (1-3): ").strip()

        if choice == '1':
            clean_staging = True
        elif choice == '2':
            clean_production = True
        elif choice == '3':
            clean_staging = True
            clean_production = True
        else:
            print("Invalid choice. Exiting.")
            sys.exit(1)

    success_count = 0
    total_count = 0

    # Clean staging
    if clean_staging:
        total_count += 1
        if cleanup_rss_feed(args.bucket, 'feed-staging.xml', args.dry_run):
            success_count += 1

    # Clean production
    if clean_production:
        total_count += 1
        if cleanup_rss_feed(args.bucket, 'feed.xml', args.dry_run):
            success_count += 1

    # Summary
    print(f"\n{'='*60}")
    print(f"Summary: {success_count}/{total_count} RSS feed(s) cleaned successfully")
    print(f"{'='*60}\n")

    if args.dry_run:
        print("ℹ️  This was a dry run. Run without --dry-run to apply changes.")

    sys.exit(0 if success_count == total_count else 1)


if __name__ == '__main__':
    main()
