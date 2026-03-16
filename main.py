# 天堂M 吃王小幫手

import os, json, time, asyncio, threading, requests, pytz, psycopg2
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from threading import Lock
from fastapi import FastAPI, Request, Header
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    JoinEvent,  
    MemberJoinedEvent, MessageEvent, TextMessage, TextSendMessage, FlexSendMessage
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
# 工具函式
def is_peak_time():
    return False # 暫時關閉，永遠允許 Flex 訊息

    #h = now_tw().hour
    #return 19 <= h <= 23
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
    if event.source.type == "group":
        return event.source.group_id
    elif event.source.type == "room":
        return event.source.room_id
    else:
        return event.source.user_id
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
        return conn
    except Exception as e:
        print(f"Database connection failed: {e}")
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

def init_cd_boss_with_given_time(group_id, base_time, user_id):
    """
    開機初始化：只針對『目前沒紀錄』的王補上開機時間。
    """
    conn = get_pg_conn()
    if not conn: return
    
    try:
        cur = conn.cursor()
        
        # 1. 先抓出目前該群組資料庫中所有王最新的紀錄清單
        # 使用 DISTINCT ON 確保每隻王只會出現一筆最新的
        cur.execute("""
            SELECT boss_name 
            FROM boss_time 
            WHERE group_id = %s
        """, (group_id,))
        
        # 取得所有已經有紀錄的王名集合
        recorded_bosses = {row[0] for row in cur.fetchall()}
        
        # 2. 遍歷定義好的 cd_map，只處理不在 recorded_bosses 裡的王
        for boss, cd in cd_map.items():
            if boss in recorded_bosses:
                # 只要有紀錄（不論時間點），就跳過，不覆蓋既有的狀態
                continue
            
            # 沒紀錄的，才補上開機時間
            respawn = base_time + timedelta(hours=cd)
            insert_query = """
                INSERT INTO boss_time (group_id, boss_name, kill_time, respawn_time, user_id, note, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            cur.execute(insert_query, (group_id, boss, base_time, respawn, user_id, "伺服器開機補推", "boot"))
            
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"Error during selective boot init: {e}")
    finally:
        conn.close()

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

def build_kill_list_flex(title, display_items):
    """
    優化版：高對比度、適配黑白主題的重生列表
    """
    rows = []
    now = now_tw()

    for dt, line_text in display_items:
        parts = line_text.split(" ", 1)
        time_str = parts[0]
        boss_info = parts[1] if len(parts) > 1 else ""
        
        # 判定狀態色塊顏色
        diff = (dt - now).total_seconds()
        if diff < 0:
            bg_color = "#F44336"  # 質感紅 (已過)
            status_text = "已重生"
        elif diff < 1800:
            bg_color = "#FF9800"  # 質感橘 (30分內)
            status_text = "即將"
        else:
            bg_color = "#4CAF50"  # 質感綠 (尚未)
            status_text = "等待"

        # 取得純王名
        pure_name = boss_info.split("（")[0].split(" <")[0].split(" #")[0].strip()

        rows.append({
            "type": "box",
            "layout": "horizontal",
            "contents": [
                # 1. 時間色塊標籤
                {
                    "type": "box",
                    "layout": "vertical",
                    "flex": 3,
                    "contents": [
                        {"type": "text", "text": time_str, "size": "xs", "color": "#ffffff", "weight": "bold", "align": "center"},
                        {"type": "text", "text": status_text, "size": "xxs", "color": "#ffffff", "align": "center", "opacity": "0.8"}
                    ],
                    "backgroundColor": bg_color,
                    "cornerRadius": "sm",
                    "paddingAll": "2px"
                },
                # 2. 王名 (加大 md，加粗，使用深灰色確保黑白主題皆清楚)
                {
                    "type": "text", 
                    "text": boss_info, 
                    "size": "md", 
                    "weight": "bold", 
                    "flex": 6, 
                    "gravity": "center", 
                    "wrap": True,
                    "margin": "md",
                    "color": "#333333" # 在白色主題顯眼，深色主題也會自動適配
                },
                # 3. 擊殺按鈕 (使用高級深藍色)
                {
                    "type": "box",
                    "layout": "vertical",
                    "flex": 2,
                    "contents": [{"type": "text", "text": "擊殺", "size": "xs", "color": "#ffffff", "align": "center", "weight": "bold"}],
                    "backgroundColor": "#17a2b8", # 質感青藍色
                    "cornerRadius": "xxl", # 圓角按鈕
                    "paddingAll": "6px",
                    "action": {"type": "message", "label": "K", "text": f"6666 {pure_name}"}
                }
            ],
            "margin": "lg",
            "alignItems": "center"
        })

    bubble = {
        "type": "bubble",
        "header": {
            "type": "box", 
            "layout": "vertical", 
            "backgroundColor": "#343a40", # 深石板色標題
            "contents": [{"type": "text", "text": title, "color": "#ffffff", "weight": "bold", "size": "sm", "align": "center"}]
        },
        "body": {
            "type": "box", 
            "layout": "vertical", 
            "spacing": "none", 
            "contents": rows if rows else [{"type": "text", "text": "目前尚無重生資料", "align": "center", "color": "#aaaaaa", "size": "sm"}]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "message",
                        "label": "🔄 更新清單",
                        "text": "打王"
                    },
                    "style": "primary",
                    "color": "#343a40", # 使用與標題一致的深色系
                    "height": "sm"
                }
            ]
        },
        "styles": {
            "footer": {"separator": True}
        }
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

def build_subscription_flex(status, expiry_str):
    bubble = {
      "type": "bubble",
      "header": {
        "type": "box", "layout": "vertical", "backgroundColor": "#ff4444",
        "contents": [{"type": "text", "text": "⛔ 服務已到期", "color": "#ffffff", "weight": "bold", "size": "lg"}]
      },
      "body": {
        "type": "box", "layout": "vertical", "spacing": "md",
        "contents": [
          {"type": "text", "text": "您的群組授權已過期", "weight": "bold", "size": "md"},
          {"type": "text", "text": f"到期時間：{expiry_str}", "size": "sm", "color": "#aaaaaa"},
          {"type": "separator"},
          {"type": "text", "text": "請聯絡開發者申請續約，以繼續使用自動通知與統計功能。", "wrap": True, "size": "sm", "color": "#666666"}
        ]
      },
      "footer": {
        "type": "box", "layout": "vertical",
        "contents": [
          {"type": "button", "style": "primary", "color": "#ff4444", 
           "action": {"type": "uri", "label": "聯絡開發者續費", "uri": "https://line.me/ti/p/您的LINE_ID"}}
        ]
      }
    }
    return FlexSendMessage(alt_text="服務到期通知", contents=bubble)

def build_register_boss_flex(boss, kill_time, respawn_time, registrar, note=None, is_skip=False):
    map_list = BOSS_MAP.get(boss, [])
    map_text = "、".join(map_list) if map_list else "未知"

    # 根據是否輪空設定顯示文字與顏色
    header_prefix = "⭕ 輪空登記 " if is_skip else "🔥 已登記 "
    boss_color = "#A020F0" if is_skip else "#FF6D18"  # 輪空用紫色，正常用橘紅
    time_label = "🕒 輪空：" if is_skip else "🕒 死亡："

    contents = [
        # ===== 標題 =====
        {
            "type": "text",
            "text": header_prefix,
            "weight": "bold",
            "size": "lg",
            "contents": [
                {
                    "type": "span",
                    "text": header_prefix
                },
                {
                    "type": "span",
                    "text": boss,
                    "color": boss_color,
                    "weight": "bold"
                }
            ]
        },
        {
            "type": "separator",
            "margin": "md"
        },

        # ===== 資訊列：地圖 =====
        {
            "type": "box",
            "layout": "baseline",
            "contents": [
                {
                    "type": "text",
                    "text": "🗺️ 地圖：",
                    "size": "sm",
                    "color": "#888888",
                    "flex": 2
                },
                {
                    "type": "text",
                    "text": map_text,
                    "wrap": True,
                    "flex": 6
                }
            ]
        },
        # ===== 資訊列：時間 (死亡/基準) =====
        {
            "type": "box",
            "layout": "baseline",
            "contents": [
                {
                    "type": "text",
                    "text": time_label,
                    "size": "sm",
                    "color": "#888888",
                    "flex": 2
                },
                {
                    "type": "text",
                    "text": kill_time,
                    "wrap": True,
                    "flex": 6
                }
            ]
        },
        # ===== 資訊列：重生 =====
        {
            "type": "box",
            "layout": "baseline",
            "contents": [
                {
                    "type": "text",
                    "text": "✨ 重生：",
                    "size": "sm",
                    "color": "#888888",
                    "flex": 2
                },
                {
                    "type": "text",
                    "text": respawn_time,
                    "wrap": True,
                    "flex": 6
                }
            ]
        }
    ]

    # ===== 備註 =====
    if note:
        contents.append({
            "type": "box",
            "layout": "baseline",
            "contents": [
                {
                    "type": "text",
                    "text": "📌 備註：",
                    "size": "sm",
                    "color": "#888888",
                    "flex": 2
                },
                {
                    "type": "text",
                    "text": note,
                    "wrap": True,
                    "flex": 6
                }
            ]
        })

    # ===== 登記者 =====
    contents.extend([
        {
            "type": "separator",
            "margin": "lg"
        },
        {
            "type": "text",
            "text": f"👤 登記者：{registrar}",
            "size": "xs",
            "color": "#999999",
            "wrap": True
        }
    ])

    alt_title = f"輪空登記 {boss}" if is_skip else f"已登記 {boss}"

    return FlexSendMessage(
        alt_text=alt_title,
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
def build_query_record_bubble(boss, rec):
    respawn = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)
    registrar = get_username(rec.get("user"))
    
    # 標題與基礎樣式
    contents = [
        {
            "type": "text",
            "text": f"📋 歷史紀錄｜{boss}",
            "weight": "bold",
            "size": "lg",
            "color": "#111111"
        },
        {
            "type": "separator",
            "margin": "md",
            "color": "#EEEEEE"
        }
    ]

    # 定義內部資料行模板
    def create_info_row(label, value, value_color="#333333", is_bold=False):
        return {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {"type": "text", "text": label, "size": "sm", "color": "#888888", "flex": 3},
                {"type": "text", "text": value, "size": "sm", "color": value_color, "flex": 7, "weight": "bold" if is_bold else "regular", "align": "end"}
            ]
        }

    # 資料區塊
    info_box = {
        "type": "box",
        "layout": "vertical",
        "margin": "lg",
        "spacing": "sm",
        "contents": [
            create_info_row("📅 登記日期", rec['date']),
            create_info_row("🕒 死亡時間", rec['kill']),
            # 重生時間用藍色加粗，方便一眼識別
            create_info_row("✨ 重生時間", respawn.strftime('%H:%M:%S'), value_color="#1756B7", is_bold=True),
            create_info_row("👤 登記者", registrar)
        ]
    }
    
    contents.append(info_box)

    # 備註區塊
    if rec.get("note"):
        contents.append({
            "type": "box",
            "layout": "vertical",
            "margin": "md",
            "paddingAll": "sm",
            "backgroundColor": "#FDFDFD",
            "contents": [
                {
                    "type": "text",
                    "text": f"📌 {rec['note']}",
                    "size": "xs",
                    "color": "#999999",
                    "wrap": True,
                }
            ]
        })

    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": contents,
            "paddingAll": "lg"
        }
    }
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
def build_auction_flex(item_name, highest_bid, bidder_name):
    display_bidder = bidder_name if bidder_name else "目前尚無人出價"
    
    bubble = {
        "type": "bubble",
        "header": {
            "type": "box", "layout": "vertical", "backgroundColor": "#E67E22",
            "contents": [{"type": "text", "text": "⚔️ 盟內裝備快閃競標", "weight": "bold", "color": "#FFFFFF", "size": "sm"}]
        },
        "body": {
            "type": "box", "layout": "vertical", "spacing": "md",
            "contents": [
                {"type": "text", "text": f"📦 物品：{item_name}", "weight": "bold", "size": "lg"},
                {"type": "separator"},
                {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                    {"type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": "最高標", "size": "sm", "color": "#aaaaaa", "flex": 2},
                        {"type": "text", "text": f"{highest_bid} 鑽", "size": "sm", "weight": "bold", "color": "#E67E22", "flex": 4}
                    ]},
                    {"type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": "領先者", "size": "sm", "color": "#aaaaaa", "flex": 2},
                        {"type": "text", "text": f"{display_bidder}", "size": "sm", "flex": 4}
                    ]}
                ]}
            ]
        },
        "footer": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "text", "text": "輸入「下標 金額」參與競標", "size": "xs", "color": "#aaaaaa", "align": "center"}
            ]
        }
    }
    return FlexSendMessage(alt_text=f"競標中: {item_name}", contents=bubble)
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
def build_roster_delete_confirm_flex(game_name):
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
def build_roster_deleted_flex():
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
    current_name = get_username(current_uid) if current_uid else "🔴 目前空班中"
    
    # 重點：如果沒人接班，顯示提示文字
    if not next_uid:
        next_display = "⚠️ 沒人接班 (請點擊下方)"
        next_color = "#FF0000" # 紅色警告
    else:
        next_display = get_username(next_uid)
        next_color = "#000000"

    bubble = {
        "type": "bubble",
        "header": {
            "type": "box", "layout": "vertical", "backgroundColor": "#1a237e",
            "contents": [{"type": "text", "text": "⚔️ 王表交接系統", "color": "#FFFFFF", "weight": "bold"}]
        },
        "body": {
            "type": "box", "layout": "vertical", "spacing": "md",
            "contents": [
                {"type": "text", "text": f"👤 當前：{current_name}", "weight": "bold"},
                {"type": "text", "text": f"⏭️ 接班：{next_display}", "color": next_color, "size": "sm"},
                {"type": "separator", "margin": "md"}
            ]
        },
        "footer": {
            "type": "box", "layout": "vertical",
            "contents": [
                {
                    "type": "button", "style": "primary", "color": "#2E7D32",
                    "action": {"type": "message", "label": "我要接班 🙋", "text": "接班"}
                }
            ]
        }
    }
    return FlexSendMessage(alt_text="交接班狀態確認", contents=bubble)

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
    "巨大蜈蚣": ["巨大蜈蚣", "蜈蚣", "海4", "海蟲", "6"],
    "86左飛龍": ["左飛龍", "861", "86左飛龍", "左", "86下"],
    "86右飛龍": ["右飛龍", "862", "86右飛龍", "右", "86上"],
    "伊弗利特": ["伊弗利特", "伊弗", "EF", "ef", "伊佛", "衣服", "E", "e"],
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
    },"幹你娘": {
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
def handle_boss_skipped(event, group_id, boss_name, user_id, note):
    cd = cd_map.get(boss_name)
    if cd is None: return

    # 這裡會抓到「最後一次」登記的紀錄 (不論是手動還是輪空)
    latest_records = get_latest_boss_records(group_id)
    
    if boss_name in latest_records:
        last_respawn_iso = latest_records[boss_name][0]["respawn"]
        base_time = datetime.fromisoformat(last_respawn_iso)
        if base_time.tzinfo is None:
            base_time = base_time.replace(tzinfo=pytz.UTC).astimezone(TZ)
        else:
            base_time = base_time.astimezone(TZ)
    else:
        base_time = now_tw().replace(second=0, microsecond=0)

    new_respawn = base_time + timedelta(hours=cd)
    
    save_boss_to_pg(
        group_id=group_id,
        boss_name=boss_name,
        kill_time=base_time, 
        respawn_time=new_respawn,
        user_id=user_id,
        note=note,
        source="skip" # 標記為輪空
    )

    registrar = get_username(user_id)
    # 統一顯示格式為 %H:%M:%S 確保與正常登記一致
    kill_str = base_time.strftime("%H:%M:%S")
    resp_str = new_respawn.strftime("%H:%M:%S")
    
    flex_msg = build_register_boss_flex(boss_name, kill_str, resp_str, registrar, note, is_skip=True)
    text_msg = f"⭕ 輪空登記：{boss_name}\n基準點：{kill_str}\n下趟重生：{resp_str}"
    
    safe_reply(event, text_msg, flex_msg)
    
    safe_reply(event, text_msg, flex_msg)
def get_kpi_range(now):
    """
    計算以『週三 05:00』為起點的 KPI 區間
    區間：本週三 05:00:00 ~ 下週三 05:00:00 (不含)
    """
    # 計算距離最近一個週三差幾天 (Mon=0, Tue=1, Wed=2...)
    days_since_wed = (now.weekday() - 2) % 7
    
    # 取得本週三的日期
    start = now - timedelta(days=days_since_wed)
    # 強制設定時間為 05:00:00
    start = start.replace(hour=5, minute=0, second=0, microsecond=0)
    
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
            # 呼叫處理函式
            handle_boss_skipped(event, group_id, boss_name, user_id, note)
            return
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❓ 找不到王名：{boss_input}"))
            return

    #-------------------------------------------------------------刪除單一王---------------------------------------
    if msg_text.startswith("刪 "):
        name_input = msg_text[2:].strip()
        if name_input:
            success, final_name = delete_boss_records_by_alias(group_id, name_input)
            
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
                # 取得金額
                new_bid = int(text.replace("下標 ", "").strip())
                current = active_auctions[group_id]
                
                if new_bid > current["bid"]:
                    current_user_name = get_username(user_id)
                    active_auctions[group_id].update({
                        "bid": new_bid,
                        "bidder_name": current_user_name,
                        "bidder_id": user_id
                    })
                    # 更新卡片回傳
                    flex = build_auction_flex(current["item"], new_bid, current_user_name)
                    line_bot_api.reply_message(event.reply_token, flex)
                else:
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 出價需高於目前的 {current['bid']} 鑽"))
            except ValueError:
                pass # 數字格式錯誤則不回應

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
    
    #-------------------------------------------------------------查自己名冊---------------------------------------
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
    #-------------------------------------------------------------刪除 名冊---------------------------------------
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
    #-------------------------------------------------------------紀錄開機時間---------------------------------------
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
        group_id = getattr(event.source, 'group_id', 'default_group')
        # 執行初始化邏輯
        init_cd_boss_with_given_time(group_id, base_time, user)
        
        # 1. 取得 Flex 字典內容 (確保 build_boot_init_flex 回傳的是 dict)
        flex_contents = build_boot_init_flex(base_time.strftime('%H:%M'))
        
        # 2. 關鍵修正：直接傳入字典，不要使用 BubbleContainer.new_from_json_dict
        line_bot_api.reply_message(
            event.reply_token,
            FlexSendMessage(
                alt_text=f"🔌 開機時間已紀錄：{base_time.strftime('%H:%M')}",
                contents=flex_contents  # 直接傳字典進去
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
    #-------------------------------------------------------------!!!!!!!   未完成 查詢王!!!!!!!---------------------------------------
    if msg.startswith("查 "):
        name = msg.split(" ", 1)[1]
        boss = get_boss(name)
        if not boss:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("找不到此王")
            )
            return

        # 1. 從資料庫抓取該群組、該隻王的最近 5 筆紀錄
        group_id = getattr(event.source, 'group_id', 'default_group')
        conn = get_pg_conn()
        if not conn: return
        
        try:
            cur = conn.cursor()
            # 抓取最近 5 筆，按時間由舊到新排序 (符合原本程式碼習慣)
            query = """
                SELECT kill_time, respawn_time, note, user_id
                FROM (
                    SELECT kill_time, respawn_time, note, user_id
                    FROM boss_time
                    WHERE group_id = %s AND boss_name = %s
                    ORDER BY kill_time DESC
                    LIMIT 5
                ) sub
                ORDER BY kill_time ASC
            """
            cur.execute(query, (group_id, boss))
            rows = cur.fetchall()
            cur.close()
            
            if not rows:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("尚無紀錄")
                )
                return

            # 2. 轉換為 Flex Message 接收的格式
            records = []
            for row in rows:
                records.append({
                    "kill": row[0].strftime("%H:%M:%S"),
                    "respawn": row[1].isoformat(),
                    "note": row[2] if row[2] else "",
                    "user": row[3]
                })

            # 3. 呼叫原本的 Flex 產生器並送出
            flex_msg = build_query_boss_flex(boss, records)
            line_bot_api.reply_message(
                event.reply_token,
                flex_msg
            )
        except Exception as e:
            print(f"Error querying boss records: {e}")
        finally:
            conn.close()
        return
    #-------------------------------------------------------------KPI---------------------------------------
    if msg.upper() == "KPI":
        now = now_tw()
        start, end = get_kpi_range(now)
        group_id = get_source_id(event)
        
        # 使用現有的 get_kpi_ranking 函式獲取資料
        # 注意：原本檔案內的 get_kpi_ranking 內部已經會呼叫 get_kpi_range
        period_text, ranking = get_kpi_ranking(group_id)
        
        if not ranking:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(f"📊 區間：{period_text}\n目前尚無 KPI 紀錄")
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
    is_force_full = (msg == "出出")
    if msg in ("出", "出出"):
        now = now_tw()
        time_items = []
        unregistered = []
        
        group_id = getattr(event.source, 'group_id', 'default_group')
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
        
        group_id = getattr(event.source, 'group_id', 'default_group')
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
            
            text_msg = build_register_boss_text(boss, kill_str, resp_str, registrar, note)
            flex_msg = build_register_boss_flex(boss, kill_str, resp_str, registrar, note)
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
