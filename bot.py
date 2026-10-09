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
INVITE = os.getenv("FUSION_INVITE", "https://discord.gg/nSvgAgQze5")
DB = os.getenv("DATABASE_PATH", "inscricoes.sqlite3")
bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS raffle(guild INTEGER PRIMARY KEY, channel INTEGER, message INTEGER, title TEXT, description TEXT, image TEXT, deadline TEXT, capacity INTEGER, status TEXT, winners TEXT);
        CREATE TABLE IF NOT EXISTS entrants(guild INTEGER, user INTEGER, joined TEXT, PRIMARY KEY(guild,user));
        """)


def raffle(guild):
    with conn() as c:
        return c.execute("SELECT * FROM raffle WHERE guild=?", (guild,)).fetchone()


def entrants(guild):
    with conn() as c:
        return c.execute("SELECT user FROM entrants WHERE guild=? ORDER BY joined", (guild,)).fetchall()


def is_owner(i):
    return i.guild is not None and i.user.id == i.guild.owner_id


def embed_for(row):
    count = len(entrants(row["guild"]))
    e = discord.Embed(title=row["title"], description=row["description"], color=0x5865F2)
    e.add_field(name="Participantes", value=f"{count}/{row['capacity']}")
    e.add_field(name="Prazo", value=discord.utils.format_dt(datetime.fromisoformat(row["deadline"]), "f"))
    e.add_field(name="Status", value="Aberta" if row["status"] == "open" else "Encerrada")
    if row["image"]:
        e.set_image(url=row["image"])
    return e


class JoinConfirm(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=180)
        self.guild = guild

    @discord.ui.button(label="Já entrei — confirmar", style=discord.ButtonStyle.primary)
    async def confirm(self, i, button):
        r = raffle(self.guild)
        if not r or r["status"] != "open" or datetime.now(timezone.utc) >= datetime.fromisoformat(r["deadline"]):
            return await i.response.send_message("As inscrições estão encerradas.", ephemeral=True)
        with conn() as c:
            if c.execute("SELECT 1 FROM entrants WHERE guild=? AND user=?", (self.guild, i.user.id)).fetchone():
                return await i.response.send_message("Você já está inscrito.", ephemeral=True)
            count = c.execute("SELECT COUNT(*) FROM entrants WHERE guild=?", (self.guild,)).fetchone()[0]
            if count >= r["capacity"]:
                return await i.response.send_message("As vagas já foram preenchidas.", ephemeral=True)
            c.execute("INSERT INTO entrants VALUES(?,?,?)", (self.guild, i.user.id, datetime.now(timezone.utc).isoformat()))
        await i.response.send_message("Inscrição confirmada! Você está participando.", ephemeral=True)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, i, button):
        await i.response.send_message("Tudo bem, sua inscrição não foi registrada.", ephemeral=True)


class JoinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Participar", style=discord.ButtonStyle.success, custom_id="fairdraw:join")
    async def join(self, i, button):
        r = raffle(i.guild_id)
        if not r or r["status"] != "open" or datetime.now(timezone.utc) >= datetime.fromisoformat(r["deadline"]):
            return await i.response.send_message("As inscrições estão fechadas.", ephemeral=True)
        with conn() as c:
            if c.execute("SELECT 1 FROM entrants WHERE guild=? AND user=?", (i.guild_id, i.user.id)).fetchone():
                return await i.response.send_message("Você já está inscrito.", ephemeral=True)
            count = c.execute("SELECT COUNT(*) FROM entrants WHERE guild=?", (i.guild_id,)).fetchone()[0]
        if count >= r["capacity"]:
            return await i.response.send_message("As vagas já foram preenchidas.", ephemeral=True)
        await i.response.send_message(f"Você já entrou na org? Entre pelo convite {INVITE} e confirme abaixo.", view=JoinConfirm(i.guild_id), ephemeral=True)


class RemoveModal(discord.ui.Modal, title="Remover participante"):
    user_id = discord.ui.TextInput(label="ID da pessoa", placeholder="Cole o ID ou @menção", max_length=25)

    def __init__(self, guild):
        super().__init__()
        self.guild = guild

    async def on_submit(self, i):
        try:
            uid = int(self.user_id.value.strip().replace("<@", "").replace(">", "").replace("!", ""))
        except ValueError:
            return await i.response.send_message("ID inválido.", ephemeral=True)
        with conn() as c:
            cur = c.execute("DELETE FROM entrants WHERE guild=? AND user=?", (self.guild, uid))
        await i.response.send_message("Participante removido." if cur.rowcount else "Pessoa não encontrada na lista.", ephemeral=True)


class DrawModal(discord.ui.Modal, title="Sortear vencedores"):
    count = discord.ui.TextInput(label="Número de vencedores", placeholder="Ex.: 5", max_length=4)

    def __init__(self, guild):
        super().__init__()
        self.guild = guild

    async def on_submit(self, i):
        people = [x["user"] for x in entrants(self.guild)]
        try:
            n = int(self.count.value)
        except ValueError:
            return await i.response.send_message("Digite um número inteiro.", ephemeral=True)
        if n < 1 or n > len(people):
            return await i.response.send_message(f"Escolha de 1 a {len(people)}.", ephemeral=True)
        winners = random.SystemRandom().sample(people, n)
        with conn() as c:
            c.execute("UPDATE raffle SET status='closed',winners=? WHERE guild=?", (",".join(map(str, winners)), self.guild))
        await sync_public(i.guild, raffle(self.guild))
        await i.response.send_message("Prévia privada (ainda não publicada):\n" + ", ".join(f"<@{x}>" for x in winners) + "\nRevise e clique em **Confirmar resultado**.", ephemeral=True)


class DmModal(discord.ui.Modal, title="Enviar mensagem privada"):
    message = discord.ui.TextInput(label="Mensagem", style=discord.TextStyle.paragraph, max_length=1800)

    def __init__(self, member):
        super().__init__()
        self.member = member

    async def on_submit(self, i):
        try:
            await self.member.send(self.message.value)
            await i.response.send_message(f"Mensagem enviada para {self.member.mention}.", ephemeral=True)
        except discord.Forbidden:
            await i.response.send_message("Não consegui enviar. A pessoa pode estar com as DMs fechadas.", ephemeral=True)


class OwnerView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=900)
        self.guild = guild

    async def interaction_check(self, i):
        if not is_owner(i):
            await i.response.send_message("Somente o dono do servidor pode usar este painel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Ver participantes", style=discord.ButtonStyle.secondary)
    async def see(self, i, button):
        people = entrants(self.guild)
        content = "\n".join(f"<@{p['user']}> — `{p['user']}`" for p in people) or "Ainda não há participantes."
        await i.response.send_message(content[:1900], ephemeral=True)

    @discord.ui.button(label="Fechar", style=discord.ButtonStyle.danger)
    async def close(self, i, button):
        with conn() as c:
            c.execute("UPDATE raffle SET status='closed' WHERE guild=?", (self.guild,))
        await sync_public(i.guild, raffle(self.guild))
        await i.response.send_message("Inscrições fechadas.", ephemeral=True)

    @discord.ui.button(label="Reabrir", style=discord.ButtonStyle.success)
    async def reopen(self, i, button):
        r = raffle(self.guild)
        if datetime.now(timezone.utc) >= datetime.fromisoformat(r["deadline"]):
            return await i.response.send_message("O prazo já terminou.", ephemeral=True)
        with conn() as c:
            c.execute("UPDATE raffle SET status='open',winners=NULL WHERE guild=?", (self.guild,))
        await sync_public(i.guild, raffle(self.guild))
        await i.response.send_message("Inscrições reabertas.", ephemeral=True)

    @discord.ui.button(label="Remover pessoa", style=discord.ButtonStyle.secondary)
    async def remove(self, i, button):
        await i.response.send_modal(RemoveModal(self.guild))

    @discord.ui.button(label="Sortear", style=discord.ButtonStyle.primary)
    async def draw(self, i, button):
        if not entrants(self.guild):
            return await i.response.send_message("Não há participantes.", ephemeral=True)
        await i.response.send_modal(DrawModal(self.guild))

    @discord.ui.button(label="Confirmar resultado", style=discord.ButtonStyle.success)
    async def publish(self, i, button):
        r = raffle(self.guild)
        if not r or not r["winners"]:
            return await i.response.send_message("Faça um sorteio primeiro.", ephemeral=True)
        if r["status"] == "announced":
            return await i.response.send_message("Este resultado já foi publicado.", ephemeral=True)
        channel = i.guild.get_channel(r["channel"])
        if channel is None:
            return await i.response.send_message("Não encontrei o canal da inscrição.", ephemeral=True)
        winners = [int(x) for x in r["winners"].split(",")]
        await channel.send(f"🎉 Resultado confirmado de **{r['title']}**! Vencedores: " + ", ".join(f"<@{x}>" for x in winners))
        with conn() as c:
            c.execute("UPDATE raffle SET status='announced' WHERE guild=?", (self.guild,))
        ok, failed = 0, []
        for uid in winners:
            try:
                member = i.guild.get_member(uid) or await i.guild.fetch_member(uid)
                await member.send(f"Parabéns! Você ganhou **{r['title']}**. Entre em contato com a organização para combinar os próximos passos.")
                ok += 1
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                failed.append(str(uid))
        await i.response.send_message(f"Publicado. DMs entregues: {ok}. Falha para IDs: {', '.join(failed) if failed else 'nenhum'}.", ephemeral=True)


async def sync_public(guild, r):
    if not r or not r["message"]:
        return
    ch = guild.get_channel(r["channel"])
    if not ch:
        return
    try:
        msg = await ch.fetch_message(r["message"])
        await msg.edit(embed=embed_for(r), view=JoinView() if r["status"] == "open" else None)
    except discord.HTTPException:
        pass


@tasks.loop(seconds=30)
async def deadline_check():
    with conn() as c:
        rows = c.execute("SELECT * FROM raffle WHERE status='open'").fetchall()
        expired = [r for r in rows if datetime.now(timezone.utc) >= datetime.fromisoformat(r["deadline"])]
        for r in expired:
            c.execute("UPDATE raffle SET status='closed' WHERE guild=?", (r["guild"],))
    for r in expired:
        g = bot.get_guild(r["guild"])
        if g:
            await sync_public(g, raffle(r["guild"]))


@deadline_check.before_loop
async def before_deadline_check():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    init_db()
    bot.add_view(JoinView())
    if not deadline_check.is_running():
        deadline_check.start()
    await bot.tree.sync()
    print(f"Conectado como {bot.user}")


class SetupModal(discord.ui.Modal, title="Configurar inscrição"):
    title_field = discord.ui.TextInput(label="Título", max_length=256, placeholder="Ex.: Inscrição para o sorteio")
    description_field = discord.ui.TextInput(label="Descrição", style=discord.TextStyle.paragraph, max_length=1000, placeholder="Regras e informações")
    hours_field = discord.ui.TextInput(label="Prazo em horas (1 a 720)", max_length=3, placeholder="24")
    capacity_field = discord.ui.TextInput(label="Limite de participantes (1 a 10000)", max_length=5, placeholder="100")
    image_field = discord.ui.TextInput(label="URL da imagem (opcional)", required=False, max_length=1000, placeholder="https://...")

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, i):
        try:
            hours, capacity = int(self.hours_field.value), int(self.capacity_field.value)
        except ValueError:
            return await i.response.send_message("Prazo e limite precisam ser números inteiros.", ephemeral=True)
        if not 1 <= hours <= 720 or not 1 <= capacity <= 10000:
            return await i.response.send_message("Prazo: 1–720 horas. Limite: 1–10000 pessoas.", ephemeral=True)
        image = self.image_field.value.strip()
        if image and not image.startswith(("https://", "http://")):
            return await i.response.send_message("O link da imagem deve começar com http:// ou https://.", ephemeral=True)
        deadline = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        with conn() as c:
            c.execute("DELETE FROM entrants WHERE guild=?", (i.guild_id,))
            c.execute("INSERT OR REPLACE INTO raffle VALUES(?,?,?,?,?,?,?,?,?,NULL)", (i.guild_id, self.channel.id, None, self.title_field.value.strip(), self.description_field.value.strip(), image or None, deadline, capacity, "open"))
        r = raffle(i.guild_id)
        try:
            msg = await self.channel.send(embed=embed_for(r), view=JoinView())
        except discord.Forbidden:
            return await i.response.send_message("Não tenho permissão para enviar mensagens nesse canal.", ephemeral=True)
        with conn() as c:
            c.execute("UPDATE raffle SET message=? WHERE guild=?", (msg.id, i.guild_id))
        await i.response.send_message(f"Painel publicado em {self.channel.mention}.", ephemeral=True)


@bot.tree.command(name="painel-inscricao", description="Abre o formulário para criar uma inscrição")
@app_commands.describe(canal="Canal onde o painel será publicado")
async def painel_inscricao(i: discord.Interaction, canal: discord.TextChannel):
    if not is_owner(i):
        return await i.response.send_message("Somente o dono do servidor pode criar inscrições.", ephemeral=True)
    await i.response.send_modal(SetupModal(canal))


@bot.tree.command(name="painel-dono", description="Abre o painel privado de controle")
async def painel_dono(i: discord.Interaction):
    if not is_owner(i):
        return await i.response.send_message("Somente o dono do servidor pode abrir o painel.", ephemeral=True)
    r = raffle(i.guild_id)
    if not r:
        return await i.response.send_message("Use /painel-inscricao para criar uma inscrição primeiro.", ephemeral=True)
    await i.response.send_message(embed=embed_for(r), view=OwnerView(i.guild_id), ephemeral=True)


@bot.tree.command(name="enviar-na-dm", description="Envia uma mensagem privada para uma pessoa")
async def enviar_na_dm(i: discord.Interaction, pessoa: discord.Member):
    if not is_owner(i):
        return await i.response.send_message("Somente o dono do servidor pode usar este comando.", ephemeral=True)
    await i.response.send_modal(DmModal(pessoa))


if not TOKEN:
    raise SystemExit("Configure DISCORD_TOKEN nas variáveis de ambiente antes de iniciar.")
bot.run(TOKEN)
