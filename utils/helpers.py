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
    Hyper-aggressively analyzes a filename to extract the cleanest possible details.
    """
    details = {
        'original_name': filename, 'clean_title': None, 'original_title': None,
        'year': None, 'type': 'movie', 'season': None, 'episode': None, 'resolution': None,
    }
    
    base_name = filename.rsplit('.', 1)[0]
    details['original_title'] = base_name.replace('.', ' ').strip()
    
    year_match = re.search(r'\b(19[89]\d|20\d{2})\b', base_name)
    if year_match:
        details['year'] = year_match.group(1)

    se_match = re.search(r'[sS](\d{1,2})[._ ]?[eE](\d{1,3})', base_name)
    if se_match:
        details['type'] = 'series'
        details['season'] = int(se_match.group(1))
        details['episode'] = int(se_match.group(2))
    else:
        ep_match = re.search(r'\b(episode|ep|e|part)[\s._]?(\d{1,3})\b', base_name, re.IGNORECASE)
        if ep_match:
            details['type'] = 'series'
            details['episode'] = int(ep_match.group(2))
        
        season_match = re.search(r'\b(season|s)[\s._]?(\d{1,2})\b', base_name, re.IGNORECASE)
        if season_match:
            details['type'] = 'series'
            details['season'] = int(season_match.group(2))

    res_match = re.search(r'\b(2160p|1080p|720p|480p|360p|240p)\b', base_name, re.IGNORECASE)
    if res_match:
        details['resolution'] = res_match.group(1)

    title_strip = base_name
    stop_point_match = re.search(r'\b(19\d{2}|20\d{2}|[sS]\d{1,2}|E\d{1,2})\b', title_strip)
    if stop_point_match:
        title_strip = title_strip[:stop_point_match.start()]
    
    title_strip = title_strip.replace('.', ' ').replace('_', ' ').strip()
    details['clean_title'] = ' '.join(title_strip.split())
    
    if not details['clean_title']:
        details['clean_title'] = details['original_title']
        
    return details

def create_link_label(details: dict) -> str:
    """Creates a smart label for a file link."""
    if details.get('type') == 'series' and details.get('episode'):
        return f"Episode {details['episode']}"
    
    if details.get('resolution'):
        return details['resolution'].upper()
    
    return "Download"

def natural_sort_key(s: str):
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]

async def create_post(client, user_id, messages):
    user = await get_user(user_id)
    if not user: return None, None, None
    
    bot_username = client.me.username
    
    all_details = []
    for m in messages:
        details = extract_file_details(getattr(m, m.media.value).file_name)
        details['message_obj'] = m
        all_details.append(details)

    all_details.sort(key=lambda d: natural_sort_key(d['original_name']))

    base_details = all_details[0]
    title = base_details['clean_title']
    year = base_details['year']
    is_series = any(d['type'] == 'series' for d in all_details)
    
    seasons_in_batch = sorted(list(set(d['season'] for d in all_details if d['season'])))
    is_multi_season = len(seasons_in_batch) > 1

    header = f"🎬 **{title}**"
    if year: header += f" **({year})**"
    if is_series and not is_multi_season and seasons_in_batch:
        header += f" **S{seasons_in_batch[0]:02d} Complete**"
    elif is_multi_season:
         header += f" **(S{seasons_in_batch[0]:02d} - S{seasons_in_batch[-1]:02d})**"
         
    post_poster = await get_poster(base_details['clean_title'], base_details['year'], base_details['original_title'])

    links = ""
    last_season = None
    for details in all_details:
        message_obj = details['message_obj']
        file_unique_id = getattr(message_obj, message_obj.media.value).file_unique_id

        if is_multi_season and details['season'] != last_season:
            links += f"\n**{title} S{details['season']:02d}**\n"
            last_season = details['season']

        link_label = create_link_label(details)
        payload = f"get_{file_unique_id}"
        bot_redirect_link = f"https://t.me/{bot_username}?start={payload}"
        links += f"📤 **{link_label}** ➠ [Watch / Download]({bot_redirect_link})\n"

    separator = "✯ ━━━━━━ ✧ ━━━━━━ ✯"
    final_caption = f"{header}\n\n{separator}\n\n{links.strip()}\n\n{separator}"
    
    footer_buttons_data = user.get('footer_buttons', [])
    footer_keyboard = None
    if footer_buttons_data:
        buttons = [[InlineKeyboardButton(btn['name'], url=btn['url'])] for btn in footer_buttons_data]
        footer_keyboard = InlineKeyboardMarkup(buttons)
        
    return post_poster, final_caption, footer_keyboard

async def get_main_menu(user_id):
    user_settings = await get_user(user_id)
    if not user_settings: return InlineKeyboardMarkup([])
    shortener_text = "⚙️ Shortener Settings" if user_settings.get('shortener_url') else "🔗 Set Shortener"
    fsub_text = "⚙️ Manage FSub" if user_settings.get('fsub_channel') else "📢 Set FSub"
    buttons = [
        [InlineKeyboardButton("➕ Manage Auto Post", callback_data="manage_post_ch")],
        [InlineKeyboardButton("🗃️ Manage Index DB", callback_data="manage_db_ch")],
        [InlineKeyboardButton(shortener_text, callback_data="shortener_menu"), InlineKeyboardButton("🔄 Backup Links", callback_data="backup_links")],
        [InlineKeyboardButton("🔗 Set Filename Link", callback_data="set_filename_link"), InlineKeyboardButton("👣 Footer Buttons", callback_data="manage_footer")],
        [InlineKeyboardButton("🖼️ IMDb Poster", callback_data="poster_menu"), InlineKeyboardButton("📂 My Files", callback_data="my_files_1")],
        [InlineKeyboardButton(fsub_text, callback_data="set_fsub")],
        [InlineKeyboardButton("❓ How to Download", callback_data="set_download")]
    ]
    if user_id == Config.ADMIN_ID:
        buttons.append([InlineKeyboardButton("🔑 Set Owner DB", callback_data="set_owner_db")])
        buttons.append([InlineKeyboardButton("⚠️ Reset Files DB", callback_data="reset_db_prompt")])
    return InlineKeyboardMarkup(buttons)

def go_back_button(user_id):
    return InlineKeyboardMarkup([[InlineKeyboardButton("« Go Back", callback_data=f"go_back_{user_id}")]])
