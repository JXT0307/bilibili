import requests
import random
import time
import json
import re
import signal
import sys
from typing import List

# ==================== 配置区域 ====================
# 你的完整 Cookie（从浏览器复制，必须包含 SESSDATA 和 bili_jct）
COOKIE_STR = ""
# 目标动态ID（从URL中获取）
DYNAMIC_ID = 1175151701299036183

# 评论类型（动态通常为17）
COMMENT_TYPE = 17

# 评论文案列表（每行一条，会循环使用）
COMMENT_TEXTS = [
    "约这种立绘你是认真的吗，说话来📢📢📢📢约这种立绘你是认真的吗，说话来📢📢📢📢约这种立绘你是认真的吗，说话来📢📢📢📢约这种立绘你是认真的吗，说话来📢📢📢📢约这种立绘你是认真的吗，说话来📢📢📢📢约这种立绘你是认真的吗，说话来📢📢📢📢约这种立绘你是认真的吗，说话来📢📢",
    "最明显的问题，脖子歪的太过分了，大腿部分乍一看就跟反掉了一样，这么明显的违和感在拿到稿子之后就没有一个人提出来吗",
    "人体结构有问题，手指有问题，脸也不像然然，快改，快改",
    "第一场生日会办好了，让新人知道生日会是枝江最重要的节日之一，后续其他活动也会越办越好！请把产能用在关键的地方！",
    "这不是我要的那只玩偶 我要更好的那只[2025嘉然的画册_睡了]",
    "然然最爱的洛丽塔能好好搞吗？[2025嘉然的画册_哈！][2025嘉然的画册_哈！][2025嘉然的画册_哈！]"
    "驼子你知道吗，7千楼没有恨，只有心疼和不解，只有对小然和Asoul的关心和爱，如果是错误就改正，不要留下遗憾！！！"
]

# 自定义表情包接口（可添加任意B站表情代码，留空则无表情）
CUSTOM_EMOJIS = [
    # '[热词系列_好耶]',
    # '[2233娘_大笑]',
    # '[鹿鸣_比心]'
]

# 发送间隔（秒）
INTERVAL_MIN = 20
INTERVAL_MAX = 50

# 当遇到“重复评论”时的最大重试次数（尝试不同文案）
MAX_RETRIES_ON_DUPLICATE = 3

# ==================== 全局停止标志 ====================
running = True

def signal_handler(sig, frame):
    """捕获 Ctrl+C 信号，优雅停止"""
    global running
    print("\n⚠️  收到停止信号，正在完成当前发送后退出...")
    running = False

signal.signal(signal.SIGINT, signal_handler)

# ==================== 工具函数 ====================
def parse_cookie(cookie_str: str) -> dict:
    """将Cookie字符串转为字典"""
    cookies = {}
    for item in cookie_str.split('; '):
        if '=' in item:
            key, value = item.split('=', 1)
            cookies[key] = value
    return cookies

def load_comments() -> List[str]:
    """从列表加载文案，并过滤空行"""
    return [c.strip() for c in COMMENT_TEXTS if c.strip()]

# ==================== 表情包分布逻辑（移植自JS） ====================
def distribute_elements(selected_emojis: List[str], text: str) -> str:
    """
    将选中的表情包随机分布在文本的开头、中间（标点后）、结尾
    返回组合后的最终评论
    """
    if not selected_emojis:
        return text

    # 随机分配位置
    positions = []
    for _ in selected_emojis:
        r = random.random()
        if r < 0.33:
            positions.append('start')
        elif r < 0.66:
            positions.append('middle')
        else:
            positions.append('end')

    start_part = ''
    end_part = ''
    middle_emojis = []
    for i, pos in enumerate(positions):
        if pos == 'start':
            start_part += selected_emojis[i]
        elif pos == 'end':
            end_part += selected_emojis[i]
        else:
            middle_emojis.append(selected_emojis[i])

    # 查找所有标点位置（中文/英文标点）
    punct_pattern = re.compile(r'[，。！？；：,.!?;:]|…+|\.{2,}')
    matches = list(punct_pattern.finditer(text))
    punct_indices = [m.end() for m in matches]  # 标点后一位的索引

    # 去重连续标点（只保留最后一个）
    merged_indices = []
    if punct_indices:
        merged_indices = [punct_indices[0]]
        for idx in punct_indices[1:]:
            if idx != merged_indices[-1]:
                merged_indices.append(idx)

    middle_map = {}  # 位置 -> 表情字符串
    if merged_indices and middle_emojis:
        emoji_idx = 0
        for i, pos in enumerate(merged_indices):
            if i == len(merged_indices) - 1:
                # 最后一个标点后插入所有剩余表情
                remaining = ''.join(middle_emojis[emoji_idx:])
                if remaining:
                    middle_map[pos] = middle_map.get(pos, '') + remaining
                emoji_idx = len(middle_emojis)
            else:
                if emoji_idx < len(middle_emojis):
                    middle_map[pos] = middle_map.get(pos, '') + middle_emojis[emoji_idx]
                    emoji_idx += 1
        # 如果还有剩余表情，全部加到结尾
        if emoji_idx < len(middle_emojis):
            end_part = ''.join(middle_emojis[emoji_idx:]) + end_part
    else:
        # 没有标点，所有中间表情都加到结尾
        end_part = ''.join(middle_emojis) + end_part

    # 组装最终文本
    final_text = start_part
    for i, ch in enumerate(text):
        final_text += ch
        if i + 1 in middle_map:  # 注意：我们存储的是插入位置（标点后一位的索引）
            final_text += middle_map[i + 1]
    final_text += end_part
    return final_text

def build_comment_with_emojis(raw_comment: str) -> str:
    """
    根据原始文案和自定义表情包，生成带表情的最终评论
    """
    if not CUSTOM_EMOJIS:
        return raw_comment

    # 随机决定抽取的表情包数量（与原JS逻辑一致）
    comment_len = len(raw_comment)
    min_count = 4
    max_count = 15
    if comment_len < 5:
        max_count = 8
    elif comment_len > 20:
        min_count = 8

    max_count = min(max_count, len(CUSTOM_EMOJIS))
    min_count = min(min_count, max_count)
    if min_count == 0:
        return raw_comment

    tail_count = random.randint(min_count, max_count)
    # 随机打乱并选取
    shuffled = random.sample(CUSTOM_EMOJIS, len(CUSTOM_EMOJIS))
    selected = shuffled[:tail_count]
    random.shuffle(selected)  # 再随机顺序
    return distribute_elements(selected, raw_comment)

# ==================== API 评论发送 ====================
def send_comment_api(dynamic_id: int, message: str, cookies: dict, csrf: str) -> dict:
    """调用B站API发送评论，返回完整响应"""
    url = "https://api.bilibili.com/x/v2/reply/add"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://t.bilibili.com/",
        "Origin": "https://t.bilibili.com"
    }
    data = {
        "oid": dynamic_id,
        "type": COMMENT_TYPE,
        "message": message,
        "plat": 1,
        "jsonp": "jsonp",
        "csrf": csrf
    }
    try:
        resp = requests.post(url, headers=headers, cookies=cookies, data=data)
        return resp.json()
    except Exception as e:
        return {"code": -1, "message": str(e)}

# ==================== 主流程 ====================
def main():
    print("="*50)
    print("B站动态自动评论 (全自动循环版) - 按 Ctrl+C 安全停止")
    print("="*50)

    cookies = parse_cookie(COOKIE_STR)
    csrf = cookies.get("bili_jct")
    if not csrf:
        print("❌ Cookie中未找到 bili_jct")
        return
    print("✅ Cookie解析成功")

    comments = load_comments()
    if not comments:
        print("❌ 评论文案为空")
        return
    print(f"📋 文案库: {len(comments)} 条 (将循环使用)")
    print(f"😀 自定义表情包数量: {len(CUSTOM_EMOJIS)}")
    print(f"⏱️  发送间隔: {INTERVAL_MIN}~{INTERVAL_MAX} 秒")
    print(f"🔄 重复评论自动重试次数: {MAX_RETRIES_ON_DUPLICATE}")
    print("-" * 50)

    fail_count = 0
    max_fails = 3
    count = 0

    global running
    while running:
        count += 1
        # 内部重试循环：处理“重复评论”错误
        retry_count = 0
        used_indices = set()  # 记录本次尝试中已用过的文案索引
        success = False

        while not success and retry_count <= MAX_RETRIES_ON_DUPLICATE:
            # 从comments中选择一个未使用过的索引
            available_indices = [i for i in range(len(comments)) if i not in used_indices]
            if not available_indices:
                print("⚠️ 所有文案都试过且都因重复失败，等待后重试")
                break  # 跳出内循环，本次发送失败

            idx = random.choice(available_indices)
            raw_comment = comments[idx]
            used_indices.add(idx)

            final_comment = build_comment_with_emojis(raw_comment)
            print(f"\n▶ 第 {count} 次发送 (尝试 {retry_count+1}/{MAX_RETRIES_ON_DUPLICATE+1})")
            print(f"  原始文案: {raw_comment}")
            print(f"  最终评论: {final_comment}")

            result = send_comment_api(DYNAMIC_ID, final_comment, cookies, csrf)
            print(f"  返回结果: {json.dumps(result, ensure_ascii=False, indent=2)}")

            if result.get("code") == 0:
                print("✅ 发送成功")
                success = True
                fail_count = 0  # 成功时重置失败计数
                break
            else:
                msg = result.get('message', '未知错误')
                # 判断是否为“重复评论”错误（B站可能返回多种表述）
                if "重复评论" in msg or "请勿刷屏" in msg or "内容重复" in msg:
                    print("⚠️ 检测到重复评论，尝试换一条文案")
                    retry_count += 1
                    # 继续内循环，尝试另一条文案
                else:
                    print(f"❌ 发送失败: {msg}")
                    fail_count += 1
                    break  # 其他错误，跳出内循环

        # 如果因为所有文案都重复而跳出内循环，则本次发送未成功，但不算连续失败
        if not success and retry_count <= MAX_RETRIES_ON_DUPLICATE:
            # 这种情况是遇到了其他错误（非重复），已经增加了 fail_count
            pass
        elif not success:
            # 所有文案都重复导致失败，不增加 fail_count，但打印提示
            print("⏭️ 本次发送因所有文案重复而跳过，不计入失败计数")

        # 检查是否需要停止
        if fail_count >= max_fails:
            print(f"⛔ 连续失败 {max_fails} 次，停止")
            break

        # 如果收到停止信号，不再等待下一次
        if not running:
            print("⏹ 已收到停止信号，退出循环")
            break

        # 随机等待（只有当运行标志为真且未停止时才等待）
        if running:
            sleep_time = random.randint(INTERVAL_MIN, INTERVAL_MAX)
            print(f"⏳ 等待 {sleep_time} 秒... (按 Ctrl+C 可安全停止)")
            # 分段睡眠以便及时响应 Ctrl+C
            for _ in range(sleep_time):
                if not running:
                    break
                time.sleep(1)

    print("👋 脚本已退出")

if __name__ == "__main__":
    main()