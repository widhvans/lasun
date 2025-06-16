import aiohttp
from imdb import Cinemagoer
import logging
from urllib.parse import quote_plus
from telegraph.aio import Telegraph as AsyncTelegraph
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import os
import asyncio
from bs4 import BeautifulSoup

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
                if isinstance(path, list) and path and isinstance(path[0], dict) and 'src' in path[0]:
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
    
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        # Use textbbox for modern Pillow versions
        bbox = draw.textbbox((0, 0), current_line + word, font=font)
        if bbox[2] < 550:
            current_line += word + " "
        else:
            lines.append(current_line)
            current_line = word + " "
    lines.append(current_line)

    y_text = 300
    for line in lines:
        bbox = draw.textbbox((0,0), line, font=font)
        width, height = bbox[2], bbox[3]
        draw.text(((600 - width) / 2, y_text), line, font=font, fill=(255, 255, 255))
        y_text += height + 10

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    
    try:
        path = await aio_telegraph.upload_file(buffer)
        if isinstance(path, list) and path and isinstance(path[0], dict) and 'src' in path[0]:
            return 'https://telegra.ph' + path[0]['src']
    except Exception as e:
        logger.error(f"Failed to upload fallback image to Telegraph: {e}")
    
    return f"https://via.placeholder.com/600x800/0F0F0F/FFFFFF?text={quote_plus(text)}"

async def _fetch_from_tmdb(session, title, year):
    """Secondary Method: Scrapes poster from The Movie Database (TMDB)."""
    try:
        search_query = f"{title} {year}" if year else title
        search_url = f"https://www.themoviedb.org/search?query={quote_plus(search_query)}"
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.5'}
        
        async with session.get(search_url, headers=headers) as response:
            if response.status != 200: return None
            soup = BeautifulSoup(await response.text(), 'html.parser')
            
            result_card = soup.select_one("div.card.style_1 a.image")
            if not result_card or not result_card.get('href'): return None
            
            media_url = "https://www.themoviedb.org" + result_card['href']
            
        async with session.get(media_url, headers=headers) as media_response:
            if media_response.status != 200: return None
            media_soup = BeautifulSoup(await media_response.text(), 'html.parser')
            
            poster_div = media_soup.select_one("div.poster img.poster")
            if poster_div and poster_div.get('src'):
                poster_path = poster_div['src']
                full_poster_url = f"https://image.tmdb.org/t/p/original{poster_path}"
                logger.info(f"Found poster on TMDB for '{title}'.")
                return await _upload_to_telegraph(session, full_poster_url)
    except Exception as e:
        logger.error(f"An error occurred during TMDB scrape: {e}")
    return None

async def get_poster(clean_title: str, year: str = None):
    """The 'Hero' Poster Finder with a multi-layered approach."""
    logger.info(f"Poster search initiated for: Title='{clean_title}', Year='{year}'")
    search_query = f"{clean_title} {year}" if year else clean_title

    # --- Attempt 1: Primary Source (Cinemagoer/IMDb) ---
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
                        logger.info(f"Successfully processed poster for '{clean_title}' via Cinemagoer/Telegraph.")
                        return telegraph_link
    except Exception as e:
        logger.error(f"An error occurred during Cinemagoer search: {e}")

    # --- Attempt 2: Secondary Source (TMDB Scraping) ---
    logger.warning("Cinemagoer failed. Trying secondary source: TMDB.")
    async with aiohttp.ClientSession() as session:
        tmdb_link = await _fetch_from_tmdb(session, clean_title, year)
        if tmdb_link:
            logger.info(f"Successfully processed poster for '{clean_title}' via TMDB/Telegraph.")
            return tmdb_link

    # --- Attempt 3: Ultimate Fallback (Generate Image) ---
    logger.warning(f"All other methods failed. Generating fallback image for '{clean_title}'.")
    fallback_text = f"{clean_title}\n({year})" if year else clean_title
    return await _generate_fallback_image(fallback_text)
