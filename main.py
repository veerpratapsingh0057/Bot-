"""
Soviet Russia Life Simulator - Official Discord Bot
Production-level moderation and management system
Author: SRLS Development Team
License: MIT
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Union
import re
import time
from collections import defaultdict

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_PREFIX = "!"
SUCCESS_COLOR = 0x00C853
ERROR_COLOR = 0xD32F2F
FOOTER_TEXT = "Soviet Russia Life Simulator • Official Bot"

# Anti-spam configuration
SPAM_THRESHOLD = 5  # messages
SPAM_INTERVAL = 5   # seconds
SPAM_WARN_THRESHOLD = 10  # messages in interval to warn
SPAM_TIMEOUT_THRESHOLD = 15  # messages in interval to timeout

# Database path
DB_PATH = "srls_bot.db"

# ═══════════════════════════════════════════════════════════════════════════
# BOT SETUP
# ═══════════════════════════════════════════════════════════════════════════

class SRLSBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix=self.get_prefix,
            intents=intents,
            help_command=None
        )
        self.db = None
        self.spam_tracker = defaultdict(list)
        self.spam_warnings = defaultdict(int)
        
    async def get_prefix(self, message):
        """Get custom prefix for guild"""
        if not message.guild:
            return DEFAULT_PREFIX
        
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT prefix FROM prefixes WHERE guild_id = ?",
                (message.guild.id,)
            ) as cursor:
                result = await cursor.fetchone()
                return result[0] if result else DEFAULT_PREFIX
    
    async def setup_hook(self):
        """Initialize database and sync commands"""
        await self.init_db()
        await self.tree.sync()
        print("✅ Slash commands synced globally")

    async def init_db(self):
        """Initialize all database tables"""
        async with aiosqlite.connect(DB_PATH) as db:
            # Prefixes table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS prefixes (
                    guild_id INTEGER PRIMARY KEY,
                    prefix TEXT DEFAULT '!'
                )
            """)
            
            # Warnings table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS warns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    user_id INTEGER,
                    moderator_id INTEGER,
                    reason TEXT,
                    timestamp TEXT
                )
            """)
            
            # Moderation logs table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS modlogs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    action TEXT,
                    target_id INTEGER,
                    moderator_id INTEGER,
                    reason TEXT,
                    timestamp TEXT
                )
            """)
            
            # Cases table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    guild_id INTEGER,
                    action TEXT,
                    target_id INTEGER,
                    moderator_id INTEGER,
                    reason TEXT,
                    timestamp TEXT
                )
            """)
            
            # Settings table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    guild_id INTEGER PRIMARY KEY,
                    mod_role_id INTEGER,
                    mod_user_id INTEGER,
                    log_channel_id INTEGER,
                    antispam_enabled INTEGER DEFAULT 0,
                    antispam_sensitivity INTEGER DEFAULT 5
                )
            """)
            
            # Mutes table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS mutes (
                    guild_id INTEGER,
                    user_id INTEGER,
                    unmute_time TEXT,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
            
            await db.commit()
        
        print("✅ Database initialized successfully")

bot = SRLSBot()

# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

async def has_mod_permissions(interaction: discord.Interaction = None, ctx: commands.Context = None) -> bool:
    """Check if user has moderation permissions"""
    member = interaction.user if interaction else ctx.author
    guild = interaction.guild if interaction else ctx.guild
    
    # Bot owner always has permission
    if member.id == bot.owner_id:
        return True
    
    # Check for administrator
    if member.guild_permissions.administrator:
        return True
    
    # Check for configured mod role/user
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT mod_role_id, mod_user_id FROM settings WHERE guild_id = ?",
            (guild.id,)
        ) as cursor:
            result = await cursor.fetchone()
            if result:
                mod_role_id, mod_user_id = result
                if mod_role_id and discord.utils.get(member.roles, id=mod_role_id):
                    return True
                if mod_user_id and member.id == mod_user_id:
                    return True
    
    return False

async def generate_case_id(guild_id: int) -> str:
    """Generate unique case ID"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM cases WHERE guild_id = ?",
            (guild_id,)
        ) as cursor:
            count = await cursor.fetchone()
            return f"SRLS-{count[0] + 1:04d}"

async def log_moderation(guild_id: int, action: str, target_id: int, moderator_id: int, reason: str):
    """Log moderation action and create case"""
    timestamp = datetime.utcnow().isoformat()
    case_id = await generate_case_id(guild_id)
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Create case
        await db.execute(
            "INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case_id, guild_id, action, target_id, moderator_id, reason, timestamp)
        )
        
        # Log action
        await db.execute(
            "INSERT INTO modlogs (guild_id, action, target_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, action, target_id, moderator_id, reason, timestamp)
        )
        
        await db.commit()
    
    return case_id

async def send_dm_notification(user: discord.User, guild: discord.Guild, moderator: discord.Member, action: str, reason: str):
    """Send DM notification to user about moderation action"""
    try:
        embed = discord.Embed(
            title=f"🚨 Moderation Action: {action.upper()}",
            description=f"You have received a moderation action in **{guild.name}**",
            color=ERROR_COLOR,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="⚙️ Action", value=action.capitalize(), inline=True)
        embed.add_field(name="🔨 Moderator", value=moderator.mention, inline=True)
        embed.add_field(name="📝 Reason", value=reason or "No reason provided", inline=False)
        embed.set_footer(text=FOOTER_TEXT)
        
        await user.send(embed=embed)
    except discord.Forbidden:
        pass  # User has DMs disabled

def create_success_embed(title: str, description: str) -> discord.Embed:
    """Create success embed"""
    embed = discord.Embed(
        title=title,
        description=description,
        color=SUCCESS_COLOR,
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed

def create_error_embed(title: str, description: str) -> discord.Embed:
    """Create error embed"""
    embed = discord.Embed(
        title=title,
        description=description,
        color=ERROR_COLOR,
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed

def parse_time(time_str: str) -> Optional[timedelta]:
    """Parse time string like '10m', '2h', '1d' to timedelta"""
    match = re.match(r"(\d+)([smhd])", time_str.lower())
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
    
    return None

# ═══════════════════════════════════════════════════════════════════════════
# STATUS ROTATION TASK
# ═══════════════════════════════════════════════════════════════════════════

@tasks.loop(seconds=15)
async def rotate_status():
    """Rotate bot status every 15 seconds"""
    statuses = [
        discord.Game(name="Soviet Russia Life Simulator"),
        discord.Activity(type=discord.ActivityType.watching, name="Join the Revolution")
    ]
    
    current_status = statuses[rotate_status.current_loop % len(statuses)]
    await bot.change_presence(activity=current_status)

# ═══════════════════════════════════════════════════════════════════════════
# ANTI-SPAM SYSTEM
# ═══════════════════════════════════════════════════════════════════════════

@bot.event
async def on_message(message):
    """Handle anti-spam and process commands"""
    if message.author.bot or not message.guild:
        return
    
    # Check if anti-spam is enabled
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT antispam_enabled, antispam_sensitivity FROM settings WHERE guild_id = ?",
            (message.guild.id,)
        ) as cursor:
            result = await cursor.fetchone()
            antispam_enabled = result[0] if result else 0
            sensitivity = result[1] if result else SPAM_THRESHOLD
    
    if antispam_enabled:
        # Track messages
        user_id = message.author.id
        current_time = time.time()
        
        # Clean old messages
        bot.spam_tracker[user_id] = [
            t for t in bot.spam_tracker[user_id]
            if current_time - t < SPAM_INTERVAL
        ]
        
        # Add current message
        bot.spam_tracker[user_id].append(current_time)
        
        # Check spam threshold
        message_count = len(bot.spam_tracker[user_id])
        
        if message_count >= SPAM_TIMEOUT_THRESHOLD:
            # Timeout user
            try:
                await message.author.timeout(timedelta(minutes=10), reason="Automated: Excessive spam")
                await message.channel.send(
                    embed=create_error_embed(
                        "🚨 Auto-Moderation",
                        f"{message.author.mention} has been timed out for **10 minutes** due to excessive spam."
                    ),
                    delete_after=10
                )
                bot.spam_tracker[user_id] = []
                bot.spam_warnings[user_id] = 0
            except:
                pass
        
        elif message_count >= SPAM_WARN_THRESHOLD:
            bot.spam_warnings[user_id] += 1
            
            if bot.spam_warnings[user_id] == 1:
                # First warning
                try:
                    await message.channel.send(
                        f"⚠️ {message.author.mention} Please slow down! You're sending messages too quickly.",
                        delete_after=5
                    )
                except:
                    pass
        
        elif message_count >= sensitivity:
            # Delete spam messages
            try:
                await message.delete()
            except:
                pass
    
    await bot.process_commands(message)

# ═══════════════════════════════════════════════════════════════════════════
# EVENTS
# ═══════════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    """Bot startup event"""
    print("═" * 50)
    print(f"✅ Bot logged in as {bot.user}")
    print(f"✅ Bot ID: {bot.user.id}")
    print(f"✅ Connected to {len(bot.guilds)} guilds")
    print(f"✅ Default prefix: {DEFAULT_PREFIX}")
    print("═" * 50)
    
    rotate_status.start()

@bot.event
async def on_guild_join(guild):
    """Initialize guild settings when bot joins"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO prefixes (guild_id, prefix) VALUES (?, ?)",
            (guild.id, DEFAULT_PREFIX)
        )
        await db.execute(
            "INSERT OR IGNORE INTO settings (guild_id) VALUES (?)",
            (guild.id,)
        )
        await db.commit()

@bot.event
async def on_message(message):
    """Handle bot mentions"""
    if message.author.bot:
        return
    
    # Check if bot is mentioned
    if bot.user.mentioned_in(message) and not message.mention_everyone:
        prefix = await bot.get_prefix(message)
        embed = discord.Embed(
            title="⚙️ Soviet Russia Life Simulator Official Bot",
            description=(
                f"**Current Prefix:** `{prefix}`\n\n"
                f"Use `{prefix}help` to see all available commands.\n"
                f"Use `/help` for slash commands."
            ),
            color=SUCCESS_COLOR
        )
        embed.set_thumbnail(url=bot.user.display_avatar.url)
        embed.set_footer(text=FOOTER_TEXT)
        await message.channel.send(embed=embed)
    
    await bot.process_commands(message)

# ═══════════════════════════════════════════════════════════════════════════
# MODERATION COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
# WARN
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="warn", description="Warn a user")
@app_commands.describe(user="User to warn", reason="Reason for warning")
async def warn(ctx, user: discord.Member, *, reason: str = "No reason provided"):
    """Warn a user"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    # Cannot warn yourself
    if user == ctx.author:
        return await ctx.send(embed=create_error_embed("❌ Error", "You cannot warn yourself."))
    
    # Cannot warn bots
    if user.bot:
        return await ctx.send(embed=create_error_embed("❌ Error", "You cannot warn bots."))
    
    # Log warning
    timestamp = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO warns (guild_id, user_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?, ?)",
            (ctx.guild.id, user.id, ctx.author.id, reason, timestamp)
        )
        await db.commit()
        
        # Check total warnings
        async with db.execute(
            "SELECT COUNT(*) FROM warns WHERE guild_id = ? AND user_id = ?",
            (ctx.guild.id, user.id)
        ) as cursor:
            warn_count = (await cursor.fetchone())[0]
    
    # Create case
    case_id = await log_moderation(ctx.guild.id, "warn", user.id, ctx.author.id, reason)
    
    # Send DM
    await send_dm_notification(user, ctx.guild, ctx.author, "warn", reason)
    
    # Send confirmation
    embed = create_success_embed(
        "⚠️ User Warned",
        f"**User:** {user.mention}\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}\n**Case ID:** `{case_id}`\n**Total Warnings:** {warn_count}"
    )
    await ctx.send(embed=embed)
    
    # Auto-kick at 3 warnings
    if warn_count >= 3:
        try:
            await send_dm_notification(user, ctx.guild, bot.user, "auto-kick", f"Automatically kicked for reaching {warn_count} warnings")
            await user.kick(reason=f"Auto-kick: Reached {warn_count} warnings")
            await log_moderation(ctx.guild.id, "auto-kick", user.id, bot.user.id, f"Reached {warn_count} warnings")
            await ctx.send(embed=create_success_embed(
                "🔨 Auto-Kick Triggered",
                f"{user.mention} has been automatically kicked for reaching **{warn_count} warnings**."
            ))
        except:
            pass

# ─────────────────────────────────────────────────────────────────────────────
# UNWARN
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="unwarn", description="Remove warnings from a user")
@app_commands.describe(user="User to unwarn")
async def unwarn(ctx, user: discord.Member):
    """Remove all warnings from a user"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM warns WHERE guild_id = ? AND user_id = ?",
            (ctx.guild.id, user.id)
        )
        await db.commit()
    
    embed = create_success_embed(
        "✅ Warnings Cleared",
        f"All warnings have been removed from {user.mention}"
    )
    await ctx.send(embed=embed)

# ─────────────────────────────────────────────────────────────────────────────
# WARNINGS
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="warnings", description="View warnings for a user")
@app_commands.describe(user="User to check warnings for")
async def warnings(ctx, user: discord.Member):
    """View warnings for a user"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT moderator_id, reason, timestamp FROM warns WHERE guild_id = ? AND user_id = ?",
            (ctx.guild.id, user.id)
        ) as cursor:
            warns = await cursor.fetchall()
    
    if not warns:
        return await ctx.send(embed=create_success_embed(
            "✅ No Warnings",
            f"{user.mention} has no warnings."
        ))
    
    embed = discord.Embed(
        title=f"⚠️ Warnings for {user.display_name}",
        description=f"**Total Warnings:** {len(warns)}",
        color=ERROR_COLOR,
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    
    for i, (mod_id, reason, timestamp) in enumerate(warns[:10], 1):
        moderator = ctx.guild.get_member(mod_id)
        mod_name = moderator.display_name if moderator else "Unknown"
        time_str = datetime.fromisoformat(timestamp).strftime("%Y-%m-%d %H:%M")
        embed.add_field(
            name=f"Warning #{i}",
            value=f"**Moderator:** {mod_name}\n**Reason:** {reason}\n**Date:** {time_str}",
            inline=False
        )
    
    embed.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=embed)

# ─────────────────────────────────────────────────────────────────────────────
# MUTE
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="mute", description="Mute a user")
@app_commands.describe(user="User to mute", duration="Duration (e.g., 10m, 2h, 1d)", reason="Reason for mute")
async def mute(ctx, user: discord.Member, duration: str, *, reason: str = "No reason provided"):
    """Mute a user for a specified duration"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    time_delta = parse_time(duration)
    if not time_delta:
        return await ctx.send(embed=create_error_embed("❌ Invalid Duration", "Use format like: 10m, 2h, 1d"))
    
    try:
        await user.timeout(time_delta, reason=reason)
        case_id = await log_moderation(ctx.guild.id, "mute", user.id, ctx.author.id, reason)
        await send_dm_notification(user, ctx.guild, ctx.author, "mute", reason)
        
        embed = create_success_embed(
            "🔇 User Muted",
            f"**User:** {user.mention}\n**Duration:** {duration}\n**Reason:** {reason}\n**Case ID:** `{case_id}`"
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to mute this user."))

# ─────────────────────────────────────────────────────────────────────────────
# UNMUTE
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="unmute", description="Unmute a user")
@app_commands.describe(user="User to unmute")
async def unmute(ctx, user: discord.Member):
    """Unmute a user"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    try:
        await user.timeout(None)
        case_id = await log_moderation(ctx.guild.id, "unmute", user.id, ctx.author.id, "Unmuted by moderator")
        
        embed = create_success_embed(
            "🔊 User Unmuted",
            f"**User:** {user.mention}\n**Case ID:** `{case_id}`"
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to unmute this user."))

# ─────────────────────────────────────────────────────────────────────────────
# BAN
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="ban", description="Ban a user")
@app_commands.describe(user="User to ban", reason="Reason for ban")
async def ban(ctx, user: discord.Member, *, reason: str = "No reason provided"):
    """Ban a user from the server"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    if user == ctx.author:
        return await ctx.send(embed=create_error_embed("❌ Error", "You cannot ban yourself."))
    
    if user.bot:
        return await ctx.send(embed=create_error_embed("❌ Error", "You cannot ban bots."))
    
    try:
        await send_dm_notification(user, ctx.guild, ctx.author, "ban", reason)
        await user.ban(reason=reason)
        case_id = await log_moderation(ctx.guild.id, "ban", user.id, ctx.author.id, reason)
        
        embed = create_success_embed(
            "🔨 User Banned",
            f"**User:** {user.mention}\n**Reason:** {reason}\n**Case ID:** `{case_id}`"
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to ban this user."))

# ─────────────────────────────────────────────────────────────────────────────
# UNBAN
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="unban", description="Unban a user")
@app_commands.describe(user_id="User ID to unban")
async def unban(ctx, user_id: str):
    """Unban a user by their ID"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    try:
        user_id = int(user_id)
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user)
        case_id = await log_moderation(ctx.guild.id, "unban", user_id, ctx.author.id, "Unbanned by moderator")
        
        embed = create_success_embed(
            "✅ User Unbanned",
            f"**User:** {user.mention}\n**Case ID:** `{case_id}`"
        )
        await ctx.send(embed=embed)
    except ValueError:
        await ctx.send(embed=create_error_embed("❌ Error", "Invalid user ID."))
    except discord.NotFound:
        await ctx.send(embed=create_error_embed("❌ Error", "User not found or not banned."))
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to unban users."))

# ─────────────────────────────────────────────────────────────────────────────
# KICK
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="kick", description="Kick a user")
@app_commands.describe(user="User to kick", reason="Reason for kick")
async def kick(ctx, user: discord.Member, *, reason: str = "No reason provided"):
    """Kick a user from the server"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    if user == ctx.author:
        return await ctx.send(embed=create_error_embed("❌ Error", "You cannot kick yourself."))
    
    try:
        await send_dm_notification(user, ctx.guild, ctx.author, "kick", reason)
        await user.kick(reason=reason)
        case_id = await log_moderation(ctx.guild.id, "kick", user.id, ctx.author.id, reason)
        
        embed = create_success_embed(
            "👢 User Kicked",
            f"**User:** {user.mention}\n**Reason:** {reason}\n**Case ID:** `{case_id}`"
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to kick this user."))

# ─────────────────────────────────────────────────────────────────────────────
# TIMEOUT
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="timeout", description="Timeout a user")
@app_commands.describe(user="User to timeout", duration="Duration (e.g., 10m, 2h)")
async def timeout(ctx, user: discord.Member, duration: str):
    """Timeout a user for a specified duration"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    time_delta = parse_time(duration)
    if not time_delta:
        return await ctx.send(embed=create_error_embed("❌ Invalid Duration", "Use format like: 10m, 2h, 1d"))
    
    try:
        await user.timeout(time_delta, reason=f"Timeout by {ctx.author}")
        case_id = await log_moderation(ctx.guild.id, "timeout", user.id, ctx.author.id, f"Timed out for {duration}")
        
        embed = create_success_embed(
            "⏱️ User Timed Out",
            f"**User:** {user.mention}\n**Duration:** {duration}\n**Case ID:** `{case_id}`"
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to timeout this user."))

# ─────────────────────────────────────────────────────────────────────────────
# NICKNAME
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="nick", description="Change a user's nickname")
@app_commands.describe(user="User to change nickname", nickname="New nickname")
async def nick(ctx, user: discord.Member, *, nickname: str):
    """Change a user's nickname"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    try:
        old_nick = user.display_name
        await user.edit(nick=nickname)
        case_id = await log_moderation(ctx.guild.id, "nickname", user.id, ctx.author.id, f"Changed from '{old_nick}' to '{nickname}'")
        
        embed = create_success_embed(
            "✏️ Nickname Changed",
            f"**User:** {user.mention}\n**Old:** {old_nick}\n**New:** {nickname}\n**Case ID:** `{case_id}`"
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to change this user's nickname."))

# ─────────────────────────────────────────────────────────────────────────────
# ADD ROLE
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="addrole", description="Add a role to a user")
@app_commands.describe(user="User to add role to", role="Role to add")
async def addrole(ctx, user: discord.Member, role: discord.Role):
    """Add a role to a user"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    if role in user.roles:
        return await ctx.send(embed=create_error_embed("❌ Error", f"{user.mention} already has the role {role.mention}"))
    
    try:
        await user.add_roles(role)
        case_id = await log_moderation(ctx.guild.id, "addrole", user.id, ctx.author.id, f"Added role: {role.name}")
        
        embed = create_success_embed(
            "➕ Role Added",
            f"**User:** {user.mention}\n**Role:** {role.mention}\n**Case ID:** `{case_id}`"
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to manage this role."))

# ─────────────────────────────────────────────────────────────────────────────
# REMOVE ROLE
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="removerole", description="Remove a role from a user")
@app_commands.describe(user="User to remove role from", role="Role to remove")
async def removerole(ctx, user: discord.Member, role: discord.Role):
    """Remove a role from a user"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    if role not in user.roles:
        return await ctx.send(embed=create_error_embed("❌ Error", f"{user.mention} doesn't have the role {role.mention}"))
    
    try:
        await user.remove_roles(role)
        case_id = await log_moderation(ctx.guild.id, "removerole", user.id, ctx.author.id, f"Removed role: {role.name}")
        
        embed = create_success_embed(
            "➖ Role Removed",
            f"**User:** {user.mention}\n**Role:** {role.mention}\n**Case ID:** `{case_id}`"
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to manage this role."))

# ─────────────────────────────────────────────────────────────────────────────
# CLEAR
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="clear", description="Delete messages")
@app_commands.describe(amount="Number of messages to delete")
async def clear(ctx, amount: int):
    """Delete a specified number of messages"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    if amount < 1 or amount > 100:
        return await ctx.send(embed=create_error_embed("❌ Error", "Amount must be between 1 and 100."))
    
    try:
        deleted = await ctx.channel.purge(limit=amount + 1)  # +1 for the command itself
        embed = create_success_embed(
            "🗑️ Messages Cleared",
            f"Deleted **{len(deleted) - 1}** messages."
        )
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(5)
        await msg.delete()
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to delete messages."))

# ─────────────────────────────────────────────────────────────────────────────
# LOCK
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="lock", description="Lock a channel")
@app_commands.describe(channel="Channel to lock (current channel if not specified)")
async def lock(ctx, channel: discord.TextChannel = None):
    """Lock a channel"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    channel = channel or ctx.channel
    
    try:
        await channel.set_permissions(ctx.guild.default_role, send_messages=False)
        embed = create_success_embed(
            "🔒 Channel Locked",
            f"{channel.mention} has been locked."
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to lock this channel."))

# ─────────────────────────────────────────────────────────────────────────────
# UNLOCK
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="unlock", description="Unlock a channel")
@app_commands.describe(channel="Channel to unlock (current channel if not specified)")
async def unlock(ctx, channel: discord.TextChannel = None):
    """Unlock a channel"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    channel = channel or ctx.channel
    
    try:
        await channel.set_permissions(ctx.guild.default_role, send_messages=True)
        embed = create_success_embed(
            "🔓 Channel Unlocked",
            f"{channel.mention} has been unlocked."
        )
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to unlock this channel."))

# ─────────────────────────────────────────────────────────────────────────────
# SLOWMODE
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="slowmode", description="Set channel slowmode")
@app_commands.describe(seconds="Slowmode delay in seconds (0 to disable)")
async def slowmode(ctx, seconds: int):
    """Set slowmode for current channel"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    if seconds < 0 or seconds > 21600:
        return await ctx.send(embed=create_error_embed("❌ Error", "Slowmode must be between 0 and 21600 seconds (6 hours)."))
    
    try:
        await ctx.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            embed = create_success_embed("⏱️ Slowmode Disabled", "Slowmode has been disabled for this channel.")
        else:
            embed = create_success_embed("⏱️ Slowmode Enabled", f"Slowmode set to **{seconds}** seconds.")
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to edit this channel."))

# ─────────────────────────────────────────────────────────────────────────────
# ANNOUNCE
# ─────────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="announce", description="Send an announcement")
@app_commands.describe(channel="Channel to send announcement to", message="Announcement message")
async def announce(ctx, channel: discord.TextChannel, *, message: str):
    """Send an announcement to a channel"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    try:
        embed = discord.Embed(
            title="📢 Announcement",
            description=message,
            color=SUCCESS_COLOR,
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=f"Announced by {ctx.author.display_name}")
        await channel.send(embed=embed)
        
        await ctx.send(embed=create_success_embed(
            "✅ Announcement Sent",
            f"Announcement posted in {channel.mention}"
        ))
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to send messages in that channel."))

# ═══════════════════════════════════════════════════════════════════════════
# CASE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

@bot.hybrid_command(name="case", description="View a moderation case")
@app_commands.describe(case_id="Case ID to view")
async def case(ctx, case_id: str):
    """View details of a moderation case"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM cases WHERE case_id = ? AND guild_id = ?",
            (case_id.upper(), ctx.guild.id)
        ) as cursor:
            case_data = await cursor.fetchone()
    
    if not case_data:
        return await ctx.send(embed=create_error_embed("❌ Not Found", f"Case `{case_id}` not found."))
    
    _, guild_id, action, target_id, mod_id, reason, timestamp = case_data
    
    target = await bot.fetch_user(target_id)
    moderator = await bot.fetch_user(mod_id)
    
    embed = discord.Embed(
        title=f"📋 Case {case_id}",
        color=SUCCESS_COLOR,
        timestamp=datetime.fromisoformat(timestamp)
    )
    embed.add_field(name="⚙️ Action", value=action.upper(), inline=True)
    embed.add_field(name="👤 Target", value=target.mention, inline=True)
    embed.add_field(name="🔨 Moderator", value=moderator.mention, inline=True)
    embed.add_field(name="📝 Reason", value=reason, inline=False)
    embed.set_footer(text=FOOTER_TEXT)
    
    await ctx.send(embed=embed)

# ═══════════════════════════════════════════════════════════════════════════
# ADDITIONAL COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

@bot.hybrid_command(name="modlogs", description="View moderation logs for a user")
@app_commands.describe(user="User to view logs for")
async def modlogs(ctx, user: discord.Member):
    """View moderation history for a user"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT action, moderator_id, reason, timestamp FROM modlogs WHERE guild_id = ? AND target_id = ? ORDER BY timestamp DESC LIMIT 10",
            (ctx.guild.id, user.id)
        ) as cursor:
            logs = await cursor.fetchall()
    
    if not logs:
        return await ctx.send(embed=create_success_embed(
            "✅ Clean Record",
            f"{user.mention} has no moderation history."
        ))
    
    embed = discord.Embed(
        title=f"📜 Moderation Logs for {user.display_name}",
        color=ERROR_COLOR,
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    
    for action, mod_id, reason, timestamp in logs:
        moderator = ctx.guild.get_member(mod_id)
        mod_name = moderator.display_name if moderator else "Unknown"
        time_str = datetime.fromisoformat(timestamp).strftime("%Y-%m-%d %H:%M")
        embed.add_field(
            name=f"{action.upper()}",
            value=f"**Moderator:** {mod_name}\n**Reason:** {reason}\n**Date:** {time_str}",
            inline=False
        )
    
    embed.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="purgeuser", description="Delete messages from a specific user")
@app_commands.describe(user="User whose messages to delete", amount="Number of messages to check")
async def purgeuser(ctx, user: discord.Member, amount: int = 100):
    """Delete messages from a specific user"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    if amount < 1 or amount > 500:
        return await ctx.send(embed=create_error_embed("❌ Error", "Amount must be between 1 and 500."))
    
    def check(m):
        return m.author == user
    
    try:
        deleted = await ctx.channel.purge(limit=amount, check=check)
        embed = create_success_embed(
            "🗑️ Messages Purged",
            f"Deleted **{len(deleted)}** messages from {user.mention}"
        )
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(5)
        await msg.delete()
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("❌ Error", "I don't have permission to delete messages."))

@bot.hybrid_command(name="serverstats", description="Display server statistics")
async def serverstats(ctx):
    """Display server statistics"""
    guild = ctx.guild
    
    total_members = guild.member_count
    bots = sum(1 for m in guild.members if m.bot)
    humans = total_members - bots
    
    embed = discord.Embed(
        title=f"📊 {guild.name} Statistics",
        color=SUCCESS_COLOR,
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    
    embed.add_field(name="👥 Total Members", value=total_members, inline=True)
    embed.add_field(name="🤖 Bots", value=bots, inline=True)
    embed.add_field(name="👤 Humans", value=humans, inline=True)
    
    embed.add_field(name="📝 Text Channels", value=len(guild.text_channels), inline=True)
    embed.add_field(name="🔊 Voice Channels", value=len(guild.voice_channels), inline=True)
    embed.add_field(name="🎭 Roles", value=len(guild.roles), inline=True)
    
    embed.add_field(name="📅 Created", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="👑 Owner", value=guild.owner.mention, inline=True)
    embed.add_field(name="🌐 Region", value=str(guild.preferred_locale), inline=True)
    
    embed.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="lockdown", description="Lock all channels (emergency)")
async def lockdown(ctx):
    """Lock all channels in the server"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    locked = 0
    for channel in ctx.guild.text_channels:
        try:
            await channel.set_permissions(ctx.guild.default_role, send_messages=False)
            locked += 1
        except:
            pass
    
    embed = create_success_embed(
        "🚨 Server Lockdown Activated",
        f"**{locked}** channels have been locked.\n\nUse `/unlockdown` to restore normal operations."
    )
    await ctx.send(embed=embed)

@bot.hybrid_command(name="unlockdown", description="Unlock all channels")
async def unlockdown(ctx):
    """Unlock all channels in the server"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    unlocked = 0
    for channel in ctx.guild.text_channels:
        try:
            await channel.set_permissions(ctx.guild.default_role, send_messages=True)
            unlocked += 1
        except:
            pass
    
    embed = create_success_embed(
        "✅ Server Lockdown Lifted",
        f"**{unlocked}** channels have been unlocked."
    )
    await ctx.send(embed=embed)

# ═══════════════════════════════════════════════════════════════════════════
# ANTI-SPAM COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

@bot.hybrid_group(name="antispam", description="Manage anti-spam settings")
async def antispam(ctx):
    """Anti-spam management"""
    if ctx.invoked_subcommand is None:
        await ctx.send_help(antispam)

@antispam.command(name="enable", description="Enable anti-spam protection")
async def antispam_enable(ctx):
    """Enable anti-spam protection"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (guild_id, antispam_enabled) VALUES (?, 1)",
            (ctx.guild.id,)
        )
        await db.commit()
    
    await ctx.send(embed=create_success_embed(
        "✅ Anti-Spam Enabled",
        "Automatic spam protection is now active."
    ))

@antispam.command(name="disable", description="Disable anti-spam protection")
async def antispam_disable(ctx):
    """Disable anti-spam protection"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE settings SET antispam_enabled = 0 WHERE guild_id = ?",
            (ctx.guild.id,)
        )
        await db.commit()
    
    await ctx.send(embed=create_success_embed(
        "✅ Anti-Spam Disabled",
        "Automatic spam protection is now inactive."
    ))

@antispam.command(name="sensitivity", description="Set anti-spam sensitivity")
@app_commands.describe(level="Sensitivity level (1-10, default 5)")
async def antispam_sensitivity(ctx, level: int):
    """Set anti-spam sensitivity level"""
    if not await has_mod_permissions(ctx=ctx):
        return await ctx.send(embed=create_error_embed("❌ Permission Denied", "You do not have permission to perform this action."))
    
    if level < 1 or level > 10:
        return await ctx.send(embed=create_error_embed("❌ Error", "Sensitivity must be between 1 and 10."))
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE settings SET antispam_sensitivity = ? WHERE guild_id = ?",
            (level, ctx.guild.id)
        )
        await db.commit()
    
    await ctx.send(embed=create_success_embed(
        "✅ Sensitivity Updated",
        f"Anti-spam sensitivity set to **{level}**"
    ))

# ═══════════════════════════════════════════════════════════════════════════
# UTILITY COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

@bot.hybrid_command(name="ping", description="Check bot latency")
async def ping(ctx):
    """Check bot latency"""
    # API latency
    api_latency = round(bot.latency * 1000, 2)
    
    # Database latency
    start = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("SELECT 1")
    db_latency = round((time.time() - start) * 1000, 2)
    
    embed = discord.Embed(
        title="🏓 Pong!",
        color=SUCCESS_COLOR,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="API Latency", value=f"{api_latency}ms", inline=True)
    embed.add_field(name="Database Latency", value=f"{db_latency}ms", inline=True)
    embed.set_footer(text=FOOTER_TEXT)
    
    await ctx.send(embed=embed)

@bot.hybrid_command(name="userinfo", description="Get information about a user")
@app_commands.describe(user="User to get info about")
async def userinfo(ctx, user: discord.Member = None):
    """Get detailed information about a user"""
    user = user or ctx.author
    
    embed = discord.Embed(
        title=f"👤 User Information: {user.display_name}",
        color=SUCCESS_COLOR,
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    
    embed.add_field(name="Username", value=str(user), inline=True)
    embed.add_field(name="ID", value=user.id, inline=True)
    embed.add_field(name="Nickname", value=user.nick or "None", inline=True)
    
    embed.add_field(name="Account Created", value=user.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Joined Server", value=user.joined_at.strftime("%Y-%m-%d") if user.joined_at else "Unknown", inline=True)
    embed.add_field(name="Bot", value="Yes" if user.bot else "No", inline=True)
    
    roles = [role.mention for role in user.roles[1:]]  # Exclude @everyone
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles[:10]) if roles else "None", inline=False)
    
    embed.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="serverinfo", description="Get server information")
async def serverinfo(ctx):
    """Get detailed server information"""
    guild = ctx.guild
    
    embed = discord.Embed(
        title=f"🏛️ {guild.name}",
        color=SUCCESS_COLOR,
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    
    embed.add_field(name="Owner", value=guild.owner.mention, inline=True)
    embed.add_field(name="Server ID", value=guild.id, inline=True)
    embed.add_field(name="Created", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
    
    total = guild.member_count
    bots = sum(1 for m in guild.members if m.bot)
    embed.add_field(name="Members", value=f"Total: {total}\nHumans: {total - bots}\nBots: {bots}", inline=True)
    
    embed.add_field(name="Channels", value=f"Text: {len(guild.text_channels)}\nVoice: {len(guild.voice_channels)}", inline=True)
    embed.add_field(name="Roles", value=len(guild.roles), inline=True)
    
    embed.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="invite", description="Get bot invite link")
async def invite(ctx):
    """Get bot invite link and support server"""
    embed = discord.Embed(
        title="⚙️ Invite Soviet Russia Life Simulator Bot",
        description="Add this bot to your server!",
        color=SUCCESS_COLOR
    )
    
    invite_url = f"https://discord.com/api/oauth2/authorize?client_id={bot.user.id}&permissions=8&scope=bot%20applications.commands"
    
    embed.add_field(name="📥 Invite Bot", value=f"[Click Here]({invite_url})", inline=False)
    embed.add_field(name="🔧 Support Server", value="[Join Support Server](https://discord.gg/srls)", inline=False)
    
    embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.set_footer(text=FOOTER_TEXT)
    
    await ctx.send(embed=embed)

# ═══════════════════════════════════════════════════════════════════════════
# HELP COMMAND
# ═══════════════════════════════════════════════════════════════════════════

@bot.hybrid_command(name="help", description="Soviet Command Center")
async def help_command(ctx):
    """Display help menu"""
    prefix = await bot.get_prefix(ctx.message) if isinstance(ctx, commands.Context) else "/"
    
    embed = discord.Embed(
        title="⚙️ Soviet Command Center",
        description=f"**Current Prefix:** `{prefix}`\n\nAll commands support both slash (`/`) and prefix (`{prefix}`) usage.",
        color=SUCCESS_COLOR,
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=bot.user.display_avatar.url)
    
    # Moderation
    embed.add_field(
        name="🔨 Moderation",
        value=(
            "`warn` `unwarn` `warnings`\n"
            "`mute` `unmute` `ban` `unban`\n"
            "`kick` `timeout` `nick`\n"
            "`addrole` `removerole`\n"
            "`clear` `purgeuser`"
        ),
        inline=True
    )
    
    # Channel Management
    embed.add_field(
        name="🔒 Channel Control",
        value=(
            "`lock` `unlock`\n"
            "`lockdown` `unlockdown`\n"
            "`slowmode` `announce`"
        ),
        inline=True
    )
    
    # Information
    embed.add_field(
        name="📊 Information",
        value=(
            "`case` `modlogs`\n"
            "`userinfo` `serverinfo`\n"
            "`serverstats` `ping`"
        ),
        inline=True
    )
    
    # Anti-Spam
    embed.add_field(
        name="🚨 Anti-Spam",
        value=(
            "`antispam enable`\n"
            "`antispam disable`\n"
            "`antispam sensitivity`"
        ),
        inline=True
    )
    
    # Configuration
    embed.add_field(
        name="⚙️ Configuration",
        value=(
            "`setup`\n"
            "`prefix`"
        ),
        inline=True
    )
    
    # Utility
    embed.add_field(
        name="🛠️ Utility",
        value=(
            "`invite`\n"
            "`help`"
        ),
        inline=True
    )
    
    embed.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=embed)

# ═══════════════════════════════════════════════════════════════════════════
# SETUP SYSTEM
# ═══════════════════════════════════════════════════════════════════════════

class SetupView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=180)
        self.ctx = ctx
    
    @discord.ui.button(label="Configure Moderator Role", style=discord.ButtonStyle.primary, emoji="🎭")
    async def mod_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Only the command user can use this!", ephemeral=True)
        
        await interaction.response.send_message("Please mention the role you want to set as moderator role:", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', check=check, timeout=60)
            if msg.role_mentions:
                role = msg.role_mentions[0]
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "INSERT OR REPLACE INTO settings (guild_id, mod_role_id) VALUES (?, ?)",
                        (interaction.guild.id, role.id)
                    )
                    await db.commit()
                
                await interaction.followup.send(
                    embed=create_success_embed(
                        "✅ Setup Successful",
                        f"{role.mention} can now perform moderation tasks."
                    )
                )
            else:
                await interaction.followup.send("No role mentioned!", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("Setup timed out!", ephemeral=True)
    
    @discord.ui.button(label="Configure Moderator User", style=discord.ButtonStyle.primary, emoji="👤")
    async def mod_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Only the command user can use this!", ephemeral=True)
        
        await interaction.response.send_message("Please mention the user you want to set as moderator:", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', check=check, timeout=60)
            if msg.mentions:
                user = msg.mentions[0]
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "INSERT OR REPLACE INTO settings (guild_id, mod_user_id) VALUES (?, ?)",
                        (interaction.guild.id, user.id)
                    )
                    await db.commit()
                
                await interaction.followup.send(
                    embed=create_success_embed(
                        "✅ Setup Successful",
                        f"{user.mention} can now perform moderation tasks."
                    )
                )
            else:
                await interaction.followup.send("No user mentioned!", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("Setup timed out!", ephemeral=True)
    
    @discord.ui.button(label="Change Prefix", style=discord.ButtonStyle.primary, emoji="🔧")
    async def change_prefix(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Only the command user can use this!", ephemeral=True)
        
        await interaction.response.send_message("Please type the new prefix:", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', check=check, timeout=60)
            new_prefix = msg.content.strip()
            
            if len(new_prefix) > 5:
                return await interaction.followup.send("Prefix must be 5 characters or less!", ephemeral=True)
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO prefixes (guild_id, prefix) VALUES (?, ?)",
                    (interaction.guild.id, new_prefix)
                )
                await db.commit()
            
            await interaction.followup.send(
                embed=create_success_embed(
                    "✅ Prefix Changed",
                    f"New prefix: `{new_prefix}`"
                )
            )
        except asyncio.TimeoutError:
            await interaction.followup.send("Setup timed out!", ephemeral=True)
    
    @discord.ui.button(label="View Settings", style=discord.ButtonStyle.secondary, emoji="📋")
    async def view_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Only the command user can use this!", ephemeral=True)
        
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT prefix FROM prefixes WHERE guild_id = ?",
                (interaction.guild.id,)
            ) as cursor:
                prefix_data = await cursor.fetchone()
            
            async with db.execute(
                "SELECT mod_role_id, mod_user_id, antispam_enabled, antispam_sensitivity FROM settings WHERE guild_id = ?",
                (interaction.guild.id,)
            ) as cursor:
                settings_data = await cursor.fetchone()
        
        prefix = prefix_data[0] if prefix_data else DEFAULT_PREFIX
        
        embed = discord.Embed(
            title="⚙️ Current Server Settings",
            color=SUCCESS_COLOR,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(name="Prefix", value=f"`{prefix}`", inline=True)
        
        if settings_data:
            mod_role_id, mod_user_id, antispam, sensitivity = settings_data
            
            mod_role = interaction.guild.get_role(mod_role_id) if mod_role_id else None
            mod_user = interaction.guild.get_member(mod_user_id) if mod_user_id else None
            
            embed.add_field(
                name="Moderator Role",
                value=mod_role.mention if mod_role else "Not Set",
                inline=True
            )
            embed.add_field(
                name="Moderator User",
                value=mod_user.mention if mod_user else "Not Set",
                inline=True
            )
            embed.add_field(
                name="Anti-Spam",
                value="Enabled" if antispam else "Disabled",
                inline=True
            )
            embed.add_field(
                name="Sensitivity",
                value=str(sensitivity),
                inline=True
            )
        
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.hybrid_command(name="setup", description="Configure bot settings")
async def setup(ctx):
    """Interactive setup menu"""
    if not ctx.author.guild_permissions.administrator:
        if not await has_mod_permissions(ctx=ctx):
            return await ctx.send(embed=create_error_embed(
                "❌ Permission Denied",
                "You do not have permission to perform this action."
            ))
    
    embed = discord.Embed(
        title="⚙️ Server Setup",
        description="Configure your server settings using the buttons below.",
        color=SUCCESS_COLOR
    )
    embed.set_footer(text=FOOTER_TEXT)
    
    view = SetupView(ctx)
    await ctx.send(embed=embed, view=view)

@bot.hybrid_command(name="prefix", description="Change server prefix")
@app_commands.describe(new_prefix="New command prefix")
async def prefix(ctx, new_prefix: str = None):
    """Change or view server prefix"""
    if new_prefix:
        if not ctx.author.guild_permissions.administrator:
            return await ctx.send(embed=create_error_embed(
                "❌ Permission Denied",
                "Only administrators can change the prefix."
            ))
        
        if len(new_prefix) > 5:
            return await ctx.send(embed=create_error_embed("❌ Error", "Prefix must be 5 characters or less."))
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO prefixes (guild_id, prefix) VALUES (?, ?)",
                (ctx.guild.id, new_prefix)
            )
            await db.commit()
        
        await ctx.send(embed=create_success_embed(
            "✅ Prefix Changed",
            f"New prefix: `{new_prefix}`"
        ))
    else:
        current_prefix = await bot.get_prefix(ctx.message) if isinstance(ctx, commands.Context) else DEFAULT_PREFIX
        await ctx.send(embed=create_success_embed(
            "Current Prefix",
            f"The current prefix is: `{current_prefix}`"
        ))

# ═══════════════════════════════════════════════════════════════════════════
# RUN BOT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    bot.run("YOUR_BOT_TOKEN_HERE")
