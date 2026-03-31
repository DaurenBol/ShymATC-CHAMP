import os, json, base64, requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

BOT_TOKEN    = os.environ.get("BOT_TOKEN")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO  = "DaurenBol/ShymATC-CHAMP"
DATA_FILE    = "data.json"
BRANCH       = "main"

(
    EV_NAME, EV_TYPE, EV_CATEGORY, EV_CATEGORY_CUSTOM,
    EV_FORMAT, EV_VENUE, EV_VENUE_CUSTOM,
    EV_DATE_START, EV_DATE_END, EV_RULES, EV_CONFIRM,
    ADD_PARTICIPANT,
    MATCH_EVENT, MATCH_HOME, MATCH_AWAY, MATCH_SCORE,
    DELETE_CONFIRM, FINISH_CONFIRM,
    ADD_ADMIN_ID, RESET_CONFIRM
) = range(20)

EVENT_TYPES = [
    ("🏆 Турнир","tournament"),("🏅 Лига","league"),("🥇 Чемпионат","championship"),
    ("🏆 Кубок","cup"),("⚡ Разовое","single"),("🤝 Встреча","meetup"),
    ("🎯 Тренировка","training"),("📝 Регистрация","registration"),("🔧 Другое","other"),
]
CATEGORIES = [
    ("⚽ Футбол","football"),("🎮 Киберфутбол","efootball"),("🥊 UFC","ufc"),
    ("♟ Шахматы","chess"),("🎾 Теннис","tennis"),("🏀 Баскетбол","basketball"),
    ("💼 Бизнес","business"),("🎓 Обучение","education"),("✏️ Другое","other_cat"),
]
FORMATS = [
    ("👤 Индивидуальный","individual"),("👥 Командный","team"),
    ("👫 Парный","pair"),("👨‍👩‍👧‍👦 Групповой","group"),("🔀 Смешанный","mixed"),
]
VENUES = [
    ("📍 Офлайн","offline"),("🌐 Онлайн","online"),("🎮 PlayStation","playstation"),
    ("💻 PC","pc"),("🎯 Xbox","xbox"),("📱 Mobile","mobile"),
    ("✈️ Telegram","telegram"),("📹 Zoom","zoom"),("🔧 Другое","other_venue"),
]

def gh():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

def load_data():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_FILE}"
    try:
        r = requests.get(url, headers=gh(), timeout=10)
        if r.status_code == 200:
            c = r.json()
            data = json.loads(base64.b64decode(c["content"]).decode())
            data["_sha"] = c["sha"]
            return data
    except Exception as e:
        print(f"load error: {e}")
    return {"events": [], "admins": [2070550], "_sha": None}

def save_data(data):
    sha = data.pop("_sha", None)
    encoded = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode()
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_FILE}"
    payload = {"message": "update via bot", "content": encoded, "branch": BRANCH}
    if sha: payload["sha"] = sha
    try:
        r = requests.put(url, headers=gh(), json=payload, timeout=15)
        return r.status_code in [200, 201]
    except Exception as e:
        print(f"save error: {e}")
        return False

def is_admin(uid, data): return uid in data.get("admins", [2070550])
def get_event(data, eid): return next((e for e in data["events"] if e["id"] == eid), None)
def gen_id(data):
    i = 1
    while f"ev_{i:03d}" in [e["id"] for e in data["events"]]: i += 1
    return f"ev_{i:03d}"
def label(items, val): return next((l for l,v in items if v==val), val)
def chunks(lst, n):
    for i in range(0, len(lst), n): yield lst[i:i+n]

def calc_standings(event):
    st = {p: {"name":p,"p":0,"w":0,"d":0,"l":0,"gf":0,"ga":0} for p in event["participants"]}
    for m in event.get("matches", []):
        if not m.get("played"): continue
        h,a,sh,sa = m["home"],m["away"],m["score_home"],m["score_away"]
        for name,gf,ga in [(h,sh,sa),(a,sa,sh)]:
            if name in st: st[name]["p"]+=1; st[name]["gf"]+=gf; st[name]["ga"]+=ga
        if sh>sa:
            if h in st: st[h]["w"]+=1
            if a in st: st[a]["l"]+=1
        elif sh<sa:
            if a in st: st[a]["w"]+=1
            if h in st: st[h]["l"]+=1
        else:
            if h in st: st[h]["d"]+=1
            if a in st: st[a]["d"]+=1
    rows = list(st.values())
    rows.sort(key=lambda x:(x["w"]*3+x["d"],x["gf"]-x["ga"]),reverse=True)
    return rows

def main_menu_kb(is_adm):
    kb = [
        [InlineKeyboardButton("📋 События", callback_data="menu_events"),
         InlineKeyboardButton("📊 Таблица", callback_data="menu_standings")],
        [InlineKeyboardButton("⚡ Матчи", callback_data="menu_matches")],
    ]
    if is_adm:
        kb += [
            [InlineKeyboardButton("➕ Создать событие", callback_data="menu_new_event")],
            [InlineKeyboardButton("👤 Участники", callback_data="menu_participants"),
             InlineKeyboardButton("⚽ Добавить матч", callback_data="menu_add_match")],
            [InlineKeyboardButton("🏁 Завершить", callback_data="menu_finish"),
             InlineKeyboardButton("🗑 Удалить", callback_data="menu_delete")],
            [InlineKeyboardButton("👑 Добавить админа", callback_data="menu_add_admin"),
             InlineKeyboardButton("🔄 Сброс", callback_data="menu_reset")],
        ]
    return InlineKeyboardMarkup(kb)

def options_kb(items, prefix, cols=2):
    btns = [InlineKeyboardButton(l, callback_data=f"{prefix}{v}") for l,v in items]
    return InlineKeyboardMarkup(list(chunks(btns, cols)))

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    adm = is_admin(update.effective_user.id, data)
    name = update.effective_user.first_name or "друг"
    await update.message.reply_text(
        f"👋 Привет, *{name}*!\n\n🏆 *ShymATC\\-CHAMP*",
        parse_mode="MarkdownV2",
        reply_markup=main_menu_kb(adm)
    )

async def show_main_menu(q, ctx):
    data = load_data()
    adm = is_admin(q.from_user.id, data)
    await q.edit_message_text("🏆 *ShymATC-CHAMP*", parse_mode="Markdown", reply_markup=main_menu_kb(adm))

async def back_main(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await show_main_menu(q, ctx)
    return ConversationHandler.END

async def menu_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    action = q.data.replace("menu_","")
    data = load_data()

    if action == "events":
        if not data["events"]:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_main")]])
            await q.edit_message_text("📋 Событий нет.", reply_markup=kb); return
        text = "📋 *События:*\n\n"
        for e in data["events"]:
            s = "🟢" if e.get("active") else "⚫"
            text += f"{s} *{e['name']}*\n"
            text += f"📌 {label(EVENT_TYPES,e.get('event_type',''))} · {label(CATEGORIES,e.get('category',''))}\n"
            text += f"📍 {label(VENUES,e.get('venue_type',''))} · 👥 {len(e.get('participants',[]))} уч.\n\n"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_main")]])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

    elif action == "standings":
        active = [e for e in data["events"] if e.get("active")]
        if not active:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_main")]])
            await q.edit_message_text("Нет активных событий.", reply_markup=kb); return
        if len(active) == 1:
            await do_standings(q, active[0]); return
        kb = [[InlineKeyboardButton(e["name"], callback_data=f"st_{e['id']}")] for e in active]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
        await q.edit_message_text("📊 Выбери событие:", reply_markup=InlineKeyboardMarkup(kb))

    elif action == "matches":
        all_m = [(e["name"],m) for e in data["events"] for m in e.get("matches",[]) if m.get("played")]
        if not all_m:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_main")]])
            await q.edit_message_text("Матчей нет.", reply_markup=kb); return
        text = "⚡ *Матчи:*\n\n"
        for ename,m in all_m[-8:]:
            sh,sa = m["score_home"],m["score_away"]
            res = f"🏆 {m['home']}" if sh>sa else (f"🏆 {m['away']}" if sa>sh else "🤝")
            text += f"*{m['home']}* {sh}:{sa} *{m['away']}* — {res}\n_{ename}_\n\n"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_main")]])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

    elif action == "new_event":
        if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
        ctx.user_data.clear()
        await q.edit_message_text("✏️ Введи *название события*:", parse_mode="Markdown")
        return EV_NAME

    elif action == "participants":
        if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
        active = [e for e in data["events"] if e.get("active")]
        if not active:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_main")]])
            await q.edit_message_text("Нет активных событий.", reply_markup=kb); return
        if len(active) == 1:
            ctx.user_data["ap_event_id"] = active[0]["id"]
            current = ", ".join(active[0]["participants"]) or "пока никого"
            await q.edit_message_text(f"👤 *{active[0]['name']}*\nСейчас: {current}\n\nВводи имена, /done — готово", parse_mode="Markdown")
            return ADD_PARTICIPANT
        kb = [[InlineKeyboardButton(e["name"], callback_data=f"ap_{e['id']}")] for e in active]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
        await q.edit_message_text("👤 Выбери событие:", reply_markup=InlineKeyboardMarkup(kb))
        return ADD_PARTICIPANT

    elif action == "add_match":
        if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
        active = [e for e in data["events"] if e.get("active") and len(e.get("participants",[])) >= 2]
        if not active:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_main")]])
            await q.edit_message_text("Нет событий с участниками.", reply_markup=kb); return
        if len(active) == 1:
            ctx.user_data["match"] = {"event_id": active[0]["id"]}
            kb = [[InlineKeyboardButton(p, callback_data=f"mh_{p}")] for p in active[0]["participants"]]
            await q.edit_message_text(f"⚽ *{active[0]['name']}*\nКто первый:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return MATCH_HOME
        kb = [[InlineKeyboardButton(e["name"], callback_data=f"me_{e['id']}")] for e in active]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
        await q.edit_message_text("⚽ Выбери событие:", reply_markup=InlineKeyboardMarkup(kb))
        return MATCH_EVENT

    elif action == "finish":
        if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
        active = [e for e in data["events"] if e.get("active")]
        if not active:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_main")]])
            await q.edit_message_text("Нет активных событий.", reply_markup=kb); return
        kb = [[InlineKeyboardButton(f"🏁 {e['name']}", callback_data=f"fin_{e['id']}")] for e in active]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
        await q.edit_message_text("🏁 Какое завершить?", reply_markup=InlineKeyboardMarkup(kb))
        return FINISH_CONFIRM

    elif action == "delete":
        if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
        if not data["events"]:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_main")]])
            await q.edit_message_text("Событий нет.", reply_markup=kb); return
        kb = [[InlineKeyboardButton(f"🗑 {e['name']}", callback_data=f"del_{e['id']}")] for e in data["events"]]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
        await q.edit_message_text("🗑 Какое удалить?", reply_markup=InlineKeyboardMarkup(kb))
        return DELETE_CONFIRM

    elif action == "add_admin":
        if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
        await q.edit_message_text("👑 Введи Telegram user_id нового админа:")
        return ADD_ADMIN_ID

    elif action == "reset":
        if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Да, очистить", callback_data="reset_yes"), InlineKeyboardButton("❌ Отмена", callback_data="back_main")]])
        await q.edit_message_text("⚠️ *Удалить все события?*", parse_mode="Markdown", reply_markup=kb)
        return RESET_CONFIRM

async def do_standings(q, event):
    rows = calc_standings(event)
    medals = ["🥇","🥈","🥉"]
    text = f"📊 *{event['name']}*\n\n`# Участник         И  В  Н  П  Оч`\n`{'─'*35}`\n"
    for i,r in enumerate(rows):
        pts = r["w"]*3+r["d"]
        rank = medals[i] if i<3 else f"{i+1}."
        name = r["name"][:15].ljust(15)
        text += f"`{rank} {name} {r['p']:2} {r['w']:2} {r['d']:2} {r['l']:2} {pts:3}`\n"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_main")]])
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

async def standings_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = load_data()
    event = get_event(data, q.data.replace("st_",""))
    if event: await do_standings(q, event)

# CREATE EVENT
async def ev_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ev"] = {"name": update.message.text.strip()}
    await update.message.reply_text("📌 *Тип события:*", parse_mode="Markdown", reply_markup=options_kb(EVENT_TYPES, "evt_"))
    return EV_TYPE

async def ev_type_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["ev"]["event_type"] = q.data.replace("evt_","")
    await q.edit_message_text("🎯 *Категория:*", parse_mode="Markdown", reply_markup=options_kb(CATEGORIES, "cat_"))
    return EV_CATEGORY

async def ev_category_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    val = q.data.replace("cat_","")
    if val == "other_cat":
        await q.edit_message_text("✏️ Введи категорию:")
        return EV_CATEGORY_CUSTOM
    ctx.user_data["ev"]["category"] = val
    await q.edit_message_text("👥 *Формат участия:*", parse_mode="Markdown", reply_markup=options_kb(FORMATS, "fmt_", cols=1))
    return EV_FORMAT

async def ev_category_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ev"]["category"] = update.message.text.strip()
    await update.message.reply_text("👥 *Формат участия:*", parse_mode="Markdown", reply_markup=options_kb(FORMATS, "fmt_", cols=1))
    return EV_FORMAT

async def ev_format_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["ev"]["participation_format"] = q.data.replace("fmt_","")
    await q.edit_message_text("📍 *Площадка или платформа:*", parse_mode="Markdown", reply_markup=options_kb(VENUES, "ven_"))
    return EV_VENUE

async def ev_venue_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    val = q.data.replace("ven_","")
    if val == "other_venue":
        await q.edit_message_text("✏️ Введи площадку:")
        return EV_VENUE_CUSTOM
    ctx.user_data["ev"]["venue_type"] = val
    await q.edit_message_text("📅 Дата начала (01.04.2026)\nИли /skip:")
    return EV_DATE_START

async def ev_venue_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ev"]["venue_type"] = update.message.text.strip()
    await update.message.reply_text("📅 Дата начала (01.04.2026)\nИли /skip:")
    return EV_DATE_START

async def ev_date_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ctx.user_data["ev"]["date_start"] = "" if text.startswith("/") else text
    await update.message.reply_text("📅 Дата окончания\nИли /skip:")
    return EV_DATE_END

async def ev_date_end(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ctx.user_data["ev"]["date_end"] = "" if text.startswith("/") else text
    await update.message.reply_text("📋 Правила события\nИли /skip:")
    return EV_RULES

async def ev_rules(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ctx.user_data["ev"]["rules"] = "" if text.startswith("/") else text
    ev = ctx.user_data["ev"]
    summary = (f"✅ *Подтверди:*\n\n"
               f"📌 *{ev['name']}*\n"
               f"🏷 {label(EVENT_TYPES,ev.get('event_type',''))}\n"
               f"🎯 {label(CATEGORIES,ev.get('category',''))}\n"
               f"👥 {label(FORMATS,ev.get('participation_format',''))}\n"
               f"📍 {label(VENUES,ev.get('venue_type',''))}\n")
    if ev.get("date_start"): summary += f"📅 {ev['date_start']}"
    if ev.get("date_end"): summary += f" → {ev['date_end']}\n"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Создать", callback_data="ev_create"), InlineKeyboardButton("❌ Отмена", callback_data="back_main")]])
    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=kb)
    return EV_CONFIRM

async def ev_confirm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "back_main":
        await show_main_menu(q, ctx); return ConversationHandler.END
    data = load_data()
    ev = ctx.user_data["ev"]
    eid = gen_id(data)
    event = {"id":eid,"name":ev["name"],"event_type":ev.get("event_type","other"),
             "category":ev.get("category","other"),"participation_format":ev.get("participation_format","individual"),
             "venue_type":ev.get("venue_type","offline"),"date_start":ev.get("date_start",""),
             "date_end":ev.get("date_end",""),"rules":ev.get("rules",""),"active":True,
             "participants":[],"matches":[],"created_at":datetime.now().strftime("%d.%m.%Y %H:%M")}
    data["events"].append(event)
    save_data(data)
    ctx.user_data["ap_event_id"] = eid
    await q.edit_message_text(f"🎉 *{ev['name']}* создано!\n\nВводи имена участников, /done — завершить", parse_mode="Markdown")
    return ADD_PARTICIPANT

# PARTICIPANTS
async def ap_event_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["ap_event_id"] = q.data.replace("ap_","")
    data = load_data()
    event = get_event(data, ctx.user_data["ap_event_id"])
    current = ", ".join(event["participants"]) or "пока никого"
    await q.edit_message_text(f"👤 *{event['name']}*\nСейчас: {current}\n\nВводи имена, /done — готово", parse_mode="Markdown")
    return ADD_PARTICIPANT

async def ap_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    data = load_data()
    event = get_event(data, ctx.user_data.get("ap_event_id"))
    if not event:
        await update.message.reply_text("Ошибка. Начни заново.")
        return ConversationHandler.END
    if name in event["participants"]:
        await update.message.reply_text(f"*{name}* уже есть.", parse_mode="Markdown")
        return ADD_PARTICIPANT
    event["participants"].append(name)
    save_data(data)
    current = ", ".join(event["participants"])
    await update.message.reply_text(f"✅ *{name}* добавлен\n{current}\n\nЕщё или /done", parse_mode="Markdown")
    return ADD_PARTICIPANT

async def done_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    adm = is_admin(update.effective_user.id, data)
    await update.message.reply_text("✅ Готово!", reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

# MATCH
async def match_event_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["match"] = {"event_id": q.data.replace("me_","")}
    data = load_data()
    event = get_event(data, ctx.user_data["match"]["event_id"])
    kb = [[InlineKeyboardButton(p, callback_data=f"mh_{p}")] for p in event["participants"]]
    await q.edit_message_text(f"⚽ *{event['name']}*\nКто первый:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    return MATCH_HOME

async def match_home_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["match"]["home"] = q.data.replace("mh_","")
    data = load_data()
    event = get_event(data, ctx.user_data["match"]["event_id"])
    others = [p for p in event["participants"] if p != ctx.user_data["match"]["home"]]
    kb = [[InlineKeyboardButton(p, callback_data=f"ma_{p}")] for p in others]
    await q.edit_message_text("Кто второй:", reply_markup=InlineKeyboardMarkup(kb))
    return MATCH_AWAY

async def match_away_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["match"]["away"] = q.data.replace("ma_","")
    h,a = ctx.user_data["match"]["home"],ctx.user_data["match"]["away"]
    await q.edit_message_text(f"Счёт *{h}* vs *{a}*\nФормат: `2:1`", parse_mode="Markdown")
    return MATCH_SCORE

async def match_score_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace("-",":").replace(" ","")
    if ":" not in text:
        await update.message.reply_text("Формат: `2:1`", parse_mode="Markdown"); return MATCH_SCORE
    try: sh,sa = map(int, text.split(":"))
    except:
        await update.message.reply_text("Формат: `2:1`", parse_mode="Markdown"); return MATCH_SCORE
    m = ctx.user_data["match"]
    data = load_data()
    event = get_event(data, m["event_id"])
    event["matches"].append({"home":m["home"],"away":m["away"],"score_home":sh,"score_away":sa,"played":True,"date":datetime.now().strftime("%d.%m.%Y")})
    save_data(data)
    res = f"🏆 *{m['home']}*" if sh>sa else (f"🏆 *{m['away']}*" if sa>sh else "🤝 Ничья")
    adm = is_admin(update.effective_user.id, data)
    await update.message.reply_text(f"✅ *{m['home']}* {sh}:{sa} *{m['away']}*\n{res}", parse_mode="Markdown", reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

# FINISH
async def finish_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = load_data()
    event = get_event(data, q.data.replace("fin_",""))
    if event:
        event["active"] = False
        save_data(data)
        await do_standings(q, event)
    return ConversationHandler.END

# DELETE
async def delete_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = load_data()
    event = get_event(data, q.data.replace("del_",""))
    if event:
        data["events"] = [e for e in data["events"] if e["id"] != event["id"]]
        save_data(data)
    await show_main_menu(q, ctx)
    return ConversationHandler.END

# ADD ADMIN
async def add_admin_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: new_id = int(update.message.text.strip())
    except:
        await update.message.reply_text("Введи число."); return ADD_ADMIN_ID
    data = load_data()
    if new_id not in data["admins"]:
        data["admins"].append(new_id); save_data(data)
        await update.message.reply_text(f"✅ Админ {new_id} добавлен.", reply_markup=main_menu_kb(True))
    else:
        await update.message.reply_text("Уже админ.", reply_markup=main_menu_kb(True))
    return ConversationHandler.END

# RESET
async def reset_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "reset_yes":
        data = load_data()
        new_data = {"events": [], "admins": data.get("admins",[2070550]), "_sha": data.get("_sha")}
        save_data(new_data)
    await show_main_menu(q, ctx)
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    data = load_data()
    adm = is_admin(update.effective_user.id, data)
    await update.message.reply_text("❌ Отменено.", reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_new_event$")],
        states={
            EV_NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND, ev_name)],
            EV_TYPE:[CallbackQueryHandler(ev_type_cb, pattern="^evt_")],
            EV_CATEGORY:[CallbackQueryHandler(ev_category_cb, pattern="^cat_")],
            EV_CATEGORY_CUSTOM:[MessageHandler(filters.TEXT & ~filters.COMMAND, ev_category_custom)],
            EV_FORMAT:[CallbackQueryHandler(ev_format_cb, pattern="^fmt_")],
            EV_VENUE:[CallbackQueryHandler(ev_venue_cb, pattern="^ven_")],
            EV_VENUE_CUSTOM:[MessageHandler(filters.TEXT & ~filters.COMMAND, ev_venue_custom)],
            EV_DATE_START:[MessageHandler(filters.TEXT & ~filters.COMMAND, ev_date_start), CommandHandler("skip", ev_date_start)],
            EV_DATE_END:[MessageHandler(filters.TEXT & ~filters.COMMAND, ev_date_end), CommandHandler("skip", ev_date_end)],
            EV_RULES:[MessageHandler(filters.TEXT & ~filters.COMMAND, ev_rules), CommandHandler("skip", ev_rules)],
            EV_CONFIRM:[CallbackQueryHandler(ev_confirm_cb, pattern="^(ev_create|back_main)$")],
            ADD_PARTICIPANT:[MessageHandler(filters.TEXT & ~filters.COMMAND, ap_name), CommandHandler("done", done_cmd)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_participants$")],
        states={
            ADD_PARTICIPANT:[CallbackQueryHandler(ap_event_cb, pattern="^ap_"), MessageHandler(filters.TEXT & ~filters.COMMAND, ap_name), CommandHandler("done", done_cmd)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_add_match$")],
        states={
            MATCH_EVENT:[CallbackQueryHandler(match_event_cb, pattern="^me_")],
            MATCH_HOME:[CallbackQueryHandler(match_home_cb, pattern="^mh_")],
            MATCH_AWAY:[CallbackQueryHandler(match_away_cb, pattern="^ma_")],
            MATCH_SCORE:[MessageHandler(filters.TEXT & ~filters.COMMAND, match_score_msg)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_finish$")],
        states={FINISH_CONFIRM:[CallbackQueryHandler(finish_cb, pattern="^fin_")]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_delete$")],
        states={DELETE_CONFIRM:[CallbackQueryHandler(delete_cb, pattern="^del_")]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_add_admin$")],
        states={ADD_ADMIN_ID:[MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_msg)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_reset$")],
        states={RESET_CONFIRM:[CallbackQueryHandler(reset_cb, pattern="^reset_yes$")]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")]
    ))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(standings_cb, pattern="^st_"))
    app.add_handler(CallbackQueryHandler(back_main, pattern="^back_main$"))

    print("✅ ShymATC-CHAMP bot v2 started")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
