import requests
from bs4 import BeautifulSoup
import os
import time
import json
import re
import urllib3
import traceback
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

load_dotenv()

# ===[설정 영역]==========================
DISCORD_WEBHOOK_URL = os.environ.get("cse_WEBHOOK_URL")
MONITOR_WEBHOOK_URL = os.environ.get("MONITOR_WEBHOOK_URL")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "..", "data", "cse_data.json")

# 게시판 목록 (이전 설정 그대로)
TARGET_BOARDS = [
    {
        "id": "bachelor", 
        "name": "학사공지", 
        "url": "https://computer.cnu.ac.kr/computer/notice/bachelor.do?articleLimit=30"
    },
    {
        "id": "general", 
        "name": "교내일반소식", 
        "url": "https://computer.cnu.ac.kr/computer/notice/notice.do?articleLimit=30" 
    },
    {
        "id": "job", 
        "name": "교외활동·인턴·취업", 
        "url": "https://computer.cnu.ac.kr/computer/notice/job.do?articleLimit=30" 
    },
    {
        "id": "project", 
        "name": "사업단소식", 
        "url": "https://computer.cnu.ac.kr/computer/notice/project.do?articleLimit=30" 
    }
]

# 헤더 정보
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}
# ==========================================


# ===[세션 생성기]===
def get_session():
    """Retry 가능한 세션 생성"""
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session 


# ===[ID 추출기]===
def extract_article_id(link):
    """링크에서 articleNo(고유번호) 추출"""
    match = re.search(r'articleNo=(\d+)', link)
    if match:
        return int(match.group(1))
    return 0


# ===[디코 전송기]===
def send_discord_batch_alert(category_name, new_notices):
    """디스코드 전송"""
    if not new_notices:
        return

    if not DISCORD_WEBHOOK_URL:
        print("⚠ 웹후크 URL이 없음")
        return
    
    count = len(new_notices)
    message_content = f"### 📢 [{category_name}] 새 글 {count}건\n\n"
    
    for notice in new_notices:
        icon = "▶" if notice['is_top'] else "▷"
        message_content += f"{icon} [{notice['title']}](<{notice['link']}>)\n"

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message_content}, timeout=5)
        print(f"✉ [전송 완료] {category_name} - {count}건")
    except Exception as e:
        print(f"⚠ [전송 실패] {e}")


# ===[관리자 알림]===
def send_simple_error_log():
    """[관리자용] 에러 발생 사실만 간단하게 알림"""
    if not MONITOR_WEBHOOK_URL:
        return 

    now = time.strftime('%Y-%m-%d %H:%M:%S')
    content = f"🚨 **[CSE 공지봇 오류 발생]** \n{now}"
    
    try:
        requests.post(MONITOR_WEBHOOK_URL, json={"content": content}, timeout=5)
        print("✉ [관리자 알림 전송 완료]")
    except:
        print("⚠ 관리자 알림 전송 실패")


# ===[게시판 검사]===
def check_board(session, board_info, saved_data):
    """개별 게시판 확인 및 새 글 감지"""
    board_id = board_info["id"]
    board_name = board_info["name"]
    url = board_info["url"]

    print(f"● [{board_name}] 분석 중...")

    try:
        # 이전 코드 스타일 그대로: timeout은 단일값 15초
        response = session.get(url, headers=HEADERS, timeout=15)
        
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')
        rows = soup.select('table.board-table tbody tr')
        
        if not rows:
            print(f"⚠ [{board_name}] 게시글을 찾을 수 없음 (HTML 구조 변경 가능성)")
            return False
        
        last_id = saved_data.get(board_id, 0)
        new_notices = []
        max_id = last_id

        for row in rows:
            title_div = row.select_one('.b-title-box > a')
            if not title_div:
                continue 

            title = title_div.get('title') or title_div.text.strip()
            title = title.replace("자세히 보기", "").strip()
            
            href = title_div.get('href')
            
            if href.startswith('?'):
                base_url = url.split('?')[0]
                link = f"{base_url}{href}"
            else:
                link = href
            
            article_id = extract_article_id(link)
            if article_id == 0:
                continue

            row_classes = row.get('class', [])
            is_top = 'b-top-box' in row_classes

            if article_id > last_id:
                new_notices.append({
                    "id": article_id,
                    "title": title,
                    "link": link,
                    "is_top": is_top
                })
                if article_id > max_id:
                    max_id = article_id

        # 최초 실행 처리
        if last_id == 0 and max_id > 0:
            print(f"☐ [{board_name}] 최초 실행 - 기준점(ID: {max_id})만 설정, 전송 X")
            saved_data[board_id] = max_id
            return True
        
        # 새 글이 있으면 처리
        if new_notices:
            new_notices.sort(key=lambda x: x['id'])
            send_discord_batch_alert(board_name, new_notices)
            saved_data[board_id] = max_id
            return True
        
        return False

    except Exception as e:
        print(f"⚠ [{board_name}] 에러: {e}")
        return False


# ===[MAIN]===
def run_bot():
    """메인 실행 함수"""
    print("\n" + "━" * 40)
    print(f"🤖 CSE 공지봇 실행: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # SSL 경고 무시
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    try:
        saved_data = {}

        # 파일 읽기
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                try:
                    saved_data = json.load(f)
                except:
                    saved_data = {}

        session = get_session()
        any_changes = False

        # 게시판 목록 반복
        for board in TARGET_BOARDS:
            if check_board(session, board, saved_data):
                any_changes = True
        
        # 변경사항 있으면 저장
        if any_changes:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(saved_data, f, ensure_ascii=False, indent=4)
            print("☑ 데이터 저장 완료")
        else:
            print("☒ 변동 사항 없음")

    except Exception as e:
        print(f"⚠ 치명적인 오류 발생: {e}")
        traceback.print_exc()
        send_simple_error_log()


if __name__ == "__main__":
    run_bot()