# AI Newsletter Lambda - Product Specification

## 1. Product Overview

### 1.1 Purpose
The AI Newsletter Lambda is an automated AWS serverless application that processes AI-focused email newsletters from an SQS queue, enhances them with web content, generates comprehensive summaries using Claude AI, and delivers them via SNS notifications and podcast format.

### 1.2 Business Value
- **Time Efficiency**: Automatically processes and summarizes multiple AI newsletters daily
- **Content Enhancement**: Enriches newsletters with linked article content for comprehensive analysis
- **Multi-Format Output**: Provides both executive report and podcast script formats
- **Audio Generation**: Creates MP3 podcasts with RSS feed for consumption on-the-go
- **Scalability**: Handles varying newsletter volumes with intelligent batch processing

### 1.3 Target Users
- AI industry executives and professionals
- Technology leaders requiring daily AI intelligence
- Podcast listeners interested in AI developments
- Organizations tracking AI market trends

## 2. Core Features

### 2.1 Email Processing Pipeline
- **SQS Integration**: Retrieves email messages from AWS SQS queue
- **Email Parsing**: Extracts content from SES-formatted email messages
- **Newsletter Classification**: Identifies newsletter sources (TLDR AI, Ben's Bites, AI Secret, etc.)
- **Content Extraction**: Supports both plain text and HTML email formats

### 2.2 Web Content Enhancement
- **Link Extraction**: Automatically identifies and extracts HTTP/HTTPS links from newsletters
- **Content Fetching**: Retrieves and parses content from up to 5 links per newsletter
- **Content Filtering**: Excludes tracking, unsubscribe, and non-relevant links
- **Content Limiting**: Restricts web content to 3000 characters per article for optimal processing

### 2.3 Intelligent Processing Strategy
- **Hybrid Processing**: Automatically selects processing strategy based on content volume
- **Single Context Processing**: Processes all content in one API call when under token limits (≤800k tokens)
- **Batch Processing**: Splits large content into smart batches with meta-summary generation
- **Token Estimation**: Uses tiktoken for accurate token counting with character-based fallback

### 2.4 Claude AI Integration
- **Model Configuration**: Uses Claude Sonnet 4 (claude-sonnet-4-20250514) for high-quality analysis
- **Rate Limiting**: Enforces 5 requests per minute with exponential backoff retry logic
- **Dual Format Generation**: Produces both executive reports and podcast scripts
- **Error Handling**: Comprehensive retry logic with up to 6 retry attempts

### 2.5 Output Formats

#### Executive Report Format
- **Executive Summary**: 2-3 paragraph overview of key developments
- **Key Themes**: 3-5 major trends across all newsletters
- **Breaking News**: Significant announcements and funding rounds
- **Technical Insights**: Research breakthroughs and new capabilities
- **Market Impact**: Business implications and competitive changes
- **Notable Links**: Top 3-5 most important links with descriptions
- **Tomorrow's Focus**: Forward-looking insights

#### Podcast Script Format
- **Top News Headlines**: 5-6 concise news items in conversational format
- **Deep Dive Analysis**: Comprehensive analysis of the most important story covering:
  - Technical deep dive with accessible explanations
  - Financial analysis including funding and business impacts
  - Market disruption and competitive positioning
  - Cultural and social implications
  - Executive action plan with specific recommendations

### 2.6 Audio Generation & Distribution
- **Text-to-Speech**: AWS Polly integration with configurable voice (default: Joanna)
- **SSML Processing**: Advanced speech synthesis markup for natural delivery
- **Audio Chunking**: Splits large text into optimal chunks for Polly processing
- **S3 Storage**: Automatically uploads MP3 files to configured S3 bucket
- **RSS Feed**: Maintains podcast RSS feed for subscription-based consumption
- **DynamoDB Logging**: Stores podcast text content with date indexing

### 2.7 Notification & Distribution
- **SNS Integration**: Sends comprehensive summaries via AWS SNS
- **Email Formatting**: Structured email format with both executive and podcast content
- **Status Reporting**: Detailed processing status and error reporting
- **Presigned URLs**: 7-day expiration links for audio files

## 3. Technical Architecture

### 3.1 AWS Services Integration
- **AWS Lambda**: Serverless compute platform
- **Amazon SQS**: Message queuing for email processing
- **Amazon SNS**: Notification distribution
- **Amazon S3**: Audio file and RSS feed storage
- **AWS Polly**: Text-to-speech conversion
- **Amazon DynamoDB**: Podcast content storage

### 3.2 External Dependencies
- **Claude API**: Anthropic's AI service for content analysis
- **HTTP Requests**: Web content fetching via requests library
- **HTML Parsing**: BeautifulSoup4 for content extraction
- **Token Counting**: tiktoken for accurate token estimation

### 3.3 Processing Limits & Configurations
- **Maximum Messages**: 50 emails per execution (configurable)
- **Links per Email**: 5 links maximum for content enhancement
- **Token Limits**: 800,000 tokens per batch for context management
- **Content Limits**: 3000 chars for web content, 5000 chars for email content in batch mode
- **API Timeout**: 60 seconds for Claude API calls

### 3.4 Error Handling & Resilience
- **Rate Limiting**: Built-in Claude API rate limiting with exponential backoff
- **Retry Logic**: Up to 6 retry attempts with increasing delays
- **Graceful Degradation**: Continues processing when individual components fail
- **Test Mode**: Non-destructive testing mode that preserves queue messages

## 4. Supported Newsletter Sources

The system recognizes and processes newsletters from:
- TLDR AI
- Ben's Bites
- AI Secret
- AI Israel Weekly
- Aftershoot AI
- The Rundown AI
- AI Breakfast
- Import AI
- Generic "Other AI Newsletter" classification for unrecognized sources

## 5. Configuration & Environment Variables

### 5.1 Required Configuration
- `EMAIL_QUEUE_URL`: SQS queue URL for incoming emails
- `SNS_TOPIC_ARN`: SNS topic for summary distribution
- `CLAUDE_API_KEY`: Anthropic Claude API key

### 5.2 Processing Configuration
- `MAX_MESSAGES`: Maximum messages per execution (default: 50)
- `MAX_LINKS_PER_EMAIL`: Links to process per email (default: 5)
- `CLAUDE_RPM_LIMIT`: Claude requests per minute (default: 5)
- `CLAUDE_MAX_RETRIES`: Maximum retry attempts (default: 6)

### 5.3 Audio & Podcast Configuration
- `PODCAST_S3_BUCKET`: S3 bucket for audio storage
- `POLLY_VOICE`: AWS Polly voice selection (default: Joanna)
- `POLLY_RATE`: Speech rate (default: medium)
- `GENERATE_AUDIO`: Enable/disable audio generation (default: true)
- `PODCAST_IMAGE_URL`: Podcast artwork URL for RSS feed

### 5.4 Storage Configuration
- `DYNAMODB_TABLE_NAME`: DynamoDB table for podcast content (default: ai_daily_news)

### 5.5 Testing Configuration
- `TEST_MODE`: Non-destructive testing mode (default: false)

## 6. Deployment & Operations

### 6.1 Deployment Package
The application is packaged as `ai-newsletter-lambda.zip` containing:
- Python source code
- All required dependencies
- AWS SDK (boto3) and supporting libraries

### 6.2 Runtime Requirements
- Python 3.8+ runtime environment
- AWS Lambda execution role with appropriate permissions
- Network connectivity for external API calls

### 6.3 Monitoring & Logging
- CloudWatch integration for comprehensive logging
- Structured error reporting with context
- Processing metrics and performance tracking
- Email notification for processing status

## 7. Data Flow

1. **Trigger**: Lambda function executes on scheduled basis (e.g., daily via EventBridge/CloudWatch Events)
2. **Queue Polling**: Lambda function polls SQS queue for accumulated email messages from AWS SES
3. **Message Processing**: Function retrieves and parses all available email messages in the queue
4. **Content Enhancement**: System fetches and processes linked web content from newsletters
5. **AI Analysis**: Claude AI generates comprehensive summaries using hybrid processing strategy
6. **Audio Generation**: AWS Polly converts text to speech and creates MP3 files (if enabled)
7. **Content Storage**: Audio files stored in S3, podcast text content saved to DynamoDB
8. **Distribution**: Summaries sent via SNS, RSS feed updated with new podcast episode
9. **Queue Cleanup**: Successfully processed messages removed from SQS queue

## 8. Security Considerations

- API keys stored as environment variables
- No sensitive information logged or committed
- Secure HTTP requests with proper user agents
- Content filtering to exclude tracking and sensitive links
- Presigned URLs with expiration for secure audio access

## 9. Performance Characteristics

- **Processing Time**: Variable based on content volume and Claude API response times
- **Throughput**: Up to 50 newsletters per execution
- **Scalability**: Automatic batch processing for large content volumes
- **Reliability**: Comprehensive error handling with retry mechanisms
- **Resource Optimization**: Smart token management and content truncation

## 10. Future Enhancement Opportunities

- Additional newsletter source integrations
- Multi-language support for international AI newsletters
- Advanced content categorization and tagging
- Historical trend analysis and insights
- Integration with business intelligence tools
- Custom voice training for more natural podcast delivery
- Interactive web dashboard for content management