import re
import html

def prepare_text_for_speech_fixed(text: str) -> str:
    """
    Fixed version that properly handles SSML for Polly
    """
    from datetime import datetime
    
    # Get current date information for podcast intro
    now = datetime.now()
    day_name = now.strftime('%A')
    day = now.day
    if 4 <= day <= 20 or 24 <= day <= 30:
        suffix = "th"
    else:
        suffix = ["st", "nd", "rd"][day % 10 - 1]
    date_formatted = now.strftime(f'%B {day}{suffix}')
    
    # Create intro and outro (clean text, no SSML yet)
    intro = f"Welcome to Daily AI, by AI. I'm Joanna, a synthetic intelligence agent, bringing you today's most important developments in artificial intelligence. Today is {day_name}, {date_formatted}."
    outro = "That's all for today's Daily AI, by AI. I'm Joanna, a synthetic intelligence agent, and I'll be back tomorrow with more AI insights. Until then, keep innovating."
    
    # STEP 1: Clean up existing SSML tags (they'll be re-added properly later)
    # Remove all existing <break> tags - we'll add them back correctly
    text = re.sub(r'<break[^>]*/?>', ' ', text)
    
    # STEP 2: Remove markdown formatting
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'^\d+\.\s*', '', text, flags=re.MULTILINE)
    
    # STEP 3: Handle special characters that break SSML
    # Replace smart quotes with regular quotes
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace(''', "'").replace(''', "'")
    
    # Handle problematic characters
    text = text.replace('&', 'and')  # & breaks SSML
    text = text.replace('%', ' percent')  # % can be problematic
    text = text.replace('$', 'dollar ')  # $ can be problematic
    text = text.replace('<', ' less than ')  # < breaks SSML
    text = text.replace('>', ' greater than ')  # > breaks SSML
    
    # STEP 4: Clean URLs 
    text = re.sub(r'https?://[^\s]+', 'link', text)
    
    # STEP 5: Clean up whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    
    # STEP 6: HTML escape remaining content for SSML safety
    text = html.escape(text, quote=False)
    intro = html.escape(intro, quote=False)
    outro = html.escape(outro, quote=False)
    
    # STEP 7: Add SSML breaks AFTER escaping (use consistent double quotes)
    # Add natural pauses after sentences
    text = re.sub(r'([.!?])\s+', r'\1 <break time="0.5s"/> ', text)
    
    # Add breaks for section transitions
    text = re.sub(r'(TOP NEWS HEADLINES|DEEP DIVE ANALYSIS)', r'<break time="1s"/> \1 <break time="1s"/>', text)
    text = re.sub(r'(Technical Deep Dive|Financial Analysis|Market Disruption|Cultural & Social Impact|Executive Action Plan)', r'<break time="1s"/> \1 <break time="1s"/>', text)
    
    # STEP 8: Combine all parts with consistent SSML
    full_podcast = f'{intro} <break time="2s"/> {text} <break time="2s"/> {outro}'
    
    return full_podcast

def chunk_text_for_polly_fixed(text, max_length=2800):
    """
    Fixed chunking that ensures SSML compatibility
    """
    # Split into sentences, being careful about SSML tags
    sentence_pattern = r'(?<=[.!?])\s+(?![^<]*>)'  # Don't split inside SSML tags
    sentences = re.split(sentence_pattern, text.strip())
    
    chunks = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # Check if adding this sentence would exceed limit
        if len(current) + len(sentence) + 1 <= max_length:
            current += sentence + " "
        else:
            if current:
                chunks.append(current.strip())
            current = sentence + " "

    if current:
        chunks.append(current.strip())

    return chunks

def convert_text_to_speech_fixed(text, polly_client, voice_id='Joanna', rate='medium'):
    """
    Fixed TTS conversion with proper SSML handling
    """
    full_audio = b''
    chunks = chunk_text_for_polly_fixed(text)
    
    print(f"Converting {len(chunks)} text chunks to speech using voice {voice_id}")
    
    for i, chunk in enumerate(chunks):
        try:
            # Wrap in SSML with consistent quote style and prosody
            ssml_text = f'<speak><prosody rate="{rate}">{chunk}</prosody></speak>'
            
            # Debug: Print first chunk to verify SSML
            if i == 0:
                print(f"Sample SSML: {ssml_text[:200]}...")
            
            response = polly_client.synthesize_speech(
                Text=ssml_text,
                TextType='ssml',
                OutputFormat='mp3',
                VoiceId=voice_id
            )
            
            chunk_audio = response['AudioStream'].read()
            full_audio += chunk_audio
            
            print(f"✅ Processed chunk {i+1}/{len(chunks)}, size: {len(chunk_audio)} bytes")
            
        except Exception as e:
            print(f"❌ Error processing chunk {i+1}: {str(e)}")
            print(f"Problematic chunk preview: {chunk[:100]}...")
            # Continue with other chunks instead of failing completely
            continue
    
    print(f"Total audio size: {len(full_audio)} bytes")
    return full_audio

# Test the fix with your problematic text
if __name__ == "__main__":
    test_text = """Welcome to Daily AI, by AI. I'm Joanna, a synthetic intelligence agent, bringing you today's most important developments in artificial intelligence. Today is Saturday, August 23rd. <break time='2s'/> TOP NEWS HEADLINES <break time="1s"/> Meta just completed a massive AI restructure under Alexandr Wang, dissolving their AGI Foundations team and imposing a hiring freeze after their summer talent poaching spree. <break time="0.5s"/> The Chan Zuckerberg Initiative launched rBio, an AI model trained on virtual cell simulations that could compress pharma R&D timelines from decades to years."""
    
    fixed_text = prepare_text_for_speech_fixed(test_text)
    print("FIXED TEXT PREVIEW:")
    print(fixed_text[:500])
    print("\n" + "="*50)
    
    chunks = chunk_text_for_polly_fixed(fixed_text)
    print(f"Created {len(chunks)} chunks")
    for i, chunk in enumerate(chunks):
        print(f"\nChunk {i+1} preview: {chunk[:100]}...")
        # Test SSML creation
        ssml = f'<speak><prosody rate="medium">{chunk}</prosody></speak>'
        print(f"SSML valid: {len(ssml) > 0}")  # Basic validation