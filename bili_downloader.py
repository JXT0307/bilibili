"""
B站视频批量检索 + 下载工具
====================================
功能：
  1. 搜索关键词，列出相关视频（带编号）
  2. 输入编号选择下载
  3. 支持批量下载（如 1,3,5-8）
  4. 保存到E盘指定文件夹

使用：
  python bili_downloader.py
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

# ====================== 配置 ======================
SAVE_DIR = Path(r"E:\B站下载视频")
SAVE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com",
}

SEARCH_URL = "https://api.bilibili.com/x/web-interface/search/type"
VIDEO_INFO_URL = "https://api.bilibili.com/x/web-interface/view"


# ====================== B站API搜索 ======================
def search_videos(keyword: str, page: int = 1, page_size: int = 20):
    """用Playwright搜索B站视频（模拟真人操作，不会被风控）"""
    try:
        import asyncio
        from playwright.async_api import async_playwright

        async def _do_search():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/128.0.0.0 Safari/537.36"
                    ),
                    locale="zh-CN",
                )

                # 加载持久化Cookie
                cookie_file = Path("D:/test/bili_cookies.json")
                if cookie_file.exists():
                    cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
                    await context.add_cookies(cookies)

                page = await context.new_page()

                # 搜索页
                search_url = f"https://search.bilibili.com/all?keyword={quote(keyword)}&from_source=webtop_search"
                await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)

                # 提取搜索结果
                videos = await page.evaluate("""
                    () => {
                        const results = [];
                        for (const item of document.querySelectorAll('.video-list .bili-video-card')) {
                            const link = item.querySelector('a[href*="/video/"]');
                            if (!link) continue;
                            const href = link.getAttribute('href') || '';
                            const bvidMatch = href.match(/BV\\w+/);
                            if (!bvidMatch) continue;

                            const titleEl = item.querySelector('.bili-video-card__title, .title');
                            const authorEl = item.querySelector('.bili-video-card__author, .up-name, [title]');
                            const playEl = item.querySelector('.bili-video-card__stats--left, .play-count');
                            const durationEl = item.querySelector('.bili-video-card__duration, .duration');

                            results.push({
                                bvid: bvidMatch[0],
                                title: titleEl ? (titleEl.textContent || titleEl.getAttribute('title') || '').trim() : '未知',
                                author: authorEl ? (authorEl.textContent || authorEl.getAttribute('title') || '').trim() : '未知',
                                play: playEl ? playEl.textContent.trim() : '',
                                duration: durationEl ? durationEl.textContent.trim() : '',
                            });
                        }
                        return results;
                    }
                """)

                # 也搜一下全站搜索结果
                if not videos:
                    videos = await page.evaluate("""
                        () => {
                            const results = [];
                            for (const item of document.querySelectorAll('.search-result')) {
                                const link = item.querySelector('a[href*="/video/"]');
                                if (!link) continue;
                                const href = link.getAttribute('href') || '';
                                const bvidMatch = href.match(/BV\\w+/);
                                if (!bvidMatch) continue;

                                const titleEl = item.querySelector('.title, .search-title');
                                const authorEl = item.querySelector('.up-name, .search-username');
                                const playEl = item.querySelector('.play-count, .search-play');
                                const durationEl = item.querySelector('.duration, .search-duration');

                                results.push({
                                    bvid: bvidMatch[0],
                                    title: titleEl ? titleEl.textContent.trim() : '未知',
                                    author: authorEl ? authorEl.textContent.trim() : '未知',
                                    play: playEl ? playEl.textContent.trim() : '',
                                    duration: durationEl ? durationEl.textContent.trim() : '',
                                });
                            }
                            return results;
                        }
                    """)

                # 如果DOM解析不到，从页面script JSON里抓
                if not videos:
                    videos = await page.evaluate("""
                        () => {
                            const results = [];
                            for (const script of document.querySelectorAll('script')) {
                                const text = script.textContent || '';
                                if (text.includes('window.__INITIAL_STATE__')) {
                                    try {
                                        const jsonStr = text.replace('window.__INITIAL_STATE__=', '').split(';(function()')[0];
                                        const data = JSON.parse(jsonStr);
                                        const vlist = data?.videoData?.result || data?.searchResult?.result || [];
                                        for (const v of vlist) {
                                            results.push({
                                                bvid: v.bvid || '',
                                                title: v.title?.replace(/<[^>]+>/g, '') || '',
                                                author: v.author || v?.up?.name || '',
                                                play: (v.play || 0) >= 10000 ? (v.play/10000).toFixed(1)+'万' : String(v.play || 0),
                                                duration: v.duration || '',
                                            });
                                        }
                                    } catch(e) {}
                                }
                            }
                            return results;
                        }
                    """)

                await browser.close()
                return videos

        videos = asyncio.run(_do_search())
        return videos, len(videos)

    except Exception as e:
        print(f"❌ 搜索失败: {e}")
        import traceback
        traceback.print_exc()
        return [], 0


def _clean_title(title: str) -> str:
    """清理标题中的HTML标签"""
    title = re.sub(r"<[^>]+>", "", title)
    return title.strip()


def _format_play(play: int) -> str:
    if play >= 10000:
        return f"{play / 10000:.1f}万"
    return str(play)


def _format_duration(duration):
    """格式化时长"""
    if isinstance(duration, int):
        m, s = divmod(duration, 60)
        return f"{m}:{s:02d}"
    return str(duration)


# ====================== 下载视频 ======================
def check_ffmpeg():
    """检查是否安装了ffmpeg"""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def download_video(bvid: str, title: str, author: str):
    """用 yt-dlp 或 you-get 下载单个视频"""
    url = f"https://www.bilibili.com/video/{bvid}"

    # 先检查有没有安装下载工具
    tool = None
    try:
        subprocess.run(["you-get", "--version"], capture_output=True, timeout=5)
        tool = "you-get"
    except Exception:
        pass

    if not tool:
        try:
            subprocess.run(["yt-dlp", "--version"], capture_output=True, timeout=5)
            tool = "yt-dlp"
        except Exception:
            pass

    if not tool:
        # 用 python 包 requests + 解析接口下载
        return _download_with_requests(bvid, title, author, url)

    # 用工具下载
    safe_title = _safe_filename(title)
    save_path = SAVE_DIR / f"{safe_title}"
    save_path.mkdir(parents=True, exist_ok=True)

    print(f"\n⬇️  正在下载: {title}")
    print(f"  作者: {author}")
    print(f"  链接: {url}")

    try:
        if tool == "you-get":
            cmd = [
                "you-get",
                "-o", str(save_path),
                url,
            ]
        else:
            cmd = [
                "yt-dlp",
                "-o", str(save_path / "%(title)s.%(ext)s"),
                "--write-auto-subs",
                "--sub-langs", "zh-Hans,zh-CN,zh",
                url,
            ]

        print(f"  使用 {tool} 下载中...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            print(f"✅ 下载完成: {title}")
            return True
        else:
            print(f"⚠️ 下载异常: {result.stderr[:200]}")
            return False

    except subprocess.TimeoutExpired:
        print("⏰ 下载超时")
        return False
    except Exception as e:
        print(f"❌ 下载失败: {e}")
        return False


def _download_with_requests(bvid, title, author, url):
    """备用：用 requests 解析视频直链下载"""
    print("\n⚠️ 未安装 you-get 或 yt-dlp，使用备用下载方式...")

    # 获取视频播放信息
    api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    try:
        resp = requests.get(api_url, headers=HEADERS, timeout=15)
        data = resp.json()
        if data.get("code") != 0:
            print(f"❌ 获取视频信息失败: {data.get('message')}")
            return False

        # 获取视频标题
        v_title = data.get("data", {}).get("title", title)
        cid = data.get("data", {}).get("cid")

        if not cid:
            print("❌ 无法获取视频CID")
            return False

        # 获取播放地址（需要Cookie，可能被限制）
        play_url = f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn=80&fnval=0"
        play_resp = requests.get(play_url, headers=HEADERS, timeout=15)
        play_data = play_resp.json()

        if play_data.get("code") != 0:
            print(f"❌ 获取播放地址失败，建议安装 you-get 或 yt-dlp")
            print("  pip install you-get")
            return False

        durl = play_data.get("data", {}).get("durl", [])
        if not durl:
            print("❌ 未找到可下载的视频流")
            return False

        video_url = durl[0].get("url", "")
        if not video_url:
            print("❌ 视频URL为空")
            return False

        # 下载
        safe_title = _safe_filename(v_title)
        filepath = SAVE_DIR / f"{safe_title}.mp4"

        print(f"  正在下载: {safe_title}.mp4")
        video_resp = requests.get(video_url, headers=HEADERS, stream=True, timeout=300)
        total = int(video_resp.headers.get("content-length", 0))
        downloaded = 0

        with open(filepath, "wb") as f:
            for chunk in video_resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded * 100 // total
                        print(f"\r  进度: {pct}%", end="", flush=True)

        print(f"\n✅ 下载完成: {filepath}")
        return True

    except Exception as e:
        print(f"❌ 下载失败: {e}")
        return False


def _safe_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name.strip()[:100]


# ====================== 交互界面 ======================
def print_videos(videos):
    """打印视频列表"""
    print(f"\n{'='*80}")
    print(f"{'序号':>4} │ {'标题':<45} │ {'UP主':<12} │ {'播放':<8} │ {'时长':<6}")
    print(f"{'-'*4}─┼─{'-'*45}─┼─{'-'*12}─┼─{'-'*8}─┼─{'-'*6}")
    for i, v in enumerate(videos, 1):
        title = v["title"][:42] + ".." if len(v["title"]) > 44 else v["title"]
        print(f"{i:>4} │ {title:<45} │ {v['author']:<12} │ {v['play']:<8} │ {v['duration']:<6}")
    print(f"{'='*80}")


def parse_input(inp: str, max_num: int) -> list:
    """解析用户输入，支持 1,3,5-8 格式"""
    selected = set()
    parts = inp.replace("，", ",").split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                a, b = part.split("-")
                for n in range(int(a), int(b) + 1):
                    if 1 <= n <= max_num:
                        selected.add(n)
            except ValueError:
                pass
        else:
            try:
                n = int(part)
                if 1 <= n <= max_num:
                    selected.add(n)
            except ValueError:
                pass
    return sorted(selected)


def main():
    # 设置编码
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    print("=" * 60)
    print("🎬 B站视频批量检索下载工具")
    print("=" * 60)
    print(f"📁 保存路径: {SAVE_DIR}")
    print()

    # 检查下载工具
    has_you_get = False
    has_ytdlp = False
    try:
        subprocess.run(["you-get", "--version"], capture_output=True, timeout=3)
        has_you_get = True
    except Exception:
        pass
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, timeout=3)
        has_ytdlp = True
    except Exception:
        pass

    print(f"🔧 下载工具: ", end="")
    tools = []
    if has_you_get:
        tools.append("you-get ✅")
    if has_ytdlp:
        tools.append("yt-dlp ✅")
    if not tools:
        tools.append("内置(较慢) ⚠️")
    print(" + ".join(tools))
    print()

    while True:
        # 搜索
        keyword = input("🔍 请输入搜索关键词 (输入 q 退出): ").strip()
        if keyword.lower() in ("q", "quit", "exit"):
            print("👋 再见~")
            break

        if not keyword:
            continue

        # 搜索结果
        videos, total = search_videos(keyword)
        if not videos:
            print("⚠️ 未找到相关视频，换个关键词试试")
            continue

        print(f"\n📊 共找到 {total} 个结果，显示前 {len(videos)} 个:")
        print_videos(videos)

        # 选择下载
        while True:
            inp = input(
                f"\n⬇️  输入编号下载 (如 1,3,5-8 | n翻页 | b返回搜索 | q退出): "
            ).strip()

            if inp.lower() == "q":
                return
            elif inp.lower() == "b":
                break
            elif inp.lower() == "n":
                break  # 暂时只显示一页
            else:
                indices = parse_input(inp, len(videos))
                if not indices:
                    print("❌ 无效输入，请重新输入")
                    continue

                print(f"\n📥 准备下载 {len(indices)} 个视频...")
                for idx in indices:
                    v = videos[idx - 1]
                    print(f"\n{'─'*50}")
                    success = download_video(
                        v["bvid"], v["title"], v["author"]
                    )
                    if success:
                        print(f"  ✅ {v['title']} 下载成功！")
                    # 每下一个间隔3秒，避免触发风控
                    if idx != indices[-1]:
                        time.sleep(3)

                print(f"\n✅ 全部下载完成！文件保存在: {SAVE_DIR}")

                # 下载完后问要不要继续
                cont = input("\n继续下载其他视频？(y/n): ").strip().lower()
                if cont != "y":
                    print("👋 再见~")
                    return
                break


if __name__ == "__main__":
    main()
