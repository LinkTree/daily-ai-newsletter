#!/usr/bin/env python3
"""
Script to properly format RSS feeds with clean, readable XML.
Removes excessive blank lines and applies proper indentation.
"""

import boto3
import sys
from datetime import datetime
from xml.etree.ElementTree import fromstring, tostring, register_namespace
from xml.dom import minidom

# Register iTunes namespace to preserve it in the XML
register_namespace('itunes', "http://www.itunes.com/dtds/podcast-1.0.dtd")


def format_rss_feed(bucket_name: str, feed_key: str, dry_run: bool = False):
    """
    Format RSS feed with proper indentation and no excessive blank lines.

    Args:
        bucket_name: S3 bucket name
        feed_key: RSS feed file key
        dry_run: If True, only show what would be changed without updating
    """
    s3_client = boto3.client('s3')

    print(f"\n{'='*60}")
    print(f"Formatting RSS Feed: {feed_key}")
    print(f"Bucket: {bucket_name}")
    print(f"{'='*60}\n")

    try:
        # Download existing RSS feed
        print(f"📥 Downloading RSS feed from s3://{bucket_name}/{feed_key}...")
        feed_obj = s3_client.get_object(Bucket=bucket_name, Key=feed_key)
        original_content = feed_obj['Body'].read()
        original_size = len(original_content)

        # Count blank lines in original
        original_blank_lines = original_content.decode('utf-8').count('\n\n')

        print(f"📊 Original feed stats:")
        print(f"   - File size: {original_size:,} bytes")
        print(f"   - Approximate blank line sequences: {original_blank_lines}")

        # Parse XML
        rss = fromstring(original_content)

        # Convert to string first
        rss_bytes = tostring(rss, encoding="utf-8", xml_declaration=False)

        # Use minidom for pretty printing
        dom = minidom.parseString(rss_bytes)
        pretty_xml = dom.toprettyxml(indent="  ", encoding="utf-8")

        # Clean up excessive blank lines from minidom output
        # minidom sometimes adds extra blank lines, so we'll normalize them
        xml_str = pretty_xml.decode('utf-8')

        # Remove blank lines between XML tags
        lines = xml_str.split('\n')
        cleaned_lines = []
        prev_blank = False

        for line in lines:
            # Check if line is blank or just whitespace
            is_blank = len(line.strip()) == 0

            # Skip consecutive blank lines
            if is_blank and prev_blank:
                continue

            cleaned_lines.append(line)
            prev_blank = is_blank

        # Join back and ensure proper XML declaration
        formatted_xml = '\n'.join(cleaned_lines)

        # Ensure it starts with XML declaration
        if not formatted_xml.startswith('<?xml'):
            formatted_xml = '<?xml version="1.0" encoding="utf-8"?>\n' + formatted_xml

        # Remove any leading blank lines after declaration
        lines = formatted_xml.split('\n')
        result_lines = [lines[0]]  # Keep XML declaration

        # Skip blank lines after declaration
        i = 1
        while i < len(lines) and len(lines[i].strip()) == 0:
            i += 1

        # Add remaining lines
        result_lines.extend(lines[i:])

        formatted_xml = '\n'.join(result_lines)

        # Final cleanup: ensure no more than one consecutive blank line
        while '\n\n\n' in formatted_xml:
            formatted_xml = formatted_xml.replace('\n\n\n', '\n\n')

        formatted_bytes = formatted_xml.encode('utf-8')
        new_size = len(formatted_bytes)
        new_blank_lines = formatted_xml.count('\n\n')

        print(f"\n📊 Formatted feed stats:")
        print(f"   - File size: {new_size:,} bytes")
        print(f"   - Approximate blank line sequences: {new_blank_lines}")
        print(f"   - Size change: {new_size - original_size:+,} bytes")

        if dry_run:
            print("\n⚠️  DRY RUN MODE - No changes uploaded to S3")
            # Show sample of formatted XML
            print("\n📄 Sample of formatted XML (first 30 lines):")
            print("---")
            sample_lines = formatted_xml.split('\n')[:30]
            for line in sample_lines:
                print(line)
            print("---")
            return True

        # Create backup before uploading
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_key = f"backups/{feed_key.replace('.xml', '')}_format_backup_{timestamp}.xml"

        print(f"\n💾 Creating backup at s3://{bucket_name}/{backup_key}...")
        s3_client.put_object(
            Bucket=bucket_name,
            Key=backup_key,
            Body=original_content,
            ContentType='application/rss+xml'
        )

        # Upload formatted feed
        print(f"📤 Uploading formatted RSS feed to S3...")
        s3_client.put_object(
            Bucket=bucket_name,
            Key=feed_key,
            Body=formatted_bytes,
            ContentType='application/rss+xml'
        )

        print(f"✅ Successfully formatted RSS feed: {feed_key}")
        print(f"   Backup saved to: {backup_key}")

        return True

    except Exception as e:
        print(f"❌ Error formatting RSS feed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main function to format RSS feeds."""
    import argparse

    parser = argparse.ArgumentParser(description='Format RSS feeds with proper indentation')
    parser.add_argument('--bucket', default='ai-news-daily-podcast',
                        help='S3 bucket name (default: ai-news-daily-podcast)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be changed without updating')
    parser.add_argument('--staging', action='store_true',
                        help='Format staging RSS feed')
    parser.add_argument('--production', action='store_true',
                        help='Format production RSS feed')
    parser.add_argument('--both', action='store_true',
                        help='Format both staging and production RSS feeds')

    args = parser.parse_args()

    # Determine which feeds to format
    format_staging = args.staging or args.both
    format_production = args.production or args.both

    # If neither specified, ask user
    if not format_staging and not format_production:
        print("Which RSS feed(s) do you want to format?")
        print("1. Staging only")
        print("2. Production only")
        print("3. Both")
        choice = input("Enter choice (1-3): ").strip()

        if choice == '1':
            format_staging = True
        elif choice == '2':
            format_production = True
        elif choice == '3':
            format_staging = True
            format_production = True
        else:
            print("Invalid choice. Exiting.")
            sys.exit(1)

    success_count = 0
    total_count = 0

    # Format staging
    if format_staging:
        total_count += 1
        if format_rss_feed(args.bucket, 'feed-staging.xml', args.dry_run):
            success_count += 1

    # Format production
    if format_production:
        total_count += 1
        if format_rss_feed(args.bucket, 'feed.xml', args.dry_run):
            success_count += 1

    # Summary
    print(f"\n{'='*60}")
    print(f"Summary: {success_count}/{total_count} RSS feed(s) formatted successfully")
    print(f"{'='*60}\n")

    if args.dry_run:
        print("ℹ️  This was a dry run. Run without --dry-run to apply changes.")

    sys.exit(0 if success_count == total_count else 1)


if __name__ == '__main__':
    main()
