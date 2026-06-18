import json
import logging
import random
import time
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.edge.service import Service
from selenium.webdriver.support.ui import WebDriverWait

# -------------------------- 全局配置区域（所有可修改项都在这里） --------------------------
CONFIG = {
    "target_video_url": "https://www.bilibili.com/video/BV1794y1Y7N7/?spm_id_from=333.1387.homepage.video_card.click&vd_source=34f1657bec772c344c036e705cd6559a",
    "comment_list": [
        "学到了，感谢up主分享！",
        "这个思路太棒了，收藏了",
        "讲得很清楚，终于搞懂了",
        "支持up，期待更多干货",
        "太有用了，一键三连支持",
        "干货满满，收藏学习",
    ],
    "min_interval": 300,  # 最小发送间隔（秒），建议至少5分钟
    "max_interval": 1800,  # 最大发送间隔（秒）
    "max_retries": 3,  # 单条评论最大重试次数
    "cookie_file": "bilibili_cookie.json",  # 登录Cookie保存文件
    "log_file": "auto_comment.log",  # 日志文件
}
# --------------------------------------------------------------------------------------

COOKIE_PATH = Path(CONFIG["cookie_file"])
LOG_PATH = Path(CONFIG["log_file"])


def setup_logging():
    """配置日志系统（同时输出到控制台和文件）。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8", mode="a"),
            logging.StreamHandler(),
        ],
        force=True,
    )


logger = logging.getLogger(__name__)


def init_edge_browser():
    """初始化 Edge 浏览器，并加入基础反检测配置。"""
    options = webdriver.EdgeOptions()

    # 核心反检测配置
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-blink-features=AutomationControlled")

    # 禁用不必要的弹窗与扩展
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-extensions")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--window-size=1440,900")
    options.add_argument("--disable-gpu")

    # 模拟真实用户的浏览器指纹
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
    )

    # 优先使用项目目录中的本地 EdgeDriver，避免网络下载卡住
    local_driver = Path(__file__).with_name("msedgedriver.exe")
    if local_driver.exists():
        service = Service(str(local_driver))
        logger.info(f"✅ 使用本地 EdgeDriver: {local_driver}")
        driver = webdriver.Edge(service=service, options=options)
    else:
        logger.warning("⚠️ 未找到本地 EdgeDriver，改为由 Selenium 自动处理驱动")
        driver = webdriver.Edge(options=options)
    driver.set_page_load_timeout(60)
    driver.implicitly_wait(2)

    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    driver.maximize_window()
    return driver


def save_cookie(driver, path):
    """保存登录 Cookie 到本地文件。"""
    path = Path(path)
    with path.open("w", encoding="utf-8") as f:
        json.dump(driver.get_cookies(), f, ensure_ascii=False, indent=2)
    logger.info(f"✅ 登录 Cookie 已保存到 {path}")


def load_cookie(driver, path):
    """从本地文件加载登录 Cookie。"""
    path = Path(path)
    if not path.exists():
        return False

    with path.open("r", encoding="utf-8") as f:
        cookies = json.load(f)

    for cookie in cookies:
        cookie = dict(cookie)
        cookie.pop("expiry", None)
        try:
            driver.add_cookie(cookie)
        except Exception as exc:
            logger.debug(f"跳过无效 Cookie: {exc}")

    logger.info("✅ 已加载本地登录 Cookie")
    return True


def find_element_in_shadow_dom(driver, selector, timeout=10):
    """通过 JS 递归搜索 Shadow DOM 中的元素。"""
    script = """
    function findInShadows(selector) {
        function search(root) {
            try {
                let el = root.querySelector(selector);
                if (el) return el;
            } catch (e) {}
            for (let child of root.querySelectorAll('*')) {
                if (child.shadowRoot) {
                    let found = search(child.shadowRoot);
                    if (found) return found;
                }
            }
            return null;
        }
        return search(document);
    }
    return findInShadows(arguments[0]);
    """

    try:
        return WebDriverWait(driver, timeout, poll_frequency=0.5).until(
            lambda d: d.execute_script(script, selector)
        )
    except TimeoutException:
        return None


def find_send_button_by_text(driver, text="发布", timeout=5):
    """通过按钮文本回退查找按钮。"""
    script = """
    function findButtonByText(text) {
        function search(root) {
            let buttons = root.querySelectorAll('button');
            for (let btn of buttons) {
                if (btn.textContent.trim() === text && !btn.disabled) {
                    return btn;
                }
            }
            for (let child of root.querySelectorAll('*')) {
                if (child.shadowRoot) {
                    let found = search(child.shadowRoot);
                    if (found) return found;
                }
            }
            return null;
        }
        return search(document);
    }
    return findButtonByText(arguments[0]);
    """

    try:
        return WebDriverWait(driver, timeout, poll_frequency=0.5).until(
            lambda d: d.execute_script(script, text)
        )
    except TimeoutException:
        return None


def wait_for_comment_box(driver):
    """滚动页面直至评论区出现。"""
    logger.info("🔄 模拟用户浏览页面...")
    for _ in range(random.randint(3, 6)):
        driver.execute_script(f"window.scrollBy(0, {random.randint(-200, 800)})")
        time.sleep(random.uniform(0.5, 2.0))

    for _ in range(12):
        if find_element_in_shadow_dom(driver, ".brt-editor", timeout=1):
            logger.info("✅ 评论区已加载完成")
            return True
        driver.execute_script("window.scrollBy(0, 400)")
        time.sleep(random.uniform(1.0, 2.0))

    logger.error("❌ 滚动多次仍未找到评论区")
    return False


def human_like_type(driver, element, text):
    """模拟人类逐字输入。"""
    logger.info(f"⌨️ 正在输入评论: {text}")
    element.click()
    time.sleep(random.uniform(0.3, 0.8))

    # 先清空输入框
    try:
        element.clear()
    except Exception:
        driver.execute_script("arguments[0].innerText = '';", element)
    time.sleep(random.uniform(0.2, 0.6))

    # 逐字输入
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(0.05, 0.2))
        if random.random() < 0.08:
            time.sleep(random.uniform(0.2, 0.6))

    driver.execute_script(
        """
        const events = ['input', 'change', 'blur', 'focus'];
        events.forEach(eventName => {
            arguments[0].dispatchEvent(new Event(eventName, { bubbles: true, composed: true }));
        });
        """,
        element,
    )
    time.sleep(random.uniform(0.8, 1.6))


def find_submit_button(driver):
    """优先使用官方类名，其次通过文本回退。"""
    send_button = find_element_in_shadow_dom(driver, ".comment-submit-btn", timeout=3)
    if send_button:
        return send_button

    send_button = find_element_in_shadow_dom(driver, "button[class*='submit']", timeout=3)
    if send_button:
        return send_button

    return find_send_button_by_text(driver, "发布")


def send_single_comment(driver):
    """发送单条评论的完整流程，带重试机制。"""
    for attempt in range(1, CONFIG["max_retries"] + 1):
        try:
            logger.info(f"📝 开始第 {attempt} 次尝试发送评论")
            driver.refresh()
            time.sleep(random.uniform(7, 12))

            if not wait_for_comment_box(driver):
                continue

            comment_input = find_element_in_shadow_dom(driver, ".brt-editor")
            if not comment_input:
                logger.warning("⚠️ 未找到评论输入框")
                continue

            comment_text = random.choice(CONFIG["comment_list"])
            human_like_type(driver, comment_input, comment_text)
            time.sleep(random.uniform(1.2, 2.6))

            send_button = find_submit_button(driver)
            if not send_button:
                logger.error("❌ 所有方式都未找到发送按钮")
                continue

            if driver.execute_script("return arguments[0].disabled;", send_button):
                logger.warning("⚠️ 发送按钮被禁用，可能是发送太频繁或需要验证码")
                time.sleep(60)
                continue

            driver.execute_script("arguments[0].click();", send_button)
            logger.info(f"🎉 评论发送成功: {comment_text}")
            return True

        except Exception as exc:
            logger.error(f"❌ 第 {attempt} 次尝试失败: {exc}")
            if attempt < CONFIG["max_retries"]:
                retry_wait = random.randint(10, 30)
                logger.info(f"⏳ {retry_wait} 秒后重试...")
                time.sleep(retry_wait)

    logger.error("💔 已达到最大重试次数，本条评论发送失败")
    return False


def main():
    setup_logging()
    logger.info("=" * 60)
    logger.info("🚀 B站自动化评论学习脚本（优化版）启动")
    logger.info("=" * 60)

    driver = None
    try:
        driver = init_edge_browser()
        driver.get(CONFIG["target_video_url"])
        time.sleep(5)

        # 尝试加载本地 Cookie
        if load_cookie(driver, COOKIE_PATH):
            driver.refresh()
            time.sleep(5)
            if find_element_in_shadow_dom(driver, ".brt-editor", timeout=5):
                logger.info("✅ Cookie 自动登录成功")
            else:
                logger.warning("⚠️ Cookie 已失效，请手动登录")
                input("📌 手动登录完成后，请按回车键继续...")
                save_cookie(driver, COOKIE_PATH)
        else:
            logger.info("📌 未找到本地 Cookie，请手动登录")
            input("📌 手动登录完成后，请按回车键继续...")
            save_cookie(driver, COOKIE_PATH)

        logger.info("✅ 登录验证通过，开始自动评论")
        logger.info(
            f"⏱️  发送间隔: {CONFIG['min_interval'] // 60} - {CONFIG['max_interval'] // 60} 分钟"
        )

        comment_count = 0
        start_time = datetime.now()

        while True:
            if send_single_comment(driver):
                comment_count += 1
                elapsed = datetime.now() - start_time
                logger.info(
                    f"📊 运行统计: 已发送 {comment_count} 条评论 | 运行时长: {str(elapsed).split('.')[0]}"
                )

            wait_time = random.randint(CONFIG["min_interval"], CONFIG["max_interval"])
            logger.info(f"\n⏳ 下一条评论将在 {wait_time // 60} 分 {wait_time % 60} 秒后发送")
            logger.info("-" * 60)

            # 等待期间每分钟输出一次剩余时间
            for remaining in range(wait_time, 0, -60):
                logger.info(f"剩余时间: {remaining // 60} 分 {remaining % 60} 秒")
                time.sleep(60)
            time.sleep(wait_time % 60)

    except KeyboardInterrupt:
        logger.info("\n🛑 脚本被用户手动终止")
    except Exception as exc:
        logger.critical(f"💥 脚本发生致命错误: {exc}", exc_info=True)
    finally:
        if driver:
            driver.quit()
            logger.info("🔌 Edge 浏览器已关闭")
        logger.info("=" * 60)
        logger.info("👋 脚本运行结束")
        logger.info("=" * 60)


if __name__ == "__main__":
    main()