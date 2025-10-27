import json
import boto3
import random
import openai
import os
import re
from datetime import datetime
from xml.etree.ElementTree import fromstring, Element, SubElement, tostring, register_namespace
from xml.dom import minidom

S3_BUCKET = 'audiobook-daily-summary'
BOOKS_FILE = 'books.json'
POLLY_VOICE = 'Joanna'
FEED_KEY = 'feed.xml'
PODCAST_IMAGE_URL = f'https://{S3_BUCKET}.s3.amazonaws.com/podcast.jpg'
SNS_TOPIC_ARN = 'arn:aws:sns:eu-central-1:794416789749:audiobook-summary-results'
EXPIRE_IN_A_WEEK = 604800
DYNAMODB_TABLE_NAME = 'ai_daily_news'

s3 = boto3.client('s3')
polly = boto3.client('polly')
sns = boto3.client('sns')
bedrock = boto3.client('bedrock-runtime')
dynamodb = boto3.resource('dynamodb')
openai.api_key = os.environ['OPENAI_API_KEY']

register_namespace('itunes', "http://www.itunes.com/dtds/podcast-1.0.dtd")

def save_podcast_to_dynamodb(text):
    table = dynamodb.Table(DYNAMODB_TABLE_NAME)
    current_date = datetime.utcnow().strftime('%Y-%m-%d')
    
    try:
        table.put_item(
            Item={
                'date': current_date,
                'text': text
            }
        )
        return True
    except Exception as e:
        print(f"Error saving to DynamoDB: {str(e)}")
        return False

def chunk_text(text, max_length=2800):
    paragraphs = text.split('\n')
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) < max_length:
            current += para + '\n'
        else:
            chunks.append(current)
            current = para + '\n'
    if current:
        chunks.append(current)
    return chunks

def generate_summary_with_claude4_sonnet(prompt):
    response = bedrock.invoke_model(
        modelId='anthropic.claude-sonnet-4-20250514-v1:0',
        body=json.dumps({
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 1200,
            "temperature": 0.7,
            "top_p": 1,
            "stop_sequences": []
        }),
        contentType='application/json',
        accept='application/json'
    )

    result = json.loads(response['body'].read())
    return result['content'][0]['text']

def generate_summary_with_titan(prompt):
    response = bedrock.invoke_model(
        modelId='amazon.titan-text-express-v1',
        body=json.dumps({
            "inputText": prompt,
            "textGenerationConfig": {
                "maxTokenCount": 1200,
                "temperature": 0.7,
                "topP": 1,
                "stopSequences": []
            }
        }),
        contentType='application/json',
        accept='application/json'
    )

    result = json.loads(response['body'].read())
    return result['results'][0]['outputText']

def get_random_finished_book():
    response = s3.get_object(Bucket=S3_BUCKET, Key=BOOKS_FILE)
    books = json.loads(response['Body'].read())
    finished_books = [book for book in books if book.get("read_status") == "Finished"]
    return random.choice(finished_books)

def generate_propt(book):
    prompt = (
        f"Summarize the key ideas and insights of the book '{book['title']}' by {book['author']}' "
        "in a spoken, friendly style suitable for a 5-minute audio. "
        f"Write in '{book['author']}' style explaining the topic. "
        "Focus on practical takeaways, big ideas, and engaging storytelling. "
        "If its a business related book, for each main point, provide reflections relevant to leadership in technology companies—"
        "such as innovation, decision-making, people management, or strategy. If not, simply ignore this guideline."
        "Use a tone that sounds natural when read aloud."
        f"End with '{book['author']}' moto or known one liner or quote."
        "Return only the text needs to be read!"
    )
    return prompt

def generate_summary_with_openai_o4(prompt):
    
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200,
        temperature=0.7
    )
    return response['choices'][0]['message']['content']

def chunk_text(text, max_length=2800):
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

def convert_text_to_speech(text, voice_id, rate='medium'):
    full_audio = b''
    for chunk in chunk_text(text):
        ssml_text = f"<speak><prosody rate='{rate}'>{chunk}</prosody></speak>"
        polly_response = polly.synthesize_speech(
            Text=ssml_text,
            TextType='ssml',
            OutputFormat='mp3',
            VoiceId=voice_id
        )
        full_audio += polly_response['AudioStream'].read()
    return full_audio

def update_rss_feed(book, audio_key, audio_length):
    try:
        feed_obj = s3.get_object(Bucket=S3_BUCKET, Key=FEED_KEY)
        rss = fromstring(feed_obj['Body'].read())
        channel = rss.find('channel')
    except Exception:
        rss = Element("rss", version="2.0", attrib={"xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"})
        channel = SubElement(rss, "channel")
        SubElement(channel, "title").text = "Daily Book Summary"
        SubElement(channel, "link").text = f"https://{S3_BUCKET}.s3.amazonaws.com/{FEED_KEY}"
        SubElement(channel, "language").text = "en-us"
        SubElement(channel, "itunes:author").text = "Nir's AI Podcast"
        SubElement(channel, "description").text = (
            "Your daily 5-minute summary of a book, with leadership insights for tech professionals."
        )
        SubElement(channel, "itunes:image", href=PODCAST_IMAGE_URL)
        SubElement(channel, "itunes:explicit").text = "false"
        SubElement(channel, "itunes:category", text="Education")
        SubElement(channel, "itunes:category", text="Technology")

    item = SubElement(channel, "item")
    SubElement(item, "title").text = f"{book['title']} – Summary"
    SubElement(item, "description").text = (
        f"This episode summarizes '{book['title']}' by {book['author']}. "
        f"Nir originally listened to this book on {book.get('purchase_date', 'an unknown date')}"
    )
    SubElement(item, "enclosure", url=f"https://{S3_BUCKET}.s3.amazonaws.com/{audio_key}",
               length=str(audio_length), type="audio/mpeg")
    SubElement(item, "guid").text = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', book['title']).lower()}-{datetime.utcnow().strftime('%Y%m%d')}"
    SubElement(item, "pubDate").text = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
    SubElement(item, "itunes:duration").text = "5:00"

    rss_bytes = tostring(rss, encoding="utf-8", xml_declaration=True)
    pretty_xml = minidom.parseString(rss_bytes).toprettyxml(encoding="utf-8")
    s3.put_object(Bucket=S3_BUCKET, Key=FEED_KEY, Body=pretty_xml, ContentType='application/rss+xml')

def lambda_handler(event, context):
    result_message = {}
    try:
        book = get_random_finished_book()
        g_prompt = generate_propt(book)
        
        summary = "<break time=\"2s\"/>"
        #summary = "This is an A-B test of two models, which one you liked?<break time=\"1s\"/> "
        #summary = generate_summary_with_titan(g_prompt)
        #summary += "This is the end of A part, ready for the next one?<break time=\"1s\"/> "
        summary = generate_summary_with_openai_o4(g_prompt)
        audio = convert_text_to_speech(summary, POLLY_VOICE)

        safe_title = re.sub(r'[^a-zA-Z0-9_-]', '_', book['title'])
        audio_key = f"summaries/{safe_title}.mp3"

        s3.put_object(Bucket=S3_BUCKET, Key=audio_key, Body=audio, ContentType='audio/mpeg')
        s3_url = s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': audio_key}, ExpiresIn=EXPIRE_IN_A_WEEK)

        update_rss_feed(book, audio_key, len(audio))

        result_message = {
            'title': book.get('title'),
            'author': book.get('author'),
            'prompt': g_prompt,
            'summary': summary,
            's3_url': s3_url,
            'status': '✅ Success'
        }

    except Exception as e:
        result_message = {
            'status': '❌ Failure',
            'error': str(e)
        }
        raise e

    finally:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"[Daily Book Summary] {result_message.get('status', 'Unknown')}",
            Message=json.dumps(result_message)
        )

        return {
            'statusCode': 200 if result_message.get("status") == "✅ Success" else 500,
            'body': json.dumps(result_message)
        }
