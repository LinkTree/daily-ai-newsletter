#!/usr/bin/env python3
"""
One-time script to update the website link in existing RSS feeds on S3.
Updates both production and staging RSS feeds.
"""

import boto3
import os
import sys
from xml.etree.ElementTree import fromstring, tostring, register_namespace
from xml.dom import minidom

# Register iTunes namespace to preserve it in the XML
register_namespace('itunes', "http://www.itunes.com/dtds/podcast-1.0.dtd")

def update_rss_link(bucket_name: str, feed_key: str, new_website_url: str, dry_run: bool = False):
    """
    Update the website link in an RSS feed stored in S3.

    Args:
        bucket_name: S3 bucket name
        feed_key: RSS feed file key (e.g., 'feed.xml')
        new_website_url: New website URL to set
        dry_run: If True, only show what would be changed without updating
    """
    s3_client = boto3.client('s3')

    print(f"\n{'='*60}")
    print(f"Bucket: {bucket_name}")
    print(f"Feed: {feed_key}")
    print(f"New URL: {new_website_url}")
    print(f"{'='*60}\n")

    try:
        # Download existing RSS feed
        print(f"Downloading RSS feed from s3://{bucket_name}/{feed_key}...")
        feed_obj = s3_client.get_object(Bucket=bucket_name, Key=feed_key)
        rss_content = feed_obj['Body'].read()

        # Parse XML
        rss = fromstring(rss_content)
        channel = rss.find('channel')

        if channel is None:
            print("❌ Error: No <channel> element found in RSS feed")
            return False

        # Find and update the link element
        link_elem = channel.find('link')

        if link_elem is None:
            print("❌ Error: No <link> element found in channel")
            return False

        old_url = link_elem.text
        print(f"Current link: {old_url}")

        if old_url == new_website_url:
            print(f"✅ Link is already set to {new_website_url}, no update needed")
            return True

        # Update the link
        link_elem.text = new_website_url
        print(f"Updated link: {new_website_url}")

        if dry_run:
            print("\n⚠️  DRY RUN MODE - No changes uploaded to S3")
            return True

        # Convert to pretty XML
        rss_bytes = tostring(rss, encoding="utf-8", xml_declaration=True)
        pretty_xml = minidom.parseString(rss_bytes).toprettyxml(encoding="utf-8")

        # Upload updated feed back to S3
        print(f"\nUploading updated RSS feed to S3...")
        s3_client.put_object(
            Bucket=bucket_name,
            Key=feed_key,
            Body=pretty_xml,
            ContentType='application/rss+xml'
        )

        print(f"✅ Successfully updated RSS feed link in s3://{bucket_name}/{feed_key}")
        return True

    except s3_client.exceptions.NoSuchKey:
        print(f"❌ Error: RSS feed not found at s3://{bucket_name}/{feed_key}")
        return False
    except Exception as e:
        print(f"❌ Error updating RSS feed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main function to update RSS feeds."""
    import argparse

    parser = argparse.ArgumentParser(description='Update website link in RSS feeds on S3')
    parser.add_argument('--new-url', default='https://dailyaibyai.news',
                        help='New website URL (default: https://dailyaibyai.news)')
    parser.add_argument('--bucket', help='S3 bucket name (overrides environment variable)')
    parser.add_argument('--feed-key', default='feed.xml',
                        help='RSS feed file key (default: feed.xml)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be changed without updating')
    parser.add_argument('--staging', action='store_true',
                        help='Update staging environment RSS feed')
    parser.add_argument('--production', action='store_true',
                        help='Update production environment RSS feed')

    args = parser.parse_args()

    # Determine which environments to update
    update_staging = args.staging
    update_production = args.production

    # If neither specified, ask user
    if not update_staging and not update_production:
        print("Which environment(s) do you want to update?")
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
        staging_bucket = args.bucket or os.environ.get('PODCAST_S3_BUCKET_STAGING', 'ai-newsletter-podcasts-staging')
        if update_rss_link(staging_bucket, args.feed_key, args.new_url, args.dry_run):
            success_count += 1

    # Update production
    if update_production:
        total_count += 1
        prod_bucket = args.bucket or os.environ.get('PODCAST_S3_BUCKET', 'ai-newsletter-podcasts')
        if update_rss_link(prod_bucket, args.feed_key, args.new_url, args.dry_run):
            success_count += 1

    # Summary
    print(f"\n{'='*60}")
    print(f"Summary: {success_count}/{total_count} RSS feeds updated successfully")
    print(f"{'='*60}\n")

    if args.dry_run:
        print("ℹ️  This was a dry run. Run without --dry-run to apply changes.")

    sys.exit(0 if success_count == total_count else 1)


if __name__ == '__main__':
    main()
