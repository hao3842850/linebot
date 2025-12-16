# ============================================================
# 天堂M 吃王小幫手
# ============================================================
from fastapi import FastAPI, Request, Header
from linebot import LineBotApi, WebhookHandler
from linebot.models import Mention, Mentionee
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
from linebot.models import FlexSendMessage


import os
import json
from datetime import datetime, timedelta
import pytz

# =========================
# 基本設定
# =========================
ROSTER_FILE = "roster.json"

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
def init_roster():
    if not os.path.exists(ROSTER_FILE):
        with open(ROSTER_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)

def load_roster():
    with open(ROSTER_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_roster(roster):
    with open(ROSTER_FILE, "w", encoding="utf-8") as f:
        json.dump(roster, f, ensure_ascii=False, indent=2)

init_roster()

def get_source_id(event):
    if event.source.type == "group":
        return event.source.group_id
    elif event.source.type == "room":
        return event.source.room_id
    else:
        return event.source.user_id

def now_tw():
    return datetime.now(TZ)

def get_username(user_id):
    roster = load_roster()
    return roster.get(user_id, {}).get("name", "未登記玩家")

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

def build_register_boss_flex(boss, kill_time, respawn_time, note=None):
    contents = [
        {
            "type": "text",
            "text": f"🔥 已登記 {boss}",
            "weight": "bold",
            "size": "lg",
            "wrap": True
        },
        {
            "type": "text",
            "text": f"🕒 死亡時間：{kill_time}",
            "wrap": True
        },
        {
            "type": "text",
            "text": f"✨ 重生時間：{respawn_time}",
            "wrap": True
        }
    ]

    if note:
        contents.append({
            "type": "text",
            "text": f"📌 備註：{note}",
            "wrap": True
        })

    return FlexSendMessage(
        alt_text=f"已登記 {boss}",
        contents={
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": contents
            }
        }
    )

def build_help_flex():
    bubbles = []

    # =====================
    # 1️⃣ 登記王
    # =====================
    bubbles.append({
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "📌 登記BOSS",
                    "weight": "bold",
                    "size": "lg"
                },
                {
                    "type": "text",
                    "text": "指令格式：",
                    "weight": "bold"
                },
                {
                    "type": "text",
                    "text": "6666 四色\nK 四色\n0930 四色\n093045 四色 備註",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": "※ 6666 = 現在時間 and K = 現在時間",
                    "size": "sm",
                    "color": "#888888"
                }
            ]
        }
    })

    # =====================
    # 2️⃣ 查詢王
    # =====================
    bubbles.append({
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "🔍 查詢歷史登記",
                    "weight": "bold",
                    "size": "lg"
                },
                {
                    "type": "text",
                    "text": "查 王名",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": "範例：\n查 四色",
                    "wrap": True
                }
            ]
        }
    })

    # =====================
    # 3️⃣ 出王清單
    # =====================
    bubbles.append({
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "⏰ 出王清單",
                    "weight": "bold",
                    "size": "lg"
                },
                {
                    "type": "text",
                    "text": "出",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": "顯示即將重生的BOSS",
                    "size": "sm",
                    "color": "#888888"
                }
            ]
        }
    })

    # =====================
    # 4️⃣ clear 說明
    # =====================
    bubbles.append({
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "⚠️ 清除紀錄",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#D32F2F"
                },
                {
                    "type": "text",
                    "text": "clear",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": "※ 確定清除所有時間\n需按下『確定清除』",
                    "size": "sm",
                    "color": "#888888",
                    "wrap": True
                }
            ]
        }
    })

    # =====================
    # 5️⃣ 小技巧
    # =====================
    bubbles.append({
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "📃 BOSS資料",
                    "weight": "bold",
                    "size": "lg"
                },
                {
                    "type": "text",
                    "text": "王列表➡️所有王的簡稱\n王重生➡️所有王的CD時間",
                    "wrap": True
                }
            ]
        }
    })
    # =====================
    # 六 
    # =====================
    bubbles.append({
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "🔌開機時間",
                    "weight": "bold",
                    "size": "lg"
                },
                {
                    "type": "text",
                    "text": "開機 時間",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": "範例：\n開機 2100",
                    "wrap": True
                }
            ]
        }
    })

    return FlexSendMessage(
        alt_text="伊娃小幫手 使用說明",
        contents={
            "type": "carousel",
            "contents": bubbles
        }
    )

def build_query_record_bubble(boss, rec):
    respawn = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)

    contents = [
        {
            "type": "text",
            "text": f"🔥歷史登記 {boss}",
            "weight": "bold",
            "size": "lg",
            "wrap": True
        },
        {
            "type": "separator",
            "margin": "md"
        },
        {
            "type": "box",
            "layout": "vertical",
            "margin": "md",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": f"📅 登記日期：{rec['date']}",
                    "size": "sm",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": f"🕒 死亡時間：{rec['kill']}",
                    "size": "sm",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": f"✨ 重生時間：{respawn.strftime('%H:%M:%S')}",
                    "size": "sm",
                    "wrap": True
                }
            ]
        }
    ]

    if rec.get("note"):
        contents.append({
            "type": "text",
            "text": f"📌 備註：{rec['note']}",
            "size": "sm",
            "margin": "md",
            "wrap": True
        })

    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": contents
        }
    }

def clear_confirm_flex():
    return {
        "type": "bubble",
        "hero": {
            "type": "image",
            "url": "https://i.imgur.com/9M0ZK4N.png",  # 可換
            "size": "full",
            "aspectRatio": "20:13",
            "aspectMode": "cover"
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "⚠️ 危險操作",
                    "weight": "bold",
                    "size": "xl",
                    "color": "#D32F2F"
                },
                {
                    "type": "text",
                    "text": "此操作將清除所有王的紀錄\n此動作無法復原",
                    "wrap": True,
                    "margin": "md"
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "horizontal",
            "spacing": "md",
            "contents": [
                {
                    "type": "button",
                    "style": "secondary",
                    "action": {
                        "type": "message",
                        "label": "取消",
                        "text": "取消"
                    }
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#D32F2F",
                    "action": {
                        "type": "message",
                        "label": "確認清除",
                        "text": "確定清除"
                    }
                }
            ]
        }
    }

def build_boot_init_flex(base_time_str):
    return FlexSendMessage(
        alt_text="已紀錄開機時間",
        contents={
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": "🔌 開機時間已紀錄",
                        "weight": "bold",
                        "size": "lg"
                    },
                    {
                        "type": "separator",
                        "margin": "md"
                    },
                    {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "sm",
                        "margin": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": f"🕒 開機時間：{base_time_str}",
                                "wrap": True
                            },
                            {
                                "type": "text",
                                "text": "📌 僅補齊尚未登記的 CD 王",
                                "size": "sm",
                                "color": "#666666",
                                "wrap": True
                            }
                        ]
                    }
                ]
            }
        }
    )

def build_kpi_flex(title, period_text, ranking):
    rows = []

    medals = ["🥇", "🥈", "🥉"]

    for idx, (name, count) in enumerate(ranking):
        icon = medals[idx] if idx < 3 else f"{idx+1}"

        rows.append({
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {
                    "type": "text",
                    "text": icon,
                    "size": "sm",
                    "flex": 1
                },
                {
                    "type": "text",
                    "text": name,
                    "size": "sm",
                    "flex": 4
                },
                {
                    "type": "text",
                    "text": f"{count} 次",
                    "size": "sm",
                    "align": "end",
                    "flex": 2
                }
            ]
        })

    return {
        "type": "bubble",
        "size": "kilo",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "text",
                    "text": title,
                    "weight": "bold",
                    "size": "lg"
                },
                {
                    "type": "text",
                    "text": period_text,
                    "size": "xs",
                    "color": "#888888"
                },
                {
                    "type": "separator"
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": rows
                }
            ]
        }
    }


# =========================
# 王資料
# =========================
alias_map = {
    "四色": ["四色", "76", "4", "四", "4色","c","C"],
    "小紅": ["小紅", "55", "紅", "R", "r"],
    "小綠": ["小綠", "54", "綠", "G", "g"],
    "守護螞蟻": ["守護螞蟻", "螞蟻", "29"],
    "巨大蜈蚣": ["巨大蜈蚣", "蜈蚣", "海4", "海蟲", "6"],
    "86左飛龍": ["左飛龍", "861", "86左飛龍", "左", "86下"],
    "86右飛龍": ["右飛龍", "862", "86右飛龍", "右", "86上"],
    "伊弗利特": ["伊弗利特", "伊弗", "EF", "ef", "伊佛", "衣服"],
    "大腳瑪幽": ["大腳瑪幽", "大腳", "69"],
    "巨大飛龍": ["巨大飛龍", "巨飛", "GF", "82"],
    "83中飛龍": ["中飛龍", "中", "中央龍", "83"],
    "85東飛龍": ["東飛龍", "東", "85飛龍", "85"],
    "大黑長者": ["大黑長者", "大黑", "黑", "863","b","B"],
    "力卡溫": ["力卡溫", "狼人", "狼王", "22"],
    "卡司特王": ["卡司特", "卡", "卡王", "25"],
    "史前巨鱷": ["巨大鱷魚", "鱷魚", "51"],
    "強盜頭目": ["強盜頭目", "強盜", "32"],
    "樹精": ["樹精", "樹", "23", "24", "57","t","T"],
    "蜘蛛": ["蜘蛛", "D", "喇牙", "39"],
    "變形怪首領": ["變形怪首領", "變形怪", "變怪", "68"],
    "古代巨人": ["古代巨人", "古巨", "巨人", "78"],
    "不死鳥": ["不死鳥", "鳥", "452","g","gg","G","GG"],
    "死亡騎士": ["死亡騎士", "死騎", "05"],
    "克特": ["克特", "12"],
    "賽尼斯的分身": ["賽尼斯的分身", "賽尼斯", "304"],
    "貝里斯": ["貝里斯", "大克特", "將軍", "821"],
    "烏勒庫斯": ["烏勒庫斯", "烏", "231"],
    "奈克偌斯": ["奈克偌斯", "奈", "571"],
}

cd_map = {
    "四色": 2, "小紅": 2, "小綠": 2, "守護螞蟻": 3.5, "巨大蜈蚣": 2,
    "86左飛龍": 2, "86右飛龍": 2, "伊弗利特": 2, "大腳瑪幽": 3,
    "巨大飛龍": 6, "83中飛龍": 3, "85東飛龍": 3, "大黑長者": 3,
    "力卡溫": 8, "卡司特王": 7.5, "史前巨鱷": 3, "強盜頭目": 3,
    "樹精": 6, "蜘蛛": 4, "變形怪首領": 3.5, "古代巨人": 8.5,
    "不死鳥": 8, "死亡騎士": 4, "克特": 10,
    "賽尼斯的分身": 3, "貝里斯": 6, "烏勒庫斯": 6,
    "奈克偌斯": 4,
}

fixed_bosses = {
     "奇岩一樓王": {
        "times": ["00:00", "06:00", "12:00", "18:00"],
        "weekdays": [0, 1, 2, 3, 4]  # 週一～週五
    },
    "奇岩二樓王": {
        "times": ["07:00", "14:00", "21:00"],
        "weekdays": [0, 1, 2, 3, 4]
    },
    "奇岩三樓王": {
        "times": ["20:15"],
        "weekdays": [0, 1, 2, 3, 4]
    },
    "奇岩四樓王": {
        "times": ["21:15"],
        "weekdays": [0, 1, 2, 3, 4]
    },

    "黑暗四樓王": {
        "times": ["00:00", "18:00"]
    },
    "三王": {
        "times": ["19:15"]
    },
    "惡魔": {
        "times": ["22:00"]
    },
    "巴風特": {
        "times": ["14:00", "20:00"]
    },
    "異界炎魔": {
        "times": ["23:00"]
    },
    "烈焰大死騎": {
        "times": ["23:30"]
    },
    "涅默西斯高輪": {
        "times": ["22:30"]
    },
    "魔法師": {
        "times": ["01:00","03:00","05:00","07:00","09:00","11:00",
                  "13:00","15:00","17:00","19:00","21:00","23:00"]
    }
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

def get_next_fixed_time_fixed(boss_conf):
    now = now_tw()
    today = now.date()

    for day_offset in range(0, 8):  # 最多找一週
        current_date = today + timedelta(days=day_offset)
        weekday = current_date.weekday()

        # 有設定 weekdays，但今天不在 → 跳過
        if "weekdays" in boss_conf and weekday not in boss_conf["weekdays"]:
            continue

        for t in boss_conf["times"]:
            dt = TZ.localize(
                datetime.strptime(
                    f"{current_date} {t}",
                    "%Y-%m-%d %H:%M"
                )
            )

            if dt >= now:
                return dt

    return None

def init_cd_boss_with_given_time(db, group_id, base_time):
    db.setdefault("boss", {})
    db["boss"].setdefault(group_id, {})
    boss_db = db["boss"][group_id]

    for boss, cd in cd_map.items():
        # 已有紀錄就跳過
        if boss in boss_db and boss_db[boss]:
            continue

        respawn = base_time + timedelta(hours=cd)

        boss_db.setdefault(boss, []).append({
            "date": base_time.strftime("%Y-%m-%d"),
            "kill": base_time.strftime("%H:%M:%S"),
            "respawn": respawn.isoformat(),
            "note": "開機",
            "user": "__SYSTEM__"
        })


def get_kpi_range(now):
    """
    KPI 統計區間：
    星期三 05:00 ～ 下星期三 05:00
    """
    # weekday(): Monday=0 ... Sunday=6
    # Wednesday = 2
    days_since_wed = (now.weekday() - 2) % 7
    start = now - timedelta(days=days_since_wed)
    start = start.replace(hour=5, minute=0, second=0, microsecond=0)

    # 如果現在還沒到本週三 05:00，往前推一週
    if now < start:
        start -= timedelta(days=7)

    end = start + timedelta(days=7)
    return start, end
def calculate_kpi(boss_db, start, end):
    """
    boss_db = db["boss"][group_id]
    回傳 dict: {user_id: count}
    """
    result = {}

    for boss, records in boss_db.items():
        for rec in records:
            # 排除開機補登記
            if rec.get("user") == "__SYSTEM__":
                continue

            kill_dt = TZ.localize(
                datetime.strptime(
                    f"{rec['date']} {rec['kill']}",
                    "%Y-%m-%d %H:%M:%S"
                )
            )

            if start <= kill_dt < end:
                uid = rec["user"]
                result[uid] = result.get(uid, 0) + 1

    return result



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
    
    if msg.startswith("加入名冊"):
        parts = msg.split(" ", 2)
        if len(parts) < 3:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("❌ 用法：加入名冊 血盟名 遊戲名")
            )
            return

        _, clan, game_name = parts
        roster = load_roster()
        roster[user] = {
            "name": game_name,
            "clan": clan
        }
        save_roster(roster)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                f"✅ 已加入名冊\n玩家：{game_name}\n血盟：{clan}"
            )
        )
        return
    
    # ===== 查名冊 @某人 =====
    if msg.startswith("查名冊") and event.message.mention:
        mentions = event.message.mention.mentionees

        if not mentions:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("❌ 請使用：查名冊 @某人")
            )
            return

        target_user_id = mentions[0]["userId"]

        roster = load_roster()
        player = roster.get(target_user_id)

        if not player:
            reply = "❌ 此玩家尚未加入名冊"
        else:
            reply = (
                "👤 玩家名冊資料\n"
                f"遊戲名：{player.get('name')}\n"
                f"血盟：{player.get('clan')}"
            )

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(reply)
        )
        return
    
    # ===== 查名冊 玩家名字（模糊查詢）=====
    if msg.startswith("查名冊 ") and not event.message.mention:
        keyword = msg.replace("查名冊", "").strip()

        if not keyword:
            return

        roster = load_roster()
        results = []

        # 🔍 模糊搜尋
        for uid, info in roster.items():
            name = info.get("name", "")
            if keyword in name:
                results.append((uid, info))

        # ❌ 找不到
        if not results:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("❌ 名冊中找不到符合的玩家")
            )
            return

        messages = []

    # ⚠️ LINE 一次最多回 5 則（保險）
        for i, (uid, info) in enumerate(results[:5], start=1):
            text = (
                f"{i}️⃣ @玩家 是\n"
                f"血盟：{info['clan']}\n"
                f"遊戲名：{info['name']}"
            )

            messages.append(
                TextSendMessage(
                    text=text,
                    mention=Mention(
                        mentionees=[
                            Mentionee(
                                user_id=uid,
                                index=text.find("@玩家"),
                                length=3
                            )
                        ]
                    )
                )
            )

        line_bot_api.reply_message(event.reply_token, messages)
        return

    if msg.lower() == "help":
        line_bot_api.reply_message(
            event.reply_token,
            build_help_flex()
        )
        return

    def build_query_boss_flex(boss, records):
        bubbles = []
    
        # ⭐ 新 → 舊（保險再 reversed 一次）
        for rec in reversed(records):
            bubbles.append(build_query_record_bubble(boss, rec))
    
        return FlexSendMessage(
            alt_text=f"{boss} 最近紀錄",
            contents={
                "type": "carousel",
                "contents": bubbles
            }
        )

    
    group_id = get_source_id(event)
    db.setdefault("boss", {})
    db["boss"].setdefault(group_id, {})
    boss_db = db["boss"][group_id]
   
    # =========================
    # 開機 初始化 CD 王
    # =========================
    if msg.startswith("開機 "):
        parts = msg.split(" ", 1)
        time_token = parts[1].strip()
    
        base_time = parse_time(time_token)
        if not base_time:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("❌ 時間格式錯誤，請使用 HHMM 或 HHMMSS")
            )
            return
    
        init_cd_boss_with_given_time(db, group_id, base_time)
        save_db(db)
    
        flex_msg = build_boot_init_flex(
            base_time.strftime('%H:%M')
        )
        
        line_bot_api.reply_message(
            event.reply_token,
            flex_msg
        )
        return
    
    
    # =========================
    # clear
    # =========================
    if msg == "clear":
        db.setdefault("__WAIT__", {})
        db["__WAIT__"][group_id] = {
            "user": user
        }
        save_db(db)

    
        flex = FlexSendMessage(
            alt_text="清除確認",
            contents=clear_confirm_flex()
        )
        line_bot_api.reply_message(event.reply_token, flex)
        return



    if msg == "確定清除":
        wait = db.get("__WAIT__", {}).get(group_id)
        
        if not wait or wait["user"] != user:
            return

    
        db["boss"].pop(group_id, None)
        db["__WAIT__"].pop(group_id, None)

        save_db(db)
    
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("✅ 已清除本群組所有王紀錄")
        )
        return

    if msg == "取消":
        db.get("__WAIT__", {}).pop(group_id, None)
        save_db(db)
    
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("❎ 已取消清除")
        )
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
    
        records = boss_db[boss][-5:]  # 最近 5 筆（舊 → 新）
    
        flex_msg = build_query_boss_flex(boss, records)
    
        line_bot_api.reply_message(
            event.reply_token,
            flex_msg
        )
        return

    # =========================
    # KPI
    # =========================
    if msg.upper() == "KPI":
        now = now_tw()
        start, end = get_kpi_range(now)
    
        # ⭐ 一定要有這行
        kpi_data = calculate_kpi(boss_db, start, end)
    
        if not kpi_data:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("📊 本週尚無 KPI 紀錄")
            )
            return
    
        ranking = sorted(
            kpi_data.items(),
            key=lambda x: x[1],
            reverse=True
        )
    
        display = [(get_username(uid), count) for uid, count in ranking]
    
        bubble = build_kpi_flex(
            "📊 本週 KPI 排行榜",
            f"{start.strftime('%m/%d %H:%M')} ～ {end.strftime('%m/%d %H:%M')}",
            display
        )
    
        line_bot_api.reply_message(
            event.reply_token,
            FlexSendMessage(
                alt_text="本週 KPI 排行榜",
                contents=bubble
            )
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
            passed_minutes = int((now - base_respawn).total_seconds() // 60)

            step = timedelta(hours=cd)

            # ===== 是否允許跳下一場 =====
            allow_jump = False

            # 有新登記（代表這筆就是最新場）
            # → 這裡不用特別判斷，因為 rec 就是最後一筆

            # 超過 30 分鐘 → 視為放生
            if passed_minutes >= 30:
                allow_jump = True

            if allow_jump:
                missed = 0
                t = base_respawn
                while t < now:
                    t += step
                    missed += 1
            else:
                # ❗ 未滿 30 分鐘 → 卡在這一場
                t = base_respawn
                missed = 0

            # ===== 組輸出 =====
            line = f"{t.strftime('%H:%M:%S')} {boss}"

            # 備註（包含 開機）
            if rec.get("note"):
                line += f" ({rec['note']})"

            # 未打顯示（只在未跳場時）
            if not allow_jump and passed_minutes > 0:
                line += f" <{passed_minutes}分未打>"
                priority = 0
            else:
                priority = 1

            if missed > 0:
                line += f"#過{missed}"

            time_items.append((priority, t, line))

    # ===== 固定王 =====
        for boss, conf in fixed_bosses.items():
            t = get_next_fixed_time_fixed(conf)
            if not t:
                continue
        
            time_items.append(
                (2, t, f"{t.strftime('%H:%M:%S')} {boss}")
            )

    # 排序
        time_items.sort(key=lambda x: (x[0], x[1]))

    # ===== 組輸出 =====
        output = ["📢【即將重生列表】", ""]
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
        flex_msg = build_register_boss_flex(
            boss=boss,
            kill_time=rec['kill'],
            respawn_time=respawn.strftime('%H:%M:%S'),
            note=note
        )
        
        line_bot_api.reply_message(
            event.reply_token,
            flex_msg
        )
        return




@app.get("/")
def root():
    return {"status": "OK"}
