# 天堂M 吃王小幫手

# ===== 系統與基礎庫 =====

import os
import json
import time
import pytz
import asyncio
import requests
import threading
from threading import Lock
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from boss_config import cd_map, BOSS_MAP, alias_map, MAJOR_BOSSES, get_boss
from flex_templates import build_all_boss_quick_flex, build_register_boss_flex, build_member_list_flex
# ===== 資料庫連結 =====
import psycopg2
# ===== Web 框架 =====
from fastapi import FastAPI, Request, Header
# ===== LINE SDK 導入 (核心修正區) =====
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent,
    TextMessage,
    TextSendMessage,
    FlexSendMessage,
    BubbleContainer,      
    MemberJoinedEvent
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
    """從資料庫抓取各隻王最後一筆紀錄 (用於 '備份' 與 '出')"""
    conn = get_pg_conn()
    if not conn: return {}
    try:
        cur = conn.cursor()
        # DISTINCT ON 確保每隻王只取最新的一筆
        query = """
            SELECT DISTINCT ON (boss_name) 
                   boss_name, kill_time, respawn_time, note, user_id, source
            FROM boss_time
            WHERE group_id = %s
            ORDER BY boss_name, kill_time DESC
        """
        cur.execute(query, (group_id,))
        rows = cur.fetchall()
        cur.close()
        
        result = {}
        for row in rows:
            boss_name = row[0]
            kt_raw = row[1]  # 資料庫原始時間 (通常是 UTC)
            
            # --- 關鍵修復：時區轉換 ---
            # 如果抓出來的時間沒有時區資訊，先給它 UTC，再轉成台北 TZ (GMT+8)
            if kt_raw.tzinfo is None:
                kt_tw = pytz.utc.localize(kt_raw).astimezone(TZ)
            else:
                kt_tw = kt_raw.astimezone(TZ)
            
            # 處理重生時間
            rt_raw = row[2]
            rt_tw = rt_raw.astimezone(TZ) if rt_raw.tzinfo else pytz.utc.localize(rt_raw).astimezone(TZ)

            # 轉換為 dict 格式，並補齊 KPI 結算需要的欄位
            result[boss_name] = [{
                "date": kt_tw.strftime("%Y-%m-%d"),
                "kill": kt_tw.strftime("%H:%M:%S"), # 這邊輸出的就會是正確的台北時間
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
    conn = get_pg_conn()
    if not conn: return
    try:
        cur = conn.cursor()
        # 抓出「最近12小時內」已有紀錄的王，避免重複補推
        cur.execute("""
            SELECT DISTINCT boss_name FROM boss_time 
            WHERE group_id = %s AND respawn_time > CURRENT_TIMESTAMP - INTERVAL '12 hours'
        """, (group_id,))
        recorded_bosses = {row[0] for row in cur.fetchall()}
        
        for boss, cd in cd_map.items():
            if boss in recorded_bosses: continue
            
            respawn = base_time + timedelta(hours=cd)
            cur.execute("""
                INSERT INTO boss_time (group_id, boss_name, kill_time, respawn_time, user_id, note, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (group_id, boss, base_time, respawn, user_id, "伺服器開機補推", "boot"))
        conn.commit()
    except Exception as e:
        print(f"Init Error: {e}")
    finally:
        conn.close()

def update_system_config(group_id, key, value):
    """
    更新系統設定到資料庫 (例如：最後開機時間)
    """
    conn = get_pg_conn()
    if not conn: return
    
    try:
        cur = conn.cursor()
        # 使用 ON CONFLICT 確保如果 key 已存在就更新，不存在就插入
        # 注意：這需要您的資料庫中有一個系統設定表，或者您可以改存入 boss_time 表的一個特殊紀錄
        query = """
            INSERT INTO system_config (group_id, config_key, config_value, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (group_id, config_key) 
            DO UPDATE SET config_value = EXCLUDED.config_value, updated_at = CURRENT_TIMESTAMP
        """
        cur.execute(query, (group_id, key, value))
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"Error updating system config: {e}")
    finally:
        conn.close()

def update_system_config(group_id, key, value):
    conn = get_pg_conn()
    if not conn: return
    try:
        cur = conn.cursor()
        # 借用 boss_time 表存系統變數
        cur.execute("""
            INSERT INTO boss_time (group_id, boss_name, kill_time, note, source)
            VALUES (%s, %s, %s, %s, %s)
        """, (group_id, "__SYSTEM_CONFIG__", value, key, "config"))
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"Error: {e}")
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


# 王資料
def get_boss(name):
    """
    透過 alias_map 尋找標準的王名
    """
    name = name.strip().lower()
    for standard_name, aliases in alias_map.items():
        # 轉換別名表為小寫進行比對
        if name == standard_name or name in [a.lower() for a in aliases]:
            return standard_name
    return None

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
@handler.add(MemberJoinedEvent)

def handle_member_joined(event):
    if event.source.type in ["group", "room"]:
        line_bot_api.reply_message(event.reply_token, build_join_roster_guide_flex())

import re

def sanitize_register_line(line: str) -> str:
    """ 清理單行內容，過濾掉裝飾符號與標題 """
    line = line.strip()
    if not line or any(x in line for x in ["📦", "王表備份", "—"]): return ""
    # 移除「#過N」或「#過 N」
    line = re.sub(r"\s*#\s*過\s*\d+", "", line)
    # 壓縮多餘空白
    return re.sub(r"\s{2,}", " ", line).strip()

def build_kpi_backup_text(kpi_db):
    lines = ["__KPI_START__"]
    for user_id, count in kpi_db.items():
        name = get_username(user_id)
        lines.append(f"{name} {user_id} {count}")
    lines.append("__KPI_END__")
    return "\n".join(lines)
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
    boss_db = db["boss"][group_id]
    clean_msg = msg.strip()
    
    # 備份 (修正版：純粹輸出原始紀錄)
    if clean_msg == "備份" and "\n" not in msg:
        now = now_tw()
        output = ["📦【王表備份】", ""]

        group_id = getattr(event.source, 'group_id', 'default_group')
        # 抓取該群組所有王最新的一筆紀錄
        boss_db_from_pg = get_latest_boss_records(group_id)

        for boss, records in boss_db_from_pg.items():
            if not records: continue
            
            # 取得最後一次登記的原始資料
            last = records[0] 
            kill_time = last.get("kill") # 格式 "14:30:00"
            note = last.get("note", "").strip()

            if not kill_time: continue

            # 將 "14:30:00" 轉為 "1430" (最純粹的輸入格式)
            hhmmss = kill_time.replace(":", "")[:6] 

            # ===== 組輸出 (不帶 #過) =====
            line = f"{hhmmss} {boss}"
            if note:
                line += f" {note}"

            output.append(line)

        reply = "\n".join(output)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return
    
    # 1. 加入打王組：輸入「+1」
    if text == "+1":
        user_name = get_username(user_id)
        conn = get_pg_conn()
        cur = conn.cursor()
        try:
            # 存入資料
            cur.execute(
                "INSERT INTO boss_team (group_id, user_id, user_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (group_id, user_id, user_name)
            )
            conn.commit()

            # 💡 增加步驟：抓取該群組目前所有成員名單
            cur.execute("SELECT user_name FROM boss_team WHERE group_id = %s", (group_id,))
            rows = cur.fetchall()
            members = [r[0] for r in rows]
            member_list_str = "、".join(members) # 用頓號隔開人名

            line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage(text=f"✅ {user_name} 已加入打王組！\n\n目前成員：{member_list_str}")
            )
        except Exception as e:
            print(f"Error: {e}")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 系統忙碌中，請稍後再試"))
        finally:
            cur.close()
            conn.close()

    # 2. 退出打王組：輸入「-1」
    elif text == "-1":
        conn = get_pg_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM boss_team WHERE group_id = %s AND user_id = %s", (group_id, user_id))
        conn.commit()
        cur.close()
        conn.close()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 已將您移出打王組。"))

    # 在 handle_message 內判斷指令的地方
    if text == "登記" or text == "打王":
        flex = build_all_boss_quick_flex()
        line_bot_api.reply_message(event.reply_token, flex)
        return
# --- 競標系統區塊 ---
    
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
    #-----查名冊
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
        if len(parts) < 2: return
        
        time_token = parts[1].strip()
        base_time = parse_time(time_token)
        
        if not base_time:
            line_bot_api.reply_message(event.reply_token, TextSendMessage("❌ 格式錯誤"))
            return

        # 取得當前使用者 ID
        user_id = event.source.user_id
        
        # 執行步驟：只針對沒紀錄的王補推
        # 這裡會用到你提供的定義
        init_cd_boss_with_given_time(group_id, base_time, user_id)
        
        # 存入資料庫 Config (如果需要儲存最後一次開機時間)
        update_system_config(group_id, "last_boot_time", base_time.strftime('%Y-%m-%d %H:%M:%S'))

        # 回傳 Flex 訊息
        flex_contents = build_boot_init_flex(base_time.strftime('%H:%M'))
        line_bot_api.reply_message(
            event.reply_token,
            FlexSendMessage(
                alt_text="🔌 開機初始化補推完成",
                contents=BubbleContainer.new_from_json_dict(flex_contents)
            )
        )
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
    # KPI 指令處理
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
    # 出
    is_force_full = (msg == "出出")
    if msg in ("出", "出出", "tj"):
        now = now_tw()
        time_items = []
        unregistered = []
        
        group_id = getattr(event.source, 'group_id', 'default_group')
        boss_db_from_pg = get_latest_boss_records(group_id) 

        for boss, cd in cd_map.items():
            if boss not in boss_db_from_pg or not boss_db_from_pg[boss]:
                unregistered.append(boss)
                continue
            
            # 從資料庫取出最近一筆紀錄
            rec = boss_db_from_pg[boss][-1]
            
            # 確保時區正確轉換
            if isinstance(rec["respawn"], str):
                base_respawn = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)
            else:
                base_respawn = rec["respawn"].astimezone(TZ)
                
            step = timedelta(hours=cd)
            
            if now < base_respawn:
                display_time = base_respawn
                passed_minutes = None
                missed = 0
            else:
                diff = now - base_respawn
                # 計算已經過了幾輪
                rounds_passed = int(diff.total_seconds() // step.total_seconds())
                current_respawn = base_respawn + rounds_passed * step
                passed_minutes = int((now - current_respawn).total_seconds() // 60)
                
                # 判斷門檻：30分鐘內視為「未打」，超過則跳「下一輪」
                if passed_minutes <= 30:
                    display_time = current_respawn
                    missed = rounds_passed           
                else:
                    display_time = current_respawn + step
                    missed = rounds_passed + 1
                    passed_minutes = None
            
            note = rec.get("note", "").strip()
            # 格式化輸出
            time_str = display_time.strftime('%H:%M:%S')
            
            # 如果不是今天的王，加上日期標註 (例如跨日後看昨天的進度)
            if display_time.date() > now.date():
                time_str = f"明 {display_time.strftime('%H:%M')}"
            
            line = f"{time_str} {boss}"
            if note:
                line += f"（{note}）"
            if passed_minutes is not None and passed_minutes <= 30:
                line += f" <{passed_minutes}分未打>"
            if missed > 0:
                line += f" #過{missed}"
                
            time_items.append((display_time, line))

        # 排序：時間越近的排在越上面
        time_items.sort(key=lambda x: x[0])
        
        # --- 輸出組合 ---
        output = ["📢【即將重生列表】", ""]
        
        # 判斷是否需要截斷（熱門時段邏輯）
        display_items = time_items
        if is_peak_time() and not is_force_full:
            display_items = time_items[:14]
            output = ["📢【即將重生列表｜熱門】", ""]

        for _, line in display_items:
            output.append(line)

        if is_peak_time() and not is_force_full:
            output.append("\n👉 輸入「出出」查看完整清單")

        if unregistered:
            output.append("\n— 未登記 —")
            # 簡單排序未登記的王名
            unregistered.sort()
            output.append("、".join(unregistered))

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("\n".join(output))
        )
        return
    # ===== 固定王(關閉) =====
    #    for boss, conf in fixed_bosses.items():
    #        t = get_next_fixed_time_fixed(conf)
    #        if not t:
    #           continue
    #   
    #       time_items.append(
    #            (2, t, f"{t.strftime('%H:%M:%S')} {boss}")
    #        )


    # ===== 登記王（支援多行 / 備份貼上 + KPI）=====
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
