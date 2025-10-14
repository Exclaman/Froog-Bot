# Discord Bot Troubleshooting Guide

## If commands aren't showing up:

### 1. Check Bot Console Output
When you start the bot, you should see:
```
Bot Name has connected to Discord!
Synced X command(s)
  - command1
  - command2
  - etc...
```

### 2. Force Command Refresh in Discord
- Type `/` in Discord and wait a few seconds
- If commands don't appear, restart Discord completely
- Or use Ctrl+R to reload Discord

### 3. Check Bot Permissions
Make sure your bot has:
- Send Messages permission
- Use Slash Commands permission  
- Read Message History permission

### 4. Manual Command Sync (if needed)
Add this command to your bot temporarily for testing:

```python
@bot.command()
async def sync(ctx):
    if ctx.author.id == YOUR_USER_ID:  # Replace with your Discord user ID
        await bot.tree.sync()
        await ctx.send("Commands synced!")
```

### 5. Check Guild vs Global Commands
Slash commands can take up to 1 hour to appear globally.
For instant testing, you can make guild-specific commands by adding guild parameter:

```python
@bot.tree.command(name="test", description="Test command", guild=discord.Object(id=YOUR_GUILD_ID))
```

### 6. Restart Discord
Sometimes Discord needs to be completely restarted to see new commands.