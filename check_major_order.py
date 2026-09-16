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
            print(f"[Warn] 翻译失败，使用原文: {e}")
    return text

def format_duration(seconds):
    if seconds <= 0:
        return "即将结束"
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

def parse_order_data(raw_item):
    """自适应解析来自 api.helldivers2.dev 或 helldiversstats 的数据"""
    if not raw_item:
        return None

    # 1. 提取 ID
    order_id = raw_item.get("id") or raw_item.get("id32") or raw_item.get("orderId32")
    if not order_id:
        return None

    # 2. 提取 标题 & 战报简报
    setting = raw_item.get("setting", {})
    raw_title = setting.get("overrideTitle") or raw_item.get("title") or f"行动 #{order_id}"
    raw_brief = setting.get("overrideBrief") or raw_item.get("briefing") or raw_item.get("brief") or "前线暂无补充简报。"

    # 3. 提取勋章奖励
    reward_medals = 0
    reward_dict = setting.get("reward") or raw_item.get("reward")
    if isinstance(reward_dict, dict):
        reward_medals = reward_dict.get("amount", 0)
    elif "rewardMedals" in raw_item:
        reward_medals = raw_item.get("rewardMedals", 0)

    # 4. 计算剩余秒数与有效性
    expires_in = 0
    if "expiresIn" in raw_item:
        expires_in = raw_item.get("expiresIn", 0)
    elif "expiresInLastSeen" in raw_item and "lastSeenAt" in raw_item:
        # helldiversstats 逻辑：结合 lastSeenAt 与 expiresInLastSeen
        try:
            last_seen = datetime.fromisoformat(raw_item["lastSeenAt"].replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            passed = (now - last_seen).total_seconds()
            expires_in = max(0, int(raw_item["expiresInLastSeen"] - passed))
        except Exception:
            expires_in = raw_item.get("expiresInLastSeen", 0)

    return {
        "id": order_id,
        "title": to_chinese(raw_title),
        "brief": to_chinese(raw_brief),
        "reward_medals": reward_medals,
        "expires_in": expires_in,
        "is_active": expires_in > 0
    }

def fetch_current_order():
    """获取并校验当前 MO，若网络失败则抛出异常，防止状态误杀"""
    headers = {
        "Accept-Language": "zh-Hans,zh-CN;q=0.9",
        "User-Agent": "HelldiversDiscordNotifier/2.0",
        "X-Super-Client": "HelldiversDiscordNotifier",
        "X-Super-Contact": "admin@helldivers.internal"
    }

    # 1. 尝试主社区 API 的 assignments 路径
    for url in ["https://api.helldivers2.dev/api/v1/assignments", "https://api.helldivers2.dev/raw/api/v2/assignments"]:
        try:
            print(f"[Info] 正在请求主要端点: {url} ...")
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and len(data) > 0:
                    parsed = parse_order_data(data[0])
                    if parsed:
                        return parsed
        except Exception as e:
            print(f"[Warn] 端点 {url} 失败: {e}")

    # 2. 回退到 helldiversstats.com
    try:
        fallback_url = "https://helldiversstats.com/api/v1/historical-major-orders?limit=1"
        print(f"[Info] 尝试备用端点: {fallback_url} ...")
        r = requests.get(fallback_url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            items = data if isinstance(data, list) else data.get("data", [])
            if items:
                parsed = parse_order_data(items[0])
                # 如果这个历史最新的单子已经超时，说明当前战役空档期，当前无 MO
                if parsed and parsed["is_active"]:
                    return parsed
                else:
                    print("[Info] 备用源最新 MO 已过期，当前无正在进行的 MO")
                    return None
    except Exception as e:
        print(f"[Warn] 备用端点请求失败: {e}")

    raise RuntimeError("所有 API 请求失败，保持现有状态，不执行通知逻辑。")

def main():
    state = load_state()
    try:
        current = fetch_current_order()
    except Exception as e:
        print(f"[Safe Exit] {e}")
        # 网络失败直接退出，绝对不修改本地记录，杜绝误报
        return

    last_order_id = state.get("last_order_id")
    last_order_title = state.get("last_order_title", "作战任务")

    # 1. 检测上一条指令是否已结束
    if last_order_id:
        # 如果当前无进行中的 MO，或者当前 MO 的 ID 变了，则旧 MO 结案
        if not current or current["id"] != last_order_id:
            print(f"[Info] 确认指令 #{last_order_id} 已结束")
            embed_finished = {
                "title": "🔴 【战略通报】重要指令已结束",
                "description": f"### 战事封盘：{last_order_title}\n\n全体绝地潜兵请注意，该阶段战区统筹已截止。",
                "color": 0xE74C3C,
                "fields": [
                    {
                        "name": "🎖️ 战况与结算",
                        "value": "战区战果正由超级地球最高参谋部核实，相应勋章将陆续派发至各驱逐舰终端。",
                        "inline": False
                    }
                ],
                "footer": {"text": "超级地球最高指挥部 · 统辖管理区"},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            send_discord_embed(embed_finished)
            state["last_order_id"] = None

    # 2. 检测是否有新发布的指令
    if current and current["is_active"]:
        cid = current["id"]
        if cid != state.get("last_order_id"):
            medals = current["reward_medals"]
            reward_desc = f"🎖️ **{medals}** 战争债券勋章" if medals else "管理式民主的无上荣光"
            time_str = format_duration(current["expires_in"])

            embed_new = {
                "title": "🟢 【优先警报】接收到新的重要指令",
                "description": f"### 🛡️ {current['title']}\n\n> {current['brief']}",
                "color": 0xF1C40F,
                "fields": [
                    {"name": "🎯 作战目标", "value": "根据战术地图指引解放或防守指定星系。", "inline": False},
                    {"name": "🎁 作战津贴", "value": reward_desc, "inline": True},
                    {"name": "⏱️ 作战时限", "value": f"`{time_str}`", "inline": True}
                ],
                "footer": {"text": "传播管理式民主 · 战略前线实况"},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            send_discord_embed(embed_new)

            state["last_order_id"] = cid
            state["last_order_title"] = current["title"]

    save_state(state)

if __name__ == "__main__":
    main()
