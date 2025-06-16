import aiohttp
from bs4 import BeautifulSoup
import logging
import re

logger = logging.getLogger(__name__)

async def get_poster(title: str, year: str = None, retries=2):
    """
    Finds a poster by scraping IMDb with an improved multi-pass search.
    If IMDb fails, it will generate a fallback placeholder image.
    """
    try:
        # --- Pass 1: Highly specific search with title and year ---
        search_query = f"{title} {year}".strip() if year else title
        logger.info(f"Searching for poster with query: '{search_query}'")
        poster_url = await _fetch_imdb_poster(search_query)
        if poster_url:
            return poster_url

        # --- Pass 2: Broader search without the year (if first pass failed) ---
        if year:
            logger.warning(f"Poster search failed for '{search_query}'. Retrying without the year.")
            poster_url = await _fetch_imdb_poster(title)
            if poster_url:
                return poster_url
        
        # --- Pass 3: Try removing parentheses content ---
        cleaned_title = re.sub(r'\(.*?\)', '', title).strip()
        if cleaned_title.lower() != title.lower():
            logger.warning(f"Poster search failed for '{title}'. Retrying with cleaned title '{cleaned_title}'.")
            poster_url = await _fetch_imdb_poster(f"{cleaned_title} {year}" if year else cleaned_title)
            if poster_url:
                return poster_url

    except Exception as e:
        logger.error(f"An unexpected error occurred during poster scraping for query '{title}': {e}")

    # --- Final Fallback: Generate a placeholder image ---
    logger.warning(f"All poster search passes failed for query '{title}'. Generating a fallback placeholder.")
    placeholder_text = f"{title}{f' ({year})' if year else ''}".replace(" ", "+")
    return f"https://placehold.co/600x900/000000/FFFFFF?text={placeholder_text}"


async def _fetch_imdb_poster(search_query):
    """The core function to fetch a poster from IMDb for a given query."""
    try:
        search_query_encoded = re.sub(r'\s+', '+', search_query)
        search_url = f"https://www.imdb.com/find?q={search_query_encoded}&s=tt&ttype=ft&ref_=fn_ft"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36', 'Accept-Language': 'en-US,en;q=0.9'}

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(search_url, timeout=10) as resp:
                if resp.status != 200:
                    logger.error(f"IMDb find page request failed with status {resp.status} for query '{search_query}'")
                    return None
                
                soup = BeautifulSoup(await resp.text(), 'html.parser')
                
                # Find the first search result link in the list
                result_section = soup.find('ul', class_='ipc-metadata-list')
                if not result_section: return None
                
                first_result = result_section.find('a', class_='ipc-metadata-list-summary-item__t')
                if not first_result or not first_result.get('href'): return None
                
                movie_url = "https://www.imdb.com" + first_result['href'].split('?')[0]

            async with session.get(movie_url, timeout=10) as movie_resp:
                if movie_resp.status != 200:
                    logger.error(f"IMDb movie page request failed with status {movie_resp.status} for URL '{movie_url}'")
                    return None
                
                movie_soup = BeautifulSoup(await movie_resp.text(), 'html.parser')
                
                # Use a more robust selector to target the primary poster image
                img_tag = movie_soup.select_one('div.ipc-poster img.ipc-image')
                
                if img_tag and img_tag.get('src'):
                    poster_url = img_tag['src']
                    # Get the highest resolution version of the poster
                    if '_V1_' in poster_url:
                        poster_url = poster_url.split('_V1_')[0] + "_V1_FMjpg_UX1000_.jpg"
                    
                    logger.info(f"Successfully found poster for '{search_query}': {poster_url}")
                    return poster_url

    except Exception as e:
        logger.error(f"A sub-search for poster '{search_query}' failed with exception: {e}", exc_info=False)
    
    return None
