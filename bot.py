import discord
from discord.ext import commands
import sqlite3
import datetime
import re
import os

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Database setup
def init_database():
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    
    # Ensure table exists
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS time_trials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            track_name TEXT NOT NULL,
            time_minutes INTEGER NOT NULL,
            time_seconds INTEGER NOT NULL,
            time_milliseconds INTEGER NOT NULL,
            game_mode TEXT NOT NULL DEFAULT '150cc',
            vehicle_setup TEXT,
            date_recorded TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        )
    ''')
    
    # --- Migration check for items_setting column ---
    cursor.execute("PRAGMA table_info(time_trials)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "items_setting" not in columns:
        print("⚠️ Adding missing column: items_setting")
        cursor.execute("ALTER TABLE time_trials ADD COLUMN items_setting TEXT NOT NULL DEFAULT 'shrooms'")
        conn.commit()

    # Create index for better query performance (now including items_setting)
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_user_track_mode_items
        ON time_trials (user_id, track_name, game_mode, items_setting)
    ''')
    
    conn.commit()
    conn.close()

# Helper functions
def parse_time(time_str):
    """Parse time in format MM:SS.mmm or M:SS.mmm"""
    pattern = r'^(\d{1,2}):(\d{2})\.(\d{3})$'
    match = re.match(pattern, time_str)
    if not match:
        return None
    
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    milliseconds = int(match.group(3))
    
    if seconds >= 60:
        return None
    
    return minutes, seconds, milliseconds

def format_time(minutes, seconds, milliseconds):
    """Format time as MM:SS.mmm"""
    return f"{minutes}:{seconds:02d}.{milliseconds:03d}"

def time_to_total_ms(minutes, seconds, milliseconds):
    """Convert time to total milliseconds for comparison"""
    return (minutes * 60 * 1000) + (seconds * 1000) + milliseconds

def truncate_text(text, max_length=100):
    """Truncate text to fit Discord field limits"""
    if not text:
        return text
    if len(text) <= max_length:
        return text
    return text[:max_length-3] + "..."

from tracks_config import MK8_TRACKS, GAME_MODES
from world_records_itemless import WORLD_RECORDS_ITEMLESS
from world_records_shrooms import WORLD_RECORDS_SHROOMS

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    init_database()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.tree.command(name="compare_wr_itemless", description="Compare your shroomless times to world records and group by proximity")
async def compare_wr_itemless(interaction: discord.Interaction):
    items = "no_items"  # Shroomless/Itemless only
    mode = "150cc"      # Default mode for WRs (adjust if needed)

    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()

    # Get all user's times for shroomless 150cc
    cursor.execute('''
        SELECT track_name, time_minutes, time_seconds, time_milliseconds
        FROM time_trials
        WHERE user_id = ? AND game_mode = ? AND items_setting = ?
    ''', (interaction.user.id, mode, items))
    user_times = cursor.fetchall()
    conn.close()

    # Prepare grouping buckets
    buckets = {
        "Within 1s": [],
        "Within 2s": [],
        "Within 3s": [],
        "Within 5s": [],
        "Within 7s": [],
        "7s+": []
    }

    for track_name, mins, secs, ms in user_times:
        wr_time_str = WORLD_RECORDS_ITEMLESS.get(track_name)
        if not wr_time_str:
            continue
        wr_parsed = parse_time(wr_time_str)
        if not wr_parsed:
            continue
        user_ms = time_to_total_ms(mins, secs, ms)
        wr_ms = time_to_total_ms(*wr_parsed)
        diff = user_ms - wr_ms
        diff_s = diff / 1000.0
        formatted_user = format_time(mins, secs, ms)
        formatted_wr = format_time(*wr_parsed)
        entry = f"{track_name}: {formatted_user} (WR: {formatted_wr}, +{diff_s:.3f}s)"
        if diff_s <= 1:
            buckets["Within 1s"].append(entry)
        elif diff_s <= 2:
            buckets["Within 2s"].append(entry)
        elif diff_s <= 3:
            buckets["Within 3s"].append(entry)
        elif diff_s <= 5:
            buckets["Within 5s"].append(entry)
        elif diff_s <= 7:
            buckets["Within 7s"].append(entry)
        else:
            buckets["7s+"].append(entry)

    embed = discord.Embed(title="⏱️ Your Shroomless Times vs World Records", color=0x1abc9c)
    for group, entries in buckets.items():
        if entries:
            embed.add_field(name=group, value="\n".join(entries), inline=False)
        else:
            embed.add_field(name=group, value="None", inline=False)

    embed.set_footer(text="World records: Shroomless/Itemless only. Times shown are your PBs for each track.")
    await interaction.response.send_message(embed=embed)
@bot.tree.command(name="add_time", description="Add a new time trial record")
async def add_time(
    interaction: discord.Interaction, 
    track: str, 
    time: str, 
    mode: str = "150cc", 
    items: str = "shrooms",   
    vehicle: str | None = None, 
    notes: str | None = None
):
    # Validate track
    if track not in MK8_TRACKS:
        await interaction.response.send_message(f"❌ Invalid track name. Use `/list_tracks` to see all available tracks.", ephemeral=True)
        return
    
    # Validate mode
    if mode not in GAME_MODES:
        await interaction.response.send_message(f"❌ Invalid game mode. Choose from: {', '.join(GAME_MODES)}", ephemeral=True)
        return
    
    # Validate items setting
    if items not in ["shrooms", "no_items"]:
        await interaction.response.send_message("❌ Invalid items setting. Choose `shrooms` or `no_items`.", ephemeral=True)
        return
    
    # Parse time
    parsed_time = parse_time(time)
    if not parsed_time:
        await interaction.response.send_message("❌ Invalid time format. Use MM:SS.mmm (e.g., 1:23.456)", ephemeral=True)
        return
    
    minutes, seconds, milliseconds = parsed_time
    
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    
    # Check personal best for this user/track/mode/items
    cursor.execute('''
        SELECT time_minutes, time_seconds, time_milliseconds 
        FROM time_trials 
        WHERE user_id = ? AND track_name = ? AND game_mode = ? AND items_setting = ?
        ORDER BY (time_minutes * 60000 + time_seconds * 1000 + time_milliseconds) ASC
        LIMIT 1
    ''', (interaction.user.id, track, mode, items))
    
    current_best = cursor.fetchone()
    
    # Insert new record
    cursor.execute('''
        INSERT INTO time_trials (user_id, track_name, time_minutes, time_seconds, time_milliseconds, game_mode, items_setting, vehicle_setup, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (interaction.user.id, track, minutes, seconds, milliseconds, mode, items, vehicle or "", notes or ""))
    
    conn.commit()
    conn.close()
    
    formatted_time = format_time(minutes, seconds, milliseconds)
    embed = discord.Embed(title="🏁 Time Trial Added!", color=0x00ff00)
    embed.add_field(name="Track", value=track, inline=False)
    embed.add_field(name="Time", value=formatted_time, inline=True)
    embed.add_field(name="Mode", value=mode, inline=True)
    embed.add_field(name="Items", value=items, inline=True)  # NEW FIELD
    
    if vehicle:
        embed.add_field(name="Vehicle Setup", value=truncate_text(vehicle, 1000), inline=True)
    if notes:
        embed.add_field(name="Notes", value=truncate_text(notes, 1000), inline=False)
    
    # Personal best check
    if current_best:
        current_total_ms = time_to_total_ms(current_best[0], current_best[1], current_best[2])
        new_total_ms = time_to_total_ms(minutes, seconds, milliseconds)
        
        if new_total_ms < current_total_ms:
            improvement_ms = current_total_ms - new_total_ms
            improvement_seconds = improvement_ms / 1000
            embed.add_field(name="🎉 New Personal Best!", value=f"Improved by {improvement_seconds:.3f} seconds!", inline=False)
            embed.color = 0xffd700
        else:
            difference_ms = new_total_ms - current_total_ms
            difference_seconds = difference_ms / 1000
            embed.add_field(name="Current PB", value=f"{format_time(current_best[0], current_best[1], current_best[2])} (+{difference_seconds:.3f}s)", inline=False)
    else:
        embed.add_field(name="🎉 First Time on This Track!", value=f"This is your first recorded time for this track/mode/items setting.", inline=False)
        embed.color = 0xffd700
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="view_times", description="View your times for a specific track and mode/items")
async def view_times(interaction: discord.Interaction, track: str, mode: str = "150cc", items: str = "shrooms"):
    # Validate track
    if track not in MK8_TRACKS:
        await interaction.response.send_message(f"❌ Invalid track name. Use `/list_tracks` to see all available tracks.", ephemeral=True)
        return
    
    # Validate mode
    if mode not in GAME_MODES:
        await interaction.response.send_message(f"❌ Invalid game mode. Choose from: {', '.join(GAME_MODES)}", ephemeral=True)
        return
    
    # Validate items
    if items not in ["shrooms", "no_items"]:
        await interaction.response.send_message("❌ Invalid items setting. Choose `shrooms` or `no_items`.", ephemeral=True)
        return
    
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    
    # Fetch all records for this user/track/mode/items
    cursor.execute('''
        SELECT time_minutes, time_seconds, time_milliseconds, vehicle_setup, date_recorded, notes
        FROM time_trials 
        WHERE user_id = ? AND track_name = ? AND game_mode = ? AND items_setting = ?
        ORDER BY (time_minutes * 60000 + time_seconds * 1000 + time_milliseconds) ASC
    ''', (interaction.user.id, track, mode, items))
    
    results = cursor.fetchall()
    conn.close()
    
    if not results:
        await interaction.response.send_message(f"❌ No times found for {track} in {mode} mode ({items}).", ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"📜 Times for {track} ({mode}, {items})",
        color=0x3498db
    )
    
    for idx, (mins, secs, ms, vehicle, date_recorded, notes) in enumerate(results, 1):
        formatted_time = format_time(mins, secs, ms)
        field_value = f"⏱ {formatted_time} | 🗓 {date_recorded.split()[0]}"
        if vehicle:
            field_value += f" | 🚗 {truncate_text(vehicle, 50)}"
        if notes:
            field_value += f" | 📝 {truncate_text(notes, 50)}"
        
        embed.add_field(name=f"{idx}.", value=field_value, inline=False)
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="personal_best", description="View your personal best for a specific track and mode/items")
async def personal_best(interaction: discord.Interaction, track: str, mode: str = "150cc", items: str = "shrooms"):
    # Validate track
    if track not in MK8_TRACKS:
        await interaction.response.send_message(f"❌ Invalid track name. Use `/list_tracks` to see all available tracks.", ephemeral=True)
        return
    
    # Validate mode
    if mode not in GAME_MODES:
        await interaction.response.send_message(f"❌ Invalid game mode. Choose from: {', '.join(GAME_MODES)}", ephemeral=True)
        return
    
    # Validate items
    if items not in ["shrooms", "no_items"]:
        await interaction.response.send_message("❌ Invalid items setting. Choose `shrooms` or `no_items`.", ephemeral=True)
        return
    
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT time_minutes, time_seconds, time_milliseconds, vehicle_setup, date_recorded, notes
        FROM time_trials 
        WHERE user_id = ? AND track_name = ? AND game_mode = ? AND items_setting = ?
        ORDER BY (time_minutes * 60000 + time_seconds * 1000 + time_milliseconds) ASC
        LIMIT 1
    ''', (interaction.user.id, track, mode, items))
    
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        await interaction.response.send_message(f"❌ No records found for {track} in {mode} mode ({items}).", ephemeral=True)
        return
    
    mins, secs, ms, vehicle, date_recorded, notes = result
    formatted_time = format_time(mins, secs, ms)
    
    embed = discord.Embed(title="🏆 Personal Best", color=0xffd700)
    embed.add_field(name="Track", value=track, inline=False)
    embed.add_field(name="Mode", value=mode, inline=True)
    embed.add_field(name="Items", value=items, inline=True)  # NEW FIELD
    embed.add_field(name="Time", value=formatted_time, inline=True)
    
    if vehicle:
        embed.add_field(name="Vehicle Setup", value=truncate_text(vehicle, 1000), inline=True)
    
    embed.add_field(name="Date Recorded", value=date_recorded.split()[0], inline=True)
    
    if notes:
        embed.add_field(name="Notes", value=truncate_text(notes, 1000), inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="delete_time", description="Delete one of your recorded times for a track/mode/items")
async def delete_time(
    interaction: discord.Interaction,
    track: str,
    mode: str = "150cc",
    items: str = "shrooms"
):
    # Validate track
    if track not in MK8_TRACKS:
        await interaction.response.send_message(
            f"❌ Invalid track name. Use `/list_tracks` to see all available tracks.",
            ephemeral=True
        )
        return
    
    # Validate mode
    if mode not in GAME_MODES:
        await interaction.response.send_message(
            f"❌ Invalid game mode. Choose from: {', '.join(GAME_MODES)}",
            ephemeral=True
        )
        return
    
    # Validate items
    if items not in ["shrooms", "no_items"]:
        await interaction.response.send_message(
            "❌ Invalid items setting. Choose `shrooms` or `no_items`.",
            ephemeral=True
        )
        return
    
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    
    # Find the most recent record for this user/track/mode/items
    cursor.execute('''
        SELECT id, time_minutes, time_seconds, time_milliseconds, date_recorded
        FROM time_trials
        WHERE user_id = ? AND track_name = ? AND game_mode = ? AND items_setting = ?
        ORDER BY date_recorded DESC
        LIMIT 1
    ''', (interaction.user.id, track, mode, items))
    
    result = cursor.fetchone()
    
    if not result:
        conn.close()
        await interaction.response.send_message(
            f"❌ No records found for {track} in {mode} mode ({items}).",
            ephemeral=True
        )
        return
    
    record_id, mins, secs, ms, date_recorded = result
    
    # Delete that record
    cursor.execute('DELETE FROM time_trials WHERE id = ?', (record_id,))
    conn.commit()
    conn.close()
    
    formatted_time = format_time(mins, secs, ms)
    
    embed = discord.Embed(title="🗑️ Time Deleted", color=0xe74c3c)
    embed.add_field(name="Track", value=track, inline=False)
    embed.add_field(name="Mode", value=mode, inline=True)
    embed.add_field(name="Items", value=items, inline=True)
    embed.add_field(name="Time", value=formatted_time, inline=True)
    embed.add_field(name="Date Recorded", value=date_recorded.split()[0], inline=True)
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="clear_track", description="Clear all your times for a specific track")
async def clear_track(interaction: discord.Interaction, track: str):
    if track not in MK8_TRACKS:
        await interaction.response.send_message(f"❌ Invalid track name. Use `/list_tracks` to see all available tracks.", ephemeral=True)
        return
    
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    
    # Count existing records
    cursor.execute('SELECT COUNT(*) FROM time_trials WHERE user_id = ? AND track_name = ?', (interaction.user.id, track))
    count = cursor.fetchone()[0]
    
    if count == 0:
        await interaction.response.send_message(f"❌ No records found for {track}.", ephemeral=True)
        conn.close()
        return
    
    # Delete all records for this track
    cursor.execute('DELETE FROM time_trials WHERE user_id = ? AND track_name = ?', (interaction.user.id, track))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="🗑️ Track Records Cleared", color=0xff0000)
    embed.add_field(name="Track", value=track, inline=False)
    embed.add_field(name="Records Deleted", value=str(count), inline=True)
    embed.add_field(name="⚠️ Warning", value="This action cannot be undone!", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="list_tracks", description="List all 96 Mario Kart 8 Deluxe tracks")
async def list_tracks(interaction: discord.Interaction):
    # Group tracks by category (all 96 tracks organized by cups)
    cups = [
        # Base Game Nitro Cups
        ("Mushroom Cup", MK8_TRACKS[0:4]),
        ("Flower Cup", MK8_TRACKS[4:8]),
        ("Star Cup", MK8_TRACKS[8:12]),
        ("Special Cup", MK8_TRACKS[12:16]),
        # Base Game Retro Cups
        ("Shell Cup", MK8_TRACKS[16:20]),
        ("Banana Cup", MK8_TRACKS[20:24]),
        ("Leaf Cup", MK8_TRACKS[24:28]),
        ("Lightning Cup", MK8_TRACKS[28:32]),
        ("Bell Cup", MK8_TRACKS[32:36]),
        ("Egg Cup", MK8_TRACKS[36:40]),
        ("Triforce Cup", MK8_TRACKS[40:44]),
        ("Crossing Cup", MK8_TRACKS[44:48]),
        # DLC Booster Course Pass
        ("Golden Dash Cup", MK8_TRACKS[48:52]),
        ("Lucky Cat Cup", MK8_TRACKS[52:56]),
        ("Turnip Cup", MK8_TRACKS[56:60]),
        ("Propeller Cup", MK8_TRACKS[60:64]),
        ("Rock Cup", MK8_TRACKS[64:68]),
        ("Moon Cup", MK8_TRACKS[68:72]),
        ("Fruit Cup", MK8_TRACKS[72:76]),
        ("Boomerang Cup", MK8_TRACKS[76:80]),
        ("Feather Cup", MK8_TRACKS[80:84]),
        ("Cherry Cup", MK8_TRACKS[84:88]),
        ("Acorn Cup", MK8_TRACKS[88:92]),
        ("Spiny Cup", MK8_TRACKS[92:96])
    ]
    
    # Create embed with all 24 cups (within Discord's 25 field limit)
    embed = discord.Embed(title="🏁 All 96 Mario Kart 8 Deluxe Tracks", color=0x0099ff)
    embed.description = "**Base Game (48) + Booster Course Pass DLC (48)**"
    
    for cup_name, tracks in cups:
        track_list = "\n".join([f"• {track}" for track in tracks])
        embed.add_field(name=cup_name, value=track_list, inline=True)
    
    embed.set_footer(text="Total: 96 tracks across 24 cups (12 Base Game + 12 DLC)")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="stats", description="View your overall time trial statistics")
async def stats(interaction: discord.Interaction, mode: str = "150cc", items: str = "shrooms"):
    # Validate mode
    if mode not in GAME_MODES:
        await interaction.response.send_message(f"❌ Invalid game mode. Choose from: {', '.join(GAME_MODES)}", ephemeral=True)
        return
    
    # Validate items
    if items not in ["shrooms", "no_items"]:
        await interaction.response.send_message("❌ Invalid items setting. Choose `shrooms` or `no_items`.", ephemeral=True)
        return
    
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    
    # Count distinct tracks with times for this user/mode/items
    cursor.execute('''
        SELECT COUNT(DISTINCT track_name) 
        FROM time_trials 
        WHERE user_id = ? AND game_mode = ? AND items_setting = ?
    ''', (interaction.user.id, mode, items))
    tracks_recorded = cursor.fetchone()[0]
    
    # Total time trials for this user/mode/items
    cursor.execute('''
        SELECT COUNT(*)
        FROM time_trials
        WHERE user_id = ? AND game_mode = ? AND items_setting = ?
    ''', (interaction.user.id, mode, items))
    total_records = cursor.fetchone()[0]
    
    # Fastest time across all tracks (this user/mode/items only)
    cursor.execute('''
        SELECT track_name, time_minutes, time_seconds, time_milliseconds
        FROM time_trials 
        WHERE user_id = ? AND game_mode = ? AND items_setting = ?
        ORDER BY (time_minutes * 60000 + time_seconds * 1000 + time_milliseconds) ASC
        LIMIT 1
    ''', (interaction.user.id, mode, items))
    best_time = cursor.fetchone()
    
    # Slowest time across all tracks (this user/mode/items only)
    cursor.execute('''
        SELECT track_name, time_minutes, time_seconds, time_milliseconds
        FROM time_trials 
        WHERE user_id = ? AND game_mode = ? AND items_setting = ?
        ORDER BY (time_minutes * 60000 + time_seconds * 1000 + time_milliseconds) DESC
        LIMIT 1
    ''', (interaction.user.id, mode, items))
    worst_time = cursor.fetchone()
    
    conn.close()
    
    # Build stats embed
    embed = discord.Embed(
        title=f"📊 Time Trial Stats ({mode}, {items})",
        color=0x9b59b6
    )
    
    embed.add_field(name="🏁 Tracks Recorded", value=str(tracks_recorded), inline=True)
    embed.add_field(name="📂 Total Records", value=str(total_records), inline=True)
    
    if best_time:
        embed.add_field(
            name="⚡ Fastest Time",
            value=f"{format_time(best_time[1], best_time[2], best_time[3])} ({best_time[0]})",
            inline=False
        )
    if worst_time:
        embed.add_field(
            name="🐢 Slowest Time",
            value=f"{format_time(worst_time[1], worst_time[2], worst_time[3])} ({worst_time[0]})",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="compare_wr_shrooms", description="Compare your shrooms times to world records and group by proximity (150cc/200cc)")
async def compare_wr_shrooms(interaction: discord.Interaction, cc: str = "150cc"):
    if cc not in WORLD_RECORDS_SHROOMS:
        await interaction.response.send_message(f"❌ Invalid CC. Choose '150cc' or '200cc'", ephemeral=True)
        return

    items = "shrooms"
    mode = cc

    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()

    # Get all user's times for shrooms and selected cc
    cursor.execute('''
        SELECT track_name, time_minutes, time_seconds, time_milliseconds
        FROM time_trials
        WHERE user_id = ? AND game_mode = ? AND items_setting = ?
    ''', (interaction.user.id, mode, items))
    user_times = cursor.fetchall()
    conn.close()

    buckets = {
        "Within 1s": [],
        "Within 2s": [],
        "Within 3s": [],
        "Within 5s": [],
        "Within 7s": [],
        "7s+": []
    }

    wr_dict = WORLD_RECORDS_SHROOMS[cc]
    for track_name, mins, secs, ms in user_times:
        wr_time_str = wr_dict.get(track_name)
        if not wr_time_str:
            continue
        wr_parsed = parse_time(wr_time_str)
        if not wr_parsed:
            continue
        user_ms = time_to_total_ms(mins, secs, ms)
        wr_ms = time_to_total_ms(*wr_parsed)
        diff = user_ms - wr_ms
        diff_s = diff / 1000.0
        formatted_user = format_time(mins, secs, ms)
        formatted_wr = format_time(*wr_parsed)
        entry = f"{track_name}: {formatted_user} (WR: {formatted_wr}, +{diff_s:.3f}s)"
        if diff_s <= 1:
            buckets["Within 1s"].append(entry)
        elif diff_s <= 2:
            buckets["Within 2s"].append(entry)
        elif diff_s <= 3:
            buckets["Within 3s"].append(entry)
        elif diff_s <= 5:
            buckets["Within 5s"].append(entry)
        elif diff_s <= 7:
            buckets["Within 7s"].append(entry)
        else:
            buckets["7s+"].append(entry)

    embed = discord.Embed(title=f"⏱️ Your Shrooms Times vs World Records ({cc})", color=0x3498db)
    for group, entries in buckets.items():
        if entries:
            embed.add_field(name=group, value="\n".join(entries), inline=False)
        else:
            embed.add_field(name=group, value="None", inline=False)

    embed.set_footer(text="World records: Shrooms only. Times shown are your PBs for each track.")
    await interaction.response.send_message(embed=embed)

# Run the bot
if __name__ == "__main__":
    token = os.getenv('FROOG')
    if not token:
        print("❌ DISCORD_BOT_TOKEN environment variable not found!")
        print("Please set your Discord bot token as an environment variable.")
        exit(1)
    
    print("Starting bot...")
    print("Note: Discord bot tokens should start with a bot ID followed by a dot and then the actual token")
    print("If you're getting login errors, please verify your token is correct")
    
    try:
        bot.run(token)
    except discord.LoginFailure:
        print("❌ Login failed! Please check your Discord bot token.")
        print("Make sure you copied the token correctly from the Discord Developer Portal.")
        print("The token should be a long string with letters, numbers, and special characters.")
        exit(1)
    except Exception as e:
        print(f"❌ An error occurred: {e}")
        exit(1)