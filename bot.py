import discord
import sqlite3
import os
import random
import datetime
import asyncio
from dotenv import load_dotenv
load_dotenv()
from tracks_config import MK8_TRACKS, GAME_MODES
from karts_config import MK8_VEHICLES
from world_records_itemless import WORLD_RECORDS_ITEMLESS
from world_records_shrooms import WORLD_RECORDS_SHROOMS
from discord.ext import commands, tasks

# Helper functions
def get_tour_tracks():
    """Get list of all Tour tracks from MK8_TRACKS"""
    return [track for track in MK8_TRACKS if track.startswith("Tour ")]

def get_non_tour_tracks():
    """Get list of all non-Tour tracks from MK8_TRACKS"""
    return [track for track in MK8_TRACKS if not track.startswith("Tour ")]

def select_weekly_tracks(week_number):
    """Select 3 tracks for the week. Every other week includes 1 tour track."""
    # Use week number as seed to ensure consistent selection across all servers
    random.seed(week_number)
    
    tour_tracks = get_tour_tracks()
    non_tour_tracks = get_non_tour_tracks()
    
    # Every other week (odd week numbers) should include a tour track
    if week_number % 2 == 1:
        # Include 1 tour track and 2 regular tracks
        selected_tracks = []
        selected_tracks.append(random.choice(tour_tracks))
        selected_tracks.extend(random.sample(non_tour_tracks, 2))
    else:
        # All regular tracks
        selected_tracks = random.sample(non_tour_tracks, 3)
    
    # Reset random seed to avoid affecting other random operations
    random.seed()
    
    return selected_tracks

def get_current_week():
    """Get current week number since October 14, 2025"""
    today = datetime.date.today()
    # Get Monday of current week
    monday = today - datetime.timedelta(days=today.weekday())
    # Calculate week number since October 14, 2025
    start_date = datetime.date(2025, 10, 14)  # Start counting from today
    # Find the Monday of the week containing October 14, 2025
    start_monday = start_date - datetime.timedelta(days=start_date.weekday())
    
    week_number = (monday - start_monday).days // 7 + 1
    return max(1, week_number)  # Ensure we never return 0 or negative

async def track_autocomplete(interaction, current: str):
    return [discord.app_commands.Choice(name=track, value=track) for track in MK8_TRACKS if current.lower() in track.lower()][:25]

async def mode_autocomplete(interaction, current: str):
    return [discord.app_commands.Choice(name=mode, value=mode) for mode in GAME_MODES if current.lower() in mode.lower()][:25]

async def items_autocomplete(interaction, current: str):
    return [discord.app_commands.Choice(name=item, value=item) for item in ["shrooms", "no_items"] if current.lower() in item.lower()][:25]

async def test_autocomplete(interaction, current: str):
    return [discord.app_commands.Choice(name=vehicle, value=vehicle) for vehicle in MK8_VEHICLES if current.lower() in vehicle.lower()][:25]

async def cc_autocomplete(interaction, current: str):
    return [discord.app_commands.Choice(name=cc, value=cc) for cc in ["150cc", "200cc"] if current.lower() in cc.lower()][:25]

def truncate_text(text, max_length):
    if not text:
        return ""
    return text if len(text) <= max_length else text[:max_length-3] + "..."

def parse_time(time_str):
    # Accepts MM:SS.mmm or M:SS.mmm
    try:
        mins_secs, ms = time_str.split('.')
        mins, secs = mins_secs.split(':')
        return int(mins), int(secs), int(ms)
    except Exception:
        return None

def format_time(mins, secs, ms):
    return f"{mins}:{secs:02d}.{ms:03d}"

def time_to_total_ms(mins, secs, ms):
    return mins * 60000 + secs * 1000 + ms

def init_database():
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS time_trials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            track_name TEXT,
            time_minutes INTEGER,
            time_seconds INTEGER,
            time_milliseconds INTEGER,
            game_mode TEXT,
            items_setting TEXT,
            vehicle_setup TEXT,
            notes TEXT,
            date_recorded TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Weekly trials table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS weekly_trials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_number INTEGER UNIQUE,
            track1 TEXT,
            track2 TEXT,
            track3 TEXT,
            start_date TEXT,
            end_date TEXT,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    
    # Weekly submissions table (separate from regular time_trials)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS weekly_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_number INTEGER,
            user_id INTEGER,
            track_name TEXT,
            time_minutes INTEGER,
            time_seconds INTEGER,
            time_milliseconds INTEGER,
            game_mode TEXT,
            items_setting TEXT,
            vehicle_setup TEXT,
            notes TEXT,
            date_recorded TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (week_number) REFERENCES weekly_trials(week_number)
        )
    ''')
    
    conn.commit()
    conn.close()

async def generate_weekly_leaderboard(week_number, tracks):
    """Generate leaderboard embed for weekly trials"""
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    
    embed = discord.Embed(
        title=f"🏆 Weekly Trials Results - Week {week_number}",
        description="Final leaderboard for this week's trials",
        color=0xffd700
    )
    
    for i, track in enumerate(tracks, 1):
        # Get top 5 times for this track
        cursor.execute('''
            SELECT user_id, time_minutes, time_seconds, time_milliseconds, vehicle_setup
            FROM weekly_submissions
            WHERE week_number = ? AND track_name = ?
            ORDER BY (time_minutes * 60000 + time_seconds * 1000 + time_milliseconds) ASC
            LIMIT 5
        ''', (week_number, track))
        
        results = cursor.fetchall()
        
        if results:
            leaderboard_text = ""
            for j, (user_id, mins, secs, ms, vehicle) in enumerate(results, 1):
                formatted_time = format_time(mins, secs, ms)
                try:
                    user = await bot.fetch_user(user_id)
                    username = truncate_text(user.display_name, 20)  # Limit username length
                except:
                    username = f"User {user_id}"
                
                medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][j-1]
                vehicle_str = f" ({truncate_text(vehicle, 15)})" if vehicle else ""  # Limit vehicle length
                line = f"{medal} {username}: {formatted_time}{vehicle_str}\n"
                
                # Check if adding this line would exceed limit
                if len(leaderboard_text + line) > 900:  # Leave buffer
                    leaderboard_text += "... (truncated)"
                    break
                
                leaderboard_text += line
            
            embed.add_field(
                name=f"{i}. {truncate_text(track, 30)}",  # Limit track name in title
                value=leaderboard_text.rstrip("\n") or "No submissions",
                inline=False
            )
        else:
            embed.add_field(
                name=f"{i}. {truncate_text(track, 30)}",
                value="No submissions",
                inline=False
            )
    
    conn.close()
    return embed

# Bot setup
intents = discord.Intents.default()
# Remove privileged intents that require approval
# intents.members = True  # Commented out - requires privileged intent
# intents.guilds = True   # This is included in default intents
bot = commands.Bot(command_prefix="!", intents=intents)

@tasks.loop(time=datetime.time(hour=12, minute=0))  # Sunday 12:00 PM
async def start_weekly_trials():
    """Start new weekly trials every Sunday at 12:00 PM"""
    today = datetime.date.today()
    if today.weekday() == 6:  # Sunday = 6
        await setup_new_weekly_trials()

@tasks.loop(time=datetime.time(hour=12, minute=0))  # Saturday 12:00 PM  
async def end_weekly_trials():
    """End weekly trials every Saturday at 12:00 PM and show results"""
    today = datetime.date.today()
    if today.weekday() == 5:  # Saturday = 5
        await finish_weekly_trials()

async def setup_new_weekly_trials(target_guild=None):
    """Set up new weekly trials"""
    week_number = get_current_week()
    tracks = select_weekly_tracks(week_number)
    
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    
    # Deactivate previous trials
    cursor.execute('UPDATE weekly_trials SET is_active = 0 WHERE is_active = 1')
    
    # Insert new weekly trials
    start_date = datetime.date.today().isoformat()
    end_date = (datetime.date.today() + datetime.timedelta(days=6)).isoformat()
    
    cursor.execute('''
        INSERT OR REPLACE INTO weekly_trials 
        (week_number, track1, track2, track3, start_date, end_date, is_active)
        VALUES (?, ?, ?, ?, ?, ?, 1)
    ''', (week_number, tracks[0], tracks[1], tracks[2], start_date, end_date))
    
    conn.commit()
    conn.close()
    
    # Announce new trials - if target_guild specified, only post there
    guilds_to_announce = [target_guild] if target_guild else bot.guilds
    
    for guild in guilds_to_announce:
        if guild is None:
            continue
        
        # Find the specific time-trials-of-the-week channel (exact name match)
        target_channels = [ch for ch in guild.text_channels if ch.name.lower().strip() == 'time-trials-of-the-week']
        
        if target_channels:
            channel = target_channels[0]  # Use the first (should be only) match
            embed = discord.Embed(
                title="🏁 New Weekly Time Trials!",
                description=f"Week {week_number} trials are now active!",
                color=0x00ff00
            )
            embed.add_field(name="Featured Tracks", value=f"1. {tracks[0]}\n2. {tracks[1]}\n3. {tracks[2]}", inline=False)
            embed.add_field(name="Duration", value=f"{start_date} to {end_date}", inline=False)
            embed.add_field(name="How to Participate", value="Use `/add_time` with 150cc and shrooms for these tracks!", inline=False)
            
            try:
                await channel.send(embed=embed)
                print(f"✅ Posted new weekly trials to {guild.name}#{channel.name}")
            except discord.Forbidden:
                print(f"❌ Missing permissions to send messages in {guild.name}#{channel.name}")
            except discord.HTTPException as e:
                print(f"❌ Failed to send message in {guild.name}#{channel.name}: {e}")
            except Exception as e:
                print(f"❌ Unexpected error sending message in {guild.name}#{channel.name}: {e}")
        else:
            print(f"ℹ️ No 'time-trials-of-the-week' channel found in {guild.name}")

async def finish_weekly_trials(target_guild=None):
    """Finish current weekly trials and show leaderboard"""
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    
    # Get current active trials
    cursor.execute('SELECT * FROM weekly_trials WHERE is_active = 1')
    current_trial = cursor.fetchone()
    
    if current_trial:
        week_number, track1, track2, track3 = current_trial[1], current_trial[2], current_trial[3], current_trial[4]
        
        # Generate leaderboard - if target_guild specified, only post there
        guilds_to_announce = [target_guild] if target_guild else bot.guilds
        
        for guild in guilds_to_announce:
            if guild is None:
                continue
            
            # Find the specific time-trials-of-the-week channel (exact name match)
            target_channels = [ch for ch in guild.text_channels if ch.name.lower().strip() == 'time-trials-of-the-week']
            
            if target_channels:
                channel = target_channels[0]  # Use the first (should be only) match
                embed = await generate_weekly_leaderboard(week_number, [track1, track2, track3])
                
                try:
                    await channel.send(embed=embed)
                    print(f"✅ Posted weekly leaderboard to {guild.name}#{channel.name}")
                except discord.Forbidden:
                    print(f"❌ Missing permissions to send messages in {guild.name}#{channel.name}")
                except discord.HTTPException as e:
                    print(f"❌ Failed to send leaderboard in {guild.name}#{channel.name}: {e}")
                except Exception as e:
                    print(f"❌ Unexpected error sending leaderboard in {guild.name}#{channel.name}: {e}")
            else:
                print(f"ℹ️ No 'time-trials-of-the-week' channel found in {guild.name}")
    
    conn.close()

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    init_database()
    
    # Start scheduled tasks only if they're not already running
    if not start_weekly_trials.is_running():
        start_weekly_trials.start()
        print("✅ Started weekly trials task")
    else:
        print("ℹ️ Weekly trials task already running")
        
    if not end_weekly_trials.is_running():
        end_weekly_trials.start()
        print("✅ Started end weekly trials task")
    else:
        print("ℹ️ End weekly trials task already running")
    
    # Check if we need to setup trials for current week (in case bot was offline)
    await check_and_setup_current_week()
    
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

async def check_and_setup_current_week():
    """Check if current week has active trials, if not, set them up"""
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    
    current_week = get_current_week()
    cursor.execute('SELECT * FROM weekly_trials WHERE week_number = ? AND is_active = 1', (current_week,))
    
    if not cursor.fetchone():
        # No active trials for current week, set them up
        await setup_new_weekly_trials()
    
    conn.close()

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
            # Limit entries and ensure field value doesn't exceed 1024 characters
            field_value = ""
            shown_entries = 0
            max_entries_per_field = 15  # Reasonable limit to prevent overflow
            
            for entry in entries[:max_entries_per_field]:
                new_line = entry + "\n"
                if len(field_value + new_line) > 1000:  # Leave buffer for potential "..." 
                    break
                field_value += new_line
                shown_entries += 1
            
            # Remove trailing newline
            field_value = field_value.rstrip("\n")
            
            # Add truncation indicator if needed
            if shown_entries < len(entries):
                remaining = len(entries) - shown_entries
                field_value += f"\n... and {remaining} more"
            
            embed.add_field(name=f"{group} ({len(entries)})", value=field_value or "None", inline=False)
        else:
            embed.add_field(name=f"{group} (0)", value="None", inline=False)

    embed.set_footer(text="World records: Shroomless/Itemless only. Times shown are your PBs for each track.")
    await interaction.response.send_message(embed=embed)
@bot.tree.command(name="add_time", description="Add a new time trial record")
@discord.app_commands.autocomplete(
    track=track_autocomplete,
    mode=mode_autocomplete,
    items=items_autocomplete,
    vehicle=test_autocomplete
)
async def add_time(
    interaction: discord.Interaction,
    track: str,
    time: str,
    mode: str,
    items: str,
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
    
    # Check if this qualifies for weekly trials (150cc and shrooms only)
    weekly_submission_made = False
    weekly_best_info = None
    if mode == "150cc" and items == "shrooms":
        # Check if there are active weekly trials and if this track is part of them
        cursor.execute('SELECT * FROM weekly_trials WHERE is_active = 1')
        current_trial = cursor.fetchone()
        
        if current_trial:
            week_number = current_trial[1]
            active_tracks = [current_trial[2], current_trial[3], current_trial[4]]
            
            if track in active_tracks:
                # Check current weekly best for this user/track
                cursor.execute('''
                    SELECT time_minutes, time_seconds, time_milliseconds 
                    FROM weekly_submissions 
                    WHERE week_number = ? AND user_id = ? AND track_name = ? AND game_mode = ? AND items_setting = ?
                    ORDER BY (time_minutes * 60000 + time_seconds * 1000 + time_milliseconds) ASC
                    LIMIT 1
                ''', (week_number, interaction.user.id, track, mode, items))
                
                current_weekly_best = cursor.fetchone()
                
                # Insert into weekly submissions
                cursor.execute('''
                    INSERT INTO weekly_submissions 
                    (week_number, user_id, track_name, time_minutes, time_seconds, time_milliseconds, game_mode, items_setting, vehicle_setup, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (week_number, interaction.user.id, track, minutes, seconds, milliseconds, mode, items, vehicle or "", notes or ""))
                conn.commit()
                
                weekly_submission_made = True
                
                # Check if this is a weekly personal best
                if current_weekly_best:
                    current_weekly_ms = time_to_total_ms(current_weekly_best[0], current_weekly_best[1], current_weekly_best[2])
                    new_total_ms = time_to_total_ms(minutes, seconds, milliseconds)
                    
                    if new_total_ms < current_weekly_ms:
                        improvement_ms = current_weekly_ms - new_total_ms
                        improvement_seconds = improvement_ms / 1000
                        weekly_best_info = f"🎉 New Weekly Best! Improved by {improvement_seconds:.3f}s"
                    else:
                        difference_ms = new_total_ms - current_weekly_ms
                        difference_seconds = difference_ms / 1000
                        weekly_best_info = f"Weekly Best: {format_time(current_weekly_best[0], current_weekly_best[1], current_weekly_best[2])} (+{difference_seconds:.3f}s)"
                else:
                    weekly_best_info = "🎉 First Weekly Submission for this track!"
    
    conn.close()
    
    formatted_time = format_time(minutes, seconds, milliseconds)
    embed = discord.Embed(title="🏁 Time Trial Added!", color=0x00ff00)
    embed.add_field(name="Track", value=track, inline=False)
    embed.add_field(name="Time", value=formatted_time, inline=True)
    embed.add_field(name="Mode", value=mode, inline=True)
    embed.add_field(name="Items", value=items, inline=True)
    if vehicle:
        embed.add_field(name="Vehicle Setup", value=truncate_text(vehicle, 1000), inline=True)
    if notes:
        embed.add_field(name="Notes", value=truncate_text(notes, 1000), inline=False)
    
    # Check for ping AFTER inserting the new record
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    
    # Check if this new time is now the top time
    cursor.execute('''
        SELECT user_id, time_minutes, time_seconds, time_milliseconds 
        FROM time_trials 
        WHERE track_name = ? AND game_mode = ? AND items_setting = ?
        ORDER BY (time_minutes * 60000 + time_seconds * 1000 + time_milliseconds) ASC
        LIMIT 1
    ''', (track, mode, items))
    top_time = cursor.fetchone()
    
    ping_message = None
    ping_debug = None
    
    # If current user is now the top time holder, find who they beat
    if top_time and top_time[0] == interaction.user.id:
        # Get the second-best time (which would be the previous record holder)
        cursor.execute('''
            SELECT user_id, time_minutes, time_seconds, time_milliseconds 
            FROM time_trials 
            WHERE track_name = ? AND game_mode = ? AND items_setting = ?
            AND user_id != ?
            ORDER BY (time_minutes * 60000 + time_seconds * 1000 + time_milliseconds) ASC
            LIMIT 1
        ''', (track, mode, items, interaction.user.id))
        previous_holder = cursor.fetchone()
        
        if previous_holder:
            prev_user_id = previous_holder[0]
            ping_message = f"🏁 <@{prev_user_id}> Your top time for {track} ({mode}, {items}) was just beaten!"
    
    conn.close()
    
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
    
    # Add weekly trials information if applicable
    if weekly_submission_made:
        embed.add_field(name="📅 Weekly Trials", value=weekly_best_info, inline=False)
        if weekly_best_info.startswith("🎉 New Weekly Best!"):
            embed.color = 0xffd700
    
    if ping_message:
        try:
            await interaction.channel.send(ping_message)
        except Exception:
            await interaction.followup.send(ping_message, ephemeral=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="view_times", description="View your times for a specific track and mode/items")
@discord.app_commands.autocomplete(
    track=track_autocomplete,
    mode=mode_autocomplete,
    items=items_autocomplete
)
async def view_times(interaction: discord.Interaction, track: str, mode: str = None, items: str = None):
    # Validate track
    if track not in MK8_TRACKS:
        await interaction.response.send_message(f"❌ Invalid track name. Use `/list_tracks` to see all available tracks.")
        return
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    # Build query
    query = '''SELECT time_minutes, time_seconds, time_milliseconds, vehicle_setup, date_recorded, notes, game_mode, items_setting FROM time_trials WHERE user_id = ? AND track_name = ?'''
    params = [interaction.user.id, track]
    if mode:
        query += ' AND game_mode = ?'
        params.append(mode)
    if items:
        query += ' AND items_setting = ?'
        params.append(items)
    query += ' ORDER BY (time_minutes * 60000 + time_seconds * 1000 + time_milliseconds) ASC'
    cursor.execute(query, tuple(params))
    results = cursor.fetchall()
    conn.close()
    if not results:
        await interaction.response.send_message(f"❌ No times found for {track}" + (f" in {mode} mode ({items})" if mode and items else "."), ephemeral=True)
        return
    embed = discord.Embed(
        title=f"📜 Times for {track}" + (f" ({mode}, {items})" if mode and items else " (All Categories)"),
        color=0x3498db
    )
    
    # Limit the number of entries to prevent overflow
    max_entries = 20  # Reasonable limit
    total_results = len(results)
    
    for idx, (mins, secs, ms, vehicle, date_recorded, notes, rec_mode, rec_items) in enumerate(results[:max_entries], 1):
        formatted_time = format_time(mins, secs, ms)
        field_value = f"⏱ {formatted_time} | 🗓 {date_recorded.split()[0]} | 🏷 {rec_mode}, {rec_items}"
        if vehicle:
            field_value += f" | 🚗 {truncate_text(vehicle, 50)}"
        if notes:
            field_value += f" | 📝 {truncate_text(notes, 50)}"
        embed.add_field(name=f"{idx}.", value=field_value, inline=False)
    
    # Add footer if results were truncated
    if total_results > max_entries:
        embed.set_footer(text=f"Showing {max_entries} of {total_results} times. Use filters to see specific times.")
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="personal_best", description="View your personal best for a specific track and mode/items")
@discord.app_commands.autocomplete(
    track=track_autocomplete,
    mode=mode_autocomplete,
    items=items_autocomplete
)
async def personal_best(interaction: discord.Interaction, track: str, mode: str = "150cc", items: str = "shrooms"):
    # Validate track
    if track not in MK8_TRACKS:
        await interaction.response.send_message(f"❌ Invalid track name. Use `/list_tracks` to see all available tracks.")
        return
    
    # Validate mode
    if mode not in GAME_MODES:
        await interaction.response.send_message(f"❌ Invalid game mode. Choose from: {', '.join(GAME_MODES)}")
        return
    
    # Validate items
    if items not in ["shrooms", "no_items"]:
        await interaction.response.send_message("❌ Invalid items setting. Choose `shrooms` or `no_items`.")
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
@discord.app_commands.autocomplete(
    track=track_autocomplete,
    mode=mode_autocomplete,
    items=items_autocomplete
)
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
@discord.app_commands.autocomplete(track=track_autocomplete)
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
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="stats", description="View your time trial stats and comparisons.")
@discord.app_commands.autocomplete(
    mode=mode_autocomplete,
    items=items_autocomplete
)
async def stats(
    interaction: discord.Interaction,
    mode: str = "150cc",
    items: str = "shrooms",
    compare_user: str = None
):
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
    # Total run submissions
    cursor.execute('''
        SELECT COUNT(*) FROM time_trials WHERE user_id = ? AND game_mode = ? AND items_setting = ?
    ''', (interaction.user.id, mode, items))
    total_submissions = cursor.fetchone()[0]
    # Most played track
    cursor.execute('''
        SELECT track_name, COUNT(*) as cnt FROM time_trials
        WHERE user_id = ? AND game_mode = ? AND items_setting = ?
        GROUP BY track_name ORDER BY cnt DESC LIMIT 1
    ''', (interaction.user.id, mode, items))
    most_played = cursor.fetchone()
    # Recent activity (last 5 runs)
    cursor.execute('''
        SELECT track_name, time_minutes, time_seconds, time_milliseconds, date_recorded FROM time_trials
        WHERE user_id = ? AND game_mode = ? AND items_setting = ?
        ORDER BY date_recorded DESC LIMIT 5
    ''', (interaction.user.id, mode, items))
    recent_runs = cursor.fetchall()
    # Track completion rate
    cursor.execute('''
        SELECT COUNT(DISTINCT track_name) FROM time_trials WHERE user_id = ? AND game_mode = ? AND items_setting = ?
    ''', (interaction.user.id, mode, items))
    tracks_recorded = cursor.fetchone()[0]
    completion_rate = f"{tracks_recorded}/{len(MK8_TRACKS)} ({(tracks_recorded/len(MK8_TRACKS))*100:.1f}%)"
    # Average rank per map & WR gap
    ranks = []
    wr_gaps = []
    for track in MK8_TRACKS:
        cursor.execute('''
            SELECT user_id, time_minutes, time_seconds, time_milliseconds FROM time_trials
            WHERE track_name = ? AND game_mode = ? AND items_setting = ?
            ORDER BY (time_minutes * 60000 + time_seconds * 1000 + time_milliseconds) ASC
        ''', (track, mode, items))
        all_times = cursor.fetchall()
        user_best = None
        for idx, (uid, mins, secs, ms) in enumerate(all_times, 1):
            if uid == interaction.user.id:
                user_best = (mins, secs, ms, idx)
                break
        if user_best:
            ranks.append(user_best[3])
            if items == "shrooms":
                wr_dict = WORLD_RECORDS_SHROOMS.get(mode, {})
                wr_time_str = wr_dict.get(track) if wr_dict else None
            else:
                wr_time_str = WORLD_RECORDS_ITEMLESS.get(track)
            wr_parsed = parse_time(wr_time_str) if wr_time_str else None
            if wr_parsed:
                user_ms = time_to_total_ms(user_best[0], user_best[1], user_best[2])
                wr_ms = time_to_total_ms(*wr_parsed)
                wr_gaps.append(user_ms - wr_ms)
    avg_rank = sum(ranks) / len(ranks) if ranks else None
    avg_gap = (sum(wr_gaps) / len(wr_gaps)) / 1000 if wr_gaps else None
    # Head-to-head comparison
    head_to_head = None
    if compare_user:
        try:
            compare_id = int(compare_user)
            wins = 0
            losses = 0
            for track in MK8_TRACKS:
                cursor.execute('''
                    SELECT user_id, time_minutes, time_seconds, time_milliseconds FROM time_trials
                    WHERE track_name = ? AND game_mode = ? AND items_setting = ?
                    ORDER BY (time_minutes * 60000 + time_seconds * 1000 + time_milliseconds) ASC
                    LIMIT 2
                ''', (track, mode, items))
                top_two = cursor.fetchall()
                if len(top_two) == 2:
                    if top_two[0][0] == interaction.user.id and top_two[1][0] == compare_id:
                        wins += 1
                    elif top_two[0][0] == compare_id and top_two[1][0] == interaction.user.id:
                        losses += 1
            head_to_head = f"Wins: {wins}, Losses: {losses}"
        except Exception:
            head_to_head = "Invalid user ID for comparison."
    conn.close()
    embed = discord.Embed(title=f"📊 Time Trial Stats ({mode}, {items})", color=0x9b59b6)
    embed.add_field(name="Total Run Submissions", value=str(total_submissions), inline=True)
    if avg_rank:
        embed.add_field(name="Average Rank (per map)", value=f"{avg_rank:.2f}", inline=True)
    else:
        embed.add_field(name="Average Rank (per map)", value="No times recorded", inline=True)
    if avg_gap is not None:
        embed.add_field(name="Average Distance from WR", value=f"{avg_gap:.3f} seconds", inline=True)
    else:
        embed.add_field(name="Average Distance from WR", value="N/A", inline=True)
    embed.add_field(name="Track Completion Rate", value=completion_rate, inline=True)
    if most_played:
        embed.add_field(name="Most Played Track", value=f"{most_played[0]} ({most_played[1]} runs)", inline=True)
    else:
        embed.add_field(name="Most Played Track", value="N/A", inline=True)
    if recent_runs:
        # Ensure recent runs field doesn't exceed limit
        recent_lines = []
        for r in recent_runs:
            line = f"{r[0]}: {format_time(r[1], r[2], r[3])} ({r[4].split()[0]})"
            # Truncate track name if too long
            if len(line) > 100:
                track_name = truncate_text(r[0], 30)
                line = f"{track_name}: {format_time(r[1], r[2], r[3])} ({r[4].split()[0]})"
            recent_lines.append(line)
        
        recent_str = "\n".join(recent_lines)
        # Ensure total field value is under 1024 characters
        if len(recent_str) > 1000:
            recent_str = recent_str[:997] + "..."
        
        embed.add_field(name="Recent Runs", value=recent_str, inline=False)
    else:
        embed.add_field(name="Recent Runs", value="N/A", inline=False)
    if head_to_head:
        embed.add_field(name="Head-to-Head vs User", value=head_to_head, inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="compare_wr_shrooms", description="Compare your shrooms times to world records and group by proximity (150cc/200cc)")
@discord.app_commands.autocomplete(cc=cc_autocomplete)
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
            # Limit entries and ensure field value doesn't exceed 1024 characters
            field_value = ""
            shown_entries = 0
            max_entries_per_field = 15  # Reasonable limit to prevent overflow
            
            for entry in entries[:max_entries_per_field]:
                new_line = entry + "\n"
                if len(field_value + new_line) > 1000:  # Leave buffer for potential "..." 
                    break
                field_value += new_line
                shown_entries += 1
            
            # Remove trailing newline
            field_value = field_value.rstrip("\n")
            
            # Add truncation indicator if needed
            if shown_entries < len(entries):
                remaining = len(entries) - shown_entries
                field_value += f"\n... and {remaining} more"
            
            embed.add_field(name=f"{group} ({len(entries)})", value=field_value or "None", inline=False)
        else:
            embed.add_field(name=f"{group} (0)", value="None", inline=False)

    embed.set_footer(text="World records: Shrooms only. Times shown are your PBs for each track.")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Show the top time for every track, mode, and items setting.")
@discord.app_commands.autocomplete(
    mode=mode_autocomplete,
    items=items_autocomplete
)
async def leaderboard(interaction: discord.Interaction, mode: str, items: str):
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    embed = discord.Embed(title=f"🏆 Leaderboard ({mode}, {items})", color=0x00bfff)
    # Define cups and their track indices (same as list_tracks)
    cups = [
        ("Mushroom Cup", MK8_TRACKS[0:4]),
        ("Flower Cup", MK8_TRACKS[4:8]),
        ("Star Cup", MK8_TRACKS[8:12]),
        ("Special Cup", MK8_TRACKS[12:16]),
        ("Shell Cup", MK8_TRACKS[16:20]),
        ("Banana Cup", MK8_TRACKS[20:24]),
        ("Leaf Cup", MK8_TRACKS[24:28]),
        ("Lightning Cup", MK8_TRACKS[28:32]),
        ("Bell Cup", MK8_TRACKS[32:36]),
        ("Egg Cup", MK8_TRACKS[36:40]),
        ("Triforce Cup", MK8_TRACKS[40:44]),
        ("Crossing Cup", MK8_TRACKS[44:48]),
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
    for cup_name, tracks in cups:
        field_lines = []
        for track in tracks:
            cursor.execute('''
                SELECT user_id, time_minutes, time_seconds, time_milliseconds, vehicle_setup
                FROM time_trials
                WHERE track_name = ? AND game_mode = ? AND items_setting = ?
                ORDER BY (time_minutes * 60000 + time_seconds * 1000 + time_milliseconds) ASC
                LIMIT 1
            ''', (track, mode, items))
            result = cursor.fetchone()
            if result:
                user_id, mins, secs, ms, vehicle = result
                try:
                    user = await bot.fetch_user(user_id)
                    user_name = truncate_text(user.display_name, 20)  # Limit username length
                except Exception:
                    user_name = f"User {user_id}"
                formatted_time = format_time(mins, secs, ms)
                vehicle_str = f" ({truncate_text(vehicle, 15)})" if vehicle else ""  # Limit vehicle length
                
                # Truncate track name if needed
                track_display = truncate_text(track, 25)
                line = f"{track_display}: {user_name} {formatted_time}{vehicle_str}"
                
                # Ensure individual line isn't too long
                if len(line) > 80:
                    line = f"{truncate_text(track, 20)}: {truncate_text(user_name, 15)} {formatted_time}"
                
                field_lines.append(line)
            else:
                field_lines.append(f"{truncate_text(track, 25)}: No record")
        
        # Ensure field value doesn't exceed 1024 characters
        field_value = "\n".join(field_lines)
        if len(field_value) > 1000:
            # If still too long, truncate the field
            field_value = field_value[:997] + "..."
        
        embed.add_field(name=cup_name, value=field_value, inline=False)
    conn.close()
    embed.set_footer(text="Each field is a cup. Only 25 cups/fields allowed per embed.")
    await interaction.response.send_message(embed=embed)



@bot.tree.command(name="current_trials", description="View current weekly time trials")
async def current_trials(interaction: discord.Interaction):
    # Check if command is used in the correct channel
    if interaction.channel.name != 'time-trials-of-the-week':
        await interaction.response.send_message(
            "❌ This command can only be used in the #time-trials-of-the-week channel.",
            ephemeral=True
        )
        return
    
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM weekly_trials WHERE is_active = 1')
    current_trial = cursor.fetchone()
    
    if not current_trial:
        await interaction.response.send_message("❌ No active weekly trials at the moment.", ephemeral=True)
        conn.close()
        return
    
    week_number, track1, track2, track3, start_date, end_date = current_trial[1], current_trial[2], current_trial[3], current_trial[4], current_trial[5], current_trial[6]
    
    embed = discord.Embed(
        title=f"🏁 Weekly Time Trials - Week {week_number}",
        description="Current active weekly trials",
        color=0x3498db
    )
    
    embed.add_field(name="Featured Tracks", value=f"1. {track1}\n2. {track2}\n3. {track3}", inline=False)
    embed.add_field(name="Duration", value=f"{start_date} to {end_date}", inline=False)
    embed.add_field(name="How to Participate", value="Use `/add_time` with 150cc and shrooms for these tracks!", inline=False)
    
    # Show current participant count
    cursor.execute('SELECT COUNT(DISTINCT user_id) FROM weekly_submissions WHERE week_number = ?', (week_number,))
    participant_count = cursor.fetchone()[0]
    embed.add_field(name="Participants", value=str(participant_count), inline=True)
    
    conn.close()
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="weekly_leaderboard", description="View current weekly trials leaderboard")
async def weekly_leaderboard(interaction: discord.Interaction):
    # Check if command is used in the correct channel
    if interaction.channel.name != 'time-trials-of-the-week':
        await interaction.response.send_message(
            "❌ This command can only be used in the #time-trials-of-the-week channel.",
            ephemeral=True
        )
        return
    
    conn = sqlite3.connect('mario_kart_times.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM weekly_trials WHERE is_active = 1')
    current_trial = cursor.fetchone()
    
    if not current_trial:
        await interaction.response.send_message("❌ No active weekly trials at the moment.", ephemeral=True)
        conn.close()
        return
    
    week_number = current_trial[1]
    tracks = [current_trial[2], current_trial[3], current_trial[4]]
    
    embed = await generate_weekly_leaderboard(week_number, tracks)
    embed.title = f"🏆 Weekly Trials Leaderboard - Week {week_number}"
    embed.description = "Current standings (live leaderboard)"
    embed.color = 0x3498db
    
    conn.close()
    await interaction.response.send_message(embed=embed)

async def admin_action_autocomplete(interaction, current: str):
    actions = ["start_now", "end_now", "schedule"]
    return [discord.app_commands.Choice(name=action, value=action) for action in actions if current.lower() in action.lower()][:25]

@bot.tree.command(name="weekly_admin", description="Admin commands for weekly trials")
@discord.app_commands.autocomplete(action=admin_action_autocomplete)
@discord.app_commands.describe(
    action="Action to perform",
    time_hour="Hour for scheduling (0-23)",
    time_minute="Minute for scheduling (0-59)"
)
async def weekly_admin(
    interaction: discord.Interaction, 
    action: str,
    time_hour: int = 12,
    time_minute: int = 0
):
    # Check if user has captain or coach role (alternative method without Members Intent)
    try:
        # Try to get the member object from the guild
        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            # Fallback: fetch member if not in cache
            member = await interaction.guild.fetch_member(interaction.user.id)
        
        user_roles = [role.name.lower() for role in member.roles]
        print(f"🔍 DEBUG: User {interaction.user} in {interaction.guild.name} has roles: {user_roles}")
        print(f"🔍 DEBUG: User ID: {interaction.user.id}, Guild ID: {interaction.guild.id}")
        
        if not any(role in user_roles for role in ['captain', 'coach']):
            await interaction.response.send_message(f"❌ You need either the 'captain' or 'coach' role to use this command.\n**Your current roles:** {', '.join([role.name for role in member.roles if role.name != '@everyone'])}", ephemeral=True)
            return
    except discord.Forbidden:
        # Bot doesn't have permission to fetch member info
        await interaction.response.send_message("❌ Bot doesn't have permission to check your roles. Please contact an administrator.", ephemeral=True)
        return
    except Exception as e:
        print(f"❌ Error checking roles: {e}")
        await interaction.response.send_message("❌ Unable to verify your roles. Please try again or contact an administrator.", ephemeral=True)
        return
    
    # Defer the response to avoid timeout
    await interaction.response.defer(ephemeral=True)
    
    if action.lower() == "start_now":
        try:
            await setup_new_weekly_trials(target_guild=interaction.guild)
            await interaction.followup.send(
                "✅ Started new weekly trials immediately.\n"
                "ℹ️ Check the console for details about posting to channels."
            )
        except Exception as e:
            print(f"❌ Error in setup_new_weekly_trials: {e}")
            await interaction.followup.send(
                f"⚠️ Trials were set up, but there may have been issues posting announcements.\n"
                f"**Error:** {str(e)[:100]}...\n"
                f"Please check bot permissions in the 'time-trials-of-the-week' channel."
            )
    
    elif action.lower() == "end_now":
        try:
            await finish_weekly_trials(target_guild=interaction.guild)
            await interaction.followup.send(
                "✅ Ended current weekly trials and showed results.\n"
                "ℹ️ Check the console for details about posting leaderboards."
            )
        except Exception as e:
            print(f"❌ Error in finish_weekly_trials: {e}")
            await interaction.followup.send(
                f"⚠️ Trials were ended, but there may have been issues posting results.\n"
                f"**Error:** {str(e)[:100]}...\n"
                f"Please check bot permissions in the 'time-trials-of-the-week' channel."
            )
    
    elif action.lower() == "schedule":
        # Update task timing (would require restart to take effect)
        await interaction.followup.send(
            f"⚠️ Schedule change requested to {time_hour:02d}:{time_minute:02d}. "
            "Bot restart required for time changes to take effect."
        )
    
    else:
        await interaction.followup.send(
            "❌ Invalid action. Available actions: `start_now`, `end_now`, `schedule`"
        )

@bot.tree.command(name="check_permissions", description="Check bot permissions for weekly trials")
async def check_permissions(interaction: discord.Interaction):
    # Check if user has captain or coach role first
    try:
        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            member = await interaction.guild.fetch_member(interaction.user.id)
        
        user_roles = [role.name.lower() for role in member.roles]
        
        if not any(role in user_roles for role in ['captain', 'coach']):
            await interaction.response.send_message(
                "❌ You need either the 'captain' or 'coach' role to use this command.", 
                ephemeral=True
            )
            return
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Error checking your roles: {e}", 
            ephemeral=True
        )
        return

    # Check for time-trials-of-the-week channel
    target_channels = [ch for ch in interaction.guild.text_channels if ch.name.lower().strip() == 'time-trials-of-the-week']
    
    if not target_channels:
        await interaction.response.send_message(
            "❌ No 'time-trials-of-the-week' channel found in this server.\n"
            "Please create this channel for weekly trials to work.",
            ephemeral=True
        )
        return
    
    channel = target_channels[0]
    
    # Check bot permissions in that channel
    bot_member = interaction.guild.get_member(bot.user.id)
    permissions = channel.permissions_for(bot_member)
    
    embed = discord.Embed(
        title="🔍 Bot Permissions Check",
        description=f"Checking permissions for #{channel.name}",
        color=0x3498db
    )
    
    # Check required permissions
    required_perms = {
        "Send Messages": permissions.send_messages,
        "Embed Links": permissions.embed_links,
        "Read Messages": permissions.read_messages,
        "Use Slash Commands": permissions.use_slash_commands,
        "Read Message History": permissions.read_message_history
    }
    
    all_good = True
    perm_status = []
    
    for perm_name, has_perm in required_perms.items():
        if has_perm:
            perm_status.append(f"✅ {perm_name}")
        else:
            perm_status.append(f"❌ {perm_name}")
            all_good = False
    
    embed.add_field(
        name="Required Permissions",
        value="\n".join(perm_status),
        inline=False
    )
    
    if all_good:
        embed.add_field(
            name="✅ Status",
            value="All permissions are correct! Weekly trials should work.",
            inline=False
        )
        embed.color = 0x00ff00
    else:
        embed.add_field(
            name="❌ Action Required",
            value=f"Please give the bot the missing permissions in #{channel.name}\n"
                  "Go to Server Settings → Roles → Froog → Enable missing permissions",
            inline=False
        )
        embed.color = 0xff0000
    
    # Test sending a message
    try:
        test_embed = discord.Embed(
            title="🧪 Permission Test",
            description="If you can see this, the bot can send messages!",
            color=0x00ff00
        )
        await channel.send(embed=test_embed)
        embed.add_field(
            name="🧪 Send Test",
            value="✅ Successfully sent test message",
            inline=False
        )
    except discord.Forbidden:
        embed.add_field(
            name="🧪 Send Test",
            value="❌ Failed to send test message - permission denied",
            inline=False
        )
    except Exception as e:
        embed.add_field(
            name="🧪 Send Test",
            value=f"❌ Failed to send test message: {str(e)[:100]}",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Main block
if __name__ == "__main__":
    token = os.getenv('DISCORD_BOT_TOKEN')
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