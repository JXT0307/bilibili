import logging
import random
import time
from pathlib import Path
from urllib.parse import urlparse
from enum import Enum, auto

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, WebDriverException
# ====================== 全局配置区 ======================
CONFIG = {
    "up_uid": "176347936",
    "target_comment_uid": "2985199",
    "report_reason": "其他",
    "max_dynamic_count": 15,
    "max_scroll_comment": 20,
    "min_wait": 2,
    "max_wait": 5,
    "log_file": "bili_report.log",
    "keep_browser_open": True,
    "comment_item_sel": (
        ".reply-item, .comment-wrap .reply-item, .bili-comment-container .reply-item, "
        "[class*='comment'] [class*='reply-item'], [data-comment-id]"
    ),
    "card_selectors": [
        '.bili-dyn-list__item', '.dyn-item', '[class*="dyn-item"]',
        '[data-did]', '[data-dynamic-id]', '[data-oid]', '[data-id]'
    ],
    "dynamic_url_js": """
function normalize(url){
    if(!url) return '';
    if(url.startsWith('//')) return 'https:' + url;
    if(url.startsWith('/')) return 'https://www.bilibili.com' + url;
    return url;
}
function isDynamicUrl(href){
    if(!href) return false;
    try{
        const u = new URL(href, location.href);
        const p = u.pathname || '';
        const isT = u.hostname === 't.bilibili.com' && /^\/\\d+(\\/)?$/.test(p);
        const isOpus = (u.hostname === 'www.bilibili.com' || u.hostname === 'm.bilibili.com')
            && (/^\/opus\/\\d+/.test(p) || /^\/t\\.bilibili\\.com\/\\d+/.test(p));
        const isSpace = u.hostname === 'space.bilibili.com'
            && /^\/\\d+\/dynamic\/?$/.test(p);
        return isT || isOpus || isSpace;
    }catch(e){ return false; }
}
function hasDynamicMarker(el){
    if(!el) return false;
    const text = (el.innerText || '').toLowerCase();
    return text.includes('动态') || text.includes('分享') || text.includes('投稿');
}
"""
}

# 页面状态枚举
class PageStatus(Enum):
    NORMAL = auto()
    REDIRECT = auto()
    ERROR = auto()
    LOGIN = auto()
    # 页面状态枚举
# 新增：自定义流程终止异常，保证finally一定执行浏览器逻辑
class StopScriptException(Exception):
    """自定义异常：前置校验失败时抛出，确保进入finally处理浏览器"""
    pass

# ====================== 日志初始化 ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(CONFIG["log_file"], encoding="utf-8", mode="a"),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger(__name__)

# ====================== 基础工具函数 ======================
def init_edge_browser():
    """初始化Edge无痕防检测浏览器，移除冲突CDP命令，解决驱动频繁崩溃"""
    options = webdriver.EdgeOptions()
    # 恢复标准反检测配置，不会引发驱动崩溃，降低网站自动化识别
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,900")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--no-first-run")
    # eager加载策略：DOM就绪即放行，减少长时间阻塞驱动
    options.page_load_strategy = "eager"

    # 自定义浏览器UA
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0"
    )
    options.add_argument(f"--user-agent={ua}")

    driver_path = Path(__file__).with_name("msedgedriver.exe")
    driver = None
    if driver_path.exists():
        logger.info(f"✅ 使用本地 EdgeDriver: {driver_path}")
        try:
            driver = webdriver.Edge(service=Service(str(driver_path)), options=options)
        except Exception as e:
            logger.error(f"本地EdgeDriver启动失败：{e}，自动切换系统内置驱动")
            driver = webdriver.Edge(options=options)
    else:
        logger.warning("⚠️ 未检测到本地msedgedriver.exe，使用系统自动匹配驱动")
        driver = webdriver.Edge(options=options)

    try:
        driver.set_page_load_timeout(60)
        driver.implicitly_wait(2)
        # 已移除 Page.addScriptToEvaluateOnNewDocument CDP 命令，规避驱动底层兼容崩溃
        driver.maximize_window()
    except Exception as e:
        logger.warning(f"浏览器后置配置异常，不影响基础运行：{e}")
    return driver

def shadow_count(driver, selector):
    """递归穿透 Shadow DOM 统计目标元素数量"""
    js = """
function cnt(root, sel){
    let num = 0;
    try { num = root.querySelectorAll(sel).length; } catch(e) {}
    for(let c of root.querySelectorAll('*')){
        if(c.shadowRoot) num += cnt(c.shadowRoot, sel);
    }
    return num;
}
return cnt(document, arguments[0]);
"""
    return driver.execute_script(js, selector)

def get_page_status(driver):
    """统一识别页面状态：正常/跳转/错误/登录页"""
    try:
        current = driver.current_url.lower()
        # 优先判定登录页标识，避免因跳转至登录页而误判为异常跳转
        if any(token in current for token in ("login", "passport")):
            return PageStatus.LOGIN
        # 其次判定跳转页标识
        if any(token in current for token in ("account.bilibili.com", "member.bilibili.com", "/big", "?spm_id_from=", "/404", "/error")):
            return PageStatus.REDIRECT

        text = driver.execute_script("return document.body && document.body.innerText ? document.body.innerText : ''") or ""
        title = driver.execute_script("return document.title || ''") or ""
        combined = f"{title} {text}".lower()
        # 错误页面关键词
        if any(token in combined for token in ("页面不存在", "not found", "访问被拒绝", "请求异常", "系统繁忙")):
            return PageStatus.ERROR
        if "/404" in current or current.endswith("/404"):
            return PageStatus.ERROR
    except Exception:
        return PageStatus.ERROR
    return PageStatus.NORMAL

def safe_click(driver, element):
    """优先原生点击，失败自动降级JS点击"""
    try:
        element.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            return False

def wait_for_condition(driver, condition, timeout=10):
    """等待指定页面条件成立，超时返回None"""
    try:
        return WebDriverWait(driver, timeout).until(condition)
    except TimeoutException:
        return None

def random_sleep():
    """全局随机等待，取配置min/max区间"""
    time.sleep(random.uniform(CONFIG["min_wait"], CONFIG["max_wait"]))

def is_logged_in(driver):
    """检测当前页面是否存在有效B站登录Cookie"""
    status = get_page_status(driver)
    if status in (PageStatus.REDIRECT, PageStatus.ERROR):
        return False

    try:
        cookie_ok = driver.execute_script(
            "return /SESSDATA=|bili_jct=|DedeUserID=/.test(document.cookie);"
        )
        if cookie_ok and status == PageStatus.NORMAL:
            return True
    except Exception:
        pass

    try:
        return (
            driver.execute_script(
                "return !!document.querySelector('a[href*=\"/space.bilibili.com/\"]') || "
                "!!document.querySelector('[class*=\"user\"]');"
            )
            and status == PageStatus.NORMAL
        )
    except Exception:
        return False

def wait_for_login(driver, url, timeout=90):
    """循环等待用户扫码登录完成，自动处理跳转/错误页，优化扫码等待逻辑"""
    logger.info(f"正在访问页面检查登录状态: {url}")
    safe_get(driver, url, timeout=20)
    end = time.time() + timeout
    while time.time() < end:
        status = get_page_status(driver)

        if status == PageStatus.ERROR or status == PageStatus.REDIRECT:
            logger.warning(f"检测到异常页面：{driver.current_url}，尝试回到目标页")
            safe_get(driver, url, timeout=20)
            # 异常页面重载后固定等待5秒，完成页面渲染跳转
            time.sleep(20)
            continue

        if status == PageStatus.LOGIN:
            if is_logged_in(driver):
                logger.info("登录Cookie已生效，校验页面中")
            else:
                logger.info(f"当前处于登录页面，等待扫码完成，地址：{driver.current_url}")
                # 扫码页面固定5秒间隔，避免高频刷新打断扫码流程
                time.sleep(5)
                continue

        if is_logged_in(driver):
            if is_target_dynamic_page(driver, url):
                logger.info("✅ 登录完成，已定位目标UP动态主页")
                return True
            logger.info("✅ 检测到登录，但页面跳转，尝试切回动态页")
            force_open_target_page(driver, url)
            if is_target_dynamic_page(driver, url):
                return True
        # 已登录但页面不对，短间隔重试校验
        time.sleep(3)
    logger.warning("登录等待超时，未检测到有效登录状态")
    return False

def wait_for_page_ready(driver, timeout=15):
    """等待页面完全加载完成（document.readyState === complete）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if driver.execute_script(
                "return document.readyState === 'complete' && document.body !== null;"
            ):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

def safe_get(driver, url, timeout=30):
    """安全访问URL，失败自动重试一次"""
    last_error = None
    for attempt in range(2):
        try:
            driver.get(url)
            if wait_for_page_ready(driver, timeout=timeout):
                return True
        except Exception as exc:
            last_error = exc
            logger.warning(f"第 {attempt + 1} 次打开页面失败：{url}，异常：{exc}")
            random_sleep()
    if last_error:
        logger.error(f"页面访问彻底失败 {url} | {last_error}")
    return False

def open_dynamic_detail(driver, url):
    """打开单条动态详情页，自动转换旧t.bilibili链接为opus格式，预检测动态有效性，规避404/私密/已删除动态"""
    # 兜底修正：如果传入的仍是旧版 t.bilibili.com 链接，强制转为 opus 链接防止404
    try:
        parsed = urlparse(url)
        if parsed.hostname == 't.bilibili.com':
            dyn_id = parsed.path.strip('/')
            if dyn_id.isdigit():
                url = f"https://www.bilibili.com/opus/{dyn_id}"
                logger.info(f"检测到旧版动态链接，已自动转换为：{url}")
        # 新增：opus链接提取ID，预检测当前页面是否存在该动态卡片，提前过滤失效动态
        elif parsed.hostname == 'www.bilibili.com' and parsed.path.startswith('/opus/'):
            dyn_id = parsed.path.split('/')[-1]
            if dyn_id.isdigit():
                # 在当前页面DOM中查找是否存在对应data-id卡片，避免访问已删除/私密的动态
                exists = driver.execute_script(
                    f"return !!document.querySelector('[data-did=\"{dyn_id}\"]') || "
                    f"!!document.querySelector('[data-dynamic-id=\"{dyn_id}\"]') || "
                    f"!!document.querySelector('[data-oid=\"{dyn_id}\"]') || "
                    f"!!document.querySelector('[data-id=\"{dyn_id}\"]');"
                )
                if not exists:
                    logger.warning(f"⚠️ 动态ID {dyn_id} 在主页列表无对应卡片，判定为已删除/私密动态，直接跳过")
                    return False
    except Exception as e:
        logger.debug(f"动态ID预检测异常，继续尝试访问页面：{e}")

    logger.info(f"准备访问动态详情页：{url}")
    if not safe_get(driver, url, timeout=20):
        logger.warning(f"页面访问请求失败，跳过本条动态 {url}")
        return False
    random_sleep()
    wait_for_page_ready(driver, timeout=20)

    status = get_page_status(driver)
    if status in (PageStatus.ERROR, PageStatus.REDIRECT):
        logger.warning(
            f"⚠️ 动态页面404/异常跳转，当前地址：{driver.current_url}，原始链接：{url}"
        )
        return False
    logger.info(f"动态页面加载正常：{driver.current_url}")
    return True

def is_target_dynamic_page(driver, list_url):
    """校验当前页面严格等于目标UP主动态主页（匹配域名+路径）"""
    try:
        current = driver.current_url
    except Exception:
        return False
    if get_page_status(driver) == PageStatus.REDIRECT:
        return False

    current_parsed = urlparse(current)
    target_parsed = urlparse(list_url)
    if current_parsed.scheme != target_parsed.scheme:
        return False
    if current_parsed.netloc != target_parsed.netloc:
        return False
    if current_parsed.path.rstrip("/") != target_parsed.path.rstrip("/"):
        return False
    return True

def has_dynamic_feed(driver):
    """判断页面是否存在动态列表DOM结构"""
    try:
        dom_ok = driver.execute_script("""
        return !!document.querySelector('.bili-dyn-list__item, .dyn-item, [data-did], [data-dynamic-id]') &&
        !!document.querySelector('a[href*="/opus/"], a[href*="t.bilibili.com/"]');
        """)
    except Exception:
        dom_ok = False
    return dom_ok

def wait_for_dynamic_feed(driver, list_url, timeout=45):
    """循环等待页面加载出UP主动态列表，异常页面自动重定向"""
    end = time.time() + timeout
    while time.time() < end:
        current = driver.current_url
        status = get_page_status(driver)

        if is_target_dynamic_page(driver, list_url) and has_dynamic_feed(driver):
            logger.info(f"✅ 动态列表加载完成，页面地址：{current}")
            return True

        if status in (PageStatus.REDIRECT, PageStatus.ERROR):
            logger.warning(f"页面异常跳转，尝试恢复动态主页：{current}")
            safe_get(driver, list_url, timeout=20)
        else:
            logger.info(f"当前页面非目标动态页 {current}，强制重定向主页")
            force_open_target_page(driver, list_url)

        time.sleep(3)
        wait_for_page_ready(driver)
    logger.warning(f"⚠️ {timeout}秒内未加载出动态列表，当前页面：{driver.current_url}")
    return False

def force_open_target_page(driver, list_url):
    """多重方案强制切回UP动态主页，兼容各种页面跳转拦截"""
    for attempt in range(4):
        status = get_page_status(driver)
        if status in (PageStatus.REDIRECT, PageStatus.LOGIN):
            logger.warning(f"检测到跳转页面 {driver.current_url}，强制切回动态主页")

        # 方案1：直接get访问链接
        if safe_get(driver, list_url, timeout=20):
            driver.execute_script("window.location.replace(arguments[0]);", list_url)
            wait_for_page_ready(driver)
            random_sleep()

        # 方案2：点击页面内动态标签
        tab_selectors = [
            "a[href*='/dynamic']",
            "a[href$='/dynamic']",
            "a[title='动态']",
            "a[title*='动态']",
            "button[aria-label*='动态']",
            "div[title*='动态']",
            "a[href*='space.bilibili.com'][href*='/dynamic']",
        ]
        for selector in tab_selectors:
            try:
                tab = driver.find_element(By.CSS_SELECTOR, selector)
                if tab and tab.is_displayed():
                    safe_click(driver, tab)
                    time.sleep(2)
                    wait_for_page_ready(driver)
                    if is_target_dynamic_page(driver, list_url):
                        logger.info(f"✅ 第 {attempt + 1} 次成功切换至动态主页")
                        return True
            except Exception:
                continue

        if is_target_dynamic_page(driver, list_url):
            logger.info(f"✅ 第 {attempt + 1} 次页面已为目标动态主页")
            return True
        random_sleep()
    logger.warning("多次重试仍无法定位动态主页")
    return is_target_dynamic_page(driver, list_url)

# ====================== 动态列表采集函数 ======================
def load_dynamic_cards(driver, target_count):
    """滚动页面加载足够数量动态卡片，无新内容自动停止"""
    wait_for_page_ready(driver)
    last_cnt = 0
    same = 0
    for i in range(50):
        current_cnt = count_dynamic_cards(driver)
        logger.info(f"第 {i + 1} 次滚动，已加载 {current_cnt} 条动态卡片")

        if current_cnt >= target_count:
            logger.info(f"已达到采集上限 {target_count} 条，停止滚动")
            break
        if current_cnt == last_cnt:
            same += 1
            if same >= 4:
                logger.info("页面无更多动态，全部加载完成")
                break
        else:
            same = 0
            last_cnt = current_cnt

        # 滚动逻辑：下拉到底再回弹模拟真人
        driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")
        random_sleep()
        driver.execute_script("window.scrollBy(0, -200)")
        time.sleep(0.5)
        wait_for_page_ready(driver)
    return count_dynamic_cards(driver)

def count_dynamic_cards(driver):
    """JS递归统计页面有效动态卡片数量，复用全局动态匹配逻辑"""
    js = CONFIG["dynamic_url_js"] + """
function isCardLike(el){
    if(!el || typeof el.querySelectorAll !== 'function') return false;
    const attrs = [el.getAttribute('data-did'), el.getAttribute('data-dynamic-id'), el.getAttribute('data-oid'), el.getAttribute('data-id')].filter(Boolean);
    if(attrs.some(v => /^\\d+$/.test(v))) return true;
    if(el.querySelectorAll('a[href]').length){
        for(const a of el.querySelectorAll('a[href]')){
            if(isDynamicUrl(normalize(a.getAttribute('href'))) || hasDynamicMarker(a)) return true;
        }
    }
    return hasDynamicMarker(el);
}
function count(root, selectors){
    const seen = new Set();
    let count = 0;
    function walk(node){
        if(!node || !node.querySelectorAll) return;
        for(const sel of selectors){
            try{
                const els = node.querySelectorAll(sel);
                for(const el of els){
                    if(isCardLike(el) && !seen.has(el)){ seen.add(el); count += 1; }
                }
            }catch(e){}
        }
        try{
            const links = node.querySelectorAll('a[href]');
            for(const a of links){
                if(isDynamicUrl(normalize(a.getAttribute('href'))) && !seen.has(a)){ seen.add(a); count += 1; }
            }
        }catch(e){}
        for(const el of node.querySelectorAll('*')){
            if(el.shadowRoot) walk(el.shadowRoot);
        }
    }
    walk(root);
    return count;
}
return count(document, arguments[0]);
"""
    return int(driver.execute_script(js, CONFIG["card_selectors"]) or 0)

def get_dynamic_urls(driver, total):
    """提取所有动态链接，自动去重，最多返回total条"""
    js = CONFIG["dynamic_url_js"] + """
function addIfDynamic(urls, seen, value){
    if(!value) return;
    const href = normalize(value);
    if(!isDynamicUrl(href) || seen.has(href)) return;
    seen.add(href);
    urls.push(href);
}
function isCardCandidate(el){
    if(!el || !el.querySelectorAll) return false;
    const attrs = [el.getAttribute('data-did'), el.getAttribute('data-dynamic-id'), el.getAttribute('data-oid'), el.getAttribute('data-id')].filter(Boolean);
    if(attrs.some(v => /^\\d+$/.test(v))) return true;
    for(const a of el.querySelectorAll('a[href], [data-href]')){
        const href = a.getAttribute('href') || a.getAttribute('data-href');
        if(isDynamicUrl(normalize(href))) return true;
    }
    return hasDynamicMarker(el);
}
function collect(root, maxNum, selectors) {
    const urls = [];
    const seen = new Set();

    function addCard(card) {
        if (!card || !isCardCandidate(card)) return;
        const rawId = card.getAttribute('data-did') || card.getAttribute('data-dynamic-id') || card.getAttribute('data-oid') || card.getAttribute('data-id') || card.dataset.did || card.dataset.dynamicId || card.dataset.oid || card.dataset.id;
        if (rawId && /^\\d+$/.test(String(rawId))) {
            // 强制将旧版 t.bilibili.com 链接转换为 opus 格式，防止404跳转
            // 新增：校验该卡片是否包含有效的动态链接，避免提取无效ID
            const hasValidLink = card.querySelector('a[href*="/opus/"]') || card.querySelector('a[href*="t.bilibili.com/"]');
            if(hasValidLink){
                 addIfDynamic(urls, seen, 'https://www.bilibili.com/opus/' + rawId);
                 return;
            }
        }
        for (const a of card.querySelectorAll('a[href], [data-href]')) {
            const href = a.getAttribute('href') || a.getAttribute('data-href');
            addIfDynamic(urls, seen, href);
            if (urls.length >= maxNum) return;
        }
    }

    function walk(node) {
        if (!node || !node.querySelectorAll || urls.length >= maxNum) return;
        for (const sel of selectors) {
            try {
                const items = node.querySelectorAll(sel);
                for (let i = 0; i < items.length && urls.length < maxNum; i++) addCard(items[i]);
            } catch (e) {}
        }
        for (const el of node.querySelectorAll('a[href], [data-href]')) {
            const href = el.getAttribute('href') || el.getAttribute('data-href');
            addIfDynamic(urls, seen, href);
            if (urls.length >= maxNum) break;
        }
        for (const el of node.querySelectorAll('*')) {
            if (el.shadowRoot) walk(el.shadowRoot);
            if (urls.length >= maxNum) break;
        }
    }
    walk(root);
    return urls;
}
return collect(document, arguments[0], arguments[1]);
"""
    urls = driver.execute_script(js, total, CONFIG["card_selectors"])
    # 最终去重兜底
    unique_urls = list(dict.fromkeys(urls))
    logger.info(f"去重后有效动态链接数量：{len(unique_urls)}")
    for idx, url in enumerate(unique_urls, 1):
        logger.info(f"  {idx}. {url}")
    return unique_urls

# ====================== 评论查找与举报逻辑 ======================
def find_target_comment(driver, target_uid):
    """滚动加载评论，递归穿透Shadow DOM查找指定UID用户评论DOM节点"""
    logger.info("开始滚动加载评论容器，寻找目标用户评论")
    if get_page_status(driver) == PageStatus.ERROR:
        logger.warning("当前动态页面异常，跳过评论检索")
        return None

    driver.execute_script("window.scrollTo(0, Math.max(document.body.scrollHeight * 0.4, 800));")
    time.sleep(2)

    comment_item_sel = CONFIG["comment_item_sel"]
    last_cnt = 0
    same = 0

    # 循环滚动加载评论
    for i in range(CONFIG["max_scroll_comment"]):
        driver.execute_script("window.scrollBy(0, 800)")
        time.sleep(random.uniform(1.2, 2.0))
        curr = shadow_count(driver, comment_item_sel)

        if i % 3 == 0:
            logger.info(f"评论滚动第{i+1}轮，已加载{curr}条评论")
        if curr == last_cnt:
            same += 1
            if same >= 3:
                logger.info("评论区已加载完毕，无更多内容")
                break
        else:
            same = 0
            last_cnt = curr

    # JS递归查找目标评论
    find_js = """
function findComment(root, targetMid, itemSel){
    try {
        const items = root.querySelectorAll(itemSel);
        for(let item of items){
            const attrs = [item.dataset.mid, item.dataset.userId, item.dataset.commentUid, item.getAttribute('data-user-id'), item.getAttribute('data-userid'), item.getAttribute('data-mid')];
            if(attrs.some(v => v === targetMid)) return item;
            const avatar = item.querySelector('a[href*="/space.bilibili.com/"]');
            if(avatar){
                const match = avatar.href.match(/space\\.bilibili\\.com\\/(\\d+)/);
                if(match && match[1] === targetMid) return item;
            }
            const text = (item.innerText || '');
            if(text.includes(targetMid)) return item;
        }
    } catch(e) {}
    for(let c of root.querySelectorAll('*')){
        if(c.shadowRoot){
            const res = findComment(c.shadowRoot, targetMid, itemSel);
            if(res) return res;
        }
    }
    return null;
}
return findComment(document, arguments[0], arguments[1]);
"""
    comment_node = driver.execute_script(find_js, str(target_uid), comment_item_sel)
    if comment_node:
        logger.info(f"✅ 成功定位UID {target_uid} 的评论元素")
        return comment_node
    logger.info(f"本动态下未找到UID {target_uid} 的评论")
    return None

def execute_report(driver, comment_node, reason):
    """执行完整举报流程：更多按钮→举报→选择理由→提交，全步骤加异常捕获"""
    try:
        # 滚动评论到视图中央
        driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});", comment_node)
        time.sleep(1.5)

        # 1. 点击更多按钮
        more_js = """
function findMore(root){
    const selectors = ['.more-btn', '.icon-more', '[aria-label="更多"]', '[title="更多"]', 'button[aria-label="更多"]'];
    for(const sel of selectors){
        try {
            const els = root.querySelectorAll(sel);
            if(els.length){ els[0].click(); return true; }
        } catch(e) {}
    }
    for(let c of root.querySelectorAll('*')){
        if(c.shadowRoot && findMore(c.shadowRoot)) return true;
    }
    return false;
}
return findMore(document);
"""
        if not driver.execute_script(more_js):
            logger.error("未找到评论「更多」操作按钮，举报终止")
            return False
        time.sleep(1.0)

        # 2. 点击举报选项
        report_js = """
function findText(root, text){
    try {
        for(let el of root.querySelectorAll('*')){
            if(el.innerText && el.innerText.trim() === text){ el.click(); return true; }
        }
    } catch(e) {}
    for(let c of root.querySelectorAll('*')){
        if(c.shadowRoot && findText(c.shadowRoot, text)) return true;
    }
    return false;
}
return findText(document, '举报');
"""
        if not driver.execute_script(report_js):
            logger.error("弹窗内未找到「举报」选项，举报终止")
            return False
        time.sleep(1.5)

        # 3. 选择举报理由
        reason_js = """
function selectReason(root, txt){
    try {
        for(let el of root.querySelectorAll('*')){
            if(el.innerText && el.innerText.includes(txt)){ el.click(); return true; }
        }
    } catch(e) {}
    for(let c of root.querySelectorAll('*')){
        if(c.shadowRoot && selectReason(c.shadowRoot, txt)) return true;
    }
    return false;
}
return selectReason(document, arguments[0]);
"""
        if not driver.execute_script(reason_js, reason):
            logger.error(f"举报弹窗未匹配理由：{reason}，举报终止")
            return False
        time.sleep(1.0)

        # 4. 点击提交按钮
        submit_js = """
function submit(root){
    try {
        for(let btn of root.querySelectorAll('button')){
            if(btn.innerText && btn.innerText.trim() === '提交'){ btn.click(); return true; }
        }
    } catch(e) {}
    for(let c of root.querySelectorAll('*')){
        if(c.shadowRoot && submit(c.shadowRoot)) return true;
    }
    return false;
}
return submit(document);
"""
        if not driver.execute_script(submit_js):
            logger.error("未找到举报「提交」按钮，举报终止")
            return False
        time.sleep(1.0)

        logger.info(f"🎉 举报提交完成，举报理由：{reason}")
        return True
    except Exception as exc:
        logger.error(f"举报流程出现异常：{exc}", exc_info=False)
        # 异常时截图留存（可选）
        try:
            driver.save_screenshot("report_error_screenshot.png")
            logger.info("异常截图已保存至 report_error_screenshot.png")
        except Exception:
            pass
        return False

# ====================== 主执行入口 ======================
# ====================== 主执行入口 ======================
def main():
    logger.info("=" * 70)
    logger.info("🚀 B站评论自动举报脚本（优化稳定版）")
    logger.info(
        f"目标UP UID:{CONFIG['up_uid']} | 待举报评论UID:{CONFIG['target_comment_uid']} | 举报理由:{CONFIG['report_reason']}"
    )
    logger.info("=" * 70)

    driver = None
    success_count = 0
    list_url = f"https://space.bilibili.com/{CONFIG['up_uid']}/dynamic"

    try:
        # 初始化浏览器
        driver = init_edge_browser()
        # 等待登录校验
        if not wait_for_login(driver, list_url):
            logger.warning("登录校验失败，脚本退出")
            raise StopScriptException("登录校验失败")
        # 强制切回动态主页
        if not force_open_target_page(driver, list_url):
            logger.warning("无法进入UP动态主页，脚本退出")
            raise StopScriptException("无法进入UP动态主页")
        wait_for_page_ready(driver)
        # 等待动态列表加载完成
        if not wait_for_dynamic_feed(driver, list_url):
            logger.error("动态列表加载失败，终止任务")
            raise StopScriptException("动态列表加载失败")

        # 滚动采集动态
        total_dyn = load_dynamic_cards(driver, CONFIG["max_dynamic_count"])
        dyn_urls = get_dynamic_urls(driver, total_dyn)

        # 第一次抓取为空，刷新重试一次
        if not dyn_urls:
            logger.warning("首次未抓取到动态链接，刷新页面重试")
            driver.refresh()
            time.sleep(5)
            wait_for_page_ready(driver)
            total_dyn = load_dynamic_cards(driver, CONFIG["max_dynamic_count"])
            dyn_urls = get_dynamic_urls(driver, total_dyn)

        if not dyn_urls:
            logger.error("始终未获取有效动态链接，请检查页面/登录状态")
            raise StopScriptException("始终未获取有效动态链接")

        # 逐条处理动态
        for idx, url in enumerate(dyn_urls, 1):
            logger.info(f"\n===== 处理第 {idx}/{len(dyn_urls)} 条动态：{url} =====")
            if not open_dynamic_detail(driver, url):
                logger.warning(f"动态页面失效，跳过本条：{url}")
                continue
            time.sleep(random.uniform(3, 5))

            # 查找目标评论并举报
            comment = find_target_comment(driver, CONFIG["target_comment_uid"])
            if comment:
                if execute_report(driver, comment, CONFIG["report_reason"]):
                    success_count += 1
            else:
                logger.info("当前动态无目标用户评论，直接跳过")

            # 每条动态间隔20~40秒防风控
            wait = random.randint(20, 40)
            logger.info(f"冷却 {wait} 秒后处理下一条动态")
            time.sleep(wait)

        # 任务正常跑完统计
        logger.info(f"\n===== 全部任务执行完毕 =====")
        logger.info(f"共遍历动态：{len(dyn_urls)} 条 | 成功举报评论：{success_count} 条")

    except KeyboardInterrupt:
        logger.info("用户手动终止脚本运行")
    except WebDriverException as driver_exc:
        logger.critical(f"浏览器底层驱动EdgeDriver崩溃异常：{driver_exc}")
        raise StopScriptException("浏览器驱动崩溃，终止任务")
    except StopScriptException as stop_exc:
        logger.warning(f"脚本前置校验失败，提前终止：{stop_exc}")
    except Exception as exc:
        logger.critical(f"脚本致命异常终止：{exc}", exc_info=True)
    finally:
        # 无论任何提前退出，一定会执行浏览器保留/关闭逻辑
        if driver:
            if CONFIG.get("keep_browser_open", False):
                logger.info("配置保留浏览器窗口，请手动关闭")
            else:
                try:
                    driver.quit()
                except Exception:
                    pass
                logger.info("浏览器已关闭，脚本完全退出")

if __name__ == "__main__":
    main()