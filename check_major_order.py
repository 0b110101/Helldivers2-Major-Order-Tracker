import os
import json
import requests
from datetime import datetime, timezone

try:
    from deep_translator import GoogleTranslator
    TRANSLATOR_AVAILABLE = True
except ImportError:
    TRANSLATOR_AVAILABLE = False

DATA_FILE = "data/helldivers_state.json"
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SUPER_EARTH_ICON = "https://static.wikia.nocookie.net/helldivers_gamepedia/images/c/c2/Super_Earth.png"

# 针对 helldiversstats.com 官方文档规范参数
STATS_URL = "https://helldiversstats.com/api/v1/historical-major-orders?limit=1"

def load_state():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def to_chinese(text):
    if not text:
        return ""
    if any('\u4e00' <= char <= '\u9fff' for char in text):
        return text
    if TRANSLATOR_AVAILABLE:
        try:
            return GoogleTranslator(source='auto', target='zh-CN').translate(text)
        except Exception as e:
            print(f"[Warn] 翻译失败: {e}")
    return text

def format_duration(seconds):
    if seconds <= 0:
        return "即将结束 / 战区结算中"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    mins = (seconds % 3600) // 60
    parts = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0:
        parts.append(f"{hours}小时")
    if mins > 0 and days == 0:
        parts.append(f"{mins}分钟")
    return " ".join(parts) if parts else "少于1分钟"

def send_discord_embed(embed_payload):
    if not WEBHOOK_URL:
        print("[Error] DISCORD_WEBHOOK_URL 未配置")
        return
    data = {
        "username": "超级地球战略指挥部",
        "avatar_url": SUPER_EARTH_ICON,
        "embeds": [embed_payload]
    }
    resp = requests.post(WEBHOOK_URL, json=data, timeout=15)
    resp.raise_for_status()

def main():
    state = load_state()
    headers = {
        "Accept-Language": "zh-Hans,zh-CN;q=0.9",
        "User-Agent": "HelldiversDiscordNotifier/1.0"
    }

    print(f"[Info] 正在请求有效端点: {STATS_URL} ...")
    try:
        res = requests.get(STATS_URL, headers=headers, timeout=15)
        print(f"[Info] 状态码: {res.status_code}")
        if res.status_code != 200:
            print(f"[Error] API 响应异常: {res.text[:200]}")
            return
        
        raw_json = res.json()
        print("\n================== [DEBUG JSON 开始] ==================")
        print(json.dumps(raw_json, ensure_ascii=False, indent=2)[:3000])
        print("================== [DEBUG JSON 结束] ==================\n")

    except Exception as e:
        print(f"[Safe Exit] 网络请求异常: {e}")
        return

    # 解析数据列表
    if isinstance(raw_json, list):
        items = raw_json
    elif isinstance(raw_json, dict) and "data" in raw_json:
        items = raw_json["data"]
    else:
        items = [raw_json]

    if not items:
        print("[Info] 列表为空，跳过处理。")
        return

    raw_item = items[0]
    
    # 提取关键字段
    order_id = raw_item.get("orderId32") or raw_item.get("id32") or raw_item.get("id")
    briefing = raw_item.get("briefing") or raw_item.get("brief") or "前线战报简报准备中。"
    title = raw_item.get("title") or f"战略行动 #{order_id}"
    medals = raw_item.get("rewardMedals", 0)

    # 倒计时计算（结合 lastSeenAt 与 expiresInLastSeen）
    expires_in = raw_item.get("expiresInLastSeen", 0)
    if "lastSeenAt" in raw_item and expires_in > 0:
        try:
            last_seen = datetime.fromisoformat(raw_item["lastSeenAt"].replace("Z", "+00:00"))
            passed = (datetime.now(timezone.utc) - last_seen).total_seconds()
            expires_in = max(0, int(expires_in - passed))
        except Exception:
            pass

    is_active = (expires_in > 0)
    print(f"[Debug] 提取结果: ID={order_id}, 剩余秒数={expires_in}, 活跃={is_active}")

    # 中文翻译
    title_cn = to_chinese(title)
    brief_cn = to_chinese(briefing)
    reward_str = f"🎖️ **{medals}** 战争债券勋章" if medals else "管理式民主的无上荣光"
    time_str = format_duration(expires_in)

    last_order_id = state.get("last_order_id")

    # 1. 如果此前记录的指令与当前不同，或者已非活跃状态，触发结案通报
    if last_order_id and (last_order_id != order_id or not is_active):
        print(f"[Info] 指令 #{last_order_id} 结案")
        embed_finished = {
            "title": "🔴 【战略通报】重要指令已结束",
            "description": f"### 战事封盘：{state.get('last_order_title', '前线战略行动')}\n\n全体绝地潜兵请注意，该阶段战区统筹已截止。",
            "color": 0xE74C3C,
            "fields": [
                {"name": "🎖️ 战况与结算", "value": "战区战果正由超级地球最高参谋部核实，相应勋章将陆续派发至各驱逐舰终端。", "inline": False}
            ],
            "footer": {"text": "超级地球最高指挥部 · 统辖管理区"},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        send_discord_embed(embed_finished)
        state["last_order_id"] = None

    # 2. 如果当前是指令进行中，且是新 ID
    if is_active and order_id != state.get("last_order_id"):
        print(f"[Info] 发送新指令通知: #{order_id}")
        embed_new = {
            "title": "🟢 【优先警报】接收到新的重要指令",
            "description": f"### 🛡️ {title_cn}\n\n> {brief_cn}",
            "color": 0xF1C40F,
            "fields": [
                {"name": "🎯 作战目标", "value": "根据战术地图指引解放或防守指定星系。", "inline": False},
                {"name": "🎁 作战津贴", "value": reward_str, "inline": True},
                {"name": "⏱️ 作战时限", "value": f"`{time_str}`", "inline": True}
            ],
            "footer": {"text": "传播管理式民主 · 战略前线实况"},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        send_discord_embed(embed_new)
        state["last_order_id"] = order_id
        state["last_order_title"] = title_cn

    save_state(state)
    print("[Done] 流程执行完毕。")

if __name__ == "__main__":
    main()
