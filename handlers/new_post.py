import asyncio
import re
import logging
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, ChatAdminRequired, UserNotParticipant
from config import Config
from database.db import get_user, find_owner_by_db_channel
from utils.helpers import create_post, extract_file_details

logger = logging.getLogger(__name__)

def get_batch_key(filename: str):
    """
    Creates a batching key based on the extracted title and year.
    This groups all files of a movie/series together, including different seasons.
    e.g., "Mirzapur (2018)" becomes the key for S01, S02, etc.
    """
    details = extract_file_details(filename)
    title = details.get('title', 'untitled')
    year = details.get('year', '0000')
    
    # For series, we group by title and year to batch all seasons together
    if details.get('type') == 'series':
        return f"{title}_{year}".lower()
        
    # For movies, we also group by title and year
    return f"{title}_{year}".lower()

@Client.on_message(filters.channel & (filters.document | filters.video | filters.audio), group=2)
async def new_file_handler(client, message):
    """
    This handler now uses the globally available client.owner_db_channel_id
    set by the bot at startup. The logic is self-contained in the worker.
    """
    try:
        # Determine the owner of the content based on the DB channel it came from
        user_id = await find_owner_by_db_channel(message.chat.id)
        if not user_id: 
            # If not found, maybe it's the admin posting in their own channel
            if message.chat.id == client.owner_db_channel_id and Config.ADMIN_ID:
                 user_id = Config.ADMIN_ID
            else:
                return # Not a channel we are tracking for any user

        media = getattr(message, message.media.value, None)
        if not media or not getattr(media, 'file_name', None):
            return
        
        # The worker (in bot.py) now handles copying to the owner DB and saving data
        # We just need to queue the original message and its owner
        await client.file_queue.put((message, user_id))
        logger.info(f"Added file '{media.file_name}' to the processing queue for user {user_id}.")

    except Exception as e:
        logger.exception(f"Error in new_file_handler while adding to queue: {e}")

# Note: The process_batch logic has been fully moved into bot.py's worker
# and the create_post helper in helpers.py. This file is now just for
# capturing new messages and adding them to the queue.
