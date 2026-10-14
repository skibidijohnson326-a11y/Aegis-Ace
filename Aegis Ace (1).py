import discord, json, os, time, random, secrets
from discord import app_commands
from discord.ext import commands, tasks
from datetime import timedelta, datetime, timezone
from collections import defaultdict, deque
from typing import Literal, Optional

TOKEN = "MTU1OTYxMzYwMjAxNTYwODg3Mg.Ga5yMu.k6yf-PfRLERlTds13ULWVqWzrLCnGrBwKIUF04"
DATA_FILE = "data.json"

class C:
    BRAND = 0x5865F2
    OK = 0x57F287
    WARN = 0xFEE75C
    ERR = 0xED4245
    ORANGE = 0xF0A030
    DARK = 0x2B2D31

FEATURES = {
    "antispam":    ("Anti-Spam",      "6 messages in 5 seconds: purge and 10 minute timeout"),
    "antilink":    ("Anti-Invite",    "deletes Discord invite links"),
    "antimention": ("Anti-Mass-Ping", "5 or more pings or @everyone: 30 minute timeout"),
    "antiraid":    ("Anti-Raid",      "8 joins in 10 seconds: new joins are kicked"),
    "antinuke":    ("Anti-Nuke",      "3 deletes or bans in 15 seconds: user is banned"),
}

def load():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {}

data = load()

def save():
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def gcfg(guild_id):
    g = data.setdefault(str(guild_id), {})
    g.setdefault("log", None)
    g.setdefault("warns", {})
    g.setdefault("min_age_days", 0)
    g.setdefault("staff", [])
    g.setdefault("apps", {"channel": None, "types": {}, "pending": {}})
    g.setdefault("cases", {})
    g.setdefault("case_count", 0)
    g.setdefault("giveaways", {})
    sec = g.setdefault("security", {})
    for k in FEATURES:
        sec.setdefault(k, True)
    return g

def is_staff(user, guild):
    if guild is None:
        return False
    if user.id == guild.owner_id:
        return True
    return str(user.id) in gcfg(guild.id)["staff"]

def is_auth():
    async def predicate(i: discord.Interaction):
        return is_staff(i.user, i.guild)
    return app_commands.check(predicate)

def is_owner():
    async def predicate(i: discord.Interaction):
        return i.guild is not None and i.user.id == i.guild.owner_id
    return app_commands.check(predicate)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
start_time = time.time()

spam_tracker = defaultdict(lambda: deque(maxlen=15))
join_tracker = defaultdict(lambda: deque(maxlen=50))
nuke_tracker = defaultdict(lambda: deque(maxlen=20))

def mk(title=None, desc=None, color=C.BRAND, *, thumb=None):
    e = discord.Embed(title=title, description=desc, color=color, timestamp=datetime.now(timezone.utc))
    if thumb:
        e.set_thumbnail(url=thumb)
    if bot.user:
        e.set_footer(text=bot.user.name, icon_url=bot.user.display_avatar.url)
    return e

def ok(text):
    return mk("Done", text, C.OK)

def fail(text):
    return mk("Error", text, C.ERR)

async def reply(i: discord.Interaction, embed, *, ephemeral=False, view=None):
    kw = {"embed": embed, "ephemeral": ephemeral}
    if view:
        kw["view"] = view
    if i.response.is_done():
        await i.followup.send(**kw)
    else:
        await i.response.send_message(**kw)

def case_embed(action, emoji, color, target, mod, reason, extra=None):
    e = mk(f"{emoji} {action}".strip(), color=color, thumb=target.display_avatar.url)
    e.add_field(name="User", value=f"{target.mention}\n`{target.id}`")
    e.add_field(name="Moderator", value=mod.mention)
    for k, v in (extra or {}).items():
        e.add_field(name=k, value=v)
    e.add_field(name="Reason", value=f"```{reason}```", inline=False)
    return e

def new_case(guild, action, target, mod, reason):
    g = gcfg(guild.id)
    g["case_count"] += 1
    n = g["case_count"]
    g["cases"][str(n)] = {
        "action": action,
        "target": target.id,
        "mod": mod.id,
        "reason": reason,
        "time": int(time.time()),
    }
    save()
    return n

async def log(guild, embed):
    cid = gcfg(guild.id)["log"]
    if not cid:
        return
    ch = guild.get_channel(cid)
    if ch:
        try:
            await ch.send(embed=embed)
        except discord.HTTPException:
            pass

async def notify(member, action, guild, reason, color):
    e = mk(f"You were {action} in {guild.name}", f"**Reason:** {reason}", color,
           thumb=guild.icon.url if guild.icon else None)
    try:
        await member.send(embed=e)
    except discord.HTTPException:
        pass

def hierarchy_problem(i: discord.Interaction, member: discord.Member):
    if member.id == i.user.id:
        return "you can't do that to yourself"
    if member.id == i.guild.owner_id:
        return "you can't target the server owner"
    if member.top_role >= i.guild.me.top_role:
        return f"{member.mention}'s role is higher than or equal to mine"
    return None

class OwnedView(discord.ui.View):
    def __init__(self, owner_id, timeout=120):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message = None

    async def interaction_check(self, i: discord.Interaction):
        if i.user.id != self.owner_id:
            await i.response.send_message(embed=fail("this menu isn't yours"), ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

class Confirm(OwnedView):
    def __init__(self, owner_id):
        super().__init__(owner_id, timeout=30)
        self.value = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def yes(self, i: discord.Interaction, _):
        self.value = True
        self.stop()
        await i.response.edit_message(embed=mk("Working on it", color=C.DARK), view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, i: discord.Interaction, _):
        self.value = False
        self.stop()
        await i.response.edit_message(embed=mk("Cancelled", "nothing was changed", C.DARK), view=None)

async def confirm(i: discord.Interaction, title, text):
    v = Confirm(i.user.id)
    await i.response.send_message(embed=mk(title, text, C.WARN), view=v, ephemeral=True)
    v.message = await i.original_response()
    await v.wait()
    if v.value is None:
        try:
            await i.edit_original_response(embed=mk("Timed out", "nothing was changed", C.DARK), view=None)
        except discord.HTTPException:
            pass
    return bool(v.value)

class Pager(OwnedView):
    def __init__(self, owner_id, pages):
        super().__init__(owner_id, timeout=180)
        self.pages = pages
        self.idx = 0
        self.sync()

    def sync(self):
        self.prev.disabled = self.idx == 0
        self.next.disabled = self.idx >= len(self.pages) - 1
        self.counter.label = f"{self.idx + 1}/{len(self.pages)}"

    async def show(self, i):
        self.sync()
        await i.response.edit_message(embed=self.pages[self.idx], view=self)

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev(self, i, _):
        self.idx -= 1
        await self.show(i)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def counter(self, i, _):
        pass

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, i, _):
        self.idx += 1
        await self.show(i)

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="ticket_claim")
    async def claim(self, i: discord.Interaction, button: discord.ui.Button):
        if not is_staff(i.user, i.guild):
            return await reply(i, fail("only staff can claim tickets"), ephemeral=True)
        topic = i.channel.topic or ""
        if topic.startswith("claimed:"):
            claimer = topic.split(":", 1)[1]
            return await reply(i, fail(f"this ticket is already claimed by <@{claimer}>"), ephemeral=True)

        await i.channel.edit(topic=f"claimed:{i.user.id}", reason=f"claimed by {i.user}")
        button.disabled = True
        button.label = f"Claimed by {i.user.display_name}"
        await i.response.edit_message(view=self)
        await i.channel.send(embed=mk("Ticket claimed", f"{i.user.mention} is handling this ticket", C.OK))
        await log(i.guild, mk("Ticket claimed", f"{i.user.mention} claimed `{i.channel.name}`", C.OK))

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close")
    async def close(self, i: discord.Interaction, button: discord.ui.Button):
        if not await confirm(i, "Close ticket", "this will delete this ticket channel"):
            return
        await log(i.guild, mk("Ticket closed", f"`{i.channel.name}` closed by {i.user.mention}", C.DARK))
        await i.channel.delete(reason=f"ticket closed by {i.user}")

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_open")
    async def open_ticket(self, i: discord.Interaction, _):
        guild = i.guild
        name = f"ticket-{i.user.name}".lower()[:90]
        existing = discord.utils.get(guild.text_channels, name=name)
        if existing:
            return await reply(i, fail(f"you already have a ticket: {existing.mention}"), ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            i.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        staff_ids = [guild.owner_id] + [int(x) for x in gcfg(guild.id)["staff"]]
        for uid in staff_ids:
            m = guild.get_member(uid)
            if m:
                overwrites[m] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        ch = await guild.create_text_channel(
            name, overwrites=overwrites, category=i.channel.category, reason=f"ticket opened by {i.user}")
        await ch.send(
            content=f"{i.user.mention}",
            embed=mk("Ticket opened", f"{i.user.mention}, staff will be with you shortly. describe your issue below.", C.BRAND),
            view=TicketCloseView())
        await reply(i, ok(f"your ticket is ready: {ch.mention}"), ephemeral=True)
        await log(guild, mk("Ticket opened", f"{i.user.mention} opened {ch.mention}", C.OK))

@bot.tree.command(name="ticketpanel", description="post the ticket panel in this channel")
@is_auth()
async def ticketpanel(i: discord.Interaction):
    e = mk("Support", "need help? open a private ticket with staff below", C.BRAND)
    await i.channel.send(embed=e, view=TicketView())
    await reply(i, ok("ticket panel posted"), ephemeral=True)

def _apps(guild_id):
    return gcfg(guild_id)["apps"]

def _app_info(i: discord.Interaction):
    ft = i.message.embeds[0].footer.text or ""
    parts = dict(p.split("=", 1) for p in ft.split("|") if "=" in p)
    return int(parts["uid"]), parts["type"]

def _type_name(a, key):
    t = a["types"].get(key)
    return t["name"] if t else "application"

async def app_autocomplete(i: discord.Interaction, current: str):
    types = _apps(i.guild.id)["types"]
    return [app_commands.Choice(name=t["name"][:100], value=k)
            for k, t in types.items() if current.lower() in t["name"].lower()][:25]

class DenyModal(discord.ui.Modal, title="Deny application"):
    reason = discord.ui.TextInput(label="Reason (optional)", style=discord.TextStyle.paragraph,
                                  required=False, max_length=500)

    def __init__(self, uid, key, msg):
        super().__init__()
        self.uid = uid
        self.key = key
        self.msg = msg

    async def on_submit(self, i: discord.Interaction):
        a = _apps(i.guild.id)
        a["pending"].pop(str(self.uid), None)
        save()
        reason = self.reason.value or "no reason given"
        name = _type_name(a, self.key)
        member = i.guild.get_member(self.uid)
        if member:
            try:
                await member.send(embed=mk(
                    "Application update",
                    f"thanks for applying for **{name}** in {i.guild.name}. your application was not accepted this time.\n**Note:** {reason}",
                    C.DARK))
            except discord.HTTPException:
                pass
        e = self.msg.embeds[0]
        e.color = C.ERR
        e.add_field(name="Decision", value=f"Denied by {i.user.mention}\n{reason}", inline=False)
        try:
            await self.msg.edit(embed=e, view=None)
        except discord.HTTPException:
            pass
        await reply(i, ok("application denied and the applicant was notified"), ephemeral=True)

class ReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="app_accept")
    async def accept(self, i: discord.Interaction, _):
        if not is_staff(i.user, i.guild):
            return await reply(i, fail("only staff can review applications"), ephemeral=True)
        uid, key = _app_info(i)
        a = _apps(i.guild.id)
        member = i.guild.get_member(uid)
        if member is None:
            return await reply(i, fail("that person isn't in the server anymore"), ephemeral=True)

        t = a["types"].get(key)
        role = i.guild.get_role(t["role"]) if t and t.get("role") else None
        if role:
            if role >= i.guild.me.top_role:
                return await reply(i, fail(f"the {role.mention} role is higher than mine"), ephemeral=True)
            try:
                await member.add_roles(role, reason=f"accepted application: {_type_name(a, key)}")
            except discord.Forbidden:
                return await reply(i, fail("i don't have permission to give that role"), ephemeral=True)

        a["pending"].pop(str(uid), None)
        save()
        name = _type_name(a, key)
        try:
            await member.send(embed=mk(
                "Application accepted",
                f"your **{name}** application in {i.guild.name} was accepted.",
                C.OK))
        except discord.HTTPException:
            pass

        e = i.message.embeds[0]
        e.color = C.OK
        e.add_field(name="Decision", value=f"Accepted by {i.user.mention}", inline=False)
        await i.response.edit_message(embed=e, view=None)
        await log(i.guild, mk("Application accepted", f"{member.mention} accepted for **{name}** by {i.user.mention}", C.OK))

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="app_deny")
    async def deny(self, i: discord.Interaction, _):
        if not is_staff(i.user, i.guild):
            return await reply(i, fail("only staff can review applications"), ephemeral=True)
        uid, key = _app_info(i)
        await i.response.send_modal(DenyModal(uid, key, i.message))

class ApplyModal(discord.ui.Modal):
    def __init__(self, key, t):
        super().__init__(title=t["name"][:45])
        self.key = key
        self.questions = t["questions"]
        self.inputs = []
        for q in self.questions:
            ti = discord.ui.TextInput(
                label=q[:45],
                placeholder="your answer",
                style=discord.TextStyle.paragraph,
                max_length=600,
                required=True,
            )
            self.add_item(ti)
            self.inputs.append(ti)

    async def on_submit(self, i: discord.Interaction):
        a = _apps(i.guild.id)
        chan = i.guild.get_channel(a["channel"]) if a["channel"] else None
        if chan is None:
            return await reply(i, fail("applications aren't set up yet"), ephemeral=True)
        if str(i.user.id) in a["pending"]:
            return await reply(i, fail("you already have an application waiting for review"), ephemeral=True)
        if self.key not in a["types"]:
            return await reply(i, fail("that application was removed"), ephemeral=True)

        t = a["types"][self.key]
        e = discord.Embed(title=f"{t['name']} application", color=C.BRAND, timestamp=datetime.now(timezone.utc))
        e.set_author(name=str(i.user), icon_url=i.user.display_avatar.url)
        e.add_field(name="Applicant", value=f"{i.user.mention}\n`{i.user.id}`", inline=False)
        for q, ti in zip(self.questions, self.inputs):
            e.add_field(name=q[:256], value=(ti.value or "no answer")[:1024], inline=False)
        e.set_footer(text=f"uid={i.user.id}|type={self.key}")

        await chan.send(embed=e, view=ReviewView())
        a["pending"][str(i.user.id)] = self.key
        save()
        await reply(i, ok(f"your **{t['name']}** application was sent to staff"), ephemeral=True)

class AppPickView(discord.ui.View):
    def __init__(self, types):
        super().__init__(timeout=120)
        self.types = types
        opts = [discord.SelectOption(label=t["name"][:100], value=k,
                                     description=(t.get("desc") or "")[:100] or None)
                for k, t in list(types.items())[:25]]
        sel = discord.ui.Select(placeholder="choose a position", options=opts)
        sel.callback = self.picked
        self.add_item(sel)

    async def picked(self, i: discord.Interaction):
        key = i.data["values"][0]
        await i.response.send_modal(ApplyModal(key, self.types[key]))

class ApplyPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Apply", style=discord.ButtonStyle.success, custom_id="app_open")
    async def open_apps(self, i: discord.Interaction, _):
        a = _apps(i.guild.id)
        if not a["types"]:
            return await reply(i, fail("no applications are open right now"), ephemeral=True)
        if str(i.user.id) in a["pending"]:
            return await reply(i, fail("you already have an application waiting for review"), ephemeral=True)
        await reply(i, mk("Choose a position", None, C.BRAND),
                    ephemeral=True, view=AppPickView(a["types"]))

class NewAppModal(discord.ui.Modal):
    desc = discord.ui.TextInput(label="Short description", required=False, max_length=100,
                                placeholder="what this position does")
    questions = discord.ui.TextInput(label="Questions (one per line, max 5)", style=discord.TextStyle.paragraph,
                                     max_length=1000, placeholder="Why do you want this role?\nHow active are you?")

    def __init__(self, name, role_id):
        super().__init__(title=name[:45])
        self.name = name
        self.role_id = role_id

    async def on_submit(self, i: discord.Interaction):
        qs = [q.strip() for q in self.questions.value.splitlines() if q.strip()]
        if not qs:
            return await reply(i, fail("add at least one question, one per line"), ephemeral=True)
        if len(qs) > 5:
            return await reply(i, fail(f"that's {len(qs)} questions. the maximum is 5"), ephemeral=True)
        a = _apps(i.guild.id)
        key = secrets.token_hex(3)
        a["types"][key] = {
            "name": self.name,
            "desc": (self.desc.value or "").strip(),
            "role": self.role_id,
            "questions": qs,
        }
        save()
        role_txt = f"<@&{self.role_id}>" if self.role_id else "no role"
        await reply(i, ok(f"created **{self.name}**. accepted applicants get {role_txt}."), ephemeral=True)

@bot.tree.command(name="appnew", description="create a new application type")
@is_auth()
@app_commands.describe(name="name of the position, e.g. Trial Moderator",
                       role="role given when accepted (optional)")
async def appnew(i: discord.Interaction, name: app_commands.Range[str, 1, 45], role: Optional[discord.Role] = None):
    if role and role >= i.guild.me.top_role:
        return await reply(i, fail("that role is higher than mine"), ephemeral=True)
    await i.response.send_modal(NewAppModal(name, role.id if role else None))

@bot.tree.command(name="appdelete", description="delete an application type")
@is_auth()
@app_commands.autocomplete(app=app_autocomplete)
@app_commands.describe(app="which application to delete")
async def appdelete(i: discord.Interaction, app: str):
    a = _apps(i.guild.id)
    t = a["types"].pop(app, None)
    if not t:
        return await reply(i, fail("couldn't find that application"), ephemeral=True)
    save()
    await reply(i, ok(f"deleted **{t['name']}**"))

@bot.tree.command(name="approle", description="change the role given when an application is accepted")
@is_auth()
@app_commands.autocomplete(app=app_autocomplete)
@app_commands.describe(app="which application", role="role to give (leave empty to give none)")
async def approle(i: discord.Interaction, app: str, role: Optional[discord.Role] = None):
    a = _apps(i.guild.id)
    t = a["types"].get(app)
    if not t:
        return await reply(i, fail("couldn't find that application"), ephemeral=True)
    if role and role >= i.guild.me.top_role:
        return await reply(i, fail("that role is higher than mine"), ephemeral=True)
    t["role"] = role.id if role else None
    save()
    text = f"accepted **{t['name']}** applicants will get {role.mention}" if role else f"**{t['name']}** acceptances won't give a role"
    await reply(i, ok(text))

@bot.tree.command(name="applypanel", description="post the application panel here")
@is_auth()
async def applypanel(i: discord.Interaction):
    a = _apps(i.guild.id)
    e = mk("Applications", "pick a position below to apply", C.BRAND)
    if a["types"]:
        for t in list(a["types"].values())[:25]:
            gives = f"<@&{t['role']}>" if t.get("role") else "no role"
            desc = t.get("desc") or "no description"
            e.add_field(name=t["name"], value=f"{desc}\n**Gives:** {gives}", inline=False)
    else:
        e.add_field(name="No applications open", value="no positions yet", inline=False)
    await i.channel.send(embed=e, view=ApplyPanelView())
    await reply(i, ok("application panel posted"), ephemeral=True)

@bot.tree.command(name="appchannel", description="set the channel where applications get sent for review")
@is_auth()
async def appchannel(i: discord.Interaction, channel: discord.TextChannel):
    _apps(i.guild.id)["channel"] = channel.id
    save()
    await reply(i, ok(f"applications will be sent to {channel.mention}"))

@bot.tree.command(name="appstatus", description="see application settings")
@is_auth()
async def appstatus(i: discord.Interaction):
    a = _apps(i.guild.id)
    e = mk("Application settings", color=C.BRAND)
    cid = a["channel"]
    e.add_field(name="Review channel", value=f"<#{cid}>" if cid else "not set", inline=False)
    if not a["types"]:
        e.add_field(name="Positions", value="none yet", inline=False)
    for key, t in list(a["types"].items())[:25]:
        gives = f"<@&{t['role']}>" if t.get("role") else "no role"
        waiting = sum(1 for v in a["pending"].values() if v == key)
        e.add_field(name=t["name"],
                    value=f"gives: {gives}\nquestions: **{len(t['questions'])}**\nwaiting: **{waiting}**",
                    inline=False)
    await reply(i, e)

@bot.tree.command(name="appclear", description="clear a member's waiting application so they can apply again")
@is_auth()
@app_commands.describe(member="the member whose waiting application to clear")
async def appclear(i: discord.Interaction, member: discord.Member):
    a = _apps(i.guild.id)
    if a["pending"].pop(str(member.id), None) is None:
        return await reply(i, fail(f"{member.mention} has no waiting application"), ephemeral=True)
    save()
    await reply(i, ok(f"{member.mention} can apply again"), ephemeral=True)

def gw_embed(gw, ended=False):
    if ended:
        e = mk(f"Giveaway: {gw['prize']}", "this giveaway has ended", C.DARK)
    else:
        e = mk(f"Giveaway: {gw['prize']}",
               f"press **Enter** to join\nwinners: **{gw['winners']}**\nends <t:{gw['end']}:R>", C.BRAND)
    e.add_field(name="Entries", value=str(len(gw["entries"])))
    e.add_field(name="Hosted by", value=f"<@{gw['host']}>")
    return e

class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Enter", style=discord.ButtonStyle.success, custom_id="gw_enter")
    async def enter(self, i: discord.Interaction, _):
        gws = gcfg(i.guild.id)["giveaways"]
        gw = gws.get(str(i.message.id))
        if not gw or gw["ended"]:
            return await reply(i, fail("this giveaway has ended"), ephemeral=True)
        uid = str(i.user.id)
        if uid in gw["entries"]:
            gw["entries"].remove(uid)
            msg = "you left the giveaway"
        else:
            gw["entries"].append(uid)
            msg = "you're entered"
        save()
        await i.response.edit_message(embed=gw_embed(gw))
        await i.followup.send(embed=ok(msg), ephemeral=True)

async def end_giveaway(guild, mid, gw):
    gw["ended"] = True
    pool = list(gw["entries"])
    winners = random.sample(pool, min(gw["winners"], len(pool))) if pool else []
    gw["winners_list"] = winners
    save()

    channel = guild.get_channel(gw["channel"])
    if channel is None:
        return
    try:
        msg = await channel.fetch_message(int(mid))
        await msg.edit(embed=gw_embed(gw, ended=True), view=None)
    except discord.HTTPException:
        pass
    if winners:
        mentions = " ".join(f"<@{w}>" for w in winners)
        await channel.send(embed=mk("Giveaway winners", f"congratulations {mentions}, you won **{gw['prize']}**", C.OK))
    else:
        await channel.send(embed=mk("Giveaway ended", f"no entries for **{gw['prize']}**", C.DARK))

@tasks.loop(seconds=15)
async def check_giveaways():
    now = int(time.time())
    for guild in bot.guilds:
        gws = gcfg(guild.id)["giveaways"]
        for mid, gw in list(gws.items()):
            if gw["ended"] or gw["end"] > now:
                continue
            try:
                await end_giveaway(guild, mid, gw)
            except Exception as err:
                print(f"giveaway {mid} failed: {err}")

@bot.tree.command(name="giveaway", description="start a giveaway in this channel")
@is_auth()
@app_commands.describe(minutes="how long it runs (1 to 43200)", prize="what you're giving away",
                       winners="how many winners (1 to 10)")
async def giveaway(i: discord.Interaction, minutes: app_commands.Range[int, 1, 43200],
                   prize: app_commands.Range[str, 1, 200], winners: app_commands.Range[int, 1, 10] = 1):
    await i.response.defer(ephemeral=True)
    gw = {
        "channel": i.channel.id,
        "prize": prize,
        "winners": winners,
        "end": int(time.time()) + minutes * 60,
        "host": i.user.id,
        "entries": [],
        "ended": False,
        "winners_list": [],
    }
    msg = await i.channel.send(embed=gw_embed(gw), view=GiveawayView())
    gcfg(i.guild.id)["giveaways"][str(msg.id)] = gw
    save()
    await reply(i, ok(f"giveaway for **{prize}** started, ends in {minutes} minutes"), ephemeral=True)

@bot.tree.command(name="greroll", description="pick a new winner for an ended giveaway")
@is_auth()
@app_commands.describe(message_id="the giveaway message id")
async def greroll(i: discord.Interaction, message_id: str):
    gw = gcfg(i.guild.id)["giveaways"].get(message_id)
    if not gw or not gw["ended"]:
        return await reply(i, fail("that giveaway isn't found or hasn't ended yet"), ephemeral=True)
    pool = [x for x in gw["entries"] if x not in gw["winners_list"]] or list(gw["entries"])
    if not pool:
        return await reply(i, fail("nobody entered that giveaway"), ephemeral=True)
    w = random.choice(pool)
    gw["winners_list"] = [w]
    save()
    channel = i.guild.get_channel(gw["channel"])
    if channel:
        await channel.send(embed=mk("New winner", f"congratulations <@{w}>, you won **{gw['prize']}**", C.OK))
    await reply(i, ok("new winner picked"), ephemeral=True)

@bot.tree.command(name="authorize", description="give a user staff access to the bot (server owner only)")
@is_owner()
@app_commands.describe(member="the person to give staff access to")
async def authorize(i: discord.Interaction, member: discord.Member):
    staff = gcfg(i.guild.id)["staff"]
    if str(member.id) in staff:
        return await reply(i, fail(f"{member.mention} already has staff access"), ephemeral=True)
    staff.append(str(member.id))
    save()
    await reply(i, ok(f"{member.mention} now has staff access"))
    await log(i.guild, mk("Staff added", f"{member.mention} was given staff access by {i.user.mention}", C.OK))

@bot.tree.command(name="unauthorize", description="remove a user's staff access (server owner only)")
@is_owner()
@app_commands.describe(member="the person to remove staff access from")
async def unauthorize(i: discord.Interaction, member: discord.Member):
    staff = gcfg(i.guild.id)["staff"]
    if str(member.id) not in staff:
        return await reply(i, fail(f"{member.mention} doesn't have staff access"), ephemeral=True)
    staff.remove(str(member.id))
    save()
    await reply(i, ok(f"removed staff access from {member.mention}"))
    await log(i.guild, mk("Staff removed", f"{member.mention} lost staff access (removed by {i.user.mention})", C.ERR))

@bot.tree.command(name="staff", description="see who has staff access")
@is_auth()
async def staff_cmd(i: discord.Interaction):
    ids = gcfg(i.guild.id)["staff"]
    lines = [f"<@{i.guild.owner_id}> (owner)"]
    lines += [f"<@{uid}>" for uid in ids]
    await reply(i, mk("Staff", "\n".join(lines), C.BRAND), ephemeral=True)

@bot.tree.command(name="case", description="look up one moderation action by its case number")
@is_auth()
@app_commands.describe(case_id="the case number from the log, e.g. 12")
async def case(i: discord.Interaction, case_id: app_commands.Range[int, 1, 1000000]):
    c = gcfg(i.guild.id)["cases"].get(str(case_id))
    if not c:
        return await reply(i, fail(f"there's no case #{case_id}"), ephemeral=True)
    e = mk(f"Case #{case_id}: {c['action']}", color=C.DARK)
    e.add_field(name="User", value=f"<@{c['target']}>\n`{c['target']}`")
    e.add_field(name="Moderator", value=f"<@{c['mod']}>")
    e.add_field(name="When", value=f"<t:{c['time']}:F> (<t:{c['time']}:R>)", inline=False)
    e.add_field(name="Reason", value=f"```{c['reason']}```", inline=False)
    await reply(i, e, ephemeral=True)

@bot.tree.command(name="kick", description="kick a member")
@is_auth()
async def kick(i: discord.Interaction, member: discord.Member, reason: str = "no reason"):
    if p := hierarchy_problem(i, member):
        return await reply(i, fail(p), ephemeral=True)
    await notify(member, "kicked", i.guild, reason, C.ORANGE)
    await member.kick(reason=reason)
    cn = new_case(i.guild, "kick", member, i.user, reason)
    e = case_embed("Member kicked", "", C.ORANGE, member, i.user, reason)
    e.add_field(name="Case", value=f"#{cn}")
    await reply(i, e)
    await log(i.guild, e)

@bot.tree.command(name="ban", description="ban a member")
@is_auth()
async def ban(i: discord.Interaction, member: discord.Member, reason: str = "no reason"):
    if p := hierarchy_problem(i, member):
        return await reply(i, fail(p), ephemeral=True)
    if not await confirm(i, "Confirm ban", f"ban {member.mention}?\n**Reason:** {reason}"):
        return
    await notify(member, "banned", i.guild, reason, C.ERR)
    await member.ban(reason=reason)
    cn = new_case(i.guild, "ban", member, i.user, reason)
    e = case_embed("Member banned", "", C.ERR, member, i.user, reason)
    e.add_field(name="Case", value=f"#{cn}")
    await i.delete_original_response()
    await i.followup.send(embed=e)
    await log(i.guild, e)

@bot.tree.command(name="unban", description="unban by user id")
@is_auth()
async def unban(i: discord.Interaction, user_id: str):
    if not user_id.isdigit():
        return await reply(i, fail("that's not a valid user id"), ephemeral=True)
    try:
        user = await bot.fetch_user(int(user_id))
        await i.guild.unban(user)
    except discord.NotFound:
        return await reply(i, fail("that user isn't banned or doesn't exist"), ephemeral=True)
    except discord.HTTPException:
        return await reply(i, fail("couldn't unban that user"), ephemeral=True)
    cn = new_case(i.guild, "unban", user, i.user, "unbanned")
    e = case_embed("Member unbanned", "", C.OK, user, i.user, "unbanned")
    e.add_field(name="Case", value=f"#{cn}")
    await reply(i, e)
    await log(i.guild, e)

@bot.tree.command(name="softban", description="ban + unban to wipe their messages")
@is_auth()
async def softban(i: discord.Interaction, member: discord.Member, reason: str = "no reason"):
    if p := hierarchy_problem(i, member):
        return await reply(i, fail(p), ephemeral=True)
    if not await confirm(i, "Confirm softban", f"softban {member.mention}? this deletes their last 24 hours of messages"):
        return
    await notify(member, "softbanned (you can rejoin)", i.guild, reason, C.ORANGE)
    await member.ban(reason=reason, delete_message_seconds=86400)
    await i.guild.unban(member)
    cn = new_case(i.guild, "softban", member, i.user, reason)
    e = case_embed("Member softbanned", "", C.ORANGE, member, i.user, reason)
    e.add_field(name="Case", value=f"#{cn}")
    await i.delete_original_response()
    await i.followup.send(embed=e)
    await log(i.guild, e)

@bot.tree.command(name="timeout", description="timeout a member")
@is_auth()
@app_commands.describe(minutes="1 to 40320 (28 days)")
async def timeout(i: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = "no reason"):
    if p := hierarchy_problem(i, member):
        return await reply(i, fail(p), ephemeral=True)
    await member.timeout(timedelta(minutes=minutes), reason=reason)
    cn = new_case(i.guild, f"timeout {minutes}m", member, i.user, reason)
    ends = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    e = case_embed("Member timed out", "", C.ORANGE, member, i.user, reason,
                   {"Duration": f"{minutes}m", "Ends": discord.utils.format_dt(ends, "R")})
    e.add_field(name="Case", value=f"#{cn}")
    await reply(i, e)
    await log(i.guild, e)

@bot.tree.command(name="untimeout", description="remove timeout")
@is_auth()
async def untimeout(i: discord.Interaction, member: discord.Member):
    await member.timeout(None)
    cn = new_case(i.guild, "untimeout", member, i.user, "timeout lifted")
    e = case_embed("Timeout removed", "", C.OK, member, i.user, "timeout lifted")
    e.add_field(name="Case", value=f"#{cn}")
    await reply(i, e)
    await log(i.guild, e)

@bot.tree.command(name="warn", description="warn a member")
@is_auth()
async def warn(i: discord.Interaction, member: discord.Member, reason: str):
    g = gcfg(i.guild.id)
    w = g["warns"].setdefault(str(member.id), [])
    w.append({"reason": reason, "by": i.user.id, "time": int(time.time())})
    save()
    cn = new_case(i.guild, "warn", member, i.user, reason)
    e = case_embed("Member warned", "", C.WARN, member, i.user, reason, {"Total warns": len(w)})
    e.add_field(name="Case", value=f"#{cn}")
    await reply(i, e)
    await notify(member, "warned", i.guild, reason, C.WARN)
    await log(i.guild, e)

@bot.tree.command(name="warnings", description="see someone's warns")
@is_auth()
async def warnings(i: discord.Interaction, member: discord.Member):
    w = gcfg(i.guild.id)["warns"].get(str(member.id), [])
    if not w:
        return await reply(i, mk("No warns", f"{member.mention} has no warns", C.OK), ephemeral=True)
    pages = []
    for start in range(0, len(w), 5):
        e = mk(f"Warnings for {member.display_name}", f"total: **{len(w)}**", C.WARN, thumb=member.display_avatar.url)
        for n, x in enumerate(w[start:start + 5], start=start + 1):
            e.add_field(name=f"#{n} • <t:{x['time']}:d>", value=f"{x['reason']}\nby <@{x['by']}> • <t:{x['time']}:R>", inline=False)
        pages.append(e)
    v = Pager(i.user.id, pages)
    await i.response.send_message(embed=pages[0], view=v, ephemeral=True)
    v.message = await i.original_response()

@bot.tree.command(name="clearwarns", description="clear someone's warns")
@is_auth()
async def clearwarns(i: discord.Interaction, member: discord.Member):
    n = len(gcfg(i.guild.id)["warns"].pop(str(member.id), []))
    save()
    cn = new_case(i.guild, "clearwarns", member, i.user, f"cleared {n} warn(s)")
    e = case_embed("Warns cleared", "", C.OK, member, i.user, f"cleared {n} warn(s)")
    e.add_field(name="Case", value=f"#{cn}")
    await reply(i, e)
    await log(i.guild, e)

@bot.tree.command(name="purge", description="delete messages")
@is_auth()
@app_commands.describe(amount="how many messages to scan (1-500)", member="only delete this person's messages")
async def purge(i: discord.Interaction, amount: app_commands.Range[int, 1, 500], member: Optional[discord.Member] = None):
    await i.response.defer(ephemeral=True)
    check = (lambda m: m.author.id == member.id) if member else (lambda m: True)
    deleted = await i.channel.purge(limit=amount, check=check)
    await i.followup.send(embed=ok(f"deleted **{len(deleted)}** messages" + (f" from {member.mention}" if member else "")), ephemeral=True)
    await log(i.guild, mk("Purge", f"{i.user.mention} deleted **{len(deleted)}** messages in {i.channel.mention}", C.DARK))

async def lock_channel(channel, guild):
    store = gcfg(guild.id).setdefault("locks", {})
    if str(channel.id) in store:
        return None
    snap = {}
    locked = skipped = 0
    for role in guild.roles:
        if role >= guild.me.top_role:
            skipped += 1
            continue
        ow = channel.overwrites_for(role)
        snap[f"r:{role.id}"] = ow.send_messages
        ow.send_messages = False
        try:
            await channel.set_permissions(role, overwrite=ow, reason="channel locked")
            locked += 1
        except discord.HTTPException:
            skipped += 1
    keep = [guild.owner_id, guild.me.id] + [int(x) for x in gcfg(guild.id)["staff"]]
    for uid in dict.fromkeys(keep):
        m = guild.get_member(uid)
        if m is None:
            continue
        ow = channel.overwrites_for(m)
        snap[f"m:{uid}"] = ow.send_messages
        ow.send_messages = True
        try:
            await channel.set_permissions(m, overwrite=ow, reason="channel locked")
        except discord.HTTPException:
            pass
    store[str(channel.id)] = snap
    save()
    return locked, skipped

async def unlock_channel(channel, guild):
    store = gcfg(guild.id).setdefault("locks", {})
    snap = store.pop(str(channel.id), None)
    save()
    if snap is None:
        ow = channel.overwrites_for(guild.default_role)
        ow.send_messages = None
        try:
            await channel.set_permissions(guild.default_role, overwrite=ow, reason="channel unlocked")
        except discord.HTTPException:
            pass
        return True
    for key, val in snap.items():
        kind, _, rid = key.partition(":")
        target = guild.get_role(int(rid)) if kind == "r" else guild.get_member(int(rid))
        if target is None:
            continue
        ow = channel.overwrites_for(target)
        ow.send_messages = val
        try:
            if ow.is_empty():
                await channel.set_permissions(target, overwrite=None, reason="channel unlocked")
            else:
                await channel.set_permissions(target, overwrite=ow, reason="channel unlocked")
        except discord.HTTPException:
            pass
    return True

@bot.tree.command(name="lock", description="lock this channel")
@is_auth()
async def lock(i: discord.Interaction):
    res = await lock_channel(i.channel, i.guild)
    if res is None:
        return await reply(i, fail("this channel is already locked"), ephemeral=True)
    locked, skipped = res
    await reply(i, mk("Channel locked",
                      f"locked by {i.user.mention}\nroles changed: **{locked}**\nroles skipped (above my role): **{skipped}**",
                      C.ERR))

@bot.tree.command(name="unlock", description="unlock this channel")
@is_auth()
async def unlock(i: discord.Interaction):
    await unlock_channel(i.channel, i.guild)
    await reply(i, mk("Channel unlocked", f"unlocked by {i.user.mention}", C.OK))

@bot.tree.command(name="lockdown", description="lock every channel")
@is_auth()
async def lockdown(i: discord.Interaction):
    if not await confirm(i, "Server lockdown", "this will stop everyone except staff from talking in every text channel"):
        return
    done = 0
    for ch in i.guild.text_channels:
        try:
            if await lock_channel(ch, i.guild) is not None:
                done += 1
        except discord.HTTPException:
            pass
    e = mk("Server locked down", f"locked **{done}/{len(i.guild.text_channels)}** channels\nby {i.user.mention}", C.ERR)
    await i.delete_original_response()
    await i.followup.send(embed=e)
    await log(i.guild, e)

@bot.tree.command(name="unlockdown", description="unlock every channel")
@is_auth()
async def unlockdown(i: discord.Interaction):
    await i.response.defer()
    done = 0
    for ch in i.guild.text_channels:
        try:
            await unlock_channel(ch, i.guild)
            done += 1
        except discord.HTTPException:
            pass
    e = mk("Lockdown lifted", f"unlocked **{done}/{len(i.guild.text_channels)}** channels\nby {i.user.mention}", C.OK)
    await i.followup.send(embed=e)
    await log(i.guild, e)

@bot.tree.command(name="slowmode", description="set slowmode (0 to turn off)")
@is_auth()
async def slowmode(i: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
    await i.channel.edit(slowmode_delay=seconds)
    text = "slowmode is now **off**" if seconds == 0 else f"slowmode set to **{seconds}s**"
    await reply(i, mk("Slowmode", text, C.BRAND))

@bot.tree.command(name="nick", description="change someone's nickname")
@is_auth()
@app_commands.describe(nickname="leave empty to reset")
async def nick(i: discord.Interaction, member: discord.Member, nickname: Optional[str] = None):
    if member.id != i.user.id and (p := hierarchy_problem(i, member)):
        return await reply(i, fail(p), ephemeral=True)
    old = member.display_name
    await member.edit(nick=nickname)
    await reply(i, ok(f"`{old}` changed to `{nickname or member.name}`"))

@bot.tree.command(name="addrole", description="give a role")
@is_auth()
async def addrole(i: discord.Interaction, member: discord.Member, role: discord.Role):
    if role >= i.guild.me.top_role:
        return await reply(i, fail("that role is higher than mine"), ephemeral=True)
    await member.add_roles(role)
    await reply(i, ok(f"gave {role.mention} to {member.mention}"))

@bot.tree.command(name="removerole", description="remove a role")
@is_auth()
async def removerole(i: discord.Interaction, member: discord.Member, role: discord.Role):
    if role >= i.guild.me.top_role:
        return await reply(i, fail("that role is higher than mine"), ephemeral=True)
    await member.remove_roles(role)
    await reply(i, ok(f"removed {role.mention} from {member.mention}"))

@bot.tree.command(name="say", description="make the bot send a message")
@is_auth()
async def say(i: discord.Interaction, message: str):
    await i.channel.send(message)
    await reply(i, ok("message sent"), ephemeral=True)

class EmbedModal(discord.ui.Modal, title="Embed builder"):
    t = discord.ui.TextInput(label="Title", max_length=256)
    d = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, max_length=4000)
    c = discord.ui.TextInput(label="Color (hex)", placeholder="#5865F2", required=False, max_length=7)
    img = discord.ui.TextInput(label="Image URL", required=False)
    foot = discord.ui.TextInput(label="Footer", required=False, max_length=200)

    async def on_submit(self, i: discord.Interaction):
        try:
            color = int((self.c.value or "5865F2").strip("#"), 16)
        except ValueError:
            color = C.BRAND
        e = discord.Embed(title=self.t.value, description=self.d.value, color=color)
        if self.img.value:
            e.set_image(url=self.img.value)
        if self.foot.value:
            e.set_footer(text=self.foot.value)
        await i.channel.send(embed=e)
        await i.response.send_message(embed=ok("embed sent"), ephemeral=True)

    async def on_error(self, i: discord.Interaction, error):
        await reply(i, fail(f"couldn't send that embed. check the image URL\n```{error}```"), ephemeral=True)

@bot.tree.command(name="embed", description="open the embed builder")
@is_auth()
async def embed_cmd(i: discord.Interaction):
    await i.response.send_modal(EmbedModal())

@bot.tree.command(name="dm", description="dm a member through the bot")
@is_auth()
async def dm(i: discord.Interaction, member: discord.Member, message: str):
    e = mk(f"Message from {i.guild.name}", message, C.BRAND, thumb=i.guild.icon.url if i.guild.icon else None)
    try:
        await member.send(embed=e)
        await reply(i, ok(f"sent to {member.mention}"), ephemeral=True)
    except discord.HTTPException:
        await reply(i, fail(f"{member.mention} has DMs turned off"), ephemeral=True)

@bot.tree.command(name="resetchannel", description="wipe this channel completely (clones and deletes it)")
@is_auth()
async def resetchannel(i: discord.Interaction):
    if not await confirm(i, "Reset channel", f"this will delete {i.channel.mention} and recreate it empty. this can't be undone"):
        return
    old = i.channel
    new = await old.clone(reason=f"reset by {i.user}")
    await new.edit(position=old.position)
    await old.delete()
    await new.send(embed=mk("Channel reset", f"this channel was wiped by {i.user.mention}", C.BRAND))
    await log(i.guild, mk("Channel reset", f"{new.mention} was reset by {i.user.mention}", C.DARK))

def security_embed(guild):
    g = gcfg(guild.id)
    sec = g["security"]
    on = sum(sec.values())
    e = mk("Security", f"**{on}/{len(FEATURES)}** modules active\nselect a module to turn it on or off", C.BRAND,
           thumb=guild.icon.url if guild.icon else None)
    for k, (name, desc) in FEATURES.items():
        e.add_field(name=f"{'On' if sec[k] else 'Off'}: {name}", value=desc, inline=False)
    e.add_field(name="Minimum account age", value=f"{g['min_age_days']} days" if g["min_age_days"] else "off")
    e.add_field(name="Log channel", value=f"<#{g['log']}>" if g["log"] else "not set")
    return e

class SecurityView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.message = None
        self.build()

    def build(self):
        self.clear_items()
        sec = gcfg(self.guild_id)["security"]
        for k, (name, _) in FEATURES.items():
            b = discord.ui.Button(label=name, style=discord.ButtonStyle.success if sec[k] else discord.ButtonStyle.secondary)
            b.callback = self.toggle(k)
            self.add_item(b)

    def toggle(self, key):
        async def cb(i: discord.Interaction):
            sec = gcfg(self.guild_id)["security"]
            sec[key] = not sec[key]
            save()
            self.build()
            await i.response.edit_message(embed=security_embed(i.guild), view=self)
            await log(i.guild, mk("Security changed",
                f"{i.user.mention} turned **{FEATURES[key][0]}** {'on' if sec[key] else 'off'}", C.DARK))
        return cb

    async def interaction_check(self, i: discord.Interaction):
        if not is_staff(i.user, i.guild):
            await i.response.send_message(embed=fail("you're not allowed to change this"), ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

@bot.tree.command(name="setlog", description="set the log channel")
@is_auth()
async def setlog(i: discord.Interaction, channel: discord.TextChannel):
    gcfg(i.guild.id)["log"] = channel.id
    save()
    await reply(i, ok(f"logs will go to {channel.mention}"))
    await log(i.guild, mk("Logging enabled", f"this channel is now the log channel (set by {i.user.mention})", C.OK))

@bot.tree.command(name="security", description="turn a security feature on or off")
@is_auth()
async def security(i: discord.Interaction, feature: Literal["antispam", "antilink", "antimention", "antiraid", "antinuke"], enabled: bool):
    gcfg(i.guild.id)["security"][feature] = enabled
    save()
    name = FEATURES[feature][0]
    await reply(i, mk("Security updated", f"**{name}** is now **{'on' if enabled else 'off'}**",
                      C.OK if enabled else C.ERR))

@bot.tree.command(name="securitystatus", description="open the security panel")
@is_auth()
async def securitystatus(i: discord.Interaction):
    v = SecurityView(i.guild.id)
    await i.response.send_message(embed=security_embed(i.guild), view=v, ephemeral=True)
    v.message = await i.original_response()

@bot.tree.command(name="minage", description="kick accounts newer than X days (0 = off)")
@is_auth()
async def minage(i: discord.Interaction, days: app_commands.Range[int, 0, 365]):
    gcfg(i.guild.id)["min_age_days"] = days
    save()
    text = "account age filter is now off" if days == 0 else f"accounts younger than **{days} days** will be kicked"
    await reply(i, mk("Account age filter", text, C.BRAND))

@bot.event
async def on_ready():
    bot.add_view(TicketView())
    bot.add_view(TicketCloseView())
    bot.add_view(ApplyPanelView())
    bot.add_view(ReviewView())
    bot.add_view(GiveawayView())
    if not check_giveaways.is_running():
        check_giveaways.start()
    await bot.tree.sync()
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name=f"over {len(bot.guilds)} servers | /help"))
    print(f"online as {bot.user}")

@bot.tree.error
async def on_error(i: discord.Interaction, error):
    if isinstance(error, app_commands.CheckFailure):
        e = fail("you don't have permission to use this command")
    elif isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
        e = fail("i don't have permission for that")
    else:
        e = fail(f"something went wrong\n```{error}```")
    try:
        await reply(i, e, ephemeral=True)
    except discord.HTTPException:
        pass

async def punish(msg, reason, minutes=0, purge_user=False):
    try:
        if purge_user:
            await msg.channel.purge(limit=30, check=lambda m: m.author.id == msg.author.id)
        else:
            await msg.delete()
    except discord.HTTPException:
        pass
    if minutes:
        try:
            await msg.author.timeout(timedelta(minutes=minutes), reason=f"[auto] {reason}")
        except discord.HTTPException:
            pass
    try:
        await msg.channel.send(
            content=msg.author.mention,
            embed=mk(None, f"**{reason}** isn't allowed here" + (f"\ntimed out for **{minutes}m**" if minutes else ""), C.ERR),
            delete_after=6,
        )
    except discord.HTTPException:
        pass
    e = mk("Auto-mod", color=C.ORANGE, thumb=msg.author.display_avatar.url)
    e.add_field(name="User", value=f"{msg.author.mention}\n`{msg.author.id}`")
    e.add_field(name="Channel", value=msg.channel.mention)
    e.add_field(name="Trigger", value=reason)
    if minutes:
        e.add_field(name="Action", value=f"timed out {minutes}m")
    await log(msg.guild, e)

@bot.event
async def on_message(msg):
    if msg.author.bot or not msg.guild or is_staff(msg.author, msg.guild):
        return
    sec = gcfg(msg.guild.id)["security"]
    content = msg.content.lower()

    if sec["antilink"] and ("discord.gg/" in content or "discord.com/invite" in content):
        return await punish(msg, "invite links")

    if sec["antimention"] and (len(msg.mentions) + len(msg.role_mentions) >= 5 or msg.mention_everyone):
        return await punish(msg, "mass pinging", 30)

    if sec["antispam"]:
        now = time.time()
        q = spam_tracker[(msg.guild.id, msg.author.id)]
        q.append(now)
        if len([t for t in q if now - t < 5]) >= 6:
            q.clear()
            return await punish(msg, "spamming", 10, purge_user=True)

@bot.event
async def on_member_join(member):
    g = gcfg(member.guild.id)
    if g["min_age_days"] > 0:
        age = (datetime.now(timezone.utc) - member.created_at).days
        if age < g["min_age_days"]:
            try:
                await member.send(embed=mk("Account too new",
                    f"your account must be at least **{g['min_age_days']} days** old to join **{member.guild.name}**", C.ERR))
            except discord.HTTPException:
                pass
            try:
                await member.kick(reason="[anti-alt] account too new")
                e = mk("Account too new", f"kicked {member.mention} (`{member}`)", C.ORANGE, thumb=member.display_avatar.url)
                e.add_field(name="Account age", value=f"{age} days")
                await log(member.guild, e)
            except discord.HTTPException:
                pass
            return

    if g["security"]["antiraid"]:
        now = time.time()
        q = join_tracker[member.guild.id]
        q.append(now)
        if len([t for t in q if now - t < 10]) >= 8:
            try:
                await member.kick(reason="[anti-raid] raid detected")
                await log(member.guild, mk("Anti-raid", f"raid detected, kicked {member.mention} (`{member}`)", C.ERR))
            except discord.HTTPException:
                pass

async def nuke_check(guild, action, what):
    if not gcfg(guild.id)["security"]["antinuke"]:
        return
    user = None
    try:
        async for entry in guild.audit_logs(limit=1, action=action):
            user = entry.user
    except discord.Forbidden:
        return
    if user is None or user.id == bot.user.id or is_staff(user, guild):
        return
    now = time.time()
    q = nuke_tracker[(guild.id, user.id)]
    q.append(now)
    if len([t for t in q if now - t < 15]) >= 3:
        q.clear()
        try:
            await guild.ban(user, reason=f"[anti-nuke] mass {what}")
            await log(guild, mk("Anti-nuke", f"banned {user.mention} (`{user}`) for mass **{what}**", C.ERR))
        except discord.HTTPException:
            await log(guild, mk("Anti-nuke failed",
                f"{user.mention} is mass **{what}** but i couldn't ban them", C.ERR))

@bot.event
async def on_guild_channel_delete(channel):
    await nuke_check(channel.guild, discord.AuditLogAction.channel_delete, "deleting channels")

@bot.event
async def on_guild_role_delete(role):
    await nuke_check(role.guild, discord.AuditLogAction.role_delete, "deleting roles")

@bot.event
async def on_member_ban(guild, user):
    await nuke_check(guild, discord.AuditLogAction.ban, "banning members")

HELP = {
    "general": ("", "General", False, [
        ("ping", "check bot latency"), ("help", "this menu"), ("userinfo", "info about a user"),
        ("serverinfo", "info about the server"), ("avatar", "get someone's profile picture"), ("botinfo", "bot stats"),
        ("membercount", "member count"), ("report", "report a user to staff"), ("changelog", "see what has changed")]),
    "fun": ("", "Fun", False, [
        ("coinflip", "flip a coin"), ("roll", "roll a dice"), ("8ball", "ask the magic 8ball")]),
    "mod": ("", "Moderation", True, [
        ("kick", "kick a member"), ("ban", "ban a member"), ("unban", "unban by id"),
        ("softban", "ban and unban to delete messages"), ("timeout", "timeout a member"),
        ("untimeout", "remove a timeout"), ("warn", "warn a member"), ("warnings", "view warns"),
        ("clearwarns", "clear warns"), ("purge", "bulk delete messages"), ("case", "look up a mod action by number"),
        ("ticketpanel", "post the ticket panel here")]),
    "giveaways": ("", "Giveaways", True, [
        ("giveaway", "start a giveaway in this channel"), ("greroll", "pick a new winner")]),
    "apps": ("", "Applications", True, [
        ("appnew", "create an application type"), ("appdelete", "delete an application type"),
        ("approle", "set the role for an application"), ("applypanel", "post the application panel here"),
        ("appchannel", "set the review channel"), ("appstatus", "see application settings"),
        ("appclear", "let a stuck applicant apply again")]),
    "admin": ("", "Admin", True, [
        ("lock", "lock this channel"), ("unlock", "unlock this channel"), ("lockdown", "lock every channel"),
        ("unlockdown", "unlock every channel"), ("slowmode", "set slowmode"), ("nick", "change a nickname"),
        ("addrole", "give a role"), ("removerole", "remove a role"), ("say", "make the bot send a message"),
        ("embed", "embed builder"), ("dm", "dm a member"), ("resetchannel", "wipe this channel")]),
    "security": ("", "Security", True, [
        ("setlog", "set the log channel"), ("security", "turn a module on or off"),
        ("securitystatus", "open the security panel"), ("minage", "minimum account age")]),
    "access": ("", "Access", True, [
        ("authorize", "give someone staff access (owner only)"), ("unauthorize", "remove staff access (owner only)"),
        ("staff", "see who has staff access")]),
}

def help_home(staff):
    e = mk("Help", "choose a category from the menu", C.BRAND,
           thumb=bot.user.display_avatar.url if bot.user else None)
    for key, (emoji, name, staff_only, cmds) in HELP.items():
        if staff or not staff_only:
            e.add_field(name=name, value=f"{len(cmds)} commands")
    return e

def help_page(key):
    emoji, name, _, cmds = HELP[key]
    return mk(name, "\n".join(f"`/{c}` - {d}" for c, d in cmds), C.BRAND)

class HelpSelect(discord.ui.Select):
    def __init__(self, staff):
        opts = [discord.SelectOption(label="Home", value="home")]
        opts += [discord.SelectOption(label=v[1], value=k, description=f"{len(v[3])} commands")
                 for k, v in HELP.items() if staff or not v[2]]
        super().__init__(placeholder="choose a category", options=opts)
        self.staff = staff

    async def callback(self, i: discord.Interaction):
        v = self.values[0]
        await i.response.edit_message(embed=help_home(self.staff) if v == "home" else help_page(v))

class HelpView(OwnedView):
    def __init__(self, owner_id, staff):
        super().__init__(owner_id, timeout=180)
        self.add_item(HelpSelect(staff))

CHANGELOG = [
    ("Latest", [
        "Added /changelog",
        "Credits in /botinfo now list the contributors 4568y and voi.djs",
        "Added /appclear to free a member whose application is stuck waiting",
        "Fixed case titles starting with a space when there's no emoji",
        "Fixed application panel and settings going past Discord's 25-field limit",
        "Fixed /nick changing the nickname of members above the bot's role",
        "Fixed /unban crashing on an unknown or unbanned user id",
    ]),
    ("Lock system", [
        "Lock now covers every role below the bot, not just @everyone",
        "Staff, the owner, and the bot can still talk while a channel is locked",
        "Unlock restores the exact permissions from before the lock",
        "Lockdown and unlockdown use the same system across all text channels",
    ]),
    ("Access", [
        "Removed the hardcoded staff list",
        "The server owner is the only one who can use admin commands by default",
        "Added /authorize, /unauthorize, and /staff for managing staff access",
        "Staff are exempt from auto-mod and the anti-nuke check",
    ]),
    ("Applications", [
        "Staff create custom application types with /appnew",
        "Applicants pick a position from a dropdown and fill in the questions",
        "Review channel with Accept and Deny buttons",
        "Accepted applicants get the role set for that position",
        "Denials can include a reason, which is sent to the applicant",
    ]),
    ("Giveaways", [
        "/giveaway with an Enter button and automatic winner selection",
        "Giveaways survive restarts and end on time",
        "/greroll picks a new winner",
    ]),
    ("Cases", [
        "Every moderation action gets a case number",
        "/case shows the details of any case",
    ]),
    ("Tickets", [
        "Ticket panel with Open Ticket button",
        "Private ticket channels visible only to the opener and staff",
        "Claim button shows which staff member is handling a ticket",
        "Close button deletes the ticket after confirmation",
    ]),
    ("Security", [
        "Anti-spam, anti-invite, anti-mass-ping, anti-raid, and anti-nuke",
        "Minimum account age filter",
        "Security panel with on and off buttons",
        "Log channel for all moderation and security events",
    ]),
    ("Moderation and admin", [
        "kick, ban, unban, softban, timeout, untimeout, warn, warnings, clearwarns, purge",
        "lock, unlock, lockdown, unlockdown, slowmode, nick, addrole, removerole, say, embed, dm, resetchannel",
        "Confirmation prompts for bans, softbans, lockdowns, and channel resets",
    ]),
    ("First release", [
        "Help menu with categories",
        "Ping, user info, server info, avatar, bot info, member count, coin flip, dice, and 8-ball",
        "Member reports sent to the log channel",
    ]),
]

def changelog_pages():
    pages = []
    for n, (title, items) in enumerate(CHANGELOG, start=1):
        e = mk(f"Changelog: {title}", "\n".join(f"- {x}" for x in items), C.BRAND)
        e.set_footer(text=f"entry {n} of {len(CHANGELOG)}")
        pages.append(e)
    return pages

@bot.tree.command(name="changelog", description="see what has changed in the bot")
async def changelog(i: discord.Interaction):
    pages = changelog_pages()
    v = Pager(i.user.id, pages)
    await i.response.send_message(embed=pages[0], view=v, ephemeral=True)
    v.message = await i.original_response()

@bot.tree.command(name="ping", description="check bot latency")
async def ping(i: discord.Interaction):
    ms = round(bot.latency * 1000)
    color = C.OK if ms < 150 else C.WARN if ms < 300 else C.ERR
    await reply(i, mk("Pong", f"websocket latency: **{ms}ms**", color))

@bot.tree.command(name="help", description="list commands")
async def help_cmd(i: discord.Interaction):
    staff = is_staff(i.user, i.guild)
    v = HelpView(i.user.id, staff)
    await i.response.send_message(embed=help_home(staff), view=v, ephemeral=True)
    v.message = await i.original_response()

@bot.tree.command(name="userinfo", description="info about a user")
@app_commands.describe(member="who to look up (defaults to you)")
async def userinfo(i: discord.Interaction, member: Optional[discord.Member] = None):
    m = member or i.user
    e = mk(m.display_name, f"{m.mention} • `{m}`", m.color if m.color.value else C.BRAND,
           thumb=m.display_avatar.url)
    e.add_field(name="ID", value=f"`{m.id}`")
    e.add_field(name="Created", value=discord.utils.format_dt(m.created_at, "R"))
    e.add_field(name="Joined", value=discord.utils.format_dt(m.joined_at, "R") if m.joined_at else "unknown")
    e.add_field(name="Top role", value=m.top_role.mention if len(m.roles) > 1 else "none")
    e.add_field(name="Booster", value="yes" if m.premium_since else "no")
    e.add_field(name="Bot", value="yes" if m.bot else "no")
    roles = [r.mention for r in reversed(m.roles[1:])][:15]
    e.add_field(name=f"Roles ({len(m.roles) - 1})", value=" ".join(roles) or "none", inline=False)
    if m.banner if hasattr(m, "banner") else None:
        e.set_image(url=m.banner.url)
    await reply(i, e)

@bot.tree.command(name="serverinfo", description="info about the server")
async def serverinfo(i: discord.Interaction):
    g = i.guild
    bots = sum(1 for m in g.members if m.bot)
    e = mk(g.name, g.description, C.BRAND, thumb=g.icon.url if g.icon else None)
    e.add_field(name="Owner", value=f"<@{g.owner_id}>")
    e.add_field(name="Members", value=f"{g.member_count - bots} humans\n{bots} bots")
    e.add_field(name="Channels", value=f"{len(g.text_channels)} text\n{len(g.voice_channels)} voice")
    e.add_field(name="Roles", value=len(g.roles))
    e.add_field(name="Boosts", value=f"{g.premium_subscription_count} (tier {g.premium_tier})")
    e.add_field(name="Verification", value=str(g.verification_level).title())
    e.add_field(name="Created", value=discord.utils.format_dt(g.created_at, "D"), inline=False)
    if g.banner:
        e.set_image(url=g.banner.url)
    await reply(i, e)

@bot.tree.command(name="avatar", description="get someone's profile picture")
async def avatar(i: discord.Interaction, member: Optional[discord.Member] = None):
    m = member or i.user
    e = mk(f"{m.display_name}'s avatar", color=C.BRAND)
    e.set_image(url=m.display_avatar.with_size(1024).url)
    v = discord.ui.View()
    for fmt in ("png", "jpg", "webp"):
        v.add_item(discord.ui.Button(label=fmt.upper(), url=m.display_avatar.with_format(fmt).with_size(1024).url))
    await i.response.send_message(embed=e, view=v)

@bot.tree.command(name="botinfo", description="bot stats")
async def botinfo(i: discord.Interaction):
    up = int(time.time() - start_time)
    d, r = divmod(up, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    e = mk(bot.user.name, color=C.BRAND, thumb=bot.user.display_avatar.url)
    e.add_field(name="Uptime", value=f"{d}d {h}h {m}m {s}s")
    e.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms")
    e.add_field(name="Servers", value=len(bot.guilds))
    e.add_field(name="Users", value=sum(g.member_count or 0 for g in bot.guilds))
    e.add_field(name="Library", value=f"discord.py {discord.__version__}")
    e.add_field(name="Created by", value="sarcokur5728 (Smallkitcat)", inline=False)
    e.add_field(name="Contributors", value="4568y\nvoi.djs", inline=False)
    await reply(i, e)

@bot.tree.command(name="membercount", description="how many members")
async def membercount(i: discord.Interaction):
    await reply(i, mk("Member count", f"**{i.guild.member_count:,}** members", C.BRAND))

@bot.tree.command(name="coinflip", description="flip a coin")
async def coinflip(i: discord.Interaction):
    await reply(i, mk("Coin flip", f"it landed on **{random.choice(['heads', 'tails'])}**", C.WARN))

@bot.tree.command(name="roll", description="roll a dice")
@app_commands.describe(sides="number of sides (2-1000)")
async def roll(i: discord.Interaction, sides: app_commands.Range[int, 2, 1000] = 6):
    await reply(i, mk("Dice roll", f"you rolled **{random.randint(1, sides)}** (d{sides})", C.BRAND))

@bot.tree.command(name="8ball", description="ask the magic 8ball")
async def eightball(i: discord.Interaction, question: str):
    a = random.choice(["yes", "no", "maybe", "definitely", "no chance", "ask again later", "100%", "doubt it"])
    e = mk("Magic 8-ball", color=C.DARK)
    e.add_field(name="Question", value=question[:1024], inline=False)
    e.add_field(name="Answer", value=f"**{a}**", inline=False)
    await reply(i, e)

class ReportModal(discord.ui.Modal, title="Report a user"):
    reason = discord.ui.TextInput(label="What happened?", style=discord.TextStyle.paragraph, max_length=1000)
    evidence = discord.ui.TextInput(label="Evidence (message links, etc.)", required=False, max_length=500)

    def __init__(self, target):
        super().__init__()
        self.target = target

    async def on_submit(self, i: discord.Interaction):
        e = mk("New report", color=C.WARN, thumb=self.target.display_avatar.url)
        e.add_field(name="Reported", value=f"{self.target.mention}\n`{self.target.id}`")
        e.add_field(name="By", value=i.user.mention)
        e.add_field(name="Channel", value=i.channel.mention)
        e.add_field(name="Reason", value=self.reason.value, inline=False)
        if self.evidence.value:
            e.add_field(name="Evidence", value=self.evidence.value, inline=False)
        await log(i.guild, e)
        await i.response.send_message(embed=ok("your report was sent to staff"), ephemeral=True)

@bot.tree.command(name="report", description="report a user to staff")
async def report(i: discord.Interaction, member: discord.Member):
    if member.id == i.user.id:
        return await reply(i, fail("you can't report yourself"), ephemeral=True)
    await i.response.send_modal(ReportModal(member))

bot.run("MTU1OTYxMzYwMjAxNTYwODg3Mg.Ga5yMu.k6yf-PfRLERlTds13ULWVqWzrLCnGrBwKIUF04")
