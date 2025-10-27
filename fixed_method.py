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
    intro = f"Welcome to Daily AI, by AI. I'm {host_name}, bringing you today's most important developments in artificial intelligence. Today is {day_name}, {date_formatted}."
    outro = f"That's all for today's Daily AI, by AI. I'm {host_name}, and I'll be back tomorrow with more AI insights. Until then, keep innovating."
    
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