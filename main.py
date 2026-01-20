# 天堂M 吃王小幫手
from fastapi import FastAPI, Request, Header
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MemberJoinedEvent,
    MessageEvent,
    TextMessage,
    TextSendMessage,
    FlexSendMessage
)
from datetime import datetime, timedelta, timezone

import psycopg2
from urllib.parse import urlparse
import os
import json
from datetime import datetime, timedelta
import pytz
import asyncio
from threading import Lock

# 基本設定
db_lock = Lock()
app = FastAPI()
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
line_bot_api = LineBotApi(CHANNEL_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)
TZ = pytz.timezone("Asia/Taipei")
DB_FILE = "database.json"

# 工具函式
def is_peak_time():  #熱門時段定義
    h = now_tw().hour
    return 19 <= h <= 23
def safe_reply(event, text_msg, flex_msg=None):  #安全回覆LINE訊息 
    try:
        if is_peak_time() or flex_msg is None:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text_msg)
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                flex_msg
            )
    except Exception as e:
        print("Reply failed:", e)
def get_source_id(event):  #群組分離
    if event.source.type == "group":
        return event.source.group_id
    elif event.source.type == "room":
        return event.source.room_id
    else:
        return event.source.user_id
def now_tw():  #台灣時區
    return datetime.now(TZ)
def get_username(user_id):  #顯示未登記玩家
    try:
        profile = get_roster_profile(user_id)
        return profile["name"] if profile else "未登記玩家"
    except Exception:
        return "未知玩家"
def init_db():  #初始化JSON資料庫
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump({"boss": {}}, f, ensure_ascii=False, indent=2)
def load_db():  #讀取JSON資料庫
    with db_lock:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
def save_db(db):  #寫入JSON資料庫
    with db_lock:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
init_db()
#--------------flex卡片式輸出格式--------------
def build_register_boss_flex(boss, kill_time, respawn_time, registrar, note=None):  #登記成功卡片
    map_list = BOSS_MAP.get(boss, [])
    map_text = "、".join(map_list) if map_list else "未知"
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
            "text": f"🗺️ 地圖：{map_text}",
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
        },
        {
            "type": "text",
            "text": f"👤 登記者：{registrar}",
            "size": "sm",
            "color": "#555555",
            "wrap": True
        },
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
def build_register_boss_text(boss, kill_time, respawn_time, registrar, note):  #登記成功文字
    map_list = BOSS_MAP.get(boss, [])
    map_text = "、".join(map_list) if map_list else "未知"

    msg = (
        f"已登記 {boss}\n"
        f"地圖：{map_text}\n"
        f"死亡時間：{kill_time}\n"
    )
    if note:
        msg += f"\n備註：{note}"
    return msg
def build_help_flex():  #help卡片
    bubbles = []
    # 1️⃣ 登記王
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
    # 2️⃣ 查詢王
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
    # 3️⃣ 出王清單
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
    # 4️⃣ clear 說明
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
    # 5️⃣ 小技巧
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
    # 六 
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
        alt_text="使用說明",
        contents={
            "type": "carousel",
            "contents": bubbles
        }
    )
def build_join_roster_guide_flex():  #入群名冊卡片
    return FlexSendMessage(
        alt_text="歡迎加入群組，請加入名冊",
        contents={
            "type": "bubble",
            "size": "mega",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": "👋 歡迎加入群組",
                        "weight": "bold",
                        "size": "xl"
                    },
                    {
                        "type": "text",
                        "text": "📌 為了正確統計王表與 KPI\n請務必先加入名冊",
                        "wrap": True,
                        "size": "sm",
                        "color": "#555555"
                    },
                    {
                        "type": "separator",
                        "margin": "md"
                    },
                    {
                        "type": "text",
                        "text": "✍️ 加入名冊指令",
                        "weight": "bold"
                    },
                    {
                        "type": "text",
                        "text": "加入名冊 血盟名 遊戲角色名",
                        "wrap": True,
                        "size": "sm"
                    },
                    {
                        "type": "text",
                        "text": "📘 範例：\n加入名冊 酒窖 威士忌乄",
                        "wrap": True,
                        "size": "sm",
                        "color": "#666666"
                    }
                ]
            },
        }
    )
def build_query_record_bubble(boss, rec):  #歷史登記卡片
    respawn = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)
    registrar = get_username(rec.get("user"))
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
                },
                {
                    "type": "text",
                    "text": f"👤 登記者：{registrar}",
                    "size": "sm",
                    "color": "#555555",
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
def clear_confirm_flex():  #clear卡片
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
                        "text": "取消清除"
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
def build_boot_init_flex(base_time_str):  #開機登記卡片
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
def build_kpi_flex(title, period_text, ranking):  #KPI排名卡片
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
def build_roster_added_flex(clan, game_name):  #加入名冊卡片
    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "✅ 已加入名冊", "weight": "bold"},
                {"type": "text", "text": f"🎮 角色：{game_name}"},
                {"type": "text", "text": f"🏰 血盟：{clan}"}
            ]
        }
    }
def build_roster_confirm_update_flex(old_name, old_clan, new_name, new_clan):  #重複名冊卡片
    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "⚠️ 名冊已存在", "weight": "bold"},
                {"type": "text", "text": f"目前：{old_name} / {old_clan}"},
                {"type": "text", "text": f"修改為：{new_name} / {new_clan}"},
                {
                    "type": "button",
                    "action": {"type": "message", "label": "確認修改", "text": "確認修改"}
                },
                {
                    "type": "button",
                    "action": {"type": "message", "label": "取消", "text": "取消"}
                }
            ]
        }
    }
def build_roster_self_flex(game_name, clan):  #我的名冊卡片
    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "👤 我的名冊", "weight": "bold"},
                {"type": "text", "text": f"🎮 {game_name}"},
                {"type": "text", "text": f"🏰 {clan}"}
            ]
        }
    }
def build_roster_delete_confirm_flex(game_name):  #刪除名冊卡片
    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "⚠️ 確認刪除名冊", "weight": "bold"},
                {"type": "text", "text": f"角色：{game_name}"},
                {
                    "type": "button",
                    "action": {"type": "message", "label": "確認刪除", "text": "確認刪除"}
                },
                {
                    "type": "button",
                    "action": {"type": "message", "label": "取消", "text": "取消"}
                }
            ]
        }
    }
def build_roster_deleted_flex():  #成功刪除名冊卡片
    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "🗑 名冊已刪除", "weight": "bold"}
            ]
        }
    }
def build_roster_search_flex(keyword, rows):  #查名冊卡片
    """
    rows: [(game_name, clan_name, line_user_name)]
    """
    contents = []

    if not rows:
        contents.append({
            "type": "text",
            "text": "查無符合的名冊資料",
            "size": "sm",
            "color": "#888888"
        })
    else:
        for game_name, clan_name, line_name in rows:
            contents.append({
                "type": "box",
                "layout": "vertical",
                "spacing": "xs",
                "margin": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": f"🎮 角色：{game_name}",
                        "size": "sm",
                        "weight": "bold"
                    },
                    {
                        "type": "text",
                        "text": f"🏰 血盟：{clan_name}",
                        "size": "xs",
                        "color": "#666666"
                    },
                ]
            })
    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [{
                "type": "text",
                "text": f"🔍 名冊查詢：{keyword}",
                "weight": "bold",
                "size": "lg"
            }]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": contents
        }
    }
    return FlexSendMessage(
        alt_text=f"名冊查詢：{keyword}",
        contents=bubble
    )
def ensure_roster_table():  #外接資料庫連線
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS roster (
                id SERIAL PRIMARY KEY,
                line_user_id TEXT NOT NULL,
                game_name TEXT NOT NULL,
                clan_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
            """)
        conn.commit()
def query_roster(clan_name=None):  #查詢成員名冊
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            if clan_name:
                cur.execute("""
                    SELECT game_name, clan_name
                    FROM roster
                    WHERE clan_name = %s
                    ORDER BY created_at
                """, (clan_name,))
            else:
                cur.execute("""
                    SELECT game_name, clan_name
                    FROM roster
                    ORDER BY clan_name, created_at
                """)
            return cur.fetchall()
def search_roster(keyword):  #關鍵字名冊查詢
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT line_user_id, game_name, clan_name
                FROM roster
                WHERE game_name ILIKE %s
                OR clan_name ILIKE %s
                ORDER BY created_at
            """, (f"%{keyword}%", f"%{keyword}%"))
            return cur.fetchall()
def build_boss_list_text():  #王列表格式
    lines = ["📜【王列表（含所有簡稱）】", ""]
    for boss, aliases in alias_map.items():
        alias_text = "、".join(aliases)
        lines.append(f"🔹 {boss}")
        lines.append(f"   ➜ {alias_text}")
        lines.append("")
    return "\n".join(lines)
def build_boss_cd_list_text():  #王重生格式
    lines = ["⏳【王重生時間一覽】", ""]
    for boss, cd in sorted(cd_map.items(), key=lambda x: x[1]):
        # 小數轉成 小時 + 分鐘
        hours = int(cd)
        minutes = int((cd - hours) * 60)
        if minutes > 0:
            cd_text = f"{hours} 小時 {minutes} 分"
        else:
            cd_text = f"{hours} 小時"
        lines.append(f"🔹 {boss}：{cd_text}")
    return "\n".join(lines)
def safe_send(event, msg_obj):
    """
    安全發送 LINE 訊息
    msg_obj: TextSendMessage 或 FlexSendMessage
    """
    try:
        line_bot_api.reply_message(event.reply_token, msg_obj)
        print(f"[LINE] 成功發送訊息給 {get_source_id(event)}")
    except Exception as e:
        print(f"[LINE ERROR] 發送訊息失敗: {e}")
        # 顯示失敗的訊息內容（可選）
        if isinstance(msg_obj, TextSendMessage):
            print(f"[LINE ERROR] 內容: {msg_obj.text}")
        else:
            print(f"[LINE ERROR] FlexSendMessage 內容: {msg_obj}")
# 王資料
alias_map = {
    "四色": ["四色", "76", "4", "四", "4色","c","C"],
    "小紅": ["小紅", "55", "紅", "R", "r"],
    "小綠": ["小綠", "54", "綠", "G", "g"],
    "守護螞蟻": ["守護螞蟻", "螞蟻", "29", "ant", "a", "A"],
    "巨大蜈蚣": ["巨大蜈蚣", "蜈蚣", "海4", "海蟲", "6"],
    "86左飛龍": ["左飛龍", "861", "86左飛龍", "左", "86下"],
    "86右飛龍": ["右飛龍", "862", "86右飛龍", "右", "86上"],
    "伊弗利特": ["伊弗利特", "伊弗", "EF", "ef", "伊佛", "衣服"],
    "大腳瑪幽": ["大腳瑪幽", "大腳", "69"],
    "巨大飛龍": ["巨大飛龍", "巨飛", "GF", "82"],
    "83中飛龍": ["中飛龍", "中", "中央龍", "83", "83中飛龍"],
    "85東飛龍": ["東飛龍", "東", "85飛龍", "85","85東飛龍"],
    "大黑長者": ["大黑長者", "大黑", "黑", "863","b","B"],
    "力卡溫": ["力卡溫", "狼人", "狼王", "22", "狼"],
    "卡司特王": ["卡司特", "卡", "卡王", "25", "卡司特王"],
    "史前巨鱷": ["巨大鱷魚", "鱷魚", "51", "史前巨鱷"],
    "強盜頭目": ["強盜頭目", "強盜", "32"],
    "樹精": ["樹精", "樹", "24","t","T"],
    "蜘蛛": ["蜘蛛", "D", "喇牙", "39", "d"],
    "變形怪首領": ["變形怪首領", "變形怪", "變怪", "68", "變王"],
    "古代巨人": ["古代巨人", "古巨", "巨人", "78"],
    "不死鳥": ["不死鳥", "鳥", "452", "gg", "GG"],
    "死亡騎士": ["死亡騎士", "死騎", "05", "5"],
    "克特": ["克特", "12"],
    "賽尼斯的分身": ["賽尼斯的分身", "賽尼斯", "304"],
    "貝里斯": ["貝里斯", "大克特", "將軍", "81"],
    "烏勒庫斯": ["烏勒庫斯", "烏", "23"],
    "奈克偌斯": ["奈克偌斯", "奈", "57"],
}
cd_map = {
    "四色": 2, "小紅": 2, "小綠": 2, "守護螞蟻": 3.5, "巨大蜈蚣": 2,
    "86左飛龍": 2, "86右飛龍": 2, "伊弗利特": 2, "大腳瑪幽": 3,
    "巨大飛龍": 6, "83中飛龍": 3, "85東飛龍": 3, "大黑長者": 3,
    "力卡溫": 8, "卡司特王": 7.5, "史前巨鱷": 3, "強盜頭目": 3,
    "樹精": 3, "蜘蛛": 4, "變形怪首領": 3.5, "古代巨人": 8.5,
    "不死鳥": 8, "死亡騎士": 4, "克特": 10,
    "賽尼斯的分身": 3, "貝里斯": 6, "烏勒庫斯": 6,
    "奈克偌斯": 4,
}
BOSS_MAP = {
    "四色": ["76"],
    "小紅": ["55"],
    "小綠": ["54"],
    "守護螞蟻": ["29"],
    "巨大蜈蚣": ["06"],
    "86左飛龍": ["86"],
    "86右飛龍": ["86"],
    "伊弗利特": ["45"],
    "大腳瑪幽": ["69"],
    "巨大飛龍": ["82、86"],
    "83中飛龍": ["83"],
    "85東飛龍": ["85"],
    "大黑長者": ["86"],
    "力卡溫": ["22"],
    "卡司特王": ["25"],
    "史前巨鱷": ["51"],
    "強盜頭目": ["32"],
    "樹精": ["23、24、57"],
    "蜘蛛": ["39,65"],
    "變形怪首領": ["68"],
    "古代巨人": ["78"],
    "不死鳥": ["45"],
    "死亡騎士": ["05"],
    "克特": ["12"],
    "賽尼斯的分身": ["81"],
    "貝里斯": ["81"],
    "烏勒庫斯": ["23"],
    "奈克偌斯": ["57"],
}
# 邏輯函式
def get_roster_profile(user_id):
    row = roster_get_by_user(user_id)
    if not row:
        return None
    game_name, clan_name = row
    return {
        "name": game_name,
        "clan": clan_name
    }
def get_boss(name):
    for boss, aliases in alias_map.items():
        if name in aliases:
            return boss
    return None
def parse_time(token):
    now = now_tw()
    try:
        token = token.strip()

        # 現在時間
        if token in ("K", "k", "6666"):
            return now

        # HHMM
        if token.isdigit() and len(token) == 4:
            h = int(token[:2])
            m = int(token[2:])
            if not (0 <= h < 24 and 0 <= m < 60):
                return None
            t = now.replace(hour=h, minute=m, second=0)
            if t > now:
                t -= timedelta(days=1)
            return t

        # HHMMSS
        if token.isdigit() and len(token) == 6:
            h = int(token[:2])
            m = int(token[2:4])
            s = int(token[4:])
            if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
                return None
            t = now.replace(hour=h, minute=m, second=s)
            if t > now:
                t -= timedelta(days=1)
            return t

    except Exception:
        return None

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
def build_query_boss_flex(boss, records):
    if not records:
        return TextSendMessage("尚無紀錄")
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
    
def get_pg_conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")

    result = urlparse(url)
    return psycopg2.connect(
        host=result.hostname,
        port=result.port,
        user=result.username,
        password=result.password,
        dbname=result.path[1:],
        sslmode="require"
    )
def roster_get_by_user(user_id):
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT game_name, clan_name FROM roster WHERE line_user_id = %s",
                (user_id,)
            )
            return cur.fetchone()

def roster_insert(user_id, game_name, clan_name):
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO roster (line_user_id, game_name, clan_name)
                VALUES (%s, %s, %s)
                """,
                (user_id, game_name, clan_name)
            )
        conn.commit()

def roster_update(user_id, game_name, clan_name):
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE roster
                SET game_name = %s, clan_name = %s
                WHERE line_user_id = %s
                """,
                (game_name, clan_name, user_id)
            )
        conn.commit()

def roster_delete(user_id):
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM roster WHERE line_user_id = %s",
                (user_id,)
            )
        conn.commit()


# FastAPI Webhook

@app.on_event("startup")
async def startup():
    ensure_roster_table()
    # asyncio.create_task(boss_reminder_loop())

@app.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    body = await request.body()
    asyncio.create_task(process_line_event(body, x_line_signature))
    return "OK"

async def process_line_event(body: bytes, signature: str):
    try:
        handler.handle(body.decode("utf-8"), signature)
    except Exception as e:
        print("LINE 背景處理錯誤:", e)


@handler.add(MemberJoinedEvent)
def handle_member_joined(event):
    # 只處理群組 / room
    if event.source.type not in ["group", "room"]:
        return

    line_bot_api.reply_message(
        event.reply_token,
        build_join_roster_guide_flex()
    )

import re

def sanitize_register_line(line: str) -> str:
    """
    清理備份 / 多行貼上的單行內容
    回傳可解析的登記行，或空字串（代表跳過）
    """
    if not line:
        return ""

    line = line.strip()

    # 空行
    if not line:
        return ""

    # 備份標題
    if line.startswith("📦") or "王表備份" in line:
        return ""

    # 分隔線或裝飾
    if line.startswith("—"):
        return ""
    
    # 🔥 移除「#過N」或「#過 N」
    line = re.sub(r"\s*#\s*過\s*\d+", "", line)

    # 再清一次多餘空白
    line = re.sub(r"\s{2,}", " ", line).strip()

    return line.strip()



@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user = event.source.user_id
    text = event.message.text.strip()
    msg = text
    raw_text = event.message.text.strip()
    lines = raw_text.splitlines()
    success_count = 0
    failed_lines = []
    is_multi_register = len(lines) > 1


    db = load_db()

    group_id = get_source_id(event)
    db.setdefault("boss", {})
    db["boss"].setdefault(group_id, {})
    boss_db = db["boss"][group_id]
    

    if msg == "備份":
        if not boss_db:
            reply = "目前沒有任何王的死亡紀錄"
        else:
            now = now_tw()
            lines = ["📦【王表備份（請全部複製貼上）】", ""]

            for boss, records in boss_db.items():
                if not records:
                    continue

                last = records[-1]

                kill_time = last.get("kill")          # HH:MM:SS
                respawn_str = last.get("respawn")     # ISO
                note = last.get("note", "").strip()

                if not kill_time or not respawn_str:
                    continue

                # ===== 時間處理 =====
                parts = kill_time.split(":")
                if len(parts) not in (2, 3):
                    continue

                try:
                    h = int(parts[0])
                    m = int(parts[1])
                    s = int(parts[2]) if len(parts) == 3 else 0
                except ValueError:
                    continue

                if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
                    continue

                hhmmss = f"{h:02d}{m:02d}{s:02d}"

                base_respawn = datetime.fromisoformat(respawn_str).astimezone(TZ)
                cd = cd_map.get(boss)

                missed = 0

                if cd:
                    step = timedelta(hours=cd)

                    if now < base_respawn:
                        missed = 0
                    else:
                        diff = now - base_respawn
                        rounds_passed = int(diff.total_seconds() // step.total_seconds())

                        current_respawn = base_respawn + rounds_passed * step
                        passed_minutes = int((now - current_respawn).total_seconds() // 60)

                        if passed_minutes <= 30:
                            missed = rounds_passed
                        else:
                            missed = rounds_passed + 1


                # ===== 組輸出 =====
                line = f"{hhmmss} {boss}"

                if note:
                    line += f" {note}"

                if missed > 0:
                    line += f" #過{missed}"

                lines.append(line)

            reply = "\n".join(lines)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply)
        )
        return

    # 名冊功能
    db.setdefault("__ROSTER_WAIT__", {})
    # === 加入名冊 ===
    if msg.startswith("加入名冊"):
        parts = msg.split(" ", 2)
        if len(parts) < 3:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("❌ 用法：加入名冊 血盟名 遊戲名")
            )
            return

        _, clan, game_name = parts
        exists = roster_get_by_user(user)

        # === 已存在 → 詢問是否更新 ===
        if exists:
            old_game, old_clan = exists

            db["__ROSTER_WAIT__"][user] = {
                "action": "update",
                "clan": clan,
                "name": game_name
            }
            save_db(db)

            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(
                    alt_text="名冊已存在",
                    contents=build_roster_confirm_update_flex(
                        old_game, old_clan, game_name, clan
                    )
                )
            )
            return

        # === 不存在 → 新增 ===
        roster_insert(user, game_name, clan)

        line_bot_api.reply_message(
            event.reply_token,
            FlexSendMessage(
                alt_text="已加入名冊",
                contents=build_roster_added_flex(clan, game_name)
            )
        )
        return

    # === 確認修改名冊 ===
    if msg == "確認修改":
        wait = db.get("__ROSTER_WAIT__", {}).get(user)
        if not wait or wait["action"] != "update":
            return
    
        roster_update(user, wait["name"], wait["clan"])
    
        db["__ROSTER_WAIT__"].pop(user)
        save_db(db)
    
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("✅ 名冊已更新")
        )
        return

    # === 查自己 ===
    if msg == "查自己":
        profile = get_roster_profile(user)
        if not profile:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("❌ 尚未加入名冊")
            )
            return

        line_bot_api.reply_message(
            event.reply_token,
            FlexSendMessage(
                alt_text="我的名冊資料",
                contents=build_roster_self_flex(
                    profile["name"], profile["clan"]
                )
            )
        )
        return

        
    if msg == "刪除名冊":
        profile = get_roster_profile(user)
        if not profile:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("❌ 尚未加入名冊")
            )
            return

        line_bot_api.reply_message(
            event.reply_token,
            FlexSendMessage(
                alt_text="確認刪除名冊",
                contents=build_roster_delete_confirm_flex(profile["name"])
            )
        )
        return

        
    # === 刪除名冊 ===
    if msg == "確認刪除":
        roster_delete(user)

        line_bot_api.reply_message(
            event.reply_token,
            FlexSendMessage(
                alt_text="名冊已刪除",
                contents=build_roster_deleted_flex()
            )
        )
        return

    
    # === 取消（名冊）===
    if msg == "取消":
        if user in db.get("__ROSTER_WAIT__", {}):
            db["__ROSTER_WAIT__"].pop(user)
            save_db(db)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("❎ 已取消操作")
            )
            return

    # === 查名冊（模糊）===
    if text.startswith("查名冊"):
        parts = text.split(maxsplit=1)

        if len(parts) < 2:
            reply = TextSendMessage(text="用法：查名冊 關鍵字")
        else:
            keyword = parts[1]

            rows = search_roster(keyword)

            result = []
            for line_user_id, game_name, clan_name in rows:
                line_name = get_username(line_user_id)
                result.append((game_name, clan_name, line_name))

            reply = build_roster_search_flex(keyword, result)

        line_bot_api.reply_message(event.reply_token, reply)
        return

    if msg.lower() == "help":
        line_bot_api.reply_message(
            event.reply_token,
            build_help_flex()
        )
        return
    

    # 出
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
            step = timedelta(hours=cd)

            if now < base_respawn:
                # 尚未第一次重生
                display_time = base_respawn
                passed_minutes = None
                missed = 0
            else:
                diff = now - base_respawn
                rounds_passed = int(diff.total_seconds() // step.total_seconds())

                current_respawn = base_respawn + rounds_passed * step
                passed_minutes = int((now - current_respawn).total_seconds() // 60)

                if passed_minutes <= 30:
                    # 還在這一輪 30 分鐘內 → 未打
                    display_time = current_respawn
                    missed = rounds_passed          
                else:
                    # 已超過 30 分鐘 → 真的錯過一輪
                    display_time = current_respawn + step
                    missed = rounds_passed + 1
                    passed_minutes = None



            # ===== 組顯示字串 =====
            note = rec.get("note", "").strip()

            line = f"{display_time.strftime('%H:%M:%S')} {boss}"

            if note:
                line += f"（{note}）"

            if passed_minutes is not None and passed_minutes <= 30:
                line += f" <{passed_minutes}分未打>"

            if missed > 0:
                line += f" #過{missed}"


            # ❗ 關鍵：排序一定要用 display_time
            time_items.append((display_time, line))

        # ===== 排序（一定先完整排序）=====
        time_items.sort(key=lambda x: x[0])

        # ===== 根據時段決定顯示數 =====
        if is_peak_time():
            display_items = time_items[:10]
        else:
            display_items = time_items  # 非熱門 → 全部
        # ===== 輸出 =====
        output = ["📢【即將重生列表】", ""]

        for _, line in time_items:
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
    
    # 王列表
    
    if msg == "王列表":
        text = build_boss_list_text()

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text)
        )
        return

    
    # 王重生（CD 一覽）
    
    if msg == "王重生":
        text = build_boss_cd_list_text()

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text)
        )
        return

    
    # === 名冊（Flex）===
    if msg.startswith("名冊"):
        parts = msg.split(maxsplit=1)

        if len(parts) == 2:
            clan = parts[1]
            rows = query_roster(clan)
            keyword = clan
        else:
            rows = query_roster()
            keyword = "全部"

        result = []
        for game_name, clan_name in rows:
            result.append((game_name, clan_name, ""))

        reply = build_roster_search_flex(keyword, result)
        line_bot_api.reply_message(event.reply_token, reply)
        return
    # 開機 初始化 CD 王
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
    # clear
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

        # ===== ① 先送出 KPI =====
        now = now_tw()
        start, end = get_kpi_range(now)

        kpi_data = calculate_kpi(boss_db, start, end)

        if kpi_data:
            ranking = sorted(
                kpi_data.items(),
                key=lambda x: x[1],
                reverse=True
            )

            display = [(get_username(uid), count) for uid, count in ranking]

            kpi_bubble = build_kpi_flex(
                "📊 本週 KPI 排行榜（清除前）",
                f"{start.strftime('%m/%d %H:%M')} ～ {end.strftime('%m/%d %H:%M')}",
                display
            )

            line_bot_api.reply_message(
                event.reply_token,
                [
                    FlexSendMessage(
                        alt_text="本週 KPI 排行榜",
                        contents=kpi_bubble
                    ),
                    TextSendMessage("🗑 接下來將清除本群組所有王紀錄")
                ]
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("📊 本週尚無 KPI 紀錄，將直接清除資料")
            )

        # ===== ② 再清除資料 =====
        db["boss"].pop(group_id, None)
        db["__WAIT__"].pop(group_id, None)
        save_db(db)

        return


    if msg == "取消清除":
        db.get("__WAIT__", {}).pop(group_id, None)
        save_db(db)
    
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("❎ 已取消清除")
        )
        return
    
    # 查 王名
    
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
    # KPI
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
    
@app.get("/")
def root():
    return {"status": "OK"}
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000))
    )
