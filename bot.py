import os, json, base64, requests, threading
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, MenuButtonCommands
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

BOT_TOKEN    = os.environ.get("BOT_TOKEN")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO  = "DaurenBol/ShymATC-CHAMP"
DATA_FILE    = "data.json"
BRANCH       = "main"

# ── CACHE ──
_cache = None
_cache_sha = None

def load_data():
    global _cache, _cache_sha
    if _cache is not None:
        data = json.loads(json.dumps(_cache))
        data["_sha"] = _cache_sha
        return data
    return _load_from_github()

def _load_from_github():
    global _cache, _cache_sha
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_FILE}"
    try:
        r = requests.get(url, headers=_gh(), timeout=10)
        if r.status_code == 200:
            c = r.json()
            data = json.loads(base64.b64decode(c["content"]).decode())
            _cache = json.loads(json.dumps(data))
            _cache_sha = c["sha"]
            data["_sha"] = c["sha"]
            return data
    except Exception as e:
        print(f"load error: {e}")
    return {"events": [], "admins": [2070550], "_sha": None}

def save_data(data):
    global _cache, _cache_sha
    sha = data.pop("_sha", None)
    _cache = json.loads(json.dumps(data))
    def _push():
        global _cache_sha
        enc = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode()
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_FILE}"
        payload = {"message": "update via bot", "content": enc, "branch": BRANCH}
        if sha: payload["sha"] = sha
        elif _cache_sha: payload["sha"] = _cache_sha
        try:
            r = requests.put(url, headers=_gh(), json=payload, timeout=15)
            if r.status_code in [200, 201]:
                _cache_sha = r.json().get("content", {}).get("sha")
                print("✅ Saved to GitHub")
            else:
                print(f"⚠️ Save failed: {r.status_code}")
        except Exception as e:
            print(f"save error: {e}")
    threading.Thread(target=_push, daemon=True).start()
    return True

# ── LOCK ──
_busy = set()
async def lock_cb(q):
    uid = q.from_user.id
    if uid in _busy:
        await q.answer("⏳ Подождите...")
        return False
    _busy.add(uid)
    await q.answer()
    return True
def unlock_cb(q): _busy.discard(q.from_user.id)

# ── STATES ──
(
    EV_NAME, EV_TYPE, EV_CATEGORY, EV_CATEGORY_CUSTOM,
    EV_FORMAT, EV_VENUE, EV_VENUE_CUSTOM,
    EV_DATE_START, EV_DATE_END, EV_RULES, EV_CONFIRM,
    ADD_PARTICIPANT,
    MATCH_EVENT, MATCH_HOME, MATCH_AWAY, MATCH_DATE, MATCH_SCORE,
    DELETE_CONFIRM, FINISH_CONFIRM,
    ADD_ADMIN_ID, RESET_CONFIRM,
    EDIT_SELECT, EDIT_EVENT_FIELD, EDIT_EVENT_VALUE,
    EDIT_MATCH_SELECT, EDIT_MATCH_FIELD, EDIT_MATCH_VALUE,
    EDIT_PARTICIPANT_OLD, EDIT_PARTICIPANT_NEW,
    REMOVE_PARTICIPANT_SELECT,
    DELETE_MATCH_SELECT, DELETE_MATCH_CONFIRM,
    DIRTY_EVENT, DIRTY_PLAYER, DIRTY_LABEL,
    TICKER_INPUT,
    GROUP_SELECT, GROUP_NAME,
    GROUP_DELETE_SELECT,
) = range(39)

# ── CONSTANTS ──
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
TYPE_LABELS = {
    "tournament":"Турнир","league":"Лига","championship":"Чемпионат",
    "cup":"Кубок","single":"Разовое","meetup":"Встреча",
    "training":"Тренировка","registration":"Регистрация","other":"Событие"
}
TICKER_MODES = [
    ("last_match",  "⚽ Последний матч"),
    ("leader",      "🏆 Лидер турнира"),
    ("top_scorer",  "🎯 Лучший бомбардир"),
    ("top_match",   "🔥 Самый результативный матч"),
    ("dirty",       "😈 Грязный игрок"),
]

# ── HELPERS ──
def _gh(): return {"Authorization":f"token {GITHUB_TOKEN}","Accept":"application/vnd.github.v3+json"}
def is_admin(uid, data): return uid in data.get("admins", [2070550])
def get_event(data, eid): return next((e for e in data["events"] if e["id"] == eid), None)
def gen_id(data):
    i = 1
    while f"ev_{i:03d}" in [e["id"] for e in data["events"]]: i += 1
    return f"ev_{i:03d}"
def lbl(items, val): return next((l for l, v in items if v == val), val)
def chunks(lst, n):
    for i in range(0, len(lst), n): yield lst[i:i+n]

def calc_standings(event):
    st = {p: {"name":p,"p":0,"w":0,"d":0,"l":0,"gf":0,"ga":0} for p in event["participants"]}
    for m in event.get("matches", []):
        if not m.get("played"): continue
        h, a, sh, sa = m["home"], m["away"], m["score_home"], m["score_away"]
        for name, gf, ga in [(h, sh, sa), (a, sa, sh)]:
            if name in st: st[name]["p"] += 1; st[name]["gf"] += gf; st[name]["ga"] += ga
        if sh > sa:
            if h in st: st[h]["w"] += 1
            if a in st: st[a]["l"] += 1
        elif sh < sa:
            if a in st: st[a]["w"] += 1
            if h in st: st[h]["l"] += 1
        else:
            if h in st: st[h]["d"] += 1
            if a in st: st[a]["d"] += 1
    rows = list(st.values())
    rows.sort(key=lambda x: (x["w"]*3+x["d"], x["gf"]-x["ga"]), reverse=True)
    return rows

# ── KEYBOARDS ──
def main_menu_kb(is_adm):
    kb = [[InlineKeyboardButton("📋 События", callback_data="menu_events"),
           InlineKeyboardButton("📊 Таблица", callback_data="menu_standings")],
          [InlineKeyboardButton("⚡ Матчи", callback_data="menu_matches")]]
    if is_adm:
        kb += [
            [InlineKeyboardButton("➕ Создать событие", callback_data="menu_new_event")],
            [InlineKeyboardButton("👤 Участники", callback_data="menu_participants"),
             InlineKeyboardButton("⚽ Добавить матч", callback_data="menu_add_match")],
            [InlineKeyboardButton("✏️ Редактировать", callback_data="menu_edit")],
            [InlineKeyboardButton("🏁 Завершить", callback_data="menu_finish"),
             InlineKeyboardButton("🗑 Удалить", callback_data="menu_delete")],
            [InlineKeyboardButton("😈 Грязный игрок", callback_data="menu_dirty_player"),
             InlineKeyboardButton("📢 Бегущая строка", callback_data="menu_ticker")],
            [InlineKeyboardButton("🔗 Объединить события", callback_data="menu_group")],
            [InlineKeyboardButton("👑 Добавить админа", callback_data="menu_add_admin"),
             InlineKeyboardButton("🔄 Сброс", callback_data="menu_reset")],
        ]
    return InlineKeyboardMarkup(kb)

def options_kb(items, prefix, cols=2):
    btns = [InlineKeyboardButton(l, callback_data=f"{prefix}{v}") for l, v in items]
    return InlineKeyboardMarkup(list(chunks(btns, cols)))

def back_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_main")]])
def skip_kb(cd): return InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить", callback_data=cd)]])
def done_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data="ap_done")]])
def home_inline_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 На главную", callback_data="back_main")]])

# ── BASE HANDLERS ──
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data(); adm = is_admin(update.effective_user.id, data)
    name = update.effective_user.first_name or "друг"
    await update.message.reply_text(
        f"👋 Привет, *{name}*!\n\n🏆 *ShymATC-CHAMP*",
        parse_mode="Markdown", reply_markup=main_menu_kb(adm))

async def show_menu(q, ctx):
    data = load_data(); adm = is_admin(q.from_user.id, data)
    await q.edit_message_text("🏆 *ShymATC-CHAMP*", parse_mode="Markdown", reply_markup=main_menu_kb(adm))

async def back_main(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return
    try: await show_menu(q, ctx)
    finally: unlock_cb(q)
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    data = load_data(); adm = is_admin(update.effective_user.id, data)
    await update.message.reply_text("❌ Отменено.", reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

# ── MENU HANDLER ──
async def menu_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return
    action = q.data.replace("menu_", "")
    data = load_data()
    try:
        # ── EVENTS ──
        if action == "events":
            if not data["events"]:
                await q.edit_message_text("📋 Событий нет.", reply_markup=back_kb()); return
            text = "📋 *События:*\n\n"
            for e in data["events"]:
                s = "🟢" if e.get("active") else "⚫"
                mp = len([m for m in e.get("matches", []) if m.get("played")])
                text += f"{s} *{e['name']}*\n{TYPE_LABELS.get(e.get('event_type',''),'')}\n👥 {len(e.get('participants',[]))} уч. · ⚽ {mp} матчей\n\n"
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())

        # ── STANDINGS ──
        elif action == "standings":
            active = [e for e in data["events"] if e.get("active")]
            if not active:
                await q.edit_message_text("Нет активных событий.", reply_markup=back_kb()); return
            if len(active) == 1:
                await _do_standings(q, active[0]); return
            kb = [[InlineKeyboardButton(e["name"], callback_data=f"st_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
            await q.edit_message_text("📊 Выбери событие:", reply_markup=InlineKeyboardMarkup(kb))

        # ── MATCHES ──
        elif action == "matches":
            all_m = [(e["name"], m) for e in data["events"] for m in e.get("matches", []) if m.get("played")]
            if not all_m:
                await q.edit_message_text("Матчей нет.", reply_markup=back_kb()); return
            text = "⚡ *Матчи:*\n\n"
            for ename, m in all_m[-10:]:
                sh, sa = m["score_home"], m["score_away"]
                res = f"🏆 {m['home']}" if sh > sa else (f"🏆 {m['away']}" if sa > sh else "🤝")
                ds = f" · {m.get('date','')}" if m.get('date') else ""
                text += f"*{m['home']}* {sh}:{sa} *{m['away']}*{ds}\n{res} · _{ename}_\n\n"
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())

        # ── NEW EVENT ──
        elif action == "new_event":
            if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
            ctx.user_data.clear()
            await q.edit_message_text("✏️ Введи *название события*:", parse_mode="Markdown")
            return EV_NAME

        # ── ADD MATCH ──
        elif action == "add_match":
            if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
            active = [e for e in data["events"] if e.get("active") and len(e.get("participants", [])) >= 2]
            if not active:
                await q.edit_message_text("Нет событий с участниками.", reply_markup=back_kb()); return
            if len(active) == 1:
                ctx.user_data["match"] = {"event_id": active[0]["id"]}
                kb = [[InlineKeyboardButton(p, callback_data=f"mh_{p}")] for p in active[0]["participants"]]
                await q.edit_message_text(f"⚽ *{active[0]['name']}*\nКто первый:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                return MATCH_HOME
            kb = [[InlineKeyboardButton(e["name"], callback_data=f"me_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
            await q.edit_message_text("⚽ Выбери событие:", reply_markup=InlineKeyboardMarkup(kb))
            return MATCH_EVENT

        # ── EDIT ──
        elif action == "edit":
            if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
            if not data["events"]:
                await q.edit_message_text("Событий нет.", reply_markup=back_kb()); return
            kb = [
                [InlineKeyboardButton("📌 Переименовать событие", callback_data="edit_ev_name")],
                [InlineKeyboardButton("⚽ Изменить счёт матча", callback_data="edit_match_score")],
                [InlineKeyboardButton("🗑 Удалить матч", callback_data="edit_delete_match")],
                [InlineKeyboardButton("👤 Переименовать участника", callback_data="edit_participant")],
                [InlineKeyboardButton("🎮 Команда участника", callback_data="edit_team")],
                [InlineKeyboardButton("🗑 Удалить участника", callback_data="edit_remove_participant")],
                [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
            ]
            await q.edit_message_text("✏️ *Что редактируем?*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return EDIT_SELECT

        # ── FINISH ──
        elif action == "finish":
            if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
            active = [e for e in data["events"] if e.get("active")]
            if not active:
                await q.edit_message_text("Нет активных событий.", reply_markup=back_kb()); return
            kb = [[InlineKeyboardButton(f"🏁 {e['name']}", callback_data=f"fin_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
            await q.edit_message_text("🏁 Какое завершить?", reply_markup=InlineKeyboardMarkup(kb))
            return FINISH_CONFIRM

        # ── DELETE ──
        elif action == "delete":
            if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
            if not data["events"]:
                await q.edit_message_text("Событий нет.", reply_markup=back_kb()); return
            kb = [[InlineKeyboardButton(f"🗑 {e['name']}", callback_data=f"del_{e['id']}")] for e in data["events"]]
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
            await q.edit_message_text("🗑 Какое удалить?", reply_markup=InlineKeyboardMarkup(kb))
            return DELETE_CONFIRM

        # ── DIRTY PLAYER ──
        elif action == "dirty_player":
            if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
            evs = [e for e in data["events"] if e.get("participants")]
            if not evs:
                await q.edit_message_text("Нет событий с участниками.", reply_markup=back_kb()); return
            kb = [[InlineKeyboardButton(e["name"], callback_data=f"dp_ev_{e['id']}")] for e in evs]
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
            await q.edit_message_text("😈 *Грязный игрок*\nВыбери событие:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return DIRTY_EVENT

        # ── TICKER ──
        elif action == "ticker":
            if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
            await _show_ticker_menu(q, data)
            return TICKER_INPUT

        # ── GROUP ──
        elif action == "group":
            if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
            if len(data.get("events", [])) < 2:
                await q.edit_message_text("Нужно минимум 2 события.", reply_markup=back_kb()); return
            groups = data.get("groups", [])
            kb_rows = []
            for e in data["events"]:
                kb_rows.append([InlineKeyboardButton(f"☐ {e['name']}", callback_data=f"grp_ev_{e['id']}")])
            if groups:
                kb_rows.append([InlineKeyboardButton("🗑 Удалить группу", callback_data="grp_delete")])
            kb_rows.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
            ctx.user_data["grp_selected"] = []
            current_groups = ""
            if groups:
                current_groups = "\n\nТекущие группы:\n" + "\n".join(f"· {g['name']}" for g in groups)
            await q.edit_message_text(
                f"🔗 *Объединить события*{current_groups}\n\nВыбери события для объединения:",
                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb_rows))
            return GROUP_SELECT

        # ── ADD ADMIN ──
        elif action == "add_admin":
            if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
            await q.edit_message_text("👑 Введи Telegram user_id нового админа:")
            return ADD_ADMIN_ID

        # ── RESET ──
        elif action == "reset":
            if not is_admin(q.from_user.id, data): await q.edit_message_text("⛔ Нет доступа."); return
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Да, очистить", callback_data="reset_yes"),
                InlineKeyboardButton("❌ Отмена", callback_data="back_main"),
            ]])
            await q.edit_message_text("⚠️ *Удалить все события?*", parse_mode="Markdown", reply_markup=kb)
            return RESET_CONFIRM
    finally:
        unlock_cb(q)

# ── STANDINGS ──
async def _do_standings(q, event):
    rows = calc_standings(event)
    medals = ["🥇","🥈","🥉"]
    text = f"📊 *{event['name']}*\n\n`# Участник       И  В  Н  П  ГЗ ГП  ±  Оч`\n`{'─'*42}`\n"
    for i, r in enumerate(rows):
        pts = r["w"]*3+r["d"]; gd = r["gf"]-r["ga"]
        rank = medals[i] if i < 3 else f"{i+1}."
        name = r["name"][:13].ljust(13)
        gd_str = ("+"+str(gd)) if gd >= 0 else str(gd)
        text += f"`{rank} {name} {r['p']:2} {r['w']:2} {r['d']:2} {r['l']:2} {r['gf']:3} {r['ga']:3} {gd_str:>4} {pts:3}`\n"
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())

async def standings_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return
    try:
        data = load_data(); event = get_event(data, q.data.replace("st_",""))
        if event: await _do_standings(q, event)
    finally: unlock_cb(q)

# ── CREATE EVENT ──
async def ev_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ev"] = {"name": update.message.text.strip()}
    await update.message.reply_text("📌 *Тип события:*", parse_mode="Markdown", reply_markup=options_kb(EVENT_TYPES, "evt_"))
    return EV_TYPE

async def ev_type_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return EV_TYPE
    try:
        ctx.user_data["ev"]["event_type"] = q.data.replace("evt_","")
        await q.edit_message_text("🎯 *Категория:*", parse_mode="Markdown", reply_markup=options_kb(CATEGORIES, "cat_"))
    finally: unlock_cb(q)
    return EV_CATEGORY

async def ev_category_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return EV_CATEGORY
    try:
        val = q.data.replace("cat_","")
        if val == "other_cat":
            await q.edit_message_text("✏️ Введи категорию:")
            return EV_CATEGORY_CUSTOM
        ctx.user_data["ev"]["category"] = val
        await q.edit_message_text("👥 *Формат участия:*", parse_mode="Markdown", reply_markup=options_kb(FORMATS, "fmt_", cols=1))
    finally: unlock_cb(q)
    return EV_FORMAT

async def ev_category_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ev"]["category"] = update.message.text.strip()
    await update.message.reply_text("👥 *Формат:*", parse_mode="Markdown", reply_markup=options_kb(FORMATS, "fmt_", cols=1))
    return EV_FORMAT

async def ev_format_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return EV_FORMAT
    try:
        ctx.user_data["ev"]["participation_format"] = q.data.replace("fmt_","")
        await q.edit_message_text("📍 *Площадка:*", parse_mode="Markdown", reply_markup=options_kb(VENUES, "ven_"))
    finally: unlock_cb(q)
    return EV_VENUE

async def ev_venue_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return EV_VENUE
    try:
        val = q.data.replace("ven_","")
        if val == "other_venue":
            await q.edit_message_text("✏️ Введи площадку:")
            return EV_VENUE_CUSTOM
        ctx.user_data["ev"]["venue_type"] = val
        await q.edit_message_text("📅 Дата начала (например: 01.04.2026)\nИли пропусти:", reply_markup=skip_kb("skip_date_start"))
    finally: unlock_cb(q)
    return EV_DATE_START

async def ev_venue_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ev"]["venue_type"] = update.message.text.strip()
    await update.message.reply_text("📅 Дата начала (например: 01.04.2026)\nИли пропусти:", reply_markup=skip_kb("skip_date_start"))
    return EV_DATE_START

async def ev_date_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ev"]["date_start"] = update.message.text.strip()
    await update.message.reply_text("📅 Дата окончания\nИли пропусти:", reply_markup=skip_kb("skip_date_end"))
    return EV_DATE_END

async def ev_date_end(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ev"]["date_end"] = update.message.text.strip()
    await update.message.reply_text("📋 Правила события\nИли пропусти:", reply_markup=skip_kb("skip_rules"))
    return EV_RULES

async def ev_rules(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ev"]["rules"] = update.message.text.strip()
    await _show_ev_confirm(update.message, ctx)
    return EV_CONFIRM

async def ev_skip_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "skip_date_start":
        ctx.user_data["ev"]["date_start"] = ""
        await q.edit_message_text("📅 Дата окончания\nИли пропусти:", reply_markup=skip_kb("skip_date_end"))
        return EV_DATE_END
    elif q.data == "skip_date_end":
        ctx.user_data["ev"]["date_end"] = ""
        await q.edit_message_text("📋 Правила события\nИли пропусти:", reply_markup=skip_kb("skip_rules"))
        return EV_RULES
    elif q.data == "skip_rules":
        ctx.user_data["ev"]["rules"] = ""
        await _show_ev_confirm(q, ctx)
        return EV_CONFIRM

async def _show_ev_confirm(target, ctx):
    ev = ctx.user_data["ev"]
    text = (f"✅ *Подтверди:*\n\n📌 *{ev['name']}*\n"
            f"🏷 {lbl(EVENT_TYPES, ev.get('event_type',''))}\n"
            f"🎯 {lbl(CATEGORIES, ev.get('category',''))}\n"
            f"👥 {lbl(FORMATS, ev.get('participation_format',''))}\n"
            f"📍 {lbl(VENUES, ev.get('venue_type',''))}\n")
    if ev.get("date_start"): text += f"📅 {ev['date_start']}"
    if ev.get("date_end"): text += f" → {ev['date_end']}\n"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Создать", callback_data="ev_create"),
        InlineKeyboardButton("❌ Отмена", callback_data="back_main"),
    ]])
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await target.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def ev_confirm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return EV_CONFIRM
    try:
        if q.data == "back_main":
            await show_menu(q, ctx); return ConversationHandler.END
        data = load_data(); ev = ctx.user_data["ev"]; eid = gen_id(data)
        event = {
            "id": eid, "name": ev["name"], "event_type": ev.get("event_type","other"),
            "category": ev.get("category","other"), "participation_format": ev.get("participation_format","individual"),
            "venue_type": ev.get("venue_type","offline"), "date_start": ev.get("date_start",""),
            "date_end": ev.get("date_end",""), "rules": ev.get("rules",""), "active": True,
            "participants": [], "participant_teams": {}, "matches": [],
            "created_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
        }
        data["events"].append(event)
        ok = save_data(data)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 Добавить участников", callback_data="menu_participants")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_main")],
        ])
        await q.edit_message_text(
            f"{'✅ Сохранено!' if ok else '⚠️ Ошибка'}\n\n🎉 *{ev['name']}* создано!",
            parse_mode="Markdown", reply_markup=kb)
    finally: unlock_cb(q)
    return ConversationHandler.END

# ── PARTICIPANTS ──
def _ap_text(event):
    ps = event["participants"]
    lines = "\n".join(f"· {p}" for p in ps) if ps else "_пока никого_"
    return (f"👤 *{event['name']}*\n\n{lines}\n\n"
            f"Вводи *ИМЕНА* по одному.\n"
            f"_Когда закончишь — нажми «На главную»_")

async def _ap_show(q, event, ctx=None):
    await q.edit_message_text(_ap_text(event), parse_mode="Markdown", reply_markup=home_inline_kb())
    if ctx is not None:
        ctx.user_data["ap_msg_id"] = q.message.message_id
        ctx.user_data["ap_chat_id"] = q.message.chat_id

async def ap_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return
    data = load_data()
    try:
        if not is_admin(q.from_user.id, data):
            await q.edit_message_text("⛔ Нет доступа."); return
        active = [e for e in data["events"] if e.get("active")]
        if not active:
            await q.edit_message_text("Нет активных событий.", reply_markup=back_kb()); return
        if len(active) == 1:
            ctx.user_data["ap_eid"] = active[0]["id"]
            ctx.user_data["ap_mode"] = True
            await _ap_show(q, active[0], ctx); return
        kb = [[InlineKeyboardButton(e["name"], callback_data=f"ap_{e['id']}")] for e in active]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
        await q.edit_message_text("👤 Выбери событие:", reply_markup=InlineKeyboardMarkup(kb))
    finally:
        unlock_cb(q)

async def ap_event_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return
    try:
        ctx.user_data["ap_eid"] = q.data.replace("ap_","")
        ctx.user_data["ap_mode"] = True
        data = load_data(); event = get_event(data, ctx.user_data["ap_eid"])
        await _ap_show(q, event, ctx)
    finally: unlock_cb(q)

async def ap_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.user_data.get("ap_mode"): return
    name = update.message.text.strip()
    data = load_data(); event = get_event(data, ctx.user_data.get("ap_eid"))
    if not event:
        ctx.user_data.pop("ap_mode", None)
        await update.message.reply_text("Ошибка. Начни заново."); return
    if name in event["participants"]:
        await update.message.reply_text(f"⚠️ *{name}* уже есть.", parse_mode="Markdown"); return
    event["participants"].append(name)
    event.setdefault("participant_teams", {})[name] = "не определено"
    save_data(data)
    data2 = load_data(); event2 = get_event(data2, ctx.user_data.get("ap_eid"))
    upd_text = _ap_text(event2) if event2 else f"✅ *{name}* добавлен"
    msg_id  = ctx.user_data.get("ap_msg_id")
    chat_id = ctx.user_data.get("ap_chat_id")
    if msg_id and chat_id:
        try:
            await ctx.bot.edit_message_text(upd_text, chat_id=chat_id, message_id=msg_id,
                parse_mode="Markdown", reply_markup=home_inline_kb()); return
        except Exception: pass
    msg = await update.message.reply_text(upd_text, parse_mode="Markdown", reply_markup=home_inline_kb())
    ctx.user_data["ap_msg_id"]  = msg.message_id
    ctx.user_data["ap_chat_id"] = msg.chat_id

async def ap_done_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data.pop("ap_mode", None)
    data = load_data(); adm = is_admin(q.from_user.id, data)
    await q.edit_message_text("✅ Участники сохранены!", reply_markup=main_menu_kb(adm))

# ── ADD MATCH ──
async def match_event_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return MATCH_EVENT
    try:
        ctx.user_data["match"] = {"event_id": q.data.replace("me_","")}
        data = load_data(); event = get_event(data, ctx.user_data["match"]["event_id"])
        kb = [[InlineKeyboardButton(p, callback_data=f"mh_{p}")] for p in event["participants"]]
        await q.edit_message_text(f"⚽ *{event['name']}*\nКто первый:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return MATCH_HOME

async def match_home_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return MATCH_HOME
    try:
        ctx.user_data["match"]["home"] = q.data.replace("mh_","")
        data = load_data(); event = get_event(data, ctx.user_data["match"]["event_id"])
        others = [p for p in event["participants"] if p != ctx.user_data["match"]["home"]]
        kb = [[InlineKeyboardButton(p, callback_data=f"ma_{p}")] for p in others]
        await q.edit_message_text("Кто второй:", reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return MATCH_AWAY

async def match_away_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return MATCH_AWAY
    try:
        ctx.user_data["match"]["away"] = q.data.replace("ma_","")
        today = datetime.now().strftime("%d.%m.%Y")
        h, a = ctx.user_data["match"]["home"], ctx.user_data["match"]["away"]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📅 Сегодня ({today})", callback_data=f"md_{today}")],
            [InlineKeyboardButton("✏️ Другая дата", callback_data="md_custom")],
        ])
        await q.edit_message_text(f"📅 Дата матча *{h}* vs *{a}*:", parse_mode="Markdown", reply_markup=kb)
    finally: unlock_cb(q)
    return MATCH_DATE

async def match_date_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return MATCH_DATE
    try:
        if q.data == "md_custom":
            await q.edit_message_text("📅 Введи дату (например: 01.04.2026):")
            return MATCH_DATE
        ctx.user_data["match"]["date"] = q.data.replace("md_","")
        h, a = ctx.user_data["match"]["home"], ctx.user_data["match"]["away"]
        await q.edit_message_text(f"Счёт *{h}* vs *{a}*\nФормат: `2:1`", parse_mode="Markdown")
    finally: unlock_cb(q)
    return MATCH_SCORE

async def match_date_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["match"]["date"] = update.message.text.strip()
    m = ctx.user_data["match"]
    await update.message.reply_text(f"Счёт *{m['home']}* vs *{m['away']}*\nФормат: `2:1`", parse_mode="Markdown")
    return MATCH_SCORE

async def match_score_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace("-",":").replace(" ","")
    if ":" not in text:
        await update.message.reply_text("Формат: `2:1`", parse_mode="Markdown"); return MATCH_SCORE
    try: sh, sa = map(int, text.split(":"))
    except:
        await update.message.reply_text("Формат: `2:1`", parse_mode="Markdown"); return MATCH_SCORE
    m = ctx.user_data["match"]; data = load_data(); event = get_event(data, m["event_id"])
    event["matches"].append({
        "home": m["home"], "away": m["away"],
        "score_home": sh, "score_away": sa,
        "played": True, "date": m.get("date", datetime.now().strftime("%d.%m.%Y")),
    })
    ok = save_data(data)
    res = f"🏆 *{m['home']}*" if sh > sa else (f"🏆 *{m['away']}*" if sa > sh else "🤝 Ничья")
    adm = is_admin(update.effective_user.id, data)
    await update.message.reply_text(
        f"{'✅ Результат сохранён!' if ok else '⚠️ Ошибка'}\n\n"
        f"*{m['home']}* {sh}:{sa} *{m['away']}*\n{res}\n📅 {m.get('date','')}",
        parse_mode="Markdown", reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

# ── EDIT ──
async def edit_select_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return EDIT_SELECT
    data = load_data()
    try:
        if q.data == "edit_ev_name":
            kb = [[InlineKeyboardButton(e["name"], callback_data=f"een_{e['id']}")] for e in data["events"]]
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
            await q.edit_message_text("📌 Выбери событие:", reply_markup=InlineKeyboardMarkup(kb))
            return EDIT_EVENT_FIELD
        elif q.data == "edit_match_score":
            active = [e for e in data["events"] if e.get("active")]
            if not active:
                await q.edit_message_text("Нет активных событий.", reply_markup=back_kb()); return ConversationHandler.END
            kb = [[InlineKeyboardButton(e["name"], callback_data=f"ems_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
            await q.edit_message_text("⚽ Выбери событие:", reply_markup=InlineKeyboardMarkup(kb))
            return EDIT_MATCH_SELECT
        elif q.data == "edit_participant":
            active = [e for e in data["events"] if e.get("active") and e.get("participants")]
            if not active:
                await q.edit_message_text("Нет событий с участниками.", reply_markup=back_kb()); return ConversationHandler.END
            kb = [[InlineKeyboardButton(e["name"], callback_data=f"ep_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
            await q.edit_message_text("👤 Выбери событие:", reply_markup=InlineKeyboardMarkup(kb))
            return EDIT_PARTICIPANT_OLD
        elif q.data == "edit_team":
            active = [e for e in data["events"] if e.get("active") and e.get("participants")]
            if not active:
                await q.edit_message_text("Нет событий с участниками.", reply_markup=back_kb()); return ConversationHandler.END
            kb = [[InlineKeyboardButton(e["name"], callback_data=f"etm_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
            await q.edit_message_text("🎮 Выбери событие:", reply_markup=InlineKeyboardMarkup(kb))
            return EDIT_PARTICIPANT_OLD
        elif q.data == "edit_remove_participant":
            active = [e for e in data["events"] if e.get("active") and e.get("participants")]
            if not active:
                await q.edit_message_text("Нет событий с участниками.", reply_markup=back_kb()); return ConversationHandler.END
            kb = [[InlineKeyboardButton(e["name"], callback_data=f"erp_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
            await q.edit_message_text("🗑 Выбери событие:", reply_markup=InlineKeyboardMarkup(kb))
            return REMOVE_PARTICIPANT_SELECT
        elif q.data == "edit_delete_match":
            active = [e for e in data["events"] if e.get("active") and any(m.get("played") for m in e.get("matches",[]))]
            if not active:
                await q.edit_message_text("Нет событий с матчами.", reply_markup=back_kb()); return ConversationHandler.END
            kb = [[InlineKeyboardButton(e["name"], callback_data=f"edm_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
            await q.edit_message_text("🗑 Выбери событие:", reply_markup=InlineKeyboardMarkup(kb))
            return DELETE_MATCH_SELECT
    finally: unlock_cb(q)

async def edit_event_name_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return EDIT_EVENT_FIELD
    try:
        ctx.user_data["edit_eid"] = q.data.replace("een_","")
        data = load_data(); event = get_event(data, ctx.user_data["edit_eid"])
        await q.edit_message_text(f"Текущее: *{event['name']}*\n\nВведи новое название:", parse_mode="Markdown")
    finally: unlock_cb(q)
    return EDIT_EVENT_VALUE

async def edit_event_name_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    data = load_data(); event = get_event(data, ctx.user_data["edit_eid"])
    old_name = event["name"]; event["name"] = new_name
    ok = save_data(data); adm = is_admin(update.effective_user.id, data)
    await update.message.reply_text(
        f"{'✅ Переименовано!' if ok else '⚠️ Ошибка'}\n\n*{old_name}* → *{new_name}*",
        parse_mode="Markdown", reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

async def edit_match_event_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return EDIT_MATCH_SELECT
    try:
        ctx.user_data["edit_eid"] = q.data.replace("ems_","")
        data = load_data(); event = get_event(data, ctx.user_data["edit_eid"])
        ps = set(event.get("participants",[]))
        played = [(i,m) for i,m in enumerate(event.get("matches",[])) if m.get("played") and m.get("home") in ps and m.get("away") in ps]
        if not played:
            await q.edit_message_text("Нет сыгранных матчей.", reply_markup=back_kb()); return ConversationHandler.END
        kb = [[InlineKeyboardButton(
            f"{m['home']} {m['score_home']}:{m['score_away']} {m['away']} ({m.get('date','')})",
            callback_data=f"emm_{i}")] for i,m in played]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
        await q.edit_message_text("⚽ Выбери матч:", reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return EDIT_MATCH_FIELD

async def edit_match_select_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return EDIT_MATCH_FIELD
    try:
        idx = int(q.data.replace("emm_",""))
        ctx.user_data["edit_match_idx"] = idx
        data = load_data(); event = get_event(data, ctx.user_data["edit_eid"])
        m = event["matches"][idx]
        await q.edit_message_text(
            f"Матч: *{m['home']}* {m['score_home']}:{m['score_away']} *{m['away']}*\n\nВведи новый счёт (формат: `2:1`):",
            parse_mode="Markdown")
    finally: unlock_cb(q)
    return EDIT_MATCH_VALUE

async def edit_match_score_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace("-",":").replace(" ","")
    if ":" not in text:
        await update.message.reply_text("Формат: `2:1`", parse_mode="Markdown"); return EDIT_MATCH_VALUE
    try: sh, sa = map(int, text.split(":"))
    except:
        await update.message.reply_text("Формат: `2:1`", parse_mode="Markdown"); return EDIT_MATCH_VALUE
    data = load_data(); event = get_event(data, ctx.user_data["edit_eid"])
    idx = ctx.user_data["edit_match_idx"]; m = event["matches"][idx]
    old = f"{m['score_home']}:{m['score_away']}"; m["score_home"] = sh; m["score_away"] = sa
    ok = save_data(data); adm = is_admin(update.effective_user.id, data)
    await update.message.reply_text(
        f"{'✅ Счёт изменён!' if ok else '⚠️ Ошибка'}\n\n*{m['home']}* {old} → {sh}:{sa} *{m['away']}*",
        parse_mode="Markdown", reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

async def edit_participant_event_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return EDIT_PARTICIPANT_OLD
    try:
        ctx.user_data["edit_eid"] = q.data.replace("ep_","")
        data = load_data(); event = get_event(data, ctx.user_data["edit_eid"])
        kb = [[InlineKeyboardButton(p, callback_data=f"epp_{p}")] for p in event["participants"]]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
        await q.edit_message_text("👤 Выбери участника:", reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return EDIT_PARTICIPANT_NEW

async def edit_participant_select_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return EDIT_PARTICIPANT_NEW
    try:
        pname = q.data.replace("epp_","")
        ctx.user_data["edit_participant_old"] = pname
        if ctx.user_data.get("edit_mode") == "team":
            data = load_data(); event = get_event(data, ctx.user_data["edit_eid"])
            current_team = event.get("participant_teams",{}).get(pname,"не определено")
            await q.edit_message_text(
                f"👤 *{pname}*\nТекущая команда: *{current_team}*\n\nВведи новое название команды:",
                parse_mode="Markdown")
        else:
            await q.edit_message_text(f"Текущее: *{pname}*\n\nВведи новое имя:", parse_mode="Markdown")
    finally: unlock_cb(q)
    return EDIT_PARTICIPANT_NEW

async def edit_participant_new_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    old_name = ctx.user_data["edit_participant_old"]
    data = load_data(); event = get_event(data, ctx.user_data["edit_eid"])
    adm = is_admin(update.effective_user.id, data)
    if ctx.user_data.get("edit_mode") == "team":
        event.setdefault("participant_teams", {})[old_name] = value
        ok = save_data(data)
        await update.message.reply_text(
            f"{'✅ Команда обновлена!' if ok else '⚠️ Ошибка'}\n\n👤 *{old_name}* · 🎮 *{value}*",
            parse_mode="Markdown", reply_markup=main_menu_kb(adm))
    else:
        new_name = value
        if old_name in event["participants"]:
            event["participants"][event["participants"].index(old_name)] = new_name
        for m in event.get("matches",[]):
            if m.get("home") == old_name: m["home"] = new_name
            if m.get("away") == old_name: m["away"] = new_name
        teams = event.get("participant_teams", {})
        if old_name in teams:
            teams[new_name] = teams.pop(old_name)
        ok = save_data(data)
        await update.message.reply_text(
            f"{'✅ Имя изменено!' if ok else '⚠️ Ошибка'}\n\n*{old_name}* → *{new_name}*",
            parse_mode="Markdown", reply_markup=main_menu_kb(adm))
    ctx.user_data.pop("edit_mode", None)
    return ConversationHandler.END

async def edit_team_event_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return EDIT_PARTICIPANT_OLD
    try:
        ctx.user_data["edit_eid"] = q.data.replace("etm_","")
        ctx.user_data["edit_mode"] = "team"
        data = load_data(); event = get_event(data, ctx.user_data["edit_eid"])
        teams = event.get("participant_teams", {})
        kb = [[InlineKeyboardButton(
            f"{p} · {teams.get(p,'не определено')}",
            callback_data=f"epp_{p}")] for p in event["participants"]]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
        await q.edit_message_text("🎮 Выбери участника:", reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return EDIT_PARTICIPANT_NEW

async def remove_participant_event_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return REMOVE_PARTICIPANT_SELECT
    try:
        ctx.user_data["edit_eid"] = q.data.replace("erp_","")
        data = load_data(); event = get_event(data, ctx.user_data["edit_eid"])
        kb = [[InlineKeyboardButton(f"🗑 {p}", callback_data=f"rpp_{p}")] for p in event["participants"]]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
        await q.edit_message_text("🗑 Выбери участника для удаления:", reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return REMOVE_PARTICIPANT_SELECT

async def remove_participant_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return REMOVE_PARTICIPANT_SELECT
    try:
        name = q.data.replace("rpp_",""); data = load_data()
        event = get_event(data, ctx.user_data["edit_eid"])
        event["participants"] = [p for p in event["participants"] if p != name]
        event.get("participant_teams", {}).pop(name, None)
        before = len(event.get("matches",[]))
        event["matches"] = [m for m in event.get("matches",[]) if m.get("home") != name and m.get("away") != name]
        removed = before - len(event["matches"])
        ok = save_data(data); adm = is_admin(q.from_user.id, data)
        extra = f"\nУдалено матчей: {removed}" if removed > 0 else ""
        await q.edit_message_text(
            f"{'✅ Участник удалён!' if ok else '⚠️ Ошибка'}\n\n*{name}* удалён.{extra}",
            parse_mode="Markdown", reply_markup=main_menu_kb(adm))
    finally: unlock_cb(q)
    return ConversationHandler.END

async def delete_match_event_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return DELETE_MATCH_SELECT
    try:
        eid = q.data.replace("edm_",""); ctx.user_data["edit_eid"] = eid
        data = load_data(); event = get_event(data, eid)
        ps = set(event.get("participants",[]))
        valid = [(i,m) for i,m in enumerate(event.get("matches",[])) if m.get("played") and m.get("home") in ps and m.get("away") in ps]
        if not valid:
            await q.edit_message_text("Нет сыгранных матчей.", reply_markup=back_kb()); return ConversationHandler.END
        kb = [[InlineKeyboardButton(
            f"{m['home']} {m['score_home']}:{m['score_away']} {m['away']} ({m.get('date','')})",
            callback_data=f"dmc_{i}")] for i,m in valid]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
        await q.edit_message_text("🗑 Выбери матч для удаления:", reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return DELETE_MATCH_CONFIRM

async def delete_match_confirm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return DELETE_MATCH_CONFIRM
    try:
        idx = int(q.data.replace("dmc_",""))
        data = load_data(); event = get_event(data, ctx.user_data["edit_eid"])
        m = event["matches"][idx]
        label = f"*{m['home']}* {m['score_home']}:{m['score_away']} *{m['away']}* ({m.get('date','')})"
        event["matches"].pop(idx)
        ok = save_data(data); adm = is_admin(q.from_user.id, data)
        await q.edit_message_text(
            f"{'✅ Матч удалён!' if ok else '⚠️ Ошибка'}\n\n{label}",
            parse_mode="Markdown", reply_markup=main_menu_kb(adm))
    finally: unlock_cb(q)
    return ConversationHandler.END

# ── FINISH / DELETE ──
async def finish_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return FINISH_CONFIRM
    try:
        data = load_data(); event = get_event(data, q.data.replace("fin_",""))
        if event:
            event["active"] = False; save_data(data)
            await _do_standings(q, event)
    finally: unlock_cb(q)
    return ConversationHandler.END

async def delete_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return DELETE_CONFIRM
    try:
        data = load_data(); event = get_event(data, q.data.replace("del_",""))
        if event:
            name = event["name"]
            data["events"] = [e for e in data["events"] if e["id"] != event["id"]]
            ok = save_data(data); adm = is_admin(q.from_user.id, data)
            await q.edit_message_text(
                f"{'✅ Удалено!' if ok else '⚠️ Ошибка'}\n\n*{name}* удалено.",
                parse_mode="Markdown", reply_markup=main_menu_kb(adm))
    finally: unlock_cb(q)
    return ConversationHandler.END

# ── DIRTY PLAYER ──
async def dirty_event_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return DIRTY_EVENT
    try:
        eid = q.data.replace("dp_ev_",""); ctx.user_data["dirty_eid"] = eid
        data = load_data(); event = get_event(data, eid)
        current = event.get("dirty_player","")
        current_txt = f"\nСейчас: 😈 *{current}*" if current else "\nСейчас: не установлен"
        kb = [[InlineKeyboardButton(f"👤 {p}", callback_data=f"dp_pl_{p}")] for p in event["participants"]]
        if current:
            kb.append([InlineKeyboardButton("🗑 Убрать", callback_data="dp_pl_CLEAR")])
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
        await q.edit_message_text(
            f"😈 *{event['name']}*{current_txt}\n\nВыбери игрока:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return DIRTY_PLAYER

async def dirty_player_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return DIRTY_PLAYER
    try:
        name = q.data.replace("dp_pl_","")
        data = load_data(); event = get_event(data, ctx.user_data["dirty_eid"])
        adm = is_admin(q.from_user.id, data)
        if name == "CLEAR":
            event.pop("dirty_player", None); event.pop("dirty_label", None)
            ok = save_data(data)
            await q.edit_message_text(
                f"{'✅ Убрано!' if ok else '⚠️ Ошибка'}\n\n*{event['name']}*: карточка убрана.",
                parse_mode="Markdown", reply_markup=main_menu_kb(adm))
            return ConversationHandler.END
        ctx.user_data["dirty_name"] = name
        current_label = event.get("dirty_label", "Грязный игрок")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Оставить текущее название", callback_data="dirty_lbl_keep")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
        ])
        await q.edit_message_text(
            f"👤 Игрок: *{name}*\n\nВведи название карточки\n_например: Грязный игрок, Лучший дриблёр, Антигерой_\n\nСейчас: *{current_label}*",
            parse_mode="Markdown", reply_markup=kb)
    finally: unlock_cb(q)
    return DIRTY_LABEL

async def dirty_label_keep_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return DIRTY_LABEL
    try:
        name = ctx.user_data.get("dirty_name","")
        data = load_data(); event = get_event(data, ctx.user_data["dirty_eid"])
        adm = is_admin(q.from_user.id, data)
        event["dirty_player"] = name
        ok = save_data(data)
        lbl_text = event.get("dirty_label","Грязный игрок")
        await q.edit_message_text(
            f"{'✅ Сохранено!' if ok else '⚠️ Ошибка'}\n\n*{lbl_text}*: *{name}*",
            parse_mode="Markdown", reply_markup=main_menu_kb(adm))
    finally: unlock_cb(q)
    return ConversationHandler.END

async def dirty_label_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    name = ctx.user_data.get("dirty_name","")
    data = load_data(); event = get_event(data, ctx.user_data["dirty_eid"])
    adm = is_admin(update.effective_user.id, data)
    event["dirty_player"] = name; event["dirty_label"] = text
    ok = save_data(data)
    await update.message.reply_text(
        f"{'✅ Сохранено!' if ok else '⚠️ Ошибка'}\n\n*{text}*: *{name}*",
        parse_mode="Markdown", reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

# ── TICKER ──
def _build_templates(data):
    templates = []
    active = [e for e in data.get('events',[]) if e.get('active')]
    ev = active[0] if active else None
    if ev:
        ps = set(ev.get('participants',[]))
        played = [m for m in ev.get('matches',[]) if m.get('played') and m.get('home') in ps and m.get('away') in ps]
        rows = calc_standings(ev)
        if rows:
            leader = rows[0]; pts = leader['w']*3+leader['d']
            templates.append(f"🏆 ЛИДЕР: {leader['name'].upper()} — {pts} ОЧКОВ В {ev['name'].upper()}!")
        if played:
            last = played[-1]
            res = f"{last['home'].upper()} ПОБЕДИЛ" if last['score_home'] > last['score_away'] else (f"{last['away'].upper()} ПОБЕДИЛ" if last['score_away'] > last['score_home'] else "НИЧЬЯ")
            templates.append(f"⚽ {last['home'].upper()} {last['score_home']}:{last['score_away']} {last['away'].upper()} — {res}")
        if rows:
            top = max(rows, key=lambda r: r['gf'])
            if top['gf'] > 0:
                templates.append(f"🎯 БОМБАРДИР: {top['name'].upper()} — {top['gf']} ГОЛОВ")
        if played:
            top_m = max(played, key=lambda m: m['score_home']+m['score_away'])
            templates.append(f"🔥 ТОП МАТЧ: {top_m['home'].upper()} {top_m['score_home']}:{top_m['score_away']} {top_m['away'].upper()}")
        dirty = ev.get('dirty_player')
        if dirty:
            dirty_lbl = ev.get('dirty_label','ГРЯЗНЫЙ ИГРОК')
            templates.append(f"😈 {dirty_lbl.upper()}: {dirty.upper()}")
    if not templates:
        templates = ["🏆 ДОБРО ПОЖАЛОВАТЬ В SHYMATC-CHAMP!", "⚽ СЛЕДИТЕ ЗА РЕЗУЛЬТАТАМИ НА САЙТЕ!"]
    return templates

async def _show_ticker_menu(q, data):
    mode = data.get('ticker_mode','')
    custom = data.get('ticker','')
    if mode == 'custom' and custom:
        current_txt = f"\nСейчас (свой текст): _{custom}_"
    elif mode:
        mode_lbl = next((l for m,l in TICKER_MODES if m == mode), mode)
        current_txt = f"\nСейчас (авто): {mode_lbl}"
    else:
        current_txt = "\nСейчас: не установлена"
    kb = []
    for m, l in TICKER_MODES:
        active = "✅ " if m == mode else ""
        kb.append([InlineKeyboardButton(f"{active}{l}", callback_data=f"tkr_{m}")])
    kb.append([InlineKeyboardButton("✏️ Свой текст", callback_data="tkr_custom")])
    if mode:
        kb.append([InlineKeyboardButton("🗑 Убрать строку", callback_data="tkr_clear")])
    kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
    await q.edit_message_text(
        f"📢 *Бегущая строка*{current_txt}\n\nАвто-режим обновляется при каждом матче:",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def ticker_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return TICKER_INPUT
    try:
        data = load_data()
        if q.data == "tkr_clear":
            data.pop('ticker', None); data.pop('ticker_mode', None)
            ok = save_data(data); adm = is_admin(q.from_user.id, data)
            await q.edit_message_text(f"{'✅ Убрана!' if ok else '⚠️ Ошибка'}", reply_markup=main_menu_kb(adm))
            return ConversationHandler.END
        elif q.data == "tkr_custom":
            await q.edit_message_text("✏️ Введи текст бегущей строки:\n\n_Пример: СЛЕДУЮЩИЙ ТУР 10.04.2026_", parse_mode="Markdown")
            return TICKER_INPUT
        elif q.data.startswith("tkr_"):
            mode = q.data.replace("tkr_","")
            if mode in [m for m,l in TICKER_MODES]:
                data['ticker_mode'] = mode; data.pop('ticker', None)
                ok = save_data(data); adm = is_admin(q.from_user.id, data)
                mode_lbl = next((l for m,l in TICKER_MODES if m == mode), mode)
                await q.edit_message_text(
                    f"{'✅ Установлен авто-режим!' if ok else '⚠️ Ошибка'}\n\n📢 {mode_lbl}",
                    parse_mode="Markdown", reply_markup=main_menu_kb(adm))
                return ConversationHandler.END
    finally: unlock_cb(q)
    return TICKER_INPUT

async def ticker_text_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    data = load_data(); data['ticker'] = text; data['ticker_mode'] = 'custom'
    ok = save_data(data); adm = is_admin(update.effective_user.id, data)
    await update.message.reply_text(
        f"{'✅ Установлена!' if ok else '⚠️ Ошибка'}\n\n📢 _{text}_",
        parse_mode="Markdown", reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

# ── ADD ADMIN / RESET ──
# ── GROUP ──
async def grp_event_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return GROUP_SELECT
    try:
        data = load_data()
        eid = q.data.replace("grp_ev_","")
        selected = ctx.user_data.setdefault("grp_selected", [])
        if eid in selected:
            selected.remove(eid)
        else:
            selected.append(eid)
        kb_rows = []
        for e in data["events"]:
            mark = "☑" if e["id"] in selected else "☐"
            kb_rows.append([InlineKeyboardButton(f"{mark} {e['name']}", callback_data=f"grp_ev_{e['id']}")])
        if len(selected) >= 2:
            kb_rows.append([InlineKeyboardButton("✅ Объединить", callback_data="grp_confirm")])
        kb_rows.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
        await q.edit_message_text(
            f"🔗 *Объединить события*\n\nВыбрано: {len(selected)}\nНажми «Объединить» когда выберешь нужные:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb_rows))
    finally: unlock_cb(q)
    return GROUP_SELECT

async def grp_confirm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return GROUP_SELECT
    try:
        selected = ctx.user_data.get("grp_selected", [])
        if len(selected) < 2:
            await q.answer("Выбери минимум 2 события"); return GROUP_SELECT
        data = load_data()
        names = [e["name"] for e in data["events"] if e["id"] in selected]
        auto_name = " + ".join(names) + " — Общая"
        ctx.user_data["grp_auto_name"] = auto_name
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Использовать: {auto_name[:30]}...", callback_data="grp_use_auto")],
            [InlineKeyboardButton("✏️ Своё название", callback_data="grp_custom_name")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
        ])
        events_list = "\n".join(f"· {n}" for n in names)
        await q.edit_message_text(
            f"🔗 Выбрано событий: {len(selected)}\n\n{events_list}\n\nКак назвать группу?",
            parse_mode="Markdown", reply_markup=kb)
    finally: unlock_cb(q)
    return GROUP_NAME

async def grp_use_auto_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return GROUP_NAME
    try:
        name = ctx.user_data.get("grp_auto_name", "Общая группа")
        await _save_group(q, ctx, name)
    finally: unlock_cb(q)
    return ConversationHandler.END

async def grp_custom_name_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return GROUP_NAME
    try:
        await q.edit_message_text("✏️ Введи название группы:")
    finally: unlock_cb(q)
    return GROUP_NAME

async def grp_name_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    await _save_group_msg(update, ctx, name)
    return ConversationHandler.END

async def _save_group(q, ctx, name):
    data = load_data()
    selected = ctx.user_data.get("grp_selected", [])
    groups = data.setdefault("groups", [])
    i = 1
    while f"grp_{i:03d}" in [g["id"] for g in groups]: i += 1
    groups.append({"id": f"grp_{i:03d}", "name": name, "event_ids": selected})
    ok = save_data(data); adm = is_admin(q.from_user.id, data)
    await q.edit_message_text(
        f"{'✅ Группа создана!' if ok else '⚠️ Ошибка'}\n\n🔗 *{name}*",
        parse_mode="Markdown", reply_markup=main_menu_kb(adm))

async def _save_group_msg(update, ctx, name):
    data = load_data()
    selected = ctx.user_data.get("grp_selected", [])
    groups = data.setdefault("groups", [])
    i = 1
    while f"grp_{i:03d}" in [g["id"] for g in groups]: i += 1
    groups.append({"id": f"grp_{i:03d}", "name": name, "event_ids": selected})
    ok = save_data(data); adm = is_admin(update.effective_user.id, data)
    await update.message.reply_text(
        f"{'✅ Группа создана!' if ok else '⚠️ Ошибка'}\n\n🔗 *{name}*",
        parse_mode="Markdown", reply_markup=main_menu_kb(adm))

async def grp_delete_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return GROUP_SELECT
    try:
        data = load_data()
        groups = data.get("groups", [])
        if not groups:
            await q.edit_message_text("Групп нет.", reply_markup=back_kb()); return ConversationHandler.END
        kb = [[InlineKeyboardButton(f"🗑 {g['name']}", callback_data=f"grp_del_{g['id']}")] for g in groups]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
        await q.edit_message_text("🗑 Выбери группу для удаления:", reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return GROUP_DELETE_SELECT

async def grp_del_confirm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return GROUP_DELETE_SELECT
    try:
        gid = q.data.replace("grp_del_","")
        data = load_data()
        name = next((g["name"] for g in data.get("groups",[]) if g["id"]==gid), "")
        data["groups"] = [g for g in data.get("groups",[]) if g["id"] != gid]
        ok = save_data(data); adm = is_admin(q.from_user.id, data)
        await q.edit_message_text(
            f"{'✅ Группа удалена!' if ok else '⚠️ Ошибка'}\n\n*{name}*",
            parse_mode="Markdown", reply_markup=main_menu_kb(adm))
    finally: unlock_cb(q)
    return ConversationHandler.END

async def add_admin_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: new_id = int(update.message.text.strip())
    except:
        await update.message.reply_text("Введи числовой ID."); return ADD_ADMIN_ID
    data = load_data()
    if new_id not in data["admins"]:
        data["admins"].append(new_id); ok = save_data(data)
        await update.message.reply_text(f"{'✅ Добавлен!' if ok else '⚠️ Ошибка'}\n\nID: {new_id}", reply_markup=main_menu_kb(True))
    else:
        await update.message.reply_text("Уже админ.", reply_markup=main_menu_kb(True))
    return ConversationHandler.END

async def reset_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await lock_cb(q): return RESET_CONFIRM
    try:
        if q.data == "reset_yes":
            data = load_data()
            ok = save_data({"events":[], "admins":data.get("admins",[2070550])})
            await q.edit_message_text(f"{'✅ Все данные очищены!' if ok else '⚠️ Ошибка'}", reply_markup=main_menu_kb(True))
        else:
            await show_menu(q, ctx)
    finally: unlock_cb(q)
    return ConversationHandler.END

# ── MAIN ──
def main():
    async def post_init(app):
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        await app.bot.set_my_commands([
            BotCommand("start", "Главное меню"),
            BotCommand("cancel", "Отменить действие"),
        ])

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # ── CONVERSATION HANDLERS ──
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_new_event$")],
        states={
            EV_NAME:           [MessageHandler(filters.TEXT & ~filters.COMMAND, ev_name)],
            EV_TYPE:           [CallbackQueryHandler(ev_type_cb, pattern="^evt_")],
            EV_CATEGORY:       [CallbackQueryHandler(ev_category_cb, pattern="^cat_")],
            EV_CATEGORY_CUSTOM:[MessageHandler(filters.TEXT & ~filters.COMMAND, ev_category_custom)],
            EV_FORMAT:         [CallbackQueryHandler(ev_format_cb, pattern="^fmt_")],
            EV_VENUE:          [CallbackQueryHandler(ev_venue_cb, pattern="^ven_")],
            EV_VENUE_CUSTOM:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ev_venue_custom)],
            EV_DATE_START:     [MessageHandler(filters.TEXT & ~filters.COMMAND, ev_date_start),
                                CallbackQueryHandler(ev_skip_cb, pattern="^skip_date_start$")],
            EV_DATE_END:       [MessageHandler(filters.TEXT & ~filters.COMMAND, ev_date_end),
                                CallbackQueryHandler(ev_skip_cb, pattern="^skip_date_end$")],
            EV_RULES:          [MessageHandler(filters.TEXT & ~filters.COMMAND, ev_rules),
                                CallbackQueryHandler(ev_skip_cb, pattern="^skip_rules$")],
            EV_CONFIRM:        [CallbackQueryHandler(ev_confirm_cb, pattern="^(ev_create|back_main)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_add_match$")],
        states={
            MATCH_EVENT: [CallbackQueryHandler(match_event_cb, pattern="^me_")],
            MATCH_HOME:  [CallbackQueryHandler(match_home_cb,  pattern="^mh_")],
            MATCH_AWAY:  [CallbackQueryHandler(match_away_cb,  pattern="^ma_")],
            MATCH_DATE:  [CallbackQueryHandler(match_date_cb,  pattern="^md_"),
                          MessageHandler(filters.TEXT & ~filters.COMMAND, match_date_msg)],
            MATCH_SCORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, match_score_msg)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_edit$")],
        states={
            EDIT_SELECT:             [CallbackQueryHandler(edit_select_cb,            pattern="^edit_")],
            EDIT_EVENT_FIELD:        [CallbackQueryHandler(edit_event_name_cb,        pattern="^een_")],
            EDIT_EVENT_VALUE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_event_name_value)],
            EDIT_MATCH_SELECT:       [CallbackQueryHandler(edit_match_event_cb,       pattern="^ems_")],
            EDIT_MATCH_FIELD:        [CallbackQueryHandler(edit_match_select_cb,      pattern="^emm_")],
            EDIT_MATCH_VALUE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_match_score_value)],
            EDIT_PARTICIPANT_OLD:    [CallbackQueryHandler(edit_team_event_cb,        pattern="^etm_"),
                                      CallbackQueryHandler(edit_participant_event_cb, pattern="^ep_")],
            EDIT_PARTICIPANT_NEW:    [CallbackQueryHandler(edit_participant_select_cb,pattern="^epp_"),
                                      MessageHandler(filters.TEXT & ~filters.COMMAND, edit_participant_new_value)],
            REMOVE_PARTICIPANT_SELECT:[CallbackQueryHandler(remove_participant_event_cb, pattern="^erp_"),
                                       CallbackQueryHandler(remove_participant_cb,      pattern="^rpp_")],
            DELETE_MATCH_SELECT:     [CallbackQueryHandler(delete_match_event_cb,    pattern="^edm_")],
            DELETE_MATCH_CONFIRM:    [CallbackQueryHandler(delete_match_confirm_cb,  pattern="^dmc_")],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_finish$")],
        states={FINISH_CONFIRM: [CallbackQueryHandler(finish_cb, pattern="^fin_")]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_delete$")],
        states={DELETE_CONFIRM: [CallbackQueryHandler(delete_cb, pattern="^del_")]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_add_admin$")],
        states={ADD_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_msg)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_reset$")],
        states={RESET_CONFIRM: [CallbackQueryHandler(reset_cb, pattern="^reset_yes$")]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_dirty_player$")],
        states={
            DIRTY_EVENT:  [CallbackQueryHandler(dirty_event_cb,      pattern="^dp_ev_")],
            DIRTY_PLAYER: [CallbackQueryHandler(dirty_player_cb,     pattern="^dp_pl_")],
            DIRTY_LABEL:  [CallbackQueryHandler(dirty_label_keep_cb, pattern="^dirty_lbl_keep$"),
                           MessageHandler(filters.TEXT & ~filters.COMMAND, dirty_label_msg)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_group$")],
        states={
            GROUP_SELECT: [
                CallbackQueryHandler(grp_event_cb,   pattern="^grp_ev_"),
                CallbackQueryHandler(grp_confirm_cb, pattern="^grp_confirm$"),
                CallbackQueryHandler(grp_delete_cb,  pattern="^grp_delete$"),
            ],
            GROUP_NAME: [
                CallbackQueryHandler(grp_use_auto_cb,    pattern="^grp_use_auto$"),
                CallbackQueryHandler(grp_custom_name_cb, pattern="^grp_custom_name$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, grp_name_msg),
            ],
            GROUP_DELETE_SELECT: [
                CallbackQueryHandler(grp_del_confirm_cb, pattern="^grp_del_"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_ticker$")],
        states={
            TICKER_INPUT: [CallbackQueryHandler(ticker_cb, pattern="^tkr_"),
                           MessageHandler(filters.TEXT & ~filters.COMMAND, ticker_text_msg)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main, pattern="^back_main$")],
    ))

    # ── GLOBAL HANDLERS ──
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(ap_start,     pattern="^menu_participants$"))
    app.add_handler(CallbackQueryHandler(ap_event_cb,  pattern="^ap_"))
    app.add_handler(CallbackQueryHandler(ap_done_cb,   pattern="^ap_done$"))
    app.add_handler(CallbackQueryHandler(standings_cb, pattern="^st_"))
    app.add_handler(CallbackQueryHandler(back_main,    pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(menu_handler, pattern="^menu_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ap_name))

    print("✅ ShymATC-CHAMP bot v5.5 started")
    _load_from_github()
    print(f"📦 Cache: {len(_cache.get('events',[]))} events")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
