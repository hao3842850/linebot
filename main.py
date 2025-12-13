# ============================================================
# 天堂M 吃王小幫手 - 乾淨穩定版（已移除 MongoDB / 名冊功能）
# ============================================================
# 功能：
# - 登記王：6666 / HHMM / HHMMSS 王名 [備註]
# - 查詢王：查 王名
# - 出：顯示即將重生排序（含過一）
# - 開機 / 維修：自動補登尚未登記的 CD 王
# - clear → 是：清空資料
# ============================================================

from fastapi import FastAPI, Request, Header
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError

import os
import json
from datetime import datetime, timedelta
import pytz

# =========================
# 基本設定
# =========================
app = FastAPI()

CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

line_bot_api = LineBotApi(CHANNEL_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

TZ = pytz.timezone("Asia/Taipei")
DB_FILE = "database.json"

# =========================
# 工具函式
# =========================

def now_tw():
    return datetime.now(TZ)


def init_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump({"boss": {}}, f, ensure_ascii=False, indent=2)


def load_db():
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


init_db()

# =========================
# 王資料
# =========================

alias_map = {
    "四色": ["四色", "76", "4", "四", "4色"],
    "小紅": ["小紅", "55", "紅", "R", "r"],
    "小綠": ["小綠", "54", "綠", "G", "g"],
    "守護螞蟻": ["守護螞蟻", "螞蟻", "29"],
    "巨大蜈蚣": ["巨大蜈蚣", "蜈蚣", "6"],
    "伊弗利特": ["伊弗利特", "伊弗", "EF", "ef"],
}

cd_map = {
    "四色": 2,
    "小紅": 2,
    "小綠": 2,
    "守護螞蟻": 3.5,
    "巨大蜈蚣": 2,
    "伊弗利特": 2,
}

fixed_bosses = {
    "巴風特": ["14:00", "20:00"],
    "惡魔": ["22:00"],
}

# =========================
# 邏輯函式
# =========================

def get_boss(name):
    for boss, aliases in alias_map.items():
        if name in aliases:
            return boss
    return None


def parse_time(token):
    now = now_tw()
    if token == "6666":
        return now
    if token.isdigit() and len(token) == 4:
        t = now.replace(hour=int(token[:2]), minute=int(token[2:]), second=0)
        if t > now:
            t -= timedelta(days=1)
        return t
    if token.isdigit() and len(token) == 6:
        t = now.replace(hour=int(token[:2]), minute=int(token[2:4]), second=int(token[4:]))
        if t > now:
            t -= timedelta(days=1)
        return t
    return None


def get_next_fixed_time(time_list):
    now = now_tw()
    today = now.strftime("%Y-%m-%d")
    times = []
    for t in time_list:
        dt = TZ.localize(datetime.strptime(f"{today} {t}", "%Y-%m-%d %H:%M"))
        if dt >= now:
            times.append(dt)
    if times:
        return min(times)
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    return TZ.localize(datetime.strptime(f"{tomorrow} {time_list[0]}", "%Y-%m-%d %H:%M"))


# =========================
# FastAPI Webhook
# =========================

@app.post("/callback")
async def callback(request: Request, x_line_signature=Header(None)):
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError:
        return "Invalid signature"
    return "OK"


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user = event.source.user_id
    msg = event.message.text.strip()
    db = load_db()

    group_id = event.source.group_id if event.source.type == "group" else user
    db.setdefault("boss", {})
    db["boss"].setdefault(group_id, {})
    boss_db = db["boss"][group_id]

    # =========================
    # clear
    # =========================
    if msg == "clear":
        db["__WAIT__"] = user
        save_db(db)
        line_bot_api.reply_message(event.reply_token, TextSendMessage("⚠️ 輸入『是』確認清除"))
        return

    if msg == "是" and db.get("__WAIT__") == user:
        save_db({"boss": {}})
        line_bot_api.reply_message(event.reply_token, TextSendMessage("✅ 已清空所有紀錄"))
        return

    # =========================
    # 查 王名
    # =========================
    if msg.startswith("查 "):
        boss = get_boss(msg.split(" ", 1)[1])
        if not boss or boss not in boss_db:
            line_bot_api.reply_message(event.reply_token, TextSendMessage("尚無紀錄"))
            return
        rec = boss_db[boss][-1]
        resp = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)
        text = (
            f"【{boss}】\n"
            f"死亡：{rec['kill']}\n"
            f"重生：{resp.strftime('%H:%M:%S')}"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text))
        return

    # =========================
    # 出
    # =========================
    if msg == "出":
        now = now_tw()
        items = []

    # ============================
    # 處理 CD 王（含 30 分鐘未打排序）
    # ============================
    for boss, cd in cd_map.items():
        if boss in boss_db and boss_db[boss]:
            rec = boss_db[boss][-1]
            base_respawn = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)

            t = base_respawn
            missed = 0
            step = timedelta(hours=cd)

            # 過一處理
            while t < now:
                t += step
                missed += 1

        # 已重生多久（分鐘）
            passed_minutes = int((now - base_respawn).total_seconds() // 60)

            line = f"{t.strftime('%H:%M:%S')} {boss}"

            if rec.get("note"):
                line += f" ({rec['note']})"

        # 30 分鐘內未吃 → 顯示 <XX分未打>
            if passed_minutes >= 0 and passed_minutes <= 30:
                line += f" <{passed_minutes}分未打>"

        # 過一顯示（你原本就有）
            if missed > 0:
                line += f"#過{missed}"

        # ⭐ 排序權重
        # 0 = 30 分鐘內未打
        # 1 = 其他已登記
            priority = 0 if 0 <= passed_minutes <= 30 else 1

            items.append((priority, t, line))


        for boss, times in fixed_bosses.items():
            t = get_next_fixed_time(times)
            items.append((t, f"{t.strftime('%H:%M:%S')} {boss}"))

        items.sort(key=lambda x: (x[0], x[1]))
        output = ["【即將重生列表】", ""]
        for _, t, line in items:
            output.append(line)

        line_bot_api.reply_message(event.reply_token, TextSendMessage("\n".join(output)))
        return

    # =========================
    # 登記王
    # =========================
    parts = msg.split(" ")

    if len(parts) >= 2:
        time_token = parts[0]
        boss_name = parts[1]
        note = " ".join(parts[2:]) if len(parts) > 2 else ""

    # === 解析時間 ===
        if time_token == "6666" or time_token.upper() == "K":
            t = now_tw()   # 現在時間
        else:
            t = parse_time(time_token)  # 0930 / 123045
            if not t:
                return

        boss = get_boss(boss_name)
        if not boss:
            return

        cd = cd_map.get(boss)
        if cd is None:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("此王為固定時間或未設定 CD")
            )
            return

        respawn = t + timedelta(hours=cd)

        rec = {
            "date": now_tw().strftime("%Y-%m-%d"),
            "kill": t.strftime("%H:%M:%S"),
            "respawn": respawn.isoformat(),
            "note": note,
            "user": user
        }

        boss_db.setdefault(boss, []).append(rec)
        save_db(db)

    # 回覆
        msg_lines = [
            f"🔥 已登記 {boss}",
            f"🕒 死亡時間：{rec['kill']}",
            f"✨ 重生時間：{respawn.strftime('%H:%M:%S')}"
        ]
        if note:
            msg_lines.append(f"📌 備註：{note}")

        line_bot_api.reply_message(
            event.reply_token,
                TextSendMessage("\n".join(msg_lines))
        )
        return



@app.get("/")
def root():
    return {"status": "OK"}
