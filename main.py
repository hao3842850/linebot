# ============================================================
# 天堂M 吃王小幫手
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
    "巨大蜈蚣": ["巨大蜈蚣", "蜈蚣", "海4", "海蟲", "姐夫", "6"],
    "左飛龍": ["左飛龍", "861", "86左飛龍", "左", "86下"],
    "右飛龍": ["右飛龍", "862", "86右飛龍", "右", "86上"],
    "伊弗利特": ["伊弗利特", "伊弗", "EF", "ef", "伊佛", "衣服"],
    "大腳瑪幽": ["大腳瑪幽", "大腳", "69"],
    "巨大飛龍": ["巨大飛龍", "巨飛", "GF", "82"],
    "中飛龍": ["中飛龍", "中", "中央龍", "83"],
    "東飛龍": ["東飛龍", "東", "85飛龍", "85"],
    "大黑長者": ["大黑長者", "大黑", "黑", "863"],
    "力卡溫": ["力卡溫", "狼人", "狼王", "22"],
    "卡司特": ["卡司特", "卡", "卡王", "25"],
    "巨大鱷魚": ["巨大鱷魚", "鱷魚", "51"],
    "強盜頭目": ["強盜頭目", "強盜", "32"],
    "樹精": ["樹精", "樹", "23", "24", "57"],
    "蜘蛛": ["蜘蛛", "D", "喇牙", "39"],
    "變形怪首領": ["變形怪首領", "變形怪", "變怪", "68"],
    "古代巨人": ["古代巨人", "古巨", "巨人", "78"],
    "惡魔監視者": ["惡魔監視者", "監視者", "象七", "象7", "7"],
    "不死鳥": ["不死鳥", "鳥", "452"],
    "死亡騎士": ["死亡騎士", "死騎", "05"],
    "克特": ["克特", "12"],
    "曼波王": ["曼波王", "兔", "兔王"],
    "賽尼斯的分身": ["賽尼斯的分身", "賽尼斯", "304"],
    "貝里斯": ["貝里斯", "大克特", "將軍", "821"],
    "烏勒庫斯": ["烏勒庫斯", "烏", "231"],
    "奈克偌斯": ["奈克偌斯", "奈", "571"],
}

cd_map = {
    "四色": 2, "小紅": 2, "小綠": 2, "守護螞蟻": 3.5, "巨大蜈蚣": 2,
    "左飛龍": 2, "右飛龍": 2, "伊弗利特": 2, "大腳瑪幽": 3,
    "巨大飛龍": 6, "中飛龍": 3, "東飛龍": 3, "大黑長者": 3,
    "力卡溫": 8, "卡司特": 7.5, "巨大鱷魚": 3, "強盜頭目": 3,
    "樹精": 6, "蜘蛛": 4, "變形怪首領": 3.5, "古代巨人": 8.5,
    "惡魔監視者": 6, "不死鳥": 8, "死亡騎士": 4, "克特": 10,
    "曼波王": 3, "賽尼斯的分身": 3, "貝里斯": 6, "烏勒庫斯": 6,
    "奈克偌斯": 4,
}

fixed_bosses = {
    "奇岩一樓王": ["00:00", "06:00", "12:00", "18:00"],
    "奇岩二樓王": ["07:00", "14:00", "21:00"],
    "奇岩三樓王": ["20:15"],
    "奇岩四樓王": ["21:15"],
    "黑暗四樓王": ["00:00", "18:00"],
    "三王": ["19:15"],
    "惡魔": ["22:00"],
    "巴風特": ["14:00", "20:00"],
    "異界炎魔": ["23:00"],
    "魔法師": ["01:00","03:00","05:00","07:00","09:00","11:00",
              "13:00","15:00","17:00","19:00","21:00","23:00"],
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
        db["boss"].pop(group_id, None)
        db.pop("__WAIT__", None)
        save_db(db)

        line_bot_api.reply_message(event.reply_token, TextSendMessage("✅ 已清空所有紀錄"))
        return

    # =========================
    # 查 王名
    # =========================
    
    if msg.startswith("查 "):
    name = msg.split(" ", 1)[1]
    boss = get_boss(name)

    if not boss:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("找不到此王")
        )
        return

    if boss not in boss_db or not boss_db[boss]:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("尚無紀錄")
        )
        return

    rec = boss_db[boss][-5]
    respawn = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)

    text = (
        f"【{boss}】\n"
        f"🕒 死亡時間：{rec['kill']}\n"
        f"✨ 重生時間：{respawn.strftime('%H:%M:%S')}"
    )

    if rec.get("note"):
        text += f"\n📌 備註：{rec['note']}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text)
    )
    return


    # =========================
    # 出
    # =========================
    if msg == "出":
        now = now_tw()
        time_items = []
        unregistered = []

    # ===== CD 王 =====
        for boss, cd in cd_map.items():
            if boss not in boss_db or not boss_db[boss]:
                unregistered.append(boss)
                continue

            rec = boss_db[boss][-1]
            base_respawn = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)

            t = base_respawn
            missed = 0
            step = timedelta(hours=cd)

            while t < now:
                t += step
                missed += 1

            passed_minutes = int((now - base_respawn).total_seconds() // 60)

            line = f"{t.strftime('%H:%M:%S')} {boss}"

            if rec.get("note"):
                line += f" ({rec['note']})"

            if 0 <= passed_minutes <= 30:
                line += f" <{passed_minutes}分未打>"
                priority = 0
            else:
                priority = 1

            if missed > 0:
                line += f"#過{missed}"

            time_items.append((priority, t, line))

    # ===== 固定王 =====
        for boss, times in fixed_bosses.items():
            t = get_next_fixed_time(times)
            time_items.append((2, t, f"{t.strftime('%H:%M:%S')} {boss}"))

    # 排序
        time_items.sort(key=lambda x: (x[0], x[1]))

    # ===== 組輸出 =====
        output = ["【即將重生列表】", ""]
        for _, _, line in time_items:
            output.append(line)

        if unregistered:
            output.append("")
            output.append("— 未登記 —")
            for b in unregistered:
                output.append(b)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("\n".join(output))
        )
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
