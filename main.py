# === MongoDB 名冊系統 ===
from pymongo import MongoClient

MONGO_URL = "mongodb+srv://hao:wenhao0222@cluster0.utvdkw9.mongodb.net/linebot?retryWrites=true&w=majority&appName=Cluster0"
mongo_client = MongoClient(MONGO_URL)
mongo_db = mongo_client["linebot"]
roster_collection = mongo_db["roster"]

from fastapi import FastAPI, Request, Header
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError

# === 管理員清單 ===
ADMINS = {
    "U68c2e4197ef63a2c7fc923baae1f3667"   # 管理員 LINE user_id（可新增多個）
}

import os
import json
from datetime import datetime, timedelta
import pytz
import math

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
        db = json.load(f)

    # 確保 KPI 結構存在
    if "kpi" not in db:
        db["kpi"] = {
            "yes": 0,
            "no": 0
        }

    return db


def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def handle_reboot_time(event, text, db, boss_db):
    """
    開機指令：
    玩家輸入 → 開機 0930 或 開機0930
    """

    # 允許：開機0930 / 開機 0930
    t = text.replace("開機", "").replace("維修", "").strip()

    if not t.isdigit():
        return line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("❌ 格式錯誤\n\n用法：開機 HHMM\n例：開機 0930")
        )

    reboot_token = t
    kill_time = parse_time(reboot_token)

    if kill_time is None:
        return line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("❌ 時間格式錯誤")
        )

    updated = []

    # 逐一檢查所有 CD 王
    for boss, cd in cd_map.items():

        # 已登記過的不重設
        if boss in boss_db and len(boss_db[boss]) > 0:
            continue

        # 固定王不處理（保險雙重判斷）
        if boss in fixed_bosses:
            continue

        # 計算重生時間
        respawn = kill_time + timedelta(hours=cd)

        record = {
            "date": now_tw().strftime("%Y-%m-%d"),
            "kill": kill_time.strftime("%H:%M:%S"),
            "respawn": respawn.isoformat(),
            "note": "開機",
            "user": event.source.user_id,
        }

        if boss not in boss_db:
            boss_db[boss] = []

        boss_db[boss].append(record)

        updated.append(f"{boss} → {kill_time.strftime('%H:%M')}")

    save_db(db)

    if not updated:
        return line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("所有 CD 王皆已有紀錄，無需重置。")
        )

    text_output = ["【開機 — 已更新未登記王】", ""]
    text_output.extend(updated)

    return line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage("\n".join(text_output))
    )

    parts = text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return reply_text(event.reply_token, "格式錯誤\n正確用法：開機 0930")

    reboot_time = parts[1]

    if chat_id not in boss_data:
        return reply_text(event.reply_token, "尚未初始化資料")

    updated_list = []

    # 只更新 CD 王
    for boss, info in boss_data[chat_id]["CD王"].items():
        if info["time"] == "未登記":
            info["time"] = reboot_time
            updated_list.append(f"{boss} → {reboot_time}")

    if not updated_list:
        return reply_text(event.reply_token, "所有 CD 王都已有登記時間\n無需更新。")

    save_database()

    return reply_flex_message(event.reply_token, generate_reboot_flex(reboot_time, updated_list))


alias_map = {
    "四色": ["四色", "76", "4", "四", "4色"],
    "小紅": ["小紅", "55", "紅", "R", "r"],
    "小綠": ["小綠", "54", "綠", "G", "g"],
    "守護螞蟻": ["守護螞蟻", "螞蟻", "29"],
    "巨大蜈蚣": ["巨大蜈蚣", "蜈蚣", "海4", "海蟲", "姐夫", "6"],
    "86左飛龍": ["左飛龍", "861", "86左飛龍", "左", "86下"],
    "86右飛龍": ["右飛龍", "862", "86右飛龍", "右", "86上"],
    "伊弗利特": ["伊弗利特", "伊弗", "EF", "ef", "伊佛", "衣服"],
    "大腳瑪幽": ["大腳瑪幽", "大腳", "69"],
    "巨大飛龍": ["巨大飛龍", "巨飛", "GF", "82"],
    "83飛龍": ["中飛龍", "中", "中央龍", "83"],
    "85飛龍": ["東飛龍", "東", "85飛龍", "85"],
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
    "86左飛龍": 2, "86右飛龍": 2, "伊弗利特": 2, "大腳瑪幽": 3,
    "巨大飛龍": 6, "83中飛龍": 3, "85東飛龍": 3, "大黑長者": 3,
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
    # 取得群組 ID，私訊就用 user ID
    group_id = (
        event.source.group_id 
        if event.source.type == "group"
        else user
    )
    # 讓每個群組有自己的王紀錄空間
    if "boss" not in db:
        db["boss"] = {}

    if group_id not in db["boss"]:
        db["boss"][group_id] = {}

    boss_db = db["boss"][group_id]


        # 顯示自己的 LINE User ID
    if msg == "我的ID":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(f"你的 LINE USER ID：\n{user}")
        )
        return

    
    if msg == "clear":
        db["__WAIT_CONFIRM__"] = user
        save_db(db)
        line_bot_api.reply_message(event.reply_token,
                                   TextSendMessage("⚠️ 確定要清除所有紀錄嗎？/n 請輸入「是」確認"))
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
        if boss and boss in boss_db:
            boss_db.pop(boss)
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
            "【伊娃小幫手 指令說明】\n\n"
            "📌【登記王】\n"
            "  6666 王名 [備註]\n"
            "  HHMM 王名 [備註]\n"
            "  HHMMSS 王名 [備註]\n"
            "  ➤ 用於登記王死亡時間\n"
            "  ➤ 範例：0155 四色、 6666 伊弗利特、 031522 小紅 敵人吃\n"
            "📌【查詢王紀錄】\n"
            "  查 王名\n"
            "  ➤ 用於查詢王登記紀錄\n"
            "  ➤ 範例：查 四色\n"
            "📌【顯示全部王重生排序】\n"
            "  出\n"
            "  ➤ 用於查詢即將重生列表\n"
            "📌【刪除王紀錄】\n"
            "  刪除 王名\n"
            "  ➤ 用於刪除王死亡紀錄\n"
            "  ➤ 範例：刪除 四色\n"
            "📌【開機時間紀錄】\n"
            "  開機 時間\n"
            "  ➤ 用於維修後得知開機時間紀錄\n"
            "📌【清空全部紀錄】\n"
            "  clear → 再輸入：是\n"
            "  ➤ 用於維修後重製王表\n"
            " -名冊功能- \n"
            "  ➤ 用於管理群組內用戶\n\n"
            "  加入名冊 \n"
            "  加入名冊 血盟名 遊戲名\n"
            "  加入名冊 \n"
            "  加入名冊 血盟名 遊戲名\n"
        )

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(help_text)
        )
        return

    # --------------------------------------------------------
    # 王列表（顯示簡稱對照表）
    # --------------------------------------------------------
    if msg in ["王列表", "王清單", "全部王", "boss list"]:
        lines = ["【王列表 - 簡稱對照表】", ""]
        for boss, names in alias_map.items():
            lines.append(f"{boss}：{' / '.join(names)}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("\n".join(lines))
        )
        return
        
    # --------------------------------------------------------
    # 王重生：顯示所有 CD 王的重生時間（CD 小時）
    # --------------------------------------------------------
    if msg in ["王重生", "cd王", "重生時間", "cd列表"]:
        lines = ["【CD 王重生時間表】", ""]
        for boss, cd in cd_map.items():
            lines.append(f"{boss}：{cd} 小時")

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("\n".join(lines))
        )
        return
    # --------------------------------------------------------
    # 出：顯示所有重生排序（CD + 固定 + 未登記）
    # --------------------------------------------------------
    if msg == "出":
        now = now_tw()
        items = []

        # 所有可能會被列出的王（CD + 固定）
        boss_list = list(cd_map.keys()) + list(fixed_bosses.keys())

        # ============================
        # 處理 CD 王
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

                line = f"{t.strftime('%H:%M:%S')} {boss}"

                if rec.get("note"):
                    line += f" ({rec['note']})"

                if missed > 0:
                    line += f"（過{missed}）"

                items.append((t, line))

        # ============================
        # 處理固定王
        # ============================
        for boss, times in fixed_bosses.items():
            next_time = get_next_fixed_time(times)
            line = f"{next_time.strftime('%H:%M:%S')} {boss}"
            items.append((next_time, line))

        # ============================
        # 處理未登記王（永遠在下面）
        # ============================
        for boss in cd_map.keys():  # ← 只使用 CD 王，避免固定王出現在未登記
            if boss not in boss_db or len(boss_db[boss]) == 0:
                fake_time = datetime(9999, 1, 1, tzinfo=TZ)
                items.append((fake_time, boss))
                
        # 排序（未登記因 fake_time 排最後）
        items.sort(key=lambda x: x[0])

        # ============================
        # 開始輸出
        # ============================
        output = []
        output.append("【即將重生列表】")
        output.append("")

        has_unregistered = False  # ← 新增判斷用

# 已登記王
        for t, line in items:
            if t.year == 9999:
                has_unregistered = True
                continue
            output.append(line)

        if has_unregistered:
            title = "未登記"
            separator = f"— {title} —"   # ← 超短分隔線
            output.append(separator)

            for t, line in items:
                if t.year == 9999:
                    output.append(line)

# 回覆訊息
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("\n".join(output))
        )
        return
    # ============================
    # 名冊系統
    # ============================

    # 加入名冊 血盟名 遊戲名
    if msg.startswith("加入名冊"):
        parts = msg.split(" ")
        if len(parts) < 3:
            line_bot_api.reply_message(event.reply_token,
                TextSendMessage("用法：加入名冊 血盟名 遊戲名"))
            return
        
        blood = parts[1]
        game_name = " ".join(parts[2:])

        roster_collection.update_one(
            {"user_id": user},
            {"$set": {
                "user_id": user,
                "blood": blood,
                "game_name": game_name
            }},
            upsert=True
        )

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(f"已加入名冊：\n血盟：{blood}\n遊戲名：{game_name}")
        )
        return

    # 查名冊（查自己）
    if msg == "查名冊":
        data = roster_collection.find_one({"user_id": user})
        if not data:
            line_bot_api.reply_message(event.reply_token,
                TextSendMessage("你尚未加入名冊"))
            return
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                f"【你的名冊】\n血盟：{data['blood']}\n遊戲名：{data['game_name']}"
            )
        )
        return

    # 查名冊 @某人
    if msg.startswith("查名冊 @"):
        mention = msg.replace("查名冊 @", "").strip()
        # 查 LINE 暱稱 → user_id
        members = roster_collection.find({})
        target_user_id = None

        for m in members:
            try:
                profile = line_bot_api.get_profile(m["user_id"])
                if profile.display_name == mention:
                    target_user_id = m["user_id"]
                    break
            except:
                continue

        if not target_user_id:
            line_bot_api.reply_message(event.reply_token,
                TextSendMessage("查無此人（請確認 @後的 LINE 暱稱正確）"))
            return

        data = roster_collection.find_one({"user_id": target_user_id})

        if not data:
            line_bot_api.reply_message(event.reply_token,
                TextSendMessage("該用戶不在名冊中"))
            return

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                f"【{mention} 的名冊】\n血盟：{data['blood']}\n遊戲名：{data['game_name']}"
            )
        )
        return

        # 管理員刪除名冊 @某人
    if msg.startswith("管理刪除名冊 @"):
        if user not in ADMINS:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("❌ 你沒有管理員權限")
            )
            return
        
        target_name = msg.replace("刪除名冊 @", "").strip()

        # 找 LINE 名稱對應 user_id
        members = roster_collection.find({})
        target_user_id = None

        for m in members:
            try:
                profile = line_bot_api.get_profile(m["user_id"])
                if profile.display_name == target_name:
                    target_user_id = m["user_id"]
                    break
            except:
                continue

        if not target_user_id:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("❌ 找不到該玩家，請確認 LINE 暱稱是否正確")
            )
            return
        
        roster_collection.delete_one({"user_id": target_user_id})

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(f"🗑 已刪除「{target_name}」的名冊資料")
        )
        return

    
    # 刪除名冊
    if msg == "刪除名冊":
        roster_collection.delete_one({"user_id": user})
        line_bot_api.reply_message(event.reply_token,
            TextSendMessage("你的名冊資料已刪除"))
        return

    # 查詢血盟 血盟名
    if msg.startswith("查詢血盟"):
        parts = msg.split(" ")
        if len(parts) < 2:
            line_bot_api.reply_message(event.reply_token,
                TextSendMessage("用法：查詢血盟 血盟名"))
            return
        
        blood = parts[1]
        members = list(roster_collection.find({"blood": blood}))

        if not members:
            line_bot_api.reply_message(event.reply_token,
                TextSendMessage(f"血盟「{blood}」目前沒有登記資料"))
            return

        lines = [f"【血盟：{blood} 名冊】\n"]

        for m in members:
            try:
                profile = line_bot_api.get_profile(m["user_id"])
                nickname = profile.display_name
            except:
                nickname = "(無法讀取名稱)"

            lines.append(f"🧍 {nickname} — {m['game_name']}")

        line_bot_api.reply_message(event.reply_token,
            TextSendMessage("\n".join(lines)))
        return

    
    
    
    
    
    
    if msg.startswith("查 "):
        name = msg.split(" ",1)[1]
        boss = get_boss(name)
        if boss is None:
            return

        if boss not in boss_db:
            line_bot_api.reply_message(event.reply_token,
                                       TextSendMessage("尚無紀錄"))
            return

        lines = [f"【{boss} 最近登記紀錄】", ""]

        for rec in boss_db[boss][-5:]:  # 顯示最多五筆
            nickname = get_username(rec["user"])
            # 解析重生時間，去除 +08:00
            resp = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)
            resp_str = resp.strftime("%H:%M:%S")

            lines.append(f"🔥 登記日期：{rec['date']}")
            lines.append(f"🧍‍♂️ 玩家：{nickname}")
            lines.append(f"🕒 死亡時間：{rec['kill']}")
            lines.append(f"✨ 重生時間：{resp_str}")

            if rec["note"].strip() != "":
                lines.append(f"📌 備註：{rec['note']}")
            
        line_bot_api.reply_message(event.reply_token,
                                   TextSendMessage("\n".join(lines)))
        return

    if msg.startswith("開機") or msg.startswith("維修"):
        return handle_reboot_time(event, msg, db, boss_db)

    parts = msg.split(" ")

    
    parts = msg.split(" ")
    if len(parts) >= 2:
        t = parse_time(parts[0])
        if t:
            boss = get_boss(parts[1])
            if boss:
                note = " ".join(parts[2:]) if len(parts) > 2 else ""
                cd = cd_map.get(boss, None)
                if cd is None:
                    # 如果是固定時間的王，可以用不同邏輯或直接回覆不可用 cd 登記
                    line_bot_api.reply_message(event.reply_token,
                                               TextSendMessage("此王為固定時間或未設定 CD，請用固定時間查詢"))
                    return

                respawn = t + timedelta(hours=cd)

                rec = {
                    "date": now_tw().strftime("%Y-%m-%d"),
                    "kill": t.strftime("%H:%M:%S"),
                    "respawn": respawn.isoformat(),
                    "note": note,
                    "user": user
                }

                if boss not in boss_db:
                    boss_db[boss] = []
                boss_db[boss].append(rec)
                save_db(db)

                # 美化登記成功訊息
                kill_time = rec['kill']
                resp_time = respawn.strftime('%H:%M:%S')
                msg_lines = [
                    f"🔥 已登記 {boss}",
                    f"🕒 死亡時間：{kill_time}",
                    f"✨ 重生時間：{resp_time}"
                ]
                if note.strip() != "":
                    msg_lines.append(f"📌 備註：{note}")

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("\n".join(msg_lines))
                )
                return

    return

@app.get("/")
def root():
    return {"status": "OK", "msg": "Boss helper running."}
