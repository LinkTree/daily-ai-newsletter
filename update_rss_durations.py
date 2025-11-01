#!/usr/bin/env python3
"""
Script to update iTunes duration in RSS feeds based on actual MP3 file durations.
Downloads MP3 files temporarily, extracts duration using mutagen, and updates RSS.
"""

import boto3
import sys
import os
import tempfile
from datetime import datetime
from xml.etree.ElementTree import fromstring, tostring, register_namespace, SubElement
from urllib.parse import urlparse

# Try to import mutagen
try:
    from mutagen.mp3 import MP3
except ImportError:
    print("❌ Error: mutagen library is required")
    print("Install it with: pip install mutagen")
    sys.exit(1)

# Register iTunes namespace to preserve it in the XML
register_namespace('itunes', "http://www.itunes.com/dtds/podcast-1.0.dtd")


def format_duration(seconds: float) -> str:
    """
    Format duration in seconds to HH:MM:SS or MM:SS format.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted duration string
    """
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def get_mp3_duration(s3_client, url: str) -> float:
    """
    Download MP3 from S3 and get its duration.

    Args:
        s3_client: Boto3 S3 client
        url: URL to the MP3 file

    Returns:
        Duration in seconds, or None if failed
    """
    try:
        # Parse S3 URL to get bucket and key
        parsed = urlparse(url)

        # Handle different S3 URL formats
        if '.s3.amazonaws.com' in parsed.netloc or '.s3.' in parsed.netloc:
            # Format: https://bucket.s3.region.amazonaws.com/key
            # or: https://bucket.s3.amazonaws.com/key
            bucket = parsed.netloc.split('.')[0]
            key = parsed.path.lstrip('/')
        else:
            print(f"   ⚠️  Unsupported URL format: {url}")
            return None

        # Create temporary file
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp_file:
            tmp_path = tmp_file.name

        try:
            # Download from S3
            s3_client.download_file(bucket, key, tmp_path)

            # Get duration using mutagen
            audio = MP3(tmp_path)
            duration = audio.info.length

            return duration

        finally:
            # Clean up temporary file
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    except Exception as e:
        print(f"   ⚠️  Error getting duration: {str(e)}")
        return None


def update_rss_durations(bucket_name: str, feed_key: str, dry_run: bool = False):
    """
    Update episode durations in RSS feed from actual MP3 files.

    Args:
        bucket_name: S3 bucket name
        feed_key: RSS feed file key
        dry_run: If True, only show what would be changed without updating
    """
    s3_client = boto3.client('s3')

    print(f"\n{'='*60}")
    print(f"Updating Episode Durations: {feed_key}")
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
        failed_count = 0
        updates = []

        for idx, item in enumerate(items, 1):
            enclosure = item.find('enclosure')
            duration_elem = item.find('{http://www.itunes.com/dtds/podcast-1.0.dtd}duration')
            title_elem = item.find('title')

            if enclosure is None:
                print(f"   ⚠️  Episode {idx}: No enclosure found")
                failed_count += 1
                continue

            url = enclosure.get('url')
            if not url:
                print(f"   ⚠️  Episode {idx}: No URL in enclosure")
                failed_count += 1
                continue

            title = title_elem.text if title_elem is not None else f"Episode {idx}"
            old_duration = duration_elem.text if duration_elem is not None else "unknown"

            print(f"   📥 {idx}/{total_items}: Downloading {title[:50]}...")

            # Get actual duration from MP3
            duration_seconds = get_mp3_duration(s3_client, url)

            if duration_seconds is None:
                failed_count += 1
                continue

            new_duration = format_duration(duration_seconds)

            # Update or create duration element
            if duration_elem is None:
                # Create new duration element
                duration_elem = SubElement(item, '{http://www.itunes.com/dtds/podcast-1.0.dtd}duration')

            if old_duration != new_duration:
                duration_elem.text = new_duration
                updated_count += 1
                updates.append({
                    'title': title,
                    'old_duration': old_duration,
                    'new_duration': new_duration
                })
                print(f"      ✓ Duration: {old_duration} → {new_duration}")
            else:
                print(f"      ✓ Duration already correct: {new_duration}")

        # Show summary of updates
        if updates:
            print(f"\n🔄 Duration updates (showing first 5):")
            for update in updates[:5]:
                print(f"\n   {update['title'][:60]}")
                print(f"      Old: {update['old_duration']}")
                print(f"      New: {update['new_duration']}")

            if len(updates) > 5:
                print(f"\n   ... and {len(updates) - 5} more")

        print(f"\n📊 Update summary:")
        print(f"   - Total episodes: {total_items}")
        print(f"   - Durations updated: {updated_count}")
        print(f"   - Failed to process: {failed_count}")

        if updated_count == 0:
            print("\n✅ No duration updates needed")
            return True

        if dry_run:
            print("\n⚠️  DRY RUN MODE - No changes uploaded to S3")
            return True

        # Create backup before uploading
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_key = f"backups/{feed_key.replace('.xml', '')}_duration_backup_{timestamp}.xml"

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

        print(f"✅ Successfully updated episode durations in: {feed_key}")
        print(f"   Backup saved to: {backup_key}")

        return True

    except Exception as e:
        print(f"❌ Error updating RSS feed durations: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main function to update RSS durations from MP3 files."""
    import argparse

    parser = argparse.ArgumentParser(description='Update RSS episode durations from actual MP3 files')
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
        if update_rss_durations(args.bucket, 'feed-staging.xml', args.dry_run):
            success_count += 1

    # Update production
    if update_production:
        total_count += 1
        if update_rss_durations(args.bucket, 'feed.xml', args.dry_run):
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
