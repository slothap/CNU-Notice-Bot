import requests
from bs4 import BeautifulSoup
import os
import time
import json
import re
import urllib3
import random
import logging   
import sys       
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

load_dotenv()

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


logging.basicConfig(
    stream=sys.stdout, 
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


MAX_RETRIES = 3
RETRY_DELAY = 60

DORM_WEBHOOK = os.environ.get("dorm_WEBHOOK")
MONITOR_WEBHOOK = os.environ.get("MONITOR_WEBHOOK")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
DATA_FILE = os.path.join(DATA_DIR, "dorm_data.json")

TARGET_BOARDS = [
    {"id": "movein", "name": "입주/퇴거 공지", "url": "https://dorm.cnu.ac.kr/_prog/_board/?code=sub05_0501&site_dvs_cd=kr&menu_dvs_cd=030101"},
    {"id": "general", "name": "일반공지", "url": "https://dorm.cnu.ac.kr/_prog/_board/?code=sub03_0301&site_dvs_cd=kr&menu_dvs_cd=0302"},
    {"id": "work", "name": "작업공지", "url": "https://dorm.cnu.ac.kr/_prog/_board/?code=sub03_0302&site_dvs_cd=kr&menu_dvs_cd=0303"}
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Connection': 'keep-alive',
    'Referer': 'https://dorm.cnu.ac.kr/'
}

def get_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def extract_article_id(link):
    line = re.search(r'no=(\d+)', link)
    if line: return int(line.group(1))
    return 0

def send_post_discord(category, new_notices): 
    if not new_notices or not DORM_WEBHOOK: return

    count = len(new_notices)
    message = f"### 🛌 [{category}] 새 글 {count}건\n\n"
    
    for notice in new_notices:
        icon = "▶" if notice['is_top'] else "▷"
        message += f"{icon} [{notice['title']}](<{notice['link']}>)\n"
        
    try:
        requests.post(DORM_WEBHOOK, json={"content": message}, timeout=5)
        logger.info(f"[전송 완료] {category} - {count}건") 
    except Exception as e:
        logger.error(f"[전송 실패] {e}")

def send_simple_error_log(error_msg=None, is_fatal=False):
    if not MONITOR_WEBHOOK: return

    now = time.strftime('%Y-%m-%d %H:%M:%S')
    title = "🚨 **[생활관 봇 치명적 오류]**" if is_fatal else "⚠️ **[생활관 봇 단순 경고]**"
    
    content = f"{title}\n시간: {now}\n"
    if error_msg: content += f"에러: ```{error_msg}```"
    
    try: requests.post(MONITOR_WEBHOOK, json={"content": content}, timeout=5)
    except: pass

def check_board(session, board_info, saved_data):
    board_id = board_info["id"]
    board_name = board_info["name"]
    url = board_info["url"]

    logger.info(f"[{board_name}] 분석 중...") 

    time.sleep(random.uniform(5, 10))

    response = session.get(url, headers=HEADERS, verify=False, timeout=30)
    response.encoding = 'utf-8'
    soup = BeautifulSoup(response.text, 'html.parser')
    rows = soup.select('tbody > tr')
    
    if not rows:
        raise Exception(f"게시글 없음 - HTML 구조 변경 or 차단")

    last_id = saved_data.get(board_id, 0)
    new_notices = []
    max_id = last_id

    base_url_board = "https://dorm.cnu.ac.kr/_prog/_board/"
    base_url_root = "https://dorm.cnu.ac.kr"

    for row in rows:
        title_td = row.select_one('td.title')
        if not title_td: continue
        
        a_tag = title_td.select_one('a')
        if not a_tag: continue
        
        title = a_tag.get('title') or a_tag.text
        title = title.strip()

        href = a_tag.get('href')
        if not href: continue

        board_path = "/_prog/_board/"
        base_url_root = "https://dorm.cnu.ac.kr"

        if href.startswith('http'):
            link = href
        elif href.startswith('./'):
            link = base_url_root + board_path + href.replace('./', '', 1)
        elif href.startswith('/'):
            link = base_url_root + href
        elif href.startswith('?'):
            link = base_url_root + board_path + href
        else:
            link = base_url_root + board_path + href.lstrip('/')

        article_id = extract_article_id(link)
        if article_id == 0: continue

        td_num = row.select_one('td.num')
        is_top = "공지" in td_num.get_text() if td_num else False

        if article_id > last_id:
            new_notices.append({
                "id": article_id, "title": title, "link": link, "is_top": is_top
            })
            if article_id > max_id: max_id = article_id

    if last_id == 0 and max_id > 0:
        logger.info(f"[{board_name}] 최초 실행 - 기준점(ID: {max_id})만 설정")
        saved_data[board_id] = max_id
        return True

    if new_notices:
        new_notices.sort(key=lambda x: x['id'])
        send_post_discord(board_name, new_notices)
        saved_data[board_id] = max_id
        return True

    return False

def run_bot():
    logger.info("━" * 20)
    logger.info("기숙사 공지봇 검사 시작")

    try:
        saved_data = {}

        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f: saved_data = json.load(f)
            except json.JSONDecodeError as e:
                logger.error(f"데이터 파일 파싱 오류: {e}") 
            except Exception as e:
                logger.error(f"데이터 파일 읽기 오류: {e}") 
        
        with get_session() as session:
            changes = False
            for board in TARGET_BOARDS:
                board_success = False
                
                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        if check_board(session, board, saved_data):
                            changes = True
                        board_success = True
                        break 
                    except Exception as e:
                        logger.warning(f"[{board['name']}] 실패 ({attempt}/{MAX_RETRIES}): {e}") 
                        if attempt < MAX_RETRIES:
                            time.sleep(RETRY_DELAY)
                
                if not board_success:
                    send_simple_error_log(f"[{board['name']}] 3회 접속 실패", is_fatal=True)

            if changes:
                with open(DATA_FILE, "w", encoding="utf-8") as f:
                    json.dump(saved_data, f, ensure_ascii=False, indent=4)
                logger.info("데이터 저장 완료") 
            else:
                logger.info("변동 사항 없음") 
                
        logger.info("프로그램 정상 종료") 

    except Exception as e:
        logger.error(f"치명적 오류: {e}") 
        send_simple_error_log(f"오류로 인한 MAIN 종료\n{e}", is_fatal=True)

if __name__ == "__main__":
    run_bot()