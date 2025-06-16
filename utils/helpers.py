import re
import base64
import logging
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database.db import get_user
from features.poster import get_poster

logger = logging.getLogger(__name__)

def extract_file_details(filename: str):
    """
    Analyzes a filename and extracts structured information like title, year, season, episode, and quality.
    """
    # Normalize filename
    cleaned_name = re.sub(r'\[.*?\]', '', filename) # Remove bracketed content like [@channel]
    cleaned_name = re.sub(r'(\.\w+$)', '', cleaned_name) # Remove file extension
    cleaned_name = cleaned_name.replace('_', ' ').replace('.', ' ') # Replace separators with spaces

    details = {
        'original_name': filename,
        'title': None, 'year': None, 'type': 'movie',
        'season': None, 'episode': None, 'quality': None, 'resolution': None,
        'audio': [], 'source': None
    }

    # --- Extract Year ---
    year_match = re.search(r'\b(19[89]\d|20\d{2})\b', cleaned_name)
    if year_match:
        details['year'] = year_match.group(1)
        # Temporarily remove year to not confuse title extraction
        cleaned_name = cleaned_name.replace(details['year'], '')

    # --- Extract Season and Episode (Defines as a Series) ---
    se_match = re.search(r'[sS](\d{1,2})[._ ]?[eE](\d{1,3})', cleaned_name, re.IGNORECASE)
    if se_match:
        details['type'] = 'series'
        details['season'] = int(se_match.group(1))
        details['episode'] = int(se_match.group(2))
    else:
        ep_match = re.search(r'\b(episode|ep|e|part)\s?(\d{1,3})\b', cleaned_name, re.IGNORECASE)
        if ep_match:
            details['type'] = 'series'
            details['episode'] = int(ep_match.group(2))
        
        season_match = re.search(r'\b(season|s)\s?(\d{1,2})\b', cleaned_name, re.IGNORECASE)
        if season_match:
            details['type'] = 'series'
            details['season'] = int(season_match.group(2))
            
    # --- Extract Quality & Resolution ---
    # Prioritize specific resolutions first
    res_match = re.search(r'\b(2160p|1080p|720p|480p|360p|240p)\b', cleaned_name, re.IGNORECASE)
    if res_match:
        details['resolution'] = res_match.group(1).lower()
        # Add 'p' if missing, e.g. from "1080"
        if not details['resolution'].endswith('p'):
            details['resolution'] += 'p'
    
    quality_match = re.search(r'\b(4k|UHD|FHD|HD|SD)\b', cleaned_name, re.IGNORECASE)
    if quality_match:
        details['quality'] = quality_match.group(1).upper()

    # --- Extract Source ---
    source_match = re.search(r'\b(BluRay|Blu-Ray|BDRip|BRRip|WEB-DL|WEBDL|WEBRip|HDRip|DVDScr|DVD-Rip)\b', cleaned_name, re.IGNORECASE)
    if source_match:
        details['source'] = source_match.group(1).upper().replace("-", "")

    # --- Extract Audio ---
    audio_match = re.findall(r'\b(Hindi|English|Tamil|Telugu|Dual[\s-]?Audio|Multi[\s-]?Audio)\b', cleaned_name, re.IGNORECASE)
    if audio_match:
        details['audio'] = [lang.replace('-', ' ').title() for lang in audio_match]

    # --- Extract Title ---
    # Remove all technical tags to isolate the title
    title_strip = cleaned_name
    tech_tags = [
        r'[sS]\d{1,2}[eE]\d{1,3}', r'\b(episode|ep|e|part)\s?\d{1,3}\b', r'\b(season|s)\s?\d{1,2}\b',
        r'\b(2160p|1080p|720p|480p|360p|240p|4k|UHD|FHD|HD|SD)\b',
        r'\b(BluRay|Blu-Ray|BDRip|BRRip|WEB-DL|WEBDL|WEBRip|HDRip|DVDScr|DVD-Rip)\b',
        r'\b(Hindi|English|Tamil|Telugu|Dual Audio|Multi Audio)\b',
        r'\b(x264|x265|HEVC|AAC|AC3)\b',
        r'\(.*?\)' # Content in parentheses
    ]
    for tag in tech_tags:
        title_strip = re.sub(tag, '', title_strip, flags=re.IGNORECASE)
    
    details['title'] = ' '.join(title_strip.split()).strip()
    if not details['title']:
        details['title'] = filename.rsplit('.', 1)[0] # Fallback to filename without extension

    return details


def create_link_label(details: dict) -> str:
    """
    Creates a smart label for a file link based on its extracted details.
    Examples: "S01 E01", "720p", "1080p BluRay"
    """
    if details.get('type') == 'series' and details.get('episode'):
        season_part = f"S{details['season']:02d}" if details.get('season') else ""
        episode_part = f"E{details['episode']:02d}"
        return f"{season_part} {episode_part}".strip()
    
    if details.get('resolution'):
        label = details['resolution'].upper()
        if details.get('source'):
            label += f" {details['source']}"
        return label
    
    if details.get('quality'):
        return details['quality']

    # Fallback to a cleaned up original name if no specific tags found
    return " ".join(details['original_name'].rsplit('.', 1)[0].replace('.', ' ').split())


def natural_sort_key(s: str):
    """Sorts strings with numbers in a natural order."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]


async def create_post(client, user_id, messages):
    """
    The main post creation engine. It analyzes a batch of messages,
    understands the content structure (movie vs series), and formats
    a single, cohesive post.
    """
    user = await get_user(user_id)
    if not user: return None, None, None
    
    bot_username = client.me.username
    
    # 1. Analyze all files in the batch
    all_details = [extract_file_details(getattr(m, m.media.value).file_name) for m in messages]
    for i, details in enumerate(all_details):
        details['message_id'] = messages[i].id
        details['file_unique_id'] = getattr(messages[i], messages[i].media.value).file_unique_id

    # Sort files naturally based on their original names to handle episodes/parts correctly
    all_details.sort(key=lambda d: natural_sort_key(d['original_name']))

    # 2. Determine Overall Post Title and Type
    # Use the details from the first file as the base for the post
    base_details = all_details[0]
    title = base_details['title']
    year = base_details['year']
    
    # Check if we have multiple seasons in this batch
    seasons_in_batch = sorted(list(set(d['season'] for d in all_details if d['season'])))
    is_multi_season = len(seasons_in_batch) > 1
    is_series = any(d['type'] == 'series' for d in all_details)

    header = f"🎬 **{title}**"
    if year: header += f" **({year})**"
    if is_series and not is_multi_season and seasons_in_batch:
        header += f" **S{seasons_in_batch[0]:02d} Complete**"
    elif is_multi_season:
         header += f" **(S{seasons_in_batch[0]:02d} - S{seasons_in_batch[-1]:02d})**"
         
    # 3. Get Poster
    poster_title = base_details['title']
    poster_year = base_details['year']
    post_poster = await get_poster(poster_title, poster_year) if user.get('show_poster', True) else None

    # 4. Generate Links
    links = ""
    last_season = None

    for details in all_details:
        # Add a season sub-header if the season changes in a multi-season post
        if is_multi_season and details['season'] != last_season:
            links += f"\n**Mirzapur S{details['season']:02d}**\n"
            last_season = details['season']

        link_label = create_link_label(details)
        payload = f"get_{details['file_unique_id']}"
        bot_redirect_link = f"https://t.me/{bot_username}?start={payload}"
        
        # Format inspired by user examples
        links += f"📤 **{link_label}** 👉 [Click Here]({bot_redirect_link})\n"

    final_caption = f"{header}\n\n━━━━━━━━━━━━━━━━━━━━━━━\n{links.strip()}\n━━━━━━━━━━━━━━━━━━━━━━━"
    
    # 5. Add Footer Buttons
    footer_buttons_data = user.get('footer_buttons', [])
    footer_keyboard = None
    if footer_buttons_data:
        buttons = [[InlineKeyboardButton(btn['name'], url=btn['url'])] for btn in footer_buttons_data]
        footer_keyboard = InlineKeyboardMarkup(buttons)
        
    return post_poster, final_caption, footer_keyboard


# --- UNCHANGED HELPER FUNCTIONS ---

async def get_main_menu(user_id):
    user_settings = await get_user(user_id)
    if not user_settings: return InlineKeyboardMarkup([])
    shortener_text = "⚙️ Shortener Settings" if user_settings.get('shortener_url') else "🔗 Set Shortener"
    fsub_text = "⚙️ Manage FSub" if user_settings.get('fsub_channel') else "📢 Set FSub"
    buttons = [
        [InlineKeyboardButton("➕ Manage Auto Post", callback_data="manage_post_ch")],
        [InlineKeyboardButton("🗃️ Manage Index DB", callback_data="manage_db_ch")],
        [
            InlineKeyboardButton(shortener_text, callback_data="shortener_menu"),
            InlineKeyboardButton("🔄 Backup Links", callback_data="backup_links")
        ],
        [
            InlineKeyboardButton("🔗 Set Filename Link", callback_data="set_filename_link"),
            InlineKeyboardButton("👣 Footer Buttons", callback_data="manage_footer")
        ],
        [
            InlineKeyboardButton("🖼️ IMDb Poster", callback_data="poster_menu"),
            InlineKeyboardButton("📂 My Files", callback_data="my_files_1")
        ],
        [InlineKeyboardButton(fsub_text, callback_data="set_fsub")],
        [InlineKeyboardButton("❓ How to Download", callback_data="set_download")]
    ]
    if user_id == Config.ADMIN_ID:
        buttons.append([InlineKeyboardButton("🔑 Set Owner DB", callback_data="set_owner_db")])
        buttons.append([InlineKeyboardButton("⚠️ Reset Files DB", callback_data="reset_db_prompt")])
    return InlineKeyboardMarkup(buttons)

def go_back_button(user_id):
    return InlineKeyboardMarkup([[InlineKeyboardButton("« Go Back", callback_data=f"go_back_{user_id}")]])

def format_bytes(size):
    if not isinstance(size, (int, float)): return "N/A"
    power = 1024; n = 0; power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size >= power and n < len(power_labels) - 1 :
        size /= power; n += 1
    return f"{size:.2f} {power_labels[n]}"

async def get_file_raw_link(message):
    return f"https://t.me/c/{str(message.chat.id).replace('-100', '')}/{message.id}"

def encode_link(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().strip("=")

def decode_link(encoded_text: str) -> str:
    padding = 4 - (len(encoded_text) % 4)
    encoded_text += "=" * padding
    return base64.urlsafe_b64decode(encoded_text).decode()
