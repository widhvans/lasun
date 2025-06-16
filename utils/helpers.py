import re
import base64
import logging
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database.db import get_user
from features.poster import get_poster

logger = logging.getLogger(__name__)

def extract_file_details(filename: str):
    """Final Boss of filename cleaning. Extracts the absolute cleanest details."""
    details = {'original_name': filename, 'clean_title': None, 'year': None, 'type': 'movie', 'season': None, 'episode': None, 'resolution': None}
    
    base_name = filename.rsplit('.', 1)[0]
    
    year_match = re.search(r'\b(19[89]\d|20\d{2})\b', base_name)
    if year_match:
        details['year'] = year_match.group(1)

    se_match = re.search(r'[sS](\d{1,2})[._ ]?[eE](\d{1,3})', base_name)
    if se_match:
        details.update({'type': 'series', 'season': int(se_match.group(1)), 'episode': int(se_match.group(2))})
    else:
        ep_match = re.search(r'\b(ep|episode|part)[\s._]?(\d{1,3})\b', base_name, re.IGNORECASE)
        if ep_match:
            details.update({'type': 'series', 'episode': int(ep_match.group(2))})
        season_match = re.search(r'\b(season|s)[\s._]?(\d{1,2})\b', base_name, re.IGNORECASE)
        if season_match:
            details.update({'type': 'series', 'season': int(season_match.group(2))})

    res_match = re.search(r'\b(2160p|1080p|720p|480p)\b', base_name, re.IGNORECASE)
    if res_match:
        details['resolution'] = res_match.group(1)

    title_strip = base_name
    stop_point_match = re.search(r'\b(19\d{2}|20\d{2}|[sS]\d{1,2}|E\d{1,3}|COMPLETE)\b', title_strip, re.IGNORECASE)
    if stop_point_match:
        title_strip = title_strip[:stop_point_match.start()]
    
    title_strip = re.sub(r'\[.*?\]', '', title_strip)
    title_strip = title_strip.replace('.', ' ').replace('_', ' ').strip()
    details['clean_title'] = ' '.join(title_strip.split())
    
    if not details['clean_title']:
        details['clean_title'] = base_name.replace('.', ' ')
        
    return details

def create_link_label(details: dict) -> str:
    """Creates a smart label for the download link."""
    if details['type'] == 'series' and details.get('episode'):
        return f"Episode {details['episode']:02d}"
    if details.get('resolution'):
        return f"{details['resolution']}"
    return "Download"

async def create_post(client, user_id, messages):
    user = await get_user(user_id)
    if not user: return None, None, None
    
    bot_username = client.me.username
    all_details = [extract_file_details(getattr(m, m.media.value).file_name) for m in messages]
    
    all_details.sort(key=lambda d: d.get('episode') or 0)
    
    base_details = all_details[0]
    title = base_details['clean_title']
    year = base_details['year']
    is_series = any(d['type'] == 'series' for d in all_details)
    
    final_links = {}
    for idx, details in enumerate(all_details):
        media = getattr(messages[idx], messages[idx].media.value)
        key = f"ep_{details.get('episode', 0)}" if is_series else details.get('resolution', 'SD')
        final_links[key] = {
            'label': create_link_label(details),
            'url': f"https://t.me/{bot_username}?start=get_{media.file_unique_id}"
        }
        
    header = f"🎬 **{title}**"
    if year: header += f" **({year})**"
    
    post_poster = await get_poster(title, year)

    links_text = ""
    sorted_keys = sorted(final_links.keys(), key=lambda x: (isinstance(x, str) and x.startswith('ep_'), x))
    for key in sorted_keys:
        link_info = final_links[key]
        links_text += f"✨ **{link_info['label']}** ➠ [Watch / Download]({link_info['url']})\n"
        
    separator = "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
    final_caption = f"{header}\n\nミ★ ᴅᴏᴡɴʟᴏᴀᴅ ʟɪɴᴋs ★彡\n{separator}\n\n{links_text.strip()}\n\n{separator}"
    
    footer_buttons_data = user.get('footer_buttons', [])
    footer_keyboard = None
    if footer_buttons_data:
        buttons = [[InlineKeyboardButton(btn['name'], url=btn['url'])] for btn in footer_buttons_data]
        footer_keyboard = InlineKeyboardMarkup(buttons)
        
    return post_poster, final_caption, footer_keyboard

def natural_sort_key(s: str):
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]

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

def encode_link(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().strip("=")

def decode_link(encoded_text: str) -> str:
    padding = 4 - (len(encoded_text) % 4)
    encoded_text += "=" * padding
    return base64.urlsafe_b64decode(encoded_text).decode()

async def get_file_raw_link(message):
    return f"https://t.me/c/{str(message.chat.id).replace('-100', '')}/{message.id}"
