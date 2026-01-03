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
DATA_FILE = "library_data.json"

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
        return

    count = len(new_notices)
    message_content = f"### 📚 [일반공지] 새 글 {count}건\n\n"
    
    for notice in new_notices:
        title = notice['title']
        link = notice['link']
        icon = "▶" if notice['is_top'] else "▷"
        message_content += f"{icon} [{title}](<{link}>)\n"

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message_content})
        print(f"✉ [전송 완료] 도서관 공지 {count}건")
    except Exception as e:
        print(f"⚠ [전송 실패] {e}")

# 관리자 심플 알림 함수
def send_simple_error_log():
    if not MONITOR_WEBHOOK_URL: return 

    now = time.strftime('%Y-%m-%d %H:%M:%S')
    
    # 심플한 메시지 내용
    content = f"🚨 **[도서관 봇 오류 발생]** \n 시간: {now}"
    
    try:
        requests.post(MONITOR_WEBHOOK_URL, json={"content": content})
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
        response = session.get(URL, headers=HEADERS, verify=False, timeout=10)
        response.encoding = 'utf-8'

        # 3. HTML 파싱
        soup = BeautifulSoup(response.text, 'html.parser')

        # 4. 게시글 줄(Row) 탐색
        rows = soup.select('tbody > tr')
        if not rows:
            # 게시글을 못 찾은 것도 에러 상황일 수 있으므로 예외 발생시킴            print("⚠ 게시물을 찾을 수 없음")
            return

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
        
        # 1. 깃허브 로그용 상세 에러 출력
        traceback.print_exc()
        
        # 2. 관리자에게 심플 알림 전송
        send_simple_error_log()

if __name__ == "__main__":
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    check_library_notices()
