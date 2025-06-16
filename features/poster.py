import aiohttp
from bs4 import BeautifulSoup
import logging
import re
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

async def _fetch_from_imdb(session, search_query):
    """Scrapes a poster from IMDb for a given query."""
    try:
        search_url = f"https://www.imdb.com/find?q={quote_plus(search_query)}&s=tt"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36', 'Accept-Language': 'en-US,en;q=0.9'}

        async with session.get(search_url, headers=headers, timeout=10) as resp:
            if resp.status != 200: return None
            soup = BeautifulSoup(await resp.text(), 'html.parser')
            
            result_link = soup.select_one("ul.ipc-metadata-list a.ipc-metadata-list-summary-item__t")
            if not result_link or not result_link.get('href'): return None
            
            movie_url = "https://www.imdb.com" + result_link['href'].split('?')[0]

        async with session.get(movie_url, headers=headers, timeout=10) as movie_resp:
            if movie_resp.status != 200: return None
            movie_soup = BeautifulSoup(await movie_resp.text(), 'html.parser')

            img_tag = movie_soup.select_one('div.ipc-poster img.ipc-image')
            if img_tag and img_tag.get('src'):
                poster_url = img_tag['src'].split('_V1_')[0] + "_V1_FMjpg_UX1000_.jpg"
                logger.info(f"Successfully scraped poster from IMDb for query '{search_query}'")
                return poster_url
    except Exception as e:
        logger.error(f"IMDb scraping sub-routine failed for '{search_query}': {e}")
    return None

async def get_poster(clean_title: str, year: str = None):
    """
    The 'Hero' Poster Finder. It will not fail.
    It tries multiple, increasingly broad scraping strategies before falling back.
    """
    async with aiohttp.ClientSession() as session:
        # Attempt 1: The "Golden" Search (Clean Title + Year)
        if year:
            query = f"{clean_title} {year}"
            logger.info(f"Poster search (Attempt 1/2): Searching IMDb with '{query}'...")
            poster_url = await _fetch_from_imdb(session, query)
            if poster_url: return poster_url

        # Attempt 2: Broader Search (Clean Title Only)
        logger.info(f"Poster search (Attempt 2/2): Searching IMDb with clean title '{clean_title}'...")
        poster_url = await _fetch_from_imdb(session, clean_title)
        if poster_url: return poster_url

    # Final Fallback: A 100% Reliable Placeholder
    logger.warning(f"All scraping attempts failed for '{clean_title}'. Generating a reliable fallback placeholder.")
    placeholder_text = f"{clean_title}{f' ({year})' if year else ''}"
    return f"https://via.placeholder.com/500x750/000000/FFFFFF.png?text={quote_plus(placeholder_text)}"
