import os
import json
import base64
import requests
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
    NEW_EVENT_NAME, NEW_EVENT_TYPE, NEW_EVENT_SPORT, NEW_EVENT_SPORT_CUSTOM,
    ADD_PARTICIPANT,
    MATCH_EVENT, MATCH_HOME, MATCH_AWAY, MATCH_SCORE,
    DELETE_EVENT, ADD_ADMIN_ID, FINISH_EVENT_SELECT
) = range(12)

def gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

def load_data():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_FILE}"
    try:
        r = requests.get(url, headers=gh_headers(), timeout=10)
        if r.status_code == 200:
            content = r.json()
            raw = base64.b64decode(content["content"]).decode("utf-8")
            data = json.loads(raw)
            data["_sha"] = content["sha"]
            return data
    except Exception as e:
        print(f"load_data error: {e}")
    return {"events": [], "admins": [2070550], "_sha": None}

def save_data(data):
    sha = data.pop("_sha", None)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_FILE}"
    payload = {"message": "update via bot", "content": encoded, "branch": BRANCH}
    if sha:
        payload["sha"] = sha
    try:
        r = requests.put(url, headers=gh_headers(), json=payload, timeout=15)
        return r.status_code in [200, 201]
    except Exception as e:
        print(f"save_data error: {e}")
        return False

def is_admin(uid, data):
    return uid in data.get("admins", [2070550])

def get_event(data, eid):
    for e in data["events"]:
        if e["id"] == eid:
            return e
    return None

def gen_id(data):
    ids = [e["id"] for e in data["events"]]
    i = 1
    while f"event_{i:03d}" in ids:
        i += 1
    return f"event_{i:03d}"

def calc_standings(event):
    st = {p: {"name": p, "p":0,"w":0,"d":0,"l":0,"gf":0,"ga":0} for p in event["participants"]}
    for m in event.get("matches", []):
        if not m.get("played"):
            continue
        h, a, sh, sa = m["home"], m["away"], m["score_home"], m["score_away"]
        for name, gf, ga in [(h, sh, sa), (a, sa, sh)]:
            if name in st:
                st[name]["p"] += 1; st[name]["gf"] += gf; st[name]["ga"] += ga
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

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    adm = is_admin(update.effective_user.id, data)
    text = "🏆 *ShymATC-CHAMP*\n\n📋 /events\n📊 /standings\n"
    if adm:
        text += "\n*Админ:*\n➕ /new\\_event\n👤 /add\\_participant\n⚽ /add\\_match\n🏁 /finish\\_event\n🗑 /delete\\_event\n👑 /add\\_admin\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def events_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data["events"]:
        await update.message.reply_text("Событий нет. /new_event")
        return
    text = "📋 *События:*\n\n"
    for e in data["events"]:
        s = "🟢" if e.get("active") else "⚫"
        n = len(e.get("participants", []))
        mp = len([m for m in e.get("matches", []) if m.get("played")])
        text += f"{s} *{e['name']}* ({e.get('sport','')})\nУчастников: {n} · Матчей: {mp}\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def standings_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    active = [e for e in data["events"] if e.get("active")]
    if not active:
        await update.message.reply_text("Нет активных событий.")
        return
    if len(active) == 1:
        await send_st(update.message, active[0])
        return
    kb = [[InlineKeyboardButton(e["name"], callback_data=f"st_{e['id']}")] for e in active]
    await update.message.reply_text("Выбери:", reply_markup=InlineKeyboardMarkup(kb))

async def send_st(target, event):
    rows = calc_standings(event)
    medals = ["🥇","🥈","🥉"]
    text = f"📊 *{event['name']}*\n\n`# Участник         И  В  Н  П  Оч`\n`{'─'*35}`\n"
    for i, r in enumerate(rows):
        pts = r["w"]*3+r["d"]
        rank = medals[i] if i < 3 else f"{i+1}."
        name = r["name"][:15].ljust(15)
        text += f"`{rank} {name} {r['p']:2} {r['w']:2} {r['d']:2} {r['l']:2} {pts:3}`\n"
    await target.reply_text(text, parse_mode="Markdown")

async def standings_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = load_data()
    event = get_event(data, q.data.replace("st_",""))
    if event:
        rows = calc_standings(event)
        medals = ["🥇","🥈","🥉"]
        text = f"📊 *{event['name']}*\n\n`# Участник         И  В  Н  П  Оч`\n`{'─'*35}`\n"
        for i, r in enumerate(rows):
            pts = r["w"]*3+r["d"]
            rank = medals[i] if i < 3 else f"{i+1}."
            name = r["name"][:15].ljust(15)
            text += f"`{rank} {name} {r['p']:2} {r['w']:2} {r['d']:2} {r['l']:2} {pts:3}`\n"
        await q.edit_message_text(text, parse_mode="Markdown")

async def new_event_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END
    ctx.user_data.clear()
    await update.message.reply_text("Введи название события:")
    return NEW_EVENT_NAME

async def new_event_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ev"] = {"name": update.message.text.strip()}
    kb = [[InlineKeyboardButton("👤 Игроки (1v1)", callback_data="pt_player")],[InlineKeyboardButton("👥 Команды", callback_data="pt_team")]]
    await update.message.reply_text("Тип участников:", reply_markup=InlineKeyboardMarkup(kb))
    return NEW_EVENT_TYPE

async def new_event_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["ev"]["participant_type"] = "player" if q.data == "pt_player" else "team"
    kb = [
        [InlineKeyboardButton("⚽ Футбол / FIFA", callback_data="sp_football")],
        [InlineKeyboardButton("🏀 Баскетбол", callback_data="sp_basketball")],
        [InlineKeyboardButton("♟ Шахматы", callback_data="sp_chess")],
        [InlineKeyboardButton("🎾 Теннис", callback_data="sp_tennis")],
        [InlineKeyboardButton("🎮 Другое", callback_data="sp_other")],
    ]
    await q.edit_message_text("Вид спорта:", reply_markup=InlineKeyboardMarkup(kb))
    return NEW_EVENT_SPORT

async def new_event_sport(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    sport = q.data.replace("sp_","")
    if sport == "other":
        await q.edit_message_text("Введи название вида спорта / игры:")
        return NEW_EVENT_SPORT_CUSTOM
    ctx.user_data["ev"]["sport"] = sport
    return await do_create_event(q, ctx, use_edit=True)

async def new_event_sport_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ev"]["sport"] = update.message.text.strip()
    return await do_create_event(update, ctx, use_edit=False)

async def do_create_event(target, ctx, use_edit=False):
    data = load_data()
    ev = ctx.user_data["ev"]
    eid = gen_id(data)
    event = {"id": eid, "name": ev["name"], "participant_type": ev["participant_type"],
             "sport": ev["sport"], "active": True, "participants": [], "matches": []}
    data["events"].append(event)
    save_data(data)
    ctx.user_data["ap_event_id"] = eid
    ptype = "игроков" if ev["participant_type"] == "player" else "команды"
    text = f"✅ *{ev['name']}* создано!\n\nТеперь добавь {ptype} — отправляй имена по одному.\n/done — завершить"
    if use_edit:
        await target.edit_message_text(text, parse_mode="Markdown")
    else:
        await target.message.reply_text(text, parse_mode="Markdown")
    return ADD_PARTICIPANT

async def add_participant_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END
    active = [e for e in data["events"] if e.get("active")]
    if not active:
        await update.message.reply_text("Нет активных событий.")
        return ConversationHandler.END
    if len(active) == 1:
        ctx.user_data["ap_event_id"] = active[0]["id"]
        current = ", ".join(active[0]["participants"]) or "пока никого"
        await update.message.reply_text(f"*{active[0]['name']}*\nСейчас: {current}\n\nВводи имена, /done — готово", parse_mode="Markdown")
        return ADD_PARTICIPANT
    kb = [[InlineKeyboardButton(e["name"], callback_data=f"ap_{e['id']}")] for e in active]
    await update.message.reply_text("Выбери событие:", reply_markup=InlineKeyboardMarkup(kb))
    return ADD_PARTICIPANT

async def add_participant_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["ap_event_id"] = q.data.replace("ap_","")
    data = load_data()
    event = get_event(data, ctx.user_data["ap_event_id"])
    current = ", ".join(event["participants"]) or "пока никого"
    await q.edit_message_text(f"*{event['name']}*\nСейчас: {current}\n\nВводи имена, /done — готово", parse_mode="Markdown")
    return ADD_PARTICIPANT

async def add_participant_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    data = load_data()
    event = get_event(data, ctx.user_data.get("ap_event_id"))
    if not event:
        await update.message.reply_text("Ошибка. /add_participant")
        return ConversationHandler.END
    if name in event["participants"]:
        await update.message.reply_text(f"*{name}* уже есть. Другое имя или /done", parse_mode="Markdown")
        return ADD_PARTICIPANT
    event["participants"].append(name)
    save_data(data)
    current = ", ".join(event["participants"])
    await update.message.reply_text(f"✅ *{name}* добавлен\nСейчас: {current}\n\nЕщё или /done", parse_mode="Markdown")
    return ADD_PARTICIPANT

async def done_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Готово!")
    return ConversationHandler.END

async def add_match_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END
    active = [e for e in data["events"] if e.get("active")]
    if not active:
        await update.message.reply_text("Нет активных событий.")
        return ConversationHandler.END
    if len(active) == 1:
        ctx.user_data["match"] = {"event_id": active[0]["id"]}
        if len(active[0]["participants"]) < 2:
            await update.message.reply_text("Нужно минимум 2 участника. /add_participant")
            return ConversationHandler.END
        kb = [[InlineKeyboardButton(p, callback_data=f"mh_{p}")] for p in active[0]["participants"]]
        await update.message.reply_text(f"*{active[0]['name']}*\nКто первый:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return MATCH_HOME
    kb = [[InlineKeyboardButton(e["name"], callback_data=f"me_{e['id']}")] for e in active]
    await update.message.reply_text("Выбери событие:", reply_markup=InlineKeyboardMarkup(kb))
    return MATCH_EVENT

async def match_event_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    eid = q.data.replace("me_","")
    ctx.user_data["match"] = {"event_id": eid}
    data = load_data()
    event = get_event(data, eid)
    if len(event["participants"]) < 2:
        await q.edit_message_text("Нужно минимум 2 участника. /add_participant")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(p, callback_data=f"mh_{p}")] for p in event["participants"]]
    await q.edit_message_text(f"*{event['name']}*\nКто первый:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
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
    h = ctx.user_data["match"]["home"]; a = ctx.user_data["match"]["away"]
    await q.edit_message_text(f"Счёт:\n*{h}* vs *{a}*\n\nФормат: `2:1`", parse_mode="Markdown")
    return MATCH_SCORE

async def match_score_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace("-",":").replace(" ","")
    if ":" not in text:
        await update.message.reply_text("Формат: `2:1`", parse_mode="Markdown")
        return MATCH_SCORE
    try:
        sh, sa = map(int, text.split(":"))
    except:
        await update.message.reply_text("Формат: `2:1`", parse_mode="Markdown")
        return MATCH_SCORE
    m = ctx.user_data["match"]
    data = load_data()
    event = get_event(data, m["event_id"])
    from datetime import datetime
    event["matches"].append({"home": m["home"], "away": m["away"], "score_home": sh, "score_away": sa, "played": True, "date": datetime.now().strftime("%d.%m.%Y")})
    save_data(data)
    if sh > sa: res = f"🏆 *{m['home']}*"
    elif sa > sh: res = f"🏆 *{m['away']}*"
    else: res = "🤝 Ничья"
    await update.message.reply_text(f"✅ *{m['home']}* {sh}:{sa} *{m['away']}*\n{res}\n\n/standings", parse_mode="Markdown")
    return ConversationHandler.END

async def finish_event_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END
    active = [e for e in data["events"] if e.get("active")]
    if not active:
        await update.message.reply_text("Нет активных событий.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"🏁 {e['name']}", callback_data=f"fin_{e['id']}")] for e in active]
    await update.message.reply_text("Какое завершить?", reply_markup=InlineKeyboardMarkup(kb))
    return FINISH_EVENT_SELECT

async def finish_event_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = load_data()
    event = get_event(data, q.data.replace("fin_",""))
    if event:
        event["active"] = False
        save_data(data)
        await q.edit_message_text(f"🏁 *{event['name']}* завершено!", parse_mode="Markdown")
    return ConversationHandler.END

async def delete_event_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END
    if not data["events"]:
        await update.message.reply_text("Событий нет.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"🗑 {e['name']}", callback_data=f"del_{e['id']}")] for e in data["events"]]
    await update.message.reply_text("Какое удалить?", reply_markup=InlineKeyboardMarkup(kb))
    return DELETE_EVENT

async def delete_event_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = load_data()
    event = get_event(data, q.data.replace("del_",""))
    if event:
        data["events"] = [e for e in data["events"] if e["id"] != event["id"]]
        save_data(data)
        await q.edit_message_text(f"✅ *{event['name']}* удалено.", parse_mode="Markdown")
    return ConversationHandler.END

async def add_admin_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END
    await update.message.reply_text("Введи Telegram user_id нового админа:")
    return ADD_ADMIN_ID

async def add_admin_id_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        new_id = int(update.message.text.strip())
    except:
        await update.message.reply_text("Введи число.")
        return ADD_ADMIN_ID
    data = load_data()
    if new_id not in data["admins"]:
        data["admins"].append(new_id)
        save_data(data)
        await update.message.reply_text(f"✅ Админ {new_id} добавлен.")
    else:
        await update.message.reply_text("Уже админ.")
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Отменено.")
    return ConversationHandler.END

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("new_event", new_event_start)],
        states={
            NEW_EVENT_NAME:        [MessageHandler(filters.TEXT & ~filters.COMMAND, new_event_name)],
            NEW_EVENT_TYPE:        [CallbackQueryHandler(new_event_type, pattern="^pt_")],
            NEW_EVENT_SPORT:       [CallbackQueryHandler(new_event_sport, pattern="^sp_")],
            NEW_EVENT_SPORT_CUSTOM:[MessageHandler(filters.TEXT & ~filters.COMMAND, new_event_sport_custom)],
            ADD_PARTICIPANT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, add_participant_name), CommandHandler("done", done_cmd)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_participant", add_participant_start)],
        states={
            ADD_PARTICIPANT: [CallbackQueryHandler(add_participant_cb, pattern="^ap_"), MessageHandler(filters.TEXT & ~filters.COMMAND, add_participant_name), CommandHandler("done", done_cmd)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_match", add_match_start)],
        states={
            MATCH_EVENT: [CallbackQueryHandler(match_event_cb, pattern="^me_")],
            MATCH_HOME:  [CallbackQueryHandler(match_home_cb, pattern="^mh_")],
            MATCH_AWAY:  [CallbackQueryHandler(match_away_cb, pattern="^ma_")],
            MATCH_SCORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, match_score_msg)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("finish_event", finish_event_start)],
        states={FINISH_EVENT_SELECT: [CallbackQueryHandler(finish_event_cb, pattern="^fin_")]},
        fallbacks=[CommandHandler("cancel", cancel)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("delete_event", delete_event_start)],
        states={DELETE_EVENT: [CallbackQueryHandler(delete_event_cb, pattern="^del_")]},
        fallbacks=[CommandHandler("cancel", cancel)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_admin", add_admin_start)],
        states={ADD_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_id_msg)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    ))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("events", events_cmd))
    app.add_handler(CommandHandler("standings", standings_cmd))
    app.add_handler(CallbackQueryHandler(standings_cb, pattern="^st_"))

    print("✅ ShymATC-CHAMP bot started")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
