import json
import boto3
import os
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from xml.etree.ElementTree import fromstring, Element, SubElement, tostring, register_namespace
from xml.dom import minidom

# Import optional dependencies with fallbacks
try:
    import requests
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

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Register XML namespace for iTunes podcast tags
register_namespace('itunes', "http://www.itunes.com/dtds/podcast-1.0.dtd")

class WeeklyAnalysisProcessor:
    def __init__(self, test_mode: bool = False):
        self.dynamodb = boto3.resource('dynamodb')
        self.polly_client = boto3.client('polly')
        self.s3_client = boto3.client('s3')
        
        # Environment variables
        self.claude_api_key = os.environ.get('CLAUDE_API_KEY')
        self.dynamodb_table_name = os.environ.get('DYNAMODB_TABLE_NAME', 'ai_daily_news')
        
        # Claude API rate limiting configuration
        self.claude_requests_per_minute = int(os.environ.get('CLAUDE_RPM_LIMIT', '5'))
        self.claude_max_retries = int(os.environ.get('CLAUDE_MAX_RETRIES', '6'))
        self.claude_base_delay = int(os.environ.get('CLAUDE_BASE_DELAY', '10'))
        self.min_request_interval = 60 / self.claude_requests_per_minute
        self.last_request_time = 0

        # Polly and S3 configuration
        self.s3_bucket = os.environ.get('PODCAST_S3_BUCKET', 'ai-newsletter-podcasts')
        self.polly_voice = os.environ.get('POLLY_VOICE', 'Joanna')
        self.polly_rate = os.environ.get('POLLY_RATE', 'medium')
        self.generate_audio = os.environ.get('GENERATE_AUDIO', 'true').lower() == 'true'
        
        # RSS Feed configuration
        self.feed_key = 'feed.xml'
        self.podcast_image_url = os.environ.get('PODCAST_IMAGE_URL', f'https://{self.s3_bucket}.s3.amazonaws.com/podcast.png')
        
        # Test mode configuration
        self.test_mode = test_mode or os.environ.get('TEST_MODE', 'false').lower() == 'true'
        if self.test_mode:
            logger.info("⚠️  Weekly Analysis Processor initialized in TEST MODE")
        
        # Claude API configuration
        self.claude_model = "claude-sonnet-4-20250514"
        self.claude_api_url = "https://api.anthropic.com/v1/messages"
        
        # Validate required environment variables
        if not self.claude_api_key:
            raise ValueError("CLAUDE_API_KEY environment variable is required")
        
        # Initialize prompts
        self._init_weekly_prompts()
    
    def _init_weekly_prompts(self):
        """Initialize weekly analysis prompt templates"""
        
        self.WEEKLY_ANALYSIS_PROMPT = """
You are a Strategic Technology Analyst creating a HIGH-LEVEL WEEKLY ANALYSIS of AI ecosystem evolution. Rather than summarizing news, your goal is to synthesize patterns, identify meta-trends, and provide strategic intelligence.

You have {num_days} days of AI developments from this past week:

{weekly_content}

Your task is to ANALYZE and SYNTHESIZE, not summarize. Create a strategic intelligence report with:

1. **Strategic Synthesis**: What are the 2-3 macro-level shifts happening in AI this week? Connect seemingly unrelated developments to reveal larger patterns.

2. **Power Dynamics**: How are competitive positions shifting? Which players are gaining/losing strategic advantage and why?

3. **Inflection Points**: Identify developments that signal fundamental changes in AI trajectory - not just incremental progress but paradigm shifts.

4. **Cross-Domain Impact Analysis**: How are advances in one AI domain (LLMs, robotics, computer vision) creating ripple effects across other domains and industries?

5. **Strategic Implications**: What do these combined developments mean for:
   - Technology strategy decisions
   - Investment priorities  
   - Competitive positioning
   - Long-term market evolution

6. **Forward Intelligence**: Based on this week's developments, what strategic scenarios should leaders prepare for in the next 3-6 months?

Focus on WHY these developments matter together, not WHAT happened. Provide strategic intelligence that helps executives understand the evolving AI landscape at a systems level.
"""

        self.WEEKLY_PODCAST_PROMPT = """
You are a strategic technology analyst hosting a WEEKLY AI INTELLIGENCE podcast for executives and strategic decision-makers. Your role is to provide high-level analysis, not news recap.

IMPORTANT: Do NOT include any introduction, welcome, or opening statements. Do NOT include any closing statements or sign-offs. Jump directly into analytical content.

This week's AI developments:

{weekly_content}

Create a strategic analysis podcast script with these sections:

## STRATEGIC PATTERN ANALYSIS
Identify the 3-4 most strategically significant developments from this week. For each, explain:
- Why this development is strategically important (beyond the obvious)
- How it connects to other developments this week
- What it signals about broader AI evolution

## CONVERGENCE ANALYSIS  
Take these 3-4 developments and analyze them as a combined force:

1. **Systems Thinking**: How do these developments interact and reinforce each other? What emergent patterns do they create?

2. **Competitive Landscape Shifts**: How do these combined developments alter the strategic playing field? Who wins/loses from these trends?

3. **Market Evolution**: What new market opportunities or threats emerge when you view these developments as interconnected rather than isolated?

4. **Technology Convergence**: Where are we seeing unexpected intersections between different AI capabilities or domains?

5. **Strategic Scenario Planning**: Given these combined developments, what are 2-3 plausible scenarios executives should prepare for?

Write in analytical, strategic language for senior technology leaders. Focus on synthesis, implications, and strategic intelligence rather than news reporting. Your audience wants to understand the strategic significance of the AI landscape evolution.
"""

    def _is_saturday(self) -> bool:
        """Check if current day is Saturday"""
        return datetime.now().weekday() == 5  # Saturday is 5 (Monday=0)

    def _should_run_weekly_analysis(self) -> bool:
        """Determine if weekly analysis should run"""
        return True

    def process_weekly_analysis(self) -> Dict[str, Any]:
        """Process weekly analysis from stored summaries"""
        try:
            logger.info("Starting Saturday weekly analysis")
            
            # Get last week's summaries
            weekly_summaries = self._get_last_week_summaries()
            
            if not weekly_summaries:
                return {
                    'status': '📭 No weekly data',
                    'total_days': 0,
                    'summary': 'No summaries found for the past week.',
                    'processed_at': datetime.now().isoformat()
                }
            
            # Generate weekly analysis
            weekly_analysis = self._generate_weekly_analysis(weekly_summaries)
            
            # Generate weekly podcast
            weekly_podcast = self._generate_weekly_podcast(weekly_summaries)
            
            # Generate audio if enabled
            audio_info = None
            if weekly_podcast.get('content'):
                audio_info = self._generate_podcast_audio(
                    weekly_podcast['content'], 
                    datetime.now().isoformat()
                )
            
            result = {
                'status': '✅ Weekly Analysis Success',
                'total_days': len(weekly_summaries),
                'processing_strategy': 'Saturday Weekly Analysis',
                'summary': weekly_analysis['content'],
                'key_insights': weekly_analysis['insights'],
                'weekly_trends': weekly_analysis.get('trends', []),
                'podcast_content': weekly_podcast.get('content', ''),
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
            logger.error(f"Error in weekly analysis: {str(e)}")
            return {
                'status': '❌ Weekly Analysis Failure',
                'error': str(e),
                'processed_at': datetime.now().isoformat()
            }

    def _get_last_week_summaries(self) -> List[Dict[str, Any]]:
        """Retrieve summaries from the last 7 days from DynamoDB"""
        try:
            table = self.dynamodb.Table(self.dynamodb_table_name)
            
            # Calculate date range for last week
            end_date = datetime.now()
            start_date = end_date - timedelta(days=7)
            
            summaries = []
            current_date = start_date
            
            while current_date <= end_date:
                date_str = current_date.strftime('%Y-%m-%d')
                try:
                    response = table.get_item(
                        Key={'date': date_str}
                    )
                    if 'Item' in response:
                        summaries.append({
                            'date': date_str,
                            'text': response['Item']['text'],
                            'day_of_week': current_date.strftime('%A')
                        })
                        logger.info(f"Retrieved summary for {date_str}")
                except Exception as e:
                    logger.warning(f"No summary found for {date_str}: {str(e)}")
                
                current_date += timedelta(days=1)
            
            logger.info(f"Retrieved {len(summaries)} summaries for weekly analysis")
            return summaries
            
        except Exception as e:
            logger.error(f"Error retrieving weekly summaries: {str(e)}")
            return []

    def _generate_weekly_analysis(self, summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate comprehensive weekly analysis"""
        try:
            # Prepare weekly content for Claude
            weekly_content = self._prepare_weekly_content(summaries)
            
            # Create weekly analysis prompt
            prompt = self._create_weekly_analysis_prompt(weekly_content, len(summaries))
            
            # Call Claude API
            response = self._call_claude_api(prompt)
            
            # Parse response
            parsed_response = self._parse_weekly_analysis_response(response)
            
            return {
                'content': parsed_response['summary'],
                'insights': parsed_response['insights'],
                'trends': parsed_response.get('trends', [])
            }
            
        except Exception as e:
            logger.error(f"Error generating weekly analysis: {str(e)}")
            raise

    def _generate_weekly_podcast(self, summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate weekly podcast script"""
        try:
            # Prepare weekly content for podcast
            weekly_content = self._prepare_weekly_content(summaries)
            
            # Create weekly podcast prompt
            prompt = self._create_weekly_podcast_prompt(weekly_content, len(summaries))
            
            # Call Claude API
            response = self._call_claude_api(prompt)
            
            return {
                'content': response
            }
            
        except Exception as e:
            logger.error(f"Error generating weekly podcast: {str(e)}")
            raise

    def _prepare_weekly_content(self, summaries: List[Dict[str, Any]]) -> str:
        """Prepare weekly summaries for analysis"""
        content_parts = []
        
        for summary in summaries:
            day_content = f"""
### {summary['day_of_week']}, {summary['date']}
{summary['text']}

"""
            content_parts.append(day_content)
        
        return '\n'.join(content_parts)

    def _create_weekly_analysis_prompt(self, weekly_content: str, num_days: int) -> str:
        """Create weekly analysis prompt"""
        return self.WEEKLY_ANALYSIS_PROMPT.format(
            num_days=num_days,
            weekly_content=weekly_content
        )

    def _create_weekly_podcast_prompt(self, weekly_content: str, num_days: int) -> str:
        """Create weekly podcast prompt"""
        return self.WEEKLY_PODCAST_PROMPT.format(
            num_days=num_days,
            weekly_content=weekly_content
        )

    def _parse_weekly_analysis_response(self, response: str) -> Dict[str, Any]:
        """Parse weekly analysis response"""
        try:
            lines = response.split('\n')
            
            summary_sections = []
            insights = []
            trends = []
            
            current_section = "summary"
            current_content = []
            
            for line in lines:
                line = line.strip()
                
                if any(keyword in line.lower() for keyword in ['power dynamics', 'strategic synthesis']):
                    if current_content:
                        summary_sections.append('\n'.join(current_content))
                    current_content = [line]
                    current_section = "summary"
                elif any(keyword in line.lower() for keyword in ['inflection points', 'cross-domain impact']):
                    if current_content:
                        if current_section == "summary":
                            summary_sections.append('\n'.join(current_content))
                        else:
                            trends.append('\n'.join(current_content))
                    current_content = [line]
                    current_section = "trends"
                elif any(keyword in line.lower() for keyword in ['strategic implications', 'forward intelligence']):
                    if current_content:
                        if current_section == "trends":
                            trends.append('\n'.join(current_content))
                        else:
                            summary_sections.append('\n'.join(current_content))
                    current_content = [line]
                    current_section = "insights"
                else:
                    current_content.append(line)
            
            # Add final content
            if current_content:
                if current_section == "trends":
                    trends.append('\n'.join(current_content))
                elif current_section == "insights":
                    insights.append('\n'.join(current_content))
                else:
                    summary_sections.append('\n'.join(current_content))
            
            return {
                'summary': '\n\n'.join(summary_sections) if summary_sections else response,
                'insights': insights[:5],
                'trends': trends[:3]
            }
            
        except Exception as e:
            logger.warning(f"Error parsing weekly analysis response: {str(e)}")
            return {
                'summary': response,
                'insights': [],
                'trends': []
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

    def _call_claude_api(self, prompt: str, retry_count: int = 0) -> str:
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
                'model': self.claude_model,
                'max_tokens': 4000,
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
                timeout=60
            )
            
            if response.status_code == 429:
                if retry_count < self.claude_max_retries:
                    # Exponential backoff: base_delay * 2^retry_count
                    delay = self.claude_base_delay * (2 ** retry_count)
                    logger.warning(f"Rate limited (429). Retrying in {delay}s (attempt {retry_count + 1}/{self.claude_max_retries})")
                    time.sleep(delay)
                    return self._call_claude_api(prompt, retry_count + 1)
                else:
                    raise Exception(f"Max retries ({self.claude_max_retries}) exceeded for rate limiting")
            
            response.raise_for_status()
            result = response.json()
            
            return result['content'][0]['text']
            
        except Exception as e:
            logger.error(f"Error calling Claude API: {str(e)}")
            raise

    def save_podcast_to_dynamodb(self, text: str) -> bool:
        """Save podcast text to DynamoDB with current date"""
        try:
            table = self.dynamodb.Table(self.dynamodb_table_name)
            current_date = datetime.now().strftime('%Y-%m-%d')
            
            table.put_item(
                Item={
                    'date': current_date,
                    'text': text
                }
            )
            
            logger.info(f"Successfully saved weekly podcast text to DynamoDB table: {self.dynamodb_table_name}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving weekly podcast to DynamoDB: {str(e)}")
            return False

    def _chunk_text_for_polly(self, text: str, max_length: int = 2800) -> List[str]:
        """Chunk text for Polly synthesis"""
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
        """Convert text to speech using AWS Polly"""
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
            # Create filename with date and weekly indicator
            audio_key = f"podcasts/weekly-ai-analysis-{date_str}.mp3"
            
            # Upload to S3
            self.s3_client.put_object(
                Bucket=self.s3_bucket,
                Key=audio_key,
                Body=audio_data,
                ContentType='audio/mpeg',
                Metadata={
                    'generated_at': datetime.now().isoformat(),
                    'content_type': 'weekly_ai_analysis_podcast'
                }
            )
            
            # Generate presigned URL (expires in 7 days)
            presigned_url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.s3_bucket, 'Key': audio_key},
                ExpiresIn=604800  # 7 days
            )
            
            logger.info(f"Weekly analysis audio uploaded to S3: {audio_key}")
            return audio_key, presigned_url
            
        except Exception as e:
            logger.error(f"Error uploading weekly audio to S3: {str(e)}")
            raise
    
    def _generate_podcast_audio(self, podcast_content: str, processed_date: str) -> Optional[Dict[str, str]]:
        """Generate MP3 audio from weekly podcast content"""
        if not self.generate_audio:
            logger.info("Audio generation disabled")
            return None
            
        try:
            logger.info("Starting weekly podcast audio generation")
            
            # Clean up the text for speech synthesis
            clean_text = self._prepare_text_for_speech(podcast_content)
            
            if not clean_text.strip():
                logger.warning("No content available for weekly audio generation")
                return None
            
            # Save cleaned podcast text to DynamoDB
            self.save_podcast_to_dynamodb(clean_text)
            
            # Convert to speech
            audio_data = self._convert_text_to_speech(clean_text)
            
            if not audio_data:
                logger.warning("No weekly audio data generated")
                return None
            
            # Upload to S3
            date_str = datetime.fromisoformat(processed_date.replace('Z', '+00:00')).strftime('%Y-%m-%d')
            audio_key, presigned_url = self._upload_audio_to_s3(audio_data, date_str)
            
            # Update RSS feed
            episode_date = datetime.fromisoformat(processed_date.replace('Z', '+00:00'))
            episode_description = self._create_episode_description(podcast_content)
            self._update_rss_feed(audio_key, len(audio_data), episode_description, episode_date)
            
            return {
                'audio_key': audio_key,
                'audio_url': presigned_url,
                'audio_size': len(audio_data)
            }
            
        except Exception as e:
            logger.error(f"Error generating weekly podcast audio: {str(e)}")
            return None
    
    def _prepare_text_for_speech(self, text: str) -> str:
        """Prepare text for speech synthesis by cleaning markdown and formatting"""
        import re
        from datetime import datetime
        
        # Get current date information for weekly podcast intro
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
            host_name = "Joanna, a synthetic intelligence analyst"
        elif voice_name.lower() == 'matthew':
            host_name = "Matthew, a synthetic intelligence analyst"
        elif voice_name.lower() == 'amy':
            host_name = "Amy, a synthetic intelligence analyst"
        elif voice_name.lower() == 'brian':
            host_name = "Brian, a synthetic intelligence analyst"
        else:
            host_name = f"{voice_name}, a synthetic intelligence analyst"
        
        # Create weekly podcast introduction
        intro = f"Welcome to Weekly AI Intelligence, your strategic analysis of artificial intelligence ecosystem evolution. I'm {host_name}, bringing you this week's most significant developments analyzed through a strategic lens. Today is {day_name}, {date_formatted}."
        
        # Create podcast closing
        outro = f"That concludes this week's AI Intelligence analysis. I'm {host_name}. These strategic insights will help guide your decision-making in the evolving AI landscape. Until next week, stay strategically informed."
        
        # Clean up the main content
        # Remove markdown headers
        text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
        
        # Remove markdown bold/italic
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        
        # Remove numbered lists format (1. 2. etc) and replace with natural speech
        text = re.sub(r'^\d+\.\s*', '', text, flags=re.MULTILINE)
        
        # Replace section dividers with pauses
        text = re.sub(r'═+', '<break time="2s"/>', text)
        
        # Clean up URLs (make them more speech-friendly)
        text = re.sub(r'https?://[^\s]+', 'link', text)
        
        # Add natural pauses after sentences
        text = re.sub(r'([.!?])\s+', r'\1 <break time="0.5s"/> ', text)
        
        # Add pause between sections
        text = re.sub(r'\n\n+', ' <break time="1s"/> ', text)
        
        # Clean up extra whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Combine intro + content + outro with pauses
        full_podcast = f"{intro} <break time='2s'/> {text} <break time='2s'/> {outro}"
        
        return full_podcast
    
    def _create_episode_description(self, podcast_content: str) -> str:
        """Create a concise episode description from weekly podcast content"""
        try:
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
                if sentence and char_count < 200:
                    description_sentences.append(sentence)
                    char_count += len(sentence)
                else:
                    break
            
            description = '. '.join(description_sentences[:3])
            if len(description) > 200:
                description = description[:197] + "..."
                
            return description or "Weekly AI strategic intelligence analysis covering ecosystem evolution and strategic implications."
            
        except Exception as e:
            logger.warning(f"Error creating weekly episode description: {str(e)}")
            return "Weekly AI strategic intelligence analysis covering ecosystem evolution and strategic implications."
    
    def _update_rss_feed(self, audio_key: str, audio_size: int, episode_description: str, episode_date: datetime) -> None:
        """Update RSS feed with new weekly podcast episode"""
        try:
            # Try to get existing feed
            try:
                feed_obj = self.s3_client.get_object(Bucket=self.s3_bucket, Key=self.feed_key)
                rss = fromstring(feed_obj['Body'].read())
                channel = rss.find('channel')
                logger.info("Found existing RSS feed, updating with weekly episode...")
            except Exception:
                # Create new feed if it doesn't exist
                logger.info("Creating new RSS feed for weekly episodes...")
                rss = Element("rss", version="2.0", attrib={"xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"})
                channel = SubElement(rss, "channel")
                
                # Add channel metadata
                SubElement(channel, "title").text = "Weekly AI Intelligence"
                SubElement(channel, "link").text = f"https://{self.s3_bucket}.s3.amazonaws.com/{self.feed_key}"
                SubElement(channel, "language").text = "en-us"
                SubElement(channel, "itunes:author").text = "Weekly AI Intelligence"
                SubElement(channel, "description").text = (
                    "Strategic intelligence analysis of artificial intelligence ecosystem evolution. "
                    "Weekly synthesis of AI developments through a strategic lens for technology leaders."
                )
                SubElement(channel, "itunes:image", href=self.podcast_image_url)
                SubElement(channel, "itunes:explicit").text = "false"
                SubElement(channel, "itunes:category", text="Technology")
                SubElement(channel, "itunes:category", text="Business")
            
            # Create new episode item
            item = SubElement(channel, "item")
            
            # Episode title with date
            episode_title = f"Weekly AI Intelligence - {episode_date.strftime('%B %d, %Y')}"
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
            episode_guid = f"weekly-ai-intelligence-{episode_date.strftime('%Y%m%d')}"
            SubElement(item, "guid").text = episode_guid
            
            # Publication date in RFC 2822 format
            pubdate = episode_date.strftime("%a, %d %b %Y %H:%M:%S +0000")
            SubElement(item, "pubDate").text = pubdate
            
            # Estimated duration (longer for weekly analysis)
            SubElement(item, "itunes:duration").text = "15:00"  # Default 15 minutes for weekly
            
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
            
            logger.info(f"RSS feed updated successfully with weekly episode: {episode_title}")
            
        except Exception as e:
            logger.error(f"Error updating RSS feed with weekly episode: {str(e)}")
            # Don't raise - RSS feed update failure shouldn't stop the main process


def lambda_handler_weekly(event, context):
    """AWS Lambda handler function for weekly analysis"""
    logger.info("Starting weekly AI analysis processor")
    
    result_message = {}
    
    # Check if running in test mode from event or environment
    test_mode_from_event = str(event.get('test', 'false')).lower() == 'true'
    test_mode_from_env = os.environ.get('TEST_MODE', 'false').lower() == 'true'
    test_mode = test_mode_from_event or test_mode_from_env
    
    if test_mode:
        logger.info("⚠️  RUNNING WEEKLY ANALYSIS IN TEST MODE")
    
    try:
        processor = WeeklyAnalysisProcessor(test_mode=test_mode)
        
        # Check if it's Saturday or if forced via event
        force_run = str(event.get('force_weekly', 'false')).lower() == 'true'
        
        if not processor._is_saturday() and not force_run:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': '📅 Not Saturday',
                    'message': 'Weekly analysis only runs on Saturday. Use force_weekly=true to override.',
                    'processed_at': datetime.now().isoformat()
                }, indent=2)
            }
        
        if force_run:
            logger.info("🔥 FORCED WEEKLY ANALYSIS RUN (not Saturday)")
        
        result_message = processor.process_weekly_analysis()
        
        logger.info(f"Weekly analysis completed: {result_message['status']}")
        
        return {
            'statusCode': 200,
            'body': json.dumps(result_message, indent=2)
        }
        
    except Exception as e:
        logger.error(f"Weekly Lambda execution failed: {str(e)}")
        result_message = {
            'status': '❌ Weekly Analysis Failure',
            'error': str(e),
            'processed_at': datetime.now().isoformat()
        }
        
        return {
            'statusCode': 500,
            'body': json.dumps(result_message, indent=2)
        }