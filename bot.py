import os, json, base64, requests, asyncio, threading
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
        r = requests.get(url, headers=gh(), timeout=10)
        if r.status_code == 200:
            c = r.json()
            data = json.loads(base64.b64decode(c["content"]).decode())
            _cache = json.loads(json.dumps(data))
            _cache_sha = c["sha"]
            data["_sha"] = c["sha"]
            return data
    except Exception as e:
        print(f"load:{e}")
    return {"events": [], "admins": [2070550], "_sha": None}

def save_data(data):
    global _cache, _cache_sha
    sha = data.pop("_sha", None)
    # Update cache immediately — bot responds instantly
    _cache = json.loads(json.dumps(data))
    # Save to GitHub in background thread — doesn't block bot
    def _push():
        global _cache_sha
        enc = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode()
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_FILE}"
        payload = {"message": "update via bot", "content": enc, "branch": BRANCH}
        if sha: payload["sha"] = sha
        elif _cache_sha: payload["sha"] = _cache_sha
        try:
            r = requests.put(url, headers=gh(), json=payload, timeout=15)
            if r.status_code in [200, 201]:
                _cache_sha = r.json().get("content", {}).get("sha")
                print("✅ Saved to GitHub")
            else:
                print(f"⚠️ Save failed: {r.status_code}")
        except Exception as e:
            print(f"save error: {e}")
    threading.Thread(target=_push, daemon=True).start()
    return True  # Always return True immediately

# ── LOCK ──
_busy = set()
async def lock_cb(q):
    uid = q.from_user.id
    if uid in _busy:
        await q.answer("⏳ Подождите...")
        return False
    _busy.add(uid)
    await q.answer("⏳")
    return True
def unlock_cb(q): _busy.discard(q.from_user.id)

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
    DELETE_MATCH_SELECT, DELETE_MATCH_CONFIRM
) = range(32)

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

def gh(): return {"Authorization":f"token {GITHUB_TOKEN}","Accept":"application/vnd.github.v3+json"}

def is_admin(uid,data): return uid in data.get("admins",[2070550])
def get_event(data,eid): return next((e for e in data["events"] if e["id"]==eid),None)
def gen_id(data):
    i=1
    while f"ev_{i:03d}" in [e["id"] for e in data["events"]]: i+=1
    return f"ev_{i:03d}"
def label(items,val): return next((l for l,v in items if v==val),val)
def chunks(lst,n):
    for i in range(0,len(lst),n): yield lst[i:i+n]

def calc_standings(event):
    st={p:{"name":p,"p":0,"w":0,"d":0,"l":0,"gf":0,"ga":0} for p in event["participants"]}
    for m in event.get("matches",[]):
        if not m.get("played"): continue
        h,a,sh,sa=m["home"],m["away"],m["score_home"],m["score_away"]
        for name,gf,ga in [(h,sh,sa),(a,sa,sh)]:
            if name in st: st[name]["p"]+=1;st[name]["gf"]+=gf;st[name]["ga"]+=ga
        if sh>sa:
            if h in st: st[h]["w"]+=1
            if a in st: st[a]["l"]+=1
        elif sh<sa:
            if a in st: st[a]["w"]+=1
            if h in st: st[h]["l"]+=1
        else:
            if h in st: st[h]["d"]+=1
            if a in st: st[a]["d"]+=1
    rows=list(st.values())
    rows.sort(key=lambda x:(x["w"]*3+x["d"],x["gf"]-x["ga"]),reverse=True)
    return rows

def main_menu_kb(is_adm):
    kb=[[InlineKeyboardButton("📋 События",callback_data="menu_events"),
         InlineKeyboardButton("📊 Таблица",callback_data="menu_standings")],
        [InlineKeyboardButton("⚡ Матчи",callback_data="menu_matches")]]
    if is_adm:
        kb+=[[InlineKeyboardButton("➕ Создать событие",callback_data="menu_new_event")],
             [InlineKeyboardButton("👤 Участники",callback_data="menu_participants"),
              InlineKeyboardButton("⚽ Добавить матч",callback_data="menu_add_match")],
             [InlineKeyboardButton("✏️ Редактировать",callback_data="menu_edit")],
             [InlineKeyboardButton("🏁 Завершить",callback_data="menu_finish"),
              InlineKeyboardButton("🗑 Удалить",callback_data="menu_delete")],
             [InlineKeyboardButton("👑 Добавить админа",callback_data="menu_add_admin"),
              InlineKeyboardButton("🔄 Сброс",callback_data="menu_reset")]]
    return InlineKeyboardMarkup(kb)

def options_kb(items,prefix,cols=2):
    btns=[InlineKeyboardButton(l,callback_data=f"{prefix}{v}") for l,v in items]
    return InlineKeyboardMarkup(list(chunks(btns,cols)))

def back_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад",callback_data="back_main")]])

async def start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    data=load_data(); adm=is_admin(update.effective_user.id,data)
    name=update.effective_user.first_name or "друг"
    await update.message.reply_text(f"👋 Привет, *{name}*!\n\n🏆 *ShymATC-CHAMP*",parse_mode="Markdown",reply_markup=main_menu_kb(adm))

async def show_main_menu(q,ctx):
    data=load_data(); adm=is_admin(q.from_user.id,data)
    await q.edit_message_text("🏆 *ShymATC-CHAMP*",parse_mode="Markdown",reply_markup=main_menu_kb(adm))

async def back_main(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return
    try: await show_main_menu(q,ctx)
    finally: unlock_cb(q)
    return ConversationHandler.END

async def menu_handler(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return
    action=q.data.replace("menu_","")
    data=load_data()
    try:
        if action=="events":
            if not data["events"]:
                await q.edit_message_text("📋 Событий нет.",reply_markup=back_kb()); return
            text="📋 *События:*\n\n"
            for e in data["events"]:
                s="🟢" if e.get("active") else "⚫"
                mp=len([m for m in e.get("matches",[]) if m.get("played")])
                text+=f"{s} *{e['name']}*\n{TYPE_LABELS.get(e.get('event_type',''),'')}\n👥 {len(e.get('participants',[]))} уч. · ⚽ {mp} матчей\n\n"
            await q.edit_message_text(text,parse_mode="Markdown",reply_markup=back_kb())

        elif action=="standings":
            active=[e for e in data["events"] if e.get("active")]
            if not active:
                await q.edit_message_text("Нет активных событий.",reply_markup=back_kb()); return
            if len(active)==1:
                await do_standings(q,active[0]); return
            kb=[[InlineKeyboardButton(e["name"],callback_data=f"st_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад",callback_data="back_main")])
            await q.edit_message_text("📊 Выбери событие:",reply_markup=InlineKeyboardMarkup(kb))

        elif action=="matches":
            all_m=[(e["name"],m) for e in data["events"] for m in e.get("matches",[]) if m.get("played")]
            if not all_m:
                await q.edit_message_text("Матчей нет.",reply_markup=back_kb()); return
            text="⚡ *Матчи:*\n\n"
            for ename,m in all_m[-10:]:
                sh,sa=m["score_home"],m["score_away"]
                res=f"🏆 {m['home']}" if sh>sa else(f"🏆 {m['away']}" if sa>sh else "🤝")
                ds=f" · {m.get('date','')}" if m.get('date') else ""
                text+=f"*{m['home']}* {sh}:{sa} *{m['away']}*{ds}\n{res} · _{ename}_\n\n"
            await q.edit_message_text(text,parse_mode="Markdown",reply_markup=back_kb())

        elif action=="new_event":
            if not is_admin(q.from_user.id,data): await q.edit_message_text("⛔ Нет доступа."); return
            ctx.user_data.clear()
            await q.edit_message_text("✏️ Введи *название события*:",parse_mode="Markdown")
            return EV_NAME

        elif action=="participants":
            if not is_admin(q.from_user.id,data): await q.edit_message_text("⛔ Нет доступа."); return
            active=[e for e in data["events"] if e.get("active")]
            if not active:
                await q.edit_message_text("Нет активных событий.",reply_markup=back_kb()); return
            if len(active)==1:
                ctx.user_data["ap_event_id"]=active[0]["id"]
                current=", ".join(active[0]["participants"]) or "пока никого"
                await q.edit_message_text(f"👤 *{active[0]['name']}*\nСейчас: {current}\n\nВводи имена, /done — готово",parse_mode="Markdown")
                return ADD_PARTICIPANT
            kb=[[InlineKeyboardButton(e["name"],callback_data=f"ap_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад",callback_data="back_main")])
            await q.edit_message_text("👤 Выбери событие:",reply_markup=InlineKeyboardMarkup(kb))
            return ADD_PARTICIPANT

        elif action=="add_match":
            if not is_admin(q.from_user.id,data): await q.edit_message_text("⛔ Нет доступа."); return
            active=[e for e in data["events"] if e.get("active") and len(e.get("participants",[]))>=2]
            if not active:
                await q.edit_message_text("Нет событий с участниками.",reply_markup=back_kb()); return
            if len(active)==1:
                ctx.user_data["match"]={"event_id":active[0]["id"]}
                kb=[[InlineKeyboardButton(p,callback_data=f"mh_{p}")] for p in active[0]["participants"]]
                await q.edit_message_text(f"⚽ *{active[0]['name']}*\nКто первый:",parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb))
                return MATCH_HOME
            kb=[[InlineKeyboardButton(e["name"],callback_data=f"me_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад",callback_data="back_main")])
            await q.edit_message_text("⚽ Выбери событие:",reply_markup=InlineKeyboardMarkup(kb))
            return MATCH_EVENT

        elif action=="edit":
            if not is_admin(q.from_user.id,data): await q.edit_message_text("⛔ Нет доступа."); return
            if not data["events"]:
                await q.edit_message_text("Событий нет.",reply_markup=back_kb()); return
            kb=[[InlineKeyboardButton("📌 Переименовать событие",callback_data="edit_ev_name")],
                [InlineKeyboardButton("⚽ Изменить счёт матча",callback_data="edit_match_score")],
                [InlineKeyboardButton("🗑 Удалить матч",callback_data="edit_delete_match")],
                [InlineKeyboardButton("👤 Переименовать участника",callback_data="edit_participant")],
                [InlineKeyboardButton("🗑 Удалить участника",callback_data="edit_remove_participant")],
                [InlineKeyboardButton("◀️ Назад",callback_data="back_main")]]
            await q.edit_message_text("✏️ *Что редактируем?*",parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb))
            return EDIT_SELECT

        elif action=="finish":
            if not is_admin(q.from_user.id,data): await q.edit_message_text("⛔ Нет доступа."); return
            active=[e for e in data["events"] if e.get("active")]
            if not active:
                await q.edit_message_text("Нет активных событий.",reply_markup=back_kb()); return
            kb=[[InlineKeyboardButton(f"🏁 {e['name']}",callback_data=f"fin_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад",callback_data="back_main")])
            await q.edit_message_text("🏁 Какое завершить?",reply_markup=InlineKeyboardMarkup(kb))
            return FINISH_CONFIRM

        elif action=="delete":
            if not is_admin(q.from_user.id,data): await q.edit_message_text("⛔ Нет доступа."); return
            if not data["events"]:
                await q.edit_message_text("Событий нет.",reply_markup=back_kb()); return
            kb=[[InlineKeyboardButton(f"🗑 {e['name']}",callback_data=f"del_{e['id']}")] for e in data["events"]]
            kb.append([InlineKeyboardButton("◀️ Назад",callback_data="back_main")])
            await q.edit_message_text("🗑 Какое удалить?",reply_markup=InlineKeyboardMarkup(kb))
            return DELETE_CONFIRM

        elif action=="add_admin":
            if not is_admin(q.from_user.id,data): await q.edit_message_text("⛔ Нет доступа."); return
            await q.edit_message_text("👑 Введи Telegram user_id нового админа:")
            return ADD_ADMIN_ID

        elif action=="reset":
            if not is_admin(q.from_user.id,data): await q.edit_message_text("⛔ Нет доступа."); return
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Да, очистить",callback_data="reset_yes"),InlineKeyboardButton("❌ Отмена",callback_data="back_main")]])
            await q.edit_message_text("⚠️ *Удалить все события?*",parse_mode="Markdown",reply_markup=kb)
            return RESET_CONFIRM
    finally:
        unlock_cb(q)

async def do_standings(q,event):
    rows=calc_standings(event)
    medals=["🥇","🥈","🥉"]
    text=f"📊 *{event['name']}*\n\n`# Участник       И  В  Н  П  ГЗ ГП  ±  Оч`\n`{'─'*42}`\n"
    for i,r in enumerate(rows):
        pts=r["w"]*3+r["d"]; gd=r["gf"]-r["ga"]
        rank=medals[i] if i<3 else f"{i+1}."
        name=r["name"][:13].ljust(13)
        gd_str=("+"+str(gd)) if gd>=0 else str(gd)
        text+=f"`{rank} {name} {r['p']:2} {r['w']:2} {r['d']:2} {r['l']:2} {r['gf']:3} {r['ga']:3} {gd_str:>4} {pts:3}`\n"
    await q.edit_message_text(text,parse_mode="Markdown",reply_markup=back_kb())

async def standings_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return
    try:
        data=load_data(); event=get_event(data,q.data.replace("st_",""))
        if event: await do_standings(q,event)
    finally: unlock_cb(q)

# CREATE EVENT
async def ev_name(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ev"]={"name":update.message.text.strip()}
    await update.message.reply_text("📌 *Тип события:*",parse_mode="Markdown",reply_markup=options_kb(EVENT_TYPES,"evt_"))
    return EV_TYPE

async def ev_type_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return EV_TYPE
    try:
        ctx.user_data["ev"]["event_type"]=q.data.replace("evt_","")
        await q.edit_message_text("🎯 *Категория:*",parse_mode="Markdown",reply_markup=options_kb(CATEGORIES,"cat_"))
    finally: unlock_cb(q)
    return EV_CATEGORY

async def ev_category_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return EV_CATEGORY
    try:
        val=q.data.replace("cat_","")
        if val=="other_cat":
            await q.edit_message_text("✏️ Введи категорию:"); return EV_CATEGORY_CUSTOM
        ctx.user_data["ev"]["category"]=val
        await q.edit_message_text("👥 *Формат участия:*",parse_mode="Markdown",reply_markup=options_kb(FORMATS,"fmt_",cols=1))
    finally: unlock_cb(q)
    return EV_FORMAT

async def ev_category_custom(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ev"]["category"]=update.message.text.strip()
    await update.message.reply_text("👥 *Формат:*",parse_mode="Markdown",reply_markup=options_kb(FORMATS,"fmt_",cols=1))
    return EV_FORMAT

async def ev_format_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return EV_FORMAT
    try:
        ctx.user_data["ev"]["participation_format"]=q.data.replace("fmt_","")
        await q.edit_message_text("📍 *Площадка:*",parse_mode="Markdown",reply_markup=options_kb(VENUES,"ven_"))
    finally: unlock_cb(q)
    return EV_VENUE

async def ev_venue_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return EV_VENUE
    try:
        val=q.data.replace("ven_","")
        if val=="other_venue":
            await q.edit_message_text("✏️ Введи площадку:"); return EV_VENUE_CUSTOM
        ctx.user_data["ev"]["venue_type"]=val
        await q.edit_message_text("📅 Дата начала (01.04.2026)\nИли /skip:")
    finally: unlock_cb(q)
    return EV_DATE_START

async def ev_venue_custom(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ev"]["venue_type"]=update.message.text.strip()
    await update.message.reply_text("📅 Дата начала (01.04.2026)\nИли /skip:")
    return EV_DATE_START

async def ev_date_start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    text=update.message.text.strip()
    ctx.user_data["ev"]["date_start"]="" if text.startswith("/") else text
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить",callback_data="skip_date_end")]])
    await update.message.reply_text("📅 Дата окончания\nИли пропусти:",reply_markup=kb)
    return EV_DATE_END

async def ev_date_end(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    text=update.message.text.strip()
    ctx.user_data["ev"]["date_end"]="" if text.startswith("/") else text
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить",callback_data="skip_rules")]])
    await update.message.reply_text("📋 Правила события\nИли пропусти:",reply_markup=kb)
    return EV_RULES

async def ev_skip_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    if q.data=="skip_date_end":
        ctx.user_data["ev"]["date_end"]=""
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить",callback_data="skip_rules")]])
        await q.edit_message_text("📋 Правила события\nИли пропусти:",reply_markup=kb)
        return EV_RULES
    elif q.data=="skip_rules":
        ctx.user_data["ev"]["rules"]=""
        return await _show_ev_confirm(q,ctx)

async def _show_ev_confirm(target,ctx):
    ev=ctx.user_data["ev"]
    summary=(f"✅ *Подтверди:*\n\n📌 *{ev['name']}*\n"
             f"🏷 {label(EVENT_TYPES,ev.get('event_type',''))}\n"
             f"🎯 {label(CATEGORIES,ev.get('category',''))}\n"
             f"👥 {label(FORMATS,ev.get('participation_format',''))}\n"
             f"📍 {label(VENUES,ev.get('venue_type',''))}\n")
    if ev.get("date_start"): summary+=f"📅 {ev['date_start']}"
    if ev.get("date_end"): summary+=f" → {ev['date_end']}\n"
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Создать",callback_data="ev_create"),InlineKeyboardButton("❌ Отмена",callback_data="back_main")]])
    if hasattr(target,'edit_message_text'):
        await target.edit_message_text(summary,parse_mode="Markdown",reply_markup=kb)
    else:
        await target.message.reply_text(summary,parse_mode="Markdown",reply_markup=kb)
    return EV_CONFIRM

async def ev_rules(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    text=update.message.text.strip()
    ctx.user_data["ev"]["rules"]="" if text.startswith("/") else text
    return await _show_ev_confirm(update,ctx)

async def ev_confirm_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return EV_CONFIRM
    try:
        if q.data=="back_main":
            await show_main_menu(q,ctx); return ConversationHandler.END
        data=load_data(); ev=ctx.user_data["ev"]; eid=gen_id(data)
        event={"id":eid,"name":ev["name"],"event_type":ev.get("event_type","other"),
               "category":ev.get("category","other"),"participation_format":ev.get("participation_format","individual"),
               "venue_type":ev.get("venue_type","offline"),"date_start":ev.get("date_start",""),
               "date_end":ev.get("date_end",""),"rules":ev.get("rules",""),"active":True,
               "participants":[],"matches":[],"created_at":datetime.now().strftime("%d.%m.%Y %H:%M")}
        data["events"].append(event)
        ok=save_data(data)
        status="✅ Данные сохранены!" if ok else "⚠️ Ошибка сохранения"
        adm=is_admin(q.from_user.id,data)
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 Добавить участников",callback_data="menu_participants")],
            [InlineKeyboardButton("🏠 Главное меню",callback_data="back_main")],
        ])
        await q.edit_message_text(f"{status}\n\n🎉 *{ev['name']}* создано!\n\nТеперь добавь участников:",parse_mode="Markdown",reply_markup=kb)
    finally: unlock_cb(q)
    return ConversationHandler.END

# PARTICIPANTS
async def ap_event_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return ADD_PARTICIPANT
    try:
        ctx.user_data["ap_event_id"]=q.data.replace("ap_","")
        data=load_data(); event=get_event(data,ctx.user_data["ap_event_id"])
        current=", ".join(event["participants"]) or "пока никого"
        await q.edit_message_text(f"👤 *{event['name']}*\nСейчас: {current}\n\nВводи имена, /done — готово",parse_mode="Markdown")
    finally: unlock_cb(q)
    return ADD_PARTICIPANT

async def ap_name(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    name=update.message.text.strip()
    data=load_data(); event=get_event(data,ctx.user_data.get("ap_event_id"))
    if not event:
        await update.message.reply_text("Ошибка. Начни заново."); return ConversationHandler.END
    if name in event["participants"]:
        await update.message.reply_text(f"*{name}* уже есть.",parse_mode="Markdown"); return ADD_PARTICIPANT
    event["participants"].append(name); ok=save_data(data)
    current=", ".join(event["participants"])
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово",callback_data="ap_done")]])
    await update.message.reply_text(
        f"{'✅' if ok else '⚠️'} *{name}* добавлен\nСписок: {current}\n\nЕщё имя или нажми «Готово»:",
        parse_mode="Markdown",reply_markup=kb)
    return ADD_PARTICIPANT

async def ap_done_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    data=load_data(); adm=is_admin(q.from_user.id,data)
    await q.edit_message_text("✅ Участники сохранены!",reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

# ADD MATCH
async def match_event_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return MATCH_EVENT
    try:
        ctx.user_data["match"]={"event_id":q.data.replace("me_","")}
        data=load_data(); event=get_event(data,ctx.user_data["match"]["event_id"])
        kb=[[InlineKeyboardButton(p,callback_data=f"mh_{p}")] for p in event["participants"]]
        await q.edit_message_text(f"⚽ *{event['name']}*\nКто первый:",parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return MATCH_HOME

async def match_home_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return MATCH_HOME
    try:
        ctx.user_data["match"]["home"]=q.data.replace("mh_","")
        data=load_data(); event=get_event(data,ctx.user_data["match"]["event_id"])
        others=[p for p in event["participants"] if p!=ctx.user_data["match"]["home"]]
        kb=[[InlineKeyboardButton(p,callback_data=f"ma_{p}")] for p in others]
        await q.edit_message_text("Кто второй:",reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return MATCH_AWAY

async def match_away_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return MATCH_AWAY
    try:
        ctx.user_data["match"]["away"]=q.data.replace("ma_","")
        today=datetime.now().strftime("%d.%m.%Y")
        h,a=ctx.user_data["match"]["home"],ctx.user_data["match"]["away"]
        kb=InlineKeyboardMarkup([[InlineKeyboardButton(f"📅 Сегодня ({today})",callback_data=f"md_{today}")],
                                  [InlineKeyboardButton("✏️ Ввести другую дату",callback_data="md_custom")]])
        await q.edit_message_text(f"📅 Дата матча *{h}* vs *{a}*:",parse_mode="Markdown",reply_markup=kb)
    finally: unlock_cb(q)
    return MATCH_DATE

async def match_date_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return MATCH_DATE
    try:
        if q.data=="md_custom":
            await q.edit_message_text("📅 Введи дату (например: 01.04.2026):"); return MATCH_DATE
        ctx.user_data["match"]["date"]=q.data.replace("md_","")
        h,a=ctx.user_data["match"]["home"],ctx.user_data["match"]["away"]
        await q.edit_message_text(f"Счёт *{h}* vs *{a}*\nФормат: `2:1`",parse_mode="Markdown")
    finally: unlock_cb(q)
    return MATCH_SCORE

async def match_date_msg(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    ctx.user_data["match"]["date"]=update.message.text.strip()
    m=ctx.user_data["match"]
    await update.message.reply_text(f"Счёт *{m['home']}* vs *{m['away']}*\nФормат: `2:1`",parse_mode="Markdown")
    return MATCH_SCORE

async def match_score_msg(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    text=update.message.text.strip().replace("-",":").replace(" ","")
    if ":"not in text:
        await update.message.reply_text("Формат: `2:1`",parse_mode="Markdown"); return MATCH_SCORE
    try: sh,sa=map(int,text.split(":"))
    except:
        await update.message.reply_text("Формат: `2:1`",parse_mode="Markdown"); return MATCH_SCORE
    m=ctx.user_data["match"]; data=load_data(); event=get_event(data,m["event_id"])
    event["matches"].append({"home":m["home"],"away":m["away"],"score_home":sh,"score_away":sa,
                              "played":True,"date":m.get("date",datetime.now().strftime("%d.%m.%Y"))})
    ok=save_data(data)
    res=f"🏆 *{m['home']}*" if sh>sa else(f"🏆 *{m['away']}*" if sa>sh else "🤝 Ничья")
    adm=is_admin(update.effective_user.id,data)
    await update.message.reply_text(
        f"{'✅ Результат сохранён!' if ok else '⚠️ Ошибка'}\n\n*{m['home']}* {sh}:{sa} *{m['away']}*\n{res}\n📅 {m.get('date','')}",
        parse_mode="Markdown",reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

# EDIT
async def edit_select_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return EDIT_SELECT
    data=load_data()
    try:
        if q.data=="edit_ev_name":
            kb=[[InlineKeyboardButton(e["name"],callback_data=f"een_{e['id']}")] for e in data["events"]]
            kb.append([InlineKeyboardButton("◀️ Назад",callback_data="back_main")])
            await q.edit_message_text("📌 Выбери событие для переименования:",reply_markup=InlineKeyboardMarkup(kb))
            return EDIT_EVENT_FIELD
        elif q.data=="edit_match_score":
            active=[e for e in data["events"] if e.get("active")]
            if not active:
                await q.edit_message_text("Нет активных событий.",reply_markup=back_kb()); return ConversationHandler.END
            kb=[[InlineKeyboardButton(e["name"],callback_data=f"ems_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад",callback_data="back_main")])
            await q.edit_message_text("⚽ Выбери событие:",reply_markup=InlineKeyboardMarkup(kb))
            return EDIT_MATCH_SELECT
        elif q.data=="edit_participant":
            active=[e for e in data["events"] if e.get("active") and e.get("participants")]
            if not active:
                await q.edit_message_text("Нет событий с участниками.",reply_markup=back_kb()); return ConversationHandler.END
            kb=[[InlineKeyboardButton(e["name"],callback_data=f"ep_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад",callback_data="back_main")])
            await q.edit_message_text("👤 Выбери событие:",reply_markup=InlineKeyboardMarkup(kb))
            return EDIT_PARTICIPANT_OLD
        elif q.data=="edit_remove_participant":
            active=[e for e in data["events"] if e.get("active") and e.get("participants")]
            if not active:
                await q.edit_message_text("Нет событий с участниками.",reply_markup=back_kb()); return ConversationHandler.END
            kb=[[InlineKeyboardButton(e["name"],callback_data=f"erp_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад",callback_data="back_main")])
            await q.edit_message_text("🗑 Выбери событие:",reply_markup=InlineKeyboardMarkup(kb))
            return REMOVE_PARTICIPANT_SELECT
        elif q.data=="edit_delete_match":
            active=[e for e in data["events"] if e.get("active") and any(m.get("played") for m in e.get("matches",[]))]
            if not active:
                await q.edit_message_text("Нет событий с матчами.",reply_markup=back_kb()); return ConversationHandler.END
            kb=[[InlineKeyboardButton(e["name"],callback_data=f"edm_{e['id']}")] for e in active]
            kb.append([InlineKeyboardButton("◀️ Назад",callback_data="back_main")])
            await q.edit_message_text("🗑 Выбери событие:",reply_markup=InlineKeyboardMarkup(kb))
            return DELETE_MATCH_SELECT
    finally: unlock_cb(q)

async def edit_event_name_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return EDIT_EVENT_FIELD
    try:
        ctx.user_data["edit_eid"]=q.data.replace("een_","")
        data=load_data(); event=get_event(data,ctx.user_data["edit_eid"])
        await q.edit_message_text(f"Текущее: *{event['name']}*\n\nВведи новое название:",parse_mode="Markdown")
    finally: unlock_cb(q)
    return EDIT_EVENT_VALUE

async def edit_event_name_value(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    new_name=update.message.text.strip(); data=load_data()
    event=get_event(data,ctx.user_data["edit_eid"]); old=event["name"]
    event["name"]=new_name; ok=save_data(data)
    adm=is_admin(update.effective_user.id,data)
    await update.message.reply_text(f"{'✅ Название изменено!' if ok else '⚠️ Ошибка'}\n\n*{old}* → *{new_name}*",parse_mode="Markdown",reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

async def edit_match_event_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return EDIT_MATCH_SELECT
    try:
        ctx.user_data["edit_eid"]=q.data.replace("ems_","")
        data=load_data(); event=get_event(data,ctx.user_data["edit_eid"])
        played=[m for m in event.get("matches",[]) if m.get("played")]
        if not played:
            await q.edit_message_text("Нет сыгранных матчей.",reply_markup=back_kb()); return ConversationHandler.END
        kb=[[InlineKeyboardButton(f"{m['home']} {m['score_home']}:{m['score_away']} {m['away']} ({m.get('date','')})",callback_data=f"emm_{i}")]
            for i,m in enumerate(event["matches"]) if m.get("played")]
        kb.append([InlineKeyboardButton("◀️ Назад",callback_data="back_main")])
        await q.edit_message_text("⚽ Выбери матч:",reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return EDIT_MATCH_FIELD

async def edit_match_select_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return EDIT_MATCH_FIELD
    try:
        idx=int(q.data.replace("emm_","")); ctx.user_data["edit_match_idx"]=idx
        data=load_data(); event=get_event(data,ctx.user_data["edit_eid"]); m=event["matches"][idx]
        await q.edit_message_text(f"Текущий: *{m['home']}* {m['score_home']}:{m['score_away']} *{m['away']}*\n\nНовый счёт (`2:1`):",parse_mode="Markdown")
    finally: unlock_cb(q)
    return EDIT_MATCH_VALUE

async def edit_match_score_value(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    text=update.message.text.strip().replace("-",":").replace(" ","")
    if ":"not in text:
        await update.message.reply_text("Формат: `2:1`",parse_mode="Markdown"); return EDIT_MATCH_VALUE
    try: sh,sa=map(int,text.split(":"))
    except:
        await update.message.reply_text("Формат: `2:1`",parse_mode="Markdown"); return EDIT_MATCH_VALUE
    data=load_data(); event=get_event(data,ctx.user_data["edit_eid"])
    idx=ctx.user_data["edit_match_idx"]; m=event["matches"][idx]
    old=f"{m['score_home']}:{m['score_away']}"; m["score_home"]=sh; m["score_away"]=sa
    ok=save_data(data); adm=is_admin(update.effective_user.id,data)
    await update.message.reply_text(f"{'✅ Счёт изменён!' if ok else '⚠️ Ошибка'}\n\n*{m['home']}* {old} → {sh}:{sa} *{m['away']}*",parse_mode="Markdown",reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

async def edit_participant_event_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return EDIT_PARTICIPANT_OLD
    try:
        ctx.user_data["edit_eid"]=q.data.replace("ep_","")
        data=load_data(); event=get_event(data,ctx.user_data["edit_eid"])
        kb=[[InlineKeyboardButton(p,callback_data=f"epp_{p}")] for p in event["participants"]]
        kb.append([InlineKeyboardButton("◀️ Назад",callback_data="back_main")])
        await q.edit_message_text("👤 Выбери участника:",reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return EDIT_PARTICIPANT_NEW

async def edit_participant_select_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return EDIT_PARTICIPANT_NEW
    try:
        ctx.user_data["edit_participant_old"]=q.data.replace("epp_","")
        await q.edit_message_text(f"Текущее: *{ctx.user_data['edit_participant_old']}*\n\nВведи новое имя:",parse_mode="Markdown")
    finally: unlock_cb(q)
    return EDIT_PARTICIPANT_NEW

async def edit_participant_new_value(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    new_name=update.message.text.strip(); old_name=ctx.user_data["edit_participant_old"]
    data=load_data(); event=get_event(data,ctx.user_data["edit_eid"])
    if old_name in event["participants"]:
        event["participants"][event["participants"].index(old_name)]=new_name
    for m in event.get("matches",[]):
        if m.get("home")==old_name: m["home"]=new_name
        if m.get("away")==old_name: m["away"]=new_name
    ok=save_data(data); adm=is_admin(update.effective_user.id,data)
    await update.message.reply_text(f"{'✅ Имя изменено!' if ok else '⚠️ Ошибка'}\n\n*{old_name}* → *{new_name}*",parse_mode="Markdown",reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

async def remove_participant_event_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return REMOVE_PARTICIPANT_SELECT
    try:
        ctx.user_data["edit_eid"]=q.data.replace("erp_","")
        data=load_data(); event=get_event(data,ctx.user_data["edit_eid"])
        kb=[[InlineKeyboardButton(f"🗑 {p}",callback_data=f"rpp_{p}")] for p in event["participants"]]
        kb.append([InlineKeyboardButton("◀️ Назад",callback_data="back_main")])
        await q.edit_message_text("🗑 Выбери участника для удаления:",reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return REMOVE_PARTICIPANT_SELECT

async def remove_participant_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return REMOVE_PARTICIPANT_SELECT
    try:
        name=q.data.replace("rpp_",""); data=load_data()
        event=get_event(data,ctx.user_data["edit_eid"])
        event["participants"]=[p for p in event["participants"] if p!=name]
        ok=save_data(data); adm=is_admin(q.from_user.id,data)
        await q.edit_message_text(f"{'✅ Участник удалён!' if ok else '⚠️ Ошибка'}\n\n*{name}* удалён.",parse_mode="Markdown",reply_markup=main_menu_kb(adm))
    finally: unlock_cb(q)
    return ConversationHandler.END

async def delete_match_event_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return DELETE_MATCH_SELECT
    try:
        eid=q.data.replace("edm_","")
        ctx.user_data["edit_eid"]=eid
        data=load_data(); event=get_event(data,eid)
        played=[m for i,m in enumerate(event.get("matches",[])) if m.get("played")]
        if not played:
            await q.edit_message_text("Нет сыгранных матчей.",reply_markup=back_kb()); return ConversationHandler.END
        kb=[[InlineKeyboardButton(
            f"{m['home']} {m['score_home']}:{m['score_away']} {m['away']} ({m.get('date','')})",
            callback_data=f"dmc_{i}"
        )] for i,m in enumerate(event["matches"]) if m.get("played")]
        kb.append([InlineKeyboardButton("◀️ Назад",callback_data="back_main")])
        await q.edit_message_text("🗑 Выбери матч для удаления:",reply_markup=InlineKeyboardMarkup(kb))
    finally: unlock_cb(q)
    return DELETE_MATCH_CONFIRM

async def delete_match_confirm_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return DELETE_MATCH_CONFIRM
    try:
        idx=int(q.data.replace("dmc_",""))
        data=load_data(); event=get_event(data,ctx.user_data["edit_eid"])
        m=event["matches"][idx]
        match_label=f"*{m['home']}* {m['score_home']}:{m['score_away']} *{m['away']}* ({m.get('date','')})"
        event["matches"].pop(idx)
        ok=save_data(data)
        adm=is_admin(q.from_user.id,data)
        await q.edit_message_text(
            f"{'✅ Матч удалён!' if ok else '⚠️ Ошибка'}\n\n{match_label}",
            parse_mode="Markdown",reply_markup=main_menu_kb(adm))
    finally: unlock_cb(q)
    return ConversationHandler.END

async def finish_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return FINISH_CONFIRM
    try:
        data=load_data(); event=get_event(data,q.data.replace("fin_",""))
        if event:
            event["active"]=False; save_data(data)
            await do_standings(q,event)
    finally: unlock_cb(q)
    return ConversationHandler.END

async def delete_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return DELETE_CONFIRM
    try:
        data=load_data(); event=get_event(data,q.data.replace("del_",""))
        if event:
            name=event["name"]
            data["events"]=[e for e in data["events"] if e["id"]!=event["id"]]
            ok=save_data(data); adm=is_admin(q.from_user.id,data)
            await q.edit_message_text(f"{'✅ Событие удалено!' if ok else '⚠️ Ошибка'}\n\n*{name}* удалено.",parse_mode="Markdown",reply_markup=main_menu_kb(adm))
    finally: unlock_cb(q)
    return ConversationHandler.END

async def add_admin_msg(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    try: new_id=int(update.message.text.strip())
    except:
        await update.message.reply_text("Введи число."); return ADD_ADMIN_ID
    data=load_data()
    if new_id not in data["admins"]:
        data["admins"].append(new_id); ok=save_data(data)
        await update.message.reply_text(f"{'✅ Админ добавлен!' if ok else '⚠️ Ошибка'}\n\nID: {new_id}",reply_markup=main_menu_kb(True))
    else:
        await update.message.reply_text("Уже админ.",reply_markup=main_menu_kb(True))
    return ConversationHandler.END

async def reset_cb(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not await lock_cb(q): return RESET_CONFIRM
    try:
        if q.data=="reset_yes":
            data=load_data()
            new_data={"events":[],"admins":data.get("admins",[2070550]),"_sha":data.get("_sha")}
            ok=save_data(new_data)
            await q.edit_message_text(f"{'✅ Все данные очищены!' if ok else '⚠️ Ошибка'}",reply_markup=main_menu_kb(True))
        else:
            await show_main_menu(q,ctx)
    finally: unlock_cb(q)
    return ConversationHandler.END

async def cancel(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear(); data=load_data(); adm=is_admin(update.effective_user.id,data)
    await update.message.reply_text("❌ Отменено.",reply_markup=main_menu_kb(adm))
    return ConversationHandler.END

def main():
    app=ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler,pattern="^menu_new_event$")],
        states={
            EV_NAME:[MessageHandler(filters.TEXT&~filters.COMMAND,ev_name)],
            EV_TYPE:[CallbackQueryHandler(ev_type_cb,pattern="^evt_")],
            EV_CATEGORY:[CallbackQueryHandler(ev_category_cb,pattern="^cat_")],
            EV_CATEGORY_CUSTOM:[MessageHandler(filters.TEXT&~filters.COMMAND,ev_category_custom)],
            EV_FORMAT:[CallbackQueryHandler(ev_format_cb,pattern="^fmt_")],
            EV_VENUE:[CallbackQueryHandler(ev_venue_cb,pattern="^ven_")],
            EV_VENUE_CUSTOM:[MessageHandler(filters.TEXT&~filters.COMMAND,ev_venue_custom)],
            EV_DATE_START:[MessageHandler(filters.TEXT&~filters.COMMAND,ev_date_start)],
            EV_DATE_END:[MessageHandler(filters.TEXT&~filters.COMMAND,ev_date_end),CallbackQueryHandler(ev_skip_cb,pattern="^skip_date_end$")],
            EV_RULES:[MessageHandler(filters.TEXT&~filters.COMMAND,ev_rules),CallbackQueryHandler(ev_skip_cb,pattern="^skip_rules$")],
            EV_CONFIRM:[CallbackQueryHandler(ev_confirm_cb,pattern="^(ev_create|back_main)$")],
        },
        fallbacks=[CommandHandler("cancel",cancel),CallbackQueryHandler(back_main,pattern="^back_main$")]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler,pattern="^menu_participants$")],
        states={
            ADD_PARTICIPANT:[
                CallbackQueryHandler(ap_event_cb,pattern="^ap_"),
                CallbackQueryHandler(ap_done_cb,pattern="^ap_done$"),
                MessageHandler(filters.TEXT&~filters.COMMAND,ap_name),
            ],
        },
        fallbacks=[CommandHandler("cancel",cancel),CallbackQueryHandler(back_main,pattern="^back_main$")]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler,pattern="^menu_add_match$")],
        states={
            MATCH_EVENT:[CallbackQueryHandler(match_event_cb,pattern="^me_")],
            MATCH_HOME:[CallbackQueryHandler(match_home_cb,pattern="^mh_")],
            MATCH_AWAY:[CallbackQueryHandler(match_away_cb,pattern="^ma_")],
            MATCH_DATE:[CallbackQueryHandler(match_date_cb,pattern="^md_"),MessageHandler(filters.TEXT&~filters.COMMAND,match_date_msg)],
            MATCH_SCORE:[MessageHandler(filters.TEXT&~filters.COMMAND,match_score_msg)],
        },
        fallbacks=[CommandHandler("cancel",cancel),CallbackQueryHandler(back_main,pattern="^back_main$")]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler,pattern="^menu_edit$")],
        states={
            EDIT_SELECT:[CallbackQueryHandler(edit_select_cb,pattern="^edit_")],
            EDIT_EVENT_FIELD:[CallbackQueryHandler(edit_event_name_cb,pattern="^een_")],
            EDIT_EVENT_VALUE:[MessageHandler(filters.TEXT&~filters.COMMAND,edit_event_name_value)],
            EDIT_MATCH_SELECT:[CallbackQueryHandler(edit_match_event_cb,pattern="^ems_")],
            EDIT_MATCH_FIELD:[CallbackQueryHandler(edit_match_select_cb,pattern="^emm_")],
            EDIT_MATCH_VALUE:[MessageHandler(filters.TEXT&~filters.COMMAND,edit_match_score_value)],
            EDIT_PARTICIPANT_OLD:[CallbackQueryHandler(edit_participant_event_cb,pattern="^ep_")],
            EDIT_PARTICIPANT_NEW:[CallbackQueryHandler(edit_participant_select_cb,pattern="^epp_"),MessageHandler(filters.TEXT&~filters.COMMAND,edit_participant_new_value)],
            REMOVE_PARTICIPANT_SELECT:[CallbackQueryHandler(remove_participant_event_cb,pattern="^erp_"),CallbackQueryHandler(remove_participant_cb,pattern="^rpp_")],
            DELETE_MATCH_SELECT:[CallbackQueryHandler(delete_match_event_cb,pattern="^edm_")],
            DELETE_MATCH_CONFIRM:[CallbackQueryHandler(delete_match_confirm_cb,pattern="^dmc_")],
        },
        fallbacks=[CommandHandler("cancel",cancel),CallbackQueryHandler(back_main,pattern="^back_main$")]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler,pattern="^menu_finish$")],
        states={FINISH_CONFIRM:[CallbackQueryHandler(finish_cb,pattern="^fin_")]},
        fallbacks=[CommandHandler("cancel",cancel),CallbackQueryHandler(back_main,pattern="^back_main$")]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler,pattern="^menu_delete$")],
        states={DELETE_CONFIRM:[CallbackQueryHandler(delete_cb,pattern="^del_")]},
        fallbacks=[CommandHandler("cancel",cancel),CallbackQueryHandler(back_main,pattern="^back_main$")]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler,pattern="^menu_add_admin$")],
        states={ADD_ADMIN_ID:[MessageHandler(filters.TEXT&~filters.COMMAND,add_admin_msg)]},
        fallbacks=[CommandHandler("cancel",cancel)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler,pattern="^menu_reset$")],
        states={RESET_CONFIRM:[CallbackQueryHandler(reset_cb,pattern="^reset_yes$")]},
        fallbacks=[CommandHandler("cancel",cancel),CallbackQueryHandler(back_main,pattern="^back_main$")]
    ))

    app.add_handler(CommandHandler("start",start))
    app.add_handler(CallbackQueryHandler(menu_handler,pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(standings_cb,pattern="^st_"))
    app.add_handler(CallbackQueryHandler(back_main,pattern="^back_main$"))

    print("✅ ShymATC-CHAMP bot v5 started")
    # Preload cache on startup
    print("📦 Preloading data from GitHub...")
    _load_from_github()
    print(f"📦 Cache loaded: {len(_cache.get('events', []))} events")
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
