import os
import json
import base64
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# ── CONFIG ──
BOT_TOKEN     = os.environ.get("BOT_TOKEN")
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO   = "DaurenBol/ShymATC-CHAMP"
DATA_FILE     = "data.json"
BRANCH        = "main"

ADMINS = [2070550]

# ── CONVERSATION STATES ──
(
    NEW_EVENT_NAME, NEW_EVENT_TYPE, NEW_EVENT_SPORT,
    ADD_PARTICIPANT,
    MATCH_EVENT, MATCH_HOME, MATCH_AWAY, MATCH_SCORE,
    DELETE_EVENT,
    ADD_ADMIN_ID
) = range(10)

# ── GITHUB HELPERS ──
def github_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def load_data():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_FILE}"
    r = requests.get(url, headers=github_headers())
    if r.status_code == 200:
        content = r.json()
        raw = base64.b64decode(content["content"]).decode("utf-8")
        data = json.loads(raw)
        data["_sha"] = content["sha"]
        return data
    return {"events": [], "admins": [2070550], "_sha": None}

def save_data(data):
    sha = data.pop("_sha", None)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_FILE}"
    payload = {
        "message": "update data via bot",
        "content": encoded,
        "branch": BRANCH
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=github_headers(), json=payload)
    return r.status_code in [200, 201]

def is_admin(user_id, data=None):
    if data:
        return user_id in data.get("admins", [2070550])
    return user_id in ADMINS

def event_by_id(data, event_id):
    for e in data["events"]:
        if e["id"] == event_id:
            return e
    return None

def gen_id(data):
    ids = [e["id"] for e in data["events"]]
    i = 1
    while f"event_{i:03d}" in ids:
        i += 1
    return f"event_{i:03d}"

def standings(event):
    """Calculate standings for an event."""
    participants = {p: {"name": p, "p":0,"w":0,"d":0,"l":0,"gf":0,"ga":0} 
                   for p in event["participants"]}
    for m in event.get("matches", []):
        if m.get("played"):
            h, a = m["home"], m["away"]
            sh, sa = m["score_home"], m["score_away"]
            if h in participants:
                participants[h]["p"] += 1
                participants[h]["gf"] += sh
                participants[h]["ga"] += sa
                if sh > sa: participants[h]["w"] += 1
                elif sh == sa: participants[h]["d"] += 1
                else: participants[h]["l"] += 1
            if a in participants:
                participants[a]["p"] += 1
                participants[a]["gf"] += sa
                participants[a]["ga"] += sh
                if sa > sh: participants[a]["w"] += 1
                elif sa == sh: participants[a]["d"] += 1
                else: participants[a]["l"] += 1
    rows = list(participants.values())
    rows.sort(key=lambda x: (x["w"]*3+x["d"], x["gf"]-x["ga"]), reverse=True)
    return rows

# ── /start ──
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    uid = update.effective_user.id
    adm = is_admin(uid, data)
    text = (
        "🏆 *ShymATC\\-CHAMP*\n\n"
        "Команды:\n"
        "/events — список событий\n"
        "/standings — турнирная таблица\n"
    )
    if adm:
        text += (
            "\n*Админ\\-команды:*\n"
            "/new\\_event — создать событие\n"
            "/add\\_match — добавить результат\n"
            "/add\\_participant — добавить участника\n"
            "/delete\\_event — удалить событие\n"
            "/add\\_admin — добавить админа\n"
        )
    await update.message.reply_text(text, parse_mode="MarkdownV2")

# ── /events ──
async def events_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data["events"]:
        await update.message.reply_text("Событий пока нет. Создай первое: /new_event")
        return
    text = "📋 *Все события:*\n\n"
    for e in data["events"]:
        status = "🟢 Активен" if e.get("active") else "⚫ Завершён"
        ptype = "👥 Команды" if e.get("participant_type") == "team" else "👤 Игроки"
        count = len(e.get("participants", []))
        matches_played = len([m for m in e.get("matches", []) if m.get("played")])
        text += (
            f"*{e['name']}*\n"
            f"{status} · {ptype} · {e.get('sport','').upper()}\n"
            f"Участников: {count} · Матчей: {matches_played}\n\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown")

# ── /standings ──
async def standings_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    active = [e for e in data["events"] if e.get("active")]
    if not active:
        await update.message.reply_text("Нет активных событий.")
        return
    if len(active) == 1:
        await send_standings(update, active[0])
        return
    keyboard = [[InlineKeyboardButton(e["name"], callback_data=f"st_{e['id']}")] for e in active]
    await update.message.reply_text("Выбери событие:", reply_markup=InlineKeyboardMarkup(keyboard))

async def send_standings(update, event):
    rows = standings(event)
    medals = ["🥇","🥈","🥉"]
    text = f"📊 *{event['name']}*\n\n"
    text += "`#  Участник          И  В  Н  П  Оч`\n"
    text += "`" + "─"*36 + "`\n"
    for i, r in enumerate(rows):
        pts = r["w"]*3 + r["d"]
        medal = medals[i] if i < 3 else f"{i+1} "
        name = r["name"][:16].ljust(16)
        line = f"{medal} {name} {r['p']:2} {r['w']:2} {r['d']:2} {r['l']:2} {pts:3}"
        text += f"`{line}`\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def standings_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    event_id = query.data.replace("st_", "")
    data = load_data()
    event = event_by_id(data, event_id)
    if event:
        await send_standings(query, event)

# ── /new_event ──
async def new_event_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END
    await update.message.reply_text("Введи название события:\n(например: FIFA Tournament Март 2026)")
    return NEW_EVENT_NAME

async def new_event_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new_event"] = {"name": update.message.text.strip()}
    keyboard = [
        [InlineKeyboardButton("👤 Игроки (1v1)", callback_data="pt_player")],
        [InlineKeyboardButton("👥 Команды", callback_data="pt_team")],
    ]
    await update.message.reply_text("Тип участников:", reply_markup=InlineKeyboardMarkup(keyboard))
    return NEW_EVENT_TYPE

async def new_event_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["new_event"]["participant_type"] = "player" if query.data == "pt_player" else "team"
    keyboard = [
        [InlineKeyboardButton("⚽ Футбол / FIFA", callback_data="sp_football")],
        [InlineKeyboardButton("♟ Шахматы", callback_data="sp_chess")],
        [InlineKeyboardButton("🎾 Теннис", callback_data="sp_tennis")],
        [InlineKeyboardButton("🎮 Другое", callback_data="sp_other")],
    ]
    await query.edit_message_text("Вид спорта:", reply_markup=InlineKeyboardMarkup(keyboard))
    return NEW_EVENT_SPORT

async def new_event_sport(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sport = query.data.replace("sp_", "")
    ctx.user_data["new_event"]["sport"] = sport
    data = load_data()
    new_id = gen_id(data)
    event = {
        "id": new_id,
        "name": ctx.user_data["new_event"]["name"],
        "participant_type": ctx.user_data["new_event"]["participant_type"],
        "sport": sport,
        "active": True,
        "participants": [],
        "matches": []
    }
    data["events"].append(event)
    ok = save_data(data)
    if ok:
        await query.edit_message_text(
            f"✅ Событие создано!\n\n"
            f"*{event['name']}*\n"
            f"ID: `{new_id}`\n\n"
            f"Теперь добавь участников: /add\\_participant",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text("❌ Ошибка сохранения. Проверь GitHub Token.")
    return ConversationHandler.END

# ── /add_participant ──
async def add_participant_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END
    active = [e for e in data["events"] if e.get("active")]
    if not active:
        await update.message.reply_text("Нет активных событий.")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(e["name"], callback_data=f"ap_{e['id']}")] for e in active]
    await update.message.reply_text("Выбери событие:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADD_PARTICIPANT

async def add_participant_event(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["ap_event_id"] = query.data.replace("ap_", "")
    data = load_data()
    event = event_by_id(data, ctx.user_data["ap_event_id"])
    ptype = "игрока (имя)" if event["participant_type"] == "player" else "команду (название)"
    current = ", ".join(event["participants"]) if event["participants"] else "пока никого"
    await query.edit_message_text(
        f"Событие: *{event['name']}*\n"
        f"Участники: {current}\n\n"
        f"Введи имя {ptype}:\n(или /done чтобы завершить)",
        parse_mode="Markdown"
    )
    return ADD_PARTICIPANT

async def add_participant_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "/done":
        await update.message.reply_text("✅ Готово!")
        return ConversationHandler.END
    data = load_data()
    event = event_by_id(data, ctx.user_data["ap_event_id"])
    if text in event["participants"]:
        await update.message.reply_text(f"'{text}' уже есть. Введи другое имя или /done")
        return ADD_PARTICIPANT
    event["participants"].append(text)
    save_data(data)
    current = ", ".join(event["participants"])
    await update.message.reply_text(
        f"✅ Добавлен: *{text}*\n\nСейчас: {current}\n\nЕщё или /done",
        parse_mode="Markdown"
    )
    return ADD_PARTICIPANT

# ── /add_match ──
async def add_match_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END
    active = [e for e in data["events"] if e.get("active")]
    if not active:
        await update.message.reply_text("Нет активных событий.")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(e["name"], callback_data=f"me_{e['id']}")] for e in active]
    await update.message.reply_text("Выбери событие:", reply_markup=InlineKeyboardMarkup(keyboard))
    return MATCH_EVENT

async def match_event_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    event_id = query.data.replace("me_", "")
    ctx.user_data["match"] = {"event_id": event_id}
    data = load_data()
    event = event_by_id(data, event_id)
    if len(event["participants"]) < 2:
        await query.edit_message_text("Нужно минимум 2 участника. /add_participant")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(p, callback_data=f"mh_{p}")] for p in event["participants"]]
    await query.edit_message_text("Кто играл дома (или первый):", reply_markup=InlineKeyboardMarkup(keyboard))
    return MATCH_HOME

async def match_home_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["match"]["home"] = query.data.replace("mh_", "")
    data = load_data()
    event = event_by_id(data, ctx.user_data["match"]["event_id"])
    others = [p for p in event["participants"] if p != ctx.user_data["match"]["home"]]
    keyboard = [[InlineKeyboardButton(p, callback_data=f"ma_{p}")] for p in others]
    await query.edit_message_text("Кто играл в гостях (или второй):", reply_markup=InlineKeyboardMarkup(keyboard))
    return MATCH_AWAY

async def match_away_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["match"]["away"] = query.data.replace("ma_", "")
    home = ctx.user_data["match"]["home"]
    away = ctx.user_data["match"]["away"]
    await query.edit_message_text(
        f"Введи счёт матча:\n*{home}* vs *{away}*\n\nФормат: `2:1` или `0:0`",
        parse_mode="Markdown"
    )
    return MATCH_SCORE

async def match_score_entered(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace("-", ":").replace(" ", "")
    if ":" not in text:
        await update.message.reply_text("Неверный формат. Введи как `2:1`", parse_mode="Markdown")
        return MATCH_SCORE
    try:
        sh, sa = map(int, text.split(":"))
    except:
        await update.message.reply_text("Неверный формат. Введи как `2:1`", parse_mode="Markdown")
        return MATCH_SCORE
    match_data = ctx.user_data["match"]
    data = load_data()
    event = event_by_id(data, match_data["event_id"])
    from datetime import datetime
    match = {
        "home": match_data["home"],
        "away": match_data["away"],
        "score_home": sh,
        "score_away": sa,
        "played": True,
        "date": datetime.now().strftime("%d.%m.%Y")
    }
    event["matches"].append(match)
    save_data(data)
    result = "🏆 Победа хозяев" if sh > sa else ("🏆 Победа гостей" if sa > sh else "🤝 Ничья")
    await update.message.reply_text(
        f"✅ Результат сохранён!\n\n"
        f"*{match_data['home']}* {sh} : {sa} *{match_data['away']}*\n"
        f"{result}\n\n"
        f"Таблица: /standings",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ── /delete_event ──
async def delete_event_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END
    if not data["events"]:
        await update.message.reply_text("Событий нет.")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(f"🗑 {e['name']}", callback_data=f"del_{e['id']}")] for e in data["events"]]
    await update.message.reply_text("Какое событие удалить?", reply_markup=InlineKeyboardMarkup(keyboard))
    return DELETE_EVENT

async def delete_event_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    event_id = query.data.replace("del_", "")
    data = load_data()
    event = event_by_id(data, event_id)
    if event:
        data["events"] = [e for e in data["events"] if e["id"] != event_id]
        save_data(data)
        await query.edit_message_text(f"✅ Событие *{event['name']}* удалено.", parse_mode="Markdown")
    return ConversationHandler.END

# ── /add_admin ──
async def add_admin_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not is_admin(update.effective_user.id, data):
        await update.message.reply_text("⛔ Нет доступа.")
        return ConversationHandler.END
    await update.message.reply_text(
        "Введи Telegram user_id нового админа:\n(узнать можно через @userinfobot)"
    )
    return ADD_ADMIN_ID

async def add_admin_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        new_id = int(update.message.text.strip())
    except:
        await update.message.reply_text("Неверный формат. Введи число.")
        return ADD_ADMIN_ID
    data = load_data()
    if new_id not in data["admins"]:
        data["admins"].append(new_id)
        save_data(data)
        await update.message.reply_text(f"✅ Админ {new_id} добавлен.")
    else:
        await update.message.reply_text("Этот пользователь уже админ.")
    return ConversationHandler.END

# ── /cancel ──
async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.")
    return ConversationHandler.END

# ── MAIN ──
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # New event conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("new_event", new_event_start)],
        states={
            NEW_EVENT_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, new_event_name)],
            NEW_EVENT_TYPE:  [CallbackQueryHandler(new_event_type, pattern="^pt_")],
            NEW_EVENT_SPORT: [CallbackQueryHandler(new_event_sport, pattern="^sp_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # Add participant conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_participant", add_participant_start)],
        states={
            ADD_PARTICIPANT: [
                CallbackQueryHandler(add_participant_event, pattern="^ap_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_participant_name),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # Add match conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_match", add_match_start)],
        states={
            MATCH_EVENT: [CallbackQueryHandler(match_event_selected, pattern="^me_")],
            MATCH_HOME:  [CallbackQueryHandler(match_home_selected, pattern="^mh_")],
            MATCH_AWAY:  [CallbackQueryHandler(match_away_selected, pattern="^ma_")],
            MATCH_SCORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, match_score_entered)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # Delete event conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("delete_event", delete_event_start)],
        states={
            DELETE_EVENT: [CallbackQueryHandler(delete_event_confirm, pattern="^del_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # Add admin conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_admin", add_admin_start)],
        states={
            ADD_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # Simple commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("events", events_cmd))
    app.add_handler(CommandHandler("standings", standings_cmd))
    app.add_handler(CallbackQueryHandler(standings_callback, pattern="^st_"))

    print("✅ ShymATC-CHAMP bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
