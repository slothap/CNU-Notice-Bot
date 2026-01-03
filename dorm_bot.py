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

# ===[설정 영역]==========================
DISCORD_WEBHOOK_URL = os.environ.get("dorm_WEBHOOK_URL") 
MONITOR_WEBHOOK_URL = os.environ.get("MONITOR_WEBHOOK_URL") # 관리자 알림용
# [Only local_test]
# DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
# MONITOR_WEBHOOK_URL = "https://discord.com/api/webhooks/..."

DATA_FILE = "dorm_data.json"

# 게시판 목록 (List>Dic)
TARGET_BOARDS = [
    {
        "id": "movein",
        "name": "입주/퇴거 공지",
        "url": "https://dorm.cnu.ac.kr/_prog/_board/?code=sub05_0501&site_dvs_cd=kr&menu_dvs_cd=030101"
    },
    {
        "id": "general",
        "name": "기숙사 일반공지",
        "url": "https://dorm.cnu.ac.kr/_prog/_board/?code=sub03_0301&site_dvs_cd=kr&menu_dvs_cd=0302"
    },
    {
        "id": "work",
        "name": "기숙사 작업공지",
        "url": "https://dorm.cnu.ac.kr/_prog/_board/?code=sub03_0302&site_dvs_cd=kr&menu_dvs_cd=0303"
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
def extract_id_from_link(link):
    """링크에서 no(고유번호) 추출"""
    match = re.search(r'no=(\d+)', link)
    if match:
        return int(match.group(1))
    return 0

# ===[디코 전송기]===
def send_discord_batch_alert(category_name, new_notices):
    if not new_notices: return
    
    if not DISCORD_WEBHOOK_URL:
        print("⚠ 웹후크 URL이 없음")
        return

    count = len(new_notices)
    message_content = f"### 🛌 [{category_name}] 새 글 {count}건\n\n"
    
    for notice in new_notices:
        icon = "▶" if notice['is_top'] else "▷"
        message_content += f"{icon} [{notice['title']}](<{notice['link']}>)\n"
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message_content})
        print(f"✉ [전송 완료] {category_name} - {count}건")
    except Exception as e:
        print(f"⚠ [전송 실패] {e}")

# 관리자 함수
def send_simple_error_log():
    if not MONITOR_WEBHOOK_URL: return 

    now = time.strftime('%Y-%m-%d %H:%M:%S')
    content = f"🚨 **[기숙사 봇 오류 발생]** \n{now}"
    
    try:
        requests.post(MONITOR_WEBHOOK_URL, json={"content": content})
        print("✉ [관리자 알림 전송 완료]")
    except:
        print("⚠ 관리자 알림 전송 실패")

# ===[게시판 검사]===
def check_board(session, board_info, saved_data):
    board_id = board_info["id"]
    board_name = board_info["name"]
    url = board_info["url"]

    print(f"⌕ [{board_name}] 분석 중...")
    
    try:
        # 1) 인터넷 접속
        response = session.get(url, headers=HEADERS, verify=False, timeout=10)
        response.encoding = 'utf-8'

        # 3) HTML 파싱
        soup = BeautifulSoup(response.text, 'html.parser')

        # 4) 게시글 줄(Row) 탐색
        rows = soup.select('tbody > tr')
        if not rows:
            print(f"⚠ [{board_name}] 게시글을 찾을 수 없음 (HTML 구조 변경 가능성)")
            return False

        # 5) 마지막으로 읽은 ID 불러오기
        last_id = saved_data.get(board_id, 0)
        
        new_notices = []
        max_id = last_id 

        # 6) 각 줄(tr) 반복 검사
        for row in rows:
            title_td = row.select_one('td.title')
            if not title_td: continue
            
            a_tag = title_td.select_one('a')
            if not a_tag: continue

            title = a_tag.get('title') or a_tag.text.strip()
            href = a_tag.get('href')
            
            if href.startswith("?"):
                link = f"https://dorm.cnu.ac.kr/_prog/_board/{href}"
            elif href.startswith("/"):
                link = f"https://dorm.cnu.ac.kr{href}"
            else:
                link = f"https://dorm.cnu.ac.kr/_prog/_board/{href}"

            article_id = extract_id_from_link(link)
            if article_id == 0: continue

            is_top = False
            num_td = row.select_one('td.num')
            if num_td and "공지" in num_td.get_text():
                is_top = True

            if article_id > last_id:
                new_notices.append({
                    "id": article_id,
                    "title": title,
                    "link": link,
                    "is_top": is_top
                })
                if article_id > max_id:
                    max_id = article_id

        # 7) 최초 실행 처리
        if last_id == 0 and max_id > 0:
            print(f"☐ [{board_name}] 최초 실행 - 기준점(ID: {max_id})만 설정합니다.")
            saved_data[board_id] = max_id
            return True

       # 8) 새 글 전송
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
    print("\n" + "━" * 40)
    print(f"🤖 기숙사 공지봇 실행: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 인증서 경고 끄기
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    try:
        saved_data = {}
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                try: saved_data = json.load(f)
                except: saved_data = {}

        session = get_session()
        any_changes = False

        for board in TARGET_BOARDS:
            if check_board(session, board, saved_data):
                any_changes = True

        if any_changes:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(saved_data, f, ensure_ascii=False, indent=4)
            print("☑ 통합 데이터 파일 저장 완료.")
        else:
            print("☒ 변동 사항 없음.")

    # 전체 로직 에러 처리
    except Exception as e:
        print(f"⚠ 치명적인 오류 발생: {e}")
        traceback.print_exc()
        send_simple_error_log()

if __name__ == "__main__":
    run_bot()
