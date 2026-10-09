import asyncio
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
FUSION_INVITE = os.getenv("FUSION_INVITE", "https://discord.gg/nSvgAgQze5").strip()
DB_PATH = os.getenv("DATABASE_PATH", "inscricoes.sqlite3")
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS raffles (
            guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,
            message_id INTEGER, title TEXT NOT NULL, description TEXT NOT NULL,
            image_url TEXT, deadline TEXT NOT NULL, capacity INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'open', winners TEXT
        );
        CREATE TABLE IF NOT EXISTS participants (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            joined_at TEXT NOT NULL, PRIMARY KEY (guild_id, user_id)
        );
        """)


def get_raffle(guild_id):
    with connect() as db:
        return db.execute("SELECT * FROM raffles WHERE guild_id=?", (guild_id,)).fetchone()


def get_people(guild_id):
    with connect() as db:
        return db.execute("SELECT user_id FROM participants WHERE guild_id=? ORDER BY joined_at", (guild_id,)).fetchall()


def owner_only(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and interaction.user.id == interaction.guild.owner_id


def raffle_embed(row):
    count = len(get_people(row["guild_id"]))
    deadline = datetime.fromisoformat(row["deadline"]).astimezone(timezone.utc)
    embed = discord.Embed(title=row["title"], description=row["description"], color=0x5865F2)
    embed.add_field(name="Inscritos", value=f"{count}/{row['capacity']}", inline=True)
    embed.add_field(name="Prazo", value=discord.utils.format_dt(deadline, "f"), inline=True)
    embed.add_field(name="Status", value="Inscrições abertas" if row["status"] == "open" else "Inscrições fechadas", inline=False)
    if row["image_url"]:
        embed.set_image(url=row["image_url"])
    return embed


class JoinView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="Participar", style=discord.ButtonStyle.success, custom_id="raffle:join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = get_raffle(interaction.guild_id)
        if not row or row["status"] != "open":
            return await interaction.response.send_message("As inscrições estão fechadas.", ephemeral=True)
        if datetime.now(timezone.utc) >= datetime.fromisoformat(row["deadline"]):
            return await interaction.response.send_message("O prazo de inscrição terminou.", ephemeral=True)
        with connect() as db:
            exists = db.execute("SELECT 1 FROM participants WHERE guild_id=? AND user_id=?", (interaction.guild_id, interaction.user.id)).fetchone()
            count = db.execute("SELECT COUNT(*) FROM participants WHERE guild_id=?", (interaction.guild_id,)).fetchone()[0]
            if exists:
                return await interaction.response.send_message("Você já está inscrito.", ephemeral=True)
            if count >= row["capacity"]:
                return await interaction.response.send_message("As vagas já foram preenchidas.", ephemeral=True)
            db.execute("INSERT INTO participants VALUES (?, ?, ?)", (interaction.guild_id, interaction.user.id, datetime.now(timezone.utc).isoformat()))
        await interaction.response.send_message(f"Confirme que já entrou na org Fusion: {FUSION_INVITE}\nAo continuar, sua inscrição será registrada.", ephemeral=True, view=FusionConfirmView(interaction.guild_id))


class FusionConfirmView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=180)
        self.guild_id = guild_id

    @discord.ui.button(label="Já entrei — confirmar inscrição", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = get_raffle(self.guild_id)
        if not row or row["status"] != "open" or datetime.now(timezone.utc) >= datetime.fromisoformat(row["deadline"]):
            return await interaction.response.send_message("As inscrições foram encerradas; não foi possível confirmar.", ephemeral=True)
        with connect() as db:
            exists = db.execute("SELECT 1 FROM participants WHERE guild_id=? AND user_id=?", (self.guild_id, interaction.user.id)).fetchone()
            count = db.execute("SELECT COUNT(*) FROM participants WHERE guild_id=?", (self.guild_id,)).fetchone()[0]
            if exists:
                return await interaction.response.send_message("Você já está inscrito.", ephemeral=True)
            if count >= row["capacity"]:
                return await interaction.response.send_message("As vagas foram preenchidas antes da sua confirmação.", ephemeral=True)
            db.execute("INSERT INTO participants VALUES (?, ?, ?)", (self.guild_id, interaction.user.id, datetime.now(timezone.utc).isoformat()))
        await interaction.response.send_message("Inscrição confirmada! Você está participando.", ephemeral=True)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Inscrição não realizada.", ephemeral=True)


class OwnerView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=900)
        self.guild_id = guild_id

    async def interaction_check(self, interaction):
        if not owner_only(interaction):
            await interaction.response.send_message("Só o dono deste servidor pode usar este painel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Ver participantes", style=discord.ButtonStyle.secondary)
    async def participants(self, interaction, button):
        people = get_people(self.guild_id)
        if not people:
            return await interaction.response.send_message("Ainda não há participantes.", ephemeral=True)
        lines = [f"<@{p['user_id']}> — `{p['user_id']}`" for p in people]
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

    @discord.ui.button(label="Fechar inscrições", style=discord.ButtonStyle.danger)
    async def close(self, interaction, button):
        with connect() as db:
            db.execute("UPDATE raffles SET status='closed' WHERE guild_id=?", (self.guild_id,))
        row = get_raffle(self.guild_id)
        await update_public_message(interaction.guild, row)
        await interaction.response.send_message("Inscrições fechadas.", ephemeral=True)

    @discord.ui.button(label="Reabrir inscrições", style=discord.ButtonStyle.success)
    async def reopen(self, interaction, button):
        row = get_raffle(self.guild_id)
        if not row or datetime.now(timezone.utc) >= datetime.fromisoformat(row["deadline"]):
            return await interaction.response.send_message("O prazo já passou; publique uma nova inscrição com outro prazo.", ephemeral=True)
        with connect() as db:
            db.execute("UPDATE raffles SET status='open' WHERE guild_id=?", (self.guild_id,))
        await update_public_message(interaction.guild, get_raffle(self.guild_id))
        await interaction.response.send_message("Inscrições reabertas.", ephemeral=True)

    @discord.ui.button(label="Remover participante", style=discord.ButtonStyle.secondary)
    async def remove(self, interaction, button):
        await interaction.response.send_modal(RemoveModal(self.guild_id))

    @discord.ui.button(label="Sortear", style=discord.ButtonStyle.primary)
    async def draw(self, interaction, button):
        if not get_people(self.guild_id):
            return await interaction.response.send_message("Não há participantes para sortear.", ephemeral=True)
        await interaction.response.send_modal(DrawModal(self.guild_id))

    @discord.ui.button(label="Confirmar resultado", style=discord.ButtonStyle.success)
    async def confirm_result(self, interaction, button):
        row = get_raffle(self.guild_id)
        if not row or not row["winners"]:
            return await interaction.response.send_message("Faça um sorteio antes de confirmar.", ephemeral=True)
        if row["status"] == "announced":
            return await interaction.response.send_message("Este resultado já foi confirmado e publicado.", ephemeral=True)
        ids = [int(x) for x in row["winners"].split(",") if x]
        channel = interaction.guild.get_channel(row["channel_id"])
        if channel is None:
            return await interaction.response.send_message("O canal da inscrição não existe mais; não consegui publicar o resultado.", ephemeral=True)
        names = ", ".join(f"<@{uid}>" for uid in ids)
        await channel.send(f"🎉 Resultado confirmado de **{row['title']}**! Vencedores: {names}")
        with connect() as db:
            db.execute("UPDATE raffles SET status='announced' WHERE guild_id=?", (self.guild_id,))
        sent, failed = 0, []
        for uid in ids:
            try:
                member = interaction.guild.get_member(uid) or await interaction.guild.fetch_member(uid)
                await member.send(f"Parabéns! Você ganhou **{row['title']}**. Entre em contato com a organização para combinar os próximos passos.")
                sent += 1
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                failed.append(str(uid))
        await interaction.response.send_message(f"Resultado publicado. DMs enviadas: {sent}. Não foi possível enviar para: {', '.join(failed) if failed else 'ninguém'}.", ephemeral=True)


class DrawModal(discord.ui.Modal, title="Sortear vencedores"):
    amount = discord.ui.TextInput(label="Quantas pessoas serão sorteadas?", placeholder="Ex.: 5", max_length=3)

    def __init__(self, guild_id):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction):
        people = [p["user_id"] for p in get_people(self.guild_id)]
        try:
            n = int(self.amount.value)
        except ValueError:
            return await interaction.response.send_message("Digite um número válido.", ephemeral=True)
        if n < 1 or n > len(people):
            return await interaction.response.send_message(f"Escolha de 1 a {len(people)} vencedores.", ephemeral=True)
        winners = random.SystemRandom().sample(people, n)
        with connect() as db:
            db.execute("UPDATE raffles SET status='closed', winners=? WHERE guild_id=?", (",".join(map(str, winners)), self.guild_id))
        await update_public_message(interaction.guild, get_raffle(self.guild_id))
        await interaction.response.send_message("Prévia do sorteio (ainda não anunciada):\n" + ", ".join(f"<@{uid}>" for uid in winners) + "\nRevise e use **Confirmar resultado** para anunciar.", ephemeral=True)


class RemoveModal(discord.ui.Modal, title="Remover participante"):
    user_id = discord.ui.TextInput(label="ID do usuário", placeholder="Ative o modo desenvolvedor para copiar o ID", max_length=25)

    def __init__(self, guild_id):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction):
        try:
            uid = int(self.user_id.value.strip().replace("<@", "").replace(">", "").replace("!", ""))
        except ValueError:
            return await interaction.response.send_message("ID inválido.", ephemeral=True)
        with connect() as db:
            cur = db.execute("DELETE FROM participants WHERE guild_id=? AND user_id=?", (self.guild_id, uid))
        await interaction.response.send_message("Participante removido." if cur.rowcount else "Esse usuário não está inscrito.", ephemeral=True)


class DmModal(discord.ui.Modal, title="Enviar mensagem privada"):
    message = discord.ui.TextInput(label="Mensagem", style=discord.TextStyle.paragraph, max_length=1800)

    def __init__(self, recipient: discord.User):
        super().__init__()
        self.recipient = recipient

    async def on_submit(self, interaction):
        try:
            await self.recipient.send(self.message.value)
            await interaction.response.send_message(f"Mensagem enviada para {self.recipient.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Não consegui enviar: a pessoa pode ter DMs fechadas para o bot.", ephemeral=True)


async def update_public_message(guild, row):
    if not row or not row["message_id"]:
        return
    channel = guild.get_channel(row["channel_id"])
    if not channel:
        return
    try:
        msg = await channel.fetch_message(row["message_id"])
        await msg.edit(embed=raffle_embed(row), view=JoinView(row["guild_id"]) if row["status"] == "open" else None)
    except discord.HTTPException:
        pass


@tasks.loop(seconds=30)
async def deadline_watcher():
    now = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        rows = db.execute("SELECT * FROM raffles WHERE status='open' AND deadline<=?", (now,)).fetchall()
        for row in rows:
            db.execute("UPDATE raffles SET status='closed' WHERE guild_id=?", (row["guild_id"],))
    for row in rows:
        guild = bot.get_guild(row["guild_id"])
        if guild:
            await update_public_message(guild, get_raffle(row["guild_id"]))


@deadline_watcher.before_loop
async def before_watcher():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    init_db()
    with connect() as db:
        open_raffles = db.execute("SELECT guild_id FROM raffles WHERE status='open'").fetchall()
    for row in open_raffles:
        bot.add_view(JoinView(row["guild_id"]))
    if not deadline_watcher.is_running():
        deadline_watcher.start()
    try:
        await bot.tree.sync()
    except discord.HTTPException:
        pass
    print(f"Conectado como {bot.user}")


@bot.tree.command(name="painel-inscricao", description="Publica o painel de inscrição no canal escolhido")
@app_commands.describe(canal="Canal público", titulo="Título do painel", descricao="Descrição", prazo="Prazo em horas", limite="Máximo de participantes", imagem="URL de imagem opcional")
async def painel_inscricao(interaction: discord.Interaction, canal: discord.TextChannel, titulo: str, descricao: str, prazo: app_commands.Range[int, 1, 720], limite: app_commands.Range[int, 1, 10000], imagem: str = ""):
    if not owner_only(interaction):
        return await interaction.response.send_message("Só o dono do servidor pode criar inscrições.", ephemeral=True)
    deadline = datetime.now(timezone.utc) + timedelta(hours=prazo)
    with connect() as db:
        db.execute("DELETE FROM participants WHERE guild_id=?", (interaction.guild_id,))
        db.execute("INSERT OR REPLACE INTO raffles (guild_id, channel_id, message_id, title, description, image_url, deadline, capacity, status, winners) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 'open', NULL)", (interaction.guild_id, canal.id, titulo[:256], descricao[:4000], imagem[:1000] or None, deadline.isoformat(), limite))
    row = get_raffle(interaction.guild_id)
    msg = await canal.send(embed=raffle_embed(row), view=JoinView(interaction.guild_id))
    with connect() as db:
        db.execute("UPDATE raffles SET message_id=? WHERE guild_id=?", (msg.id, interaction.guild_id))
    bot.add_view(JoinView(interaction.guild_id))
    await interaction.response.send_message(f"Painel publicado em {canal.mention}.", ephemeral=True)


@bot.tree.command(name="painel-dono", description="Abre o painel privado de controle")
async def painel_dono(interaction: discord.Interaction):
    if not owner_only(interaction):
        return await interaction.response.send_message("Só o dono deste servidor pode abrir o painel.", ephemeral=True)
    row = get_raffle(interaction.guild_id)
    if not row:
        return await interaction.response.send_message("Ainda não existe inscrição. Use /painel-inscricao primeiro.", ephemeral=True)
    await interaction.response.send_message(embed=raffle_embed(row), view=OwnerView(interaction.guild_id), ephemeral=True)


@bot.tree.command(name="enviar-na-dm", description="Envia uma mensagem privada para uma pessoa")
@app_commands.describe(pessoa="Quem receberá a mensagem")
async def enviar_na_dm(interaction: discord.Interaction, pessoa: discord.Member):
    if not owner_only(interaction):
        return await interaction.response.send_message("Só o dono deste servidor pode usar este comando.", ephemeral=True)
    await interaction.response.send_modal(DmModal(pessoa))


if not TOKEN:
    raise SystemExit("Configure DISCORD_TOKEN no arquivo .env antes de iniciar o bot.")
bot.run(TOKEN)
