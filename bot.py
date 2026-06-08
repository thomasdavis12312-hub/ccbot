import discord
from discord import app_commands
import asyncio
import re
import json
from collections import defaultdict
import os

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1321806864778002452
DATA_FILE = "worker_logs.json"

BLACKLIST = {"495", "tbe", "fuerte", "fuerta", "zteam", "st", "TBE", "FUERTE", "FUERTA", "ZTEAM", "ST", "VUPU", "", "-"}

USD_TO_CNY = 6.8

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

logs = defaultdict(int)
sums = defaultdict(float)
user_map = {}

live_message = None

def load_data():
    global logs, sums, user_map
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logs = defaultdict(int, data.get('logs', {}))
                sums = defaultdict(float, data.get('sums', {}))
                user_map = data.get('user_map', {})
        except:
            pass

def save_data():
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump({'logs': dict(logs), 'sums': dict(sums), 'user_map': user_map}, f, ensure_ascii=False, indent=2)

def parse_sum(content: str) -> float:
    total = 0.0
    lower = content.lower()
    sum_match = re.search(r'сумма[:\s]*(.+?)(?:\n|$)', lower)
    if sum_match:
        line = sum_match.group(1)
        parts = re.split(r'\+', line)
        for part in parts:
            part = part.strip()
            num_match = re.search(r'(\d[\d\s.,]*)', part)
            if num_match:
                num = float(re.sub(r'[^\d.]', '', num_match.group(1).replace(',', '.')))
                if '$' in part or 'dollar' in part or 'дол' in part:
                    total += num * 0.70
                elif any(x in part for x in ['y', 'юан', 'yuan', 'dodep']):
                    total += num * 0.25
                else:
                    total += num * 0.70
    return round(total, 2)

async def update_live_top():
    global live_message
    if not live_message:
        return
    try:
        filtered = {k: v for k, v in logs.items() if str(k).lower() not in [x.lower() for x in BLACKLIST]}
        sorted_logs = sorted(filtered.items(), key=lambda x: x[1], reverse=True)
        
        embed = discord.Embed(title="🏆 LIVE TOP 10 WORKERS", color=0x000082, timestamp=discord.utils.utcnow())
        desc = ""
        for i, (uid, count) in enumerate(sorted_logs[:10], 1):
            name = user_map.get(uid, uid)
            mention = f"<@{uid}>" if uid.isdigit() else name
            clean_sum = round(sums.get(uid, 0), 2)
            desc += f"**#{i}** {mention} — **{count}** логов | **${clean_sum:,}**\n"
        
        embed.description = desc if desc else "Пока нет данных"
        
        # Футер с "Ваше место"
        embed.set_footer(text="Ваше место в топе смотрите командой /top")
        
        await live_message.edit(embed=embed)
    except:
        live_message = None

@client.event
async def on_ready():
    print(f"GUARD online: {client.user}")
    load_data()
    await scan_channel()
    await tree.sync()
    print("Бот запущен. Пиши /starttop")
    
    while True:
        await asyncio.sleep(300)
        await update_live_top()

async def scan_channel():
    global logs, sums, user_map
    channel = client.get_channel(CHANNEL_ID)
    if not channel:
        print("Канал не найден")
        return
    print("Сканирую канал...")
    logs.clear()
    sums.clear()
    user_map.clear()
    count = 0
    async for message in channel.history(limit=None, oldest_first=True):
        if process_message(message):
            count += 1
    save_data()
    print(f"Сканирование завершено. Воркеров: {len(logs)}")

def process_message(message):
    global logs, sums, user_map
    content = message.content
    if not re.search(r'\bWorker\s*:', content, re.IGNORECASE):
        return False
    has_sticker = bool(message.stickers) or "❄️" in content or ":vacsticker:" in content
    if not has_sticker:
        return False
    match = re.search(r'Worker\s*:\s*@?(.+?)(?:\s|$|by:|\n)', content, re.IGNORECASE | re.DOTALL)
    if not match:
        return False
    worker_raw = match.group(1).strip()
    worker_raw = re.sub(r'<@!?(\d+)>', r'\1', worker_raw).strip()
    if not worker_raw or len(worker_raw) < 2:
        return False
    uid = worker_raw if not worker_raw.isdigit() else str(worker_raw)
    if worker_raw.isdigit():
        member = message.guild.get_member(int(worker_raw)) if message.guild else None
        if member:
            user_map[uid] = member.display_name or member.name
    else:
        user_map[uid] = worker_raw
    logs[uid] += 1
    sums[uid] += parse_sum(content)
    return True

@tree.command(name="starttop", description="Запустить автообновляемый топ")
async def starttop(interaction: discord.Interaction):
    global live_message
    await interaction.response.defer(ephemeral=True)
    
    embed = discord.Embed(title="🏆 LIVE TOP 10 WORKERS", color=0x00ff00, description="Загрузка...")
    live_message = await interaction.channel.send(embed=embed)
    
    await interaction.followup.send("✅ Live топ запущен! Обновляется каждые 5 минут.", ephemeral=True)
    await update_live_top()

@tree.command(name="top", description="Ваше личное место в топе")
async def top(interaction: discord.Interaction):
    filtered = {k: v for k, v in logs.items() if str(k).lower() not in [x.lower() for x in BLACKLIST]}
    sorted_logs = sorted(filtered.items(), key=lambda x: x[1], reverse=True)
    
    user_id = str(interaction.user.id)
    user_logs = filtered.get(user_id, 0)
    user_sum = round(sums.get(user_id, 0), 2)
    user_rank = 0
    if user_logs > 0:
        for rank, (uid, cnt) in enumerate(sorted_logs, 1):
            if uid == user_id:
                user_rank = rank
                break
    
    if user_rank > 0:
        await interaction.response.send_message(f"**Ваше место в топе: #{user_rank} | ${user_sum:,}**", ephemeral=True)
    else:
        await interaction.response.send_message("Вы пока не в топе.", ephemeral=True)

@tree.command(name="rescan", description="Полный перескан")
async def rescan(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await scan_channel()
    await interaction.followup.send("Рескан завершён.", ephemeral=True)
    if live_message:
        await update_live_top()

client.run(TOKEN)