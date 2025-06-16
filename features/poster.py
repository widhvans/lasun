import aiohttp
from bs4 import BeautifulSoup
import logging
import re
from urllib.parse import quote_plus
from telegraph import Telegraph
from telegraph.aio import Telegraph as AsyncTelegraph
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)
telegraph = Telegraph()
telegraph.create_account(short_name='PosterBot')
aio_telegraph = AsyncTelegraph(telegraph.get_access_token())

async def _upload_to_telegraph(session, image_url):
    """Downloads an image and re-uploads it to telegra.ph for a reliable link."""
    try:
        async with session.get(image_url) as response:
            if response.status == 200:
                content = await response.read()
                # Use aio_telegraph for async upload
                path = await aio_telegraph.upload_file(BytesIO(content))
                return 'https://telegra.ph' + path[0]['src']
    except Exception as e:
        logger.error(f"Failed to upload image to Telegraph: {e}")
    return None

async def _generate_fallback_image(text):
    """Generates a fallback image in memory and uploads it to telegra.ph."""
    try:
        font = ImageFont.truetype("./resources/font.ttf", 40)
    except IOError:
        font = ImageFont.load_default() # Fallback font
    
    image = Image.new('RGB', (500, 750), color = (0, 0, 0))
    draw = ImageDraw.Draw(image)
    
    lines = text.split('\n')
    y_text = 150
    for line in lines:
        width, height = draw.textsize(line, font=font)
        draw.text(((500 - width) / 2, y_text), line, font=font, fill=(255, 255, 255))
        y_text += height + 10

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    
    try:
        path = await aio_telegraph.upload_file(buffer)
        return 'https://telegra.ph' + path[0]['src']
    except Exception as e:
        logger.error(f"Failed to upload fallback image to Telegraph: {e}")
        return None


async def get_poster(clean_title: str, year: str = None):
    """The 'Hero' Poster Finder. It downloads, re-uploads, and will not fail."""
    async with aiohttp.ClientSession() as session:
        search_query = f"{clean_title} {year}" if year else clean_title
        logger.info(f"Poster search started for: '{search_query}'")

        try:
            # Main search on IMDb
            imdb_url = f"https://www.imdb.com/find?q={quote_plus(search_query)}&s=tt"
            headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.5'}
            async with session.get(imdb_url, headers=headers) as resp:
                if resp.status == 200:
                    soup = BeautifulSoup(await resp.text(), 'html.parser')
                    result_link = soup.select_one("ul.ipc-metadata-list a.ipc-metadata-list-summary-item__t")
                    if result_link and result_link.get('href'):
                        movie_url = "https://www.imdb.com" + result_link['href'].split('?')[0]
                        async with session.get(movie_url, headers=headers) as movie_resp:
                            if movie_resp.status == 200:
                                movie_soup = BeautifulSoup(await movie_resp.text(), 'html.parser')
                                img_tag = movie_soup.select_one('div.ipc-poster img.ipc-image')
                                if img_tag and img_tag.get('src'):
                                    poster_url = img_tag['src'].split('_V1_')[0] + "_V1_FMjpg_UX1000_.jpg"
                                    telegraph_link = await _upload_to_telegraph(session, poster_url)
                                    if telegraph_link:
                                        logger.info(f"Successfully processed poster for '{clean_title}' via Telegraph.")
                                        return telegraph_link
        except Exception as e:
            logger.error(f"An error occurred during IMDb scrape: {e}")

    # If all else fails, generate and upload a fallback image.
    logger.warning(f"All other methods failed. Generating fallback image for '{clean_title}'.")
    fallback_text = f"{clean_title}\n({year})" if year else clean_title
    return await _generate_fallback_image(fallback_text)
