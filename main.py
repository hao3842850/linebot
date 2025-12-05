# ============================================================
# 天堂M 吃王小幫手 - 完整最終版 main.py
# 支援：
# - 登記 6666 / HHMM / HHMMSS
# - 查詢王 / 查 王名
# - 出（固定王 + CD 王混排序）
# - 過一 / 過二
# - clear → 是
# - 刪除王
# - 群組聊天不回覆，僅處理指令
# - 全台灣時間 UTC+8
# ============================================================

from fastapi import FastAPI, Request, Header
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError

import os
import json
from datetime import datetime, timedelta
import pytz

def get_username(user_id):
    try:
        profile = line_bot_api.get_profile(user_id)
        return profile.display_name
    except:
        return user_id  # fallback


app = FastAPI()

CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

line_bot_api = LineBotApi(CHANNEL_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

TZ = pytz.timezone("Asia/Taipei")

def now_tw():
    return datetime.now(TZ)

DB_FILE = "database.json"

if not os.path.exists(DB_FILE):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False, indent=2)

def load_db():
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

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

def get_boss(name):
    for k, arr in alias_map.items():
        if name in arr:
            return k
    return None

def get_next_fixed_time(time_list):
    now = now_tw()
    today_str = now.strftime("%Y-%m-%d")
    candidates = []
    for t in time_list:
        dt = datetime.strptime(f"{today_str} {t}", "%Y-%m-%d %H:%M")
        dt = TZ.localize(dt)
        if dt >= now:
            candidates.append(dt)
    if candidates:
        return min(candidates)
    tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    dt = datetime.strptime(f"{tomorrow_str} {time_list[0]}", "%Y-%m-%d %H:%M")
    return TZ.localize(dt)

def parse_time(token):
    now = now_tw()
    if token == "6666":
        return now
    if len(token) == 4:
        h, m = int(token[:2]), int(token[2:])
        t = now.replace(hour=h, minute=m, second=0)
        if t > now:
            t -= timedelta(days=1)
        return t
    if len(token) == 6:
        h, m, s = int(token[:2]), int(token[2:4]), int(token[4:])
        t = now.replace(hour=h, minute=m, second=s)
        if t > now:
            t -= timedelta(days=1)
        return t
    return None

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

    if msg == "clear":
        db["__WAIT_CONFIRM__"] = user
        save_db(db)
        line_bot_api.reply_message(event.reply_token,
                                   TextSendMessage("⚠️ 確定要清除所有紀錄嗎？請輸入「是」確認"))
        return

    if msg == "是" and db.get("__WAIT_CONFIRM__") == user:
        for k in list(db.keys()):
            if k != "__WAIT_CONFIRM__":
                db.pop(k)
        save_db({})
        line_bot_api.reply_message(event.reply_token,
                                   TextSendMessage("所有王的紀錄已清除"))
        return

    if msg.startswith("刪除 ") or msg.startswith("del "):
        name = msg.split(" ",1)[1]
        boss = get_boss(name)
        if boss and boss in db:
            db.pop(boss)
            save_db(db)
            line_bot_api.reply_message(event.reply_token,
                                       TextSendMessage(f"已刪除 {boss} 的紀錄"))
        else:
            line_bot_api.reply_message(event.reply_token,
                                       TextSendMessage("找不到王名"))
        return

# --------------------------------------------------------
# help 說明
# --------------------------------------------------------
if msg in ["help", "指令", "幫助"]:
    help_text = (
        "【天堂M 吃王小幫手 指令說明】\n\n"
        "📌 登記王\n"
        "  6666 王名\n"
        "  HHMM 王名\n"
        "  HHMMSS 王名\n"
        "  範例： 0155 四色\n\n"
        "📌 查詢王紀錄\n"
        "  查 王名\n\n"
        "📌 顯示全部王重生排序\n"
        "  出\n\n"
        "📌 刪除王紀錄\n"
        "  刪除 王名\n\n"
        "📌 清空全部紀錄\n"
        "  clear → 再輸入：是\n"
    )

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(help_text)
    )
    return


    if msg == "出":
        lines = ["【即將重生列表】", ""]
        now = now_tw()
        items = []

        for boss, cd in cd_map.items():
            if boss in db:
                rec = db[boss][-1]
                t = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)
                while t < now:
                    t += timedelta(hours=cd)
                items.append(
                    (t, f"{t.strftime('%H:%M:%S')} {boss}" +
                        (f" ({rec['note']})" if rec["note"] else ""))
                )

        for boss, times in fixed_bosses.items():
            t = get_next_fixed_time(times)
            items.append((t, f"{t.strftime('%H:%M:%S')} {boss}（固定）"))

        items.sort(key=lambda x: x[0])
        for t, s in items:
            lines.append(s)

        lines.append("")
        lines.append("--- 未登記 ---")
        for boss in alias_map:
            if boss not in db:
                lines.append(boss)

        line_bot_api.reply_message(event.reply_token,
                                   TextSendMessage("\n".join(lines)))
        return

    if msg.startswith("查 "):
        name = msg.split(" ",1)[1]
        boss = get_boss(name)
        if boss is None:
            return

        if boss not in db:
            line_bot_api.reply_message(event.reply_token,
                                       TextSendMessage("尚無紀錄"))
            return

        lines = [f"【{boss} 最近登記紀錄】", ""]

        for rec in db[boss][-5:]:
            nickname = get_username(rec["user"])
            lines.append(f"{rec['date']} by {nickname}")
            lines.append(f"死亡 {rec['kill']}")
            lines.append(f"重生 {rec['respawn'].split('T')[1]}")
            if rec["note"]:
                lines.append(f"備註: {rec['note']}")
            lines.append("")

        line_bot_api.reply_message(event.reply_token,
                                   TextSendMessage("\n".join(lines)))
        return

    parts = msg.split(" ")
    if len(parts) >= 2:
        t = parse_time(parts[0])
        if t:
            boss = get_boss(parts[1])
            if boss:
                note = " ".join(parts[2:]) if len(parts) > 2 else ""
                cd = cd_map[boss]
                respawn = t + timedelta(hours=cd)

                rec = {
                    "date": now_tw().strftime("%Y-%m-%d"),
                    "kill": t.strftime("%H:%M:%S"),
                    "respawn": respawn.isoformat(),
                    "note": note,
                    "user": user
                }

                if boss not in db:
                    db[boss] = []
                db[boss].append(rec)
                save_db(db)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        f"已登記 {boss}\n死亡時間：{rec['kill']}\n下次重生時間：{respawn.strftime('%H:%M:%S')}"
                    )
                )
                return

    return

@app.get("/")
def root():
    return {"status": "OK", "msg": "Boss helper running."}
