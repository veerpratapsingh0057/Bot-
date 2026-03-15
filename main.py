"""
Soviet Russia Life Simulator - Discord Bot
Simple, Reliable Moderation System

Version: 1.0.0
Python: 3.8+
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite
import asyncio
import logging
import sys
from datetime import datetime, timedelta
import json
import re
import time

# Configuration
DATABASE_PATH = "srls_bot.db"
DEFAULT_PREFIX = "!"
BOT_VERSION = "1.0.0"

EMOJIS = {
    "success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️",
    "hammer": "🔨", "gear": "⚙️", "lock": "🔒", "unlock": "🔓",
    "kick": "👢", "mute": "🔇", "unmute": "🔊", "case": "📋",
    "stats": "📊", "user": "👤", "ping": "🏓", "book": "📚"
}

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('SRLSBot')

# Globals
db = None
prefix_cache = {}
spam_tracker = {}
rate_limits = {}

# Database functions
async def init_database():
    global db
    try:
        logger.info("Database: Connecting...")
        db = await aiosqlite.connect(DATABASE_PATH)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.commit()
        logger.info("Database: Connected")
        logger.info("Database: Creating tables...")
        
        tables = [
            "CREATE TABLE IF NOT EXISTS prefixes (guild_id INTEGER PRIMARY KEY, prefix TEXT DEFAULT '!')",
            "CREATE TABLE IF NOT EXISTS warns (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, user_id INTEGER, moderator_id INTEGER, reason TEXT, timestamp TEXT, active INTEGER DEFAULT 1)",
            "CREATE TABLE IF NOT EXISTS modlogs (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, action TEXT, target_id INTEGER, moderator_id INTEGER, reason TEXT, timestamp TEXT)",
            "CREATE TABLE IF NOT EXISTS cases (case_id TEXT PRIMARY KEY, guild_id INTEGER, action TEXT, target_id INTEGER, moderator_id INTEGER, reason TEXT, timestamp TEXT)",
            "CREATE TABLE IF NOT EXISTS settings (guild_id INTEGER PRIMARY KEY, mod_role_id INTEGER, log_channel_id INTEGER, antispam_enabled INTEGER DEFAULT 0, antispam_sensitivity INTEGER DEFAULT 5)",
            "CREATE TABLE IF NOT EXISTS active_mutes (guild_id INTEGER, user_id INTEGER, unmute_time TEXT, reason TEXT, PRIMARY KEY (guild_id, user_id))"
        ]
        
        for table_sql in tables:
            await db.execute(table_sql)
        
        await db.commit()
        logger.info("Database: Tables created")
        return True
    except Exception as e:
        logger.error(f"Database error: {e}")
        return False

async def get_prefix(guild_id):
    if guild_id in prefix_cache:
        return prefix_cache[guild_id]
    try:
        async with db.execute("SELECT prefix FROM prefixes WHERE guild_id = ?", (guild_id,)) as cursor:
            row = await cursor.fetchone()
            prefix = row['prefix'] if row else DEFAULT_PREFIX
            prefix_cache[guild_id] = prefix
            return prefix
    except:
        return DEFAULT_PREFIX

async def generate_case_id(guild_id):
    try:
        async with db.execute("SELECT COUNT(*) as count FROM cases WHERE guild_id = ?", (guild_id,)) as cursor:
            row = await cursor.fetchone()
            count = (row['count'] if row else 0) + 1
            return f"SRLS-{count:04d}"
    except:
        return f"SRLS-{int(time.time()) % 10000:04d}"

async def log_action(guild_id, action, target_id, moderator_id, reason):
    try:
        case_id = await generate_case_id(guild_id)
        timestamp = datetime.utcnow().isoformat()
        await db.execute("INSERT INTO cases (case_id, guild_id, action, target_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (case_id, guild_id, action, target_id, moderator_id, reason, timestamp))
        await db.execute("INSERT INTO modlogs (guild_id, action, target_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                        (guild_id, action, target_id, moderator_id, reason, timestamp))
        await db.commit()
        return case_id
    except Exception as e:
        logger.error(f"Error logging action: {e}")
        return None

# Helper functions
def parse_duration(duration_str):
    match = re.match(r'(\d+)([smhdw])', duration_str.lower())
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

def create_embed(title, description, color, emoji=None):
    if emoji:
        title = f"{emoji} {title}"
    embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.utcnow())
    embed.set_footer(text="Soviet Russia Life Simulator Bot")
    return embed

def check_spam(user_id, message_content):
    now = time.time()
    if user_id not in spam_tracker:
        spam_tracker[user_id] = []
    spam_tracker[user_id] = [msg for msg in spam_tracker[user_id] if now - msg['time'] < 10]
    spam_tracker[user_id].append({'content': message_content, 'time': now})
    recent = spam_tracker[user_id]
    recent_5s = [msg for msg in recent if now - msg['time'] < 5]
    if len(recent_5s) >= 5:
        return True, "Too many messages"
    contents = [msg['content'] for msg in recent]
    if contents.count(message_content) >= 3:
        return True, "Duplicate messages"
    return False, ""

# Bot setup
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
bot.start_time = datetime.utcnow()

# Events
@bot.event
async def on_ready():
    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info("  SOVIET RUSSIA LIFE SIMULATOR - DISCORD BOT")
    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info(f"Logged in as {bot.user.name} (ID: {bot.user.id})")
    logger.info(f"Connected to {len(bot.guilds)} guilds")
    logger.info(f"Serving {sum(g.member_count for g in bot.guilds)} users")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash commands")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")
    status_rotation.start()
    unmute_checker.start()
    logger.info("Bot is ready!")
    logger.info("═══════════════════════════════════════════════════════════════")

@bot.event
async def on_guild_join(guild):
    logger.info(f"Joined guild: {guild.name} (ID: {guild.id})")
    try:
        await db.execute("INSERT OR IGNORE INTO prefixes (guild_id, prefix) VALUES (?, ?)", (guild.id, DEFAULT_PREFIX))
        await db.commit()
    except:
        pass

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    try:
        async with db.execute("SELECT antispam_enabled FROM settings WHERE guild_id = ?", (message.guild.id,)) as cursor:
            row = await cursor.fetchone()
            if row and row['antispam_enabled']:
                if not message.author.guild_permissions.administrator:
                    is_spam, reason = check_spam(message.author.id, message.content)
                    if is_spam:
                        try:
                            await message.delete()
                            await message.author.timeout(timedelta(minutes=5), reason=f"Spam: {reason}")
                            logger.warning(f"Anti-spam triggered for {message.author.id}: {reason}")
                        except:
                            pass
                        return
    except:
        pass
    await bot.process_commands(message)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏱️ Please wait {error.retry_after:.1f}s before using this command again.", delete_after=5)
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.", delete_after=5)
        return
    if isinstance(error, commands.BotMissingPermissions):
        await ctx.send(f"❌ I need these permissions: {', '.join(error.missing_permissions)}", delete_after=5)
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument: `{error.param.name}`", delete_after=5)
        return
    logger.error(f"Command error: {error}")
    await ctx.send("❌ An error occurred.", delete_after=5)

# Background tasks
@tasks.loop(seconds=15)
async def status_rotation():
    statuses = [
        discord.Game(name="Soviet Russia Life Simulator"),
        discord.Activity(type=discord.ActivityType.watching, name=f"{len(bot.guilds)} Servers"),
        discord.Activity(type=discord.ActivityType.listening, name="Moderation Commands"),
    ]
    status = statuses[status_rotation.current_loop % len(statuses)]
    await bot.change_presence(activity=status)

@status_rotation.before_loop
async def before_status():
    await bot.wait_until_ready()

@tasks.loop(seconds=60)
async def unmute_checker():
    try:
        now = datetime.utcnow()
        async with db.execute("SELECT * FROM active_mutes WHERE unmute_time <= ?", (now.isoformat(),)) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                guild = bot.get_guild(row['guild_id'])
                if guild:
                    member = guild.get_member(row['user_id'])
                    if member:
                        try:
                            await member.timeout(None, reason="Mute expired")
                            await db.execute("DELETE FROM active_mutes WHERE guild_id = ? AND user_id = ?", 
                                           (row['guild_id'], row['user_id']))
                        except:
                            pass
        await db.commit()
    except:
        pass

@unmute_checker.before_loop
async def before_unmute():
    await bot.wait_until_ready()

# Commands
@bot.hybrid_command(name="ping")
async def ping(ctx):
    """Check bot latency"""
    latency = round(bot.latency * 1000)
    uptime = datetime.utcnow() - bot.start_time
    embed = create_embed("Pong!", f"**Latency:** {latency}ms\n**Uptime:** {uptime.days}d {uptime.seconds//3600}h", discord.Color.blue(), EMOJIS['ping'])
    await ctx.send(embed=embed)

@bot.hybrid_command(name="serverinfo")
async def serverinfo(ctx):
    """Server information"""
    guild = ctx.guild
    embed = create_embed(guild.name, f"**Members:** {guild.member_count}\n**Created:** <t:{int(guild.created_at.timestamp())}:R>", discord.Color.blue(), EMOJIS['info'])
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="userinfo")
async def userinfo(ctx, member: discord.Member = None):
    """User information"""
    member = member or ctx.author
    warn_count = 0
    try:
        async with db.execute("SELECT COUNT(*) as c FROM warns WHERE guild_id = ? AND user_id = ? AND active = 1", (ctx.guild.id, member.id)) as cursor:
            row = await cursor.fetchone()
            warn_count = row['c'] if row else 0
    except:
        pass
    embed = create_embed(member.name, f"**ID:** {member.id}\n**Joined:** <t:{int(member.joined_at.timestamp())}:R>\n**Warnings:** {warn_count}",
                        member.color if member.color != discord.Color.default() else discord.Color.blue(), EMOJIS['user'])
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="warn")
@commands.has_permissions(moderate_members=True)
async def warn(ctx, member: discord.Member, *, reason: str):
    """Warn a user"""
    if member == ctx.author:
        await ctx.send("❌ You can't warn yourself.", delete_after=5)
        return
    try:
        timestamp = datetime.utcnow().isoformat()
        await db.execute("INSERT INTO warns (guild_id, user_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?, ?)",
                        (ctx.guild.id, member.id, ctx.author.id, reason, timestamp))
        async with db.execute("SELECT COUNT(*) as c FROM warns WHERE guild_id = ? AND user_id = ? AND active = 1", (ctx.guild.id, member.id)) as cursor:
            row = await cursor.fetchone()
            warn_count = row['c'] if row else 0
        await db.commit()
        case_id = await log_action(ctx.guild.id, "WARN", member.id, ctx.author.id, reason)
        embed = create_embed("User Warned", f"**User:** {member.mention}\n**Reason:** {reason}\n**Warnings:** {warn_count}\n**Case:** {case_id}",
                            discord.Color.green(), EMOJIS['warning'])
        await ctx.send(embed=embed)
        try:
            dm_embed = create_embed("Warning", f"You were warned in **{ctx.guild.name}**\n**Reason:** {reason}\n**Case:** {case_id}",
                                   discord.Color.orange(), EMOJIS['warning'])
            await member.send(embed=dm_embed)
        except:
            pass
        if warn_count >= 3:
            try:
                await member.kick(reason="3 warnings accumulated")
                await ctx.send(f"🔨 {member.mention} was auto-kicked for 3 warnings.")
            except:
                pass
    except Exception as e:
        logger.error(f"Warn error: {e}")
        await ctx.send("❌ Failed to warn user.", delete_after=5)

@bot.hybrid_command(name="kick")
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason: str):
    """Kick a user"""
    if member == ctx.author:
        await ctx.send("❌ You can't kick yourself.", delete_after=5)
        return
    try:
        try:
            dm = create_embed("Kicked", f"You were kicked from **{ctx.guild.name}**\n**Reason:** {reason}", discord.Color.red(), EMOJIS['kick'])
            await member.send(embed=dm)
        except:
            pass
        await member.kick(reason=reason)
        case_id = await log_action(ctx.guild.id, "KICK", member.id, ctx.author.id, reason)
        embed = create_embed("User Kicked", f"**User:** {member.mention}\n**Reason:** {reason}\n**Case:** {case_id}", discord.Color.green(), EMOJIS['kick'])
        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f"Kick error: {e}")
        await ctx.send("❌ Failed to kick user.", delete_after=5)

@bot.hybrid_command(name="ban")
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason: str):
    """Ban a user"""
    if member == ctx.author:
        await ctx.send("❌ You can't ban yourself.", delete_after=5)
        return
    if len(reason) < 10:
        await ctx.send("❌ Reason must be at least 10 characters.", delete_after=5)
        return
    try:
        try:
            dm = create_embed("Banned", f"You were banned from **{ctx.guild.name}**\n**Reason:** {reason}", discord.Color.red(), EMOJIS['hammer'])
            await member.send(embed=dm)
        except:
            pass
        await member.ban(reason=reason, delete_message_days=1)
        case_id = await log_action(ctx.guild.id, "BAN", member.id, ctx.author.id, reason)
        embed = create_embed("User Banned", f"**User:** {member.mention}\n**Reason:** {reason}\n**Case:** {case_id}", discord.Color.green(), EMOJIS['hammer'])
        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f"Ban error: {e}")
        await ctx.send("❌ Failed to ban user.", delete_after=5)

@bot.hybrid_command(name="unban")
@commands.has_permissions(ban_members=True)
async def unban(ctx, user_id: str, *, reason: str = "No reason"):
    """Unban a user by ID"""
    try:
        user = await bot.fetch_user(int(user_id))
        await ctx.guild.unban(user, reason=reason)
        case_id = await log_action(ctx.guild.id, "UNBAN", user.id, ctx.author.id, reason)
        embed = create_embed("User Unbanned", f"**User:** {user.mention}\n**Reason:** {reason}\n**Case:** {case_id}", discord.Color.green(), EMOJIS['success'])
        await ctx.send(embed=embed)
    except:
        await ctx.send("❌ User not found or not banned.", delete_after=5)

@bot.hybrid_command(name="mute")
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, duration: str, *, reason: str):
    """Timeout a user"""
    if member == ctx.author:
        await ctx.send("❌ You can't mute yourself.", delete_after=5)
        return
    duration_delta = parse_duration(duration)
    if not duration_delta:
        await ctx.send("❌ Invalid duration. Use: 10s, 5m, 2h, 1d", delete_after=5)
        return
    if duration_delta > timedelta(days=28):
        await ctx.send("❌ Max duration is 28 days.", delete_after=5)
        return
    try:
        await member.timeout(duration_delta, reason=reason)
        unmute_time = datetime.utcnow() + duration_delta
        await db.execute("INSERT OR REPLACE INTO active_mutes (guild_id, user_id, unmute_time, reason) VALUES (?, ?, ?, ?)",
                        (ctx.guild.id, member.id, unmute_time.isoformat(), reason))
        await db.commit()
        case_id = await log_action(ctx.guild.id, "MUTE", member.id, ctx.author.id, f"{reason} ({duration})")
        embed = create_embed("User Muted", f"**User:** {member.mention}\n**Duration:** {duration}\n**Reason:** {reason}\n**Case:** {case_id}",
                            discord.Color.green(), EMOJIS['mute'])
        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f"Mute error: {e}")
        await ctx.send("❌ Failed to mute user.", delete_after=5)

@bot.hybrid_command(name="unmute")
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member, *, reason: str = "No reason"):
    """Remove timeout"""
    try:
        await member.timeout(None, reason=reason)
        await db.execute("DELETE FROM active_mutes WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, member.id))
        await db.commit()
        case_id = await log_action(ctx.guild.id, "UNMUTE", member.id, ctx.author.id, reason)
        embed = create_embed("User Unmuted", f"**User:** {member.mention}\n**Reason:** {reason}\n**Case:** {case_id}", discord.Color.green(), EMOJIS['unmute'])
        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f"Unmute error: {e}")
        await ctx.send("❌ Failed to unmute user.", delete_after=5)

@bot.hybrid_command(name="clear")
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int = 10):
    """Clear messages"""
    if amount < 1 or amount > 100:
        await ctx.send("❌ Amount must be 1-100.", delete_after=5)
        return
    try:
        deleted = await ctx.channel.purge(limit=amount + 1)
        msg = await ctx.send(f"✅ Deleted {len(deleted)-1} messages.")
        await asyncio.sleep(3)
        await msg.delete()
    except:
        await ctx.send("❌ Failed to clear messages.", delete_after=5)

@bot.hybrid_command(name="warnings")
async def warnings(ctx, member: discord.Member = None):
    """View warnings"""
    member = member or ctx.author
    try:
        async with db.execute("SELECT * FROM warns WHERE guild_id = ? AND user_id = ? AND active = 1 ORDER BY timestamp DESC LIMIT 10",
                             (ctx.guild.id, member.id)) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            embed = create_embed("No Warnings", f"{member.mention} has no active warnings.", discord.Color.green(), EMOJIS['success'])
            await ctx.send(embed=embed)
            return
        warning_text = "\n\n".join([f"**{i+1}.** <t:{int(datetime.fromisoformat(row['timestamp']).timestamp())}:R>\n└ {row['reason']}"
                                   for i, row in enumerate(rows)])
        embed = create_embed(f"Warnings for {member.name}", warning_text, discord.Color.orange(), EMOJIS['warning'])
        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f"Warnings error: {e}")
        await ctx.send("❌ Failed to fetch warnings.", delete_after=5)

@bot.hybrid_command(name="case")
async def case(ctx, case_id: str):
    """View case details"""
    try:
        async with db.execute("SELECT * FROM cases WHERE case_id = ? AND guild_id = ?", (case_id.upper(), ctx.guild.id)) as cursor:
            row = await cursor.fetchone()
        if not row:
            await ctx.send("❌ Case not found.", delete_after=5)
            return
        try:
            target = await bot.fetch_user(row['target_id'])
            target_str = f"{target.mention} ({target})"
        except:
            target_str = f"User ID: {row['target_id']}"
        embed = create_embed(f"Case {row['case_id']}", 
                            f"**Action:** {row['action']}\n**Target:** {target_str}\n**Reason:** {row['reason']}\n**Time:** <t:{int(datetime.fromisoformat(row['timestamp']).timestamp())}:F>",
                            discord.Color.blue(), EMOJIS['case'])
        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f"Case error: {e}")
        await ctx.send("❌ Failed to fetch case.", delete_after=5)

@bot.hybrid_command(name="setprefix")
@commands.has_permissions(administrator=True)
async def setprefix(ctx, prefix: str):
    """Set bot prefix"""
    if len(prefix) > 5:
        await ctx.send("❌ Prefix must be 5 characters or less.", delete_after=5)
        return
    try:
        await db.execute("INSERT OR REPLACE INTO prefixes (guild_id, prefix) VALUES (?, ?)", (ctx.guild.id, prefix))
        await db.commit()
        prefix_cache[ctx.guild.id] = prefix
        embed = create_embed("Prefix Updated", f"Prefix set to `{prefix}`", discord.Color.green(), EMOJIS['success'])
        await ctx.send(embed=embed)
    except:
        await ctx.send("❌ Failed to set prefix.", delete_after=5)

@bot.hybrid_command(name="setmodrole")
@commands.has_permissions(administrator=True)
async def setmodrole(ctx, role: discord.Role):
    """Set moderator role"""
    try:
        await db.execute("INSERT INTO settings (guild_id, mod_role_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET mod_role_id = ?",
                        (ctx.guild.id, role.id, role.id))
        await db.commit()
        embed = create_embed("Moderator Role Set", f"Moderator role set to {role.mention}", discord.Color.green(), EMOJIS['success'])
        await ctx.send(embed=embed)
    except:
        await ctx.send("❌ Failed to set moderator role.", delete_after=5)

@bot.hybrid_command(name="setlogchannel")
@commands.has_permissions(administrator=True)
async def setlogchannel(ctx, channel: discord.TextChannel):
    """Set log channel"""
    try:
        await db.execute("INSERT INTO settings (guild_id, log_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = ?",
                        (ctx.guild.id, channel.id, channel.id))
        await db.commit()
        embed = create_embed("Log Channel Set", f"Log channel set to {channel.mention}", discord.Color.green(), EMOJIS['success'])
        await ctx.send(embed=embed)
    except:
        await ctx.send("❌ Failed to set log channel.", delete_after=5)

@bot.hybrid_command(name="antispam")
@commands.has_permissions(administrator=True)
async def antispam(ctx, action: str):
    """Toggle anti-spam (enable/disable)"""
    if action.lower() not in ['enable', 'disable']:
        await ctx.send("❌ Use: !antispam enable or !antispam disable", delete_after=5)
        return
    enabled = 1 if action.lower() == 'enable' else 0
    try:
        await db.execute("INSERT INTO settings (guild_id, antispam_enabled) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET antispam_enabled = ?",
                        (ctx.guild.id, enabled, enabled))
        await db.commit()
        status = "enabled" if enabled else "disabled"
        embed = create_embed("Anti-Spam Updated", f"Anti-spam {status}", discord.Color.green(), EMOJIS['success'])
        await ctx.send(embed=embed)
    except:
        await ctx.send("❌ Failed to update anti-spam.", delete_after=5)

@bot.hybrid_command(name="config")
@commands.has_permissions(administrator=True)
async def config(ctx):
    """View configuration"""
    try:
        prefix = await get_prefix(ctx.guild.id)
        async with db.execute("SELECT * FROM settings WHERE guild_id = ?", (ctx.guild.id,)) as cursor:
            settings = await cursor.fetchone()
        config_text = f"**Prefix:** `{prefix}`\n"
        if settings:
            if settings['mod_role_id']:
                role = ctx.guild.get_role(settings['mod_role_id'])
                config_text += f"**Mod Role:** {role.mention if role else 'Not set'}\n"
            if settings['log_channel_id']:
                channel = ctx.guild.get_channel(settings['log_channel_id'])
                config_text += f"**Log Channel:** {channel.mention if channel else 'Not set'}\n"
            antispam = "✅ Enabled" if settings['antispam_enabled'] else "❌ Disabled"
            config_text += f"**Anti-Spam:** {antispam}\n"
        embed = create_embed("Server Configuration", config_text, discord.Color.blue(), EMOJIS['gear'])
        await ctx.send(embed=embed)
    except:
        await ctx.send("❌ Failed to fetch config.", delete_after=5)

@bot.hybrid_command(name="lockdown")
@commands.has_permissions(administrator=True)
async def lockdown(ctx, *, reason: str = "Emergency"):
    """Lock all channels"""
    locked = 0
    for channel in ctx.guild.text_channels:
        try:
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = False
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
            locked += 1
        except:
            pass
    embed = create_embed("Lockdown Activated", f"**Reason:** {reason}\n**Locked:** {locked} channels", discord.Color.red(), EMOJIS['lock'])
    await ctx.send(embed=embed)

@bot.hybrid_command(name="unlockdown")
@commands.has_permissions(administrator=True)
async def unlockdown(ctx):
    """Unlock all channels"""
    unlocked = 0
    for channel in ctx.guild.text_channels:
        try:
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = None
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
            unlocked += 1
        except:
            pass
    embed = create_embed("Lockdown Lifted", f"**Unlocked:** {unlocked} channels", discord.Color.green(), EMOJIS['unlock'])
    await ctx.send(embed=embed)

@bot.hybrid_command(name="stats")
async def stats(ctx):
    """Server statistics"""
    try:
        async with db.execute("SELECT COUNT(*) as c FROM cases WHERE guild_id = ?", (ctx.guild.id,)) as cursor:
            row = await cursor.fetchone()
            total_cases = row['c'] if row else 0
        async with db.execute("SELECT COUNT(*) as c FROM warns WHERE guild_id = ? AND active = 1", (ctx.guild.id,)) as cursor:
            row = await cursor.fetchone()
            active_warns = row['c'] if row else 0
        embed = create_embed("Server Statistics", f"**Total Cases:** {total_cases}\n**Active Warnings:** {active_warns}", discord.Color.blue(), EMOJIS['stats'])
        await ctx.send(embed=embed)
    except:
        await ctx.send("❌ Failed to fetch stats.", delete_after=5)

@bot.hybrid_command(name="help")
async def help_cmd(ctx):
    """Help menu"""
    embed = create_embed("SRLS Bot Commands", 
                        "**Moderation:**\n`warn`, `kick`, `ban`, `unban`, `mute`, `unmute`, `clear`\n\n"
                        "**Info:**\n`ping`, `serverinfo`, `userinfo`, `warnings`, `case`, `stats`\n\n"
                        "**Config:**\n`setprefix`, `setmodrole`, `setlogchannel`, `antispam`, `config`\n\n"
                        "**Server:**\n`lockdown`, `unlockdown`",
                        discord.Color.blue(), EMOJIS['book'])
    await ctx.send(embed=embed)

# Startup
async def main():
    async with bot:
        await init_database()
        await bot.start(os.getenv("TOKEN"))

if __name__ == "__main__":
    try:
        logger.info("═══════════════════════════════════════════════════════════════")
        logger.info("  SOVIET RUSSIA LIFE SIMULATOR - DISCORD BOT")
        logger.info("═══════════════════════════════════════════════════════════════")
        logger.info("Initializing...")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
