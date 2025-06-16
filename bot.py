import logging
import sys
import os
import asyncio
from pyromod import Client
from aiohttp import web
from config import Config
from database.db import get_user, save_file_data, get_owner_db_channel, remove_from_list
from utils.helpers import create_post
from handlers.new_post import get_batch_key
from pyrogram.errors import (
    ChatAdminRequired, UserNotParticipant, FloodWait,
    WebpageCurlFailed, ChannelPrivate, WebpageMediaEmpty
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("pyromod").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# Web Server Handler
async def handle_redirect(request):
    file_unique_id = request.match_info.get('file_unique_id', None)
    if not file_unique_id:
        return web.Response(text="File ID missing.", status=400)
    try:
        with open(Config.BOT_USERNAME_FILE, 'r') as f:
            bot_username = f.read().strip().replace("@", "")
    except FileNotFoundError:
        logger.error(f"FATAL: Bot username file not found at {Config.BOT_USERNAME_FILE}")
        return web.Response(text="Bot configuration error.", status=500)
    payload = f"get_{file_unique_id}"
    telegram_url = f"https://t.me/{bot_username}?start={payload}"
    return web.HTTPFound(telegram_url)


class Bot(Client):
    def __init__(self):
        super().__init__(
            "FinalStorageBot",
            api_id=Config.API_ID, api_hash=Config.API_HASH, bot_token=Config.BOT_TOKEN,
            plugins=dict(root="handlers")
        )
        self.me = None
        self.owner_db_channel_id = None
        self.web_app = None
        self.web_runner = None
        self.file_queue = asyncio.Queue()
        self.file_batch = {}
        self.batch_locks = {}

    async def file_processor_worker(self):
        """The single worker that processes files from the queue one by one."""
        logger.info("File processor worker started.")
        while True:
            try:
                message_to_process, user_id = await self.file_queue.get()
                
                if not self.owner_db_channel_id:
                    self.owner_db_channel_id = await get_owner_db_channel()

                if not self.owner_db_channel_id:
                    logger.error("Owner DB not set. Worker is sleeping for 60s. Please set it via the admin panel.")
                    await asyncio.sleep(60)
                    await self.file_queue.put((message_to_process, user_id))
                    continue

                copied_message = await message_to_process.copy(chat_id=self.owner_db_channel_id)
                await save_file_data(owner_id=user_id, original_message=message_to_process, copied_message=copied_message)
                
                filename = getattr(copied_message, copied_message.media.value).file_name
                batch_key = get_batch_key(filename)

                if user_id not in self.batch_locks: self.batch_locks[user_id] = {}
                if batch_key not in self.batch_locks[user_id]:
                    self.batch_locks[user_id][batch_key] = asyncio.Lock()
                
                async with self.batch_locks[user_id][batch_key]:
                    if batch_key not in self.file_batch.setdefault(user_id, {}):
                        self.file_batch[user_id][batch_key] = [copied_message]
                        asyncio.create_task(self.process_batch_task(user_id, batch_key))
                    else:
                        self.file_batch[user_id][batch_key].append(copied_message)
                
                await asyncio.sleep(2)
            except Exception:
                logger.exception("Error in file processor worker")
            finally:
                self.file_queue.task_done()

    async def process_batch_task(self, user_id, batch_key):
        """The task that waits and posts a batch of files with smart error handling."""
        try:
            await asyncio.sleep(10)
            if user_id not in self.batch_locks or batch_key not in self.batch_locks.get(user_id, {}): return
            async with self.batch_locks[user_id][batch_key]:
                messages = self.file_batch[user_id].pop(batch_key, [])
                if not messages: return
                
                user = await get_user(user_id)
                if not user or not user.get('post_channels'): return

                poster, caption, footer_keyboard = await create_post(self, user_id, messages)
                if not caption: return
                
                # Iterate over a copy of the list to allow safe removal within the loop
                for channel_id in user.get('post_channels', []).copy():
                    try:
                        if poster:
                            await self.send_photo(channel_id, photo=poster, caption=caption, reply_markup=footer_keyboard)
                        else:
                            await self.send_message(channel_id, caption, reply_markup=footer_keyboard, disable_web_page_preview=True)
                        await asyncio.sleep(3) # Delay between posts to avoid flood waits

                    except (ChannelPrivate, ChatAdminRequired, UserNotParticipant):
                        logger.error(f"PERMISSION ERROR in channel {channel_id} for user {user_id}. Removing channel from settings.")
                        # Automatically remove the problematic channel
                        await remove_from_list(user_id, 'post_channels', channel_id)
                        # Inform the user
                        await self.send_message(
                            user_id,
                            f"⚠️ **Auto-Posting Disabled for Channel**\n\n"
                            f"I failed to post to the channel with ID `{channel_id}` because I am no longer an admin there or the channel is private.\n\n"
                            f"I have **automatically removed it** from your post channels to prevent further errors.\n\n"
                            f"**To fix this:**\n1. Make me an admin in that channel again.\n2. Re-add the channel in `Settings > Manage Auto Post`."
                        )
                    except (WebpageCurlFailed, WebpageMediaEmpty):
                        logger.warning(f"Failed to send poster to channel {channel_id} (URL invalid or empty). Sending post as text-only.")
                        # Fallback to sending as a text message if the poster URL is bad
                        await self.send_message(channel_id, caption, reply_markup=footer_keyboard, disable_web_page_preview=True)
                    except FloodWait as e:
                        logger.warning(f"FloodWait in channel {channel_id}. Sleeping for {e.value} seconds.")
                        await asyncio.sleep(e.value)
                        # Retry after the flood wait
                        await self.send_photo(channel_id, photo=poster, caption=caption, reply_markup=footer_keyboard)
                    except Exception as e:
                        logger.error(f"An unexpected error occurred while posting to {channel_id} for user {user_id}: {e}")
                        try:
                            # Notify user of other unexpected errors
                            await self.send_message(user_id, f"An unexpected error occurred while posting to channel `{channel_id}`.\n\n`{e}`")
                        except Exception:
                             logger.error(f"Failed to send error notification to user {user_id}")

        except Exception:
            logger.exception(f"A major error occurred in process_batch_task for user {user_id}")
        finally:
            # Clean up locks and batch data
            if user_id in self.batch_locks and batch_key in self.batch_locks.get(user_id, {}):
                del self.batch_locks[user_id][batch_key]
            if user_id in self.file_batch and not self.file_batch.get(user_id, {}):
                del self.file_batch[user_id]
            if user_id in self.batch_locks and not self.batch_locks.get(user_id, {}):
                del self.batch_locks[user_id]

    async def start_web_server(self):
        self.web_app = web.Application()
        self.web_app.router.add_get('/get/{file_unique_id}', handle_redirect)
        self.web_runner = web.AppRunner(self.web_app)
        await self.web_runner.setup()
        site = web.TCPSite(self.web_runner, Config.VPS_IP, Config.VPS_PORT)
        await site.start()
        logger.info(f"Web redirector server started at http://{Config.VPS_IP}:{Config.VPS_PORT}")

    async def start(self):
        await super().start()
        self.me = await self.get_me()
        
        self.owner_db_channel_id = await get_owner_db_channel()
        if self.owner_db_channel_id:
            logger.info(f"Successfully loaded Owner Database ID [{self.owner_db_channel_id}] from database.")
        else:
            logger.warning("Owner Database ID is not yet set. Please use the 'Set Owner DB' button as admin.")

        try:
            with open(Config.BOT_USERNAME_FILE, 'w') as f:
                f.write(f"@{self.me.username}")
            logger.info(f"Successfully updated bot username to @{self.me.username} in {Config.BOT_USERNAME_FILE}")
        except Exception as e:
            logger.error(f"Could not write to {Config.BOT_USERNAME_FILE}. Error: {e}")
        
        asyncio.create_task(self.file_processor_worker())
        await self.start_web_server()
        logger.info(f"Bot @{self.me.username} and all services started successfully.")

    async def stop(self, *args):
        logger.info("Stopping bot and web server...")
        if self.web_runner:
            await self.web_runner.cleanup()
        await super().stop()
        logger.info("Bot stopped.")

if __name__ == "__main__":
    Bot().run()
