import asyncio
import re
import logging
from pyrogram import Client, filters
from config import Config
from database.db import find_owner_by_db_channel
from utils.helpers import extract_file_details

logger = logging.getLogger(__name__)

def get_batch_key(filename: str):
    """Creates a batching key based on the 'clean_title' and year for perfect grouping."""
    details = extract_file_details(filename)
    title = details.get('clean_title', 'untitled').strip()
    year = details.get('year', '0000')
    return f"{title}_{year}".lower()

@Client.on_message(filters.channel & (filters.document | filters.video | filters.audio), group=2)
async def new_file_handler(client, message):
    """Adds incoming files to the processing queue."""
    try:
        user_id = await find_owner_by_db_channel(message.chat.id)
        if not user_id: 
            if hasattr(client, 'owner_db_channel_id') and message.chat.id == client.owner_db_channel_id and Config.ADMIN_ID:
                 user_id = Config.ADMIN_ID
            else:
                return

        media = getattr(message, message.media.value, None)
        if not media or not getattr(media, 'file_name', None):
            return
        
        await client.file_queue.put((message, user_id))
        logger.info(f"Added file '{media.file_name}' to the processing queue for user {user_id}.")

    except Exception as e:
        logger.exception(f"Error in new_file_handler while adding to queue: {e}")
