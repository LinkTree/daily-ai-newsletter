import json
import boto3
import email
import base64
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
import os
import time
import logging
import tempfile
from urllib.parse import urljoin, urlparse
from xml.etree.ElementTree import fromstring, Element, SubElement, tostring, register_namespace
from xml.dom import minidom

# Import optional dependencies with fallbacks
try:
    import requests
    from bs4 import BeautifulSoup
    WEB_FETCHING_AVAILABLE = True
except ImportError:
    WEB_FETCHING_AVAILABLE = False
    logging.warning("Web fetching dependencies not available")

try:
    import tiktoken
    TOKEN_COUNTING_AVAILABLE = True
except ImportError:
    TOKEN_COUNTING_AVAILABLE = False
    logging.warning("Token counting not available, using character estimation")

try:
    from mutagen.mp3 import MP3
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False
    logging.warning("Mutagen not available, using default duration for podcasts")

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Register XML namespace for iTunes podcast tags
register_namespace('itunes', "http://www.itunes.com/dtds/podcast-1.0.dtd")

class ClaudeNewsletterProcessor:
    def __init__(self, test_mode: bool = False, create_rss: bool = True):
        self.sqs_client = boto3.client('sqs')
        self.sns_client = boto3.client('sns')
        self.polly_client = boto3.client('polly')
        self.s3_client = boto3.client('s3')
        self.dynamodb = boto3.resource('dynamodb')
        
        # Environment variables
        self.queue_url = os.environ.get('EMAIL_QUEUE_URL')
        self.sns_topic_arn = os.environ.get('SNS_TOPIC_ARN')
        self.claude_api_key = os.environ.get('CLAUDE_API_KEY')
        self.max_messages = int(os.environ.get('MAX_MESSAGES', '50'))
        self.max_links_per_email = int(os.environ.get('MAX_LINKS_PER_EMAIL', '5'))
        self.dynamodb_table_name = os.environ.get('DYNAMODB_TABLE_NAME', 'ai_daily_news')

        # Environment detection for staging
        self.environment = os.environ.get('ENVIRONMENT', 'production')
        self.s3_key_prefix = os.environ.get('S3_KEY_PREFIX', '')
        self.notification_prefix = os.environ.get('NOTIFICATION_PREFIX', '')
        self.podcast_title = os.environ.get('PODCAST_TITLE', 'Daily AI, by AI')
        self.podcast_title_short = os.environ.get('PODCAST_TITLE_SHORT', 'Daily AI')

        # Claude API rate limiting - NEW CONFIGURATION
        self.claude_requests_per_minute = int(os.environ.get('CLAUDE_RPM_LIMIT', '5'))
        self.claude_max_retries = int(os.environ.get('CLAUDE_MAX_RETRIES', '6'))
        self.claude_base_delay = int(os.environ.get('CLAUDE_BASE_DELAY', '10'))
        self.min_request_interval = 60 / self.claude_requests_per_minute # seconds between requests
        self.last_request_time = 0

        # Polly and S3 configuration
        self.s3_bucket = os.environ.get('PODCAST_S3_BUCKET', 'ai-newsletter-podcasts')
        self.polly_voice = os.environ.get('POLLY_VOICE', 'Joanna')
        self.polly_rate = os.environ.get('POLLY_RATE', 'medium')
        self.generate_audio = os.environ.get('GENERATE_AUDIO', 'true').lower() == 'true'
        self.update_rss_feed = os.environ.get('UPDATE_RSS_FEED', 'true').lower() == 'true'
        
        # RSS Feed configuration
        self.feed_key = os.environ.get('RSS_FEED_NAME', 'feed.xml')
        self.podcast_image_url = os.environ.get('PODCAST_IMAGE_URL', f'https://{self.s3_bucket}.s3.amazonaws.com/podcast.png')
        
        # Test mode configuration (can be set via parameter or environment)
        self.test_mode = test_mode or os.environ.get('TEST_MODE', 'false').lower() == 'true'
        if self.test_mode:
            logger.info("⚠️  Processor initialized in TEST MODE - messages will not be deleted")
        
        # RSS creation configuration (can be set via parameter or environment)
        self.create_rss = create_rss and os.environ.get('UPDATE_RSS_FEED', 'true').lower() == 'true'
        if not self.create_rss:
            logger.info("⚠️  RSS feed creation DISABLED")
        
        # Claude API configuration
        self.claude_model = "claude-sonnet-4-5-20250929"  # You can change to claude-3-opus-20240229 for better quality
        self.claude_api_url = "https://api.anthropic.com/v1/messages"
        
        # Token counting for Claude (with fallback)
        if TOKEN_COUNTING_AVAILABLE:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")  # Approximate for Claude
        else:
            self.tokenizer = None
        self.max_tokens_per_batch = 800000  # Conservative limit for Claude context
        
        # Validate required environment variables
        required_vars = ['EMAIL_QUEUE_URL', 'SNS_TOPIC_ARN', 'CLAUDE_API_KEY']
        for var in required_vars:
            if not os.environ.get(var):
                raise ValueError(f"{var} environment variable is required")
        
        # Initialize prompts
        self._init_prompts()
    
    def _init_prompts(self):
        """Initialize all prompt templates in one central location"""
        
        # =======================================================================
        # EXECUTIVE REPORT PROMPTS
        # =======================================================================
        
        self.EXECUTIVE_COMPREHENSIVE_PROMPT = """
You are a Technology News Editor, expert in the AI domain analyzing daily AI newsletters. I need a comprehensive summary of {num_emails} AI newsletters received today.

Here are the newsletters with their linked content:

{email_content}

Please provide a comprehensive analysis with:

1. **Executive Summary**: A 2-3 paragraph overview of the day's most important AI developments

2. **Key Themes**: Identify 3-5 major themes or trends emerging across all newsletters

3. **Breaking News**: Any significant announcements, funding rounds, or major developments

4. **Technical Insights**: Important technical developments, research breakthroughs, or new capabilities

5. **Market Impact**: Business implications, competitive landscape changes, and market movements

6. **Notable Links**: Top 3-5 most important links worth reading further (with brief descriptions)

7. **Tomorrow's Focus**: What to watch for based on today's developments

Format your response as a well-structured report that an AI-focused executive would want to read to stay informed.
"""

        self.EXECUTIVE_BATCH_PROMPT = """
You are a Technology News Editor, expert in the AI domain analyzing AI newsletters - this is batch {batch_num}. Summarize these {num_emails} newsletters:

{batch_content}

Provide:
1. **Key Developments**: Main developments from these newsletters
2. **Important Links**: Most valuable links and why they matter
3. **Notable Quotes/Data**: Any significant statistics or announcements

Keep it concise but comprehensive.
"""

        self.EXECUTIVE_META_SUMMARY_PROMPT = """
You are a a Technology News Editor, expert in the AI domain, creating a final summary from {num_batches} batch summaries covering {num_emails} AI newsletters from sources: {newsletter_types}.

Here are the batch summaries:

{batch_summaries}

Create a comprehensive final report with:

1. **Executive Summary**: Overall picture of today's AI developments
2. **Key Themes**: Major trends across all newsletters  
3. **Critical Developments**: Most important news/announcements
4. **Technical Breakthroughs**: Notable research or technical advances
5. **Market & Business Impact**: Commercial implications
6. **Must-Read Links**: Top 5 links worth following up on
7. **Looking Ahead**: What these developments mean for the future

Make this a report an AI executive would want to read to stay informed.
"""

        # =======================================================================
        # PODCAST SCRIPT PROMPTS  
        # =======================================================================
        
        self.PODCAST_COMPREHENSIVE_PROMPT = """
You are a professional technology radio host, creating a podcast script analyzing {num_emails} AI newsletters received today. Create engaging spoken content for a technology podcast.

IMPORTANT: Do NOT include any introduction, welcome, or opening statements. Jump directly into the content. The introduction will be added separately.

Here are the newsletters with their linked content:

{email_content}

Create a podcast script with these TWO sections:

## TOP NEWS HEADLINES
Provide 5-6 concise news items in conversational podcast format. Each headline should be 1-2 sentences maximum, written as if being spoken aloud to a business audience. Focus on the most impactful and interesting stories.

Example format:
"OpenAI just announced their new reasoning model that can solve complex problems..."
"Google's latest AI breakthrough shows 40% improvement in code generation..."

## DEEP DIVE ANALYSIS
Take the single most important news item from your headlines and provide a comprehensive analysis covering:

1. **Technical Deep Dive**: Explain the technology involved, how it works, and technical implications in accessible terms
2. **Financial Analysis**: Discuss funding, valuations, revenue implications, cost considerations, and business model impacts
3. **Market Disruption**: Analyze competitive positioning, market disruption potential, and broader industry effects
4. **Cultural & Social Impact**: How this affects society, user behavior, adoption patterns, and cultural shifts
5. **Executive Action Plan**: Provide 2-3 specific, actionable recommendations for what a technology company executive should consider doing in response to this development

Write this entire section as engaging podcast content - conversational, insightful, but professional. Speak directly to technology executives as your audience.

IMPORTANT: Do NOT include any closing statements, sign-offs, or "thank you for listening" type endings. End with the content. The closing will be added separately.
"""

        self.PODCAST_BATCH_PROMPT = """
You are creating podcast content from AI newsletters - this is batch {batch_num}. Analyze these {num_emails} newsletters for podcast format.

IMPORTANT: Do NOT include any introduction or closing statements. Jump directly into the content.

{batch_content}

Provide podcast-style content focusing on:
1. **Key Developments**: Main developments suitable for podcast headlines
2. **Important Stories**: Most compelling stories with details
3. **Notable Information**: Any significant data, quotes, or technical details

Write in conversational podcast style, keep it engaging but concise. Do NOT include any sign-offs or closing remarks.
"""

        self.PODCAST_META_SUMMARY_PROMPT = """
You are creating a final podcast script from {num_batches} batch summaries covering {num_emails} AI newsletters from sources: {newsletter_types}.

IMPORTANT: Do NOT include any introduction, welcome, or opening statements. Do NOT include any closing statements or sign-offs. Jump directly into the content and end with the content.

Here are the batch summaries:

{batch_summaries}

Create a comprehensive podcast script with these TWO sections:

## TOP NEWS HEADLINES
Provide 5-6 concise news items in conversational podcast format. Each headline should be 1-2 sentences maximum, written as if being spoken aloud to a business audience.

## DEEP DIVE ANALYSIS
Take the single most important news item and provide comprehensive analysis covering:
1. **Technical Deep Dive**: Technology explanation in accessible terms
2. **Financial Analysis**: Business implications, funding, valuations
3. **Market Disruption**: Competitive impact and industry effects
4. **Cultural & Social Impact**: Societal and behavioral implications
5. **Executive Action Plan**: 2-3 specific recommendations for tech executives

Write as engaging podcast content for technology executives. Do NOT include any closing remarks or sign-offs.
"""

    def save_podcast_to_dynamodb(self, text: str, episode_title: str = None) -> bool:
        """Save podcast text and episode title to DynamoDB with current date"""
        try:
            table = self.dynamodb.Table(self.dynamodb_table_name)
            current_date = datetime.now().strftime('%Y-%m-%d')

            # Use generated title or fallback to date-based title
            if not episode_title:
                episode_title = f"{self.podcast_title_short} Summary - {current_date}"

            table.put_item(
                Item={
                    'date': current_date,
                    'text': text,
                    'episode_title': episode_title,
                    'generated_at': datetime.now().isoformat()
                }
            )

            logger.info(f"Successfully saved podcast text and title to DynamoDB table: {self.dynamodb_table_name}")
            return True

        except Exception as e:
            logger.error(f"Error saving to DynamoDB: {str(e)}")
            return False

    def process_newsletter_queue(self) -> Dict[str, Any]:
        """
        Main processing function using hybrid strategy
        
        Returns:
            Processing results summary
        """
        try:
            # Get all messages from queue
            all_messages = self._get_all_queue_messages()
            
            if not all_messages:
                return {
                    'status': '📭 No emails found',
                    'total_emails': 0,
                    'summary': 'No new AI newsletters to process.',
                    'processed_at': datetime.now().isoformat()
                }
            
            logger.info(f"Processing {len(all_messages)} emails from queue")
            
            # Parse all emails first
            parsed_emails = []
            for message in all_messages:
                try:
                    email_data = self._parse_sqs_message(message)
                    parsed_emails.append(email_data)
                except Exception as e:
                    logger.error(f"Error parsing message: {str(e)}")
                    continue
            
            # Enhance emails with web content
            enhanced_emails = self._enhance_emails_with_web_content(parsed_emails)
            
            # Apply hybrid processing strategy
            summary = self._hybrid_processing(enhanced_emails)

            # Generate episode title from podcast content
            episode_title = None
            if summary.get('podcast_content'):
                episode_title = self._generate_episode_title(summary['podcast_content'])

            # Generate podcast audio if enabled
            audio_info = None
            if summary.get('podcast_content'):
                audio_info = self._generate_podcast_audio(
                    summary['podcast_content'],
                    datetime.now().isoformat(),
                    episode_title
                )
            
            # Clean up processed messages (only in production mode)
            if not self.test_mode:
                self._cleanup_messages(all_messages)
            else:
                logger.info("TEST MODE: Skipping message cleanup - messages will remain in queue")
            
            result = {
                'status': '✅ Success',
                'total_emails': len(enhanced_emails),
                'processing_strategy': summary['strategy_used'],
                'podcast_content': summary.get('podcast_content', ''),
                'podcast_headlines': summary.get('podcast_headlines', []),
                'podcast_deep_dive': summary.get('podcast_deep_dive', ''),
                'episode_title': episode_title,
                'processed_at': datetime.now().isoformat()
            }
            
            # Add audio information if generated
            if audio_info:
                result.update({
                    'audio_generated': True,
                    'audio_url': audio_info['audio_url'],
                    'audio_key': audio_info['audio_key'],
                    'audio_size': audio_info['audio_size']
                })
            else:
                result['audio_generated'] = False
            
            return result
            
        except Exception as e:
            logger.error(f"Error processing newsletter queue: {str(e)}")
            return {
                'status': '❌ Failure',
                'error': str(e),
                'processed_at': datetime.now().isoformat()
            }
    
    def _enforce_rate_limit(self):
        """Ensure we don't exceed rate limits"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time

        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last
            logger.info(f"Rate limiting: sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)

        self.last_request_time = time.time()

    def _get_all_queue_messages(self) -> List[Dict[str, Any]]:
        """Retrieve all messages from SQS queue"""
        all_messages = []
        
        logger.info(f"Starting to retrieve messages from queue: {self.queue_url}")
        logger.info(f"Max messages to retrieve: {self.max_messages}")
        
        # Check queue attributes first
        try:
            queue_attrs = self.sqs_client.get_queue_attributes(
                QueueUrl=self.queue_url,
                AttributeNames=['ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible', 'ApproximateNumberOfMessagesDelayed']
            )
            attrs = queue_attrs.get('Attributes', {})
            logger.info(f"Queue stats - Available: {attrs.get('ApproximateNumberOfMessages', 'unknown')}, "
                       f"Not Visible: {attrs.get('ApproximateNumberOfMessagesNotVisible', 'unknown')}, "
                       f"Delayed: {attrs.get('ApproximateNumberOfMessagesDelayed', 'unknown')}")
        except Exception as e:
            logger.warning(f"Could not get queue attributes: {str(e)}")
        
        attempt = 0
        while len(all_messages) < self.max_messages:
            attempt += 1
            try:
                logger.info(f"Attempt {attempt}: Polling SQS queue...")

                # Use shorter visibility timeout for test mode so messages become visible again quickly
                visibility_timeout = 30 if self.test_mode else None

                # Build receive_message parameters
                receive_params = {
                    'QueueUrl': self.queue_url,
                    'MaxNumberOfMessages': 10,
                    'WaitTimeSeconds': 2
                }
                if visibility_timeout:
                    receive_params['VisibilityTimeout'] = visibility_timeout
                    logger.info(f"Using VisibilityTimeout={visibility_timeout}s for test mode")

                # Try simpler receive call first
                response = self.sqs_client.receive_message(**receive_params)

                # If that doesn't work, try with all attributes
                if not response.get('Messages') and attempt == 1:
                    logger.info("Trying with message attributes...")
                    receive_params['WaitTimeSeconds'] = 5
                    receive_params['MessageAttributeNames'] = ['All']
                    receive_params['AttributeNames'] = ['All']
                    response = self.sqs_client.receive_message(**receive_params)
                
                logger.info(f"SQS response keys: {list(response.keys())}")
                
                messages = response.get('Messages', [])
                logger.info(f"Found {len(messages)} messages in this batch")
                
                if not messages:
                    logger.info("No messages found, breaking loop")
                    break
                
                all_messages.extend(messages)
                logger.info(f"Retrieved {len(messages)} messages, total: {len(all_messages)}")
                
            except Exception as e:
                logger.error(f"Error retrieving messages on attempt {attempt}: {str(e)}")
                logger.error(f"Queue URL being used: {self.queue_url}")
                break
        
        logger.info(f"Final message count: {len(all_messages)}")
        return all_messages
    
    def _parse_sqs_message(self, sqs_message: Dict[str, Any]) -> Dict[str, Any]:
        """Parse email content from SQS message"""
        try:
            message_body = json.loads(sqs_message['Body'])
            
            if 'Message' in message_body:
                ses_message = json.loads(message_body['Message'])
            else:
                ses_message = message_body
            
            mail_info = ses_message.get('mail', {})
            common_headers = mail_info.get('commonHeaders', {})
            
            # Extract email content
            email_content = ""
            if 'content' in ses_message:
                try:
                    decoded_content = base64.b64decode(ses_message['content']).decode('utf-8')
                    email_message = email.message_from_string(decoded_content)
                    email_content = self._extract_email_text(email_message)
                except Exception as e:
                    logger.warning(f"Could not decode email content: {str(e)}")
                    email_content = ""
            
            return {
                'message_id': mail_info.get('messageId', 'Unknown'),
                'from': common_headers.get('from', ['Unknown'])[0],
                'subject': common_headers.get('subject', 'No Subject'),
                'date': common_headers.get('date', 'Unknown Date'),
                'content': email_content,
                'newsletter_type': self._identify_newsletter(
                    common_headers.get('from', [''])[0], 
                    common_headers.get('subject', '')
                ),
                'sqs_receipt_handle': sqs_message['ReceiptHandle']
            }
            
        except Exception as e:
            logger.error(f"Error parsing SQS message: {str(e)}")
            raise
    
    def _extract_email_text(self, email_message) -> str:
        """Extract text content from email message"""
        try:
            if email_message.is_multipart():
                for part in email_message.walk():
                    if part.get_content_type() == "text/plain":
                        return part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    elif part.get_content_type() == "text/html":
                        html_content = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        return self._html_to_text(html_content)
            else:
                content = email_message.get_payload(decode=True)
                if content:
                    content_str = content.decode('utf-8', errors='ignore')
                    if email_message.get_content_type() == "text/html":
                        return self._html_to_text(content_str)
                    return content_str
            
            return ""
            
        except Exception as e:
            logger.warning(f"Error extracting email text: {str(e)}")
            return ""
    
    def _html_to_text(self, html_content: str) -> str:
        """Convert HTML to clean text"""
        if not WEB_FETCHING_AVAILABLE:
            # Simple HTML tag removal if BeautifulSoup not available
            import re
            # Remove HTML tags
            clean = re.compile('<.*?>')
            text = re.sub(clean, '', html_content)
            return text.strip()
        
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            return soup.get_text(separator='\n', strip=True)
        except Exception as e:
            logger.warning(f"Error converting HTML to text: {str(e)}")
            return html_content
    
    def _identify_newsletter(self, from_address: str, subject: str) -> str:
        """Identify newsletter type"""
        from_lower = from_address.lower()
        subject_lower = subject.lower()
        
        patterns = {
            'TLDR AI': ['tldrnewsletter.com', 'tldr ai'],
            "Ben's Bites": ['bensbites', "ben's bites"],
            'AI Secret': ['aisecret', 'ai secret'],
            'AI Israel Weekly': ['ai-israel'],
            'Aftershoot AI': ['aftershoot'],
            'The Rundown AI': ['therundown', 'rundown'],
            'AI Breakfast': ['aibreakfast'],
            'Import AI': ['importai'],
            'Test Email': ['linktree@gmail.com']
        }
        
        for newsletter_type, keywords in patterns.items():
            if any(keyword in from_lower or keyword in subject_lower for keyword in keywords):
                return newsletter_type
        
        return 'Other AI Newsletter'
    
    def _enhance_emails_with_web_content(self, emails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Enhance emails by fetching content from links"""
        if not WEB_FETCHING_AVAILABLE:
            logger.warning("Web fetching not available, skipping link content extraction")
            for email_data in emails:
                email_data['extracted_links'] = []
                email_data['web_content'] = []
                email_data['enhanced'] = False
            return emails
        
        enhanced_emails = []
        
        for email_data in emails:
            try:
                # Extract links from email content
                links = self._extract_links(email_data['content'])
                
                # Fetch content from top links
                web_content = self._fetch_web_content(links[:self.max_links_per_email])
                
                # Add web content to email data
                email_data['extracted_links'] = links
                email_data['web_content'] = web_content
                email_data['enhanced'] = len(web_content) > 0
                
                enhanced_emails.append(email_data)
                
            except Exception as e:
                logger.warning(f"Error enhancing email {email_data.get('message_id', 'unknown')}: {str(e)}")
                email_data['enhanced'] = False
                email_data['web_content'] = []
                enhanced_emails.append(email_data)
        
        return enhanced_emails
    
    def _extract_links(self, content: str) -> List[str]:
        """Extract HTTP/HTTPS links from email content"""
        if not content:
            return []
        
        # Pattern to match HTTP/HTTPS URLs
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+[^\s<>"{}|\\^`\[\].,;!?]'
        links = re.findall(url_pattern, content)
        
        # Filter out common tracking/unsubscribe links
        filtered_links = []
        exclude_patterns = [
            'unsubscribe', 'tracking', 'pixel', 'beacon', 'analytics',
            'utm_campaign', 'mailchimp', 'substack.com/unsubscribe',
            'manage-subscription', 'email-settings'
        ]
        
        for link in links:
            if not any(pattern in link.lower() for pattern in exclude_patterns):
                filtered_links.append(link)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_links = []
        for link in filtered_links:
            if link not in seen:
                seen.add(link)
                unique_links.append(link)
        
        return unique_links[:self.max_links_per_email * 2]  # Get more than needed, filter later
    
    def _fetch_web_content(self, links: List[str]) -> List[Dict[str, Any]]:
        """Fetch content from web links"""
        if not WEB_FETCHING_AVAILABLE:
            logger.warning("Web fetching not available")
            return []
        
        web_content = []
        
        for link in links[:self.max_links_per_email]:
            try:
                content = self._fetch_single_url(link)
                if content:
                    web_content.append(content)
            except Exception as e:
                logger.warning(f"Error fetching {link}: {str(e)}")
                continue
        
        return web_content
    
    def _fetch_single_url(self, url: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
        """Fetch content from a single URL"""
        if not WEB_FETCHING_AVAILABLE:
            return None
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            
            # Only process HTML content
            content_type = response.headers.get('content-type', '').lower()
            if 'html' not in content_type:
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Remove unwanted elements
            for element in soup(['script', 'style', 'nav', 'footer', 'aside', 'header']):
                element.decompose()
            
            # Extract title
            title = soup.find('title')
            title_text = title.get_text(strip=True) if title else "No Title"
            
            # Extract main content (try different selectors)
            content_selectors = [
                'main', 'article', '.content', '.post-content', 
                '.article-content', '[role="main"]', '.entry-content'
            ]
            
            content_text = ""
            for selector in content_selectors:
                content_elem = soup.select_one(selector)
                if content_elem:
                    content_text = content_elem.get_text(separator='\n', strip=True)
                    break
            
            # Fallback to body if no specific content found
            if not content_text:
                body = soup.find('body')
                if body:
                    content_text = body.get_text(separator='\n', strip=True)
            
            # Limit content length
            max_content_length = 3000
            if len(content_text) > max_content_length:
                content_text = content_text[:max_content_length] + "..."
            
            return {
                'url': url,
                'title': title_text[:200],  # Limit title length
                'content': content_text,
                'fetched_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.warning(f"Error fetching URL {url}: {str(e)}")
            return None
    
    def _hybrid_processing(self, emails: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply hybrid processing strategy based on content size - PODCAST ONLY"""
        try:
            # Estimate total token count
            total_content = self._prepare_content_for_estimation(emails)
            estimated_tokens = self._estimate_tokens(total_content)

            logger.info(f"Estimated tokens: {estimated_tokens}, Max allowed: {self.max_tokens_per_batch}")

            if estimated_tokens <= self.max_tokens_per_batch:
                # Strategy 1: Single context processing - podcast only
                podcast_content = self._single_context_podcast_processing(emails)

                return {
                    'strategy_used': 'Single Context Processing (Podcast Only)',
                    'podcast_content': podcast_content['content'],
                    'podcast_headlines': podcast_content.get('headlines', []),
                    'podcast_deep_dive': podcast_content.get('deep_dive', '')
                }
            else:
                # Strategy 2: Batch processing with meta-summary - podcast only
                podcast_content = self._batch_podcast_processing(emails)

                return {
                    'strategy_used': f'Batch Processing (Podcast Only, {len(self._create_smart_batches(emails))} batches)',
                    'podcast_content': podcast_content['content'],
                    'podcast_headlines': podcast_content.get('headlines', []),
                    'podcast_deep_dive': podcast_content.get('deep_dive', '')
                }

        except Exception as e:
            logger.error(f"Error in hybrid processing: {str(e)}")
            raise
    
    def _prepare_content_for_estimation(self, emails: List[Dict[str, Any]]) -> str:
        """Prepare content for token estimation"""
        content_parts = []
        
        for email in emails:
            email_content = f"""
Newsletter: {email['newsletter_type']}
Subject: {email['subject']}
Content: {email['content'][:2000]}
"""
            
            # Add web content
            for web_item in email.get('web_content', []):
                email_content += f"\nLinked Article: {web_item['title']}\n{web_item['content'][:1000]}"
            
            content_parts.append(email_content)
        
        return '\n'.join(content_parts)
    
    def _estimate_tokens(self, text: str) -> int:
        """Estimate tokens with fallback"""
        if TOKEN_COUNTING_AVAILABLE and self.tokenizer:
            return len(self.tokenizer.encode(text))
        else:
            # Fallback: rough estimation of ~4 characters per token
            return len(text) // 4
    
    def _single_context_processing(self, emails: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process all emails in a single Claude API call"""
        try:
            logger.info("Using single context processing strategy")
            
            # Prepare comprehensive prompt
            prompt = self._create_comprehensive_prompt(emails)
            
            # Call Claude API
            response = self._call_claude_api(prompt)
            
            # Parse response
            parsed_response = self._parse_claude_response(response)
            
            return {
                'content': parsed_response['summary'],
                'insights': parsed_response['insights'],
                'top_links': parsed_response.get('top_links', [])
            }
            
        except Exception as e:
            logger.error(f"Error in single context processing: {str(e)}")
            raise
    
    def _batch_processing(self, emails: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process emails in batches with meta-summary"""
        try:
            logger.info("Using batch processing strategy")
            
            # Create smart batches
            batches = self._create_smart_batches(emails)
            
            # Process each batch
            batch_summaries = []
            for i, batch in enumerate(batches):
                try:
                    batch_prompt = self._create_batch_prompt(batch, i + 1)
                    batch_response = self._call_claude_api(batch_prompt)
                    batch_summaries.append(batch_response)
                except Exception as e:
                    logger.error(f"Error processing batch {i + 1}: {str(e)}")
                    continue
            
            # Create meta-summary
            meta_prompt = self._create_meta_summary_prompt(batch_summaries, emails)
            meta_response = self._call_claude_api(meta_prompt)
            parsed_meta = self._parse_claude_response(meta_response)
            
            return {
                'content': parsed_meta['summary'],
                'insights': parsed_meta['insights'],
                'top_links': parsed_meta.get('top_links', [])
            }
            
        except Exception as e:
            logger.error(f"Error in batch processing: {str(e)}")
            raise
    
    def _single_context_podcast_processing(self, emails: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process all emails in a single Claude API call for podcast format"""
        try:
            logger.info("Using single context podcast processing")
            
            # Prepare podcast prompt
            prompt = self._create_podcast_prompt(emails)
            
            # Call Claude API
            response = self._call_claude_api(prompt)
            
            # Parse podcast response
            parsed_response = self._parse_podcast_response(response)
            
            return {
                'content': parsed_response['full_content'],
                'headlines': parsed_response['headlines'],
                'deep_dive': parsed_response['deep_dive']
            }
            
        except Exception as e:
            logger.error(f"Error in single context podcast processing: {str(e)}")
            raise
    
    def _batch_podcast_processing(self, emails: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process emails in batches for podcast format with meta-summary"""
        try:
            logger.info("Using batch podcast processing")
            
            # Create smart batches
            batches = self._create_smart_batches(emails)
            
            # Process each batch for podcast content
            batch_podcast_summaries = []
            for i, batch in enumerate(batches):
                try:
                    batch_prompt = self._create_batch_podcast_prompt(batch, i + 1)
                    batch_response = self._call_claude_api(batch_prompt)
                    batch_podcast_summaries.append(batch_response)
                except Exception as e:
                    logger.error(f"Error processing podcast batch {i + 1}: {str(e)}")
                    continue
            
            # Create meta-podcast summary
            meta_prompt = self._create_meta_podcast_prompt(batch_podcast_summaries, emails)
            meta_response = self._call_claude_api(meta_prompt)
            parsed_meta = self._parse_podcast_response(meta_response)
            
            return {
                'content': parsed_meta['full_content'],
                'headlines': parsed_meta['headlines'],
                'deep_dive': parsed_meta['deep_dive']
            }
            
        except Exception as e:
            logger.error(f"Error in batch podcast processing: {str(e)}")
            raise
    
    def _create_smart_batches(self, emails: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Create smart batches based on token estimation"""
        batches = []
        current_batch = []
        current_tokens = 0
        
        for email in emails:
            # Estimate tokens for this email
            email_content = self._prepare_single_email_content(email)
            email_tokens = self._estimate_tokens(email_content)
            
            # Check if adding this email would exceed limit
            if current_tokens + email_tokens > self.max_tokens_per_batch * 0.7:  # Use 70% of limit for safety
                if current_batch:  # Only create batch if not empty
                    batches.append(current_batch)
                    current_batch = [email]
                    current_tokens = email_tokens
                else:
                    # Single email is too large, truncate it
                    truncated_email = self._truncate_email(email)
                    batches.append([truncated_email])
            else:
                current_batch.append(email)
                current_tokens += email_tokens
        
        # Add the last batch if not empty
        if current_batch:
            batches.append(current_batch)
        
        return batches
    
    def _prepare_single_email_content(self, email: Dict[str, Any]) -> str:
        """Prepare content for a single email for token estimation"""
        content = f"""
Newsletter: {email['newsletter_type']}
Subject: {email['subject']}
Content: {email['content']}
"""
        
        for web_item in email.get('web_content', []):
            content += f"\nLinked Article: {web_item['title']}\n{web_item['content']}"
        
        return content
    
    def _truncate_email(self, email: Dict[str, Any]) -> Dict[str, Any]:
        """Truncate email content to fit within token limits"""
        truncated_email = email.copy()
        
        # Truncate main content
        if len(email['content']) > 5000:
            truncated_email['content'] = email['content'][:5000] + "... [Content truncated]"
        
        # Limit web content
        if email.get('web_content'):
            truncated_email['web_content'] = email['web_content'][:2]  # Only first 2 web items
            for web_item in truncated_email['web_content']:
                if len(web_item['content']) > 1000:
                    web_item['content'] = web_item['content'][:1000] + "... [Content truncated]"
        
        return truncated_email
    
    def _create_comprehensive_prompt(self, emails: List[Dict[str, Any]]) -> str:
        """Create comprehensive prompt for single context processing"""
        # Build email content section
        email_content = ""
        for i, email in enumerate(emails, 1):
            email_content += f"""
### Newsletter {i}: {email['newsletter_type']}
**Subject:** {email['subject']}
**From:** {email['from']}
**Date:** {email['date']}

**Content:**
{email['content']}

"""
            
            # Add web content if available
            if email.get('web_content'):
                email_content += f"\n**Linked Articles ({len(email['web_content'])}):**\n"
                for j, web_item in enumerate(email['web_content'], 1):
                    email_content += f"""
{j}. **{web_item['title']}** ({web_item['url']})
{web_item['content']}

"""
        
        # Use centralized prompt template
        return self.EXECUTIVE_COMPREHENSIVE_PROMPT.format(
            num_emails=len(emails),
            email_content=email_content
        )
    
    def _create_podcast_prompt(self, emails: List[Dict[str, Any]]) -> str:
        """Create podcast-specific prompt for all emails"""
        # Build email content section (reuse same logic as executive prompt)
        email_content = ""
        for i, email in enumerate(emails, 1):
            email_content += f"""
### Newsletter {i}: {email['newsletter_type']}
**Subject:** {email['subject']}
**From:** {email['from']}
**Date:** {email['date']}

**Content:**
{email['content']}

"""
            
            # Add web content if available
            if email.get('web_content'):
                email_content += f"\n**Linked Articles ({len(email['web_content'])}):**\n"
                for j, web_item in enumerate(email['web_content'], 1):
                    email_content += f"""
{j}. **{web_item['title']}** ({web_item['url']})
{web_item['content']}

"""
        
        # Use centralized prompt template
        return self.PODCAST_COMPREHENSIVE_PROMPT.format(
            num_emails=len(emails),
            email_content=email_content
        )
    
    def _create_batch_podcast_prompt(self, batch: List[Dict[str, Any]], batch_num: int) -> str:
        """Create podcast batch prompt"""
        # Build batch content
        batch_content = ""
        for i, email in enumerate(batch, 1):
            batch_content += f"""
### Newsletter {i}: {email['newsletter_type']}
**Subject:** {email['subject']}
**Content:** {email['content'][:3000]}
"""
            
            if email.get('web_content'):
                for web_item in email['web_content'][:2]:  # Limit to 2 web items per email in batch
                    batch_content += f"\n**Linked:** {web_item['title']}\n{web_item['content'][:800]}\n"
        
        # Use centralized prompt template
        return self.PODCAST_BATCH_PROMPT.format(
            batch_num=batch_num,
            num_emails=len(batch),
            batch_content=batch_content
        )
    
    def _create_meta_podcast_prompt(self, batch_summaries: List[str], all_emails: List[Dict[str, Any]]) -> str:
        """Create meta-podcast summary prompt"""
        newsletter_types = list(set(email['newsletter_type'] for email in all_emails))
        
        # Build batch summaries section
        batch_summaries_content = ""
        for i, summary in enumerate(batch_summaries, 1):
            batch_summaries_content += f"""
### Batch {i} Summary:
{summary}

"""
        
        # Use centralized prompt template
        return self.PODCAST_META_SUMMARY_PROMPT.format(
            num_batches=len(batch_summaries),
            num_emails=len(all_emails),
            newsletter_types=', '.join(newsletter_types),
            batch_summaries=batch_summaries_content
        )
    
    def _create_batch_prompt(self, batch: List[Dict[str, Any]], batch_num: int) -> str:
        """Create prompt for batch processing"""
        # Build batch content
        batch_content = ""
        for i, email in enumerate(batch, 1):
            batch_content += f"""
### Newsletter {i}: {email['newsletter_type']}
**Subject:** {email['subject']}
**Content:** {email['content'][:3000]}
"""
            
            if email.get('web_content'):
                for web_item in email['web_content'][:2]:  # Limit to 2 web items per email in batch
                    batch_content += f"\n**Linked:** {web_item['title']}\n{web_item['content'][:800]}\n"
        
        # Use centralized prompt template
        return self.EXECUTIVE_BATCH_PROMPT.format(
            batch_num=batch_num,
            num_emails=len(batch),
            batch_content=batch_content
        )
    
    def _create_meta_summary_prompt(self, batch_summaries: List[str], all_emails: List[Dict[str, Any]]) -> str:
        """Create meta-summary prompt"""
        newsletter_types = list(set(email['newsletter_type'] for email in all_emails))
        
        # Build batch summaries section
        batch_summaries_content = ""
        for i, summary in enumerate(batch_summaries, 1):
            batch_summaries_content += f"""
### Batch {i} Summary:
{summary}

"""
        
        # Use centralized prompt template
        return self.EXECUTIVE_META_SUMMARY_PROMPT.format(
            num_batches=len(batch_summaries),
            num_emails=len(all_emails),
            newsletter_types=', '.join(newsletter_types),
            batch_summaries=batch_summaries_content
        )

    def _call_claude_api(self, prompt: str, retry_count: int = 0, max_tokens: int = 4000,
                        temperature: float = 1.0, model: str = None) -> str:
        """Call Claude API with exponential backoff retry logic"""
        try:
            # Enforce rate limiting before each request
            self._enforce_rate_limit()
            headers = {
                'Content-Type': 'application/json',
                'x-api-key': self.claude_api_key,
                'anthropic-version': '2023-06-01'
            }

            data = {
                'model': model if model else self.claude_model,
                'max_tokens': max_tokens,
                'temperature': temperature,
                'messages': [
                    {
                        'role': 'user',
                        'content': prompt
                    }
                ]
            }
            
            response = requests.post(
                self.claude_api_url,
                headers=headers,
                json=data,
                timeout=360
            )
            
            if response.status_code == 429:
                if retry_count < self.claude_max_retries:
                    # Exponential backoff: base_delay * 2^retry_count
                    delay = self.claude_base_delay * (2 ** retry_count)
                    logger.warning(f"Rate limited (429). Retrying in {delay}s (attempt {retry_count + 1}/{self.claude_max_retries})")
                    time.sleep(delay)
                    return self._call_claude_api(prompt, retry_count + 1, max_tokens, temperature, model)
                else:
                    raise Exception(f"Max retries ({self.claude_max_retries}) exceeded for rate limiting")
            
            response.raise_for_status()
            result = response.json()
            
            return result['content'][0]['text']
            
        except Exception as e:
            logger.error(f"Error calling Claude API: {str(e)}")
            raise
    
    def _parse_claude_response(self, response: str) -> Dict[str, Any]:
        """Parse Claude's response to extract structured information"""
        try:
            # Extract key sections using simple parsing
            lines = response.split('\n')
            
            summary_sections = []
            insights = []
            top_links = []
            
            current_section = "summary"
            current_content = []
            
            for line in lines:
                line = line.strip()
                
                # Detect section headers
                if any(keyword in line.lower() for keyword in ['executive summary', 'key themes', 'breaking news']):
                    if current_content:
                        summary_sections.append('\n'.join(current_content))
                    current_content = [line]
                    current_section = "summary"
                elif any(keyword in line.lower() for keyword in ['insight', 'notable', 'must-read', 'links']):
                    if current_content:
                        if current_section == "summary":
                            summary_sections.append('\n'.join(current_content))
                        else:
                            insights.append('\n'.join(current_content))
                    current_content = [line]
                    current_section = "insights"
                elif line.startswith(('http://', 'https://')):
                    top_links.append(line)
                else:
                    current_content.append(line)
            
            # Add final content
            if current_content:
                if current_section == "summary":
                    summary_sections.append('\n'.join(current_content))
                else:
                    insights.append('\n'.join(current_content))
            
            return {
                'summary': '\n\n'.join(summary_sections) if summary_sections else response,
                'insights': insights[:5],  # Limit to 5 insights
                'top_links': top_links[:5]  # Limit to 5 links
            }
            
        except Exception as e:
            logger.warning(f"Error parsing Claude response: {str(e)}")
            return {
                'summary': response,
                'insights': [],
                'top_links': []
            }
    
    def _parse_podcast_response(self, response: str) -> Dict[str, Any]:
        """Parse Claude's podcast response to extract structured content"""
        try:
            lines = response.split('\n')
            
            headlines = []
            deep_dive_content = []
            current_section = "unknown"
            current_content = []
            
            for line in lines:
                line = line.strip()
                
                # Detect section headers
                if any(keyword in line.lower() for keyword in ['top news headlines', 'headlines', 'news headlines']):
                    if current_content and current_section == "deep_dive":
                        deep_dive_content.extend(current_content)
                    current_content = []
                    current_section = "headlines"
                elif any(keyword in line.lower() for keyword in ['deep dive analysis', 'deep dive', 'analysis']):
                    if current_content and current_section == "headlines":
                        # Process headlines from current_content
                        headline_text = '\n'.join(current_content)
                        # Extract individual headlines (lines that look like news items)
                        for content_line in current_content:
                            content_line = content_line.strip()
                            if content_line and not content_line.startswith('#') and len(content_line) > 20:
                                # Clean up quote marks and numbering
                                clean_headline = content_line.strip('"').strip("'")
                                clean_headline = re.sub(r'^\d+\.\s*', '', clean_headline)
                                if clean_headline:
                                    headlines.append(clean_headline)
                    current_content = []
                    current_section = "deep_dive"
                elif line and not line.startswith('#'):
                    current_content.append(line)
            
            # Add final content
            if current_content:
                if current_section == "headlines":
                    for content_line in current_content:
                        content_line = content_line.strip()
                        if content_line and not content_line.startswith('#') and len(content_line) > 20:
                            clean_headline = content_line.strip('"').strip("'")
                            clean_headline = re.sub(r'^\d+\.\s*', '', clean_headline)
                            if clean_headline:
                                headlines.append(clean_headline)
                elif current_section == "deep_dive":
                    deep_dive_content.extend(current_content)
            
            # Limit headlines to 6
            headlines = headlines[:6]
            
            return {
                'full_content': response,
                'headlines': headlines,
                'deep_dive': '\n'.join(deep_dive_content) if deep_dive_content else response
            }

        except Exception as e:
            logger.warning(f"Error parsing podcast response: {str(e)}")
            return {
                'full_content': response,
                'headlines': [],
                'deep_dive': response
            }

    def _generate_episode_title(self, podcast_script: str) -> str:
        """Generate a compelling episode title using Claude Haiku"""
        try:
            logger.info("Generating episode title with Claude Haiku...")

            # Construct prompt for title generation
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

            # Call Claude Haiku 4.5 with specific parameters
            response = self._call_claude_api(
                prompt=prompt,
                max_tokens=50,
                temperature=0.7,
                model="claude-haiku-4-5"
            )

            # Clean up response (remove quotes, extra whitespace, trailing punctuation)
            title = response.strip().strip('"').strip("'").strip()

            # Validate title length
            word_count = len(title.split())
            if word_count < 3 or word_count > 15:
                logger.warning(f"Generated title has {word_count} words, outside recommended range")

            logger.info(f"Generated episode title: {title}")
            return title

        except Exception as e:
            logger.error(f"Error generating episode title: {str(e)}")
            # Fallback to date-based title
            current_date = datetime.now().strftime('%B %d, %Y')
            fallback_title = f"{self.podcast_title_short} Summary - {current_date}"
            logger.info(f"Using fallback title: {fallback_title}")
            return fallback_title

    def _chunk_text_for_polly(self, text: str, max_length: int = 2800) -> List[str]:
        """Chunk text for Polly synthesis (based on reference.py)"""
        import re
        
        # Split into sentences (with punctuation)
        sentence_endings = re.compile(r'(?<=[.!?])\s+')
        sentences = sentence_endings.split(text.strip())
        
        chunks = []
        current = ""

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            if len(current) + len(sentence) + 1 <= max_length:
                current += sentence + " "
            else:
                if current:
                    chunks.append(current.strip())
                current = sentence + " "

        if current:
            chunks.append(current.strip())

        return chunks
    
    def _convert_text_to_speech(self, text: str, voice_id: str = None, rate: str = None) -> bytes:
        """Convert text to speech using AWS Polly (based on reference.py)"""
        try:
            voice_id = voice_id or self.polly_voice
            rate = rate or self.polly_rate
            
            full_audio = b''
            chunks = self._chunk_text_for_polly(text)
            
            logger.info(f"Converting {len(chunks)} text chunks to speech using voice {voice_id}")
            
            for i, chunk in enumerate(chunks):
                try:
                    # Create SSML with prosody for natural speech
                    ssml_text = f"<speak><prosody rate='{rate}'>{chunk}</prosody></speak>"
                    
                    polly_response = self.polly_client.synthesize_speech(
                        Text=ssml_text,
                        TextType='ssml',
                        OutputFormat='mp3',
                        VoiceId=voice_id
                    )
                    
                    chunk_audio = polly_response['AudioStream'].read()
                    full_audio += chunk_audio
                    
                    logger.info(f"Processed chunk {i+1}/{len(chunks)}, size: {len(chunk_audio)} bytes")
                    
                except Exception as e:
                    logger.error(f"Error processing chunk {i+1}: {str(e)}")
                    # Continue with other chunks
                    continue
            
            logger.info(f"Total audio size: {len(full_audio)} bytes")
            return full_audio
            
        except Exception as e:
            logger.error(f"Error converting text to speech: {str(e)}")
            raise
    
    def _upload_audio_to_s3(self, audio_data: bytes, date_str: str) -> Tuple[str, str]:
        """Upload audio to S3 and return key and presigned URL"""
        try:
            # Create filename with date
            audio_key = f"{self.s3_key_prefix}podcasts/ai-newsletter-{date_str}.mp3"
            
            # Upload to S3
            self.s3_client.put_object(
                Bucket=self.s3_bucket,
                Key=audio_key,
                Body=audio_data,
                ContentType='audio/mpeg',
                Metadata={
                    'generated_at': datetime.now().isoformat(),
                    'content_type': 'ai_newsletter_podcast'
                }
            )
            
            # Generate presigned URL (expires in 7 days)
            presigned_url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.s3_bucket, 'Key': audio_key},
                ExpiresIn=604800  # 7 days
            )
            
            logger.info(f"Audio uploaded to S3: {audio_key}")
            return audio_key, presigned_url
            
        except Exception as e:
            logger.error(f"Error uploading audio to S3: {str(e)}")
            raise
    
    def _generate_podcast_audio(self, podcast_content: str, processed_date: str, episode_title: str = None) -> Optional[Dict[str, str]]:
        """Generate MP3 audio from podcast content"""
        if not self.generate_audio:
            logger.info("Audio generation disabled")
            return None
            
        try:
            logger.info("Starting podcast audio generation")
            
            # Clean up the text for speech synthesis
            # Remove markdown formatting and clean up for audio
            clean_text = self._prepare_text_for_speech(podcast_content)
            
            if not clean_text.strip():
                logger.warning("No content available for audio generation")
                return None
            
            # Save cleaned podcast text and title to DynamoDB
            self.save_podcast_to_dynamodb(clean_text, episode_title)
            
            # Convert to speech
            audio_data = self._convert_text_to_speech(clean_text)
            
            if not audio_data:
                logger.warning("No audio data generated")
                return None
            
            # Upload to S3
            date_str = datetime.fromisoformat(processed_date.replace('Z', '+00:00')).strftime('%Y-%m-%d')
            audio_key, presigned_url = self._upload_audio_to_s3(audio_data, date_str)
            
            # Update RSS feed only if enabled
            if self.create_rss:
                episode_date = datetime.fromisoformat(processed_date.replace('Z', '+00:00'))
                episode_description = self._create_episode_description(podcast_content)
                self._update_rss_feed(audio_key, len(audio_data), episode_description, episode_date, episode_title)
                logger.info("RSS feed updated with new episode")
            else:
                logger.info("RSS feed update skipped (create_rss=false)")
            
            return {
                'audio_key': audio_key,
                'audio_url': presigned_url,
                'audio_size': len(audio_data)
            }
            
        except Exception as e:
            logger.error(f"Error generating podcast audio: {str(e)}")
            return None
    
    def _prepare_text_for_speech(self, text: str) -> str:
        """Prepare text for speech synthesis by cleaning markdown and formatting"""
        import re
        import html
        from datetime import datetime
        
        # Get current date information for podcast intro
        now = datetime.now()
        day_name = now.strftime('%A')
        date_formatted = now.strftime('%B %d')
        
        # Add ordinal suffix to day
        day = now.day
        if 4 <= day <= 20 or 24 <= day <= 30:
            suffix = "th"
        else:
            suffix = ["st", "nd", "rd"][day % 10 - 1]
        date_formatted = now.strftime(f'%B {day}{suffix}')
        
        # Get host name from Polly voice
        voice_name = self.polly_voice
        if voice_name.lower() == 'joanna':
            host_name = "Joanna, a synthetic intelligence agent"
        elif voice_name.lower() == 'matthew':
            host_name = "Matthew, a synthetic intelligence agent"
        elif voice_name.lower() == 'amy':
            host_name = "Amy, a synthetic intelligence agent"
        elif voice_name.lower() == 'brian':
            host_name = "Brian, a synthetic intelligence agent"
        else:
            host_name = f"{voice_name}, a synthetic intelligence agent"
        
        # Create podcast introduction and outro (clean text)
        env_notice = "This is a staging test. " if self.environment == 'staging' else ""
        intro = f"{env_notice}Welcome to {self.podcast_title}. I'm {host_name}, bringing you today's most important developments in artificial intelligence. Today is {day_name}, {date_formatted}."
        outro = f"That's all for today's {self.podcast_title}. I'm {host_name}, and I'll be back tomorrow with more AI insights. Until then, keep innovating."
        
        # STEP 1: Remove existing SSML tags (they'll be re-added properly)
        text = re.sub(r'<break[^>]*/?>', ' ', text)
        
        # STEP 2: Remove markdown formatting
        text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        text = re.sub(r'^\d+\.\s*', '', text, flags=re.MULTILINE)
        
        # STEP 3: Handle problematic characters for SSML
        # Replace smart quotes with regular quotes
        text = text.replace('"', '"').replace('"', '"')
        text = text.replace(''', "'").replace(''', "'")
        
        # Replace characters that break SSML
        text = text.replace('&', 'and')
        text = text.replace('%', ' percent')
        text = text.replace('$', 'dollar ')
        text = text.replace('<', ' less than ')
        text = text.replace('>', ' greater than ')
        
        # STEP 4: Clean up URLs and section dividers
        text = re.sub(r'https?://[^\s]+', 'link', text)
        text = re.sub(r'═+', ' ', text)
        
        # STEP 5: Clean up whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        # STEP 6: HTML escape all content for SSML safety
        text = html.escape(text, quote=False)
        intro = html.escape(intro, quote=False)
        outro = html.escape(outro, quote=False)
        
        # STEP 7: Add SSML breaks with CONSISTENT double quotes
        text = re.sub(r'([.!?])\s+', r'\1 <break time="0.5s"/> ', text)
        text = re.sub(r'(TOP NEWS HEADLINES|DEEP DIVE ANALYSIS)', r'<break time="1s"/> \1 <break time="1s"/>', text)
        text = re.sub(r'(Technical Deep Dive|Financial Analysis|Market Disruption|Cultural and Social Impact|Executive Action Plan)', r'<break time="1s"/> \1 <break time="1s"/>', text)
        
        # STEP 8: Combine with consistent SSML
        full_podcast = f'{intro} <break time="2s"/> {text} <break time="2s"/> {outro}'
        
        return full_podcast        

    
    def _create_episode_description(self, podcast_content: str) -> str:
        """Create a concise episode description from podcast content"""
        try:
            # Extract first few sentences as description
            import re
            
            # Remove markdown and clean up text
            clean_content = re.sub(r'^#+\s*', '', podcast_content, flags=re.MULTILINE)
            clean_content = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean_content)
            clean_content = re.sub(r'\*([^*]+)\*', r'\1', clean_content)
            
            # Split into sentences and take first 2-3
            sentences = re.split(r'[.!?]+', clean_content)
            description_sentences = []
            char_count = 0
            
            for sentence in sentences:
                sentence = sentence.strip()
                if sentence and char_count < 200:  # Keep under 200 chars for description
                    description_sentences.append(sentence)
                    char_count += len(sentence)
                else:
                    break
            
            description = '. '.join(description_sentences[:3])
            if len(description) > 200:
                description = description[:197] + "..."

            default_desc = f"{self.podcast_title} newsletter summary with the latest developments in artificial intelligence."
            return description or default_desc

        except Exception as e:
            logger.warning(f"Error creating episode description: {str(e)}")
            return f"{self.podcast_title} newsletter summary with the latest developments in artificial intelligence."

    def _format_duration(self, seconds: float) -> str:
        """Format duration in seconds to HH:MM:SS or MM:SS format for iTunes."""
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60

        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"

    def _get_audio_duration_from_s3(self, audio_key: str) -> str:
        """Download MP3 from S3 and get its duration using mutagen.

        Args:
            audio_key: S3 key for the audio file

        Returns:
            Formatted duration string (MM:SS or HH:MM:SS), or "10:00" as fallback
        """
        if not MUTAGEN_AVAILABLE:
            logger.warning("Mutagen not available, using default duration")
            return "10:00"

        tmp_path = None
        try:
            # Create temporary file
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp_file:
                tmp_path = tmp_file.name

            # Download from S3
            logger.info(f"Downloading audio from S3 to extract duration: {audio_key}")
            self.s3_client.download_file(self.s3_bucket, audio_key, tmp_path)

            # Get duration using mutagen
            audio = MP3(tmp_path)
            duration_seconds = audio.info.length
            formatted_duration = self._format_duration(duration_seconds)

            logger.info(f"Extracted audio duration: {formatted_duration}")
            return formatted_duration

        except Exception as e:
            logger.warning(f"Could not extract audio duration from S3: {str(e)}, using default")
            return "10:00"  # Fallback to default
        finally:
            # Clean up temporary file
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _update_rss_feed(self, audio_key: str, audio_size: int, episode_description: str, episode_date: datetime, episode_title: str = None) -> None:
        """Update RSS feed with new podcast episode (based on reference.py)"""
        try:
            # Try to get existing feed
            try:
                feed_obj = self.s3_client.get_object(Bucket=self.s3_bucket, Key=self.feed_key)
                rss = fromstring(feed_obj['Body'].read())
                channel = rss.find('channel')
                logger.info("Found existing RSS feed, updating...")
            except Exception:
                # Create new feed if it doesn't exist
                logger.info("Creating new RSS feed...")
                rss = Element("rss", version="2.0", attrib={"xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"})
                channel = SubElement(rss, "channel")

                # Add channel metadata
                SubElement(channel, "title").text = self.podcast_title
                SubElement(channel, "link").text = "https://dailyaibyai.news"
                SubElement(channel, "language").text = "en-us"
                SubElement(channel, "itunes:author").text = self.podcast_title
                SubElement(channel, "description").text = (
                    "Your daily AI newsletter summary in podcast format. "
                    "Comprehensive analysis of the latest developments in artificial intelligence, "
                    "delivered by a synthetic intelligence agent."
                )
                SubElement(channel, "itunes:image", href=self.podcast_image_url)
                SubElement(channel, "itunes:explicit").text = "false"
                SubElement(channel, "itunes:category", text="Technology")
                SubElement(channel, "itunes:category", text="News")
            
            # Create new episode item
            item = SubElement(channel, "item")

            # Episode title - use generated title or fallback to date-based
            if not episode_title:
                episode_title = f"{self.podcast_title_short} Summary - {episode_date.strftime('%B %d, %Y')}"
            SubElement(item, "title").text = episode_title
            
            # Episode description
            SubElement(item, "description").text = episode_description
            
            # Audio enclosure
            audio_url = f"https://{self.s3_bucket}.s3.amazonaws.com/{audio_key}"
            SubElement(item, "enclosure", 
                      url=audio_url,
                      length=str(audio_size), 
                      type="audio/mpeg")
            
            # Unique episode GUID
            guid_prefix = 'staging-daily-ai' if self.environment == 'staging' else 'daily-ai'
            episode_guid = f"{guid_prefix}-{episode_date.strftime('%Y%m%d')}"
            SubElement(item, "guid").text = episode_guid
            
            # Publication date in RFC 2822 format
            pubdate = episode_date.strftime("%a, %d %b %Y %H:%M:%S +0000")
            SubElement(item, "pubDate").text = pubdate

            # Calculate real duration from audio file on S3
            duration = self._get_audio_duration_from_s3(audio_key)
            SubElement(item, "itunes:duration").text = duration
            
            # Convert to pretty XML
            rss_bytes = tostring(rss, encoding="utf-8", xml_declaration=True)
            pretty_xml = minidom.parseString(rss_bytes).toprettyxml(encoding="utf-8")
            
            # Upload updated feed to S3
            self.s3_client.put_object(
                Bucket=self.s3_bucket, 
                Key=self.feed_key, 
                Body=pretty_xml, 
                ContentType='application/rss+xml'
            )
            
            logger.info(f"RSS feed updated successfully with episode: {episode_title}")
            
        except Exception as e:
            logger.error(f"Error updating RSS feed: {str(e)}")
            # Don't raise - RSS feed update failure shouldn't stop the main process
    
    def _cleanup_messages(self, processed_messages: List[Dict[str, Any]]) -> None:
        """Delete processed messages from SQS queue"""
        try:
            for i in range(0, len(processed_messages), 10):
                batch = processed_messages[i:i+10]
                
                delete_entries = []
                for idx, message in enumerate(batch):
                    delete_entries.append({
                        'Id': str(idx),
                        'ReceiptHandle': message['ReceiptHandle']
                    })
                
                if delete_entries:
                    self.sqs_client.delete_message_batch(
                        QueueUrl=self.queue_url,
                        Entries=delete_entries
                    )
            
            logger.info(f"Deleted {len(processed_messages)} messages from queue")
            
        except Exception as e:
            logger.error(f"Error cleaning up messages: {str(e)}")
    
    def send_summary_email(self, result_data: Dict[str, Any]) -> None:
        """Send summary email via SNS"""
        try:
            status = result_data['status']
            total_emails = result_data.get('total_emails', 0)
            strategy = result_data.get('processing_strategy', 'Unknown')

            subject = f"{self.notification_prefix}[AI Newsletter Summary] {status} - {total_emails} emails ({strategy})"
            logger.info(f"Constructed email subject: {subject}")
            logger.info(f"Notification prefix: '{self.notification_prefix}' | Environment: {self.environment}")

            if result_data['status'] == '✅ Success':
                message_body = self._create_success_email(result_data)
            elif result_data['status'] == '📭 No emails found':
                message_body = self._create_no_emails_notification(result_data)
            elif result_data['status'] == '❌ Failure':
                message_body = self._create_error_email(result_data)
            else:
                # Fallback for any unexpected status
                message_body = self._create_error_email(result_data)

            logger.info(f"About to publish SNS notification - Topic: {self.sns_topic_arn}")
            logger.info(f"SNS Subject parameter: '{subject}'")
            logger.info(f"Subject length: {len(subject)} characters")

            response = self.sns_client.publish(
                TopicArn=self.sns_topic_arn,
                Subject=subject,
                Message=message_body
            )

            logger.info(f"SNS MessageId: {response.get('MessageId', 'N/A')}")
            logger.info("Summary email sent successfully")

        except Exception as e:
            logger.error(f"Error sending summary email: {str(e)}")
            raise
    
    def _create_success_email(self, result_data: Dict[str, Any]) -> str:
        """Create success email message with podcast format only"""
        podcast_content = result_data.get('podcast_content', '')
        podcast_headlines = result_data.get('podcast_headlines', [])
        podcast_deep_dive = result_data.get('podcast_deep_dive', '')
        episode_title = result_data.get('episode_title', '')
        audio_generated = result_data.get('audio_generated', False)
        audio_url = result_data.get('audio_url', '')
        audio_size = result_data.get('audio_size', 0)

        env_badge = "🧪 [STAGING TEST] " if self.environment == 'staging' else ""
        message = f"""{env_badge}🎙️ AI Podcast Summary - {datetime.now().strftime('%B %d, %Y')}

📊 Processed: {result_data['total_emails']} newsletters
🔧 Strategy: {result_data.get('processing_strategy', 'Unknown')}"""

        # Add episode title if available
        if episode_title:
            message += f"""
🎯 Episode Title: {episode_title}"""

        # Add audio information if available
        if audio_generated and audio_url:
            audio_size_mb = round(audio_size / (1024 * 1024), 1) if audio_size > 0 else 0
            message += f"""
🎧 Podcast Generated: {audio_size_mb}MB MP3
🔗 Audio Link: {audio_url}
"""

        # Add podcast format section
        message += f"""

═══════════════════════════════════════
🎙️ PODCAST SCRIPT
═══════════════════════════════════════

"""

        if podcast_headlines:
            message += "📰 TOP NEWS HEADLINES:\n"
            for i, headline in enumerate(podcast_headlines, 1):
                message += f"{i}. {headline}\n"
            message += "\n"

        if podcast_deep_dive:
            message += "🔍 DEEP DIVE ANALYSIS:\n"
            message += f"{podcast_deep_dive}\n\n"
        elif podcast_content:
            message += f"{podcast_content}\n\n"

        message += f"""═══════════════════════════════════════
⏰ Generated: {result_data['processed_at']}
🚀 Ready for your day ahead!
"""

        return message
    
    def _create_error_email(self, result_data: Dict[str, Any]) -> str:
        """Create error email message"""
        error = result_data.get('error', 'Unknown error')
        processed_at = result_data['processed_at']
        
        return f"""❌ AI Newsletter Processing Failed

🔴 Error: {error}

⏰ Time: {processed_at}

Please check the Lambda logs for more details.

This might be due to:
- API rate limits
- Network connectivity issues  
- Malformed email content
- Claude API issues

The system will retry on the next scheduled run.
"""

    def _create_no_emails_notification(self, result_data: Dict[str, Any]) -> str:
        """Create informational email for when no emails are found"""
        processed_at = result_data['processed_at']
        env_badge = "🧪 [STAGING TEST] " if self.environment == 'staging' else ""

        return f"""{env_badge}📭 No AI Newsletters Found

✅ System Status: Healthy
⏰ Checked at: {processed_at}

The AI newsletter processing system checked the queue and found no new emails to process.

This is normal and means:
- ✓ The system is running correctly
- ✓ No new newsletters have arrived since the last check
- ✓ All previous emails have been processed

Next scheduled check: {self._get_next_run_time() if self.environment == 'production' else 'Manual invocation only (staging)'}

No action required.
"""

    def _get_next_run_time(self) -> str:
        """Calculate next scheduled run time"""
        from datetime import datetime, timedelta
        # Scheduled daily at 10:00 UTC
        now = datetime.utcnow()
        next_run = now.replace(hour=10, minute=0, second=0, microsecond=0)
        if now.hour >= 10:
            next_run += timedelta(days=1)
        return next_run.strftime('%Y-%m-%d %H:%M UTC')

def lambda_handler(event, context):
    """AWS Lambda handler function"""
    logger.info("Starting advanced AI newsletter processing with Claude")
    
    result_message = {}
    
    # Check if running in test mode from event or environment
    test_mode_from_event = str(event.get('test', 'false')).lower() == 'true'
    test_mode_from_env = os.environ.get('TEST_MODE', 'false').lower() == 'true'
    test_mode = test_mode_from_event or test_mode_from_env
    
    # Check RSS creation setting from event
    create_rss_from_event = str(event.get('create_rss', 'true')).lower() == 'true'
    
    if test_mode:
        if test_mode_from_event:
            logger.info("⚠️  RUNNING IN TEST MODE (from event JSON) - Messages will NOT be deleted from queue")
        else:
            logger.info("⚠️  RUNNING IN TEST MODE (from environment) - Messages will NOT be deleted from queue")
    
    if not create_rss_from_event:
        logger.info("⚠️  RSS CREATION DISABLED (from event JSON)")
    
    try:
        processor = ClaudeNewsletterProcessor(test_mode=test_mode, create_rss=create_rss_from_event)
        result_message = processor.process_newsletter_queue()
        
        logger.info(f"Processing completed: {result_message['status']}")
        
        return {
            'statusCode': 200,
            'body': json.dumps(result_message, indent=2)
        }
        
    except Exception as e:
        logger.error(f"Lambda execution failed: {str(e)}")
        result_message = {
            'status': '❌ Failure',
            'error': str(e),
            'processed_at': datetime.now().isoformat()
        }
        
        return {
            'statusCode': 500,
            'body': json.dumps(result_message, indent=2)
        }
    
    finally:
        try:
            if result_message:
                # Create a new processor instance for sending email (test mode doesn't matter for email sending)
                email_processor = ClaudeNewsletterProcessor(create_rss=create_rss_from_event)
                email_processor.send_summary_email(result_message)
        except Exception as e:
            logger.error(f"Failed to send summary email: {str(e)}")

# Required dependencies for Lambda deployment
"""
Create requirements.txt with:

boto3==1.34.0
requests==2.31.0
beautifulsoup4==4.12.2
tiktoken==0.5.2
lxml==4.9.3
"""

# Environment variables needed:
"""
EMAIL_QUEUE_URL=https://sqs.eu-central-1.amazonaws.com/794416789749/ai-newsletter-emails
SNS_TOPIC_ARN=arn:aws:sns:eu-central-1:794416789749:ai-newsletter-notifications
CLAUDE_API_KEY=your-claude-api-key-here
MAX_MESSAGES=50
MAX_LINKS_PER_EMAIL=5

# DynamoDB variables:
DYNAMODB_TABLE_NAME=ai_daily_news

# New Polly/Audio variables:
PODCAST_S3_BUCKET=ai-newsletter-podcasts
POLLY_VOICE=Joanna
POLLY_RATE=medium
GENERATE_AUDIO=true
UPDATE_RSS_FEED=true

# RSS Feed variables:
PODCAST_IMAGE_URL=https://ai-newsletter-podcasts.s3.amazonaws.com/podcast.jpg

# Test mode:
TEST_MODE=false
"""
