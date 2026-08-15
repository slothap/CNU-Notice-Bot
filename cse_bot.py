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

load_dotenv() #환경변수 로드

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MAX_RETRIES = 3
RETRY_DELAY = 60

CSE_WEBHOOK = os.environ.get("cse_WEBHOOK")
MONITOR_WEBHOOK = os.environ.get("MONITOR_WEBHOOK")

# JSON 파일 
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
# 신규 환경 실행
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
DATA_FILE = os.path.join(DATA_DIR, "cse_data.json")

# 게시판 목록
TARGET_BOARDS = [
    {"id": "bachelor", "name": "학사공지", "url": "https://computer.cnu.ac.kr/computer/notice/bachelor.do?articleLimit=30"},
    {"id": "general", "name": "교내일반소식", "url": "https://computer.cnu.ac.kr/computer/notice/notice.do?articleLimit=30"},
    {"id": "job", "name": "교외활동·인턴·취업", "url": "https://computer.cnu.ac.kr/computer/notice/job.do?articleLimit=30"},
    {"id": "project", "name": "사업단소식", "url": "https://computer.cnu.ac.kr/computer/notice/project.do?articleLimit=30"}
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Connection': 'keep-alive'
}

# 네트워크 세션(서버 에러 재시도 기능 추가)
def get_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

# 게시글 ID 추출기
def extract_article_id(link):
    line = re.search(r'articleNo=(\d+)', link) # 정규표현식으로 숫자 찾기
    if line: return int(line.group(1)) #정수로 변환
    return 0

# 게시물 알림 전송기
def send_post_discord(category, new_notices):
    if not new_notices or not CSE_WEBHOOK: return

    count = len(new_notices)    #글 개수
    message = f"### 📢 [{category}] 새 글 {count}건\n\n"
    
    # 개별 게시물 작업
    for notice in new_notices:
        icon = "▶" if notice['is_top'] else "▷" #상단 고정 공지
        message += f"{icon} [{notice['title']}](<{notice['link']}>)\n" 
    # 디스코드 전송
    try:
        requests.post(CSE_WEBHOOK, json={"content": message}, timeout=5)
        logger.info(f"[전송 완료] {category} - {count}건") 
    except Exception as e:
        logger.error(f"[전송 실패] {e}") 

# 오류 로그 전송기
def send_simple_error_log(error_msg=None, is_fatal=False):
    if not MONITOR_WEBHOOK: return

    now = time.strftime('%Y-%m-%d %H:%M:%S')

    title = "🚨 **[CSE 봇 치명적 오류]**" if is_fatal else "⚠️ **[컴융 봇 단순 경고]**"
    
    content = f"{title}\n시간: {now}\n"
    if error_msg: content += f"에러: ```{error_msg}```"
    try: requests.post(MONITOR_WEBHOOK, json={"content": content}, timeout=5)
    except: pass

# 게시판 검사 로직
def check_board(session, board_info, saved_data):
    board_id = board_info["id"]
    board_name = board_info["name"]
    url = board_info["url"]

    logger.info(f"[{board_name}] 분석 중...") 

    # !! 차단 방지용 랜덤 대기
    time.sleep(random.uniform(5, 10))

    response = session.get(url, headers=HEADERS, verify=False, timeout=30)
    response.encoding = 'utf-8'
    soup = BeautifulSoup(response.text, 'html.parser')
    rows = soup.select('table.board-table tbody tr')

    if not rows:
        # 재시도
        raise Exception(f"게시글 없음 - HTML 구조 변경 or 차단")
    

    last_id = saved_data.get(board_id, 0)
    new_notices = []
    max_id = last_id

    base_url = url.split('?')[0]

    for row in rows:
        title_div = row.select_one('.b-title-box > a')
        if not title_div: continue

        # 제목 추출
        title = title_div.get('title') or title_div.text
        title = title.replace("자세히 보기", "").strip()

        # 링크 추출
        href = title_div.get('href')
        if not href: continue

        link = f"{base_url.split('?')[0]}{href}" if href.startswith('?') else href

        # 글 번호 추출
        article_id = extract_article_id(href)
        if article_id == 0: continue


        row_classes = row.get('class', [])
        is_top = 'b-top-box' in row_classes

        if article_id > last_id:
            new_notices.append({
                "id": article_id, "title": title, "link": link, "is_top": is_top
            })
            if article_id > max_id: max_id = article_id

    # 최초 실행일 경우, 기준점만 설정
    if last_id == 0 and max_id > 0:
        logger.info(f"[{board_name}] 최초 실행 - 기준점(ID: {max_id})만 설정")
        saved_data[board_id] = max_id
        return True # 변동사항 있음(json)

    # 새 글 전송
    if new_notices:
        new_notices.sort(key=lambda x: x['id']) # 과거글~최신글 정렬
        send_post_discord(board_name, new_notices) # 전송
        saved_data[board_id] = max_id #최상단 ID 데이터 업데이트
        return True # 변동사항 있음(json)

    return False # 변동사항 없음

# 메인 실행
def run_bot():
    logger.info("━" * 20) 
    logger.info("CSE 공지봇 검사 시작")

    try:
        saved_data = {}

        # json 파일 읽기
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
                board_success = False # 게시판 접속 성공 확인
                
                # 재시도: 최대 3회
                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        if check_board(session, board, saved_data): # 게시판 검사 시작
                            changes = True # 변경 확인
                        board_success = True # 접속 확인
                        break # 성공 시 탈출

                    except Exception as e:
                        logger.warning(f"[{board['name']}] 실패 ({attempt}/{MAX_RETRIES}): {e}")
                        if attempt < MAX_RETRIES: # 재시도
                            time.sleep(RETRY_DELAY)
                
                # 재시도 실패 시 관리자 알림
                if not board_success:
                    send_simple_error_log(f"[{board['name']}] 3회 접속 실패", is_fatal=True)

            # 변경 확인
            if changes:
                with open(DATA_FILE, "w", encoding="utf-8") as f:
                    json.dump(saved_data, f, ensure_ascii=False, indent=4) # 최신 ID 작성
                logger.info("데이터 저장 완료") 
            else:
                logger.info("변동 사항 없음") 
        
        logger.info("프로그램 정상 종료") 

    except Exception as e:
        logger.error(f"치명적 오류: {e}")
        send_simple_error_log(f"오류로 인한 MAIN 종료\n{e}", is_fatal=True)

if __name__ == "__main__":
    run_bot()
