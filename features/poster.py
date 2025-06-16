
import aiohttp
from bs4 import BeautifulSoup
import logging
import re
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

async def _fetch_from_imdb(session, search_query):
    """The core function to scrape a poster from IMDb for a given query."""
    try:
        # Use a more specific search URL for movies/series
        search_url = f"https://www.imdb.com/find?q={quote_plus(search_query)}&s=tt"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36', 'Accept-Language': 'en-US,en;q=0.9'}

        async with session.get(search_url, headers=headers, timeout=10) as resp:
            if resp.status != 200: return None
            soup = BeautifulSoup(await resp.text(), 'html.parser')
            
            # Find the first search result link, which is usually the most relevant
            result_link = soup.select_one("ul.ipc-metadata-list a.ipc-metadata-list-summary-item__t")
            if not result_link or not result_link.get('href'): return None
            
            movie_url = "https://www.imdb.com" + result_link['href'].split('?')[0]

        # Go to the movie's page to get the high-quality poster
        async with session.get(movie_url, headers=headers, timeout=10) as movie_resp:
            if movie_resp.status != 200: return None
            movie_soup = BeautifulSoup(await movie_resp.text(), 'html.parser')

            # This selector targets the primary poster image on the page
            img_tag = movie_soup.select_one('div.ipc-poster img.ipc-image')
            if img_tag and img_tag.get('src'):
                # Get the highest resolution version of the poster available
                poster_url = img_tag['src'].split('_V1_')[0] + "_V1_FMjpg_UX1000_.jpg"
                logger.info(f"Successfully scraped poster from IMDb for query '{search_query}'")
                return poster_url
    except Exception as e:
        logger.error(f"IMDb scraping sub-routine failed for '{search_query}': {e}")
    return None

async def get_poster(clean_title: str, year: str = None, original_title: str = None):
    """
    An advanced, multi-layered, API-free poster finder. It tries multiple scraping
    strategies before creating a reliable fallback image.
    """
    async with aiohttp.ClientSession() as session:
        # --- Attempt 1: The "Golden" Search (Clean Title + Year) ---
        # This is the most likely to get a perfect match.
        if year:
            query = f"{clean_title} {year}"
            logger.info(f"Poster search (Attempt 1/3): Searching IMDb with '{query}'...")
            poster_url = await _fetch_from_imdb(session, query)
            if poster_url: return poster_url

        # --- Attempt 2: Broader Search (Clean Title Only) ---
        # Useful for releases where the year might be wrong in the filename.
        logger.info(f"Poster search (Attempt 2/3): Searching IMDb with clean title '{clean_title}'...")
        poster_url = await _fetch_from_imdb(session, clean_title)
        if poster_url: return poster_url

        # --- Attempt 3: The "Just in Case" Search (Original Title) ---
        # If the cleaning was too aggressive, this might catch it.
        if original_title and original_title.lower() != clean_title.lower():
            logger.info(f"Poster search (Attempt 3/3): Searching IMDb with original title '{original_title}'...")
            poster_url = await _fetch_from_imdb(session, original_title)
            if poster_url: return poster_url

    # --- Final Fallback: A 100% Reliable Placeholder ---
    # This service is extremely robust and works well with Telegram.
    logger.warning(f"All scraping attempts failed for '{clean_title}'. Generating a reliable fallback placeholder.")
    placeholder_text = f"{clean_title}{f' ({year})' if year else ''}"
    # URL encode the text to handle special characters
    return f"https://via.placeholder.com/500x750/000000/FFFFFF.png?text={quote_plus(placeholder_text)}"
