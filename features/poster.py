import aiohttp
from imdb import Cinemagoer
import logging
import asyncio
import re
from urllib.parse import quote_plus
from telegraph.aio import Telegraph as AsyncTelegraph
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import os

logger = logging.getLogger(__name__)

# --- Initialize Cinemagoer and Telegraph ---
ia = Cinemagoer()
aio_telegraph = AsyncTelegraph()

async def _upload_to_telegraph(session, image_url):
    """Downloads an image and re-uploads it to telegra.ph for a 100% reliable link."""
    try:
        async with session.get(image_url) as response:
            if response.status == 200:
                content = await response.read()
                path = await aio_telegraph.upload_file(BytesIO(content))
                return 'https://telegra.ph' + path[0]['src']
    except Exception as e:
        logger.error(f"Failed to upload image to Telegraph: {e}")
    return None

async def _generate_fallback_image(text):
    """Generates a fallback image in memory and uploads it to telegra.ph."""
    font_path = "./resources/font.ttf"
    try:
        font = ImageFont.truetype(font_path, 40) if os.path.exists(font_path) else ImageFont.load_default()
    except IOError:
        font = ImageFont.load_default()
    
    image = Image.new('RGB', (600, 800), color=(15, 15, 15))
    draw = ImageDraw.Draw(image)
    
    # Word wrap logic
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        # Use the modern textlength method to check width
        if draw.textlength(current_line + word + " ", font=font) < 550:
            current_line += word + " "
        else:
            lines.append(current_line)
            current_line = word + " "
    lines.append(current_line)

    y_text = 300
    for line in lines:
        # Use textbbox to get width and height for centering
        bbox = draw.textbbox((0, 0), line.strip(), font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        
        draw.text(((600 - width) / 2, y_text), line.strip(), font=font, fill=(255, 255, 255))
        y_text += height + 15

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    
    try:
        path = await aio_telegraph.upload_file(buffer)
        return 'https://telegra.ph' + path[0]['src']
    except Exception as e:
        logger.error(f"Failed to upload fallback image to Telegraph: {e}")
        return "https://via.placeholder.com/600x800/0F0F0F/FFFFFF.png?text=Poster+Not+Found"

async def get_poster(clean_title: str, year: str = None):
    """
    The 'Hero' Poster Finder.
    1. Uses the Cinemagoer library for accurate IMDb searching.
    2. Downloads the poster and re-uploads to Telegraph for 100% reliability.
    3. Generates a custom fallback image if all else fails.
    """
    logger.info(f"Poster search initiated for: Title='{clean_title}', Year='{year}'")
    search_query = f"{clean_title} {year}" if year else clean_title

    try:
        loop = asyncio.get_event_loop()
        movies = await loop.run_in_executor(None, lambda: ia.search_movie(search_query))
        
        if movies:
            movie_id = movies[0].movieID
            movie = await loop.run_in_executor(None, lambda: ia.get_movie(movie_id))
            
            if movie and 'full-size cover url' in movie:
                poster_url = movie['full-size cover url']
                async with aiohttp.ClientSession() as session:
                    telegraph_link = await _upload_to_telegraph(session, poster_url)
                    if telegraph_link:
                        logger.info(f"Successfully processed poster for '{clean_title}' via Telegraph.")
                        return telegraph_link
    except Exception as e:
        logger.error(f"An error occurred during Cinemagoer search: {e}")

    logger.warning(f"All other methods failed. Generating fallback image for '{clean_title}'.")
    fallback_text = f"{clean_title}\n({year})" if year else clean_title
    return await _generate_fallback_image(fallback_text)
