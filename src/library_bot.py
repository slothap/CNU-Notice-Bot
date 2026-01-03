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

# ==========================================
# [설정 영역]
# ==========================================
DISCORD_WEBHOOK_URL = os.environ.get("library_WEBHOOK_URL")
# 관리자 에러 알림용 웹후크
MONITOR_WEBHOOK_URL = os.environ.get("MONITOR_WEBHOOK_URL")

# [테스트용] 로컬 테스트 시 주석 해제
# DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
# MONITOR_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
URL = "https://library.cnu.ac.kr/bbs/list/1"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "..", "data", "library_data.json")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Connection': 'keep-alive',
    'Referer': 'https://library.cnu.ac.kr/',
    'Upgrade-Insecure-Requests': '1'
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
def extract_id_from_link(link):
    """링크에서 1_...(고유번호) 추출"""
    match_under = re.search(r'_(\d+)$', link)
    if match_under:
        return int(match_under.group(1))
    
    # 예비용 (슬래시 패턴)
    match_slash = re.search(r'/(\d+)$', link)
    if match_slash:
        return int(match_slash.group(1))
        
    return 0

# ===[디코 전송기]===
def send_discord_message(new_notices):
    """학생용 공지 알림 전송"""
    if not new_notices: return

    if not DISCORD_WEBHOOK_URL:
        print("⚠ 웹후크 URL이 없음")
        send_simple_error_log("웹후크 URL이 없음")
        return

    count = len(new_notices)
    message_content = f"### :books: [일반공지] 새 글 {count}건\n\n"
    
    for notice in new_notices:
        title = notice['title']
        link = notice['link']
        icon = "▶" if notice['is_top'] else "▷"
        message_content += f"{icon} [{title}](<{link}>)\n"

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message_content})
        print(f"✉ [전송 완료] 도서관 공지 {count}건")
    except Exception as e:
        send_simple_error_log("공지 전송 실패")
        print(f"⚠ [전송 실패] {e}")

# 관리자 심플 알림 함수
def send_simple_error_log(error_msg=None):
    if not MONITOR_WEBHOOK_URL: return 

    now = time.strftime('%Y-%m-%d %H:%M:%S')
    if error_msg:
        content = (
            f"🚨 **[도서관 봇 접속 장애]**\n"
            f"시간: {now}\n"
            f"에러: ```{error_msg}```\n"
            f"> 💡 **IP 차단**이나 **서버 점검**이 의심됩니다."
        )
    else:
        content = f"🚨 **[도서관 봇 오류]** \n{now}"
    
    try:
        requests.post(MONITOR_WEBHOOK_URL, json={"content": content}, timeout=5)
        print("✉ [관리자 알림 전송 완료]")
    except:
        print("⚠ 관리자 알림 전송 실패")

# ===[MAIN]===
def check_library_notices():
    print("\n" + "━" * 40)
    print(f"🤖 도서관 공지봇 실행: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        # 1. 기존 데이터 파일 읽기
        saved_data = {}
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                try: saved_data = json.load(f)
                except: saved_data = {}
        
        last_id = saved_data.get("last_id", 0)

        # 2. 웹페이지 접속
        session = get_session()
        response = session.get(URL, headers=HEADERS, verify=False, timeout=30)  # 여기 30초로 변경
        response.encoding = 'utf-8'

        # 3. HTML 파싱
        soup = BeautifulSoup(response.text, 'html.parser')

        # 4. 게시글 줄(Row) 탐색
        rows = soup.select('tbody > tr')
        if not rows:
            # 게시글을 못 찾은 것도 에러 상황일 수 있으므로 예외 발생
            send_simple_error_log("게시글(tr)을 찾을 수 없음")
            raise Exception("⚠ [도서관 일반공지] 게시글(tr)을 찾을 수 없음 (HTML 구조 변경 의심)")

        new_notices = []
        max_id_in_this_scan = last_id

        # 5. 각 줄 반복 검사
        for row in rows:
            a_tag = row.select_one('td.title a') or row.select_one('td.subject a') or row.select_one('a')
            if not a_tag: continue

            title = a_tag.get('title') or a_tag.text.strip()
            title = title.replace("새글", "").strip()
            
            href = a_tag.get('href')
            link = f"https://library.cnu.ac.kr{href}"
            
            article_id = extract_id_from_link(link)
            if article_id == 0: continue

            is_top = 'always' in row.get('class', [])

            if article_id > last_id:
                new_notices.append({
                    "id": article_id,
                    "title": title,
                    "link": link,
                    "is_top": is_top
                })
                if article_id > max_id_in_this_scan:
                    max_id_in_this_scan = article_id

        # 6. 최초 실행 처리
        if last_id == 0 and max_id_in_this_scan > 0:
            print(f"☐ [도서관] 최초 실행 - 기준점(ID: {max_id_in_this_scan})만 설정")
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump({"last_id": max_id_in_this_scan}, f, indent=4)
            return

        # 7. 새 글 전송 및 저장
        if new_notices:
            new_notices.sort(key=lambda x: x['id'])
            send_discord_message(new_notices)
            
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump({"last_id": max_id_in_this_scan}, f, indent=4)
            print("☑ 도서관 데이터 저장 완료")
        else:
            print("☒ 도서관 새 소식 없음")

    # 에러 발생 시 처리
    except Exception as e:
        print(f"⚠ 치명적인 오류 발생: {e}")
        traceback.print_exc()
        send_simple_error_log(f"프로그램 강제 종료\n{str(e)}") # 상세 에러 내용 전송

if __name__ == "__main__":
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    check_library_notices()
