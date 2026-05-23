# 天堂M 吃王小幫手

# === 1. 標準庫模組 (Standard Libraries) ===
import asyncio
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib.parse import urlparse

# === 2. 第三方套件 (Third-party Libraries) ===
import psycopg2
import pytz
import requests
from fastapi import FastAPI, Header, Request

# === 3. LINE Bot SDK 相關導入 ===
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    # 事件類
    JoinEvent,
    MemberJoinedEvent,
    MessageEvent,
    # 訊息類
    TextMessage,
    TextSendMessage,
    # Flex Message 核心與容器
    FlexSendMessage,
    FlexContainer,
    BubbleContainer
)

# 基本設定
db_lock = Lock()
app = FastAPI()
active_auctions = {}
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
line_bot_api = LineBotApi(CHANNEL_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)
TZ = pytz.timezone("Asia/Taipei")
DB_FILE = "database.json"
DATABASE_URL = os.getenv("DATABASE_URL")
@app.on_event("startup")
def startup_event():
    print("🚀 系統啟動，準備初始化資料庫...")
    init_db()
    init_fixed_boss_db()
# 工具函式
def is_peak_time():
    return False # 暫時關閉，永遠允許 Flex 訊息

    #h = now_tw().hour
    #return 19 <= h <= 23
def safe_init_dict(parent_dict, key):
    """
    安全初始化字典：防止 'str' object has no attribute 'setdefault' 錯誤。
    如果發現資料壞掉（變成字串），會自動把它修復成空字典。
    """
    # 如果 key 不存在，或者它裡面的值不是字典 (dict) 型態
    if key not in parent_dict or not isinstance(parent_dict[key], dict):
        # 強制幫它建立或覆寫成一個新的空字典
        parent_dict[key] = {}
        
    return parent_dict[key]
def init_fixed_boss_db():
    """自動建立固定王專用的資料表，並確保欄位長度足夠"""
    print("🔧 系統啟動：檢查並建立 fixed_boss_records 資料表...")
    conn = get_pg_conn()
    if not conn:
        print("⚠️ 無法連線至資料庫，請檢查 DATABASE_URL 設定。")
        return

    try:
        with conn.cursor() as cur:
            # 建立具備正確 VARCHAR 長度的資料表 (如果不存在的話)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fixed_boss_records2 (
                    id SERIAL PRIMARY KEY,
                    group_id VARCHAR(255) NOT NULL,
                    boss_name VARCHAR(255) NOT NULL,
                    status VARCHAR(50) NOT NULL,
                    record_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
        conn.commit()
        print("✅ fixed_boss_records 資料表已確認存在 / 建立完成！")
    except Exception as e:
        print(f"❌ 自動建立資料表失敗: {e}")
        conn.rollback()
    finally:
        conn.close()
def check_subscription(group_id):
    """檢查訂閱：回傳 (是否允許, 到期時間, 狀態文字)"""
    conn = get_pg_conn()
    if not conn: return True, None, "資料庫連線異常"
    try:
        cur = conn.cursor()
        cur.execute("SELECT status, expiry_date FROM subscriptions WHERE group_id = %s", (group_id,))
        row = cur.fetchone()
        now = now_tw()
        
        # 1. 如果是新群組，自動給 7 天試用
        if not row:
            expiry = now + timedelta(days=7)
            cur.execute(
                "INSERT INTO subscriptions (group_id, status, expiry_date) VALUES (%s, %s, %s)",
                (group_id, 'trial', expiry)
            )
            conn.commit()
            return True, expiry, "試用中"

        status, expiry_date = row
        
        # 2. 【核心修正】處理字串轉時間問題
        if isinstance(expiry_date, str):
            # 處理 PostgreSQL 格式字串: 2026-02-16 02:52:00...
            try:
                clean_date = expiry_date.split('.')[0].split('+')[0]
                expiry_date = datetime.strptime(clean_date, '%Y-%m-%d %H:%M:%S')
            except:
                return True, None, "時間格式解析失敗"

        # 3. 補上時區資訊
        if expiry_date.tzinfo is None:
            expiry_date = TZ.localize(expiry_date)

        # 4. 判斷是否到期
        if now > expiry_date:
            return False, expiry_date, "已到期"
            
        return True, expiry_date, "授權有效"
    except Exception as e:
        print(f"訂閱檢查出錯: {e}")
        return True, None, "系統略過檢查"
    finally:
        cur.close()
        conn.close()

def build_subscription_flex(status, expiry_date):
    """建立訂閱到期的卡片回覆"""
    expiry_str = expiry_date.strftime('%Y-%m-%d %H:%M')
    bubble = {
      "type": "bubble",
      "header": {
        "type": "box", "layout": "vertical", "backgroundColor": "#222222",
        "contents": [{"type": "text", "text": "🔔 系統權限通知", "color": "#FFD700", "weight": "bold", "size": "lg"}]
      },
      "body": {
        "type": "box", "layout": "vertical", "spacing": "md",
        "contents": [
          {"type": "text", "text": f"目前狀態：{status}", "weight": "bold", "size": "md"},
          {"type": "text", "text": f"有效期限至：\n{expiry_str}", "size": "sm", "color": "#aaaaaa", "wrap": True},
          {"type": "separator", "margin": "lg"},
          {"type": "text", "text": "⚠️ 試用期已結束，功能已暫時鎖定。請聯絡管理員開通正式版以繼續使用。", "wrap": True, "size": "xs", "color": "#ff4444"}
        ]
      },
      "footer": {
        "type": "box", "layout": "vertical",
        "contents": [
          {"type": "button", "style": "primary", "color": "#FFD700", 
           "action": {"type": "uri", "label": "聯絡開發者", "uri": "https://line.me/ti/p/wenhao0222"}}
        ]
      }
    }
    return FlexSendMessage(alt_text="訂閱到期通知", contents=bubble)


def safe_reply(event, text_msg, flex_msg=None):
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
def get_source_id(event):
    # 1. 先取得原始的來源 ID
    if event.source.type == "group":
        source_id = event.source.group_id
    elif event.source.type == "room":
        source_id = event.source.room_id
    else:
        source_id = event.source.user_id
        
    # 2. 【核心修改】在這裡寫死：如果來源是 B 群，強制變成 A 群
    GROUP_B_ID = "Cfea8c07f23c410a1e328871f8573f5e5"
    GROUP_A_ID = "C5b59f9fe8a7c3b709742b8f765d8f95e"
    
    if source_id == GROUP_B_ID:
        return GROUP_A_ID  # 狸貓換太子，把 B 騙成 A

    # 其他情況正常回傳
    return source_id
def now_tw():
    return datetime.now(TZ)
def get_username(user_id):
    try:
        profile = get_roster_profile(user_id)
        return profile["name"] if profile else "未登記玩家"
    except Exception:
        return "未知玩家"

    
def get_pg_conn():
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        # 開啟自動提交，確保每一筆寫入都會立刻進入資料庫，讓另一台機器人讀到
        conn.autocommit = True 
        return conn
    except Exception as e:
        print(f"❌ 資料庫連線失敗: {e}")
        return None
    
def save_boss_to_pg(group_id, boss_name, kill_time, respawn_time, user_id, note, source="manual"):
    """將單筆登記紀錄寫入資料庫"""
    conn = get_pg_conn()
    if not conn: return
    try:
        cur = conn.cursor()
        query = """
            INSERT INTO boss_time (group_id, boss_name, kill_time, respawn_time, user_id, note, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        cur.execute(query, (group_id, boss_name, kill_time, respawn_time, user_id, note, source))
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"Error saving boss record: {e}")
    finally:
        conn.close()

def get_latest_boss_records(group_id):
    """修正版：改用 id 排序，確保最後一次登記的指令永遠優先 (解決無法覆蓋問題)"""
    conn = get_pg_conn()
    if not conn: return {}
    try:
        cur = conn.cursor()
        # 關鍵：ORDER BY boss_name, id DESC 
        # 這樣最後寫入的那筆資料(id最大)會被當作該王的目前狀態
        query = """
            SELECT DISTINCT ON (boss_name) 
                   boss_name, kill_time, respawn_time, note, user_id, source
            FROM boss_time
            WHERE group_id = %s
            ORDER BY boss_name, id DESC
        """
        cur.execute(query, (group_id,))
        rows = cur.fetchall()
        cur.close()
        
        result = {}
        for row in rows:
            boss_name = row[0]
            kt_raw = row[1]
            kt_tw = kt_raw.astimezone(TZ) if kt_raw.tzinfo else pytz.utc.localize(kt_raw).astimezone(TZ)
            rt_raw = row[2]
            rt_tw = rt_raw.astimezone(TZ) if rt_raw.tzinfo else pytz.utc.localize(rt_raw).astimezone(TZ)

            result[boss_name] = [{
                "date": kt_tw.strftime("%Y-%m-%d"),
                "kill": kt_tw.strftime("%H:%M:%S"),
                "respawn": rt_tw.isoformat(), 
                "note": row[3] if row[3] else "",
                "user": row[4],
                "source": row[5]
            }]
        return result
    except Exception as e:
        print(f"Error fetching boss records: {e}")
        return {}
    finally:
        conn.close()

from datetime import timedelta

from datetime import timedelta  # 確保有匯入 timedelta


def delete_boss_records_by_alias(group_id, input_text):
    """
    根據新的 alias_map 結構：{"全名": ["簡稱1", "簡稱2"]}
    尋找對應的全名並徹底清除紀錄。
    """
    target_boss = None
    
    # 遍歷 alias_map 進行匹配
    for full_name, aliases in alias_map.items():
        # 如果輸入的字在簡稱清單中，或者剛好就是全名
        if input_text in aliases or input_text == full_name:
            target_boss = full_name
            break
            
    if not target_boss:
        return False, None

    conn = get_pg_conn()
    if not conn: return False, target_boss
    try:
        cur = conn.cursor()
        # 執行 DELETE 徹底清除該群組中該王的所有紀錄
        query = "DELETE FROM boss_time WHERE group_id = %s AND boss_name = %s"
        cur.execute(query, (group_id, target_boss))
        conn.commit()
        count = cur.rowcount
        cur.close()
        return count > 0, target_boss
    except Exception as e:
        print(f"SQL 刪除出錯: {e}")
        return False, target_boss
    finally:
        conn.close()

def get_kpi_ranking(group_id):
    conn = get_pg_conn()
    if not conn: return "資料庫連線失敗", []
    
    try:
        cur = conn.cursor()
        now = now_tw()
        start_time, end_time = get_kpi_range(now)
        
        # 格式化顯示用的日期字串 (例如 02/04 ~ 02/11)
        period_text = f"{start_time.strftime('%m/%d')} ~ {end_time.strftime('%m/%d')}"
        
        query = """
            SELECT user_id, COUNT(*) as count
            FROM boss_time
            WHERE group_id = %s 
              AND kill_time >= %s 
              AND kill_time < %s
              AND source != 'boot'  -- 排除開機自動補推的紀錄
            GROUP BY user_id
            ORDER BY count DESC
        """
        cur.execute(query, (group_id, start_time, end_time))
        rows = cur.fetchall()
        
        # 轉換 user_id 為遊戲名稱
        ranking = []
        for user_id, count in rows:
            name = get_username(user_id) # 呼叫你名冊中的遊戲名
            ranking.append((name, count))
            
        return period_text, ranking
    except Exception as e:
        print(f"KPI Error: {e}")
        return "統計出錯", []
    finally:
        conn.close()

def delete_all_boss_records(group_id):
    """確實執行 SQL 刪除"""
    conn = get_pg_conn()
    if not conn: return
    try:
        cur = conn.cursor()
        # 強制指定群組刪除
        cur.execute("DELETE FROM boss_time WHERE group_id = %s", (group_id,))
        conn.commit()  # <--- 這行沒寫資料永遠刪不掉
        print(f"PostgreSQL 刪除完成: {group_id}")
        cur.close()
    except Exception as e:
        print(f"SQL 刪除出錯: {e}")
    finally:
        conn.close()

def get_all_records_for_kpi(group_id, start_time, end_time):
    """抓取區間內所有紀錄，並格式化為符合 calculate_kpi 要求的 dict 格式"""
    conn = get_pg_conn()
    if not conn: return {}
    records = {}
    try:
        cur = conn.cursor()
        # 注意：這裡多抓一個 source 欄位，因為你的 KPI 邏輯有排除 backup
        query = """
            SELECT boss_name, kill_time, user_id, source
            FROM boss_time 
            WHERE group_id = %s 
              AND kill_time >= %s 
              AND kill_time < %s
        """
        cur.execute(query, (group_id, start_time, end_time))
        rows = cur.fetchall()
        
        for boss, kt, uid, src in rows:
            if boss not in records:
                records[boss] = []
            
            # 這裡的 Key 必須完全對應 rec['date'] 和 rec['kill']
            records[boss].append({
                "date": kt.strftime("%Y-%m-%d"),    # 對應 rec['date']
                "kill": kt.strftime("%H:%M:%S"),    # 對應 rec['kill']
                "user": uid,                        # 對應 rec['user']
                "source": src                       # 對應 rec.get("source")
            })
        cur.close()
    finally:
        conn.close()
    return records
def background_check():
    while True:
        try:
            conn = get_pg_conn()
            cur = conn.cursor()
            now = now_tw()
            
            # 撈取所有還沒重生的紀錄
            cur.execute("SELECT group_id, boss_name, respawn_time FROM boss_time")
            rows = cur.fetchall()
            
            for row in rows:
                group_id, boss_name, respawn_time = row
                
                # 確保時區一致
                if respawn_time.tzinfo is None:
                    respawn_time = TZ.localize(respawn_time)
                
                # 計算距離重生的秒數
                time_diff = (respawn_time - now).total_seconds()

                # 判斷是否在 5 分鐘左右 (270~330 秒)
                if 270 <= time_diff < 330:
                    # 【核心修改】：只針對大王清單內的王進行處理
                    if boss_name in MAJOR_BOSSES:
                        # 執行標記通知
                        notify_boss_team(group_id, boss_name)
                    # 一般王直接跳過，不做任何動作 (不用發送普通推播)
            
            cur.close()
            conn.close()
        except Exception as e:
            print(f"背景檢查發生錯誤: {e}")
        
        # 每 60 秒檢查一次
        time.sleep(60)

# 啟動背景執行緒 (放在檔案最下方)
t = threading.Thread(target=background_check)
t.daemon = True
t.start()

# 1. 定義需要 @標記 的大王清單 (名稱需與 cd_map 一致)
MAJOR_BOSSES = ["古代巨人", "不死鳥", "死亡騎士", "克特"]

def notify_boss_team(group_id, boss_name):
    conn = get_pg_conn()
    cur = conn.cursor()
    try:
        # 1. 抓取成員
        cur.execute("SELECT user_id FROM boss_team WHERE group_id = %s", (group_id,))
        rows = cur.fetchall()
        
        # 2. 基礎訊息文字
        base_msg = f"【{boss_name}】即將在 5 分鐘後重生！"
        
        if rows:
            user_ids = [r[0] for r in rows]
            text_prefix = "📢 打王組集合！ "
            mentionees = []
            
            # 3. 嚴格計算每個人的 Index 位址
            for i, uid in enumerate(user_ids[:50]):
                mentionees.append({
                    "index": len(text_prefix) + i,
                    "length": 1,
                    "userId": str(uid)
                })

            # 組合最終文字：前綴 + 空格預留位 + 訊息內容
            full_text = f"{text_prefix}{' ' * len(mentionees)}\n{base_msg}"

            # 4. 手動建構 Payload (不依賴 SDK 類別)
            payload = {
                "to": group_id,
                "messages": [
                    {
                        "type": "text",
                        "text": full_text,
                        "mention": {
                            "mentionees": mentionees
                        }
                    }
                ]
            }

            # 5. 直接發送 Post 請求到 LINE API
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
            }
            
            response = requests.post(
                "https://api.line.me/v2/bot/message/push",
                headers=headers,
                data=json.dumps(payload)
            )
            
            if response.status_code != 200:
                print(f"LINE API 報錯: {response.text}")
        else:
            # 沒人時發送普通訊息
            line_bot_api.push_message(group_id, TextSendMessage(text=f"⏰ {base_msg}"))
            
    except Exception as e:
        print(f"通知過程發生錯誤: {e}")
    finally:
        cur.close()
        conn.close()



        
# 1. 產生確認用 Flex 卡片的函式
def build_confirmation_flex(boss_name):
    bubble = {
        "type": "bubble",
        "size": "kilo", # 保持小卡片的精緻感
        "header": {
            "type": "box", 
            "layout": "vertical", 
            "backgroundColor": "#2C3E50", 
            "paddingAll": "15px", 
            "contents": [
                # 第一行：較小的副標題
                {
                    "type": "text", 
                    "text": "⚔️ 擊殺確認", 
                    "color": "#ffffff",
                    "weight": "bold",
                    "align": "center",
                    "size": "sm",
                    "opacity": "0.8" # 稍微降低透明度，不搶主視覺
                },
                # 第二行：放大的王名，並允許自動換行
                {
                    "type": "text", 
                    "text": boss_name, 
                    "color": "#FFD700", # 亮金色
                    "weight": "bold",
                    "align": "center",
                    "size": "xl", # 加大字體
                    "wrap": True, # 🌟 關鍵：允許長文字自動換行
                    "margin": "sm" # 與上一行保持一點點距離
                }
            ]
        },
        "body": {
            "type": "box", 
            "layout": "vertical", 
            "spacing": "lg", 
            "paddingAll": "20px",
            "contents": [
                {
                    "type": "text", 
                    "text": "王 5 分鐘後即將重生\n請盡速回報結果：", 
                    "size": "sm",
                    "color": "#555555", 
                    "align": "center",
                    "weight": "bold",
                    "wrap": True
                },
                {
                    "type": "box", 
                    "layout": "horizontal", 
                    "spacing": "md", 
                    "contents": [
                        {
                            "type": "button", 
                            "style": "primary", 
                            "color": "#4A90E2", 
                            "action": {
                                "type": "message", 
                                "label": "我方擊殺", 
                                "text": f"紀錄 我方擊殺 {boss_name}"
                            }
                        },
                        {
                            "type": "button", 
                            "style": "primary", 
                            "color": "#FF5252", 
                            "action": {
                                "type": "message", 
                                "label": "敵人吃", 
                                "text": f"紀錄 敵人吃 {boss_name}"
                            }
                        }
                    ]
                }
            ]
        }
    }
    return FlexSendMessage(alt_text=f"{boss_name} 擊殺確認", contents=bubble)

# 2. 5分鐘後執行的推播函式
def send_delayed_confirmation(group_id, boss_name):
    try:
        flex_msg = build_confirmation_flex(boss_name)
        # 這裡必須使用 push_message，因為已經超過 1 分鐘無法 reply
        line_bot_api.push_message(group_id, flex_msg)
    except Exception as e:
        print(f"延遲發送卡片失敗: {e}")


def auto_mark_missed(group_id, boss_name):
    """計時結束後檢查是否有回報，若無則自動記錄為漏掉"""
    conn = get_pg_conn()
    if not conn: 
        return
    
    try:
        with conn.cursor() as cur:
            # 檢查過去 15 分鐘內，這個群組的這隻王是否已經有任何狀態的紀錄
            # (用 15 分鐘是為了確保涵蓋倒數的 10 分鐘加上一點緩衝時間)
            cur.execute("""
                SELECT id FROM fixed_boss_records2
                WHERE group_id = %s AND boss_name = %s 
                AND record_time >= NOW() - INTERVAL '15 minutes'
            """, (group_id, boss_name))
            
            row = cur.fetchone()
            
            # 如果找不到紀錄，代表沒有人點擊卡片回報
            if not row:
                cur.execute("""
                    INSERT INTO fixed_boss_records2 (group_id, boss_name, status) 
                    VALUES (%s, %s, '漏掉')
                """, (group_id, boss_name))
                conn.commit()
                print(f"✅ {boss_name} 超時未回報，已自動寫入：漏掉")
                
                # (選用) 如果希望機器人自動通報漏掉了，可以取消下面這行的註解
                # line_bot_api.push_message(group_id, TextSendMessage(text=f"⏳ {boss_name} 超時未回報，系統已自動記錄為：漏掉"))
                
    except Exception as e:
        print(f"❌ 自動標記漏掉失敗: {e}")
        conn.rollback()
    finally:
        conn.close()
        
def init_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump({"boss": {}}, f, ensure_ascii=False, indent=2)
def load_db():
    with db_lock:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
def save_db(db):
    with db_lock:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
init_db()
def build_all_boss_quick_flex():
    # 取得 BOSS 名稱（確保 cd_map 已定義）
    boss_names = sorted(list(cd_map.keys()))
    
    rows = []
    # 每 4 隻王一列，減少垂直高度，避免超過螢幕
    for i in range(0, len(boss_names), 4):
        chunk = boss_names[i:i+4]
        cols = []
        for name in chunk:
            cols.append({
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#4682B4",
                "cornerRadius": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": name,
                        "size": "xxs", # 使用極小字體確保 4 欄塞得下
                        "align": "center",
                        "color": "#ffffff",
                        "weight": "bold",
                        "gravity": "center"
                    }
                ],
                "paddingAll": "8px", # 確保數值帶 px
                "action": {
                    "type": "message",
                    "label": name,
                    "text": f"6666 {name}"
                }
            })
        
        # 補齊空格
        while len(cols) < 4:
            cols.append({"type": "spacer", "flex": 1})
            
        rows.append({
            "type": "box",
            "layout": "horizontal",
            "spacing": "xs",
            "contents": cols
        })

    # 封裝成 Bubble
    bubble_content = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#2c3e50",
            "contents": [
                {"type": "text", "text": "快速登記 (6666)", "weight": "bold", "color": "#ffffff", "size": "sm", "align": "center"}
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": rows
        }
    }
    
    # 務必檢查這裡的 FlexSendMessage 拼字與結構
    return FlexSendMessage(alt_text="快速登記選單", contents=bubble_content)

def build_undo_flex(boss_name, k_time_str, r_time_str, note=None):
    """
    建立撤銷成功的 Flex Message
    """
    # 備註欄位：如果有備註才顯示，否則回傳空元件
    note_component = {
        "type": "box",
        "layout": "vertical",
        "margin": "lg",
        "spacing": "sm",
        "contents": [
            {
                "type": "box",
                "layout": "baseline",
                "spacing": "sm",
                "contents": [
                    {"type": "text", "text": "備註", "color": "#aaaaaa", "size": "sm", "flex": 1},
                    {"type": "text", "text": str(note), "wrap": True, "color": "#666666", "size": "sm", "flex": 4}
                ]
            }
        ]
    } if note else {"type": "filler"}

    contents = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "撤銷成功", "weight": "bold", "color": "#FFFFFF", "size": "sm"},
                {"type": "text", "text": boss_name, "weight": "bold", "size": "xxl", "margin": "md", "color": "#FFFFFF"}
            ],
            "backgroundColor": "#27ae60" # 成功色：綠色
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "📊 系統已回溯至上一筆紀錄", "size": "sm", "color": "#111111", "weight": "bold"},
                {"type": "separator", "margin": "md"},
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "baseline",
                            "spacing": "sm",
                            "contents": [
                                {"type": "text", "text": "擊殺", "color": "#aaaaaa", "size": "sm", "flex": 1},
                                {"type": "text", "text": k_time_str, "wrap": True, "color": "#666666", "size": "sm", "flex": 4}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "baseline",
                            "spacing": "sm",
                            "contents": [
                                {"type": "text", "text": "重生", "color": "#aaaaaa", "size": "sm", "flex": 1},
                                {"type": "text", "text": r_time_str, "wrap": True, "color": "#666666", "size": "sm", "flex": 4}
                            ]
                        }
                    ]
                },
                note_component
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": "提示：輸入「打王」查看完整清單", "style": "italic", "size": "xs", "color": "#aaaaaa", "align": "center"}
            ]
        }
    }
    return contents

# 這是暫時的名單，之後你可以隨時替換成你給我的真實名單
MAYBE_SKIP_BOSSES = ["小紅", "小綠", "守護螞蟻", "巨大蜈蚣", "伊弗利特", "大腳瑪幽", "巨大飛龍", "力卡溫", "卡司特王", "變形怪首領", "古代巨人", "不死鳥", "克特", "賽尼斯的分身", "貝里斯", "烏勒庫斯", "奈克偌斯"] 

def build_kill_list_flex(title, display_items):
    rows = []
    now = now_tw()

    for i, (dt, line_text) in enumerate(display_items):
        parts = line_text.split(" ", 1)
        time_str = parts[0] 
        boss_info = parts[1] if len(parts) > 1 else ""
        
        diff = (dt - now).total_seconds()
        if diff < 0:
            bg_color = "#FF5252"
            status_text = "已重生"
        elif diff < 1800:
            bg_color = "#FFB74D"
            status_text = "即將"
        else:
            bg_color = "#66BB6A"
            status_text = "等待"

        # 乾淨的王名，用來比對清單
        pure_name = boss_info.split("（")[0].split(" <")[0].split(" #")[0].strip()

        # 1. 擊殺按鈕 (Flex: 2)
        kill_btn = {
            "type": "box",
            "layout": "vertical",
            "flex": 2,
            "contents": [{"type": "text", "text": "擊殺", "size": "xs", "color": "#ffffff", "align": "center", "weight": "bold"}],
            "backgroundColor": "#4A90E2", 
            "cornerRadius": "lg",
            "paddingAll": "6px",
            "action": {"type": "message", "label": "K", "text": f"6666 {pure_name}"}
        }

        # 2. 時間色塊標籤 (Flex: 3，稍微縮小)
        time_box = {
            "type": "box",
            "layout": "vertical",
            "flex": 3, 
            "contents": [
                {"type": "text", "text": time_str, "size": "xxs", "color": "#ffffff", "weight": "bold", "align": "center"},
                {"type": "text", "text": status_text, "size": "xxs", "color": "#ffffff", "align": "center", "opacity": "0.9", "margin": "2px"}
            ],
            "backgroundColor": bg_color,
            "cornerRadius": "md",
            "paddingAll": "4px"
            # 移除 marginLeft，交給外層統一管理
        }

        # 3. 王名標籤 (Flex: 7，給予更多空間避免換行過多)
        boss_name_box = {
            "type": "text", 
            "text": boss_info, 
            "size": "sm", 
            "weight": "bold", 
            "flex": 7, 
            "gravity": "center", 
            "wrap": True
            # 移除 marginLeft
        }

        # 開始組合這一列的內容 (順序：擊殺 -> 時間 -> 王名)
        row_contents = [kill_btn, time_box, boss_name_box]

        # --- 判斷邏輯：如果符合輪空條件，將輪空按鈕加在最右邊 ---
        if pure_name in MAYBE_SKIP_BOSSES and "#過" not in boss_info:
            # 縮小王名空間，騰出位置給輪空按鈕
            boss_name_box["flex"] = 5
            
            # 建立輪空按鈕
            skip_btn = {
                "type": "box",
                "layout": "vertical",
                "flex": 2,
                "contents": [{"type": "text", "text": "輪空", "size": "xs", "color": "#ffffff", "align": "center", "weight": "bold"}],
                "backgroundColor": "#9E9E9E", 
                "cornerRadius": "lg",
                "paddingAll": "6px",
                "action": {"type": "message", "label": "Skip", "text": f"{pure_name} 空"} 
            }
            # 將輪空按鈕加入陣列的最尾端
            row_contents.append(skip_btn)


        # 組合整列容器
        row_box = {
            "type": "box",
            "layout": "horizontal",
            "margin": "md",
            "spacing": "sm", # 🔥 新增：利用官方內建的間距屬性，讓所有元件整齊排開
            "alignItems": "center",
            "contents": row_contents
        }

        if i > 0:
            rows.append({"type": "separator", "margin": "lg", "color": "#ECECEC"})
            row_box["margin"] = "lg"

        rows.append(row_box)

    # 組合整個 Bubble
    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box", 
            "layout": "horizontal",
            "alignItems": "center",
            "backgroundColor": "#2C3E50", 
            "paddingAll": "15px", 
            "contents": [
                {"type": "text", "text": title, "color": "#ffffff", "weight": "bold", "size": "md", "flex": 1},
                {"type": "button", "action": {"type": "message", "label": "交班", "text": "交班"}, "style": "primary", "color": "#1DB100", "height": "sm", "flex": 0}
            ]
        },
        "body": {
            "type": "box", 
            "layout": "vertical", 
            "spacing": "none", 
            "paddingAll": "20px", 
            "contents": rows if rows else [{"type": "text", "text": "目前尚無重生資料", "align": "center", "color": "#aaaaaa", "size": "sm", "margin": "xl"}]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "10px",
            "contents": [{"type": "button", "action": {"type": "message", "label": "🔄 更新清單", "text": "打王"}, "style": "secondary", "height": "sm"}]
        },
        "styles": {"footer": {"separator": True}}
    }
    
    return FlexSendMessage(alt_text=title, contents=bubble)

def notify_boss_team_with_flex(group_id, boss_name):
    conn = get_pg_conn()
    cur = conn.cursor()
    try:
        # 1. 抓取打王組成員
        cur.execute("SELECT user_id FROM boss_team WHERE group_id = %s", (group_id,))
        rows = cur.fetchall()
        
        base_msg = f"【{boss_name}】即將在 5 分鐘後重生！"
        full_text = f"⏰ 提醒：{base_msg}"
        mention_payload = None  # 用來存標記資料的變數

        # 2. 手動建構標記 (使用字典而非類別)
        if rows:
            user_ids = [r[0] for r in rows]
            text_prefix = "📢 打王組集合！ "
            mentionees = []
            
            # 手動計算每個人的標記位置
            for i, uid in enumerate(user_ids[:50]): # LINE 限制上限 50 人
                mentionees.append({
                    "index": len(text_prefix) + i,
                    "length": 1,
                    "userId": uid
                })
            
            # 組合最終文字：前綴 + 空格(標記位) + 訊息
            full_text = f"{text_prefix}{' ' * len(mentionees)}\n{base_msg}"
            # 這就是 LINE API 需要的標記字典格式
            mention_payload = {"mentionees": mentionees}

        # 3. 定義 bubble (卡片內容)
        bubble = {
            "type": "bubble",
            "size": "sm",
            "header": {
                "type": "box", "layout": "vertical", "backgroundColor": "#E74C3C",
                "contents": [{"type": "text", "text": "⚔️ 大王警告", "color": "#ffffff", "weight": "bold", "size": "sm", "align": "center"}]
            },
            "body": {
                "type": "box", "layout": "vertical", 
                "contents": [
                    {"type": "text", "text": f"{boss_name}", "weight": "bold", "size": "xl", "align": "center", "margin": "md"},
                    {"type": "text", "text": "準備重生", "size": "sm", "color": "#aaaaaa", "align": "center"}
                ]
            }
        }

        # 4. 發送訊息 (直接將字典丟入 mention 參數)
        messages = [
            TextSendMessage(text=full_text, mention=mention_payload),
            FlexSendMessage(alt_text=f"警報: {boss_name}", contents=bubble)
        ]
        
        line_bot_api.push_message(group_id, messages)
            
    except Exception as e:
        print(f"通知出錯: {e}")
    finally:
        cur.close()
        conn.close()


def undo_last_boss_record(group_id, input_text):
    """
    撤銷最近一筆登記，並回傳該王目前的最新狀態（Flex Message）。
    """
    target_boss = None
    for full_name, aliases in alias_map.items():
        if input_text in aliases or input_text == full_name:
            target_boss = full_name
            break
            
    if not target_boss:
        return False, TextSendMessage(text="❌ 找不到該王名，請確認輸入是否正確。")

    conn = get_pg_conn()
    if not conn: return False, TextSendMessage(text="❌ 資料庫連線失敗")
    
    try:
        cur = conn.cursor()
        # 1. 刪除最近一筆
        cur.execute("""
            DELETE FROM boss_time 
            WHERE id = (
                SELECT id FROM boss_time 
                WHERE group_id = %s AND boss_name = %s 
                ORDER BY id DESC LIMIT 1
            )
            RETURNING id;
        """, (group_id, target_boss))
        
        if not cur.fetchone():
            return False, TextSendMessage(text=f"❌ 找不到 {target_boss} 的任何登記紀錄可供撤銷。")
        
        conn.commit()

        # 2. 查詢更新後的最新紀錄
        cur.execute("""
            SELECT kill_time, respawn_time, note 
            FROM boss_time 
            WHERE group_id = %s AND boss_name = %s 
            ORDER BY id DESC LIMIT 1
        """, (group_id, target_boss))
        
        new_record = cur.fetchone()
        
        if new_record:
            k_time, r_time, note = new_record
            
            # --- 台灣時區轉換 (Asia/Taipei) ---
            k_tw = k_time.astimezone(TZ) if k_time.tzinfo else pytz.utc.localize(k_time).astimezone(TZ)
            r_tw = r_time.astimezone(TZ) if r_time.tzinfo else pytz.utc.localize(r_time).astimezone(TZ)

            # 產生卡片內容
            flex_json = build_undo_flex(
                target_boss, 
                k_tw.strftime('%H:%M:%S'), 
                r_tw.strftime('%H:%M:%S'), 
                note
            )
            return True, FlexSendMessage(alt_text=f"撤銷成功：{target_boss}", contents=flex_json)
        else:
            return True, TextSendMessage(text=f"✅ 已撤銷 {target_boss} 的唯一紀錄，目前無登記資料。")

    except Exception as e:
        print(f"撤銷邏輯出錯: {e}")
        return False, TextSendMessage(text="⚠️ 系統處理出錯，請稍後再試。")
    finally:
        conn.close()
from datetime import datetime, timedelta
import pytz # 記得 pip install pytz

# 🌟 新增 is_b_group=False 參數
def build_register_boss_flex(boss, kill_time, respawn_time, registrar, note=None, is_skip=False, is_b_group=False):
    # --- 1. 時間檢查邏輯 (維持不變) ---
    warning_box = None
    try:
        tz = pytz.timezone('Asia/Taipei')
        now = datetime.now(tz)
        record_time = datetime.strptime(kill_time.strip(), "%H:%M:%S")
        record_time = tz.localize(datetime(
            year=now.year, month=now.month, day=now.day,
            hour=record_time.hour, minute=record_time.minute, second=record_time.second
        ))

        if record_time > now + timedelta(minutes=10):
            record_time -= timedelta(days=1)

        diff_seconds = (now - record_time).total_seconds()
        if 1800 < diff_seconds < 43200: 
            warning_box = {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "backgroundColor": "#FFEEEE",
                "cornerRadius": "md",
                "paddingAll": "sm",
                "contents": [
                    {
                        "type": "text",
                        "text": "⚠️ 注意：此為 30 分鐘前的紀錄！",
                        "color": "#FF0000",
                        "size": "xs",
                        "weight": "bold",
                        "align": "center"
                    }
                ]
            }
    except Exception as e:
        print(f"DEBUG - 時間解析失敗: {e}")

    # --- 2. 原始 UI 構建邏輯 ---
    map_list = BOSS_MAP.get(boss, [])
    map_text = "、".join(map_list) if map_list else "未知"
    
    # 🌟 修改點：根據 is_b_group 決定不同的樣式與前綴
    if is_b_group:
        group_tag = "【特殊】" # 你可以換成 B 群的聯盟名字或代號
        boss_color = "#A020F086" if is_skip else "#00BCD4"  # B群專屬色：輪空紫 / 登記藍
        card_bg_color = "#F3E5F5" # B群專屬淡紫色背景，若不想改背景可填 "#FFFFFF"
    else:
        group_tag = ""
        boss_color = "#A020F0" if is_skip else "#FF6D18"  # A群/預設色：輪空紫 / 登記橘
        card_bg_color = "#FFFFFF" # 預設白底

    header_prefix = f"{group_tag}⭕ 輪空登記 " if is_skip else f"{group_tag}🔥 已登記 "
    time_label = "🕒 輪空：" if is_skip else "🕒 死亡："

    contents = [
        {
            "type": "text",
            "text": header_prefix,
            "weight": "bold",
            "size": "lg",
            "contents": [
                {"type": "span", "text": header_prefix},
                {"type": "span", "text": boss, "color": boss_color, "weight": "bold"}
            ]
        }
    ]

    # 若有警告則顯示
    if warning_box:
        contents.append(warning_box)

    contents.append({"type": "separator", "margin": "md"})

    # 地圖、時間、重生資訊列
    info_rows = [
        ("🗺️ 地圖：", map_text),
        (time_label, kill_time),
        ("✨ 重生：", respawn_time)
    ]

    for label, value in info_rows:
        contents.append({
            "type": "box",
            "layout": "baseline",
            "contents": [
                {"type": "text", "text": label, "size": "sm", "color": "#888888", "flex": 2},
                {"type": "text", "text": value, "wrap": True, "flex": 6}
            ]
        })

    if note:
        contents.append({
            "type": "box",
            "layout": "baseline",
            "contents": [
                {"type": "text", "text": "📌 備註：", "size": "sm", "color": "#888888", "flex": 2},
                {"type": "text", "text": note, "wrap": True, "flex": 6}
            ]
        })

    contents.extend([
        {"type": "separator", "margin": "lg"},
        {"type": "text", "text": f"👤 登記者：{registrar}", "size": "xs", "color": "#999999", "wrap": True}
    ])

    return FlexSendMessage(
        alt_text=f"{header_prefix}{boss}",
        contents={
            "type": "bubble",
            # 🌟 新增 styles 來控制卡片底色
            "styles": {
                "body": {
                    "backgroundColor": card_bg_color
                }
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": contents
            }
        }
    )

def build_register_boss_text(boss, kill_time, respawn_time, registrar, note):
    map_list = BOSS_MAP.get(boss, [])
    map_text = "、".join(map_list) if map_list else "未知"

    msg = (
        f"已登記 {boss}\n"
        f"地圖：{map_text}\n"
        f"死亡時間：{kill_time}\n"
    )
    if note:
        msg += f"備註：{note}"
    return msg
def build_help_flex():
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
        alt_text="伊娃小幫手 使用說明",
        contents={
            "type": "carousel",
            "contents": bubbles
        }
    )
def build_join_roster_guide_flex():
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
                    # ===== 標題 =====
                    {
                        "type": "text",
                        "text": "👋 歡迎加入群組",
                        "weight": "bold",
                        "size": "xl",
                        "wrap": True
                    },
                    {
                        "type": "text",
                        "text": "為了正確統計王表與 KPI\n請先完成名冊登記",
                        "wrap": True,
                        "size": "sm",
                        "color": "#666666"
                    },

                    {
                        "type": "separator",
                        "margin": "lg"
                    },

                    # ===== 指令區 =====
                    {
                        "type": "text",
                        "text": "✍️ 加入名冊方式",
                        "weight": "bold",
                        "size": "md"
                    },

                    {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "xs",
                        "backgroundColor": "#F7F7F7",
                        "paddingAll": "md",
                        "cornerRadius": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": "加入名冊 血盟名 遊戲角色名",
                                "size": "sm",
                                "weight": "bold",
                                "wrap": True
                            },
                            {
                                "type": "text",
                                "text": "📘 範例：加入名冊 酒窖 威士忌乄",
                                "size": "sm",
                                "color": "#777777",
                                "wrap": True
                            }
                        ]
                    },

                    {
                        "type": "separator",
                        "margin": "lg"
                    },

                    # ===== 補充說明 =====
                    {
                        "type": "text",
                        "text": "📌 完成後即可使用王表、吃王登記等功能",
                        "size": "xs",
                        "color": "#999999",
                        "wrap": True
                    }
                ]
            }
        }
    )

from linebot.models import FlexSendMessage, TextSendMessage

def build_boss_history_flex(boss_name, history):
    """
    建立以時間為核心視覺的 Carousel 卡片
    """
    if not history:
        return TextSendMessage(text=f"❌ 查無 {boss_name} 的紀錄。")

    bubbles = []

    for index, item in enumerate(history[:10]):
        display_time = str(item.get("time", "-"))
        display_user = str(item.get("user", "未知"))
        note_raw = item.get("note")
        display_note = str(note_raw).strip() if note_raw and str(note_raw).strip() else "-"
        
        bubble = {
            "type": "bubble",
            "size": "micro",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#464E5F", # 深藍灰質感底色
                "contents": [
                    {
                        "type": "text",
                        "text": "TIME / 登記時間",
                        "color": "#A1A7B5",
                        "size": "xxs",
                        "weight": "bold"
                    },
                    {
                        "type": "text",
                        "text": display_time,
                        "color": "#FFFFFF",
                        "size": "md",
                        "weight": "bold",
                        "margin": "sm"
                    }
                ],
                "paddingAll": "lg"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    # Boss 名稱與回報者
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "🎯",
                                "size": "xs",
                                "flex": 1
                            },
                            {
                                "type": "text",
                                "text": f"{boss_name}",
                                "size": "xs",
                                "color": "#111111",
                                "weight": "bold",
                                "flex": 5
                            }
                        ]
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "margin": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": "👤",
                                "size": "xs",
                                "flex": 1
                            },
                            {
                                "type": "text",
                                "text": display_user,
                                "size": "xs",
                                "color": "#666666",
                                "flex": 5
                            }
                        ]
                    },
                    # 裝飾用分隔線
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "lg",
                        "contents": [
                            {"type": "separator", "color": "#EEEEEE"}
                        ]
                    },
                    # 備註區塊：強化對話框感
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": "備註",
                                "size": "xxs",
                                "color": "#BBBBBB",
                                "weight": "bold"
                            },
                            {
                                "type": "text",
                                "text": display_note,
                                "size": "xs",
                                "color": "#888888",
                                "margin": "xs",
                                "wrap": True,
                                "maxLines": 3,
                                "style": "italic"
                            }
                        ]
                    }
                ],
                "paddingAll": "lg"
            },
            "styles": {
                "header": {"separator": False},
                "body": {"backgroundColor": "#FFFFFF"}
            }
        }
        bubbles.append(bubble)

    return FlexSendMessage(
        alt_text=f"📜 {boss_name} 歷史紀錄",
        contents={
            "type": "carousel",
            "contents": bubbles
        }
    )
def clear_confirm_flex():
    return {
      "type": "bubble",
      "size": "mega",
      "header": {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#D32F2F",
        "contents": [
          {
            "type": "text",
            "text": "⚠️ 危險操作確認",
            "color": "#FFFFFF",
            "weight": "bold",
            "size": "md",
            "align": "center"
          }
        ]
      },
      "body": {
        "type": "box",
        "layout": "vertical",
        "spacing": "md",
        "contents": [
          {
            "type": "text",
            "text": "清除所有王表紀錄？",
            "weight": "bold",
            "size": "md",
            "wrap": True,
            "align": "center"
          },
          {
            "type": "text",
            "text": "此動作將會抹除資料庫中所有現存紀錄，且「無法復原」。請再次確認您的操作。",
            "wrap": True,
            "size": "xs",
            "color": "#888888",
            "align": "center"
          }
        ]
      },
      "footer": {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "contents": [
          {
            "type": "button",
            "style": "primary",
            "color": "#D32F2F",
            "height": "sm",
            "action": {
              "type": "message",
              "label": "確定清除",
              "text": "確定清除"
            }
          },
          {
            "type": "button",
            "style": "link",
            "color": "#444444",
            "height": "sm",
            "action": {
              "type": "message",
              "label": "取消",
              "text": "取消清除"
            }
          }
        ]
      },
      "styles": {
        "footer": {
          "separator": True
        }
      }
    }
def build_boot_init_flex(base_time_str):
    return {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "lg",
            "contents": [
                {
                    "type": "text",
                    "text": "🔌 開機時間已紀錄",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#2E7D32"
                },
                {
                    "type": "separator",
                    "margin": "md",
                    "color": "#EEEEEE"
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "backgroundColor": "#F1F8E9",
                    "paddingAll": "md",
                    "cornerRadius": "md",
                    "contents": [
                        {
                            "type": "text",
                            "text": "🕒 開機時間",
                            "size": "xs",
                            "color": "#689F38",
                            "weight": "bold"
                        },
                        {
                            "type": "text",
                            "text": base_time_str,
                            "size": "md",
                            "weight": "bold",
                            "color": "#333333",
                            "margin": "xs"
                        }
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "contents": [
                        {
                            "type": "text",
                            "text": "ℹ️ 系統已自動補齊尚未登記的 CD 王",
                            "size": "xs",
                            "color": "#999999",
                            "wrap": True,
                            "flex": 1
                        }
                    ]
                }
            ]
        }
    }
def build_duplicate_warning_flex(boss_name, existing_status):
    # 根據已經記錄的狀態，給予不同的顏色提示，讓卡片看起來更生動
    if existing_status == "我方擊殺":
        status_color = "#4A90E2" 
    elif existing_status == "敵人吃":
        status_color = "#FF5252" 
    else:
        status_color = "#95A5A6" 

    bubble = {
        "type": "bubble",
        "size": "kilo", # 輕量級小卡片
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "20px",
            "contents": [
                # 標題區：使用橘色來表達「提醒、注意」
                {
                    "type": "text",
                    "text": "⚠️ 晚了一步！",
                    "weight": "bold",
                    "color": "#FF9800", # 警告橘
                    "size": "sm"
                },
                # 王名
                {
                    "type": "text",
                    "text": boss_name,
                    "weight": "bold",
                    "size": "xl",
                    "margin": "md",
                    "wrap": True,
                    "color": "#333333"
                },
                {"type": "separator", "margin": "md"},
                # 提示文字
                {
                    "type": "text",
                    "text": "這隻王已經被記錄過了，請勿重複登記喔！",
                    "size": "xs",
                    "color": "#888888",
                    "wrap": True,
                    "margin": "md"
                },
                # 目前狀態
                {
                    "type": "box",
                    "layout": "baseline",
                    "margin": "md",
                    "contents": [
                        {"type": "text", "text": "已記錄為", "color": "#aaaaaa", "size": "sm", "flex": 3},
                        {"type": "text", "text": existing_status, "color": status_color, "size": "md", "weight": "bold", "flex": 4}
                    ]
                }
            ]
        },
        "styles": {
            "body": {
                "backgroundColor": "#FFFDF5" # 非常淡的黃色背景，視覺上有警告意味但很柔和
            }
        }
    }
    
    return FlexSendMessage(alt_text=f"重複登記提醒：{boss_name}", contents=bubble)
def build_auction_flex(item_name, highest_bid, bidder_name):
    display_bidder = bidder_name if bidder_name else "目前尚無人出價"
    
    bubble = {
        "type": "bubble",
        "size": "mega", # 確保尺寸正確
        "header": {
            "type": "box", "layout": "vertical", "backgroundColor": "#E67E22",
            "paddingAll": "sm",
            "contents": [{"type": "text", "text": "⚔️ 盟內裝備快閃競標", "weight": "bold", "color": "#FFFFFF", "size": "sm", "align": "center"}]
        },
        "body": {
            "type": "box", "layout": "vertical", "spacing": "md",
            "contents": [
                {"type": "text", "text": f"📦 物品：{item_name}", "weight": "bold", "size": "lg", "color": "#111111"},
                {"type": "separator"},
                {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                    {"type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": "最高標", "size": "sm", "color": "#aaaaaa", "flex": 2},
                        {"type": "text", "text": f"{highest_bid} 💎", "size": "md", "weight": "bold", "color": "#E67E22", "flex": 4, "align": "end"}
                    ]},
                    {"type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": "領先者", "size": "sm", "color": "#aaaaaa", "flex": 2},
                        {"type": "text", "text": f"{display_bidder}", "size": "sm", "color": "#111111", "flex": 4, "align": "end", "weight": "bold"}
                    ]}
                ]}
            ]
        },
        "footer": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "contents": [
                {"type": "text", "text": "輸入「下標 金額」參與競標", "size": "xs", "color": "#aaaaaa", "align": "center"},
                {"type": "separator", "margin": "md"}
            ]
        }
    }
    # 注意：這裡回傳字典(dict)，方便後續調用
    return bubble
def build_kpi_flex(title, period_text, ranking):
    rows = []
    # 定義前三名的特殊顏色與圖標
    top_styles = {
        0: {"color": "#FFD700", "weight": "bold", "icon": "🥇"},  # 金
        1: {"color": "#C0C0C0", "weight": "bold", "icon": "🥈"},  # 銀
        2: {"color": "#CD7F32", "weight": "bold", "icon": "🥉"}   # 銅
    }

    for idx, (name, count) in enumerate(ranking):
        style = top_styles.get(idx, {"color": "#666666", "weight": "regular", "icon": f"{idx+1}"})
        
        # 每一行的內容
        row_content = {
            "type": "box",
            "layout": "horizontal",
            "paddingAll": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": style["icon"],
                    "size": "sm",
                    "flex": 1,
                    "align": "center",
                    "weight": style.get("weight")
                },
                {
                    "type": "text",
                    "text": name,
                    "size": "sm",
                    "flex": 4,
                    "weight": style.get("weight"),
                    "color": "#333333" if idx < 3 else "#666666"
                },
                {
                    "type": "text",
                    "text": f"{count} 次",
                    "size": "sm",
                    "align": "end",
                    "flex": 2,
                    "weight": "bold",
                    "color": style["color"] if idx < 3 else "#333333"
                }
            ]
        }
        
        # 前三名加入淡色背景強調
        if idx < 3:
            row_content["backgroundColor"] = "#F8F9FA"
            row_content["cornerRadius"] = "md"
            row_content["margin"] = "xs"

        rows.append(row_content)

    return {
        "type": "bubble",
        "size": "kilo",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1A237E",
            "contents": [
                {
                    "type": "text",
                    "text": f"🏆 {title}",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "md"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"📅 統計區間：{period_text}",
                    "size": "xs",
                    "color": "#888888",
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "xs",
                    "contents": rows
                }
            ]
        }
    }
def get_welcome_flex(notion_url):
    """回傳歡迎訊息的 Flex Message 內容"""
    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "天堂M吃王小幫手", "weight": "bold", "color": "#FFFFFF", "size": "sm"}
            ],
            "backgroundColor": "#05B050"
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "感謝邀請！", "weight": "bold", "size": "xl", "margin": "md"},
                {"type": "text", "text": "本群組已自動開啟 7 天試用期。", "size": "sm", "color": "#666666", "wrap": True},
                {"type": "separator", "margin": "lg"},
                {"type": "text", "text": "點擊下方按鈕查看如何快速上手：", "size": "sm", "color": "#999999", "margin": "md", "wrap": True}
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "uri",
                        "label": "📖 完整使用教學",
                        "uri": notion_url
                    },
                    "style": "primary",
                    "color": "#05B050"
                }
            ]
        }
    }
def build_roster_added_flex(clan, game_name):
    return {
        "type": "bubble",
        "size": "mega",  # 成功訊息不需要太大，輕量化更精緻
        "body": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#FFFFFF",
            "paddingAll": "lg",
            "contents": [
                # 頂部成功圖示與文字
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {
                            "type": "text",
                            "text": "✅",
                            "size": "lg",
                            "flex": 0
                        },
                        {
                            "type": "text",
                            "text": "登記成功",
                            "weight": "bold",
                            "size": "md",
                            "color": "#2E7D32",
                            "margin": "md",
                            "flex": 1
                        }
                    ]
                },
                # 分割線
                {
                    "type": "separator",
                    "margin": "lg",
                    "color": "#EEEEEE"
                },
                # 資料卡片區塊
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "遊戲角色", "size": "xs", "color": "#888888", "flex": 3},
                                {"type": "text", "text": game_name, "size": "sm", "color": "#333333", "weight": "bold", "flex": 7, "align": "end"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "所屬血盟", "size": "xs", "color": "#888888", "flex": 3},
                                {"type": "text", "text": clan, "size": "sm", "color": "#333333", "weight": "bold", "flex": 7, "align": "end"}
                            ]
                        }
                    ]
                },
                # 底部小字提醒
                {
                    "type": "text",
                    "text": "您現在可以正常使用王表功能了",
                    "size": "xxs",
                    "color": "#AAAAAA",
                    "margin": "xl",
                    "align": "center"
                }
            ]
        },
        "styles": {
            "body": {
                "cornerRadius": "md"
            }
        }
    }
def build_roster_confirm_update_flex(old_name, old_clan, new_name, new_clan):
    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "確認更新資料", "weight": "bold", "color": "#E67E22", "size": "lg"}
            ],
            "paddingBottom": "none"
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "系統偵測到該名冊已存在，是否要覆蓋現有資訊？", "wrap": True, "size": "sm", "color": "#8c8c8c"},
                {"type": "separator", "margin": "lg"},
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "目前內容", "size": "sm", "color": "#aaaaaa", "flex": 2},
                                {"type": "text", "text": f"{old_name} / {old_clan}", "size": "sm", "color": "#666666", "flex": 4, "align": "end"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "修改為", "size": "sm", "color": "#1DB446", "flex": 2, "weight": "bold"},
                                {"type": "text", "text": f"{new_name} / {new_clan}", "size": "sm", "color": "#1DB446", "flex": 4, "align": "end", "weight": "bold"}
                            ]
                        }
                    ]
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "height": "sm",
                    "color": "#1DB446",
                    "action": {"type": "message", "label": "確認修改", "text": "確認修改"}
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "height": "sm",
                    "action": {"type": "message", "label": "取消操作", "text": "取消"}
                }
            ]
        },
        "styles": {"footer": {"separator": True}}
    }
def build_roster_self_flex(game_name, clan):
    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "MY ROSTER",
                    "color": "#ffffff66",
                    "size": "xs",
                    "weight": "bold",
                    "letterSpacing": "2px"
                },
                {
                    "type": "text",
                    "text": "👤 我的個人名冊",
                    "color": "#ffffff",
                    "size": "lg",
                    "weight": "bold"
                }
            ],
            "backgroundColor": "#273132", # 深灰色底板，顯得較專業
            "paddingTop": "15px",
            "paddingBottom": "15px"
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": "遊戲名字", "color": "#8c8c8c", "size": "sm", "flex": 1},
                        {"type": "text", "text": game_name, "color": "#111111", "size": "sm", "flex": 2, "weight": "bold", "align": "end"}
                    ],
                    "margin": "md"
                },
                {
                    "type": "separator",
                    "margin": "md"
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": "血盟", "color": "#8c8c8c", "size": "sm", "flex": 1},
                        {"type": "text", "text": clan, "color": "#111111", "size": "sm", "flex": 2, "weight": "bold", "align": "end"}
                    ],
                    "margin": "md"
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "我的名冊",
                    "size": "xs",
                    "color": "#aaaaaa",
                    "align": "center"
                }
            ],
            "paddingTop": "10px"
        },
        "styles": {
            "footer": {
                "separator": True
            }
        }
    }
def build_stats_report_flex(total, stats_dict, details):
    body_contents = [
        # 總計區塊
        {
            "type": "box",
            "layout": "horizontal",
            "alignItems": "center",
            "contents": [
                {"type": "text", "text": "總計出現次數", "color": "#888888", "size": "sm", "flex": 2},
                {"type": "text", "text": f"{total} 次", "size": "xl", "weight": "bold", "color": "#333333", "align": "end", "flex": 2}
            ]
        },
        {"type": "separator", "margin": "lg"},
        
        # 🟢 我方擊殺
        {
            "type": "box",
            "layout": "horizontal",
            "margin": "lg",
            "contents": [
                {"type": "text", "text": "🟢 我方擊殺", "size": "sm", "color": "#4A90E2", "weight": "bold", "flex": 3},
                {"type": "text", "text": f"{stats_dict.get('我方擊殺', 0)} 次", "align": "end", "weight": "bold", "color": "#333333", "flex": 2}
            ]
        },
        # 🔴 敵人吃掉
        {
            "type": "box",
            "layout": "horizontal",
            "margin": "md",
            "contents": [
                {"type": "text", "text": "🔴 敵人吃掉", "size": "sm", "color": "#FF5252", "weight": "bold", "flex": 3},
                {"type": "text", "text": f"{stats_dict.get('敵人吃', 0)} 次", "align": "end", "weight": "bold", "color": "#333333", "flex": 2}
            ]
        },
        # ⚪ 漏掉未吃
        {
            "type": "box",
            "layout": "horizontal",
            "margin": "md",
            "contents": [
                {"type": "text", "text": "⚪ 漏掉未吃", "size": "sm", "color": "#95A5A6", "weight": "bold", "flex": 3},
                {"type": "text", "text": f"{stats_dict.get('漏掉', 0)} 次", "align": "end", "weight": "bold", "color": "#333333", "flex": 2}
            ]
        }
    ]

    # ===== 詳細時間清單 (修正為換行顯示) =====
    
    # 敵人吃場次
    if details.get("敵人吃"):
        # 建立清單字串，每一項前面加個小點並換行
        list_text = "\n".join([f"• {t}" for t in details["敵人吃"]])
        
        body_contents.append({"type": "separator", "margin": "lg"})
        body_contents.append({
            "type": "text", "text": "⚠️ 敵人吃場次明細：", "size": "xs", "color": "#FF5252", "weight": "bold", "margin": "md"
        })
        body_contents.append({
            "type": "text", 
            "text": list_text, 
            "size": "xs", 
            "color": "#666666", 
            "wrap": True, 
            "margin": "sm"
        })

    # 漏掉場次
    if details.get("漏掉"):
        # 建立清單字串，每一項前面加個小點並換行
        list_text = "\n".join([f"• {t}" for t in details["漏掉"]])
        
        body_contents.append({"type": "separator", "margin": "lg"})
        body_contents.append({
            "type": "text", "text": "⚠️ 漏掉場次明細：", "size": "xs", "color": "#95A5A6", "weight": "bold", "margin": "md"
        })
        body_contents.append({
            "type": "text", 
            "text": list_text, 
            "size": "xs", 
            "color": "#666666", 
            "wrap": True, 
            "margin": "sm"
        })

    # 組裝
    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#2C3E50",
            "paddingAll": "15px",
            "contents": [
                {"type": "text", "text": "📊 固定王統計報表", "color": "#ffffff", "weight": "bold", "size": "md", "align": "center"}
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "20px",
            "contents": body_contents
        }
    }

    return FlexSendMessage(alt_text="固定王統計報表", contents=bubble)
def build_roster_delete_confirm_flex(game_name):
    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "⚠️ 刪除確認", "weight": "bold", "color": "#E74C3C", "size": "lg"}
            ],
            "paddingBottom": "none"
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "確定要從系統中移除此角色嗎？此動作無法復原。", "wrap": True, "size": "sm", "color": "#666666"},
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "backgroundColor": "#FDF2F2",
                    "paddingAll": "md",
                    "cornerRadius": "sm",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "待刪除角色", "size": "sm", "color": "#888888", "flex": 3},
                                {"type": "text", "text": f"{game_name}", "size": "sm", "color": "#E74C3C", "flex": 4, "align": "end", "weight": "bold"}
                            ]
                        }
                    ]
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#E74C3C",
                    "height": "sm",
                    "action": {"type": "message", "label": "確認刪除", "text": "確認刪除"}
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "height": "sm",
                    "action": {"type": "message", "label": "取消", "text": "取消"}
                }
            ]
        }
    }

from linebot.models import FlexSendMessage, BubbleContainer

def build_error_flex(title, message, boss_name):
    """
    生成高質感警示類型 Flex Message
    """
    flex_content = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#FF5252", # 鮮明紅色背景
            "paddingAll": "20px",
            "contents": [
                {
                    "type": "text",
                    "text": f"⚠️ {title}", # 加入圖示
                    "weight": "bold",
                    "color": "#FFFFFF", # 改為白字提升對比
                    "size": "lg"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "20px",
            "contents": [
                # Boss 名稱標籤
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {
                            "type": "text",
                            "text": "目標對象",
                            "size": "sm",
                            "color": "#aaaaaa",
                            "flex": 2
                        },
                        {
                            "type": "text",
                            "text": boss_name,
                            "weight": "bold",
                            "size": "sm",
                            "color": "#333333",
                            "flex": 4,
                            "align": "end"
                        }
                    ]
                },
                {"type": "separator", "margin": "md"},
                # 錯誤內容區塊
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "paddingAll": "12px",
                    "backgroundColor": "#F8F9FA", # 淺灰色背景區塊
                    "cornerRadius": "md",
                    "contents": [
                        {
                            "type": "text",
                            "text": "詳細資訊",
                            "size": "xxs",
                            "color": "#888888",
                            "margin": "none"
                        },
                        {
                            "type": "text",
                            "text": message,
                            "wrap": True,
                            "color": "#E63946", # 訊息文字保持紅色警示
                            "size": "sm",
                            "margin": "sm",
                            "weight": "bold"
                        }
                    ]
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "請確認後重新輸入",
                    "size": "xs",
                    "color": "#aaaaaa",
                    "align": "center"
                }
            ],
            "paddingBottom": "15px"
        }
    }

    # 返回轉換後的 FlexSendMessage
    return FlexSendMessage(
        alt_text=f"錯誤通知: {title}",
        contents=BubbleContainer.new_from_json_dict(flex_content)
    )
def build_roster_deleted_flex():
    return {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "md",
                    "alignItems": "center",
                    "contents": [
                        {"type": "text", "text": "🗑", "size": "xl", "flex": 0},
                        {
                            "type": "box",
                            "layout": "vertical",
                            "contents": [
                                {"type": "text", "text": "名冊已成功刪除", "weight": "bold", "size": "md", "color": "#555555"},
                                {"type": "text", "text": "該角色資訊已從資料庫移除", "size": "xs", "color": "#aaaaaa"}
                            ]
                        }
                    ]
                }
            ]
        }
    }
def build_roster_search_flex(keyword, rows):
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
                        "size": "sm",
                        "weight": "bold"
                    },
                    {
                        "type": "text",
                        "text": f"📱 LINE名稱：{line_name}",
                        "size": "sm",
                        "weight": "bold"
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
def ensure_roster_table():
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS roster (
                id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

                line_user_id TEXT NOT NULL,
                game_name TEXT NOT NULL,
                clan_name TEXT NOT NULL,
                line_name TEXT,

                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),

                UNIQUE (line_user_id, game_name)
            );
            """)
        conn.commit()
def ensure_shift_table():
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS shift_info (
                    group_id TEXT PRIMARY KEY,
                    current_user_id TEXT,
                    next_user_id TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            conn.commit()

# 在啟動區（如 init_db 附近）執行一次
ensure_shift_table()
def get_line_display_name(user_id):
    try:
        profile = line_bot_api.get_profile(user_id)
        return profile.display_name
    except Exception:
        return None
def query_roster(clan_name=None):
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            if clan_name:
                cur.execute("""
                    SELECT game_name, clan_name, COALESCE(line_name, '') as line_name
                    FROM roster
                    WHERE clan_name = %s
                    ORDER BY created_at
                """, (clan_name,))
            else:
                cur.execute("""
                    SELECT game_name, clan_name, COALESCE(line_name, '') as line_name
                    FROM roster
                    ORDER BY clan_name, created_at
                """)
            return cur.fetchall()
def search_roster(keyword):
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT game_name, clan_name, COALESCE(line_name, '') as line_name
                FROM roster
                WHERE game_name ILIKE %s
                   OR clan_name ILIKE %s
                   OR line_name ILIKE %s
                ORDER BY clan_name, game_name;
            """, (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"))
            return cur.fetchall()
def build_boss_list_text():
    lines = ["📜【王列表（含所有簡稱）】", ""]
    for boss, aliases in alias_map.items():
        alias_text = "、".join(aliases)
        lines.append(f"🔹 {boss}")
        lines.append(f"   ➜ {alias_text}")
        lines.append("")
    return "\n".join(lines)
def build_boss_cd_list_text():
    lines = ["⏳【王重生時間一覽】", ""]
    for boss, cd in sorted(cd_map.items(), key=lambda x: x[1]):  # 小數轉成 小時 + 分鐘
        hours = int(cd)
        minutes = int((cd - hours) * 60)
        if minutes > 0:
            cd_text = f"{hours} 小時 {minutes} 分"
        else:
            cd_text = f"{hours} 小時"
        lines.append(f"🔹 {boss}：{cd_text}")
    return "\n".join(lines)
def get_status_flex(status_text, expiry_date, days_left):
    """回傳群組狀態的 Flex Message 內容"""
    # 根據剩餘天數決定顏色 (少於 3 天顯示紅色提醒)
    status_color = "#E63946" if days_left < 3 else "#1DB954"
    
    return {
      "type": "bubble",
      "size": "mega",
      "body": {
        "type": "box",
        "layout": "vertical",
        "contents": [
          {"type": "text", "text": "🛡️ 群組權限狀態", "weight": "bold", "color": "#1DB954", "size": "sm"},
          {"type": "text", "text": "🟢 服務中", "weight": "bold", "size": "xxl", "margin": "md"},
          {"type": "separator", "margin": "lg", "backgroundColor": "#EEEEEE"},
          {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "spacing": "sm",
            "contents": [
              {
                "type": "box",
                "layout": "baseline",
                "spacing": "sm",
                "contents": [
                  {"type": "text", "text": "目前權限", "color": "#aaaaaa", "size": "sm", "flex": 2},
                  {"type": "text", "text": status_text, "wrap": True, "color": "#666666", "size": "sm", "flex": 5}
                ]
              },
              {
                "type": "box",
                "layout": "baseline",
                "spacing": "sm",
                "contents": [
                  {"type": "text", "text": "到期日期", "color": "#aaaaaa", "size": "sm", "flex": 2},
                  {"type": "text", "text": expiry_date, "wrap": True, "color": "#666666", "size": "sm", "flex": 5}
                ]
              },
              {
                "type": "box",
                "layout": "baseline",
                "spacing": "sm",
                "contents": [
                  {"type": "text", "text": "剩餘天數", "color": "#aaaaaa", "size": "sm", "flex": 2},
                  {"type": "text", "text": f"{days_left} 天", "wrap": True, "color": status_color, "size": "sm", "flex": 5, "weight": "bold"}
                ]
              }
            ]
          }
        ]
      },
      "footer": {
        "type": "box",
        "layout": "vertical",
        "contents": [
          {
            "type": "button",
            "action": {
              "type": "uri",
              "label": "了解續約方案",
              "uri": "https://line.me/ti/p/wenhao0222"
            },
            "style": "link",
            "height": "sm"
          }
        ]
      }
    }
def get_delete_result_flex(success, name_input, final_name=None):
    """回傳刪除操作結果的 Flex Message 內容 (已修正 size 報錯)"""
    if success:
        main_color = "#E63946"
        title = "🗑 已成功清除"
        description = f"【{final_name}】的相關紀錄已從系統中移除。"
        icon_url = "https://cdn-icons-png.flaticon.com/512/1214/1214428.png"
    else:
        main_color = "#AAAAAA"
        title = "❌ 找不到紀錄"
        description = f"系統中找不到與「{name_input}」相符的資料。"
        icon_url = "https://cdn-icons-png.flaticon.com/512/564/564619.png"

    return {
        "type": "bubble",
        "size": "kilo",  # 修正處：確保使用 kilo, mega 等標準值，或直接移除此行讓它預設
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "image",
                    "url": icon_url,
                    "size": "xxs", # 圖片的 size 是合法的
                    "aspectMode": "fit"
                },
                {
                    "type": "text",
                    "text": title,
                    "weight": "bold",
                    "size": "lg", # 文字的 size 是合法的
                    "align": "center",
                    "color": main_color
                },
                {
                    "type": "text",
                    "text": description,
                    "size": "sm",
                    "color": "#666666",
                    "wrap": True,
                    "align": "center"
                }
            ]
        }
    }
def build_roster_flex(rows):
    body_contents = []

    # === 標題欄位列 ===
    body_contents.append({
        "type": "box",
        "layout": "horizontal",
        "paddingAll": "8px",
        "backgroundColor": "#333333",  # 深色背景讓標題更醒目
        "contents": [
            {"type": "text", "text": "角色", "flex": 3, "size": "xs", "color": "#FFFFFF", "weight": "bold"},
            {"type": "text", "text": "血盟", "flex": 2, "size": "xs", "color": "#FFFFFF", "weight": "bold", "align": "center"},
            {"type": "text", "text": "LINE", "flex": 2, "size": "xs", "color": "#FFFFFF", "weight": "bold", "align": "end"}
        ]
    })

    # === 資料列 (帶斑馬紋邏輯) ===
    for i, (game_name, line_name, clan_name) in enumerate(rows):
        # 奇數行使用淺灰色背景
        bg_color = "#F9F9F9" if i % 2 == 1 else "#FFFFFF"
        
        body_contents.append({
            "type": "box",
            "layout": "horizontal",
            "paddingAll": "10px",
            "backgroundColor": bg_color,
            "contents": [
                {
                    "type": "text",
                    "text": game_name,
                    "flex": 3,
                    "size": "sm",
                    "weight": "bold",
                    "wrap": True,
                    "color": "#111111"
                },
                {
                    "type": "text",
                    "text": clan_name if clan_name else "-",
                    "flex": 2,
                    "size": "xs",
                    "align": "center",
                    "color": "#666666",
                    "margin": "sm"
                },
                {
                    "type": "text",
                    "text": line_name if line_name else "-",
                    "flex": 2,
                    "size": "xs",
                    "align": "end",
                    "color": "#1E90FF"  # 維持你原本的藍色區分
                }
            ]
        })

    # === 底部提醒 ===
    body_contents.append({
        "type": "box",
        "layout": "vertical",
        "margin": "md",
        "contents": [
            {"type": "separator", "color": "#EEEEEE"},
            {
                "type": "text",
                "text": "💡 資料有誤請連繫 @H. 進行修正",
                "size": "xxs",
                "color": "#AAAAAA",
                "align": "center",
                "margin": "md"
            }
        ]
    })

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#F4F4F4",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "text",
                    "text": "📖 名冊資料",
                    "weight": "bold",
                    "size": "md",
                    "color": "#444444"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "none",
            "paddingAll": "0px",  # 滿版表格感
            "contents": body_contents
        }
    }
def build_shift_status_flex(group_id, current_uid, next_uid):
    current_name = get_username(current_uid) if current_uid else "目前空班中"
    
    if not next_uid:
        next_display = "⚠️ 尚無人接班"
        next_color = "#FF5252"
        next_weight = "bold"
    else:
        next_display = get_username(next_uid)
        next_color = "#555555"
        next_weight = "bold"

    # 移除 bubble 層級的 "size": "md"，讓它使用預設值，徹底避開 /size 報錯位置
    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#2C3E50",
            "paddingAll": "20px",
            "contents": [
                {
                    "type": "text",
                    "text": "⚔️ 王表交接系統",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "lg",
                    "align": "center"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "xl",
            "paddingAll": "20px",
            "contents": [
                {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": "當前值班員", "size": "xs", "color": "#aaaaaa"},
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "alignItems": "center",
                            "margin": "sm",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "●",
                                    "size": "xs",
                                    "color": "#66BB6A" if current_uid else "#FF5252",
                                    "flex": 0
                                },
                                {
                                    "type": "text",
                                    "text": current_name,
                                    "weight": "bold",
                                    "size": "md",
                                    "margin": "md",
                                    "flex": 1
                                }
                            ]
                        }
                    ]
                },
                {"type": "separator"},
                {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": "預計接班人員", "size": "xs", "color": "#aaaaaa"},
                        {
                            "type": "text",
                            "text": next_display,
                            "color": next_color,
                            "weight": next_weight,
                            "size": "md",
                            "margin": "sm"
                        }
                    ]
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "15px",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "message",
                        "label": "🙋 我要接班",
                        "text": "接班"
                    },
                    "style": "primary",
                    "color": "#1DB100",
                    "height": "sm"
                }
            ]
        },
        "styles": {
            "footer": {"separator": True}
        }
    }
    
    return FlexSendMessage(alt_text="📢 交接班狀態確認", contents=bubble)
def get_boss_history(group_id, boss_name):
    """查詢該群組、該王的最近 5 筆擊殺紀錄"""
    conn = get_pg_conn()
    if not conn: return []
    try:
        cur = conn.cursor()
        query = """
            SELECT kill_time, user_id, note
            FROM boss_time
            WHERE group_id = %s AND boss_name = %s
            ORDER BY kill_time DESC
            LIMIT 5
        """
        cur.execute(query, (group_id, boss_name))
        rows = cur.fetchall()
        cur.close()
        
        history = []
        for row in rows:
            kt_raw = row[0]
            kt_tw = kt_raw.astimezone(TZ) if kt_raw.tzinfo else pytz.utc.localize(kt_raw).astimezone(TZ)
            history.append({
                "time": kt_tw.strftime("%m/%d %H:%M"),
                "user": get_username(row[1]), # 呼叫您既有的取名函式
                "note": row[2] if row[2] else ""
            })
        return history
    except Exception as e:
        print(f"Error fetching history: {e}")
        return []
    finally:
        conn.close()
def build_record_success_flex(boss_name, status):
    # 根據不同的狀態，給予不同的視覺顏色與圖示
    if status == "我方擊殺":
        status_color = "#4A90E2" # 質感藍色
        icon = "✅"
    elif status == "敵人吃":
        status_color = "#FF5252" # 警示紅色
        icon = "☠️"
    else:
        status_color = "#95A5A6" # 中性灰色 (漏掉或其他)
        icon = "⚠️"

    bubble = {
        "type": "bubble",
        "size": "kilo", # 輕量級小卡片
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "20px",
            "contents": [
                # 標題區
                {
                    "type": "text",
                    "text": f"{icon} 紀錄成功",
                    "weight": "bold",
                    "color": "#888888",
                    "size": "xs"
                },
                # 王名
                {
                    "type": "text",
                    "text": boss_name,
                    "weight": "bold",
                    "size": "xl",
                    "margin": "md",
                    "wrap": True
                },
                {"type": "separator", "margin": "md"},
                # 狀態結果
                {
                    "type": "box",
                    "layout": "baseline",
                    "margin": "md",
                    "contents": [
                        {"type": "text", "text": "狀態", "color": "#aaaaaa", "size": "sm", "flex": 2},
                        {"type": "text", "text": status, "color": status_color, "size": "md", "weight": "bold", "flex": 5}
                    ]
                }
            ]
        },
        "styles": {
            "body": {
                "backgroundColor": "#FAFAFA" # 帶有一點點微灰的質感白底
            }
        }
    }
    
    return FlexSendMessage(alt_text=f"紀錄成功：{boss_name} {status}", contents=bubble)
def build_shift_success_flex(user_name):
    # 簡潔的成功提示卡片
    return FlexSendMessage(
        alt_text="接班成功",
        contents={
            "type": "bubble", "size": "kilo",
            "body": {
                "type": "box", "layout": "vertical", "paddingAll": "lg",
                "contents": [
                    {"type": "text", "text": "✅ 接班登記成功", "weight": "bold", "color": "#2E7D32"},
                    {"type": "text", "text": f"下一班人員：{user_name}", "margin": "md", "size": "sm"},
                    {"type": "separator", "margin": "md"},
                    {"type": "text", "text": "💡 交班請輸入 @All 交班", "margin": "md", "size": "xs", "color": "#888888"}
                ]
            }
        }
    )

# 王資料
alias_map = {
    "四色": ["四色", "76", "4", "四", "4色","c","C"],
    "小紅": ["小紅", "55", "紅", "R", "r"],
    "小綠": ["小綠", "54", "綠", "G", "g"],
    "守護螞蟻": ["守護螞蟻", "螞蟻", "29", "ant", "a", "A"],
    "巨大蜈蚣": ["巨大蜈蚣", "蜈蚣", "海4", "海蟲", "6", "06"],
    "86左飛龍": ["左飛龍", "861", "86左飛龍", "左", "86下"],
    "86右飛龍": ["右飛龍", "862", "86右飛龍", "右", "86上"],
    "伊弗利特": ["伊弗利特", "伊弗", "EF", "ef", "伊佛", "衣服", "E", "e", "Ef", "eF"],
    "大腳瑪幽": ["大腳瑪幽", "大腳", "69", "F", "f"],
    "巨大飛龍": ["巨大飛龍", "巨飛", "GF", "82", "gf"],
    "83中飛龍": ["中飛龍", "中", "中央龍", "83", "83中飛龍"],
    "85東飛龍": ["東飛龍", "東", "85飛龍", "85","85東飛龍"],
    "大黑長者": ["大黑長者", "大黑", "黑", "863","b","B"],
    "力卡溫": ["力卡溫", "狼人", "狼王", "22", "狼", "W", "w"],
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
    "樹精": 3, "蜘蛛": 4, "變形怪首領": 7, "古代巨人": 8.5,
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
fixed_bosses = {
     "奇岩一樓王": {
        "times": ["00:00", "06:00", "12:00", "18:00"],
        "weekdays": [0, 1, 2, 3, 4]  # 週一～週五
    },"奇岩二樓王": {
        "times": ["07:00", "14:00", "21:00"],
        "weekdays": [0, 1, 2, 3, 4]
    },"奇岩三樓王": {
        "times": ["20:15"],
        "weekdays": [0, 1, 2, 3, 4]
    },"奇岩四樓王": {
        "times": ["21:15"],
        "weekdays": [0, 1, 2, 3, 4]
    },"黑暗四樓王": {
        "times": ["00:00", "18:00"]
    },"三王": {
        "times": ["19:15"]
    },"惡魔": {
        "times": ["22:00"]
    },"巴風特": {
        "times": ["14:00", "20:00"]
    },"異界炎魔": {
        "times": ["23:00"]
    },"烈焰大死騎": {
        "times": ["23:30"]
    },"涅默西斯高輪": {
        "times": ["22:30"]
    },"魔法師": {
        "times": ["01:00","03:00","05:00","07:00","09:00","11:00",
                  "13:00","15:00","17:00","19:00","21:00","23:00"]
    }
}
def get_real_boss_name(input_name):
    """
    將使用者輸入的簡稱，轉換為 alias_map 中的正式名稱
    """
    for formal_name, aliases in alias_map.items():
        if input_name in aliases:
            return formal_name
    return input_name # 如果找不到，回傳原本的輸入 (讓資料庫自己去比對)

# 邏輯函式
def get_roster_profile(user_id):
    row = roster_get_by_user(user_id)
    if not row:
        return None
    game_name, clan_name, line_name = row
    return {
        "name": game_name,
        "clan": clan_name,
        "line_name": line_name
    }
def get_boss(name):
    for boss, aliases in alias_map.items():
        if name in aliases:
            return boss
    return None
def parse_time(token):
    now = now_tw()
    try:
        if token in ("6", "6666", "K", "k"):
            return now
        if token.isdigit() and len(token) == 4:
            h = int(token[:2])
            m = int(token[2:])
            if h > 23 or m > 59:
                return None
            t = now.replace(hour=h, minute=m, second=0)
            if t > now:
                t -= timedelta(days=1)
            return t
        if token.isdigit() and len(token) == 6:
            h = int(token[:2])
            m = int(token[2:4])
            s = int(token[4:])
            if h > 23 or m > 59 or s > 59:
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
        weekday = current_date.weekday()# 有設定 weekdays，但今天不在 → 跳過
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
    for boss, cd in cd_map.items(): # 已有紀錄就跳過
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

def init_cd_boss_with_given_time(group_id, base_time, user_id, cd_map):   
    """
    開機初始化：將各王的開機時間寫入 PostgreSQL 資料庫。
    
    【參數說明】
    - group_id: 群組的 ID (字串或數字)
    - base_time: 基準時間 (datetime 物件，通常是現在時間)
    - user_id: 執行此動作的使用者 ID
    - cd_map: 記錄各王冷卻時間的字典，例如 {'巴風特': 2, '死神': 4}
    """
    # 呼叫你專案中取得資料庫連線的函式
    conn = get_pg_conn()
    if not conn: 
        print("無法取得資料庫連線")
        return
    
    try:
        cur = conn.cursor()
        
        # 1. 抓出該群組中已經有紀錄的王
        cur.execute("""
            SELECT DISTINCT boss_name 
            FROM boss_time 
            WHERE group_id = %s
        """, (group_id,))
        
        # 使用 Set Comprehension 整理已記錄的王，加快後續比對速度
        recorded_bosses = {row[0] for row in cur.fetchall()}
        
        # 2. 遍歷 cd_map，將沒紀錄的王寫入資料庫
        count = 0
        for boss, cd in cd_map.items():
            if boss in recorded_bosses:
                continue # 已有紀錄就跳過
            
            # 計算重生時間
            respawn = base_time + timedelta(hours=cd)
            
            # 寫入 PostgreSQL 的 boss_time 資料表
            insert_query = """
                INSERT INTO boss_time (group_id, boss_name, kill_time, respawn_time, user_id, note, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            cur.execute(insert_query, (group_id, boss, base_time, respawn, user_id, "🔌開機", "boot"))
            count += 1
            
        # 3. 提交變更 (非常重要，沒這行不會存檔！)
        conn.commit()
        print(f"成功將 {count} 筆開機紀錄寫入 PostgreSQL 資料庫！")
        
    except Exception as e:
        conn.rollback() # 出錯時復原，避免資料庫狀態異常
        print(f"寫入資料庫發生錯誤: {e}")
    finally:
        # 確保正確關閉指標與連線，釋放資源
        if 'cur' in locals():
            cur.close()
        conn.close()

from datetime import datetime, timedelta
import pytz

def handle_boss_skipped(event, group_id, boss_name, user_id, note):
    cd = cd_map.get(boss_name)
    if cd is None: 
        return

    latest_records = get_latest_boss_records(group_id)
    now = now_tw()

    # 1. 歷史紀錄檢查
    if boss_name not in latest_records:
        error_msg = f"❌ 找不到【{boss_name}】的歷史紀錄，無法進行輪空登記。\n請先使用「一般擊殺登記」建立初始時間基準。"
        safe_reply(event, error_msg, None)
        return

    last_respawn_iso = latest_records[boss_name][0]["respawn"]
    base_time = datetime.fromisoformat(last_respawn_iso)
    
    if base_time.tzinfo is None:
        base_time = base_time.replace(tzinfo=pytz.UTC)
    base_time = base_time.astimezone(TZ)

    # ==========================================
    # 🛡️ 阻擋機制與 Flex 警告
    # ==========================================
    early_buffer = timedelta(minutes=5)
    late_buffer = timedelta(minutes=15)

    # 阻擋 A：太早按 (防呆：剛死就按輪空)
    if now < (base_time - early_buffer):
        title = "⚠️ 登記失敗：冷卻中"
        msg = f"這隻王還沒重生喔！\n預計重生時間為：{base_time.strftime('%H:%M')}\n請等王出現後再進行輪空操作。"
        
        flex_msg = build_error_flex(title, msg, boss_name)
        text_msg = f"⚠️ {title}\n{msg}" # 備援文字
        
        safe_reply(event, text_msg, flex_msg)
        return

    # 阻擋 B：太晚按 (防呆：已經 #過 很久)
    if now > (base_time + late_buffer):
        title = "⚠️ 登記失敗：已逾時"
        msg = f"此王已逾時超過 {late_buffer.seconds // 60} 分鐘。\n為確保時間準確，請在擊殺後改用「一般擊殺登記」來校正時間。"
        
        flex_msg = build_error_flex(title, msg, boss_name)
        text_msg = f"⚠️ {title}\n{msg}" # 備援文字
        
        safe_reply(event, text_msg, flex_msg)
        return
    # ==========================================
    # ==========================================

    # 計算下次重生時間 (依然維持基準點精準相加，時間不會跑掉)
    new_respawn = base_time + timedelta(hours=cd)
    
    # 儲存至資料庫
    save_boss_to_pg(
        group_id=group_id,
        boss_name=boss_name,
        kill_time=base_time, 
        respawn_time=new_respawn,
        user_id=user_id,
        note=note,
        source="skip" 
    )

    # 準備回覆訊息
    registrar = get_username(user_id)
    kill_str = base_time.strftime("%H:%M:%S")
    resp_str = new_respawn.strftime("%H:%M:%S")
    
    flex_msg = build_register_boss_flex(boss_name, kill_str, resp_str, registrar, note, is_skip=True)
    text_msg = f"⭕ 輪空登記成功：{boss_name}\n基準點：{kill_str}\n下趟重生：{resp_str}"
    
    safe_reply(event, text_msg, flex_msg)
def get_kpi_range(now):
    """
    計算以『週三 09:00』為起點的 KPI 區間
    區間：本週三 09:00:00 ~ 下週三 09:00:00 (不含)
    """
    # 計算距離最近一個週三差幾天 (Mon=0, Tue=1, Wed=2...)
    days_since_wed = (now.weekday() - 2) % 7
    
    # 取得本週三的日期
    start = now - timedelta(days=days_since_wed)
    # 強制設定時間為 05:00:00
    start = start.replace(hour=9, minute=0, second=0, microsecond=0)
    
    # 【關鍵判斷】：如果「現在時間」還沒到「本週三 05:00」
    # 代表統計起點應該是「上週三 05:00」
    if now < start:
        start -= timedelta(days=7)
    
    # 結束點為起點往後推 7 天
    end = start + timedelta(days=7)
    
    return start, end
def calculate_kpi(boss_db, start, end):
    """
    boss_db = db["boss"][group_id]
    回傳 dict: {user_id: count}
    排除：
    - 開機補登記 (__SYSTEM__)
    - 備份 / 多行貼上登記 (source=backup)
    """
    result = {}
    seen = set()  # KPI 去重

    for boss, records in boss_db.items():
        for rec in records:
            # 1️⃣ 排除開機補登
            if rec.get("user") == "__SYSTEM__":
                continue

            # 2️⃣ 排除備份 / 多行貼上登記
            if rec.get("source") == "backup":
                continue

            kill_dt = TZ.localize(
                datetime.strptime(
                    f"{rec['date']} {rec['kill']}",
                    "%Y-%m-%d %H:%M:%S"
                )
            )

            if not (start <= kill_dt < end):
                continue

            uid = rec["user"]
            key = (uid, boss, kill_dt)
            if key in seen:
                continue
            seen.add(key)
            result[uid] = result.get(uid, 0) + 1
    return result
def build_query_boss_flex(boss, records):
    if not records:
        return TextSendMessage("尚無紀錄")
    bubbles = []
    for rec in reversed(records):   # ⭐ 新 → 舊（保險再 reversed 一次）
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
                """
                SELECT game_name, clan_name, line_name
                FROM roster
                WHERE line_user_id = %s
                ORDER BY updated_at DESC
                LIMIT 1

                """,
                (user_id,)
            )
            return cur.fetchone()
def roster_insert(user_id, game_name, clan_name, line_name):
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO roster (line_user_id, line_name, game_name, clan_name)
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, line_name, game_name, clan_name)
            )
        conn.commit()
def roster_update(user_id, game_name, clan_name):
    line_name = get_line_display_name(user_id)
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE roster
                SET game_name = %s,
                    clan_name = %s,
                    line_name = %s,
                    updated_at = NOW()
                WHERE line_user_id = %s
                """,
                (game_name, clan_name, line_name, user_id)
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
    ensure_roster_table()# asyncio.create_task(boss_reminder_loop())
@app.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    body = await request.body()
    await process_line_event(body, x_line_signature)
    return "OK"
async def process_line_event(body: bytes, signature: str):
    try:
        handler.handle(body.decode("utf-8"), signature)
    except Exception as e:
        print("LINE 背景處理錯誤:", e)
@handler.add(JoinEvent)
def handle_join(event):
    """當機器人被邀請加入群組時觸發"""
    group_id = get_source_id(event)
    
    # 執行訂閱檢查
    check_subscription(group_id)
    
    notion_url = "https://erratic-penguin-857.notion.site/M-3069463a3aa78018be13fe885278b1cc?source=copy_link"
    
    # 使用剛才定義的單一函式取得內容
    flex_content = get_welcome_flex(notion_url)
    
    try:
        line_bot_api.reply_message(
            event.reply_token,
            [
                FlexSendMessage(alt_text="小幫手報到！", contents=flex_content)
            ]
        )
    except Exception as e:
        print(f"Error: {e}")
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
    if not line:
        return ""
    # 王表備份標題可忽略
    if line.startswith("📦") or "王表備份" in line:
        return ""
    # 分隔線或裝飾
    if line.startswith("—"):
        return ""
    # 🔥 移除「#過N」或「#過 N」
    line = re.sub(r"\s*#\s*過\s*\d+", "", line)
    # 壓縮多餘空白
    line = re.sub(r"\s{2,}", " ", line).strip()
    # 忽略多行輸入
    if "\n" in line:
        return ""
    return line
def build_kpi_backup_text(kpi_db):
    lines = ["__KPI_START__"]
    for user_id, count in kpi_db.items():
        name = get_username(user_id)
        lines.append(f"{name} {user_id} {count}")
    lines.append("__KPI_END__")
    return "\n".join(lines)
#-------------------------------------------------------------****訊息判斷****---------------------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    group_id = get_source_id(event) 
    text = event.message.text
    user = event.source.user_id
    user_id = event.source.user_id
    text = event.message.text.strip()
    msg = text
    raw_text = event.message.text.strip()
    lines = raw_text.splitlines()
    success_count = 0
    failed_lines = []
    # 在進入迴圈前，先定義好模式判斷
    is_multi_register = len(lines) > 1
    # 只有包含「📦」或「備份」字眼的多行訊息，才判定為靜音備份模式
    is_backup_mode = is_multi_register and ("📦" in raw_text or "備份" in raw_text)
    db = load_db()
    group_id = get_source_id(event)
    db.setdefault("boss", {})
    db["boss"].setdefault(group_id, {})
    raw_text = event.message.text.strip()
    msg_text_no_space = raw_text.replace(" ", "")
    text = event.message.text.strip()
    # 🌟 關鍵：確保 group_id 是透過轉換函式取得的共用 ID
    group_id = get_source_id(event) 
    user_id = event.source.user_id
    # 【強制初始化資料庫的隱藏指令】
    if text == "初始化資料庫":
        try:
            # 直接在此處呼叫我們之前寫好的建置資料庫函式
            init_db()
            line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage(text="🛠️ 已發送強制建立資料庫指令！")
            )
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage(text=f"❌ 初始化發生嚴重錯誤: {e}")
            )
        return

    import threading
    from datetime import datetime
    import pytz

    # 設定台灣時區
    TZ = pytz.timezone('Asia/Taipei')

    # --- 程式碼區塊開始 ---

    # 【功能 A】攔截 iOS 捷徑的提醒，並啟動 5 分鐘倒數
    if "⏰固定王提醒" in text and "倒數5️⃣分鐘" in text and "Boss" in text:

        # 動態擷取 Boss 的名字
        boss_name = "未知王" # 給個預設防呆值
        for line in text.split('\n'):
            if "Boss" in line:
                # 將該行用 'Boss' 切割，取後面的文字，並清除多餘的空白
                boss_name = line.split("Boss")[1].strip()
                break # 找到名字就跳出迴圈
        
        # 1. 立即使用 reply_message 回傳 Flex 卡片，省下推播額度
        flex_msg = build_confirmation_flex(boss_name)
        line_bot_api.reply_message(event.reply_token, flex_msg)
        
        # 2. 啟動防呆計時器：900秒 (15分鐘) 後執行 auto_mark_missed 檢查
        # 原程式碼註解寫 10 分鐘但數值是 900.0，此處維持 900.0 (15分鐘)
        timer = threading.Timer(900.0, auto_mark_missed, args=[group_id, boss_name])
        timer.start()
        
        return

    # 【功能 B：升級版+防重複登記】處理 Flex 卡片的按鈕回覆 (儲存至資料庫)
    if text.startswith("紀錄 "):
        # 使用 split(" ", 2) 最多只切兩刀，這樣萬一王的名字裡面有空格也不會出錯
        parts = text.split(" ", 2) 
        
        # ==========================================
        # 1. 檢查格式是否正確 (Early Return)
        # ==========================================
        if len(parts) < 3:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ 紀錄格式錯誤，請點擊卡片上的按鈕進行回報。")
            )
            return

        # ==========================================
        # 2. 提取資料
        # ==========================================
        status = parts[1]      # 例如："我方擊殺", "敵吃", 或 "漏掉"
        boss_name = parts[2]   # 例如："異界炎魔"

        # ==========================================
        # 3. 寫入資料庫與回覆 (加入防重複機制)
        # ==========================================
        try:
            conn = get_pg_conn()
            if conn:
                with conn.cursor() as cur:
                    # 💡 修正：確保資料庫使用台灣時間進行 20 分鐘內的重複檢查
                    cur.execute("""
                        SELECT status FROM fixed_boss_records2 
                        WHERE group_id = %s AND boss_name = %s 
                        AND record_time >= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Taipei' - INTERVAL '20 minutes')
                    """, (group_id, boss_name))
                    
                    existing_record = cur.fetchone()
                    
                    # 如果已經有紀錄，就阻擋寫入並回覆提示
                    if existing_record:
                        existing_status = existing_record[0]
                        
                        # 🌟 呼叫重複登記的 Flex 卡片
                        warning_flex = build_duplicate_warning_flex(boss_name, existing_status)
                        line_bot_api.reply_message(
                            event.reply_token,
                            warning_flex
                        )
                        return
                    
                    # 如果沒有紀錄，才正式寫入資料庫
                    cur.execute(
                        "INSERT INTO fixed_boss_records2 (group_id, boss_name, status) VALUES (%s, %s, %s)",
                        (group_id, boss_name, status)
                    )
                conn.commit()
                conn.close()
                
                # 🌟 寫入成功後，發送成功 Flex 卡片
                success_flex = build_record_success_flex(boss_name, status)
                line_bot_api.reply_message(
                    event.reply_token,
                    success_flex
                )
            else:
                print("❌ 寫入失敗: 無法取得資料庫連線")

        except Exception as e:
            print(f"❌ 寫入 fixed_boss_records2 失敗: {e}")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"❌ 紀錄寫入失敗，請檢查後台！錯誤訊息: {e}")
            )

        return
#   【功能 C：送出報表 + 12:00 靜默刪除版】
    if text == "固定王統計":
        try:
            conn = get_pg_conn()
            if conn:
                with conn.cursor() as cur:
                    # ==========================================
                    # 1. 撈取統計數據 (防彈版：明確指定從 UTC 轉為台灣時間)
                    # ==========================================
                    cur.execute("""
                        SELECT 
                            status, 
                            COUNT(*),
                            ARRAY_AGG(
                                TO_CHAR(
                                    record_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Taipei', 
                                    'MM/DD HH24:MI'
                                ) || ' (' || boss_name || ')'
                            )
                        FROM fixed_boss_records2
                        WHERE group_id = %s
                        GROUP BY status
                    """, (group_id,))
                    rows = cur.fetchall()

                # 整理報表資料
                stats_dict = {"我方擊殺": 0, "敵人吃": 0, "漏掉": 0}
                details = {"敵人吃": [], "漏掉": []}
                
                for row in rows:
                    status_name = row[0]
                    count = row[1]
                    time_list = row[2]
                    stats_dict[status_name] = count
                    if status_name in details and time_list:
                        details[status_name] = time_list

                total = sum(stats_dict.values())
                
                # ==========================================
                # 2. 照常送出 Flex 統計報表
                # ==========================================
                stats_flex = build_stats_report_flex(total, stats_dict, details)
                line_bot_api.reply_message(event.reply_token, stats_flex)
                
                # ==========================================
                # 3. 靜默刪除：報表送出後，如果是台灣時間 12:00 就清空資料
                # ==========================================
                # 確保你程式碼最上方有設定 TZ = pytz.timezone("Asia/Taipei")
                current_dt = datetime.now(TZ) 
                current_time = current_dt.strftime("%H:%M")
                
                if current_time == "12:00":
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM fixed_boss_records2 WHERE group_id = %s", (group_id,))
                    conn.commit()
                    print(f"🕛 台灣時間 12:00 靜默清空群組 {group_id} 的紀錄完成。")

                conn.close()
            else:
                print("❌ 統計失敗: 無法取得資料庫連線")
                
        except Exception as e:
            print(f"❌ 讀取統計資料或刪除時發生錯誤: {e}")
            try:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="❌ 讀取統計資料失敗，請稍後再試。")
                )
            except:
                pass 
                
        return
#-------------------------------------------------------------訂閱制---------------------------------------
    group_id = getattr(event.source, 'group_id', event.source.user_id)
    msg_text = event.message.text.strip()

    # --- 訂閱權限攔截開始 ---
    is_allowed, expiry, status_text = check_subscription(group_id)
    
    if not is_allowed:
        # 如果過期，只允許查詢 ID 或 狀態，其餘全部擋掉
        if msg_text not in ["ID", "狀態", "id"]:
            expiry_str = expiry.strftime('%Y-%m-%d %H:%M')
            flex_msg = build_subscription_flex(status_text, expiry_str)
            line_bot_api.reply_message(event.reply_token, flex_msg)
            return # 直接結束，不往下執行原本的功能
        
    if msg_text == "狀態":
        is_allowed, expiry, status_text = check_subscription(group_id)
        remain = expiry - now_tw()
        days = max(0, remain.days)
        
        # 使用新定義的函式取得 Flex 內容
        status_flex_content = get_status_flex(
            status_text=status_text,
            expiry_date=expiry.strftime('%Y-%m-%d'),
            days_left=days
        )
        
        line_bot_api.reply_message(
            event.reply_token, 
            FlexSendMessage(alt_text=f"權限狀態：{status_text}", contents=status_flex_content)
        )
        return
    #-------------------------------------------------------------交班  ---------------------------------------
    keywords = ["交班", "交接", "換人", "換手"]
    pattern = rf"^(@All)?({'|'.join(keywords)})$"

    # 使用 re.search 或 re.match 來判定
    if re.search(pattern, msg_text_no_space):
        with get_pg_conn() as conn:
            with conn.cursor() as cur:
                # 1. 抓取目前「下一班」是誰
                cur.execute("SELECT next_user_id FROM shift_info WHERE group_id = %s", (group_id,))
                row = cur.fetchone()
            
                # 邏輯：原本預約接班的人 (next) 變成 現在當班 (current)
                new_current = row[0] if row and row[0] else None
            
                # 2. 更新資料庫：將原本的 next 轉為 current，並將 next 清空
                cur.execute("""
                    INSERT INTO shift_info (group_id, current_user_id, next_user_id)
                    VALUES (%s, %s, NULL)
                    ON CONFLICT (group_id) DO UPDATE SET 
                        current_user_id = EXCLUDED.current_user_id,
                        next_user_id = NULL,
                        updated_at = NOW()
                """, (group_id, new_current))
                conn.commit()
            
                # 3. 顯示狀態卡片
                # 如果 new_current 是 None，代表原本沒人預約接班，可以考慮在 build_shift_status_flex 處理顯示邏輯
                flex = build_shift_status_flex(group_id, new_current, None)
                line_bot_api.reply_message(event.reply_token, flex)
                return

    elif raw_text == "接班":
        user_name = get_username(user_id)
        with get_pg_conn() as conn:
            with conn.cursor() as cur:
                # 這裡我們使用 EXCLUDED 來引用想要插入的值，避免重複傳入參數
                cur.execute("""
                    INSERT INTO shift_info (group_id, next_user_id)
                    VALUES (%s, %s)
                    ON CONFLICT (group_id) DO UPDATE SET 
                        next_user_id = EXCLUDED.next_user_id,
                        updated_at = NOW();
                """, (group_id, user_id)) # 這裡只需要 2 個參數，對應上面的 2 個 %s
                conn.commit()
                
                flex = build_shift_success_flex(user_name)
                line_bot_api.reply_message(event.reply_token, flex)
                return
            
    #-------------------------------------------------------------輪空登記 未完成 判斷重生30分鐘內輸入空才有效---------------------------------------
    # 🌟 關鍵：確保 group_id 是透過轉換函式取得的共用 ID
    group_id = get_source_id(event) 
    user_id = event.source.user_id

    msg_text = event.message.text.strip()
    parts = msg_text.split()

    # 2. 判斷是否為輪空指令 (例如：四色 空)
    if len(parts) >= 2 and ("空" in parts[1] or "輪空" in parts[1]):
        boss_input = parts[0]
        note = parts[1]
        
        # 轉換王名別名
        boss_name = None
        for real_name, aliases in alias_map.items():
            if boss_input == real_name or boss_input in aliases:
                boss_name = real_name
                break
        
        if boss_name:
            # 🌟 這裡帶入的 group_id 就會是共用 ID 了！
            handle_boss_skipped(event, group_id, boss_name, user_id, note)
            return
        else:
            return

    #-------------------------------------------------------------刪除單一王---------------------------------------
    if msg_text.startswith("刪 "):
        name_input = msg_text[2:].strip()
        if name_input:
            # 🌟 關鍵修改：確保這裡使用的是經過轉換的共用 ID
            shared_group_id = get_source_id(event)
            
            # 傳入轉換後的 shared_group_id，不論在 A 群或 B 群執行，都會刪除共用資料庫的紀錄
            success, final_name = delete_boss_records_by_alias(shared_group_id, name_input)
            
            # 使用新定義的函式取得 Flex 內容
            delete_flex_content = get_delete_result_flex(
                success=success, 
                name_input=name_input, 
                final_name=final_name
            )
            
            # 準備 alt_text
            alt_text = f"🗑 清除成功：{final_name}" if success else "❌ 清除失敗"
            
            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text=alt_text, contents=delete_flex_content)
            )
            return
    
    #-------------------------------------------------------------競標---------------------------------------
    # 1. 發起：例如打「掉落 紅布」
    if text.startswith("掉落 "):
        item_name = text.replace("掉落 ", "").strip()
        active_auctions[group_id] = {
            "item": item_name,
            "bid": 0,
            "bidder_name": None,
            "bidder_id": None
        }
        flex = build_auction_flex(item_name, 0, None)
        line_bot_api.reply_message(event.reply_token, flex)

    # 2. 下標：例如打「下標 1000」
    elif text.startswith("下標 "):
        if group_id in active_auctions:
            try:
                # 1. 解析出價金額
                new_bid = int(text.replace("下標 ", "").strip())
                current = active_auctions[group_id]
                current_bid = current["bid"]

                # 2. 判斷出價是否高於目前價格
                if new_bid > current_bid:
                    # --- 出價成功 ---
                    current_user_name = get_username(user_id)
                    active_auctions[group_id].update({
                        "bid": new_bid,
                        "bidder_name": current_user_name,
                        "bidder_id": user_id
                    })

                    # 呼叫函數取得 bubble 字典
                    bubble_dict = build_auction_flex(current["item"], new_bid, current_user_name)                
                    # 發送 Flex Message
                    line_bot_api.reply_message(
                        event.reply_token,
                        FlexSendMessage(
                            alt_text=f"🔨 出價更新：{new_bid} 鑽",
                            contents=FlexContainer.new_from_json_dict(bubble_dict) # 加上這行轉換
                        )
                    )
                else:
                    # --- 出價失敗 (使用我們之前的失敗卡片模板) ---
                    error_bid_flex = {
                        "type": "bubble",
                        "size": "mega",
                        "body": {
                            "type": "box", "layout": "vertical", "spacing": "md",
                            "contents": [
                                {"type": "text", "text": "❌ 出價無效", "weight": "bold", "color": "#E74C3C", "size": "md"},
                                {"type": "text", "text": f"出價需高於目前的最高標。", "size": "sm", "color": "#666666"},
                                {
                                    "type": "box", "layout": "vertical", "margin": "md", "backgroundColor": "#FEF5E7", "paddingAll": "md", "cornerRadius": "sm",
                                    "contents": [
                                        {"type": "text", "text": f"目前最高：{current_bid} 💎", "size": "sm", "color": "#D68910", "weight": "bold", "align": "center"}
                                    ]
                                },
                                {"type": "text", "text": f"💡 建議出價：{current_bid + 1} 鑽以上", "size": "xs", "color": "#aaaaaa", "align": "center"}
                            ]
                        }
                    }
                    line_bot_api.reply_message(
                        event.reply_token,
                        FlexSendMessage(alt_text="❌ 出價無效", contents=error_bid_flex)
                    )

            except ValueError:
                # 如果輸入不是數字，靜默處理或回覆提示
                pass

    # 3. 結標：直接打「結標」
    elif text == "結標":
        if group_id in active_auctions:
            # 取出資料並從暫存移除
            res = active_auctions.pop(group_id)
            
            if res["bidder_name"]:
                msg = (f"🎊 競標結束！\n\n"
                       f"📦 物品：{res['item']}\n"
                       f"👤 得標者：{res['bidder_name']}\n"
                       f"💰 金額：{res['bid']} 鑽\n\n"
                       f"恭喜得標！請雙方進行交易。")
            else:
                msg = f"已取消【{res['item']}】的競標（無人下標）。"
            
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

    #-------------------------------------------------------------加入名冊---------------------------------------
    db.setdefault("__ROSTER_WAIT__", {})
    if msg.startswith("加入名冊"):
        parts = msg.split(" ", 2)
        if len(parts) < 3:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("❌ 用法：加入名冊 血盟名 遊戲名")
            )
            return
        _, clan, game_name = parts
        # === 已存在 → 詢問是否更新 ===
        exists = roster_get_by_user(user)  # 先拿到資料
        if exists:
            old_game, old_clan, _ = exists
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
        line_name = get_line_display_name(user)
        roster_insert(user, game_name, clan, line_name)
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
        # 1. 取得等待中的狀態
        roster_sessions = db.get("__ROSTER_WAIT__", {})
        session = roster_sessions.get(user)

        # 2. 驗證狀態
        if not session or session.get("action") != "update":
            # 如果找不到 session，可以回一個簡單的錯誤
            return

        # 3. 執行更新
        new_name = session["name"]
        new_clan = session["clan"]
        roster_update(user, new_name, new_clan)

        # 4. 清理快取並存檔
        roster_sessions.pop(user, None)
        db["__ROSTER_WAIT__"] = roster_sessions
        save_db(db)

        # 5. 構建成功 Flex Message 卡片
        success_flex = {
            "type": "bubble",
            "size": "mega",
            "header": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "✅ 更新成功", "weight": "bold", "color": "#1DB446", "size": "lg"}
                ],
                "paddingBottom": "none"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {"type": "text", "text": "您的名冊資訊已同步更新完成。", "size": "sm", "color": "#8c8c8c"},
                    {"type": "separator", "margin": "lg"},
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "lg",
                        "spacing": "sm",
                        "contents": [
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "🛡️ 血盟", "size": "sm", "color": "#aaaaaa", "flex": 2},
                                    {"type": "text", "text": f"{new_clan}", "size": "sm", "color": "#111111", "flex": 4, "align": "end", "weight": "bold"}
                                ]
                            },
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "👤 名字", "size": "sm", "color": "#aaaaaa", "flex": 2},
                                    {"type": "text", "text": f"{new_name}", "size": "sm", "color": "#111111", "flex": 4, "align": "end", "weight": "bold"}
                                ]
                            }
                        ]
                    }
                ]
            }
        }

        # 發送卡片
        line_bot_api.reply_message(
            event.reply_token,
            FlexSendMessage(alt_text="✅ 名冊更新成功", contents=success_flex)
        )
        return
    
    #-------------------------------------------------------------查自己名冊---------------------------------------
    if msg == "查自己":
        profile = get_roster_profile(user)
        if not profile:
            no_profile_flex = {
                "type": "bubble",
                "size": "mega",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": "查無個人名冊", "weight": "bold", "color": "#E74C3C", "size": "lg"}
                    ],
                    "paddingBottom": "none"
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "md",
                    "contents": [
                        {
                            "type": "text", 
                            "text": "系統目前找不到您的登記資訊。請先完成加入名冊！", 
                            "wrap": True, 
                            "size": "sm", 
                            "color": "#666666"
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "margin": "lg",
                            "backgroundColor": "#F8F9FA",
                            "paddingAll": "md",
                            "cornerRadius": "sm",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "💡 加入指令：",
                                    "size": "xs",
                                    "color": "#8c8c8c",
                                    "weight": "bold"
                                },
                                {
                                    "type": "text",
                                    "text": "加入名冊 [血盟] [遊戲名字]",
                                    "size": "sm",
                                    "color": "#34495E",
                                    "margin": "sm"
                                }
                            ]
                        }
                    ]
                },
            }

            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text="❌ 尚未加入名冊", contents=no_profile_flex)
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
    #-------------------------------------------------------------刪除 名冊---------------------------------------
    if msg == "刪除名冊":
        profile = get_roster_profile(user)
        if not profile:
            no_profile_flex = {
                "type": "bubble",
                "size": "mega",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": "查無個人名冊", "weight": "bold", "color": "#E74C3C", "size": "lg"}
                    ],
                    "paddingBottom": "none"
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "md",
                    "contents": [
                        {
                            "type": "text", 
                            "text": "系統目前找不到您的登記資訊。請先完成加入名冊！", 
                            "wrap": True, 
                            "size": "sm", 
                            "color": "#666666"
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "margin": "lg",
                            "backgroundColor": "#F8F9FA",
                            "paddingAll": "md",
                            "cornerRadius": "sm",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "💡 加入指令：",
                                    "size": "xs",
                                    "color": "#8c8c8c",
                                    "weight": "bold"
                                },
                                {
                                    "type": "text",
                                    "text": "加入名冊 [血盟] [遊戲名字]",
                                    "size": "sm",
                                    "color": "#34495E",
                                    "margin": "sm"
                                }
                            ]
                        }
                    ]
                },
            }

            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text="❌ 尚未加入名冊", contents=no_profile_flex)
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
    if msg == "取消":
        if user in db.get("__ROSTER_WAIT__", {}):
            db["__ROSTER_WAIT__"].pop(user)
            save_db(db)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("❎ 已取消操作")
            )
            return
    #-------------------------------------------------------------查名冊 (未完成) 用LINE名稱查 去掉@抓後面字---------------------------------------
    if text.startswith("查名冊"):
        parts = text.split(maxsplit=1)

        # 只有輸入「查名冊」
        if len(parts) == 1:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text="用法：查名冊 關鍵字\n例如：查名冊 威士忌"
                )
            )
            return

        keyword = parts[1].strip()

        with db_lock:
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor()
            cur.execute("""
                SELECT game_name, line_name, clan_name
                FROM roster
                WHERE game_name ILIKE %s
                ORDER BY game_name
                LIMIT 10
            """, (f"%{keyword}%",))
            rows = cur.fetchall()
            conn.close()

        if not rows:
            reply = TextSendMessage(text="❌ 查無符合的名冊資料")
        else:
            reply = FlexSendMessage(
                alt_text="名冊查詢結果",
                contents=build_roster_flex(rows)
            )

        line_bot_api.reply_message(event.reply_token, reply)
        return
    #-------------------------------------------------------------王簡稱---------------------------------------
    if msg == "王列表":
        text = build_boss_list_text()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text)
        )
        return
    #-------------------------------------------------------------王CD表---------------------------------------
    if msg == "王重生":
        text = build_boss_cd_list_text()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text)
        )
        return
    #-------------------------------------------------------------!!!!!!! 未完成 全部名冊!!!!!!!!---------------------------------------
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
  #-------------------------------------------------------------修正紀錄開機時間---------------------------------------
    if msg.startswith("開機 "):
        parts = msg.split(" ", 1)
        if len(parts) < 2: return
        
        time_token = parts[1].strip()
        base_time = parse_time(time_token)
        
        if not base_time:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("❌ 時間格式錯誤，請使用 HHMM 或 HHMMSS")
            )
            return
            
        # 取得 group_id
        # 取得群組 ID 並撈取資料庫紀錄
        # 🌟 這裡改成呼叫 get_source_id 來取得共用的群組 ID
        group_id = get_source_id(event)
        boss_db_from_pg = get_latest_boss_records(group_id)
        
        # 💡 修正：補上 cd_map 參數，讓函式知道各王的冷卻時間
        # (假設 cd_map 在你的程式碼中已經定義在全域，或者是可以取得的變數)
        init_cd_boss_with_given_time(group_id, base_time, user, cd_map)
        
        # 取得 Flex 字典內容並回覆
        flex_contents = build_boot_init_flex(base_time.strftime('%H:%M'))
        line_bot_api.reply_message(
            event.reply_token,
            FlexSendMessage(
                alt_text=f"🔌 開機時間已紀錄：{base_time.strftime('%H:%M')}",
                contents=flex_contents  
            )
        )
        return

    #-------------------------------------------------------------清除所有登記紀錄---------------------------------------
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
        try:
            # 權限檢查
            wait = db.get("__WAIT__", {}).get(group_id)
            if not wait or wait["user"] != user:
                return

            now = now_tw()
            start, end = get_kpi_range(now)
            
            # 1. 抓取格式正確的資料 (內含 date, kill, user, source)
            boss_db_for_kpi = get_all_records_for_kpi(group_id, start, end)
            
            # 2. 呼叫你的 calculate_kpi 進行統計
            kpi_data = calculate_kpi(boss_db_for_kpi, start, end)

            # 3. 執行物理刪除 (PostgreSQL)
            # 務必確認 delete_all_boss_records 函式內有 conn.commit()
            delete_all_boss_records(group_id)
            
            # 4. 清除本地 JSON 內的紀錄 (避免王表指令抓到舊資料)
            if "boss_db" in db and group_id in db["boss_db"]:
                db["boss_db"][group_id] = {}
            
            # 5. 清除等待狀態與存檔
            db.get("__WAIT__", {}).pop(group_id, None)
            save_db(db)

            # 6. 回覆 KPI 圖卡 (因為現在 is_peak_time 回傳 False，一定會出圖卡)
            if kpi_data:
                ranking = sorted(kpi_data.items(), key=lambda x: x[1], reverse=True)
                display = [(get_username(uid), count) for uid, count in ranking]
                period_text = f"{start.strftime('%m/%d %H:%M')} ～ {end.strftime('%m/%d %H:%M')}"
                
                bubble = build_kpi_flex("📊 本週 KPI 結算", period_text, display)
                line_bot_api.reply_message(
                    event.reply_token,
                    [
                        FlexSendMessage(alt_text="KPI 結算", contents=bubble),
                        TextSendMessage("🗑️ 資料已完全清空，KPI 結算完畢。")
                    ]
                )
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("🗑️ 資料已清空 (本週無符合條件之 KPI 紀錄)。")
                )
        except Exception as e:
            import traceback
            print(traceback.format_exc()) # 後台印出詳細報錯位置
            line_bot_api.reply_message(event.reply_token, TextSendMessage(f"⚠️ 清除失敗：{str(e)}"))
        return

    if msg == "取消清除":
        db.get("__WAIT__", {}).pop(group_id, None)
        save_db(db)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("❎ 已取消清除")
        )
        return
   #-------------------------------------------------------------!!!!!!!查詢王!!!!!!!---------------------------------------
    # 在 @handler.add(MessageEvent, TextMessage) 邏輯中
    # 這是你的訊息處理核心
    if text.startswith("查 "):
        try:
            input_name = text.replace("查 ", "").strip()
            
            # 1. 進行轉換
            real_name = get_real_boss_name(input_name)
            print(f"DEBUG: 使用者輸入 '{input_name}'，轉換為 '{real_name}'") # 查看轉換是否正確
            
            # 2. 查詢資料庫
            group_id = event.source.group_id if hasattr(event.source, 'group_id') else event.source.user_id
            history = get_boss_history(group_id, real_name)
            
            print(f"DEBUG: 查到的歷史紀錄數量: {len(history)}") # 查看是否真的有資料
            
            # 3. 建立並回覆
            if not history:
                line_bot_api.reply_message(
                    event.reply_token, 
                    TextSendMessage(text=f"找不到「{real_name}」的紀錄，請確認名稱是否正確。")
                )
            else:
                flex_msg = build_boss_history_flex(real_name, history)
                line_bot_api.reply_message(event.reply_token, flex_msg)
                
        except Exception as e:
            print(f"查詢錯誤: {e}")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="查詢過程發生錯誤，請稍後再試。"))





    # 在 handle_message 內判斷指令
    if msg_text.startswith("撤"):
        boss_name_input = msg_text[1:].strip()
        
        # 💡 檢查輸入是否只有「撤」一個字
        if not boss_name_input:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入要撤銷的王名，例如：撤 克特"))
            return

        success, result_obj = undo_last_boss_record(group_id, boss_name_input)
        
        # 💡 最終防線：確認 result_obj 存在
        if result_obj:
            line_bot_api.reply_message(event.reply_token, result_obj)
        else:
            print("錯誤：result_obj 為空")
    #-------------------------------------------------------------KPI---------------------------------------
    if msg.upper() == "KPI":
        now = now_tw()
        start, end = get_kpi_range(now)
        group_id = get_source_id(event)
        
        # 使用現有的 get_kpi_ranking 函式獲取資料
        # 注意：原本檔案內的 get_kpi_ranking 內部已經會呼叫 get_kpi_range
        period_text, ranking = get_kpi_ranking(group_id)
        
        if not ranking:
            no_data_flex = {
                "type": "bubble",
                "size": "mega",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "md",
                    "contents": [
                        {
                            "type": "text",
                            "text": "📊 KPI 統計報表",
                            "weight": "bold",
                            "color": "#111111",
                            "size": "md"
                        },
                        {
                            "type": "separator",
                            "margin": "md"
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "margin": "lg",
                            "spacing": "sm",
                            "alignItems": "center",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "∅",
                                    "size": "xxl",
                                    "color": "#cccccc",
                                    "weight": "bold"
                                },
                                {
                                    "type": "text",
                                    "text": "目前尚無相關紀錄",
                                    "size": "sm",
                                    "color": "#aaaaaa"
                                }
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "margin": "md",
                            "contents": [
                                {
                                    "type": "text",
                                    "text": "查詢區間",
                                    "size": "xs",
                                    "color": "#bbbbbb",
                                    "flex": 0
                                },
                                {
                                    "type": "text",
                                    "text": f"{period_text}",
                                    "size": "xs",
                                    "color": "#bbbbbb",
                                    "align": "end"
                                }
                            ]
                        }
                    ]
                }
            }

            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text=f"📊 {period_text} 尚無 KPI 紀錄", contents=no_data_flex)
            )
            return

        # 這裡的 period_text 是由 get_kpi_ranking 產生的 "02/05 ~ 02/12"
        # 如果你想顯示具體小時，可以在 build_kpi_flex 前重新定義它：
        detailed_period = f"{start.strftime('%m/%d %H:%M')} ～ {end.strftime('%m/%d %H:%M')}"

        bubble = build_kpi_flex("本週 KPI 排行榜", detailed_period, ranking)
        line_bot_api.reply_message(
            event.reply_token,
            FlexSendMessage(alt_text="本週 KPI 排行榜", contents=bubble)
        )
        return

    #-------------------------------------------------------------重生列表---------------------------------------
    # 將訊息轉換為小寫，避免使用者不小心打成大寫 (例如 TJ) 而無法辨識
    msg_lower = msg.lower()

    # 檢查訊息是否為「出出」或其對應的英文錯字「tjtj」
    is_force_full = (msg_lower in ("出出", "tjtj"))

    # 將「tj」與「tjtj」加入主要的觸發條件中
    if msg_lower in ("出", "出出", "tj", "tjtj"):
        now = now_tw()
        time_items = []
        unregistered = []
        
        # 取得群組 ID 並撈取資料庫紀錄
        # 取得群組 ID 並撈取資料庫紀錄
        # 🌟 這裡改成呼叫 get_source_id 來取得共用的群組 ID
        group_id = get_source_id(event)
        boss_db_from_pg = get_latest_boss_records(group_id)

        for boss, cd in cd_map.items():
            if boss not in boss_db_from_pg or not boss_db_from_pg[boss]:
                unregistered.append(boss)
                continue
            
            rec = boss_db_from_pg[boss][-1]
            base_respawn = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)
            step = timedelta(hours=cd)
            
            if now < base_respawn:
                display_time = base_respawn
                passed_minutes = None
                missed = 0
            else:
                diff = now - base_respawn
                rounds_passed = int(diff.total_seconds() // step.total_seconds())
                current_respawn = base_respawn + rounds_passed * step
                passed_minutes = int((now - current_respawn).total_seconds() // 60)
                
                if passed_minutes <= 30:
                    display_time = current_respawn
                    missed = rounds_passed           
                else:
                    display_time = current_respawn + step
                    missed = rounds_passed + 1
                    passed_minutes = None
            
            note = rec.get("note", "").strip()
            line = f"{display_time.strftime('%H:%M:%S')} {boss}"

            # --- 修改開始 ---
            # 1. 先格式化原始時間字串
            time_str = display_time.strftime('%H:%M:%S')
            
            # 2. 在冒號後方插入零寬空格 (Zero Width Space)，阻斷 LINE 的連結偵測
            # 我們針對第一個冒號處理即可，或者全部替換
            safe_time_str = time_str.replace(":", ":\u200b")
            
            # 3. 使用處理過的時間組合成 line
            line = f"{safe_time_str} {boss}"
            # --- 修改結束 ---

            if note:
                line += f"（{note}）"
            if passed_minutes is not None and passed_minutes <= 30:
                line += f" <{passed_minutes}分未打>"
            if missed > 0:
                line += f" #過{missed}"
            time_items.append((display_time, line))

        time_items.sort(key=lambda x: x[0])
        
        if is_force_full:
            display_items = time_items
            output = ["📢【即將重生列表｜完整】", ""]
        elif is_peak_time():
            display_items = time_items[:14]
            output = ["📢【即將重生列表｜熱門】", ""]
        else:
            display_items = time_items
            output = ["📢【即將重生列表】", ""]

        for _, line in display_items:
            output.append(line)

        if is_peak_time() and not is_force_full:
            output.append("")
            output.append("👉 輸入「出出」可查看完整列表")

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

    #-------------------------------------------------------------帶擊殺按鈕的王列表---------------------------------------
    if msg == "打王":
        now = now_tw()
        time_items = []
        
        # 取得群組 ID 並撈取資料庫紀錄
        # 🌟 這裡改成呼叫 get_source_id 來取得共用的群組 ID
        group_id = get_source_id(event)
        boss_db_from_pg = get_latest_boss_records(group_id)
        boss_db_from_pg = get_latest_boss_records(group_id) 

        for boss, cd in cd_map.items():
            # 若沒登記則跳過，不顯示在「打出」列表中
            if boss not in boss_db_from_pg or not boss_db_from_pg[boss]:
                continue
            
            rec = boss_db_from_pg[boss][-1]
            base_respawn = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)
            step = timedelta(hours=cd)
            
            if now < base_respawn:
                display_time = base_respawn
                passed_minutes = None
                missed = 0
            else:
                diff = now - base_respawn
                rounds_passed = int(diff.total_seconds() // step.total_seconds())
                current_respawn = base_respawn + rounds_passed * step
                passed_minutes = int((now - current_respawn).total_seconds() // 60)
                
                if passed_minutes <= 30:
                    display_time = current_respawn
                    missed = rounds_passed           
                else:
                    display_time = current_respawn + step
                    missed = rounds_passed + 1
                    passed_minutes = None
            
            note = rec.get("note", "").strip()
            line = f"{display_time.strftime('%H:%M:%S')} {boss}"
            if note: line += f"（{note}）"
            if passed_minutes is not None and passed_minutes <= 30:
                line += f" <{passed_minutes}分未打>"
            if missed > 0: line += f" #過{missed}"
            
            time_items.append((display_time, line))

        # 排序並切片：僅取前 15 隻
        time_items.sort(key=lambda x: x[0])
        display_items = time_items[:15] 
        
        title = f"⚔️ 快速擊殺列表 (近 {len(display_items)} 隻)"
        
        # 呼叫 Flex 函式 (不帶 unregistered 參數)
        flex_msg = build_kill_list_flex(title, display_items)
        
        line_bot_api.reply_message(event.reply_token, flex_msg)
        return


    # 請將此段邏輯加入您的訊息處理器中
    if event.message.text == "查詢代碼":
        group_id = event.source.group_id
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"本群組的聊天室代碼為：\n{group_id}")
        )
    #-------------------------------------------------------------登記王---------------------------------------
    restored_kpi = {}
    skip_kpi = False
    for line in lines:
        raw_line = line.strip()
        if not raw_line: continue

        if raw_line == "__KPI_START__":
            skip_kpi = True
            continue
        if raw_line == "__KPI_END__":
            skip_kpi = False
            if restored_kpi:
                db.setdefault("kpi_backup", {})[now_tw().strftime("%Y-%m-%d")] = restored_kpi
                save_db(db)
            continue
        if skip_kpi:
            # ... (此處保留解析 restored_kpi 的邏輯) ...
            continue

        clean_line = sanitize_register_line(raw_line)
        if not clean_line: continue

        parts = clean_line.split()
        if len(parts) < 2:
            failed_lines.append(raw_line)
            continue

        time_token = parts[0]
        boss_name = parts[1]
        note = " ".join(parts[2:]) if len(parts) > 2 else ""

        if time_token in ["6", "6666"] or time_token.upper() == "K":
            t = now_tw()
        else:
            t = parse_time(time_token)
            
        if not t:
            failed_lines.append(raw_line)
            continue

        boss = get_boss(boss_name)
        if not boss:
            failed_lines.append(raw_line)
            continue

        cd = cd_map.get(boss)
        if cd is None: continue

        # 3. 寫入資料庫 (完全取代 boss_db 操作)
        respawn = t + timedelta(hours=cd)
        save_boss_to_pg(
            group_id=group_id,
            boss_name=boss,
            kill_time=t,
            respawn_time=respawn,
            user_id=user,
            note=note,
            source="backup" if is_backup_mode else "manual"
        )
        success_count += 1

        # 4. 回應邏輯
        if not is_backup_mode:
            registrar = get_username(user)
            kill_str = t.strftime("%H:%M:%S")
            resp_str = respawn.strftime('%H:%M:%S')
            
            # 🌟 1. 抓取真實來源 ID (注意不要用到已經被狸貓換太子的 group_id)
            real_group_id = getattr(event.source, 'group_id', None)
            
            # 🌟 2. 判斷是否為 B 群 (請填入 B 群真實的 ID)
            B_GROUP_ID = "Cfea8c07f23c410a1e328871f8573f5e5" 
            is_b = (real_group_id == B_GROUP_ID)
            
            text_msg = build_register_boss_text(boss, kill_str, resp_str, registrar, note)
            
            # 🌟 3. 將 is_b_group 參數傳遞給 Flex 函式
            flex_msg = build_register_boss_flex(
                boss=boss, 
                kill_time=kill_str, 
                respawn_time=resp_str, 
                registrar=registrar, 
                note=note, 
                is_skip=False, # 視你的原本邏輯而定
                is_b_group=is_b # 傳入判斷結果
            )
            
            safe_reply(event, text_msg, flex_msg)

    # 5. 迴圈結束後的回覆 (不再需要針對 boss_db 做 save_db)
    if is_backup_mode:
        summary_msg = f"📦 備份登記完成：成功 {success_count} 隻"
        if failed_lines:
            summary_msg += f"\n⚠️ 失敗 {len(failed_lines)} 行"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(summary_msg))
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
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL 未設定")
