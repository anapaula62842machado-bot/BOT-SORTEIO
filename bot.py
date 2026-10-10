import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
INVITE = os.getenv("FUSION_INVITE", "https://discord.gg/nSvgAgQze5").strip()
DB_PATH = os.getenv("DATABASE_PATH", "inscricoes.sqlite3")
# Coloque os IDs de usuários autorizados no Railway, separados por vírgula.
OWNER_IDS = {int(value.strip()) for value in os.getenv("OWNER_IDS", "").split(",") if value.strip().isdigit()}
bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def initialize():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS raffle(
          guild INTEGER PRIMARY KEY, channel INTEGER NOT NULL, message INTEGER,
          title TEXT NOT NULL, description TEXT NOT NULL, image TEXT,
          deadline TEXT NOT NULL, capacity INTEGER NOT NULL,
          status TEXT NOT NULL, winners TEXT
        );
        CREATE TABLE IF NOT EXISTS entrants(
          guild INTEGER NOT NULL, user INTEGER NOT NULL, joined TEXT NOT NULL,
          PRIMARY KEY(guild,user)
        );
        """)


def get_raffle(guild_id):
    with db() as con:
        return con.execute("SELECT * FROM raffle WHERE guild=?", (guild_id,)).fetchone()


def get_entrants(guild_id):
    with db() as con:
        return con.execute("SELECT user FROM entrants WHERE guild=? ORDER BY joined", (guild_id,)).fetchall()


def authorized(interaction):
    # O dono do servidor também continua autorizado automaticamente.
    return interaction.guild is not None and (
        interaction.user.id == interaction.guild.owner_id or interaction.user.id in OWNER_IDS
    )


def make_embed(row):
    total = len(get_entrants(row["guild"]))
    deadline = datetime.fromisoformat(row["deadline"])
    embed = discord.Embed(title=row["title"], description=row["description"], color=0x5865F2)
    embed.add_field(name="Participantes", value=f"{total}/{row['capacity']}")
    embed.add_field(name="Prazo", value=discord.utils.format_dt(deadline, "f"))
    embed.add_field(name="Status", value="Aberta" if row["status"] == "open" else "Encerrada")
    if row["image"]:
        embed.set_image(url=row["image"])
    return embed


async def refresh_public(guild, row):
    if not row or not row["message"]:
        return
    channel = guild.get_channel(row["channel"])
    if not channel:
        return
    try:
        message = await channel.fetch_message(row["message"])
        await message.edit(embed=make_embed(row), view=JoinView() if row["status"] == "open" else None)
    except discord.HTTPException:
        pass


class ConfirmJoinView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=180)
        self.guild_id = guild_id

    @discord.ui.button(label="Já entrei — confirmar", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction, button):
        row = get_raffle(self.guild_id)
        if not row or row["status"] != "open" or datetime.now(timezone.utc) >= datetime.fromisoformat(row["deadline"]):
            return await interaction.response.send_message("As inscrições estão encerradas.", ephemeral=True)
        with db() as con:
            if con.execute("SELECT 1 FROM entrants WHERE guild=? AND user=?", (self.guild_id, interaction.user.id)).fetchone():
                return await interaction.response.send_message("Você já está inscrito.", ephemeral=True)
            count = con.execute("SELECT COUNT(*) FROM entrants WHERE guild=?", (self.guild_id,)).fetchone()[0]
            if count >= row["capacity"]:
                return await interaction.response.send_message("As vagas já foram preenchidas.", ephemeral=True)
            con.execute("INSERT INTO entrants VALUES(?,?,?)", (self.guild_id, interaction.user.id, datetime.now(timezone.utc).isoformat()))
        await interaction.response.send_message("Inscrição confirmada! Você está participando.", ephemeral=True)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.send_message("Inscrição cancelada; seu nome não foi adicionado.", ephemeral=True)


class JoinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Participar", style=discord.ButtonStyle.success, custom_id="fairdraw:join")
    async def join(self, interaction, button):
        row = get_raffle(interaction.guild_id)
        if not row or row["status"] != "open" or datetime.now(timezone.utc) >= datetime.fromisoformat(row["deadline"]):
            return await interaction.response.send_message("As inscrições estão fechadas.", ephemeral=True)
        with db() as con:
            if con.execute("SELECT 1 FROM entrants WHERE guild=? AND user=?", (interaction.guild_id, interaction.user.id)).fetchone():
                return await interaction.response.send_message("Você já está inscrito.", ephemeral=True)
            count = con.execute("SELECT COUNT(*) FROM entrants WHERE guild=?", (interaction.guild_id,)).fetchone()[0]
        if count >= row["capacity"]:
            return await interaction.response.send_message("As vagas já foram preenchidas.", ephemeral=True)
        await interaction.response.send_message(
            f"Você já entrou na org? Acesse {INVITE} e confirme abaixo.",
            view=ConfirmJoinView(interaction.guild_id), ephemeral=True,
        )


class SetupModal(discord.ui.Modal, title="Configurar inscrição"):
    title_field = discord.ui.TextInput(label="Título", max_length=256, placeholder="Ex.: Inscrição para o sorteio")
    description_field = discord.ui.TextInput(label="Descrição", style=discord.TextStyle.paragraph, max_length=1000, placeholder="Regras e informações")
    hours_field = discord.ui.TextInput(label="Prazo em horas (1 a 720)", max_length=3, placeholder="24")
    capacity_field = discord.ui.TextInput(label="Limite de participantes (1 a 10000)", max_length=5, placeholder="100")
    image_field = discord.ui.TextInput(label="URL da imagem (opcional)", required=False, max_length=1000, placeholder="https://...")

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction):
        try:
            hours, capacity = int(self.hours_field.value), int(self.capacity_field.value)
        except ValueError:
            return await interaction.response.send_message("Prazo e limite precisam ser números inteiros.", ephemeral=True)
        if not 1 <= hours <= 720 or not 1 <= capacity <= 10000:
            return await interaction.response.send_message("Prazo: 1–720 horas. Limite: 1–10000 pessoas.", ephemeral=True)
        image = self.image_field.value.strip()
        if image and not image.startswith(("https://", "http://")):
            return await interaction.response.send_message("O link da imagem deve começar com http:// ou https://.", ephemeral=True)
        deadline = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        with db() as con:
            con.execute("DELETE FROM entrants WHERE guild=?", (interaction.guild_id,))
            con.execute(
                "INSERT OR REPLACE INTO raffle VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                (interaction.guild_id, self.channel.id, None, self.title_field.value.strip(),
                 self.description_field.value.strip(), image or None, deadline, capacity, "open"),
            )
        try:
            message = await self.channel.send(embed=make_embed(get_raffle(interaction.guild_id)), view=JoinView())
        except discord.Forbidden:
            return await interaction.response.send_message("Não tenho permissão para publicar nesse canal.", ephemeral=True)
        with db() as con:
            con.execute("UPDATE raffle SET message=? WHERE guild=?", (message.id, interaction.guild_id))
        await interaction.response.send_message(f"Painel publicado em {self.channel.mention}.", ephemeral=True)


class RemoveModal(discord.ui.Modal, title="Remover participante"):
    user_id = discord.ui.TextInput(label="ID ou menção da pessoa", max_length=25)

    def __init__(self, guild_id):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction):
        try:
            uid = int(self.user_id.value.strip().replace("<@", "").replace(">", "").replace("!", ""))
        except ValueError:
            return await interaction.response.send_message("ID inválido.", ephemeral=True)
        with db() as con:
            result = con.execute("DELETE FROM entrants WHERE guild=? AND user=?", (self.guild_id, uid))
        await interaction.response.send_message("Participante removido." if result.rowcount else "Pessoa não está inscrita.", ephemeral=True)


class DrawModal(discord.ui.Modal, title="Sortear vencedores"):
    amount = discord.ui.TextInput(label="Número de vencedores", max_length=4, placeholder="5")

    def __init__(self, guild_id):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction):
        people = [x["user"] for x in get_entrants(self.guild_id)]
        try:
            amount = int(self.amount.value)
        except ValueError:
            return await interaction.response.send_message("Digite um número inteiro.", ephemeral=True)
        if not 1 <= amount <= len(people):
            return await interaction.response.send_message(f"Escolha de 1 a {len(people)}.", ephemeral=True)
        winners = random.SystemRandom().sample(people, amount)
        with db() as con:
            con.execute("UPDATE raffle SET status='closed',winners=? WHERE guild=?", (",".join(map(str, winners)), self.guild_id))
        await refresh_public(interaction.guild, get_raffle(self.guild_id))
        await interaction.response.send_message("Prévia privada, ainda não publicada:\n" + ", ".join(f"<@{x}>" for x in winners) + "\nRevise e confirme no painel.", ephemeral=True)


class DmModal(discord.ui.Modal, title="Enviar mensagem privada"):
    message = discord.ui.TextInput(label="Mensagem", style=discord.TextStyle.paragraph, max_length=1800)

    def __init__(self, member):
        super().__init__()
        self.member = member

    async def on_submit(self, interaction):
        try:
            await self.member.send(self.message.value)
            await interaction.response.send_message(f"Mensagem enviada para {self.member.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Não consegui enviar; a pessoa pode estar com as DMs fechadas.", ephemeral=True)


class OwnerView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=900)
        self.guild_id = guild_id

    async def interaction_check(self, interaction):
        if not authorized(interaction):
            await interaction.response.send_message("Você não está na lista de donos autorizados.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Ver participantes", style=discord.ButtonStyle.secondary)
    async def show_people(self, interaction, button):
        rows = get_entrants(self.guild_id)
        text = "\n".join(f"<@{x['user']}> — `{x['user']}`" for x in rows) or "Ainda não há participantes."
        await interaction.response.send_message(text[:1900], ephemeral=True)

    @discord.ui.button(label="Fechar inscrições", style=discord.ButtonStyle.danger)
    async def close(self, interaction, button):
        with db() as con:
            con.execute("UPDATE raffle SET status='closed' WHERE guild=?", (self.guild_id,))
        await refresh_public(interaction.guild, get_raffle(self.guild_id))
        await interaction.response.send_message("Inscrições fechadas.", ephemeral=True)

    @discord.ui.button(label="Reabrir inscrições", style=discord.ButtonStyle.success)
    async def reopen(self, interaction, button):
        row = get_raffle(self.guild_id)
        if datetime.now(timezone.utc) >= datetime.fromisoformat(row["deadline"]):
            return await interaction.response.send_message("O prazo já terminou.", ephemeral=True)
        with db() as con:
            con.execute("UPDATE raffle SET status='open',winners=NULL WHERE guild=?", (self.guild_id,))
        await refresh_public(interaction.guild, get_raffle(self.guild_id))
        await interaction.response.send_message("Inscrições reabertas.", ephemeral=True)

    @discord.ui.button(label="Remover participante", style=discord.ButtonStyle.secondary)
    async def remove(self, interaction, button):
        await interaction.response.send_modal(RemoveModal(self.guild_id))

    @discord.ui.button(label="Sortear", style=discord.ButtonStyle.primary)
    async def draw(self, interaction, button):
        if not get_entrants(self.guild_id):
            return await interaction.response.send_message("Não há participantes.", ephemeral=True)
        await interaction.response.send_modal(DrawModal(self.guild_id))

    @discord.ui.button(label="Confirmar resultado", style=discord.ButtonStyle.success)
    async def publish(self, interaction, button):
        row = get_raffle(self.guild_id)
        if not row or not row["winners"]:
            return await interaction.response.send_message("Faça um sorteio primeiro.", ephemeral=True)
        if row["status"] == "announced":
            return await interaction.response.send_message("Resultado já publicado.", ephemeral=True)
        channel = interaction.guild.get_channel(row["channel"])
        if channel is None:
            return await interaction.response.send_message("Não encontrei o canal do painel.", ephemeral=True)
        winners = [int(x) for x in row["winners"].split(",")]
        await channel.send(f"🎉 Resultado confirmado de **{row['title']}**! Vencedores: " + ", ".join(f"<@{x}>" for x in winners))
        with db() as con:
            con.execute("UPDATE raffle SET status='announced' WHERE guild=?", (self.guild_id,))
        sent, failed = 0, []
        for uid in winners:
            try:
                member = interaction.guild.get_member(uid) or await interaction.guild.fetch_member(uid)
                await member.send(f"Parabéns! Você ganhou **{row['title']}**. Entre em contato com a organização para combinar os próximos passos.")
                sent += 1
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                failed.append(str(uid))
        await interaction.response.send_message(f"Publicado. DMs enviadas: {sent}; falha para IDs: {', '.join(failed) if failed else 'nenhum'}.", ephemeral=True)


@tasks.loop(seconds=30)
async def check_deadlines():
    with db() as con:
        rows = con.execute("SELECT * FROM raffle WHERE status='open'").fetchall()
        expired = [r for r in rows if datetime.now(timezone.utc) >= datetime.fromisoformat(r["deadline"])]
        for row in expired:
            con.execute("UPDATE raffle SET status='closed' WHERE guild=?", (row["guild"],))
    for row in expired:
        guild = bot.get_guild(row["guild"])
        if guild:
            await refresh_public(guild, get_raffle(row["guild"]))


@check_deadlines.before_loop
async def wait_ready():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    initialize()
    bot.add_view(JoinView())
    if not check_deadlines.is_running():
        check_deadlines.start()
    await bot.tree.sync()
    print(f"FairDraw conectado como {bot.user}")


@bot.tree.command(name="painel-inscricao", description="Cria uma inscrição usando um formulário")
@app_commands.describe(canal="Canal onde o painel será publicado")
async def painel_inscricao(interaction: discord.Interaction, canal: discord.TextChannel):
    if not authorized(interaction):
        return await interaction.response.send_message("Você não está na lista de donos autorizados.", ephemeral=True)
    await interaction.response.send_modal(SetupModal(canal))


@bot.tree.command(name="painel-dono", description="Abre o painel privado de controle")
async def painel_dono(interaction: discord.Interaction):
    if not authorized(interaction):
        return await interaction.response.send_message("Você não está na lista de donos autorizados.", ephemeral=True)
    row = get_raffle(interaction.guild_id)
    if not row:
        return await interaction.response.send_message("Use /painel-inscricao para criar uma inscrição primeiro.", ephemeral=True)
    await interaction.response.send_message(embed=make_embed(row), view=OwnerView(interaction.guild_id), ephemeral=True)


@bot.tree.command(name="enviar-na-dm", description="Envia uma mensagem privada a uma pessoa")
async def enviar_na_dm(interaction: discord.Interaction, pessoa: discord.Member):
    if not authorized(interaction):
        return await interaction.response.send_message("Você não está na lista de donos autorizados.", ephemeral=True)
    await interaction.response.send_modal(DmModal(pessoa))


if not TOKEN:
    raise SystemExit("Configure a variável DISCORD_TOKEN antes de iniciar o bot.")
bot.run(TOKEN)
