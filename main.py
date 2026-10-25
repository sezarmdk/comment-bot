# -*- coding: utf-8 -*-
"""
Telegram userbot: kanal postlariga eng birinchi izoh yozadi (tezlik o'lchovi bilan).
Boshqaruv: Saved Messages (Izbrannoe) ichida buyruqlar -> .help

O'rnatish:  pip install telethon cryptg uvloop
Ishga tushirish:  python main.py
ENV: API_ID, API_HASH, SESSION, (ixtiyoriy) PORT, FALLBACK_WAIT, RANK_WAIT
SESSION olish (kompyuterda bir marta):  python main.py login
"""
import os, sys, io, re, json, time, html, random, asyncio
from collections import deque

try:  # tezroq event loop (Linux/Render)
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except Exception:
    pass

from telethon import TelegramClient, events, errors, functions, types
from telethon.sessions import StringSession

API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "")
SESSION = os.getenv("SESSION", "")
PORT = int(os.getenv("PORT", "10000"))
FALLBACK_WAIT = float(os.getenv("FALLBACK_WAIT", "1.2"))  # guruh orqali kelmasa zaxira yo'l
RANK_WAIT = float(os.getenv("RANK_WAIT", "4"))           # o'rinni tekshirishdan oldin kutish
CFG_NAME = "userbot_config.json"

# ---------- SESSION yaratish ----------
if len(sys.argv) > 1 and sys.argv[1] == "login":
    aid = API_ID or int(input("API_ID: "))
    ahash = API_HASH or input("API_HASH: ")
    with TelegramClient(StringSession(), aid, ahash) as c:
        s = c.session.save()
        print("\n\nSESSION=" + s + "\n")
        c.send_message("me", "SESSION (hech kimga bermang):\n" + s)
    sys.exit(0)

client = TelegramClient(
    StringSession(SESSION), API_ID, API_HASH,
    connection_retries=None, retry_delay=1, auto_reconnect=True,
    flood_sleep_threshold=20, catch_up=False,
)

# ch: {"<kanal_id>": {"title","u","h","g","gh","t":[...],"on":bool}}
cfg = {"default": ["🔥🔥🔥"], "ch": {}, "order": [], "log": True}
GMAP, CMAP = {}, {}          # guruh_id -> info, kanal_id -> info
done = {}                    # dedupe
STATS = deque(maxlen=500)
PING = [0.0]
cfg_msg_id = None
ME_ID = None


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def split_texts(s):
    s = s.replace("||", " - ").replace("\n", " - ")
    return [t.strip() for t in s.split(" - ") if t.strip()]


def rebuild():
    GMAP.clear(); CMAP.clear()
    for k, v in cfg["ch"].items():
        if not v.get("on") or not v.get("g") or not v.get("h"):
            continue
        cid = int(k)
        info = {
            "cid": cid, "v": v,
            "c": types.InputPeerChannel(cid, v["h"]),
            "gp": types.InputPeerChannel(v["g"], v["gh"]),
        }
        GMAP[v["g"]] = info
        CMAP[cid] = info


def pick(info):
    arr = info["v"].get("t") or cfg["default"]
    if len(arr) < 2:
        return arr[0]
    c = random.choice([x for x in arr if x != info.get("last")] or arr)
    info["last"] = c
    return c


# ---------- Config: Saved Messages ichida fayl ----------
async def load_cfg():
    global cfg, cfg_msg_id
    async for m in client.iter_messages("me", limit=300):
        if m.file and m.file.name == CFG_NAME:
            try:
                data = json.loads((await m.download_media(file=bytes)).decode())
                for key, dv in (("default", ["🔥🔥🔥"]), ("ch", {}), ("order", []), ("log", True)):
                    data.setdefault(key, dv)
                cfg = data
                cfg_msg_id = m.id
                log("Config yuklandi:", len(cfg["ch"]), "kanal")
            except Exception as e:
                log("Config xato:", e)
            break


async def save_cfg():
    global cfg_msg_id
    buf = io.BytesIO(json.dumps(cfg, ensure_ascii=False, indent=1).encode())
    buf.name = CFG_NAME
    m = await client.send_file("me", buf, caption="#config — o'chirmang", force_document=True)
    if cfg_msg_id:
        try:
            await client.delete_messages("me", cfg_msg_id)
        except Exception:
            pass
    cfg_msg_id = m.id
    rebuild()


# ---------- Tez yuborish (raw so'rov, resolve yo'q) ----------
def rid():
    return random.randrange(-2 ** 63, 2 ** 63)


async def fast_send(peer, text, reply_id):
    req = functions.messages.SendMessageRequest(
        peer=peer, message=text, random_id=rid(), no_webpage=True,
        reply_to=types.InputReplyToMessage(reply_to_msg_id=reply_id),
    )
    for _ in range(3):
        try:
            t = time.perf_counter()
            r = await client(req)
            return r, (time.perf_counter() - t) * 1000
        except (errors.FloodWaitError, errors.SlowModeWaitError) as e:
            log("Kutish:", e.seconds, "s")
            if e.seconds > 60:
                return None, 0
            await asyncio.sleep(e.seconds + 0.3)
            req.random_id = rid()
        except Exception as e:
            log("Yuborish xatosi:", type(e).__name__, e)
            return None, 0
    return None, 0


def sent_info(r):
    if isinstance(r, types.UpdateShortSentMessage):
        return r.id, r.date
    mid = None
    for u in getattr(r, "updates", []):
        if isinstance(u, (types.UpdateNewChannelMessage, types.UpdateNewMessage)):
            return u.message.id, u.message.date
        if isinstance(u, types.UpdateMessageID):
            mid = u.id
    return mid, None


# ---------- Asosiy: post -> izoh ----------
@client.on(events.Raw(types.UpdateNewChannelMessage))
async def on_update(u):
    t0 = time.perf_counter()
    wall = time.time()
    m = u.message
    if not isinstance(m, types.Message) or m.out:
        return
    pid = m.peer_id.channel_id

    # 1) ENG TEZ: post muhokama guruhiga avto-forward bo'ldi -> darhol reply
    info = GMAP.get(pid)
    if info:
        f = m.fwd_from
        fr = m.from_id
        if not f or not f.channel_post or not isinstance(fr, types.PeerChannel) \
                or fr.channel_id != info["cid"]:
            return
        key = (info["cid"], f.channel_post)
        if key in done:
            return
        if m.grouped_id:
            gk = ("g", pid, m.grouped_id)
            if gk in done:
                return
            done[gk] = wall
        done[key] = wall
        await comment(info, m.id, m.date, f.channel_post, t0, wall, "guruh")
        return

    # 2) ZAXIRA: kanal postini ko'rdik, guruh orqali kelmasa GetDiscussionMessage
    info = CMAP.get(pid)
    if info and m.post:
        if m.grouped_id:
            gk = ("c", pid, m.grouped_id)
            if gk in done:
                return
            done[gk] = wall
        await asyncio.sleep(FALLBACK_WAIT)
        key = (pid, m.id)
        if key in done:
            return
        done[key] = time.time()
        try:
            d = await client(functions.messages.GetDiscussionMessageRequest(peer=info["c"], msg_id=m.id))
        except Exception as e:
            log("Discussion xato:", type(e).__name__)
            return
        dm = next((x for x in d.messages
                   if getattr(x.peer_id, "channel_id", None) == info["v"]["g"]), None)
        if dm:
            await comment(info, dm.id, m.date, m.id, t0, wall, "zaxira")


async def comment(info, reply_id, post_date, post_id, t0, wall, via):
    text = pick(info)
    t1 = time.perf_counter()
    r, rtt = await fast_send(info["gp"], text, reply_id)
    if not r:
        return
    total = (time.perf_counter() - t0) * 1000
    my_id, my_date = sent_info(r)
    rec = {
        "title": info["v"].get("title"), "post": post_id, "text": text, "via": via,
        "proc": (t1 - t0) * 1000, "rtt": rtt, "total": total,
        "lag": max(0.0, wall - post_date.timestamp()),
        "srv": (my_date - post_date).total_seconds() if my_date else None,
        "rank": None,
    }
    STATS.append(rec)
    log(f"✅ {rec['title']} #{post_id} | {total:.0f}ms | {via}")
    asyncio.create_task(report(info, reply_id, my_id, rec))


async def report(info, reply_id, my_id, rec):
    await asyncio.sleep(RANK_WAIT)
    if my_id:
        try:
            msgs = await client.get_messages(info["gp"], reply_to=reply_id, limit=100)
            rec["rank"] = sum(1 for x in msgs if x.id < my_id) + 1
        except Exception as e:
            log("Rank xato:", type(e).__name__)
    if not cfg.get("log", True):
        return
    rk = rec["rank"]
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rk, "🔸")
    srv = f"{rec['srv']:.0f} s" if rec["srv"] is not None else "?"
    msg = (
        f"{medal} <b>{rk or '?'}-o'rin</b> | {html.escape(str(rec['title']))}\n"
        f"⚡ Umumiy: <b>{rec['total']:.0f} ms</b> (ishlov {rec['proc']:.1f} ms + tarmoq {rec['rtt']:.0f} ms)\n"
        f"📥 Postdan kechikish: ~{rec['lag']:.2f} s | server farqi: {srv}\n"
        f"🛣 Yo'l: {rec['via']} | post #{rec['post']}\n"
        f"💬 {html.escape(rec['text'])}"
    )
    try:
        await client.send_message("me", msg, parse_mode="html")
    except Exception:
        pass


# ---------- Kanallar ----------
async def scan():
    found = {}
    async for d in client.iter_dialogs():
        e = d.entity
        if isinstance(e, types.Channel) and e.broadcast and getattr(e, "has_link", False):
            found[str(e.id)] = e
    for k, e in found.items():
        v = cfg["ch"].setdefault(k, {"t": [], "on": False, "g": None})
        v["title"], v["h"], v["u"] = e.title, e.access_hash, e.username
    cfg["order"] = [k for k in cfg["order"] if k in found] + \
                   [k for k in found if k not in cfg["order"]]
    for k in list(cfg["ch"]):
        if k not in found:
            cfg["ch"].pop(k)
    return len(found)


async def link_group(k):
    v = cfg["ch"][k]
    full = await client(functions.channels.GetFullChannelRequest(types.InputChannel(int(k), v["h"])))
    lid = full.full_chat.linked_chat_id
    if not lid:
        v["g"] = None
        return "⚠️ izoh yopiq"
    g = next((c for c in full.chats if c.id == lid), None)
    if not g:
        return "⚠️ guruh topilmadi"
    v["g"], v["gh"] = g.id, g.access_hash
    if getattr(g, "left", False):
        try:
            await client(functions.channels.JoinChannelRequest(g))
        except Exception as e:
            return f"⚠️ guruhga kirmadi ({type(e).__name__}), zaxira yo'l ishlaydi"
    return ""


async def resolve_join(ref):
    if "/+" in ref or "joinchat/" in ref:
        h = ref.rstrip("/").rsplit("/", 1)[1].lstrip("+")
        try:
            r = await client(functions.messages.ImportChatInviteRequest(h))
            return r.chats[0]
        except errors.UserAlreadyParticipantError:
            return (await client(functions.messages.CheckChatInviteRequest(h))).chat
    ent = await client.get_entity(ref)
    try:
        await client(functions.channels.JoinChannelRequest(ent))
    except Exception:
        pass
    return ent


def parse_sel(s):
    out = []
    for p in s.split(","):
        if "-" in p:
            a, b = p.split("-", 1)
            out += range(int(a), int(b) + 1)
        elif p:
            out.append(int(p))
    return out


def num_key(n):
    if 1 <= n <= len(cfg["order"]):
        return cfg["order"][n - 1]
    return None


async def apply_line(nums, rest):
    out = []
    rest = rest.strip()
    low = rest.lower()
    for n in nums:
        k = num_key(n)
        if not k:
            out.append(f"❌ .{n} yo'q")
            continue
        v = cfg["ch"][k]
        name = v.get("title")
        if not rest:
            st = "🟢" if v.get("on") else "⚪"
            t = " | ".join(v.get("t") or ["(default)"])
            out.append(f".{n} {st} {name}\n💬 {t}")
            continue
        warn = ""
        if low == "off":
            v["on"] = False
        elif low == "del":
            v["on"], v["t"] = False, []
        elif low in ("on", "def"):
            if low == "def":
                v["t"] = []
            v["on"] = True
            warn = await link_group(k)
        else:
            v["t"] = split_texts(rest)
            v["on"] = True
            warn = await link_group(k)
        st = "🟢" if v["on"] else "⚪"
        out.append(f".{n} {st} {name} — {len(v.get('t') or [])} matn {warn}".rstrip())
        if len(nums) > 1:
            await asyncio.sleep(0.3)
    return out


def list_text():
    if not cfg["order"]:
        return "📋 Kanal topilmadi. .scan"
    lines = [f"📋 Kanallar ({len(cfg['order'])}) | Default: {' - '.join(cfg['default'])[:60]}", ""]
    for i, k in enumerate(cfg["order"], 1):
        v = cfg["ch"][k]
        st = "🟢" if v.get("on") else "⚪"
        t = f" — {len(v['t'])} matn" if v.get("t") else (" — default" if v.get("on") else "")
        lines.append(f".{i} {st} {v.get('title')}{t}")
    return "\n".join(lines)


def stats_text():
    if not STATS:
        return "📊 Hali izoh yozilmadi"
    n = len(STATS)
    rk = [r["rank"] for r in STATS if r["rank"]]
    avg = lambda key: sum(r[key] for r in STATS) / n
    first = sum(1 for x in rk if x == 1)
    return (
        f"📊 Oxirgi {n} ta izoh\n"
        f"🥇 1-o'rin: {first}/{len(rk)} ({first * 100 / max(1, len(rk)):.0f}%)\n"
        f"⚡ O'rtacha umumiy: {avg('total'):.0f} ms | eng tez: {min(r['total'] for r in STATS):.0f} ms\n"
        f"🌐 O'rtacha tarmoq: {avg('rtt'):.0f} ms | ping: {PING[0]:.0f} ms\n"
        f"📥 O'rtacha post kechikishi: ~{avg('lag'):.2f} s\n"
        f"🛣 Zaxira yo'l: {sum(1 for r in STATS if r['via'] == 'zaxira')} marta"
    )


HELP = """🤖 Buyruqlar (Saved Messages'da):

.scan — barcha kanallarni yangilash (izohi ochiqlari)
.list — raqamlangan ro'yxat

.1 zor post - ajoyib - aha - olov
.2 men bir - yana men - super
  → raqam + matnlar (" - " bilan). Bir xabarda bir nechta qator mumkin.
.1-5 matn - matn   → 1 dan 5 gacha
.1,3,7 matn        → tanlanganlar
.1 off / .1 on / .1 def (default matn) / .1 del
.1                 → kanal ma'lumoti

.all matn - matn   → hamma kanalga
.on all / .off all
.def matn - matn   → default matn
.add @kanal t.me/+xxxx → kanalga kirib, ro'yxatga qo'shish
.stats — tezlik va o'rinlar statistikasi
.ping — Telegram serverigacha tezlik
.log on / .log off — har izoh logi"""

SEL_RE = re.compile(r"^\.(\d+(?:[-,]\d+)*)(?:\s+(.*))?$")


async def handle(raw):
    first = raw.split("\n", 1)[0].strip()
    cmd = first.split()[0][1:].lower()
    arg = first[len(cmd) + 1:].strip()
    extra = raw.split("\n", 1)[1] if "\n" in raw else ""

    # raqamli qatorlar: .1 matn - matn
    if SEL_RE.match(first):
        out = []
        for line in raw.split("\n"):
            m = SEL_RE.match(line.strip())
            if m:
                out += await apply_line(parse_sel(m.group(1)), m.group(2) or "")
        await save_cfg()
        return "\n".join(out)

    if cmd == "help":
        return HELP
    if cmd in ("list", "l"):
        return list_text()
    if cmd == "scan":
        n = await scan()
        await save_cfg()
        return f"🔄 {n} ta kanal topildi\n\n" + list_text()
    if cmd == "stats":
        return stats_text()
    if cmd == "ping":
        res = []
        for _ in range(3):
            t = time.perf_counter()
            await client(functions.updates.GetStateRequest())
            res.append((time.perf_counter() - t) * 1000)
        return f"🏓 Ping: {min(res):.0f} / {sum(res) / 3:.0f} ms (min/o'rt) | faol: {len(GMAP)} kanal"
    if cmd == "log":
        cfg["log"] = arg.lower() != "off"
        await save_cfg()
        return "📝 Log yoqildi" if cfg["log"] else "📝 Log o'chirildi"
    if cmd == "def":
        t = split_texts(arg + "\n" + extra)
        if not t:
            return "Matn yozing: .def zo'r - ajoyib"
        cfg["default"] = t
        await save_cfg()
        return f"✅ Default: {len(t)} variant"
    if cmd == "all":
        if not split_texts(arg + "\n" + extra):
            return "Matn yozing: .all zo'r - ajoyib"
        out = await apply_line(range(1, len(cfg["order"]) + 1), " - ".join(split_texts(arg + "\n" + extra)))
        await save_cfg()
        return "\n".join(out)
    if cmd in ("on", "off") and arg.lower() == "all":
        val = cmd == "on"
        for k in cfg["order"]:
            v = cfg["ch"][k]
            if val and not v.get("g"):
                await link_group(k)
                await asyncio.sleep(0.3)
            v["on"] = val
        await save_cfg()
        return "✅ Hammasi yoqildi" if val else "⏸ Hammasi o'chirildi"
    if cmd == "add":
        refs = arg.split()
        if not refs:
            return "Kanal yozing: .add @kanal"
        ids, out = [], []
        for ref in refs:
            try:
                ids.append(str((await resolve_join(ref)).id))
            except Exception as e:
                out.append(f"❌ {ref}: {type(e).__name__}")
        await scan()
        await save_cfg()
        for i in ids:
            if i in cfg["order"]:
                out.append(f"✅ .{cfg['order'].index(i) + 1} {cfg['ch'][i]['title']}")
            else:
                out.append(f"⚠️ {i}: izoh yopiq kanal")
        return "\n".join(out)
    return None


@client.on(events.NewMessage(outgoing=True))
async def on_cmd(ev):
    if ev.chat_id != ME_ID:
        return
    raw = (ev.raw_text or "").strip()
    if not raw.startswith(".") or len(raw) < 2:
        return
    try:
        out = await handle(raw)
    except Exception as e:
        out = f"❌ Xato: {type(e).__name__}: {e}"
    if out:
        for i in range(0, len(out), 4000):
            await ev.reply(out[i:i + 4000])


# ---------- Fon vazifalar ----------
async def http(reader, writer):
    try:
        await reader.read(1024)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                     b"Content-Length: 2\r\nConnection: close\r\n\r\nOK")
        await writer.drain()
    finally:
        writer.close()


async def cleaner():
    while True:
        await asyncio.sleep(600)
        lim = time.time() - 3600
        for k in [k for k, t in done.items() if t < lim]:
            done.pop(k, None)


async def keepalive():
    # ulanishni "issiq" ushlaydi va update oqimi to'xtab qolmasligini ta'minlaydi
    while True:
        await asyncio.sleep(40)
        try:
            t = time.perf_counter()
            await client(functions.updates.GetStateRequest())
            PING[0] = (time.perf_counter() - t) * 1000
        except Exception:
            pass


async def main():
    global ME_ID
    await asyncio.start_server(http, "0.0.0.0", PORT)
    log("HTTP port:", PORT)
    await client.start()
    me = await client.get_me()
    ME_ID = me.id
    await load_cfg()
    if not cfg["order"]:
        await scan()
        await save_cfg()
    rebuild()
    log(f"Ishga tushdi: {me.first_name} | kanal: {len(cfg['order'])} | faol: {len(GMAP)}")
    try:
        await client.send_message(
            "me", f"🤖 Userbot ishga tushdi. Kanal: {len(cfg['order'])} | faol: {len(GMAP)}\n"
                  f".list — ro'yxat, .help — yordam")
    except Exception:
        pass
    asyncio.create_task(cleaner())
    asyncio.create_task(keepalive())
    await client.run_until_disconnected()


if __name__ == "__main__":
    client.loop.run_until_complete(main())
