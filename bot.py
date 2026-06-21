import discord
from discord import app_commands
import asyncio
import re
import json
from collections import defaultdict
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1321806864778002452
DATA_FILE = "worker_logs.json"

VAKER_USER_IDS = {
    1179432470563803166,
    1294020299829936200,
    771447393479950336,
}

BLACKLIST = {"495", "tbe", "fuerte", "fuerta", "zteam", "st", "TBE", "FUERTE", "FUERTA", "ZTEAM", "ST", "VUPU", "", "-"}

USD_TO_CNY = 6.8
DODEP_RATE = 0.25

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

logs = defaultdict(int)
sums = defaultdict(float)
user_map = {}

live_message = None
commands_registered = False

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

def parse_number(raw: str) -> float:
    clean = re.sub(r'[^\d.,]', '', raw).replace(',', '.')
    if clean.count('.') > 1:
        clean = clean.replace('.', '', clean.count('.') - 1)
    return float(clean) if clean else 0.0

def is_cny_amount(part: str) -> bool:
    return bool(re.search(r'(?:\d[\d\s.,]*\s*(?:y|¥)|yuan|юан)', part, re.IGNORECASE))

def parse_sum(content: str) -> float:
    total = 0.0
    sum_match = re.search(r'сумма\s*:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
    if not sum_match:
        return total

    for index, part in enumerate(re.split(r'\+', sum_match.group(1))):
        part = part.strip()
        num_match = re.search(r'(\d[\d\s.,]*)', part)
        if not num_match:
            continue

        amount = parse_number(num_match.group(1))
        if index > 0 or re.search(r'\b(?:dodep|dod|додеп|дод)\b', part, re.IGNORECASE):
            if is_cny_amount(part):
                amount = amount / USD_TO_CNY
            total += amount * DODEP_RATE
        else:
            total += amount

    return round(total, 2)

def has_vac_marker(message) -> bool:
    content = message.content.lower()
    return (
        bool(message.stickers)
        or "❄️" in message.content
        or ":vacsticker:" in content
        or ":blow:" in content
    )

def parse_worker(content: str):
    match = re.search(r'^\s*.*?Worker\s*:\s*(.+?)\s*$', content, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None

    worker_raw = match.group(1).strip()
    mention_match = re.search(r'<@!?(\d+)>', worker_raw)
    if mention_match:
        return mention_match.group(1)

    user_match = re.search(r'@([^\s<@#:,]+)', worker_raw)
    if user_match:
        return user_match.group(1).strip()

    worker_raw = re.sub(r'^@', '', worker_raw).strip()
    return worker_raw or None

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
    global commands_registered
    print(f"GUARD online: {client.user}")
    load_data()
    await scan_channel()
    if not commands_registered:
        channel = client.get_channel(CHANNEL_ID)
        if channel and channel.guild:
            guild = discord.Object(id=channel.guild.id)
            tree.copy_global_to(guild=guild)
            synced = await tree.sync(guild=guild)

            # Команды используются только на этом сервере. Удаляем их старые
            # глобальные копии, иначе Discord показывает каждую команду дважды.
            tree.clear_commands(guild=None)
            await tree.sync()
            print(f"Команды сервера обновлены: {', '.join(command.name for command in synced)}")
        else:
            synced = await tree.sync()
            print(f"Глобальные команды обновлены: {', '.join(command.name for command in synced)}")
        commands_registered = True
    print("Бот запущен. Пиши /starttop")
    
    while True:
        await asyncio.sleep(86400)
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
        if process_message(message) or process_log_embed(message):
            count += 1
    save_data()
    print(f"Сканирование завершено. Воркеров: {len(logs)}")

def process_message(message):
    global logs, sums, user_map
    content = message.content
    if not re.search(r'\bWorker\s*:', content, re.IGNORECASE):
        return False
    if not has_vac_marker(message):
        return False

    worker_raw = parse_worker(content)
    if not worker_raw:
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

def process_log_embed(message) -> bool:
    """Добавляет в статистику сообщения, созданные командой /log."""
    if message.author != client.user or not message.embeds:
        return False

    embed = message.embeds[0]
    if "НОВЫЙ ПРОФИТ" not in (embed.description or "").upper():
        return False

    fields = {field.name.casefold(): field.value for field in embed.fields}
    worker_raw = fields.get("воркер")
    amount_raw = fields.get("сумма")
    if not worker_raw or not amount_raw:
        return False

    worker_id = parse_worker(f"Worker: {worker_raw}")
    if not worker_id:
        return False

    uid = str(worker_id)
    member = message.guild.get_member(int(uid)) if uid.isdigit() and message.guild else None
    if member:
        user_map[uid] = member.display_name or member.name

    total = parse_number(amount_raw)
    dodep_raw = fields.get("додеп")
    if dodep_raw and re.search(r'\d', dodep_raw):
        dodep = parse_number(dodep_raw)
        if is_cny_amount(dodep_raw):
            dodep /= USD_TO_CNY
        total += dodep * DODEP_RATE

    logs[uid] += 1
    sums[uid] += round(total, 2)
    return True

@client.event
async def on_message(message):
    if process_log_embed(message):
        save_data()
        await update_live_top()


def log_panel_text(view) -> str:
    mark = lambda value: "✅" if value else "▫️"
    action = "Редактирование лога" if view.target_message else "Новый лог"
    return "\n".join((
        f"## {action}",
        "Выберите воркера. Вакер и третий участник — необязательно.",
        "",
        f"{mark(view.worker_id)} Воркер",
        f"{mark(view.vaker_id)} Вакер (необязательно)",
        f"{mark(view.third_party_id)} {view.third_party_role} (необязательно)",
        f"{mark(view.region)} Регион",
        f"{mark(view.amount)} Сумма",
    ))


def participant_text(value) -> str:
    """Показывает Discord-участника как упоминание, а любое другое значение как текст."""
    if isinstance(value, int):
        return f"<@{value}>"
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def normalize_dollar_amount(value: str) -> str:
    """Убирает введённые знаки $ и добавляет один знак в конец суммы."""
    return f"{value.replace('$', '').strip()}$"


async def find_guild_member(guild: discord.Guild, raw_value: str):
    """Ищет участника на всём сервере, а не только среди видимых в канале."""
    value = raw_value.strip()
    mention = re.fullmatch(r'<@!?(\d+)>', value)
    user_id = mention.group(1) if mention else value

    if user_id.isdigit():
        member = guild.get_member(int(user_id))
        if member:
            return member
        try:
            return await guild.fetch_member(int(user_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    query = value.removeprefix("@").strip()
    if not query:
        return None

    normalized = query.casefold()
    cached_matches = [
        member for member in guild.members
        if normalized in {
            member.name.casefold(),
            member.display_name.casefold(),
            (member.global_name or "").casefold(),
        }
    ]
    if len(cached_matches) == 1:
        return cached_matches[0]

    try:
        queried = await guild.query_members(query=query, limit=100, cache=True)
    except (discord.Forbidden, discord.HTTPException):
        queried = []

    matches = [
        member for member in queried
        if normalized in {
            member.name.casefold(),
            member.display_name.casefold(),
            (member.global_name or "").casefold(),
        }
    ]
    if len(matches) == 1:
        return matches[0]

    # Discord иногда не возвращает часть людей в UserSelect/
    # query_members. REST-обход проверяет полный состав сервера.
    fetched_matches = []
    try:
        async for member in guild.fetch_members(limit=None):
            if normalized in {
                member.name.casefold(),
                member.display_name.casefold(),
                (member.global_name or "").casefold(),
            }:
                fetched_matches.append(member)
                if len(fetched_matches) > 1:
                    break
    except (discord.ClientException, discord.Forbidden, discord.HTTPException):
        return None

    return fetched_matches[0] if len(fetched_matches) == 1 else None


class MemberLookupModal(discord.ui.Modal):
    member_value = discord.ui.TextInput(
        label="Ник, ID или любой текст",
        placeholder="Например: archivfix или FUERTE",
        max_length=100,
    )

    def __init__(self, panel, target: str):
        self.panel = panel
        self.target = target
        title = "Указать воркера" if target == "worker" else f"Указать: {panel.third_party_role}"
        super().__init__(title=title, timeout=300)

    async def on_submit(self, interaction: discord.Interaction):
        raw_value = str(self.member_value).strip()
        await interaction.response.defer()
        if self.target != "worker" and raw_value.casefold() in {"-", "нет", "none"}:
            value = None
        else:
            member = await find_guild_member(interaction.guild, raw_value)
            value = member.id if member else raw_value

        if self.target == "worker":
            self.panel.worker_id = value
        else:
            self.panel.third_party_id = value
        await interaction.edit_original_response(
            content=log_panel_text(self.panel), view=self.panel
        )


class LogModal(discord.ui.Modal, title="Новый лог"):
    amount = discord.ui.TextInput(
        label="5. Сумма", placeholder="Например: 403$", max_length=50
    )
    dodep = discord.ui.TextInput(
        label="6. Додеп", placeholder="Например: 147.34 или нет",
        max_length=100, required=False
    )

    def __init__(self, panel):
        title = "Редактировать лог" if panel.target_message else "Новый лог"
        super().__init__(title=title, timeout=900)
        self.panel = panel
        if panel.amount:
            self.amount.default = panel.amount.removesuffix("$")
        if panel.dodep and panel.dodep != "—":
            self.dodep.default = panel.dodep

    async def on_submit(self, interaction: discord.Interaction):
        panel = self.panel
        if panel.finished or not panel.worker_id or not panel.region:
            await interaction.response.send_message("Форма устарела. Введите `/log` ещё раз.", ephemeral=True)
            return

        panel.amount = normalize_dollar_amount(str(self.amount))
        panel.dodep = str(self.dodep).strip() or None
        await interaction.response.defer(ephemeral=True)

        try:
            screenshot_files = []
            for index, attachment in enumerate(panel.screenshots, 1):
                content_type = attachment.content_type or "image/png"
                extension = {
                    "image/jpeg": "jpg", "image/png": "png",
                    "image/gif": "gif", "image/webp": "webp",
                }.get(content_type, "png")
                screenshot_files.append(await attachment.to_file(
                    filename=f"log-screenshot-{index}.{extension}"
                ))
            if screenshot_files:
                image_urls = [f"attachment://{item.filename}" for item in screenshot_files]
            elif panel.target_message:
                image_urls = [item.url for item in panel.target_message.attachments]
            else:
                image_urls = []
            profit_emoji = discord.utils.get(interaction.guild.emojis, name="267042fire") or "🔥"
            embed = discord.Embed(
                color=0x000082,
                description=f"## {profit_emoji} НОВЫЙ ПРОФИТ",
            )
            # Всегда держим строгую сетку 3 x 2. Discord меняет ширину колонок,
            # если в строке меньше трёх inline-полей, поэтому пустые значения
            # тоже должны занимать свои фиксированные места.
            embed.add_field(name="Воркер", value=participant_text(panel.worker_id), inline=True)
            embed.add_field(
                name="Вакер",
                value=f"<@{panel.vaker_id}>" if panel.vaker_id else "—",
                inline=True,
            )
            embed.add_field(
                name=panel.third_party_role,
                value=participant_text(panel.third_party_id) if panel.third_party_id else "—",
                inline=True,
            )
            embed.add_field(name="Сумма", value=panel.amount, inline=True)
            embed.add_field(name="Додеп", value=panel.dodep or "—", inline=True)
            embed.add_field(name="Регион", value=panel.region, inline=True)
            if image_urls:
                embed.set_image(url=image_urls[0])
            embeds = [embed]
            for image_url in image_urls[1:]:
                image_embed = discord.Embed(color=0x000082)
                image_embed.set_image(url=image_url)
                embeds.append(image_embed)

            user_ids = list(dict.fromkeys(
                value for value in (panel.worker_id, panel.vaker_id, panel.third_party_id)
                if isinstance(value, int)
            ))
            mentions = " ".join(f"<@{user_id}>" for user_id in user_ids)
            message_kwargs = dict(
                content=f"-# ||@everyone {mentions}||", embeds=embeds,
                allowed_mentions=discord.AllowedMentions(
                    everyone=True, users=True, roles=False, replied_user=False),
            )
            if panel.target_message:
                message_kwargs["allowed_mentions"] = discord.AllowedMentions.none()
                if screenshot_files:
                    message_kwargs["attachments"] = screenshot_files
                await panel.target_message.edit(**message_kwargs)
                await scan_channel()
                await update_live_top()
            else:
                message_kwargs["files"] = screenshot_files
                await interaction.channel.send(**message_kwargs)
            panel.finished = True
            panel.stop()
            await interaction.delete_original_response()
        except Exception as error:
            await interaction.edit_original_response(
                content=f"Не удалось сохранить лог.\n`{str(error)[:500]}`"
            )


class LogPanel(discord.ui.View):
    def __init__(self, owner_id: int, screenshots, vakers, target_message=None, initial=None):
        super().__init__(timeout=900)
        initial = initial or {}
        self.owner_id = owner_id
        self.screenshots = screenshots
        self.target_message = target_message
        self.worker_id = initial.get("worker_id")
        self.vaker_id = initial.get("vaker_id")
        self.third_party_id = initial.get("third_party_id")
        self.third_party_role = initial.get("third_party_role", "Вичатер")
        self.region = initial.get("region")
        self.amount = initial.get("amount")
        self.dodep = initial.get("dodep")
        self.finished = False
        self.third_party.placeholder = f"3. {self.third_party_role}"
        self.toggle_third_role.label = f"Роль: {self.third_party_role}"
        self.vaker.options = [
            discord.SelectOption(
                label="Не указывать вакера", value="none",
                description="Оставить поле пустым", default=not self.vaker_id,
            ),
            *[
                discord.SelectOption(
                    label=member.display_name[:100], value=str(member.id),
                    description=f"@{member.name}"[:100],
                    default=member.id == self.vaker_id,
                )
                for member in vakers[:24]
            ],
        ]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Эта форма принадлежит другому пользователю.", ephemeral=True
        )
        return False

    async def refresh(self, interaction):
        await interaction.response.edit_message(content=log_panel_text(self), view=self)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="1. Воркер", row=0)
    async def worker(self, interaction, select):
        self.worker_id = select.values[0].id
        await self.refresh(interaction)

    @discord.ui.select(cls=discord.ui.Select, placeholder="2. Вакер", row=1)
    async def vaker(self, interaction, select):
        self.vaker_id = None if select.values[0] == "none" else int(select.values[0])
        await self.refresh(interaction)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="3. Вичатер", row=2)
    async def third_party(self, interaction, select):
        self.third_party_id = select.values[0].id
        await self.refresh(interaction)

    @discord.ui.select(
        placeholder="4. Регион",
        options=[
            discord.SelectOption(label="China", value="🇨🇳", emoji="🇨🇳"),
            discord.SelectOption(label="EU", value="🇪🇺", emoji="🇪🇺"),
            discord.SelectOption(label="USA", value="🇺🇸", emoji="🇺🇸"),
        ],
        row=3,
    )
    async def region_select(self, interaction, select):
        self.region = select.values[0]
        await self.refresh(interaction)

    @discord.ui.button(label="Сумма и додеп", emoji="📝", style=discord.ButtonStyle.primary, row=4)
    async def details(self, interaction, button):
        if not self.worker_id:
            await interaction.response.send_message("Сначала выберите воркера.", ephemeral=True)
            return
        if not self.region:
            await interaction.response.send_message("Сначала выерите регион.", ephemeral=True)
            return
        await interaction.response.send_modal(LogModal(self))

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.danger, row=4)
    async def cancel(self, interaction, button):
        self.finished = True
        self.stop()
        await interaction.response.edit_message(content="Создание лога отменено.", view=None)

    @discord.ui.button(label="Воркер: ник / текст", style=discord.ButtonStyle.secondary, row=4)
    async def worker_by_name(self, interaction, button):
        await interaction.response.send_modal(MemberLookupModal(self, "worker"))

    @discord.ui.button(label="Участник: ник / текст", style=discord.ButtonStyle.secondary, row=4)
    async def third_party_by_name(self, interaction, button):
        await interaction.response.send_modal(MemberLookupModal(self, "third_party"))

    @discord.ui.button(label="Роль: Вичатер", style=discord.ButtonStyle.secondary, row=4)
    async def toggle_third_role(self, interaction, button):
        self.third_party_role = "Спикер" if self.third_party_role == "Вичатер" else "Вичатер"
        self.third_party_id = None
        self.third_party.placeholder = f"3. {self.third_party_role}"
        button.label = f"Роль: {self.third_party_role}"
        await self.refresh(interaction)


def stored_participant(value: Optional[str]):
    if not value or value == "—":
        return None
    mention = re.fullmatch(r'<@!?(\d+)>', value.strip())
    return int(mention.group(1)) if mention else value.strip()


async def fetch_log_message(interaction: discord.Interaction, reference: str):
    numbers = re.findall(r'\d{15,22}', reference)
    if not numbers:
        return None
    if "discord.com/channels/" in reference and len(numbers) >= 3:
        guild_id, channel_id, message_id = map(int, numbers[-3:])
        if guild_id != interaction.guild_id:
            return None
        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            try:
                channel = await interaction.guild.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
    else:
        channel = interaction.channel
        message_id = int(numbers[-1])
    try:
        return await channel.fetch_message(message_id)
    except (AttributeError, discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


@tree.command(name="log", description="Создать лог")
@app_commands.describe(
    скрин="Первый скриншот для лога",
    скрин2="Дополнительный скриншот",
    скрин3="Дополнительный скриншот",
    скрин4="Дополнительный скриншот",
    скрин5="Дополнительный скриншот",
)
async def log(
    interaction: discord.Interaction,
    скрин: discord.Attachment,
    скрин2: Optional[discord.Attachment] = None,
    скрин3: Optional[discord.Attachment] = None,
    скрин4: Optional[discord.Attachment] = None,
    скрин5: Optional[discord.Attachment] = None,
):
    if interaction.user.id not in VAKER_USER_IDS:
        await interaction.response.send_message(
            "У вас нет доступа к команде `/log`.", ephemeral=True
        )
        return
    screenshots = [item for item in (скрин, скрин2, скрин3, скрин4, скрин5) if item]
    if any(not (item.content_type or "").startswith("image/") for item in screenshots):
        await interaction.response.send_message(
            "Во всех полях **скрин** должны быть прикреплены изображения.", ephemeral=True
        )
        return
    if not interaction.guild or not isinstance(interaction.channel, discord.abc.GuildChannel):
        await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
        return

    permissions = interaction.channel.permissions_for(interaction.guild.me)
    required = {
        "view_channel": "Просматривать канал",
        "send_messages": "Отправлять сообщения",
        "embed_links": "Встраивать ссылки",
        "attach_files": "Прикреплять файлы",
        "mention_everyone": "Упоминать @everyone, @here и все роли",
    }
    missing = [label for name, label in required.items() if not getattr(permissions, name)]
    if missing:
        await interaction.response.send_message(
            "Боту не хватает прав в этом канале:\n• " + "\n• ".join(missing), ephemeral=True
        )
        return

    vakers = [interaction.guild.get_member(user_id) for user_id in VAKER_USER_IDS]
    vakers = sorted(filter(None, vakers), key=lambda member: member.display_name.casefold())
    if not vakers:
        await interaction.response.send_message(
            "Ни один разрешённый вакер не найден на этом сервере.", ephemeral=True
        )
        return

    panel = LogPanel(interaction.user.id, screenshots, vakers)
    await interaction.response.send_message(
        log_panel_text(panel), view=panel, ephemeral=True
    )


@tree.command(name="editlog", description="Изменить уже опубликованный лог")
@app_commands.describe(
    сообщение="Ссылка на лог или ID сообщения",
    скрин="Новый первый скриншот (необязательно)",
    скрин2="Новый дополнительный скриншот",
    скрин3="Новый дополнительный скриншот",
    скрин4="Новый дополнительный скриншот",
    скрин5="Новый дополнительный скриншот",
)
async def editlog(
    interaction: discord.Interaction,
    сообщение: str,
    скрин: Optional[discord.Attachment] = None,
    скрин2: Optional[discord.Attachment] = None,
    скрин3: Optional[discord.Attachment] = None,
    скрин4: Optional[discord.Attachment] = None,
    скрин5: Optional[discord.Attachment] = None,
):
    if interaction.user.id not in VAKER_USER_IDS:
        await interaction.response.send_message(
            "У вас нет доступа к команде `/editlog`.", ephemeral=True
        )
        return
    if not interaction.guild:
        await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
        return

    replacements = [item for item in (скрин, скрин2, скрин3, скрин4, скрин5) if item]
    if replacements and not скрин:
        await interaction.response.send_message("Новые скрины нужно начинать с поля `скрин`.", ephemeral=True)
        return
    if any(not (item.content_type or "").startswith("image/") for item in replacements):
        await interaction.response.send_message("Все новые скрины должны быть изображениями.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    message = await fetch_log_message(interaction, сообщение)
    if not message or message.author.id != client.user.id or not message.embeds:
        await interaction.followup.send("Лог не найден. Проверьте ссылку/ID и что сообщение создано этим ботом.", ephemeral=True)
        return
    embed = message.embeds[0]
    if "НОВЫЙ ПРОФИТ" not in (embed.description or "").upper():
        await interaction.followup.send("Это сообщение не является логом.", ephemeral=True)
        return

    fields = {field.name.casefold(): field.value for field in embed.fields}
    third_role = "Спикер" if "спикер" in fields else "Вичатер"
    initial = {
        "worker_id": stored_participant(fields.get("воркер")),
        "vaker_id": stored_participant(fields.get("вакер")),
        "third_party_id": stored_participant(fields.get(third_role.casefold())),
        "third_party_role": third_role,
        "amount": fields.get("сумма"),
        "dodep": None if fields.get("додеп") in (None, "—") else fields.get("додеп"),
        "region": fields.get("регион"),
    }
    vakers = sorted(
        filter(None, (interaction.guild.get_member(user_id) for user_id in VAKER_USER_IDS)),
        key=lambda member: member.display_name.casefold(),
    )
    panel = LogPanel(interaction.user.id, replacements, vakers, message, initial)
    await interaction.followup.send(log_panel_text(panel), view=panel, ephemeral=True)

@tree.command(name="starttop", description="Запустить автообновляемый топ")
async def starttop(interaction: discord.Interaction):
    global live_message
    await interaction.response.defer(ephemeral=True)
    
    embed = discord.Embed(title="🏆 LIVE TOP 10 WORKERS", color=0x00ff00, description="Загрузка...")
    live_message = await interaction.channel.send(embed=embed)
    
    await interaction.followup.send("✅ Live топ запущен! Обновляется раз в день.", ephemeral=True)
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
