import logging
import os
import asyncio
from pyromod import Client
from aiohttp import web
from config import Config
from database.db import get_user, save_file_data, get_owner_db_channel, remove_from_list
from utils.helpers import create_post, get_main_menu
from handlers.new_post import get_batch_key
from pyrogram.errors import (
    ChatAdminRequired, UserNotParticipant, FloodWait,
    WebpageCurlFailed, ChannelPrivate, WebpageMediaEmpty
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()])
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("pyromod").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

async def handle_redirect(request):
    file_unique_id = request.match_info.get('file_unique_id', None)
    if not file_unique_id: return web.Response(text="File ID missing.", status=400)
    try:
        with open(Config.BOT_USERNAME_FILE, 'r') as f: bot_username = f.read().strip().replace("@", "")
    except FileNotFoundError:
        logger.error(f"FATAL: Bot username file not found at {Config.BOT_USERNAME_FILE}")
        return web.Response(text="Bot configuration error.", status=500)
    payload = f"get_{file_unique_id}"
    telegram_url = f"https://t.me/{bot_username}?start={payload}"
    return web.HTTPFound(telegram_url)

class Bot(Client):
    def __init__(self):
        super().__init__("FinalStorageBot", api_id=Config.API_ID, api_hash=Config.API_HASH, bot_token=Config.BOT_TOKEN, plugins=dict(root="handlers"))
        self.me = None
        self.owner_db_channel_id = None
        self.web_app = None
        self.web_runner = None
        self.file_queue = asyncio.Queue()
        self.file_batch = {}
        self.batch_locks = {}

    async def file_processor_worker(self):
        logger.info("File processor worker started.")
        while True:
            try:
                message_to_process, user_id = await self.file_queue.get()
                user = await get_user(user_id)
                if not user or not user.get('db_channels') or not user.get('post_channels'):
                    logger.warning(f"User {user_id} has not completed setup. Sending guidance.")
                    try:
                        await self.send_message(user_id, "Hello! To get started, please use `/start` to set up your Database and Post Channels.", reply_markup=await get_main_menu(user_id))
                    except Exception as e:
                        logger.error(f"Failed to send setup guidance to user {user_id}: {e}")
                    self.file_queue.task_done()
                    continue
                
                if not self.owner_db_channel_id: self.owner_db_channel_id = await get_owner_db_channel()
                if not self.owner_db_channel_id:
                    logger.error("Owner DB not set. Worker sleeping.")
                    await asyncio.sleep(60)
                    await self.file_queue.put((message_to_process, user_id))
                    continue

                copied_message = await message_to_process.copy(chat_id=self.owner_db_channel_id)
                await save_file_data(owner_id=user_id, original_message=message_to_process, copied_message=copied_message)
                
                filename = getattr(copied_message, copied_message.media.value).file_name
                batch_key = get_batch_key(filename)

                if user_id not in self.batch_locks: self.batch_locks[user_id] = {}
                if batch_key not in self.batch_locks[user_id]: self.batch_locks[user_id][batch_key] = asyncio.Lock()
                
                async with self.batch_locks[user_id][batch_key]:
                    if batch_key not in self.file_batch.setdefault(user_id, {}):
                        self.file_batch[user_id][batch_key] = [copied_message]
                        asyncio.create_task(self.process_batch_task(user_id, batch_key))
                    else:
                        self.file_batch[user_id][batch_key].append(copied_message)
            except Exception:
                logger.exception("Error in file processor worker")
            finally:
                if 'self.file_queue' in locals() and self.file_queue: self.file_queue.task_done()

    async def process_batch_task(self, user_id, batch_key):
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
                
                for channel_id in user.get('post_channels', []).copy():
                    try:
                        if poster:
                            await self.send_photo(channel_id, photo=poster, caption=caption, reply_markup=footer_keyboard)
                        else: # This path should ideally never be taken now
                            await self.send_message(channel_id, caption, reply_markup=footer_keyboard, disable_web_page_preview=True)
                        await asyncio.sleep(3)
                    except FloodWait as e:
                        logger.warning(f"FloodWait in {channel_id}. Sleeping for {e.value}s.")
                        await asyncio.sleep(e.value)
                        await self.send_photo(channel_id, photo=poster, caption=caption, reply_markup=footer_keyboard) # Retry
                    except (ChannelPrivate, ChatAdminRequired, UserNotParticipant):
                        logger.error(f"PERMISSION ERROR in {channel_id} for user {user_id}. Removing channel.")
                        await remove_from_list(user_id, 'post_channels', channel_id)
                        await self.send_message(user_id, f"⚠️ **Auto-Posting Disabled**\nI failed to post to channel ID `{channel_id}` because I'm not an admin or it's private. I've removed it from your settings.")
                    except (WebpageCurlFailed, WebpageMediaEmpty) as e:
                        logger.warning(f"Poster URL failed for {channel_id}: {e}. Sending as text.")
                        await self.send_message(channel_id, caption, reply_markup=footer_keyboard, disable_web_page_preview=True)
                    except Exception as e:
                        logger.error(f"Unexpected error posting to {channel_id} for user {user_id}: {e}")
                        try:
                            await self.send_message(user_id, f"An error occurred posting to channel `{channel_id}`: {e}")
                        except Exception:
                             logger.error(f"Failed to send error notification to user {user_id}")
        except Exception:
            logger.exception(f"Major error in process_batch_task for user {user_id}")
        finally:
            if user_id in self.batch_locks and batch_key in self.batch_locks.get(user_id, {}): del self.batch_locks[user_id][batch_key]

    async def start_web_server(self):
        self.web_app = web.Application()
        self.web_app.router.add_get('/get/{file_unique_id}', handle_redirect)
        self.web_runner = web.AppRunner(self.web_app)
        await self.web_runner.setup()
        site = web.TCPSite(self.web_runner, Config.VPS_IP, Config.VPS_PORT)
        await site.start()
        logger.info(f"Web server started at http://{Config.VPS_IP}:{Config.VPS_PORT}")

    async def start(self):
        await super().start()
        self.me = await self.get_me()
        
        # Create resources directory if it doesn't exist
        os.makedirs("resources", exist_ok=True)
        # Download a default font for the fallback image generator
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://github.com/google/fonts/raw/main/ofl/opensans/OpenSans-Bold.ttf") as resp:
                    if resp.status == 200:
                        with open("./resources/font.ttf", "wb") as f:
                            f.write(await resp.read())
        except Exception as e:
            logger.warning(f"Could not download fallback font, will use default. Error: {e}")


        self.owner_db_channel_id = await get_owner_db_channel()
        if self.owner_db_channel_id: logger.info(f"Loaded Owner DB ID [{self.owner_db_channel_id}]")
        else: logger.warning("Owner DB ID not set. Please use admin panel.")
        
        try:
            with open(Config.BOT_USERNAME_FILE, 'w') as f: f.write(f"@{self.me.username}")
        except Exception as e:
            logger.error(f"Could not write to {Config.BOT_USERNAME_FILE}: {e}")
        
        asyncio.create_task(self.file_processor_worker())
        await self.start_web_server()
        logger.info(f"Bot @{self.me.username} started successfully.")

    async def stop(self, *args):
        logger.info("Stopping bot...")
        if self.web_runner: await self.web_runner.cleanup()
        await super().stop()
        logger.info("Bot stopped.")

if __name__ == "__main__":
    Bot().run()
