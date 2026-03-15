"""
Soviet Russia Life Simulator - Discord Bot
Enterprise-Grade Moderation & Management System

Author: SRLS Development Team
Version: 1.0.0
Python: 3.10+
Framework: discord.py v2.3+
Database: aiosqlite (SQLite with WAL)
Deployment: Railway-optimized
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite
import asyncio
import logging
import sys
import traceback
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Union
import json
import re
from collections import defaultdict
import time
from functools import wraps
import aiohttp
from io import BytesIO

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION & CONSTANTS
# ═══════════════════════════════════════════════════════════════

DATABASE_PATH = "srls_bot.db"  # Hardcoded as per user request
DEFAULT_PREFIX = "!"
BOT_VERSION = "1.0.0"
COMMAND_COOLDOWN = 3  # seconds
RATE_LIMIT_COMMANDS = 5
RATE_LIMIT_WINDOW = 10  # seconds

# Emoji fallbacks (Unicode)
EMOJI_FALLBACK = {
    "success": "✅",
    "error": "❌",
    "warning": "⚠️",
    "info": "ℹ️",
    "hammer": "🔨",
    "gear": "⚙️",
    "alert": "🚨",
    "military": "🪖",
    "lock": "🔒",
    "unlock": "🔓",
    "ban": "🔨",
    "kick": "👢",
    "mute": "🔇",
    "unmute": "🔊",
    "case": "📋",
    "stats": "📊",
    "user": "👤",
    "calendar": "📅",
    "ping": "🏓",
    "shield": "🛡️",
    "book": "📚",
    "search": "🔍"
}

# ═══════════════════════════════════════════════════════════════
# LOGGING SETUP
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger('SRLSBot')

# ═══════════════════════════════════════════════════════════════
# DATABASE MANAGER
# ═══════════════════════════════════════════════════════════════

class DatabaseManager:
    """Manages all database operations with connection pooling and error handling"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db: Optional[aiosqlite.Connection] = None
        self._connection_attempts = 0
        self._max_retries = 3
        
    async def connect(self):
        """Establish database connection with retry logic"""
        for attempt in range(self._max_retries):
            try:
                logger.info("Database: Connecting...")
                self.db = await aiosqlite.connect(self.db_path)
                
                # Enable WAL mode for concurrent access
                await self.db.execute("PRAGMA journal_mode=WAL")
                await self.db.execute("PRAGMA synchronous=NORMAL")
                await self.db.execute("PRAGMA cache_size=-64000")  # 64MB cache
                await self.db.execute("PRAGMA temp_store=MEMORY")
                
                self.db.row_factory = aiosqlite.Row
                logger.info("Database: Connected successfully")
                return True
            except Exception as e:
                logger.error(f"Database connection attempt {attempt + 1} failed: {e}")
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                else:
                    logger.critical("Database: Failed to connect after all retries")
                    raise
        return False
    
    async def initialize_tables(self):
        """Create all required database tables with indexes"""
        logger.info("Database: Initializing tables...")
        
        tables = [
            # Prefixes table
            """CREATE TABLE IF NOT EXISTS prefixes (
                guild_id INTEGER PRIMARY KEY,
                prefix TEXT DEFAULT '!',
                created_at TEXT,
                updated_at TEXT
            )""",
            
            # Warnings table
            """CREATE TABLE IF NOT EXISTS warns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                user_id INTEGER,
                moderator_id INTEGER,
                reason TEXT,
                timestamp TEXT,
                active INTEGER DEFAULT 1
            )""",
            
            # Moderation logs
            """CREATE TABLE IF NOT EXISTS modlogs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                action TEXT,
                target_id INTEGER,
                moderator_id INTEGER,
                reason TEXT,
                timestamp TEXT
            )""",
            
            # Cases table
            """CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                guild_id INTEGER,
                action TEXT,
                target_id INTEGER,
                moderator_id INTEGER,
                reason TEXT,
                timestamp TEXT,
                evidence_url TEXT
            )""",
            
            # Settings table
            """CREATE TABLE IF NOT EXISTS settings (
                guild_id INTEGER PRIMARY KEY,
                mod_role_id INTEGER,
                mod_user_ids TEXT,
                log_channel_id INTEGER,
                antispam_enabled INTEGER DEFAULT 0,
                antispam_sensitivity INTEGER DEFAULT 5,
                auto_mod_enabled INTEGER DEFAULT 1,
                custom_emojis TEXT,
                timezone TEXT DEFAULT 'UTC',
                language TEXT DEFAULT 'en'
            )""",
            
            # Anti-spam tracking
            """CREATE TABLE IF NOT EXISTS spam_tracking (
                user_id INTEGER,
                guild_id INTEGER,
                message_count INTEGER,
                last_message_time REAL,
                warnings INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, guild_id)
            )""",
            
            # Active mutes/timeouts
            """CREATE TABLE IF NOT EXISTS active_mutes (
                guild_id INTEGER,
                user_id INTEGER,
                unmute_time TEXT,
                reason TEXT,
                PRIMARY KEY (guild_id, user_id)
            )""",
            
            # Audit log
            """CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                event_type TEXT,
                user_id INTEGER,
                details TEXT,
                timestamp TEXT
            )""",
        ]
        
        try:
            for table_sql in tables:
                await self.db.execute(table_sql)
            
            # Create indexes
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_warns_guild_user ON warns(guild_id, user_id)",
                "CREATE INDEX IF NOT EXISTS idx_modlogs_guild ON modlogs(guild_id)",
                "CREATE INDEX IF NOT EXISTS idx_modlogs_target ON modlogs(target_id)",
                "CREATE INDEX IF NOT EXISTS idx_cases_guild ON cases(guild_id)",
                "CREATE INDEX IF NOT EXISTS idx_audit_guild ON audit_log(guild_id)"
            ]
            
            for index_sql in indexes:
                await self.db.execute(index_sql)
            
            await self.db.commit()
            logger.info("Database: Tables initialized successfully")
        except Exception as e:
            logger.error(f"Database: Table initialization failed: {e}")
            raise
    
    async def close(self):
        """Close database connection gracefully"""
        if self.db:
            await self.db.close()
            logger.info("Database: Connection closed")

# ═══════════════════════════════════════════════════════════════
# CACHE MANAGER
# ═══════════════════════════════════════════════════════════════

class CacheManager:
    """Manages in-memory caching for performance optimization"""
    
    def __init__(self):
        self.prefixes: Dict[int, str] = {}
        self.emojis: Dict[int, Dict[str, str]] = {}
        self.settings: Dict[int, Dict[str, Any]] = {}
        self.last_refresh = time.time()
        self.refresh_interval = 300  # 5 minutes
    
    def should_refresh(self) -> bool:
        """Check if cache should be refreshed"""
        return (time.time() - self.last_refresh) > self.refresh_interval
    
    def clear(self):
        """Clear all cache"""
        self.prefixes.clear()
        self.emojis.clear()
        self.settings.clear()
        self.last_refresh = time.time()
        logger.debug("Cache: Cleared all caches")

# ═══════════════════════════════════════════════════════════════
# EMBED FACTORY
# ═══════════════════════════════════════════════════════════════

class EmbedFactory:
    """Standardized embed creation with consistent styling"""
    
    SUCCESS_COLOR = 0x00C853
    ERROR_COLOR = 0xD32F2F
    WARNING_COLOR = 0xFFC107
    INFO_COLOR = 0x2196F3
    
    @staticmethod
    async def create(
        title: str,
        description: str,
        color: int,
        emoji: str = None,
        thumbnail: str = None,
        fields: List[Dict[str, Any]] = None,
        footer: str = "Soviet Russia Life Simulator • Official Bot",
        timestamp: bool = True,
        author: Dict[str, str] = None
    ) -> discord.Embed:
        """Create a standardized embed"""
        
        # Add emoji to title if provided
        if emoji:
            title = f"{emoji} {title}"
        
        embed = discord.Embed(
            title=title,
            description=description,
            color=color
        )
        
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        
        if fields:
            for field in fields:
                embed.add_field(
                    name=field.get("name", "Field"),
                    value=field.get("value", "N/A"),
                    inline=field.get("inline", False)
                )
        
        if footer:
            embed.set_footer(text=footer)
        
        if timestamp:
            embed.timestamp = datetime.utcnow()
        
        if author:
            embed.set_author(
                name=author.get("name", ""),
                icon_url=author.get("icon_url", "")
            )
        
        return embed

# ═══════════════════════════════════════════════════════════════
# RATE LIMITER
# ═══════════════════════════════════════════════════════════════

class RateLimiter:
    """Rate limiting for commands to prevent abuse"""
    
    def __init__(self):
        self.user_commands: Dict[int, List[float]] = defaultdict(list)
    
    def is_rate_limited(self, user_id: int) -> bool:
        """Check if user is rate limited"""
        now = time.time()
        
        # Clean old entries
        self.user_commands[user_id] = [
            ts for ts in self.user_commands[user_id]
            if now - ts < RATE_LIMIT_WINDOW
        ]
        
        # Check if rate limited
        if len(self.user_commands[user_id]) >= RATE_LIMIT_COMMANDS:
            return True
        
        # Add current command
        self.user_commands[user_id].append(now)
        return False

# ═══════════════════════════════════════════════════════════════
# ANTI-SPAM DETECTOR
# ═══════════════════════════════════════════════════════════════

class AntiSpamDetector:
    """Intelligent spam detection system"""
    
    def __init__(self):
        self.message_cache: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        self.spam_strikes: Dict[int, int] = defaultdict(int)
    
    async def check_message(self, message: discord.Message, sensitivity: int = 5) -> tuple[bool, str]:
        """
        Check if message is spam
        Returns: (is_spam, reason)
        """
        user_id = message.author.id
        now = time.time()
        
        # Clean old messages (older than 10 seconds)
        self.message_cache[user_id] = [
            msg for msg in self.message_cache[user_id]
            if now - msg['timestamp'] < 10
        ]
        
        # Add current message
        self.message_cache[user_id].append({
            'content': message.content,
            'timestamp': now
        })
        
        recent_messages = self.message_cache[user_id]
        
        # Check 1: Message frequency (5+ messages in 5 seconds)
        recent_5s = [msg for msg in recent_messages if now - msg['timestamp'] < 5]
        if len(recent_5s) >= 5:
            return True, "Message frequency too high"
        
        # Check 2: Duplicate content (same message 3+ times)
        content_counts = defaultdict(int)
        for msg in recent_messages:
            content_counts[msg['content']] += 1
        
        for count in content_counts.values():
            if count >= 3:
                return True, "Duplicate messages"
        
        # Check 3: Mass mentions (5+ mentions)
        if len(message.mentions) >= 5:
            return True, "Mass mentions"
        
        # Check 4: Excessive caps (70%+ uppercase)
        if len(message.content) > 10:
            caps_ratio = sum(1 for c in message.content if c.isupper()) / len(message.content)
            if caps_ratio > 0.7:
                return True, "Excessive caps"
        
        # Check 5: Excessive emojis (10+ emojis)
        emoji_count = len(re.findall(r'<:\w+:\d+>|[\U0001F600-\U0001F64F]', message.content))
        if emoji_count >= 10:
            return True, "Excessive emojis"
        
        # Check 6: Link spam (3+ links in 10 seconds)
        link_pattern = r'https?://\S+'
        total_links = sum(len(re.findall(link_pattern, msg['content'])) for msg in recent_messages)
        if total_links >= 3:
            return True, "Link spam"
        
        return False, ""
    
    def add_strike(self, user_id: int) -> int:
        """Add spam strike and return total strikes"""
        self.spam_strikes[user_id] += 1
        return self.spam_strikes[user_id]
    
    def reset_strikes(self, user_id: int):
        """Reset spam strikes for user"""
        self.spam_strikes[user_id] = 0

# ═══════════════════════════════════════════════════════════════
# BOT CLASS
# ═══════════════════════════════════════════════════════════════

class SRLSBot(commands.Bot):
    """Main bot class with hybrid command support"""
    
    def __init__(self):
        intents = discord.Intents.all()
        
        super().__init__(
            command_prefix=self.get_prefix,
            intents=intents,
            help_command=None,  # Custom help command
            case_insensitive=True,
            strip_after_prefix=True
        )
        
        self.db_manager = DatabaseManager(DATABASE_PATH)
        self.cache = CacheManager()
        self.rate_limiter = RateLimiter()
        self.antispam = AntiSpamDetector()
        self.start_time = datetime.utcnow()
        self.command_stats = defaultdict(int)
        
    async def get_prefix(self, message: discord.Message) -> List[str]:
        """Get guild-specific prefix with caching"""
        if not message.guild:
            return [DEFAULT_PREFIX]
        
        guild_id = message.guild.id
        
        # Check cache first
        if guild_id in self.cache.prefixes:
            return commands.when_mentioned_or(self.cache.prefixes[guild_id])(self, message)
        
        # Query database
        try:
            async with self.db_manager.db.execute(
                "SELECT prefix FROM prefixes WHERE guild_id = ?",
                (guild_id,)
            ) as cursor:
                row = await cursor.fetchone()
                prefix = row['prefix'] if row else DEFAULT_PREFIX
                
                # Cache it
                self.cache.prefixes[guild_id] = prefix
                return commands.when_mentioned_or(prefix)(self, message)
        except Exception as e:
            logger.error(f"Error fetching prefix for guild {guild_id}: {e}")
            return commands.when_mentioned_or(DEFAULT_PREFIX)(self, message)
    
    async def get_emoji(self, guild_id: int, emoji_name: str) -> str:
        """Get custom emoji or fallback to unicode"""
        # Check cache
        if guild_id in self.cache.emojis and emoji_name in self.cache.emojis[guild_id]:
            return self.cache.emojis[guild_id][emoji_name]
        
        # Try database
        try:
            async with self.db_manager.db.execute(
                "SELECT custom_emojis FROM settings WHERE guild_id = ?",
                (guild_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row and row['custom_emojis']:
                    emojis = json.loads(row['custom_emojis'])
                    self.cache.emojis[guild_id] = emojis
                    return emojis.get(emoji_name, EMOJI_FALLBACK.get(emoji_name, ""))
        except Exception as e:
            logger.error(f"Error fetching emoji: {e}")
        
        # Fallback
        return EMOJI_FALLBACK.get(emoji_name, "")
    
    async def setup_hook(self):
        """Initialize bot components"""
        # Connect to database
        await self.db_manager.connect()
        await self.db_manager.initialize_tables()
        
        # Sync slash commands
        logger.info("Commands: Syncing slash commands...")
        try:
            synced = await self.tree.sync()
            logger.info(f"Commands: Synced {len(synced)} slash commands")
        except Exception as e:
            logger.error(f"Commands: Slash sync failed: {e}")
    
    async def close(self):
        """Graceful shutdown"""
        logger.info("Bot: Shutting down gracefully...")
        await self.db_manager.close()
        await super().close()

# Initialize bot instance
bot = SRLSBot()

# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

async def parse_duration(duration_str: str) -> Optional[timedelta]:
    """Parse duration string (10s, 5m, 2h, 1d, 1w) to timedelta"""
    pattern = r'(\d+)([smhdw])'
    match = re.match(pattern, duration_str.lower())
    
    if not match:
        return None
    
    amount, unit = match.groups()
    amount = int(amount)
    
    if unit == 's':
        return timedelta(seconds=amount)
    elif unit == 'm':
        return timedelta(minutes=amount)
    elif unit == 'h':
        return timedelta(hours=amount)
    elif unit == 'd':
        return timedelta(days=amount)
    elif unit == 'w':
        return timedelta(weeks=amount)
    
    return None

async def generate_case_id(guild_id: int) -> str:
    """Generate unique case ID in format SRLS-XXXX"""
    try:
        async with bot.db_manager.db.execute(
            "SELECT COUNT(*) as count FROM cases WHERE guild_id = ?",
            (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            count = row['count'] + 1 if row else 1
            return f"SRLS-{count:04d}"
    except Exception as e:
        logger.error(f"Error generating case ID: {e}")
        return f"SRLS-{int(time.time()) % 10000:04d}"

async def log_moderation_action(
    guild_id: int,
    action: str,
    target_id: int,
    moderator_id: int,
    reason: str,
    evidence_url: str = None
):
    """Log moderation action to database"""
    try:
        case_id = await generate_case_id(guild_id)
        timestamp = datetime.utcnow().isoformat()
        
        # Insert into cases table
        await bot.db_manager.db.execute(
            """INSERT INTO cases 
            (case_id, guild_id, action, target_id, moderator_id, reason, timestamp, evidence_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (case_id, guild_id, action, target_id, moderator_id, reason, timestamp, evidence_url)
        )
        
        # Insert into modlogs table
        await bot.db_manager.db.execute(
            """INSERT INTO modlogs 
            (guild_id, action, target_id, moderator_id, reason, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (guild_id, action, target_id, moderator_id, reason, timestamp)
        )
        
        await bot.db_manager.db.commit()
        return case_id
    except Exception as e:
        logger.error(f"Error logging moderation action: {e}")
        return None

async def send_log_embed(guild: discord.Guild, embed: discord.Embed):
    """Send embed to configured log channel"""
    try:
        async with bot.db_manager.db.execute(
            "SELECT log_channel_id FROM settings WHERE guild_id = ?",
            (guild.id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row['log_channel_id']:
                channel = guild.get_channel(row['log_channel_id'])
                if channel:
                    await channel.send(embed=embed)
    except Exception as e:
        logger.error(f"Error sending log embed: {e}")

async def has_mod_permissions(ctx, user: discord.Member = None) -> bool:
    """Check if user has moderator permissions"""
    if user is None:
        user = ctx.author
    
    # Bot owner bypass
    if await bot.is_owner(user):
        return True
    
    # Guild owner bypass
    if ctx.guild.owner_id == user.id:
        return True
    
    # Administrator bypass
    if user.guild_permissions.administrator:
        return True
    
    # Check configured mod role
    try:
        async with bot.db_manager.db.execute(
            "SELECT mod_role_id, mod_user_ids FROM settings WHERE guild_id = ?",
            (ctx.guild.id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                # Check mod role
                if row['mod_role_id']:
                    mod_role = ctx.guild.get_role(row['mod_role_id'])
                    if mod_role and mod_role in user.roles:
                        return True
                
                # Check mod users
                if row['mod_user_ids']:
                    mod_users = json.loads(row['mod_user_ids'])
                    if user.id in mod_users:
                        return True
    except Exception as e:
        logger.error(f"Error checking mod permissions: {e}")
    
    return False

# ═══════════════════════════════════════════════════════════════
# EVENT HANDLERS
# ═══════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    """Bot ready event"""
    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info("  SOVIET RUSSIA LIFE SIMULATOR - DISCORD BOT")
    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info(f"Bot: Logged in as {bot.user.name}#{bot.user.discriminator} (ID: {bot.user.id})")
    logger.info(f"Bot: Connected to {len(bot.guilds)} guilds")
    
    total_users = sum(guild.member_count for guild in bot.guilds)
    logger.info(f"Bot: Serving {total_users:,} users")
    
    # Start background tasks
    logger.info("Tasks: Starting status rotation...")
    rotate_status.start()
    
    logger.info("Tasks: Starting unmute checker...")
    check_unmutes.start()
    
    logger.info("Tasks: Starting cache refresh...")
    refresh_cache.start()
    
    logger.info("Tasks: Starting health monitor...")
    health_monitor.start()
    
    logger.info("[SUCCESS] Bot is ready!")
    logger.info("═══════════════════════════════════════════════════════════════")

@bot.event
async def on_guild_join(guild: discord.Guild):
    """Handle bot joining new guild"""
    logger.info(f"Bot joined new guild: {guild.name} (ID: {guild.id})")
    
    # Initialize default settings
    try:
        await bot.db_manager.db.execute(
            "INSERT OR IGNORE INTO prefixes (guild_id, prefix, created_at) VALUES (?, ?, ?)",
            (guild.id, DEFAULT_PREFIX, datetime.utcnow().isoformat())
        )
        await bot.db_manager.db.commit()
    except Exception as e:
        logger.error(f"Error initializing guild settings: {e}")

@bot.event
async def on_message(message: discord.Message):
    """Handle incoming messages"""
    # Ignore bot messages
    if message.author.bot:
        return
    
    # Ignore DMs
    if not message.guild:
        return
    
    # Check anti-spam
    try:
        async with bot.db_manager.db.execute(
            "SELECT antispam_enabled, antispam_sensitivity FROM settings WHERE guild_id = ?",
            (message.guild.id,)
        ) as cursor:
            row = await cursor.fetchone()
            
            if row and row['antispam_enabled']:
                sensitivity = row['antispam_sensitivity'] or 5
                
                # Check if user is mod (mods are immune)
                if not await has_mod_permissions(message, message.author):
                    is_spam, reason = await bot.antispam.check_message(message, sensitivity)
                    
                    if is_spam:
                        strikes = bot.antispam.add_strike(message.author.id)
                        logger.warning(f"[AntiSpam] User {message.author.id} triggered spam filter: {reason}")
                        
                        try:
                            await message.delete()
                        except:
                            pass
                        
                        # Escalation system
                        if strikes == 1:
                            try:
                                await message.author.send(f"⚠️ **Spam Warning**\nPlease slow down your messages in {message.guild.name}.")
                            except:
                                pass
                        elif strikes == 2:
                            await message.author.timeout(timedelta(minutes=5), reason="Spam (Strike 2)")
                        elif strikes == 3:
                            await message.author.timeout(timedelta(hours=1), reason="Spam (Strike 3)")
                        elif strikes == 4:
                            await message.author.timeout(timedelta(days=1), reason="Spam (Strike 4)")
                        elif strikes >= 5:
                            # Auto-kick
                            await message.author.kick(reason="Spam (Strike 5 - Auto-kick)")
                            await log_moderation_action(
                                message.guild.id,
                                "AUTO-KICK",
                                message.author.id,
                                bot.user.id,
                                f"Auto-kick due to spam (5 strikes): {reason}"
                            )
                        
                        return  # Don't process commands
    except Exception as e:
        logger.error(f"Error in anti-spam check: {e}")
    
    # Process commands
    await bot.process_commands(message)

@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    """Global command error handler"""
    
    # Ignore command not found
    if isinstance(error, commands.CommandNotFound):
        return
    
    # Handle cooldown
    if isinstance(error, commands.CommandOnCooldown):
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Command on Cooldown",
            description=f"Please wait {error.retry_after:.1f} seconds before using this command again.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
        return
    
    # Handle missing permissions
    if isinstance(error, commands.MissingPermissions):
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Missing Permissions",
            description="You don't have permission to use this command.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
        return
    
    # Handle bot missing permissions
    if isinstance(error, commands.BotMissingPermissions):
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Bot Missing Permissions",
            description=f"I need the following permissions: {', '.join(error.missing_permissions)}",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)
        return
    
    # Handle missing required argument
    if isinstance(error, commands.MissingRequiredArgument):
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Missing Required Argument",
            description=f"Missing required argument: `{error.param.name}`\n\nUse `!help {ctx.command}` for usage info.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)
        return
    
    # Handle user input errors
    if isinstance(error, commands.UserInputError):
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Invalid Input",
            description=f"{str(error)}\n\nUse `!help {ctx.command}` for usage info.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)
        return
    
    # Log unknown errors
    logger.error(f"Command error in {ctx.command}: {error}")
    logger.error(traceback.format_exc())
    
    # Send generic error message
    emoji = await bot.get_emoji(ctx.guild.id, "error")
    embed = await EmbedFactory.create(
        title="Command Error",
        description="An unexpected error occurred while executing this command.",
        color=EmbedFactory.ERROR_COLOR,
        emoji=emoji
    )
    await ctx.send(embed=embed, delete_after=10)
    
    # Log to audit
    try:
        await bot.db_manager.db.execute(
            """INSERT INTO audit_log (guild_id, event_type, user_id, details, timestamp)
            VALUES (?, ?, ?, ?, ?)""",
            (ctx.guild.id, "COMMAND_ERROR", ctx.author.id, str(error), datetime.utcnow().isoformat())
        )
        await bot.db_manager.db.commit()
    except:
        pass

@bot.event
async def on_error(event: str, *args, **kwargs):
    """Global error handler"""
    logger.error(f"Error in event {event}:")
    logger.error(traceback.format_exc())

# ═══════════════════════════════════════════════════════════════
# BACKGROUND TASKS
# ═══════════════════════════════════════════════════════════════

@tasks.loop(seconds=15)
async def rotate_status():
    """Rotate bot status"""
    statuses = [
        discord.Game(name="Soviet Russia Life Simulator"),
        discord.Activity(type=discord.ActivityType.watching, name=f"{len(bot.guilds)} Servers"),
        discord.Activity(type=discord.ActivityType.listening, name="Moderation Commands"),
        discord.Activity(type=discord.ActivityType.watching, name="For Rule Breakers")
    ]
    
    current = statuses[rotate_status.current_loop % len(statuses)]
    await bot.change_presence(activity=current, status=discord.Status.online)
    logger.debug(f"Status: Changed to {current.name}")

@rotate_status.before_loop
async def before_status():
    """Wait for bot to be ready"""
    await bot.wait_until_ready()

@rotate_status.error
async def status_error(error):
    """Handle status rotation errors"""
    logger.error(f"Status rotation error: {error}")
    await asyncio.sleep(60)
    rotate_status.restart()

@tasks.loop(seconds=60)
async def check_unmutes():
    """Check for expired mutes and unmute users"""
    try:
        now = datetime.utcnow()
        
        async with bot.db_manager.db.execute(
            "SELECT guild_id, user_id, unmute_time FROM active_mutes WHERE unmute_time <= ?",
            (now.isoformat(),)
        ) as cursor:
            rows = await cursor.fetchall()
            
            for row in rows:
                guild = bot.get_guild(row['guild_id'])
                if not guild:
                    continue
                
                member = guild.get_member(row['user_id'])
                if not member:
                    continue
                
                try:
                    await member.timeout(None, reason="Mute duration expired")
                    logger.info(f"Auto-unmuted user {member.id} in guild {guild.id}")
                    
                    # Remove from database
                    await bot.db_manager.db.execute(
                        "DELETE FROM active_mutes WHERE guild_id = ? AND user_id = ?",
                        (row['guild_id'], row['user_id'])
                    )
                except Exception as e:
                    logger.error(f"Error auto-unmuting user: {e}")
            
            await bot.db_manager.db.commit()
    except Exception as e:
        logger.error(f"Error in unmute checker: {e}")

@check_unmutes.before_loop
async def before_unmutes():
    """Wait for bot to be ready"""
    await bot.wait_until_ready()

@tasks.loop(minutes=5)
async def refresh_cache():
    """Refresh cache periodically"""
    if bot.cache.should_refresh():
        bot.cache.clear()
        logger.debug("Cache: Refreshed")

@refresh_cache.before_loop
async def before_cache():
    """Wait for bot to be ready"""
    await bot.wait_until_ready()

@tasks.loop(seconds=60)
async def health_monitor():
    """Monitor bot health"""
    latency_ms = round(bot.latency * 1000)
    guild_count = len(bot.guilds)
    
    logger.debug(f"[Health] Latency: {latency_ms}ms | Guilds: {guild_count}")

@health_monitor.before_loop
async def before_health():
    """Wait for bot to be ready"""
    await bot.wait_until_ready()

# ═══════════════════════════════════════════════════════════════
# UTILITY COMMANDS
# ═══════════════════════════════════════════════════════════════

@bot.hybrid_command(name="ping", description="Check bot latency and status")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def ping(ctx: commands.Context):
    """Check bot latency and status"""
    
    # Measure API latency
    start = time.time()
    msg = await ctx.send("Pinging...")
    end = time.time()
    api_latency = round((end - start) * 1000)
    
    # Calculate uptime
    uptime = datetime.utcnow() - bot.start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    emoji = await bot.get_emoji(ctx.guild.id, "ping")
    
    embed = await EmbedFactory.create(
        title="Pong!",
        description="Bot Status & Latency Information",
        color=EmbedFactory.INFO_COLOR,
        emoji=emoji,
        fields=[
            {"name": "🌐 WebSocket Latency", "value": f"`{round(bot.latency * 1000)}ms`", "inline": True},
            {"name": "📡 API Latency", "value": f"`{api_latency}ms`", "inline": True},
            {"name": "⏰ Uptime", "value": f"`{days}d {hours}h {minutes}m {seconds}s`", "inline": False},
            {"name": "🏛️ Guilds", "value": f"`{len(bot.guilds)}`", "inline": True},
            {"name": "👥 Cached Users", "value": f"`{len(bot.users):,}`", "inline": True}
        ]
    )
    
    await msg.edit(content=None, embed=embed)

@bot.hybrid_command(name="serverinfo", description="Display server information")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def serverinfo(ctx: commands.Context):
    """Display detailed server information"""
    guild = ctx.guild
    
    # Calculate member stats
    total_members = guild.member_count
    humans = len([m for m in guild.members if not m.bot])
    bots = total_members - humans
    
    # Get verification level
    verification_levels = {
        discord.VerificationLevel.none: "None",
        discord.VerificationLevel.low: "Low",
        discord.VerificationLevel.medium: "Medium",
        discord.VerificationLevel.high: "High",
        discord.VerificationLevel.highest: "Highest"
    }
    
    emoji = await bot.get_emoji(ctx.guild.id, "info")
    
    embed = await EmbedFactory.create(
        title=f"{guild.name}",
        description=f"**Server Information**\n{guild.description or 'No description'}",
        color=EmbedFactory.INFO_COLOR,
        emoji=emoji,
        thumbnail=guild.icon.url if guild.icon else None,
        fields=[
            {"name": "👑 Owner", "value": guild.owner.mention if guild.owner else "Unknown", "inline": True},
            {"name": "📅 Created", "value": f"<t:{int(guild.created_at.timestamp())}:R>", "inline": True},
            {"name": "🆔 Server ID", "value": f"`{guild.id}`", "inline": True},
            {"name": "👥 Members", "value": f"Total: `{total_members}`\nHumans: `{humans}`\nBots: `{bots}`", "inline": True},
            {"name": "📊 Channels", "value": f"Text: `{len(guild.text_channels)}`\nVoice: `{len(guild.voice_channels)}`\nCategories: `{len(guild.categories)}`", "inline": True},
            {"name": "🎭 Roles", "value": f"`{len(guild.roles)}`", "inline": True},
            {"name": "💎 Boost Status", "value": f"Level: `{guild.premium_tier}`\nBoosts: `{guild.premium_subscription_count}`", "inline": True},
            {"name": "🔒 Verification", "value": f"`{verification_levels.get(guild.verification_level, 'Unknown')}`", "inline": True}
        ]
    )
    
    if guild.banner:
        embed.set_image(url=guild.banner.url)
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name="userinfo", description="Display user information")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def userinfo(ctx: commands.Context, member: discord.Member = None):
    """Display detailed user information"""
    member = member or ctx.author
    
    # Get account age
    account_age = datetime.utcnow() - member.created_at
    
    # Get join position
    sorted_members = sorted(ctx.guild.members, key=lambda m: m.joined_at or datetime.utcnow())
    join_position = sorted_members.index(member) + 1
    
    # Get roles (sorted by hierarchy)
    roles = [role.mention for role in sorted(member.roles[1:], key=lambda r: r.position, reverse=True)]
    roles_str = ", ".join(roles[:10]) if roles else "No roles"
    if len(roles) > 10:
        roles_str += f" (+{len(roles) - 10} more)"
    
    # Get warn count
    warn_count = 0
    try:
        async with bot.db_manager.db.execute(
            "SELECT COUNT(*) as count FROM warns WHERE guild_id = ? AND user_id = ? AND active = 1",
            (ctx.guild.id, member.id)
        ) as cursor:
            row = await cursor.fetchone()
            warn_count = row['count'] if row else 0
    except:
        pass
    
    emoji = await bot.get_emoji(ctx.guild.id, "user")
    
    embed = await EmbedFactory.create(
        title=f"{member.name}",
        description=f"**User Information**",
        color=member.color if member.color != discord.Color.default() else EmbedFactory.INFO_COLOR,
        emoji=emoji,
        thumbnail=member.display_avatar.url,
        fields=[
            {"name": "🆔 User ID", "value": f"`{member.id}`", "inline": True},
            {"name": "📅 Account Created", "value": f"<t:{int(member.created_at.timestamp())}:R>", "inline": True},
            {"name": "📥 Joined Server", "value": f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "Unknown", "inline": True},
            {"name": "📊 Join Position", "value": f"`#{join_position}` / `{len(ctx.guild.members)}`", "inline": True},
            {"name": "⚠️ Warnings", "value": f"`{warn_count}`", "inline": True},
            {"name": "🎭 Roles", "value": roles_str, "inline": False}
        ]
    )
    
    await ctx.send(embed=embed)

# ═══════════════════════════════════════════════════════════════
# MODERATION COMMANDS
# ═══════════════════════════════════════════════════════════════

@bot.hybrid_command(name="warn", description="Warn a user")
@commands.has_permissions(moderate_members=True)
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def warn(ctx: commands.Context, member: discord.Member, *, reason: str):
    """Warn a user with automatic escalation"""
    
    # Check if can warn
    if member == ctx.author:
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="You cannot warn yourself.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
        return
    
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="You cannot warn someone with a higher or equal role.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
        return
    
    try:
        # Insert warning
        await bot.db_manager.db.execute(
            """INSERT INTO warns (guild_id, user_id, moderator_id, reason, timestamp, active)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (ctx.guild.id, member.id, ctx.author.id, reason, datetime.utcnow().isoformat(), 1)
        )
        
        # Get total warns
        async with bot.db_manager.db.execute(
            "SELECT COUNT(*) as count FROM warns WHERE guild_id = ? AND user_id = ? AND active = 1",
            (ctx.guild.id, member.id)
        ) as cursor:
            row = await cursor.fetchone()
            warn_count = row['count'] if row else 0
        
        await bot.db_manager.db.commit()
        
        # Log to cases
        case_id = await log_moderation_action(
            ctx.guild.id,
            "WARN",
            member.id,
            ctx.author.id,
            reason
        )
        
        # DM user
        try:
            dm_embed = await EmbedFactory.create(
                title="Warning Received",
                description=f"You have been warned in **{ctx.guild.name}**",
                color=EmbedFactory.WARNING_COLOR,
                emoji=await bot.get_emoji(ctx.guild.id, "warning"),
                fields=[
                    {"name": "Reason", "value": reason, "inline": False},
                    {"name": "Moderator", "value": ctx.author.name, "inline": True},
                    {"name": "Total Warnings", "value": f"`{warn_count}`", "inline": True},
                    {"name": "Case ID", "value": f"`{case_id}`", "inline": True}
                ]
            )
            await member.send(embed=dm_embed)
        except:
            pass
        
        # Send confirmation
        emoji = await bot.get_emoji(ctx.guild.id, "success")
        embed = await EmbedFactory.create(
            title="User Warned",
            description=f"**User:** {member.mention}\n**Reason:** {reason}",
            color=EmbedFactory.SUCCESS_COLOR,
            emoji=emoji,
            fields=[
                {"name": "Case ID", "value": f"`{case_id}`", "inline": True},
                {"name": "Moderator", "value": ctx.author.mention, "inline": True},
                {"name": "Total Warnings", "value": f"`{warn_count}`", "inline": True}
            ]
        )
        await ctx.send(embed=embed)
        
        # Auto-escalation (3 warnings = kick)
        if warn_count >= 3:
            try:
                await member.kick(reason=f"Auto-kick: 3 warnings accumulated (Last: {reason})")
                
                kick_emoji = await bot.get_emoji(ctx.guild.id, "kick")
                kick_embed = await EmbedFactory.create(
                    title="Auto-Kick Executed",
                    description=f"**User:** {member.mention}\n**Reason:** 3 warnings accumulated",
                    color=EmbedFactory.WARNING_COLOR,
                    emoji=kick_emoji
                )
                await ctx.send(embed=kick_embed)
                
                await log_moderation_action(
                    ctx.guild.id,
                    "AUTO-KICK",
                    member.id,
                    bot.user.id,
                    f"Auto-kick due to 3 warnings (Last: {reason})"
                )
            except Exception as e:
                logger.error(f"Error in auto-kick: {e}")
        
        # Log to channel
        log_embed = await EmbedFactory.create(
            title="⚠️ User Warned",
            description=f"**User:** {member.mention} ({member.id})\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}",
            color=EmbedFactory.WARNING_COLOR,
            fields=[
                {"name": "Case ID", "value": f"`{case_id}`", "inline": True},
                {"name": "Total Warnings", "value": f"`{warn_count}`", "inline": True}
            ]
        )
        await send_log_embed(ctx.guild, log_embed)
        
    except Exception as e:
        logger.error(f"Error in warn command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="An error occurred while warning the user.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

@bot.hybrid_command(name="kick", description="Kick a user from the server")
@commands.has_permissions(kick_members=True)
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def kick(ctx: commands.Context, member: discord.Member, *, reason: str):
    """Kick a user from the server"""
    
    if member == ctx.author:
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="You cannot kick yourself.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
        return
    
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="You cannot kick someone with a higher or equal role.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
        return
    
    try:
        # DM user before kicking
        try:
            dm_embed = await EmbedFactory.create(
                title="Kicked from Server",
                description=f"You have been kicked from **{ctx.guild.name}**",
                color=EmbedFactory.ERROR_COLOR,
                emoji=await bot.get_emoji(ctx.guild.id, "kick"),
                fields=[
                    {"name": "Reason", "value": reason, "inline": False},
                    {"name": "Moderator", "value": ctx.author.name, "inline": True}
                ]
            )
            await member.send(embed=dm_embed)
        except:
            pass
        
        # Kick user
        await member.kick(reason=f"{reason} | Moderator: {ctx.author}")
        
        # Log action
        case_id = await log_moderation_action(
            ctx.guild.id,
            "KICK",
            member.id,
            ctx.author.id,
            reason
        )
        
        # Send confirmation
        emoji = await bot.get_emoji(ctx.guild.id, "kick")
        embed = await EmbedFactory.create(
            title="User Kicked",
            description=f"**User:** {member.mention}\n**Reason:** {reason}",
            color=EmbedFactory.SUCCESS_COLOR,
            emoji=emoji,
            fields=[
                {"name": "Case ID", "value": f"`{case_id}`", "inline": True},
                {"name": "Moderator", "value": ctx.author.mention, "inline": True}
            ]
        )
        await ctx.send(embed=embed)
        
        # Log to channel
        log_embed = await EmbedFactory.create(
            title="👢 User Kicked",
            description=f"**User:** {member.mention} ({member.id})\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}",
            color=EmbedFactory.WARNING_COLOR,
            fields=[{"name": "Case ID", "value": f"`{case_id}`", "inline": True}]
        )
        await send_log_embed(ctx.guild, log_embed)
        
    except Exception as e:
        logger.error(f"Error in kick command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description=f"Failed to kick user: {str(e)}",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

@bot.hybrid_command(name="ban", description="Ban a user from the server")
@commands.has_permissions(ban_members=True)
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def ban(ctx: commands.Context, member: Union[discord.Member, discord.User], delete_days: int = 1, *, reason: str):
    """Ban a user from the server"""
    
    if isinstance(member, discord.Member):
        if member == ctx.author:
            emoji = await bot.get_emoji(ctx.guild.id, "error")
            embed = await EmbedFactory.create(
                title="Error",
                description="You cannot ban yourself.",
                color=EmbedFactory.ERROR_COLOR,
                emoji=emoji
            )
            await ctx.send(embed=embed, delete_after=5)
            return
        
        if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            emoji = await bot.get_emoji(ctx.guild.id, "error")
            embed = await EmbedFactory.create(
                title="Error",
                description="You cannot ban someone with a higher or equal role.",
                color=EmbedFactory.ERROR_COLOR,
                emoji=emoji
            )
            await ctx.send(embed=embed, delete_after=5)
            return
    
    if len(reason) < 10:
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="Ban reason must be at least 10 characters.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
        return
    
    if delete_days < 0 or delete_days > 7:
        delete_days = 1
    
    try:
        # DM user before banning
        try:
            dm_embed = await EmbedFactory.create(
                title="Banned from Server",
                description=f"You have been banned from **{ctx.guild.name}**",
                color=EmbedFactory.ERROR_COLOR,
                emoji=await bot.get_emoji(ctx.guild.id, "ban"),
                fields=[
                    {"name": "Reason", "value": reason, "inline": False},
                    {"name": "Moderator", "value": ctx.author.name, "inline": True}
                ]
            )
            await member.send(embed=dm_embed)
        except:
            pass
        
        # Ban user
        await ctx.guild.ban(
            member,
            reason=f"{reason} | Moderator: {ctx.author}",
            delete_message_days=delete_days
        )
        
        # Log action
        case_id = await log_moderation_action(
            ctx.guild.id,
            "BAN",
            member.id,
            ctx.author.id,
            reason
        )
        
        # Send confirmation
        emoji = await bot.get_emoji(ctx.guild.id, "ban")
        embed = await EmbedFactory.create(
            title="User Banned",
            description=f"**User:** {member.mention}\n**Reason:** {reason}",
            color=EmbedFactory.SUCCESS_COLOR,
            emoji=emoji,
            fields=[
                {"name": "Case ID", "value": f"`{case_id}`", "inline": True},
                {"name": "Moderator", "value": ctx.author.mention, "inline": True},
                {"name": "Messages Deleted", "value": f"`{delete_days} days`", "inline": True}
            ]
        )
        await ctx.send(embed=embed)
        
        # Log to channel
        log_embed = await EmbedFactory.create(
            title="🔨 User Banned",
            description=f"**User:** {member.mention} ({member.id})\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}",
            color=EmbedFactory.ERROR_COLOR,
            fields=[{"name": "Case ID", "value": f"`{case_id}`", "inline": True}]
        )
        await send_log_embed(ctx.guild, log_embed)
        
    except Exception as e:
        logger.error(f"Error in ban command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description=f"Failed to ban user: {str(e)}",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

@bot.hybrid_command(name="unban", description="Unban a user from the server")
@commands.has_permissions(ban_members=True)
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def unban(ctx: commands.Context, user_id: str, *, reason: str = "No reason provided"):
    """Unban a user from the server"""
    
    try:
        user_id_int = int(user_id)
        user = await bot.fetch_user(user_id_int)
        
        await ctx.guild.unban(user, reason=f"{reason} | Moderator: {ctx.author}")
        
        # Log action
        case_id = await log_moderation_action(
            ctx.guild.id,
            "UNBAN",
            user.id,
            ctx.author.id,
            reason
        )
        
        # Send confirmation
        emoji = await bot.get_emoji(ctx.guild.id, "success")
        embed = await EmbedFactory.create(
            title="User Unbanned",
            description=f"**User:** {user.mention}\n**Reason:** {reason}",
            color=EmbedFactory.SUCCESS_COLOR,
            emoji=emoji,
            fields=[
                {"name": "Case ID", "value": f"`{case_id}`", "inline": True},
                {"name": "Moderator", "value": ctx.author.mention, "inline": True}
            ]
        )
        await ctx.send(embed=embed)
        
    except discord.NotFound:
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="This user is not banned.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
    except Exception as e:
        logger.error(f"Error in unban command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description=f"Failed to unban user: {str(e)}",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

@bot.hybrid_command(name="mute", description="Timeout a user")
@commands.has_permissions(moderate_members=True)
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def mute(ctx: commands.Context, member: discord.Member, duration: str, *, reason: str):
    """Timeout a user for a specified duration"""
    
    if member == ctx.author:
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="You cannot mute yourself.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
        return
    
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="You cannot mute someone with a higher or equal role.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
        return
    
    # Parse duration
    duration_delta = await parse_duration(duration)
    if not duration_delta:
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="Invalid duration format. Use: 10s, 5m, 2h, 1d, 1w",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
        return
    
    # Discord timeout limit is 28 days
    if duration_delta > timedelta(days=28):
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="Duration cannot exceed 28 days.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
        return
    
    try:
        # Timeout user
        await member.timeout(duration_delta, reason=f"{reason} | Moderator: {ctx.author}")
        
        # Calculate unmute time
        unmute_time = datetime.utcnow() + duration_delta
        
        # Store in database
        await bot.db_manager.db.execute(
            """INSERT OR REPLACE INTO active_mutes (guild_id, user_id, unmute_time, reason)
            VALUES (?, ?, ?, ?)""",
            (ctx.guild.id, member.id, unmute_time.isoformat(), reason)
        )
        await bot.db_manager.db.commit()
        
        # Log action
        case_id = await log_moderation_action(
            ctx.guild.id,
            "MUTE",
            member.id,
            ctx.author.id,
            f"{reason} | Duration: {duration}"
        )
        
        # DM user
        try:
            dm_embed = await EmbedFactory.create(
                title="Muted in Server",
                description=f"You have been muted in **{ctx.guild.name}**",
                color=EmbedFactory.WARNING_COLOR,
                emoji=await bot.get_emoji(ctx.guild.id, "mute"),
                fields=[
                    {"name": "Reason", "value": reason, "inline": False},
                    {"name": "Duration", "value": duration, "inline": True},
                    {"name": "Unmute Time", "value": f"<t:{int(unmute_time.timestamp())}:R>", "inline": True}
                ]
            )
            await member.send(embed=dm_embed)
        except:
            pass
        
        # Send confirmation
        emoji = await bot.get_emoji(ctx.guild.id, "mute")
        embed = await EmbedFactory.create(
            title="User Muted",
            description=f"**User:** {member.mention}\n**Reason:** {reason}",
            color=EmbedFactory.SUCCESS_COLOR,
            emoji=emoji,
            fields=[
                {"name": "Case ID", "value": f"`{case_id}`", "inline": True},
                {"name": "Duration", "value": f"`{duration}`", "inline": True},
                {"name": "Unmute Time", "value": f"<t:{int(unmute_time.timestamp())}:R>", "inline": True}
            ]
        )
        await ctx.send(embed=embed)
        
        # Log to channel
        log_embed = await EmbedFactory.create(
            title="🔇 User Muted",
            description=f"**User:** {member.mention} ({member.id})\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}",
            color=EmbedFactory.WARNING_COLOR,
            fields=[
                {"name": "Case ID", "value": f"`{case_id}`", "inline": True},
                {"name": "Duration", "value": f"`{duration}`", "inline": True}
            ]
        )
        await send_log_embed(ctx.guild, log_embed)
        
    except Exception as e:
        logger.error(f"Error in mute command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description=f"Failed to mute user: {str(e)}",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

@bot.hybrid_command(name="unmute", description="Remove timeout from a user")
@commands.has_permissions(moderate_members=True)
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def unmute(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    """Remove timeout from a user"""
    
    try:
        await member.timeout(None, reason=f"{reason} | Moderator: {ctx.author}")
        
        # Remove from database
        await bot.db_manager.db.execute(
            "DELETE FROM active_mutes WHERE guild_id = ? AND user_id = ?",
            (ctx.guild.id, member.id)
        )
        await bot.db_manager.db.commit()
        
        # Log action
        case_id = await log_moderation_action(
            ctx.guild.id,
            "UNMUTE",
            member.id,
            ctx.author.id,
            reason
        )
        
        # Send confirmation
        emoji = await bot.get_emoji(ctx.guild.id, "unmute")
        embed = await EmbedFactory.create(
            title="User Unmuted",
            description=f"**User:** {member.mention}\n**Reason:** {reason}",
            color=EmbedFactory.SUCCESS_COLOR,
            emoji=emoji,
            fields=[
                {"name": "Case ID", "value": f"`{case_id}`", "inline": True},
                {"name": "Moderator", "value": ctx.author.mention, "inline": True}
            ]
        )
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in unmute command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description=f"Failed to unmute user: {str(e)}",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

@bot.hybrid_command(name="clear", description="Clear messages from a channel")
@commands.has_permissions(manage_messages=True)
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def clear(ctx: commands.Context, amount: int = 10):
    """Clear messages from a channel"""
    
    if amount < 1 or amount > 100:
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="Amount must be between 1 and 100.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
        return
    
    try:
        deleted = await ctx.channel.purge(limit=amount + 1)  # +1 for command message
        
        emoji = await bot.get_emoji(ctx.guild.id, "success")
        embed = await EmbedFactory.create(
            title="Messages Cleared",
            description=f"Deleted `{len(deleted) - 1}` messages.",
            color=EmbedFactory.SUCCESS_COLOR,
            emoji=emoji
        )
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(5)
        await msg.delete()
        
    except Exception as e:
        logger.error(f"Error in clear command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description=f"Failed to clear messages: {str(e)}",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

@bot.hybrid_command(name="case", description="View a case by ID")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def case(ctx: commands.Context, case_id: str):
    """View details of a specific case"""
    
    try:
        async with bot.db_manager.db.execute(
            "SELECT * FROM cases WHERE case_id = ? AND guild_id = ?",
            (case_id.upper(), ctx.guild.id)
        ) as cursor:
            row = await cursor.fetchone()
            
            if not row:
                emoji = await bot.get_emoji(ctx.guild.id, "error")
                embed = await EmbedFactory.create(
                    title="Case Not Found",
                    description=f"No case found with ID `{case_id}`",
                    color=EmbedFactory.ERROR_COLOR,
                    emoji=emoji
                )
                await ctx.send(embed=embed, delete_after=5)
                return
            
            # Get target user
            try:
                target = await bot.fetch_user(row['target_id'])
                target_str = f"{target.mention} ({target})"
            except:
                target_str = f"User ID: {row['target_id']}"
            
            # Get moderator
            try:
                moderator = await bot.fetch_user(row['moderator_id'])
                moderator_str = f"{moderator.mention} ({moderator})"
            except:
                moderator_str = f"User ID: {row['moderator_id']}"
            
            emoji = await bot.get_emoji(ctx.guild.id, "case")
            embed = await EmbedFactory.create(
                title=f"Case {row['case_id']}",
                description=f"**Action:** {row['action']}",
                color=EmbedFactory.INFO_COLOR,
                emoji=emoji,
                fields=[
                    {"name": "Target", "value": target_str, "inline": True},
                    {"name": "Moderator", "value": moderator_str, "inline": True},
                    {"name": "Timestamp", "value": f"<t:{int(datetime.fromisoformat(row['timestamp']).timestamp())}:F>", "inline": False},
                    {"name": "Reason", "value": row['reason'], "inline": False}
                ]
            )
            
            if row['evidence_url']:
                embed.add_field(name="Evidence", value=row['evidence_url'], inline=False)
            
            await ctx.send(embed=embed)
            
    except Exception as e:
        logger.error(f"Error in case command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="An error occurred while fetching the case.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

@bot.hybrid_command(name="warnings", description="View warnings for a user")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def warnings(ctx: commands.Context, member: discord.Member = None):
    """View warnings for a user"""
    member = member or ctx.author
    
    try:
        async with bot.db_manager.db.execute(
            "SELECT * FROM warns WHERE guild_id = ? AND user_id = ? AND active = 1 ORDER BY timestamp DESC",
            (ctx.guild.id, member.id)
        ) as cursor:
            rows = await cursor.fetchall()
            
            if not rows:
                emoji = await bot.get_emoji(ctx.guild.id, "success")
                embed = await EmbedFactory.create(
                    title="No Warnings",
                    description=f"{member.mention} has no active warnings.",
                    color=EmbedFactory.SUCCESS_COLOR,
                    emoji=emoji
                )
                await ctx.send(embed=embed)
                return
            
            # Build warning list
            warning_list = []
            for i, row in enumerate(rows[:10], 1):  # Show max 10
                try:
                    moderator = await bot.fetch_user(row['moderator_id'])
                    mod_name = moderator.name
                except:
                    mod_name = f"ID: {row['moderator_id']}"
                
                timestamp = datetime.fromisoformat(row['timestamp'])
                warning_list.append(
                    f"**{i}.** <t:{int(timestamp.timestamp())}:R> by {mod_name}\n"
                    f"└ {row['reason']}"
                )
            
            emoji = await bot.get_emoji(ctx.guild.id, "warning")
            embed = await EmbedFactory.create(
                title=f"Warnings for {member.name}",
                description="\n\n".join(warning_list),
                color=EmbedFactory.WARNING_COLOR,
                emoji=emoji,
                thumbnail=member.display_avatar.url,
                fields=[
                    {"name": "Total Active Warnings", "value": f"`{len(rows)}`", "inline": True}
                ]
            )
            await ctx.send(embed=embed)
            
    except Exception as e:
        logger.error(f"Error in warnings command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="An error occurred while fetching warnings.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION COMMANDS
# ═══════════════════════════════════════════════════════════════

@bot.hybrid_command(name="setprefix", description="Set the bot prefix for this server")
@commands.has_permissions(administrator=True)
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def setprefix(ctx: commands.Context, prefix: str):
    """Set custom command prefix for the server"""
    
    if len(prefix) > 5:
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="Prefix must be 5 characters or less.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
        return
    
    try:
        await bot.db_manager.db.execute(
            """INSERT OR REPLACE INTO prefixes (guild_id, prefix, created_at, updated_at)
            VALUES (?, ?, COALESCE((SELECT created_at FROM prefixes WHERE guild_id = ?), ?), ?)""",
            (ctx.guild.id, prefix, ctx.guild.id, datetime.utcnow().isoformat(), datetime.utcnow().isoformat())
        )
        await bot.db_manager.db.commit()
        
        # Update cache
        bot.cache.prefixes[ctx.guild.id] = prefix
        
        emoji = await bot.get_emoji(ctx.guild.id, "success")
        embed = await EmbedFactory.create(
            title="Prefix Updated",
            description=f"Bot prefix has been set to `{prefix}`",
            color=EmbedFactory.SUCCESS_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in setprefix command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="Failed to update prefix.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

@bot.hybrid_command(name="setup", description="Configure bot settings")
@commands.has_permissions(administrator=True)
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def setup(ctx: commands.Context):
    """Interactive setup for bot configuration"""
    
    emoji = await bot.get_emoji(ctx.guild.id, "gear")
    embed = await EmbedFactory.create(
        title="Bot Setup",
        description="Welcome to the SRLS Bot setup!\n\nUse the following commands to configure the bot:\n\n"
                    "**Moderation:**\n"
                    "`!setmodrole <role>` - Set moderator role\n"
                    "`!setlogchannel <channel>` - Set log channel\n\n"
                    "**Anti-Spam:**\n"
                    "`!antispam enable` - Enable anti-spam\n"
                    "`!antispam sensitivity <1-10>` - Set sensitivity\n\n"
                    "**Customization:**\n"
                    "`!setprefix <prefix>` - Change command prefix\n"
                    "`!setemoji <name> <emoji>` - Set custom emoji\n\n"
                    "**View Settings:**\n"
                    "`!config` - View current configuration",
        color=EmbedFactory.INFO_COLOR,
        emoji=emoji
    )
    await ctx.send(embed=embed)

@bot.hybrid_command(name="setmodrole", description="Set the moderator role")
@commands.has_permissions(administrator=True)
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def setmodrole(ctx: commands.Context, role: discord.Role):
    """Set the moderator role"""
    
    try:
        await bot.db_manager.db.execute(
            """INSERT INTO settings (guild_id, mod_role_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET mod_role_id = ?""",
            (ctx.guild.id, role.id, role.id)
        )
        await bot.db_manager.db.commit()
        
        emoji = await bot.get_emoji(ctx.guild.id, "success")
        embed = await EmbedFactory.create(
            title="Moderator Role Set",
            description=f"Moderator role has been set to {role.mention}",
            color=EmbedFactory.SUCCESS_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in setmodrole command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="Failed to set moderator role.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

@bot.hybrid_command(name="setlogchannel", description="Set the log channel")
@commands.has_permissions(administrator=True)
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def setlogchannel(ctx: commands.Context, channel: discord.TextChannel):
    """Set the moderation log channel"""
    
    try:
        await bot.db_manager.db.execute(
            """INSERT INTO settings (guild_id, log_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = ?""",
            (ctx.guild.id, channel.id, channel.id)
        )
        await bot.db_manager.db.commit()
        
        emoji = await bot.get_emoji(ctx.guild.id, "success")
        embed = await EmbedFactory.create(
            title="Log Channel Set",
            description=f"Log channel has been set to {channel.mention}",
            color=EmbedFactory.SUCCESS_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in setlogchannel command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="Failed to set log channel.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

@bot.hybrid_group(name="antispam", description="Anti-spam configuration", invoke_without_command=True)
@commands.has_permissions(administrator=True)
async def antispam_group(ctx: commands.Context):
    """Anti-spam configuration"""
    if ctx.invoked_subcommand is None:
        emoji = await bot.get_emoji(ctx.guild.id, "info")
        embed = await EmbedFactory.create(
            title="Anti-Spam Commands",
            description="**Available Commands:**\n"
                        "`!antispam enable` - Enable anti-spam\n"
                        "`!antispam disable` - Disable anti-spam\n"
                        "`!antispam sensitivity <1-10>` - Set sensitivity\n"
                        "`!antispam config` - View configuration",
            color=EmbedFactory.INFO_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed)

@antispam_group.command(name="enable", description="Enable anti-spam")
@commands.has_permissions(administrator=True)
async def antispam_enable(ctx: commands.Context):
    """Enable anti-spam protection"""
    
    try:
        await bot.db_manager.db.execute(
            """INSERT INTO settings (guild_id, antispam_enabled)
            VALUES (?, 1)
            ON CONFLICT(guild_id) DO UPDATE SET antispam_enabled = 1""",
            (ctx.guild.id,)
        )
        await bot.db_manager.db.commit()
        
        emoji = await bot.get_emoji(ctx.guild.id, "success")
        embed = await EmbedFactory.create(
            title="Anti-Spam Enabled",
            description="Anti-spam protection is now enabled.",
            color=EmbedFactory.SUCCESS_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error enabling anti-spam: {e}")

@antispam_group.command(name="disable", description="Disable anti-spam")
@commands.has_permissions(administrator=True)
async def antispam_disable(ctx: commands.Context):
    """Disable anti-spam protection"""
    
    try:
        await bot.db_manager.db.execute(
            """INSERT INTO settings (guild_id, antispam_enabled)
            VALUES (?, 0)
            ON CONFLICT(guild_id) DO UPDATE SET antispam_enabled = 0""",
            (ctx.guild.id,)
        )
        await bot.db_manager.db.commit()
        
        emoji = await bot.get_emoji(ctx.guild.id, "success")
        embed = await EmbedFactory.create(
            title="Anti-Spam Disabled",
            description="Anti-spam protection is now disabled.",
            color=EmbedFactory.SUCCESS_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error disabling anti-spam: {e}")

@antispam_group.command(name="sensitivity", description="Set anti-spam sensitivity")
@commands.has_permissions(administrator=True)
async def antispam_sensitivity(ctx: commands.Context, level: int):
    """Set anti-spam sensitivity (1-10)"""
    
    if level < 1 or level > 10:
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="Sensitivity must be between 1 and 10.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=5)
        return
    
    try:
        await bot.db_manager.db.execute(
            """INSERT INTO settings (guild_id, antispam_sensitivity)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET antispam_sensitivity = ?""",
            (ctx.guild.id, level, level)
        )
        await bot.db_manager.db.commit()
        
        emoji = await bot.get_emoji(ctx.guild.id, "success")
        embed = await EmbedFactory.create(
            title="Sensitivity Updated",
            description=f"Anti-spam sensitivity has been set to `{level}`",
            color=EmbedFactory.SUCCESS_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error setting sensitivity: {e}")

@bot.hybrid_command(name="config", description="View current bot configuration")
@commands.has_permissions(administrator=True)
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def config(ctx: commands.Context):
    """View current bot configuration"""
    
    try:
        # Get prefix
        prefix = bot.cache.prefixes.get(ctx.guild.id, DEFAULT_PREFIX)
        
        # Get settings
        async with bot.db_manager.db.execute(
            "SELECT * FROM settings WHERE guild_id = ?",
            (ctx.guild.id,)
        ) as cursor:
            settings = await cursor.fetchone()
        
        # Build configuration display
        fields = [
            {"name": "🔧 Prefix", "value": f"`{prefix}`", "inline": True}
        ]
        
        if settings:
            if settings['mod_role_id']:
                role = ctx.guild.get_role(settings['mod_role_id'])
                fields.append({"name": "🛡️ Moderator Role", "value": role.mention if role else "Not set", "inline": True})
            
            if settings['log_channel_id']:
                channel = ctx.guild.get_channel(settings['log_channel_id'])
                fields.append({"name": "📝 Log Channel", "value": channel.mention if channel else "Not set", "inline": True})
            
            antispam_status = "✅ Enabled" if settings['antispam_enabled'] else "❌ Disabled"
            fields.append({"name": "🚨 Anti-Spam", "value": antispam_status, "inline": True})
            
            if settings['antispam_enabled']:
                fields.append({"name": "📊 Sensitivity", "value": f"`{settings['antispam_sensitivity']}/10`", "inline": True})
        
        emoji = await bot.get_emoji(ctx.guild.id, "gear")
        embed = await EmbedFactory.create(
            title="Bot Configuration",
            description="Current bot settings for this server",
            color=EmbedFactory.INFO_COLOR,
            emoji=emoji,
            fields=fields
        )
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in config command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="Failed to fetch configuration.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

# ═══════════════════════════════════════════════════════════════
# LOCKDOWN SYSTEM
# ═══════════════════════════════════════════════════════════════

@bot.hybrid_command(name="lockdown", description="Lock all channels in the server")
@commands.has_permissions(administrator=True)
@commands.cooldown(1, 10, commands.BucketType.guild)
async def lockdown(ctx: commands.Context, *, reason: str = "Emergency lockdown"):
    """Lock all channels to prevent messages from @everyone"""
    
    locked_channels = []
    
    try:
        for channel in ctx.guild.text_channels:
            try:
                overwrite = channel.overwrites_for(ctx.guild.default_role)
                if overwrite.send_messages != False:
                    overwrite.send_messages = False
                    await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Lockdown: {reason}")
                    locked_channels.append(channel)
            except Exception as e:
                logger.error(f"Failed to lock channel {channel.id}: {e}")
        
        # Log lockdown
        await bot.db_manager.db.execute(
            """INSERT INTO audit_log (guild_id, event_type, user_id, details, timestamp)
            VALUES (?, ?, ?, ?, ?)""",
            (ctx.guild.id, "LOCKDOWN", ctx.author.id, reason, datetime.utcnow().isoformat())
        )
        await bot.db_manager.db.commit()
        
        emoji = await bot.get_emoji(ctx.guild.id, "lock")
        embed = await EmbedFactory.create(
            title="Server Lockdown Activated",
            description=f"**Reason:** {reason}\n**Locked Channels:** {len(locked_channels)}",
            color=EmbedFactory.WARNING_COLOR,
            emoji=emoji,
            fields=[
                {"name": "Moderator", "value": ctx.author.mention, "inline": True}
            ]
        )
        await ctx.send(embed=embed)
        
        # Announce in all locked channels
        announce_embed = await EmbedFactory.create(
            title="🔒 Channel Locked",
            description=f"This server is now in lockdown.\n**Reason:** {reason}",
            color=EmbedFactory.ERROR_COLOR
        )
        
        for channel in locked_channels[:5]:  # Announce in first 5 channels
            try:
                await channel.send(embed=announce_embed)
            except:
                pass
        
    except Exception as e:
        logger.error(f"Error in lockdown command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="Failed to execute lockdown.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

@bot.hybrid_command(name="unlockdown", description="Unlock all channels in the server")
@commands.has_permissions(administrator=True)
@commands.cooldown(1, 10, commands.BucketType.guild)
async def unlockdown(ctx: commands.Context):
    """Remove lockdown from all channels"""
    
    unlocked_channels = []
    
    try:
        for channel in ctx.guild.text_channels:
            try:
                overwrite = channel.overwrites_for(ctx.guild.default_role)
                if overwrite.send_messages == False:
                    overwrite.send_messages = None
                    await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason="Lockdown lifted")
                    unlocked_channels.append(channel)
            except Exception as e:
                logger.error(f"Failed to unlock channel {channel.id}: {e}")
        
        # Log unlockdown
        await bot.db_manager.db.execute(
            """INSERT INTO audit_log (guild_id, event_type, user_id, details, timestamp)
            VALUES (?, ?, ?, ?, ?)""",
            (ctx.guild.id, "UNLOCKDOWN", ctx.author.id, "Lockdown lifted", datetime.utcnow().isoformat())
        )
        await bot.db_manager.db.commit()
        
        emoji = await bot.get_emoji(ctx.guild.id, "unlock")
        embed = await EmbedFactory.create(
            title="Server Lockdown Lifted",
            description=f"**Unlocked Channels:** {len(unlocked_channels)}",
            color=EmbedFactory.SUCCESS_COLOR,
            emoji=emoji,
            fields=[
                {"name": "Moderator", "value": ctx.author.mention, "inline": True}
            ]
        )
        await ctx.send(embed=embed)
        
        # Announce in all unlocked channels
        announce_embed = await EmbedFactory.create(
            title="🔓 Channel Unlocked",
            description="The lockdown has been lifted. You can now send messages.",
            color=EmbedFactory.SUCCESS_COLOR
        )
        
        for channel in unlocked_channels[:5]:  # Announce in first 5 channels
            try:
                await channel.send(embed=announce_embed)
            except:
                pass
        
    except Exception as e:
        logger.error(f"Error in unlockdown command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="Failed to lift lockdown.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

# ═══════════════════════════════════════════════════════════════
# HELP COMMAND
# ═══════════════════════════════════════════════════════════════

@bot.hybrid_command(name="help", description="Show bot commands and help")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def help_command(ctx: commands.Context):
    """Display help menu with all available commands"""
    
    prefix = bot.cache.prefixes.get(ctx.guild.id, DEFAULT_PREFIX) if ctx.guild else DEFAULT_PREFIX
    
    emoji = await bot.get_emoji(ctx.guild.id, "book") if ctx.guild else "📚"
    
    embed = await EmbedFactory.create(
        title="SRLS Bot - Command Help",
        description=f"**Prefix:** `{prefix}` or `/` (slash commands)\n\n"
                    "Use `{prefix}help <category>` for detailed command info.",
        color=EmbedFactory.INFO_COLOR,
        emoji=emoji,
        fields=[
            {
                "name": "🔨 Moderation",
                "value": f"`{prefix}warn`, `{prefix}kick`, `{prefix}ban`, `{prefix}unban`, `{prefix}mute`, `{prefix}unmute`, `{prefix}clear`",
                "inline": False
            },
            {
                "name": "📋 Cases & Warnings",
                "value": f"`{prefix}case`, `{prefix}warnings`",
                "inline": False
            },
            {
                "name": "🔒 Server Management",
                "value": f"`{prefix}lockdown`, `{prefix}unlockdown`",
                "inline": False
            },
            {
                "name": "⚙️ Configuration",
                "value": f"`{prefix}setup`, `{prefix}setprefix`, `{prefix}setmodrole`, `{prefix}setlogchannel`, `{prefix}antispam`, `{prefix}config`",
                "inline": False
            },
            {
                "name": "📊 Information",
                "value": f"`{prefix}ping`, `{prefix}serverinfo`, `{prefix}userinfo`",
                "inline": False
            },
            {
                "name": "🆘 Support",
                "value": "Need help? Join our support server or contact an administrator.",
                "inline": False
            }
        ],
        footer=f"Soviet Russia Life Simulator Bot v{BOT_VERSION}"
    )
    
    await ctx.send(embed=embed)

# ═══════════════════════════════════════════════════════════════
# STATISTICS COMMANDS
# ═══════════════════════════════════════════════════════════════

@bot.hybrid_command(name="stats", description="View server statistics")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def stats(ctx: commands.Context):
    """Display server moderation statistics"""
    
    try:
        # Get total cases
        async with bot.db_manager.db.execute(
            "SELECT COUNT(*) as count FROM cases WHERE guild_id = ?",
            (ctx.guild.id,)
        ) as cursor:
            row = await cursor.fetchone()
            total_cases = row['count'] if row else 0
        
        # Get action breakdown
        async with bot.db_manager.db.execute(
            "SELECT action, COUNT(*) as count FROM cases WHERE guild_id = ? GROUP BY action",
            (ctx.guild.id,)
        ) as cursor:
            actions = await cursor.fetchall()
        
        action_breakdown = "\n".join([f"**{row['action']}:** {row['count']}" for row in actions]) if actions else "No actions recorded"
        
        # Get active warnings
        async with bot.db_manager.db.execute(
            "SELECT COUNT(*) as count FROM warns WHERE guild_id = ? AND active = 1",
            (ctx.guild.id,)
        ) as cursor:
            row = await cursor.fetchone()
            active_warns = row['count'] if row else 0
        
        emoji = await bot.get_emoji(ctx.guild.id, "stats")
        embed = await EmbedFactory.create(
            title="Server Statistics",
            description="Moderation activity overview",
            color=EmbedFactory.INFO_COLOR,
            emoji=emoji,
            fields=[
                {"name": "📋 Total Cases", "value": f"`{total_cases}`", "inline": True},
                {"name": "⚠️ Active Warnings", "value": f"`{active_warns}`", "inline": True},
                {"name": "📊 Action Breakdown", "value": action_breakdown, "inline": False}
            ]
        )
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in stats command: {e}")
        emoji = await bot.get_emoji(ctx.guild.id, "error")
        embed = await EmbedFactory.create(
            title="Error",
            description="Failed to fetch statistics.",
            color=EmbedFactory.ERROR_COLOR,
            emoji=emoji
        )
        await ctx.send(embed=embed, delete_after=10)

# ═══════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        logger.info("═══════════════════════════════════════════════════════════════")
        logger.info("  SOVIET RUSSIA LIFE SIMULATOR - DISCORD BOT")
        logger.info("═══════════════════════════════════════════════════════════════")
        logger.info(f"Initializing bot...")
        logger.info(f"Python version: {sys.version.split()[0]}")
        logger.info(f"discord.py version: {discord.__version__}")
        
        # Run the bot with hardcoded token as per user request
        # For production, replace 'YOUR_BOT_TOKEN_HERE' with actual token
        bot.run('YOUR_BOT_TOKEN_HERE')
        
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        logger.critical(traceback.format_exc())
        sys.exit(1)
