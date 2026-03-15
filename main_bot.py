"""
Soviet Russia Life Simulator (SRLS) — Official Discord Bot
Built with discord.py v2.x + aiosqlite
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite
import asyncio
import time
import datetime
import re
import os

# ─── Constants ────────────────────────────────────────────────────────────────
DB_PATH = "srls.db"
DEFAULT_PREFIX = "!"
GREEN = 0x00C853
RED = 0xD32F2F
FOOTER = "Soviet Russia Life Simulator • Official Bot"

# ─── Database ─────────────────────────────────────────────────────────────────
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS prefixes (
                guild_id INTEGER PRIMARY KEY,
                prefix TEXT NOT NULL DEFAULT '!'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                user_id INTEGER,
                moderator_id INTEGER,
                reason TEXT,
                timestamp TEXT
            )
        """)
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
        await db.commit()

async def get_prefix(bot, message):
    if not message.guild:
        return DEFAULT_PREFIX
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT prefix FROM prefixes WHERE guild_id = ?", (message.guild.id,)) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else DEFAULT_PREFIX

async def log_action(guild_id, action, target_id, moderator_id, reason):
    ts = datetime.datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO modlogs (guild_id, action, target_id, moderator_id, reason, timestamp) VALUES (?,?,?,?,?,?)",
            (guild_id, action, target_id, moderator_id, reason, ts)
        )
        await db.commit()

# ─── Bot Setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.all()
bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)

# ─── Embed Helpers ────────────────────────────────────────────────────────────
def success_embed(title: str, description: str = "", ctx_or_inter=None) -> discord.Embed:
    e = discord.Embed(title=f"✅ {title}", description=description, color=GREEN)
    e.set_footer(text=FOOTER)
    if ctx_or_inter and bot.user:
        e.set_thumbnail(url=bot.user.display_avatar.url)
    return e

def error_embed(title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=f"❌ {title}", description=description, color=RED)
    e.set_footer(text=FOOTER)
    return e

def no_perm_embed() -> discord.Embed:
    return error_embed("Permission Denied", "You do not have permission to use this command.")

async def send_embed(ctx_or_inter, embed: discord.Embed, ephemeral=False):
    if isinstance(ctx_or_inter, commands.Context):
        await ctx_or_inter.send(embed=embed)
    else:
        await ctx_or_inter.response.send_message(embed=embed, ephemeral=ephemeral)

# ─── DM Helper ────────────────────────────────────────────────────────────────
async def dm_user(user: discord.Member, embed: discord.Embed):
    try:
        await user.send(embed=embed)
        return True
    except Exception:
        return False

# ─── Parse Duration ───────────────────────────────────────────────────────────
def parse_duration(duration_str: str) -> datetime.timedelta | None:
    match = re.fullmatch(r"(\d+)([smhd])", duration_str.lower())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    return {"s": datetime.timedelta(seconds=value), "m": datetime.timedelta(minutes=value),
            "h": datetime.timedelta(hours=value), "d": datetime.timedelta(days=value)}[unit]

# ─── Events ───────────────────────────────────────────────────────────────────
@tasks.loop(seconds=60)
async def status_loop():

    await bot.change_presence(
        status=discord.Status.dnd,
        activity=discord.Game(name="Soviet Russia Life Simulator")
    )
    await asyncio.sleep(30)

    await bot.change_presence(
        status=discord.Status.dnd,
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Join the Revolution"
        )
    )


@bot.event
async def on_ready():
    await init_db()
    
    await bot.tree.sync()
    status_loop.start()
    print(f"[SRLS Bot] Logged in as {bot.user} | Guilds: {len(bot.guilds)}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if bot.user in message.mentions:
        embed = discord.Embed(
            title="Soviet Russia Life Simulator",
            description="Soviet Russia Life Simulator is a role-play game where players live a virtual life in a large city inspired by Soviet-style Russia. In the game, a player starts as a normal citizen and slowly builds their life by getting jobs, earning money, buying vehicles, and interacting with other players. Some players join government organizations like police or military, while others may choose criminal paths such as gangs or illegal businesses. The world is open and active, so players can explore the city, complete missions, and create their own stories with other people. The goal of the game is to simulate a realistic life system where every player’s choices affect their progress and reputation in the city.",
            color=0xffcc00
        )
        await message.channel.send(embed=embed)

    await bot.process_commands(message)


# ════════════════════════════════════════════════════════════════════════════════
#  MODERATION COMMANDS
# ════════════════════════════════════════════════════════════════════════════════

# ─── MUTE ─────────────────────────────────────────────────────────────────────
@bot.command(name="mute")
@commands.has_permissions(moderate_members=True)
async def mute_prefix(ctx, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    await _mute(ctx, member, duration, reason)

@bot.tree.command(name="mute", description="🔨 Mute a member for a duration (e.g. 10m, 1h, 1d)")
@app_commands.describe(member="Member to mute", duration="Duration (e.g. 10m, 2h)", reason="Reason")
async def mute_slash(inter: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason provided"):
    if not inter.user.guild_permissions.moderate_members:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    await _mute(inter, member, duration, reason)

async def _mute(ctx_or_inter, member: discord.Member, duration: str, reason: str):
    td = parse_duration(duration)
    if not td:
        return await send_embed(ctx_or_inter, error_embed("Invalid Duration", "Use formats like `10s`, `5m`, `2h`, `1d`."))
    if td.total_seconds() > 2419200:
        return await send_embed(ctx_or_inter, error_embed("Duration Too Long", "Maximum timeout is 28 days."))

    until = discord.utils.utcnow() + td
    try:
        await member.timeout(until, reason=reason)
    except discord.Forbidden:
        return await send_embed(ctx_or_inter, error_embed("Action Failed", "I don't have permission to timeout this member."))

    guild = ctx_or_inter.guild if isinstance(ctx_or_inter, commands.Context) else ctx_or_inter.guild
    mod = ctx_or_inter.author if isinstance(ctx_or_inter, commands.Context) else ctx_or_inter.user
    await log_action(guild.id, "MUTE", member.id, mod.id, reason)

    dm_embed = discord.Embed(title="🔨 You Have Been Muted", color=RED,
                             description=f"**Server:** {guild.name}\n**Duration:** {duration}\n**Reason:** {reason}")
    dm_embed.set_footer(text=FOOTER)
    dm_ok = await dm_user(member, dm_embed)

    e = success_embed("Member Muted", f"**{member.mention}** has been muted for **{duration}**.\n**Reason:** {reason}", ctx_or_inter)
    e.add_field(name="DM Alert", value="✅ Sent alert in DM" if dm_ok else "❌ Could not send DM")
    await send_embed(ctx_or_inter, e)

@mute_prefix.error
async def mute_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send(embed=error_embed("Member Not Found", "Please mention a valid server member."))
    else:
        await ctx.send(embed=error_embed("Error", str(error)))

# ─── UNMUTE ───────────────────────────────────────────────────────────────────
@bot.command(name="unmute")
@commands.has_permissions(moderate_members=True)
async def unmute_prefix(ctx, member: discord.Member):
    await _unmute(ctx, member)

@bot.tree.command(name="unmute", description="🔨 Remove timeout from a member")
@app_commands.describe(member="Member to unmute")
async def unmute_slash(inter: discord.Interaction, member: discord.Member):
    if not inter.user.guild_permissions.moderate_members:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    await _unmute(inter, member)

async def _unmute(ctx_or_inter, member: discord.Member):
    try:
        await member.timeout(None)
    except discord.Forbidden:
        return await send_embed(ctx_or_inter, error_embed("Action Failed", "I cannot unmute this member."))
    guild = ctx_or_inter.guild if isinstance(ctx_or_inter, commands.Context) else ctx_or_inter.guild
    mod = ctx_or_inter.author if isinstance(ctx_or_inter, commands.Context) else ctx_or_inter.user
    await log_action(guild.id, "UNMUTE", member.id, mod.id, "N/A")
    await send_embed(ctx_or_inter, success_embed("Member Unmuted", f"{member.mention} has been unmuted.", ctx_or_inter))

@unmute_prefix.error
async def unmute_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())

# ─── WARN ─────────────────────────────────────────────────────────────────────
@bot.command(name="warn")
@commands.has_permissions(kick_members=True)
async def warn_prefix(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await _warn(ctx, member, reason)

@bot.tree.command(name="warn", description="🚨 Warn a member")
@app_commands.describe(member="Member to warn", reason="Reason for the warning")
async def warn_slash(inter: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not inter.user.guild_permissions.kick_members:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    await _warn(inter, member, reason)

async def _warn(ctx_or_inter, member: discord.Member, reason: str):
    guild = ctx_or_inter.guild
    mod = ctx_or_inter.author if isinstance(ctx_or_inter, commands.Context) else ctx_or_inter.user
    ts = datetime.datetime.utcnow().isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, timestamp) VALUES (?,?,?,?,?)",
            (guild.id, member.id, mod.id, reason, ts)
        )
        await db.commit()
        async with db.execute("SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?", (guild.id, member.id)) as c:
            count = (await c.fetchone())[0]

    await log_action(guild.id, "WARN", member.id, mod.id, reason)

    # === AUTO-KICK ON 3 WARNINGS ===
    if count >= 3:
        dm_embed = discord.Embed(
            title="🚨 You Have Been Warned & Kicked",
            color=RED,
            description=(
                f"**Server:** {guild.name}\n"
                f"**Moderator:** {mod.mention}\n"
                f"**Reason:** {reason}\n"
                f"**Total Warnings:** {count}\n\n"
                f"⚠️ You have reached **3 warnings** and have been automatically kicked."
            )
        ).set_footer(text=FOOTER)
        dm_ok = await dm_user(member, dm_embed)

        try:
            await member.kick(reason=f"Auto-kick: reached {count} warnings.")
            await log_action(guild.id, "AUTO-KICK (3 WARNS)", member.id, mod.id, f"Reached {count} warnings")
        except discord.Forbidden:
            pass

        e = success_embed(
            "Warning Issued + Auto-Kicked",
            f"{member.mention} received their **{count}rd warning** and has been **automatically kicked**.\n"
            f"**Reason:** {reason}\n"
            f"**Moderator:** {mod.mention}",
            ctx_or_inter
        )
        e.add_field(name="⚠️ Auto-Kick", value="Triggered at 3 warnings", inline=False)
        e.add_field(name="DM Alert", value="✅ Sent alert in DM" if dm_ok else "❌ Could not send DM")
        await send_embed(ctx_or_inter, e)
        return

    # === NORMAL WARNING (LESS THAN 3) ===
    dm_embed = discord.Embed(
        title="🚨 You Have Been Warned",
        color=RED,
        description=(
            f"**Server:** {guild.name}\n"
            f"**Moderator:** {mod.mention}\n"
            f"**Reason:** {reason}\n"
            f"**Total Warnings:** {count}/3\n\n"
            f"{'⚠️ One more warning and you will be kicked!' if count == 2 else ''}"
        )
    ).set_footer(text=FOOTER)
    dm_ok = await dm_user(member, dm_embed)

    e = success_embed(
        "Warning Issued",
        f"{member.mention} has been warned.\n"
        f"**Reason:** {reason}\n"
        f"**Moderator:** {mod.mention}\n"
        f"**Total Warnings:** {count}/3"
        + ("\n\n⚠️ *One more warning will result in an auto-kick.*" if count == 2 else ""),
        ctx_or_inter
    )
    e.add_field(name="DM Alert", value="✅ Sent alert in DM" if dm_ok else "❌ Could not send DM")
    await send_embed(ctx_or_inter, e)

@warn_prefix.error
async def warn_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())

# ─── UNWARN ───────────────────────────────────────────────────────────────────
@bot.command(name="unwarn")
@commands.has_permissions(kick_members=True)
async def unwarn_prefix(ctx, member: discord.Member):
    await _unwarn(ctx, member)

@bot.tree.command(name="unwarn", description="🚨 Remove the latest warning from a member")
@app_commands.describe(member="Member to remove a warning from")
async def unwarn_slash(inter: discord.Interaction, member: discord.Member):
    if not inter.user.guild_permissions.kick_members:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    await _unwarn(inter, member)

async def _unwarn(ctx_or_inter, member: discord.Member):
    guild = ctx_or_inter.guild
    mod = ctx_or_inter.author if isinstance(ctx_or_inter, commands.Context) else ctx_or_inter.user

    async with aiosqlite.connect(DB_PATH) as db:
        # Get the latest warning
        async with db.execute(
            "SELECT id FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
            (guild.id, member.id)
        ) as c:
            row = await c.fetchone()

        # If no warnings exist
        if not row:
            return await send_embed(
                ctx_or_inter,
                error_embed("No Warnings", f"{member.mention} has no warnings on record.")
            )

        # Delete the latest warning
        await db.execute("DELETE FROM warnings WHERE id=?", (row[0],))
        await db.commit()

        # Count remaining warnings
        async with db.execute(
            "SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?",
            (guild.id, member.id)
        ) as c:
            remaining = (await c.fetchone())[0]

    # Log moderation action
    await log_action(guild.id, "UNWARN", member.id, mod.id, f"Warning removed (Remaining: {remaining})")

    # Create feedback embed
    if remaining == 0:
        desc = (
            f"All warnings cleared for {member.mention}.\n"
            f"**Moderator:** {mod.mention}\n"
            f"**Status:** 🟢 Clean Record"
        )
    else:
        desc = (
            f"Latest warning removed from {member.mention}.\n"
            f"**Moderator:** {mod.mention}\n"
            f"**Remaining Warnings:** {remaining}/3"
        )

    e = success_embed("Warning Removed", desc, ctx_or_inter)
    await send_embed(ctx_or_inter, e)

@unwarn_prefix.error
async def unwarn_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())

# ─── BAN ──────────────────────────────────────────────────────────────────────
@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
async def ban_prefix(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await _ban(ctx, member, reason)

@bot.tree.command(name="ban", description="🪖 Ban a member from the server")
@app_commands.describe(member="Member to ban", reason="Reason for the ban")
async def ban_slash(inter: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not inter.user.guild_permissions.ban_members:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    await _ban(inter, member, reason)

async def _ban(ctx_or_inter, member: discord.Member, reason: str):
    guild = ctx_or_inter.guild if isinstance(ctx_or_inter, commands.Context) else ctx_or_inter.guild
    mod = ctx_or_inter.author if isinstance(ctx_or_inter, commands.Context) else ctx_or_inter.user

    dm_embed = discord.Embed(title="🪖 You Have Been Banned", color=RED,
                             description=f"**Server:** {guild.name}\n**Reason:** {reason}")
    dm_embed.set_footer(text=FOOTER)
    dm_ok = await dm_user(member, dm_embed)

    try:
        await member.ban(reason=reason)
    except discord.Forbidden:
        return await send_embed(ctx_or_inter, error_embed("Action Failed", "I cannot ban this member."))

    await log_action(guild.id, "BAN", member.id, mod.id, reason)
    e = success_embed("Member Banned", f"**{member}** has been banned.\n**Reason:** {reason}", ctx_or_inter)
    e.add_field(name="DM Alert", value="✅ Sent alert in DM" if dm_ok else "❌ Could not send DM")
    await send_embed(ctx_or_inter, e)

@ban_prefix.error
async def ban_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())

# ─── UNBAN ────────────────────────────────────────────────────────────────────
@bot.command(name="unban")
@commands.has_permissions(ban_members=True)
async def unban_prefix(ctx, user_id: int):
    await _unban(ctx, user_id)

@bot.tree.command(name="unban", description="🪖 Unban a user by their ID")
@app_commands.describe(user_id="The user's Discord ID")
async def unban_slash(inter: discord.Interaction, user_id: str):
    if not inter.user.guild_permissions.ban_members:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    try:
        uid = int(user_id)
    except ValueError:
        return await inter.response.send_message(embed=error_embed("Invalid ID", "Please provide a valid numeric user ID."), ephemeral=True)
    await _unban(inter, uid)

async def _unban(ctx_or_inter, user_id: int):
    guild = ctx_or_inter.guild if isinstance(ctx_or_inter, commands.Context) else ctx_or_inter.guild
    mod = ctx_or_inter.author if isinstance(ctx_or_inter, commands.Context) else ctx_or_inter.user
    try:
        user = await bot.fetch_user(user_id)
        await guild.unban(user)
    except discord.NotFound:
        return await send_embed(ctx_or_inter, error_embed("Not Found", "This user is not banned or does not exist."))
    except discord.Forbidden:
        return await send_embed(ctx_or_inter, error_embed("Action Failed", "I cannot unban this user."))
    await log_action(guild.id, "UNBAN", user_id, mod.id, "N/A")
    await send_embed(ctx_or_inter, success_embed("Member Unbanned", f"**{user}** (`{user_id}`) has been unbanned.", ctx_or_inter))

@unban_prefix.error
async def unban_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())

# ─── KICK ─────────────────────────────────────────────────────────────────────
@bot.command(name="kick")
@commands.has_permissions(kick_members=True)
async def kick_prefix(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await _kick(ctx, member, reason)

@bot.tree.command(name="kick", description="🪖 Kick a member from the server")
@app_commands.describe(member="Member to kick", reason="Reason")
async def kick_slash(inter: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not inter.user.guild_permissions.kick_members:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    await _kick(inter, member, reason)

async def _kick(ctx_or_inter, member: discord.Member, reason: str):
    guild = ctx_or_inter.guild if isinstance(ctx_or_inter, commands.Context) else ctx_or_inter.guild
    mod = ctx_or_inter.author if isinstance(ctx_or_inter, commands.Context) else ctx_or_inter.user
    try:
        await member.kick(reason=reason)
    except discord.Forbidden:
        return await send_embed(ctx_or_inter, error_embed("Action Failed", "I cannot kick this member."))
    await log_action(guild.id, "KICK", member.id, mod.id, reason)
    await send_embed(ctx_or_inter, success_embed("Member Kicked", f"**{member}** has been kicked.\n**Reason:** {reason}", ctx_or_inter))

@kick_prefix.error
async def kick_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())

# ─── ADD ROLE ─────────────────────────────────────────────────────────────────
@bot.command(name="addrole")
@commands.has_permissions(manage_roles=True)
async def addrole_prefix(ctx, member: discord.Member, *, role_name: str):
    await _addrole(ctx, member, role_name)


@bot.tree.command(name="addrole", description="⚙️ Add a role to a member")
@app_commands.describe(member="Target member", role="Select a role to add")
async def addrole_slash(inter: discord.Interaction, member: discord.Member, role: discord.Role):
    if not inter.user.guild_permissions.manage_roles:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    await _addrole(inter, member, role)


async def _addrole(ctx_or_inter, member: discord.Member, role_input):
    is_prefix = isinstance(ctx_or_inter, commands.Context)
    guild = ctx_or_inter.guild
    moderator = ctx_or_inter.author if is_prefix else ctx_or_inter.user

    # Determine role object
    role_obj = None
    if isinstance(role_input, discord.Role):
        role_obj = role_input
    else:
        # Remove mention formatting if user tagged a role
        match = re.match(r"<@&(\d+)>", role_input)
        if match:
            role_id = int(match.group(1))
            role_obj = guild.get_role(role_id)
        elif role_input.isdigit():
            role_obj = guild.get_role(int(role_input))
        else:
            role_obj = discord.utils.find(lambda r: r.name.lower() == role_input.lower(), guild.roles)

    if not role_obj:
        return await send_embed(ctx_or_inter, error_embed("Role Not Found", f"No role matching **{role_input}** found."))

    # Try adding role
    try:
        await member.add_roles(role_obj, reason=f"Added by {moderator}")
    except discord.Forbidden:
        return await send_embed(ctx_or_inter, error_embed("Permission Error", "I don’t have permission to assign this role."))
    except Exception as e:
        return await send_embed(ctx_or_inter, error_embed("Error", f"Unexpected error: {e}"))

    embed = success_embed("Role Added", f"✅ Added **{role_obj.name}** to {member.mention}.")
    await send_embed(ctx_or_inter, embed)


@addrole_prefix.error
async def addrole_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())
    else:
        await ctx.send(embed=error_embed("Error", str(error)))
# ─── REMOVE ROLE ──────────────────────────────────────────────────────────────
@bot.command(name="removerole")
@commands.has_permissions(manage_roles=True)
async def removerole_prefix(ctx, member: discord.Member, *, role_input: str):
    await _removerole(ctx, member, role_input)


@bot.tree.command(name="removerole", description="⚙️ Remove a role from a member")
@app_commands.describe(member="Target member", role="Select a role to remove")
async def removerole_slash(inter: discord.Interaction, member: discord.Member, role: discord.Role):
    if not inter.user.guild_permissions.manage_roles:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    await _removerole(inter, member, role)


async def _removerole(ctx_or_inter, member: discord.Member, role_input):
    is_prefix = isinstance(ctx_or_inter, commands.Context)
    guild = ctx_or_inter.guild
    moderator = ctx_or_inter.author if is_prefix else ctx_or_inter.user

    # Determine the role object (works for mention, ID, or name)
    role_obj = None
    if isinstance(role_input, discord.Role):
        role_obj = role_input
    else:
        # Match role mention like <@&123456789012345678>
        match = re.match(r"<@&(\d+)>", role_input)
        if match:
            role_id = int(match.group(1))
            role_obj = guild.get_role(role_id)
        elif role_input.isdigit():
            role_obj = guild.get_role(int(role_input))
        else:
            role_obj = discord.utils.find(lambda r: r.name.lower() == role_input.lower(), guild.roles)

    if not role_obj:
        return await send_embed(ctx_or_inter, error_embed("Role Not Found", f"No role matching **{role_input}** found."))

    # Check if user even has that role
    if role_obj not in member.roles:
        return await send_embed(ctx_or_inter, error_embed("Not Assigned", f"{member.mention} doesn’t have the role **{role_obj.name}**."))

    # Try to remove it
    try:
        await member.remove_roles(role_obj, reason=f"Removed by {moderator}")
    except discord.Forbidden:
        return await send_embed(ctx_or_inter, error_embed("Permission Error", "I don’t have permission to remove that role."))
    except Exception as e:
        return await send_embed(ctx_or_inter, error_embed("Error", f"Unexpected error: {e}"))

    embed = success_embed("Role Removed", f"🗑️ Removed **{role_obj.name}** from {member.mention}.")
    await send_embed(ctx_or_inter, embed)


@removerole_prefix.error
async def removerole_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())
    else:
        await ctx.send(embed=error_embed("Error", str(error)))
# ─── CLEAR ────────────────────────────────────────────────────────────────────
@bot.command(name="clear")
@commands.has_permissions(manage_messages=True)
async def clear_prefix(ctx, amount: int):
    await _clear(ctx, amount)

@bot.tree.command(name="clear", description="🔨 Delete messages from the channel")
@app_commands.describe(amount="Number of messages to delete (1–100)")
async def clear_slash(inter: discord.Interaction, amount: int):
    if not inter.user.guild_permissions.manage_messages:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    await _clear(inter, amount)

async def _clear(ctx_or_inter, amount: int):
    if amount < 1 or amount > 100:
        return await send_embed(ctx_or_inter, error_embed("Invalid Amount", "Please specify between 1 and 100 messages."))
    channel = ctx_or_inter.channel
    if isinstance(ctx_or_inter, discord.Interaction):
        await ctx_or_inter.response.defer(ephemeral=True)
        deleted = await channel.purge(limit=amount)
        await ctx_or_inter.followup.send(embed=success_embed("Messages Cleared", f"Deleted **{len(deleted)}** messages.", ctx_or_inter), ephemeral=True)
    else:
        await ctx_or_inter.message.delete()
        deleted = await channel.purge(limit=amount)
        msg = await channel.send(embed=success_embed("Messages Cleared", f"Deleted **{len(deleted)}** messages.", ctx_or_inter))
        await asyncio.sleep(4)
        await msg.delete()

@clear_prefix.error
async def clear_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())

# ─── LOCK ─────────────────────────────────────────────────────────────────────
@bot.command(name="lock")
@commands.has_permissions(manage_channels=True)
async def lock_prefix(ctx, channel: discord.TextChannel = None):
    await _lock(ctx, channel or ctx.channel)

@bot.tree.command(name="lock", description="🔨 Lock a channel (disable sending messages)")
@app_commands.describe(channel="Channel to lock (defaults to current)")
async def lock_slash(inter: discord.Interaction, channel: discord.TextChannel = None):
    if not inter.user.guild_permissions.manage_channels:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    await _lock(inter, channel or inter.channel)

async def _lock(ctx_or_inter, channel: discord.TextChannel):
    overwrite = channel.overwrites_for(channel.guild.default_role)
    overwrite.send_messages = False
    await channel.set_permissions(channel.guild.default_role, overwrite=overwrite)
    await send_embed(ctx_or_inter, success_embed("Channel Locked", f"{channel.mention} has been locked. 🔒", ctx_or_inter))

@lock_prefix.error
async def lock_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())

# ─── UNLOCK ───────────────────────────────────────────────────────────────────
@bot.command(name="unlock")
@commands.has_permissions(manage_channels=True)
async def unlock_prefix(ctx, channel: discord.TextChannel = None):
    await _unlock(ctx, channel or ctx.channel)

@bot.tree.command(name="unlock", description="🔨 Unlock a channel")
@app_commands.describe(channel="Channel to unlock (defaults to current)")
async def unlock_slash(inter: discord.Interaction, channel: discord.TextChannel = None):
    if not inter.user.guild_permissions.manage_channels:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    await _unlock(inter, channel or inter.channel)

async def _unlock(ctx_or_inter, channel: discord.TextChannel):
    overwrite = channel.overwrites_for(channel.guild.default_role)
    overwrite.send_messages = None
    await channel.set_permissions(channel.guild.default_role, overwrite=overwrite)
    await send_embed(ctx_or_inter, success_embed("Channel Unlocked", f"{channel.mention} has been unlocked. 🔓", ctx_or_inter))

@unlock_prefix.error
async def unlock_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())

# ─── SLOWMODE ─────────────────────────────────────────────────────────────────
@bot.command(name="slowmode")
@commands.has_permissions(manage_channels=True)
async def slowmode_prefix(ctx, seconds: int):
    await _slowmode(ctx, seconds)

@bot.tree.command(name="slowmode", description="⚙️ Set slowmode for the current channel")
@app_commands.describe(seconds="Slowmode delay in seconds (0 to disable)")
async def slowmode_slash(inter: discord.Interaction, seconds: int):
    if not inter.user.guild_permissions.manage_channels:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    await _slowmode(inter, seconds)

async def _slowmode(ctx_or_inter, seconds: int):
    if seconds < 0 or seconds > 21600:
        return await send_embed(ctx_or_inter, error_embed("Invalid Value", "Slowmode must be between 0 and 21600 seconds."))
    channel = ctx_or_inter.channel
    await channel.edit(slowmode_delay=seconds)
    msg = f"Slowmode set to **{seconds}s** in {channel.mention}." if seconds > 0 else f"Slowmode disabled in {channel.mention}."
    await send_embed(ctx_or_inter, success_embed("Slowmode Updated", msg, ctx_or_inter))

@slowmode_prefix.error
async def slowmode_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())

# ─── NICK ─────────────────────────────────────────────────────────────────────
@bot.command(name="nick")
@commands.has_permissions(manage_nicknames=True)
async def nick_prefix(ctx, member: discord.Member, *, new_name: str):
    await _nick(ctx, member, new_name)

@bot.tree.command(name="nick", description="⚙️ Change a member's nickname")
@app_commands.describe(member="Target member", new_name="New nickname")
async def nick_slash(inter: discord.Interaction, member: discord.Member, new_name: str):
    if not inter.user.guild_permissions.manage_nicknames:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    await _nick(inter, member, new_name)

async def _nick(ctx_or_inter, member: discord.Member, new_name: str):
    try:
        await member.edit(nick=new_name)
    except discord.Forbidden:
        return await send_embed(ctx_or_inter, error_embed("Action Failed", "I cannot change this member's nickname."))
    await send_embed(ctx_or_inter, success_embed("Nickname Changed", f"{member.mention}'s nickname has been set to **{new_name}**.", ctx_or_inter))

@nick_prefix.error
async def nick_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())

# ════════════════════════════════════════════════════════════════════════════════
#  UTILITY COMMANDS
# ════════════════════════════════════════════════════════════════════════════════

# ─── ANNOUNCE ────────────────────────────────────────────────────────────────
@bot.command(name="announce")
@commands.has_permissions(manage_messages=True)
async def announce_prefix(ctx, channel: discord.TextChannel = None, *, message: str = None):
    await _announce(ctx, message, None, False, "embed", channel)

@bot.tree.command(name="announce", description="📢 Make an announcement (embed or normal)")
@app_commands.describe(
    message="The announcement message",
    role="Role to mention (optional)",
    everyone="Mention @everyone (True/False)",
    content_type="Send as embed or normal message (default: embed)",
    channel="Channel to send the announcement in"
)
@app_commands.choices(content_type=[
    app_commands.Choice(name="Embed", value="embed"),
    app_commands.Choice(name="Normal", value="normal")
])
async def announce_slash(
    inter: discord.Interaction,
    message: str,
    role: discord.Role = None,
    everyone: bool = False,
    content_type: app_commands.Choice[str] = None,
    channel: discord.TextChannel = None
):
    if not inter.user.guild_permissions.manage_messages:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)

    mode = content_type.value if content_type else "embed"
    await inter.response.defer(thinking=True)
    await _announce(inter, message, role, everyone, mode, channel)

# ─── Core Function ───────────────────────────────────────────────────────────
async def _announce(
    ctx_or_inter,
    message: str,
    role: discord.Role = None,
    everyone: bool = False,
    mode: str = "embed",
    channel: discord.TextChannel = None
):
    if not message:
        return await send_embed(ctx_or_inter, error_embed("Missing Message", "You must provide a message to announce."))

    # Detect target channel
    target_channel = (
        channel
        or (ctx_or_inter.channel if isinstance(ctx_or_inter, (commands.Context, discord.Interaction)) else None)
    )
    if not target_channel:
        return await send_embed(ctx_or_inter, error_embed("No Channel Found", "Could not find a valid channel."))

    mention_text = "@everyone" if everyone else (role.mention if role else "")
    MAX_LEN = 2000

    # Split message safely without cutting words
    def split_message(text, limit):
        parts = []
        while len(text) > limit:
            split_at = text.rfind(" ", 0, limit)
            if split_at == -1:
                split_at = limit
            parts.append(text[:split_at])
            text = text[split_at:].strip()
        if text:
            parts.append(text)
        return parts

    parts = split_message(message, MAX_LEN)

    # Send announcement in multiple parts if needed
    for i, part in enumerate(parts):
        if mode == "embed":
            embed = discord.Embed(
                title=f"📢 Announcement {'(Part '+str(i+1)+')' if len(parts) > 1 else ''}",
                description=part,
                color=GREEN
            )
            embed.set_footer(text=FOOTER)
            if bot.user:
                embed.set_thumbnail(url=bot.user.display_avatar.url)
            await target_channel.send(content=mention_text if i == 0 else None, embed=embed)

        else:  # normal message mode
            msg = f"{mention_text}\n\n{part}" if i == 0 and mention_text else part
            await target_channel.send(msg)

    # Confirmation
    confirm_embed = success_embed(
        "✅ Announcement Sent",
        f"Your message has been successfully sent to {target_channel.mention}.",
        ctx_or_inter
    )
    await send_embed(ctx_or_inter, confirm_embed, ephemeral=True)

@announce_prefix.error
async def announce_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())
    else:
        await ctx.send(embed=error_embed("Error", str(error)))
        
# ─── PING ─────────────────────────────────────────────────────────────────────
@bot.command(name="ping")
async def ping_prefix(ctx):
    await _ping(ctx)

@bot.tree.command(name="ping", description="⚙️ Check bot and database latency")
async def ping_slash(inter: discord.Interaction):
    await _ping(inter)

async def _ping(ctx_or_inter):
    start = time.perf_counter()

    # DB latency
    db_start = time.perf_counter()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("SELECT 1")
    db_latency = (time.perf_counter() - db_start) * 1000

    bot_latency = bot.latency * 1000
    response_latency = (time.perf_counter() - start) * 1000

    e = discord.Embed(title="⚙️ Latency Report", color=GREEN)
    e.add_field(name="🤖 Bot Latency", value=f"`{bot_latency:.2f}ms`", inline=True)
    e.add_field(name="🗄️ DB Latency", value=f"`{db_latency:.2f}ms`", inline=True)
    e.add_field(name="⚡ Response Ping", value=f"`{response_latency:.2f}ms`", inline=True)
    e.set_footer(text=FOOTER)
    if bot.user:
        e.set_thumbnail(url=bot.user.display_avatar.url)

    await send_embed(ctx_or_inter, e)

# ─── CHANGE PREFIX ────────────────────────────────────────────────────────────
@bot.command(name="changeprefix")
@commands.has_permissions(administrator=True)
async def changeprefix_prefix(ctx, new_prefix: str):
    await _changeprefix(ctx, new_prefix)

@bot.tree.command(name="changeprefix", description="⚙️ Change the bot's command prefix (Admin only)")
@app_commands.describe(new_prefix="New prefix to use")
async def changeprefix_slash(inter: discord.Interaction, new_prefix: str):
    if not inter.user.guild_permissions.administrator:
        return await inter.response.send_message(embed=no_perm_embed(), ephemeral=True)
    await _changeprefix(inter, new_prefix)

async def _changeprefix(ctx_or_inter, new_prefix: str):
    if len(new_prefix) > 5:
        return await send_embed(ctx_or_inter, error_embed("Invalid Prefix", "Prefix must be 5 characters or fewer."))
    guild = ctx_or_inter.guild if isinstance(ctx_or_inter, commands.Context) else ctx_or_inter.guild
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO prefixes (guild_id, prefix) VALUES (?, ?)", (guild.id, new_prefix))
        await db.commit()
    await send_embed(ctx_or_inter, success_embed("Prefix Updated", f"Command prefix has been changed to `{new_prefix}`.", ctx_or_inter))

@changeprefix_prefix.error
async def changeprefix_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())

# ─── INVITE ───────────────────────────────────────────────────────────────────
@bot.command(name="invite")
async def invite_prefix(ctx):
    await _invite(ctx)

@bot.tree.command(name="invite", description="⚙️ Get bot invite and support server links")
async def invite_slash(inter: discord.Interaction):
    await _invite(inter)

async def _invite(ctx_or_inter):
    e = discord.Embed(
        title="⚙️ Invite SRLS Bot",
        description=(
            "Use the buttons below to invite the bot or join our support server.\n\n"
            f"🔗 **[Invite Bot](https://discord.com/oauth2/authorize?client_id=1467519941661036678&permissions=8&integration_type=0&scope=bot+applications.commands)**\n"
            f"💬 **[Support Server](https://discord.gg/KdvTzFGyxv)**"
        ),
        color=GREEN
    )
    e.set_footer(text=FOOTER)
    if bot.user:
        e.set_thumbnail(url=bot.user.display_avatar.url)

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Invite Bot", url="https://discord.com/oauth2/authorize?client_id=1467519941661036678&permissions=8&integration_type=0&scope=bot+applications.commands" ,style=discord.ButtonStyle.link, emoji="🔗"))
    view.add_item(discord.ui.Button(label="Support Server", url="https://discord.gg/KdvTzFGyxv", style=discord.ButtonStyle.link, emoji="💬"))

    if isinstance(ctx_or_inter, commands.Context):
        await ctx_or_inter.send(embed=e, view=view)
    else:
        await ctx_or_inter.response.send_message(embed=e, view=view)

# ════════════════════════════════════════════════════════════════════════════════
#  HELP COMMAND — Soviet Command Center
# ════════════════════════════════════════════════════════════════════════════════
@bot.command(name="help")
async def help_prefix(ctx):
    prefix = await get_prefix(bot, ctx.message)
    await _help(ctx, prefix)

@bot.tree.command(name="help", description="🪖 Soviet Command Center — all commands listed")
async def help_slash(inter: discord.Interaction):
    await _help(inter, "/")

async def _help(ctx_or_inter, prefix: str):
    e = discord.Embed(
        title="🪖 Soviet Command Center",
        description=(
            f"Welcome, comrade! Here are all available commands.\n"
            f"**Current Prefix:** `{prefix}` — Slash commands also available via `/`\n"
            f"**Legend:** `<required>` `[optional]`"
        ),
        color=GREEN
    )

    e.add_field(
        name="🔨 Moderation",
        value=(
            f"`{prefix}mute @user <time> [reason]` — Timeout a member\n"
            f"`{prefix}unmute @user` — Remove timeout\n"
            f"`{prefix}warn @user [reason]` — Issue a warning\n"
            f"`{prefix}unwarn @user` — Remove last warning\n"
            f"`{prefix}ban @user [reason]` — Ban a member\n"
            f"`{prefix}unban <user_id>` — Unban by ID\n"
            f"`{prefix}kick @user [reason]` — Kick a member\n"
            f"`{prefix}addrole @user <role>` — Add a role\n"
            f"`{prefix}removerole @user <role>` — Remove a role\n"
            f"`{prefix}clear <amount>` — Delete messages (1–100)\n"
            f"`{prefix}lock [#channel]` — Lock channel\n"
            f"`{prefix}unlock [#channel]` — Unlock channel\n"
            f"`{prefix}slowmode <seconds>` — Set slowmode\n"
            f"`{prefix}nick @user <name>` — Change nickname\n"
            f"`{prefix}announce <msg>` – Send announcements"
        ),
        inline=False
    )

    e.add_field(
        name="⚙️ Utility",
        value=(
            f"`{prefix}ping` — Check bot & DB latency\n"
            f"`{prefix}changeprefix <prefix>` — Change prefix *(Admin)*\n"
            f"`{prefix}invite` — Invite & support links"
        ),
        inline=False
    )

    e.add_field(
        name="📋 Notes",
        value=(
            "• Mute duration format: `10s`, `5m`, `2h`, `1d`\n"
            "• Moderation actions are DM'd to the target when possible\n"
            "• All actions are logged internally"
        ),
        inline=False
    )

    e.set_footer(text=FOOTER)
    if bot.user:
        e.set_thumbnail(url=bot.user.display_avatar.url)

    await send_embed(ctx_or_inter, e)

# ─── Global Error Handler ──────────────────────────────────────────────────────
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=error_embed("Missing Argument", f"Missing required argument: `{error.param.name}`.\nUse `help` for correct usage."))
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send(embed=error_embed("Member Not Found", "Could not find that member. Please mention a valid server member."))
    elif isinstance(error, commands.BadArgument):
        await ctx.send(embed=error_embed("Invalid Argument", str(error)))
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=no_perm_embed())
    else:
        await ctx.send(embed=error_embed("Unexpected Error", str(error)))

# ─── Run ──────────────────────────────────────────────────────────────────────
bot.run(os.getenv("TOKEN"))
