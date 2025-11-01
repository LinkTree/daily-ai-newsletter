import json
import os
import glob
from datetime import datetime
from typing import List, Dict, Any, Optional
import logging
from pathlib import Path

# Import the original processor but we'll modify its behavior
from lambda_function import ClaudeNewsletterProcessor

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

class LocalNewsletterProcessor(ClaudeNewsletterProcessor):
    """
    Local version of the newsletter processor that:
    - Reads from sample email files instead of SQS
    - Saves MP3 and XML files locally instead of S3
    - Still uses remote Claude API and Polly services
    """
    
    def __init__(self, sample_emails_dir: str = "sample_emails", output_dir: str = "output"):
        # Initialize parent class in test mode
        super().__init__(test_mode=True)
        
        self.sample_emails_dir = Path(sample_emails_dir)
        self.output_dir = Path(output_dir)
        
        # Create output directories
        self.output_dir.mkdir(exist_ok=True)
        (self.output_dir / "audio").mkdir(exist_ok=True)
        (self.output_dir / "rss").mkdir(exist_ok=True)
        (self.output_dir / "reports").mkdir(exist_ok=True)
        
        logger.info(f"🏠 Local processor initialized")
        logger.info(f"📁 Sample emails: {self.sample_emails_dir}")
        logger.info(f"📁 Output directory: {self.output_dir}")
    
    def process_newsletter_queue(self) -> Dict[str, Any]:
        """
        Main processing function - modified to read from local files
        """
        try:
            # Get all sample email files
            all_messages = self._load_sample_emails()
            
            if not all_messages:
                return {
                    'status': '📭 No sample emails found',
                    'total_emails': 0,
                    'summary': 'No sample email files to process.',
                    'processed_at': datetime.now().isoformat()
                }
            
            logger.info(f"📧 Processing {len(all_messages)} sample emails")
            
            # Use the same processing logic as the original
            enhanced_emails = self._enhance_emails_with_web_content(all_messages)
            
            # Apply hybrid processing strategy
            summary = self._hybrid_processing(enhanced_emails)
            
            # Generate podcast audio locally
            audio_info = None
            if summary.get('podcast_content'):
                audio_info = self._generate_podcast_audio_local(
                    summary['podcast_content'], 
                    datetime.now().isoformat()
                )
            
            # Save reports locally
            self._save_reports_local(summary, enhanced_emails)
            
            result = {
                'status': '✅ Success (Local)',
                'total_emails': len(enhanced_emails),
                'processing_strategy': summary['strategy_used'],
                'summary': summary['content'],
                'key_insights': summary['insights'],
                'top_links': summary.get('top_links', []),
                'podcast_content': summary.get('podcast_content', ''),
                'podcast_headlines': summary.get('podcast_headlines', []),
                'podcast_deep_dive': summary.get('podcast_deep_dive', ''),
                'processed_at': datetime.now().isoformat()
            }
            
            # Add local audio information if generated
            if audio_info:
                result.update({
                    'audio_generated': True,
                    'local_audio_path': audio_info['local_path'],
                    'audio_size': audio_info['audio_size']
                })
            else:
                result['audio_generated'] = False
            
            return result
            
        except Exception as e:
            logger.error(f"Error processing local newsletters: {str(e)}")
            return {
                'status': '❌ Failure (Local)',
                'error': str(e),
                'processed_at': datetime.now().isoformat()
            }
    
    def _load_sample_emails(self) -> List[Dict[str, Any]]:
        """Load sample emails from JSON files"""
        emails = []
        
        try:
            # Find all JSON files in sample emails directory
            json_files = list(self.sample_emails_dir.glob("*.json"))
            
            logger.info(f"📁 Found {len(json_files)} sample email files")
            
            for json_file in json_files:
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        email_data = json.load(f)
                    
                    # Add filename for reference
                    email_data['source_file'] = json_file.name
                    emails.append(email_data)
                    
                    logger.info(f"✅ Loaded: {json_file.name} - {email_data.get('newsletter_type', 'Unknown')}")
                    
                except Exception as e:
                    logger.error(f"❌ Error loading {json_file}: {str(e)}")
                    continue
            
            return emails
            
        except Exception as e:
            logger.error(f"Error loading sample emails: {str(e)}")
            return []
    
    def _generate_podcast_audio_local(self, podcast_content: str, processed_date: str) -> Optional[Dict[str, str]]:
        """Generate MP3 audio and save locally"""
        try:
            logger.info("🎵 Starting local podcast audio generation")
            
            # Clean up the text for speech synthesis (using parent method)
            clean_text = self._prepare_text_for_speech(podcast_content)
            
            if not clean_text.strip():
                logger.warning("⚠️ No content available for audio generation")
                return None
            
            # Save cleaned text locally
            date_str = datetime.fromisoformat(processed_date.replace('Z', '+00:00')).strftime('%Y-%m-%d')
            text_file = self.output_dir / "reports" / f"podcast_script_{date_str}.txt"
            with open(text_file, 'w', encoding='utf-8') as f:
                f.write(clean_text)
            logger.info(f"💾 Saved podcast script: {text_file}")
            
            # Convert to speech using remote Polly (parent method)
            audio_data = self._convert_text_to_speech(clean_text)
            
            if not audio_data:
                logger.warning("⚠️ No audio data generated")
                return None
            
            # Save audio locally
            audio_file = self.output_dir / "audio" / f"ai_newsletter_podcast_{date_str}.mp3"
            with open(audio_file, 'wb') as f:
                f.write(audio_data)
            
            logger.info(f"🎵 Saved audio file: {audio_file} ({len(audio_data)} bytes)")
            
            # Generate local RSS feed
            self._generate_local_rss_feed(podcast_content, audio_file, len(audio_data), date_str)
            
            return {
                'local_path': str(audio_file),
                'audio_size': len(audio_data)
            }
            
        except Exception as e:
            logger.error(f"❌ Error generating local podcast audio: {str(e)}")
            return None
    
    def _generate_local_rss_feed(self, podcast_content: str, audio_file: Path, audio_size: int, date_str: str):
        """Generate RSS feed XML and save locally"""
        try:
            from xml.etree.ElementTree import Element, SubElement, tostring
            from xml.dom import minidom
            
            # Create RSS structure
            rss = Element("rss", version="2.0", attrib={"xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"})
            channel = SubElement(rss, "channel")
            
            # Channel metadata
            SubElement(channel, "title").text = "Daily AI, by AI (Local Development)"
            SubElement(channel, "link").text = "https://dailyaibyai.news"
            SubElement(channel, "language").text = "en-us"
            SubElement(channel, "itunes:author").text = "AI Newsletter Processor (Local)"
            SubElement(channel, "description").text = (
                "Local development version of the Daily AI newsletter podcast. "
                "Comprehensive analysis of AI developments, generated locally for testing."
            )
            SubElement(channel, "itunes:explicit").text = "false"
            SubElement(channel, "itunes:category", text="Technology")
            SubElement(channel, "itunes:category", text="News")
            
            # Episode item
            item = SubElement(channel, "item")
            
            episode_title = f"Daily AI Summary - {date_str} (Local)"
            SubElement(item, "title").text = episode_title
            
            # Create episode description from content
            episode_description = self._create_episode_description(podcast_content)
            SubElement(item, "description").text = episode_description
            
            # Local audio file reference
            audio_url = f"file://{audio_file.absolute()}"
            SubElement(item, "enclosure", 
                      url=audio_url,
                      length=str(audio_size), 
                      type="audio/mpeg")
            
            episode_guid = f"daily-ai-local-{date_str}"
            SubElement(item, "guid").text = episode_guid
            
            pubdate = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")
            SubElement(item, "pubDate").text = pubdate
            
            SubElement(item, "itunes:duration").text = "10:00"
            
            # Convert to pretty XML
            rss_bytes = tostring(rss, encoding="utf-8", xml_declaration=True)
            pretty_xml = minidom.parseString(rss_bytes).toprettyxml(encoding="utf-8")
            
            # Save RSS feed locally
            rss_file = self.output_dir / "rss" / f"podcast_feed_{date_str}.xml"
            with open(rss_file, 'wb') as f:
                f.write(pretty_xml)
            
            logger.info(f"📡 Saved RSS feed: {rss_file}")
            
        except Exception as e:
            logger.error(f"❌ Error generating local RSS feed: {str(e)}")
    
    def _save_reports_local(self, summary: Dict[str, Any], emails: List[Dict[str, Any]]):
        """Save executive report and podcast script locally"""
        try:
            date_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            
            # Save executive report
            exec_report = {
                'generated_at': datetime.now().isoformat(),
                'strategy_used': summary['strategy_used'],
                'total_emails': len(emails),
                'newsletter_sources': [email.get('newsletter_type', 'Unknown') for email in emails],
                'executive_summary': summary['content'],
                'key_insights': summary['insights'],
                'top_links': summary.get('top_links', [])
            }
            
            exec_file = self.output_dir / "reports" / f"executive_report_{date_str}.json"
            with open(exec_file, 'w', encoding='utf-8') as f:
                json.dump(exec_report, f, indent=2, ensure_ascii=False)
            
            logger.info(f"📊 Saved executive report: {exec_file}")
            
            # Save podcast script
            podcast_report = {
                'generated_at': datetime.now().isoformat(),
                'strategy_used': summary['strategy_used'],
                'podcast_content': summary.get('podcast_content', ''),
                'headlines': summary.get('podcast_headlines', []),
                'deep_dive': summary.get('podcast_deep_dive', '')
            }
            
            podcast_file = self.output_dir / "reports" / f"podcast_report_{date_str}.json"
            with open(podcast_file, 'w', encoding='utf-8') as f:
                json.dump(podcast_report, f, indent=2, ensure_ascii=False)
            
            logger.info(f"🎙️ Saved podcast report: {podcast_file}")
            
        except Exception as e:
            logger.error(f"❌ Error saving reports locally: {str(e)}")
    
    def send_summary_email(self, result_data: Dict[str, Any]) -> None:
        """Override to save summary locally instead of sending email"""
        try:
            date_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            
            # Create email content (using parent method)
            if result_data['status'].startswith('✅'):
                email_content = self._create_success_email(result_data)
            else:
                email_content = self._create_error_email(result_data)
            
            # Save email content locally
            email_file = self.output_dir / "reports" / f"summary_email_{date_str}.txt"
            with open(email_file, 'w', encoding='utf-8') as f:
                f.write(email_content)
            
            logger.info(f"📧 Saved summary email: {email_file}")
            
        except Exception as e:
            logger.error(f"❌ Error saving summary email: {str(e)}")

def main():
    """Main function to run local processing"""
    logger.info("🚀 Starting local AI newsletter processing")
    
    try:
        # Initialize local processor
        processor = LocalNewsletterProcessor()
        
        # Process newsletters
        result = processor.process_newsletter_queue()
        
        # Save summary email
        processor.send_summary_email(result)
        
        # Print results
        print("\n" + "="*60)
        print("📋 PROCESSING RESULTS")
        print("="*60)
        print(f"Status: {result['status']}")
        print(f"Total Emails: {result.get('total_emails', 0)}")
        print(f"Strategy: {result.get('processing_strategy', 'Unknown')}")
        print(f"Audio Generated: {result.get('audio_generated', False)}")
        
        if result.get('local_audio_path'):
            print(f"Audio File: {result['local_audio_path']}")
        
        print(f"Processed At: {result.get('processed_at')}")
        print("\n📁 Check the 'output' directory for all generated files!")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Local processing failed: {str(e)}")
        raise

if __name__ == "__main__":
    main()