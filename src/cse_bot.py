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
from concurrent.futures import ThreadPoolExecutor

# ===[설정 영역]==========================
DISCORD_WEBHOOK_URL = os.environ.get("cse_WEBHOOK_URL")
MONITOR_WEBHOOK_URL = os.environ.get("MONITOR_WEBHOOK_URL") # 관리자 알림용
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "..", "data", "cse_data.json")
# 게시판 목록 (List>Dic)
TARGET_BOARDS = [
    {
        "id": "bachelor", 
        "name": "학사공지", 
        "url": "https://computer.cnu.ac.kr/computer/notice/bachelor.do?articleLimit=20"
    },
    {
        "id": "general", 
        "name": "교내일반소식", 
        "url": "https://computer.cnu.ac.kr/computer/notice/notice.do?articleLimit=20" 
    },
    {
        "id": "job", 
        "name": "교외활동·인턴·취업", 
        "url": "https://computer.cnu.ac.kr/computer/notice/job.do?articleLimit=20" 
    },
    {
        "id": "project", 
        "name": "사업단소식", 
        "url": "https://computer.cnu.ac.kr/computer/notice/project.do?articleLimit=20" 
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
    session = requests.Session() # 세션 객체 생성
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504]) # retry 설정
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session 

# ===[ID 추출기]===
def extract_article_id(link):
    """링크에서 articleNo(고유번호) 추출"""
    match = re.search(r'articleNo=(\d+)', link) # ID 추출
    if match:
        return int(match.group(1)) # 정수 변환
    return 0 # ID 추출 실패

# ===[디코 전송기]===
def send_discord_batch_alert(category_name, new_notices):
    """디스코드 전송"""
    if not new_notices: return

    # 웹후크 URL 존재 확인
    if not DISCORD_WEBHOOK_URL:
        send_simple_error_log("웹후크 URL이 없음")
        print("⚠ 웹후크 URL이 없음")
        return
    
    # 메시지 상단 형성
    count = len(new_notices)
    message_content = f"### 📢 [{category_name}] 새 글 {count}건\n\n"
    
    # 개별 게시물 메시지 추가
    for notice in new_notices:
        icon = "▶" if notice['is_top'] else "▷" # 상단 고정 공지 / 일반 공지 구분
        message_content += f"{icon} [{notice['title']}](<{notice['link']}>)\n" # 메시지 추가
    try:
        # 메시지 전송
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message_content})
        print(f"✉ [전송 완료] {category_name} - {count}건")
    except Exception as e:
        send_simple_error_log("공지 전송 실패")
        print(f"⚠ [전송 실패] {e}")

# 관리자 함수
def send_simple_error_log(message=None):
    """
    [관리자용] 에러 발생 사실만 간단하게 알림
    """
    if not MONITOR_WEBHOOK_URL: return 

    now = time.strftime('%Y-%m-%d %H:%M:%S')
    if message:
        content = f"🚨 **[CSE 봇 오류]** \n{message}\n({now})"
    else:
        content = f"🚨 **[CSE 봇 오류]** \n{now}"
    
    try:
        requests.post(MONITOR_WEBHOOK_URL, json={"content": content})
        print("✉ 관리자 알림 전송 완료")
    except:
        print("⚠ 관리자 알림 전송 실패")

# ===[게시판 검사]===
def check_board(session, board_info, saved_data):
    board_id = board_info["id"]
    board_name = board_info["name"]
    url = board_info["url"]

    print(f"● [{board_name}] 분석 중...")

    try:
        # 1) 인터넷 접속
        response = session.get(url, headers=HEADERS, verify=False, timeout=(15, 30)) # 연결 15초, 읽기 30초
        
        # 2) 한글 깨짐 방지
        response.encoding = 'utf-8'

        # 3) HTML 파싱
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 4) 게시글 줄 탐색 (board-table - tbody - tr)
        rows = soup.select('table.board-table tbody tr')
        
        if not rows: # 가져온 줄이 없는 경우
            send_simple_error_log("게시글(tr)을 찾을 수 없음")
            raise Exception(f"⚠ [{board_name}] 게시글(tr)을 찾을 수 없음 (HTML 구조 변경 의심)")
            
        # 5) 마지막으로 읽은 ID 가져오기
        last_id = saved_data.get(board_id, 0)
        
        new_notices = [] # 새 글 저장을 위한 리스트
        max_id = last_id # 가장 큰 번호(최신 글)을 마지막 탐색의 id로 설정 

        # 6) 줄 반복 탐색(게시글 개별 작업)     
        for row in rows:
            # 1 - 제목 박스 찾기
            title_div = row.select_one('.b-title-box > a')
            if not title_div: continue 

            # 2 - 제목 가져오기 & 가공
            title = title_div.get('title') or title_div.text.strip()
            title = title.replace("자세히 보기", "").strip()
            
            # 3 - 게시글 링크 주소 가져오기
            href = title_div.get('href')
            
            # 4 - 게시글 링크 절대 경로로 가공
            if href.startswith('?'):
                base_url = url.split('?')[0]
                link = f"{base_url}{href}"
            else:
                link = href
            
            # 5 - 글 번호 추출
            article_id = extract_article_id(link)
            if article_id == 0: continue

            # 6 - 고정 공지 여부 확인 (중요도)
            row_classes = row.get('class', [])
            is_top = 'b-top-box' in row_classes

            # 7 - 판단 로직: 기준 게시글보다 최신 게시글인지 비교
            if article_id > last_id:
                # 최신 게시글이면 전송 목록에 추가
                new_notices.append({
                    "id": article_id,
                    "title": title,
                    "link": link,
                    "is_top": is_top
                })
                # 최신 게시글 갱신 (저장용)
                if article_id > max_id:
                    max_id = article_id

        # 7) 최초 실행 처리 (json 파일이 없는 경우)
        if last_id == 0 and max_id > 0:
            print(f"☐ [{board_name}] 최초 실행 - 기준점(ID: {max_id})만 설정, 전송 X")
            saved_data[board_id] = max_id # 데이터 맵 저장
            return True
        
        # 8) 새 글이 있으면 처리
        if new_notices:
            new_notices.sort(key=lambda x: x['id']) #ID 기준 오름차순 정렬
            send_discord_batch_alert(board_name, new_notices) #디스코드 전송
            saved_data[board_id] = max_id # 데이터 맵 저장
            return True
        
    except Exception as e:
        error_msg = f"⚠ [{board_name}] 접속/파싱 실패: {e}"
        print(f"{error_msg}")
        send_simple_error_log(f"{board_name}-접속/파싱 실패")
        return False

# ===[MAIN]===
def run_bot():
    print("\n" + "━" * 40)
    print(f"🤖 CSE 공지봇 실행: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    try:
        saved_data = {}

        # 파일 읽기 (과거 최신 게시물의 ID)
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                try: saved_data = json.load(f) #json => dic
                except: saved_data = {}

        session = get_session() # 새션 생성
        any_changes = False # 파일 수정 필요 여부
        
        for board in TARGET_BOARDS:
            if check_board(session, board, saved_data):
                any_changes = True
            # 게시판 사이마다 3초씩 대기하여 서버 차단을 방지합니다.
            time.sleep(3)  
        if any_changes:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(saved_data, f, ensure_ascii=False, indent=4)
            print("☑ 데이터 저장 완료")
        else:
            print("☒ 변동 사항 없음")

    # 전체 실행 과정 에러 처리
    except Exception as e:
        print(f"⚠ 치명적인 오류 발생: {e}")
        traceback.print_exc()
        send_simple_error_log("프로그램 강제 종료")

if __name__ == "__main__":
    run_bot()
