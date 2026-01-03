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

# 게시판 목록
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

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Referer': 'https://computer.cnu.ac.kr/'
}
# ==========================================


# ===[세션 생성기]===
def get_session():
    """재시도 로직이 있는 세션 생성"""
    session = requests.Session()
    
    retry = Retry(
        total=3,  # 재시도 3회
        backoff_factor=2,  # 2초, 4초, 8초 대기
        status_forcelist=[500, 502, 503, 504, 429],
        raise_on_status=False
    )
    
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
        send_simple_error_log("웹훅 URL이 없음")
        print("⚠ 웹훅 URL이 없음")
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
        send_simple_error_log("공지 전송 실패")
        print(f"⚠ [전송 실패] {e}")


# ===[관리자 알림]===
def send_simple_error_log(message=None):
    """[관리자용] 에러 발생 알림"""
    if not MONITOR_WEBHOOK_URL:
        return 

    now = time.strftime('%Y-%m-%d %H:%M:%S')
    if message:
        content = f"🚨 **[CSE 봇 오류]** \n{message}\n({now})"
    else:
        content = f"🚨 **[CSE 봇 오류]** \n{now}"
    
    try:
        requests.post(MONITOR_WEBHOOK_URL, json={"content": content}, timeout=5)
        print("✉ 관리자 알림 전송 완료")
    except:
        print("⚠ 관리자 알림 전송 실패")


# ===[게시판 검사]===
def check_board(session, board_info, saved_data, attempt=1, max_attempts=2):
    """개별 게시판 확인 및 새 글 감지 (재시도 로직 포함)"""
    board_id = board_info["id"]
    board_name = board_info["name"]
    url = board_info["url"]

    print(f"● [{board_name}] 분석 중... (시도 {attempt}/{max_attempts})")

    try:
        # 타임아웃: 연결 20초, 읽기 30초 (느린 서버 대응)
        response = session.get(
            url, 
            headers=HEADERS, 
            verify=False, 
            timeout=(20, 30)
        )
        
        # 응답 상태 확인
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}")
        
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')
        rows = soup.select('table.board-table tbody tr')
        
        if not rows:
            raise Exception("게시글(tr)을 찾을 수 없음")
            
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
            print(f"  ☀ 최초 실행 - 기준점(ID: {max_id})만 설정")
            saved_data[board_id] = max_id
            return True
        
        # 새 글 처리
        if new_notices:
            new_notices.sort(key=lambda x: x['id'])
            send_discord_batch_alert(board_name, new_notices)
            saved_data[board_id] = max_id
            return True
        
        print("  ☐ 새 글 없음")
        return False
        
    except requests.exceptions.Timeout:
        print("  ⏱ 타임아웃 (서버 응답 지연)")
        
        # 재시도 로직
        if attempt < max_attempts:
            print(f"  ☐ 5초 후 재시도...")
            time.sleep(5)
            return check_board(session, board_info, saved_data, attempt + 1, max_attempts)
        else:
            print(f"  ☒ 최대 재시도 횟수 초과")
            send_simple_error_log(f"{board_name}-타임아웃 (GitHub Actions 차단 의심)")
            return False
    
    except requests.exceptions.ConnectionError as e:
        print(f"  ⚠ 연결 오류: {str(e)[:80]}")
        
        # 재시도 로직
        if attempt < max_attempts:
            print(f"  ☐ 5초 후 재시도...")
            time.sleep(5)
            return check_board(session, board_info, saved_data, attempt + 1, max_attempts)
        else:
            print(f"  ⚠ 연결 실패 - GitHub Actions IP 차단 가능성 높음")
            send_simple_error_log(f"{board_name}-연결 차단 (학교 서버 IP 필터링)")
            return False
        
    except Exception as e:
        error_msg = f"  ⚠ 오류: {str(e)[:80]}"
        print(error_msg)
        send_simple_error_log(f"{board_name}-{str(e)[:50]}")
        return False


# ===[MAIN]===
def run_bot():
    """메인 실행 함수"""
    print("\n" + "━" * 40)
    print(f"🤖 CSE 공지봇 실행: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"☐ 실행 환경: {'GitHub Actions' if os.getenv('GITHUB_ACTIONS') else '로컬'}")
    
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
        success_count = 0
        fail_count = 0
        
        print("☐ 게시판 순차 확인 중...\n")
        
        for i, board in enumerate(TARGET_BOARDS, 1):
            result = check_board(session, board, saved_data)
            
            if result:
                any_changes = True
                success_count += 1
            else:
                # 결과가 False여도 오류인지 새 글이 없는지 구분 필요
                fail_count += 1
            
            # 마지막 게시판이 아니면 3초 대기
            if i < len(TARGET_BOARDS):
                time.sleep(3)
        
        # 결과 요약
        print(f"\n결과: 성공 {success_count}개, 실패/변동없음 {fail_count}개")
        
        # 모든 게시판 실패 시 경고
        if fail_count == len(TARGET_BOARDS):
            print("⚠ 모든 게시판 접속 실패 - GitHub Actions IP 차단 의심")
            send_simple_error_log("전체 게시판 접속 실패 - IP 차단 의심")
        
        # 데이터 저장
        if any_changes:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(saved_data, f, ensure_ascii=False, indent=4)
            print("☑ 데이터 저장 완료")
        else:
            print("☑ 변동 사항 없음")

    except Exception as e:
        print(f"\n⚠ 치명적인 오류: {e}")
        traceback.print_exc()
        send_simple_error_log("프로그램 강제 종료")


if __name__ == "__main__":
    run_bot()