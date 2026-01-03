import os
import time
import json
import requests
import re
import traceback
from datetime import datetime

# 👇 [추가] 로컬에서 .env 파일을 읽기 위한 라이브러리
from dotenv import load_dotenv

# ===[셀레니움 관련 라이브러리]===
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 1. 환경변수 로드 (.env 파일이 있으면 읽고, 없으면 건너뜀)
load_dotenv()

# ===[설정 영역]==========================
USER_ID = os.environ.get("CNU_ID")
USER_PW = os.environ.get("CNU_PW")
DISCORD_WEBHOOK_URL = os.environ.get("with_WEBHOOK_URL")
MONITOR_WEBHOOK_URL = os.environ.get("MONITOR_WEBHOOK_URL")

LIST_URL = "https://with.cnu.ac.kr/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmList.do"
DATA_FILE = "with_data.json"
# ==========================================

def clean_text(text):
    if not text: return ""
    return re.sub(r'\s+', ' ', text).strip()

def parse_str_to_dt(date_str):
    if not date_str: return None
    try:
        if ":" in date_str:
            return datetime.strptime(date_str, "%Y.%m.%d %H:%M")
        else:
            return datetime.strptime(date_str, "%Y.%m.%d")
    except:
        return None

def calculate_multi_info(sub_items):
    if not sub_items: return None
    app_ends, oper_starts, oper_ends, capacities = [], [], [], []
    for item in sub_items:
        if item['apply_raw']:
            parts = item['apply_raw'].split('~')
            if len(parts) > 1:
                dt = parse_str_to_dt(parts[1].strip())
                if dt: app_ends.append(dt)
        if item['oper_raw']:
            parts = item['oper_raw'].split('~')
            if len(parts) > 0:
                dt_s = parse_str_to_dt(parts[0].strip())
                if dt_s: oper_starts.append(dt_s)
            if len(parts) > 1:
                dt_e = parse_str_to_dt(parts[1].strip())
                if dt_e: oper_ends.append(dt_e)
            elif len(parts) == 1 and dt_s:
                oper_ends.append(dt_s)
        if item['capacity']:
            nums = re.findall(r'\d+', item['capacity'])
            if nums: capacities.append(int(nums[0]))
            
    result = {"apply": "", "oper": "", "capacity": ""}
    if app_ends:
        result['apply'] = f"~{min(app_ends).strftime('%m.%d')}"
    if oper_starts and oper_ends:
        min_s, max_e = min(oper_starts), max(oper_ends)
        if min_s.date() == max_e.date():
            result['oper'] = f"{min_s.strftime('%m.%d %H:%M')}~{max_e.strftime('%H:%M')}"
        else:
            result['oper'] = f"{min_s.strftime('%m.%d')}~{max_e.strftime('%m.%d')}"
    if capacities:
        result['capacity'] = f"{min(capacities)}명"
    return result

def extract_details(container):
    data = {"apply_raw": "", "oper_raw": "", "capacity": ""}
    try:
        for dl in container.find_elements(By.CSS_SELECTOR, ".etc_info_txt dl"):
            dt = dl.find_element(By.TAG_NAME, "dt").get_attribute("textContent")
            dd = dl.find_element(By.TAG_NAME, "dd").get_attribute("textContent")
            if "신청" in dt: data["apply_raw"] = clean_text(dd)
            elif "운영" in dt or "교육기간" in dt: data["oper_raw"] = clean_text(dd)
    except: pass
    try:
        for dl in container.find_elements(By.CSS_SELECTOR, ".rq_desc dl"):
            dt = dl.find_element(By.TAG_NAME, "dt").get_attribute("textContent")
            if "모집" in dt or "정원" in dt:
                data["capacity"] = clean_text(dl.find_element(By.TAG_NAME, "dd").get_attribute("textContent"))
    except: pass
    return data

def post_to_discord_safe(content):
    if not DISCORD_WEBHOOK_URL or "http" not in DISCORD_WEBHOOK_URL: return
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=1)
    session.mount('http://', HTTPAdapter(max_retries=retry))
    session.mount('https://', HTTPAdapter(max_retries=retry))
    try:
        # 멘션 없이 내용만 전송
        session.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=10)
        print("✉ [전송 성공]")
    except Exception as e:
        print(f"⚠ [전송 실패] {e}")

# ===[메시지 디자인 수정 영역]===
def create_message_content(info):
    """
    요청하신 디자인:
    ** ▶ D-20 | 제목 **
    > [Sub Title] 외 N개 반 (멀티일 경우)
    > 신청: 날짜 | 운영: 날짜 | 정원: N명
    """
    # 1. 아이콘 및 D-Day 설정
    icon = "▶" if info['is_multi'] else "▷"
    d_day_part = f"{info['d_day']} | " if info['d_day'] else ""
    
    # 2. 제목 (진하게, 링크 포함)
    header = f"** {icon} {d_day_part}[{info['title']}](<{info['link']}>) **\n"
    
    body_lines = []

    # 3. (멀티 프로그램인 경우) 세부 프로그램 대표 표시
    if info['is_multi'] and info['sub_items']:
        first_sub = info['sub_items'][0]['title']
        count = len(info['sub_items']) - 1
        sub_text = f"[{first_sub}] 외 {count}개 반" if count > 0 else f"[{first_sub}]"
        body_lines.append(sub_text)

    # 4. 신청/운영/정원 정보 조립
    parts = []
    
    # 날짜 포맷팅 내부 함수
    def simple_date(raw):
        m = re.search(r'\d{4}\.(\d{2}\.\d{2})', raw)
        return m.group(1) if m else raw

    def format_single_period(raw, is_apply=False):
        if not raw: return ""
        p = raw.split('~')
        if len(p) < 2: return raw
        s, e = simple_date(p[0]), simple_date(p[1])
        return f"~{e}" if is_apply else f"{s}~{e}"

    # 데이터 추출
    apply_txt, oper_txt, cap_txt = "", "", ""
    
    if info['is_multi']:
        apply_txt = info['multi_calc']['apply']
        oper_txt = info['multi_calc']['oper']
        cap_txt = info['multi_calc']['capacity']
    else:
        apply_txt = format_single_period(info['apply_raw'], True)
        oper_txt = format_single_period(info['oper_raw'], False)
        cap_txt = info['capacity']

    # 정보 합치기 (라벨 Bold 제거)
    if apply_txt: parts.append(f"신청: {apply_txt}")
    if oper_txt: parts.append(f"운영: {oper_txt}")
    if cap_txt: parts.append(f"정원: {cap_txt}")
    
    if parts:
        body_lines.append(" | ".join(parts))

    # 5. 본문 들여쓰기 처리 (디스코드 인용문 '>' 사용)
    body_text = ""
    for line in body_lines:
        body_text += f"> {line}\n"

    return header + body_text + "\n"

def send_batch_messages(new_items):
    if not new_items: return
    
    count = len(new_items)
    # [메인 헤더]
    full_message = f"### :compass: [CNU With+] 새로운 비교과 {count}건\n\n"
    
    for item in reversed(new_items):
        content_chunk = create_message_content(item)
        if len(full_message) + len(content_chunk) > 1900:
            post_to_discord_safe(full_message)
            full_message = ""
        full_message += content_chunk

    if full_message:
        post_to_discord_safe(full_message)

def send_simple_error_log():
    if not MONITOR_WEBHOOK_URL: return 
    try:
        requests.post(MONITOR_WEBHOOK_URL, json={"content": f"🚨 **[WITH 봇 오류]** \n{time.strftime('%Y-%m-%d %H:%M:%S')}"})
    except: pass

def run_selenium_scraper():
    print("\n" + "━" * 40)
    print("🤖 WITH(비교과) 알람봇 실행")

    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        wait = WebDriverWait(driver, 20)

        print(f"☐ 로그인 페이지 접속...")
        driver.get("https://with.cnu.ac.kr/index.do")
        
        try:
            login_btn = wait.until(EC.element_to_be_clickable((By.CLASS_NAME, "login_btn")))
            driver.execute_script("arguments[0].click();", login_btn)
        except: pass

        try:
            try:
                wait.until(EC.visibility_of_element_located((By.NAME, "userId"))).send_keys(USER_ID)
                driver.find_element(By.NAME, "password").send_keys(USER_PW + Keys.RETURN)
            except:
                found = False
                for frame in driver.find_elements(By.TAG_NAME, "iframe"):
                    driver.switch_to.default_content()
                    driver.switch_to.frame(frame)
                    try:
                        driver.find_element(By.NAME, "userId").send_keys(USER_ID)
                        driver.find_element(By.NAME, "password").send_keys(USER_PW + Keys.RETURN)
                        found = True
                        driver.switch_to.default_content()
                        break
                    except: continue
                if not found: raise Exception("로그인 폼 못 찾음")
            
            try:
                wait.until(EC.invisibility_of_element_located((By.CLASS_NAME, "login_btn")))
                print("☑ 로그인 성공")
            except: print("⚠ 로그인 실패 가능성")
        except Exception as e: raise e

        last_read_id = None
        is_first = False
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    last_read_id = json.load(f).get("last_read_id")
            except: pass
        if not last_read_id: is_first = True

        driver.get(LIST_URL)
        try: wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "li div.cont_box")))
        except: raise Exception("목록 로딩 실패")

        new_items = []
        stop = False
        top_id = None

        for page in range(1, 4): 
            if stop: break
            print(f"☐ [페이지 {page}] 스캔 중...")
            if page > 1:
                try:
                    driver.execute_script(f"global.page({page});")
                    time.sleep(2)
                except: break
            
            items = driver.find_elements(By.CSS_SELECTOR, "li:has(div.cont_box)")
            if not items: items = [li for li in driver.find_elements(By.CSS_SELECTOR, "li") if li.find_elements(By.CLASS_NAME, "cont_box")]

            for item in items:
                try:
                    a_tag = item.find_element(By.CSS_SELECTOR, "a.tit")
                    pid = ""
                    try:
                        import json as pyjson
                        pid = pyjson.loads(a_tag.get_attribute("data-params")).get("encSddpbSeq")
                    except: pass
                    
                    if not pid: continue
                    if top_id is None: top_id = pid
                    if pid == last_read_id:
                        stop = True
                        break
                    if is_first: continue

                    link = f"https://with.cnu.ac.kr/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmInfo.do?encSddpbSeq={pid}&paginationInfo.currentPageNo=1"
                    full_title = a_tag.get_attribute("textContent")
                    try: title = clean_text(full_title.replace(a_tag.find_element(By.CLASS_NAME, "label").get_attribute("textContent"), ""))
                    except: title = clean_text(full_title)
                    
                    try: d_day = clean_text(item.find_element(By.CSS_SELECTOR, "span.day").get_attribute("textContent"))
                    except: d_day = ""
                    
                    is_multi = "multi_class" in item.get_attribute("class")
                    p_data = {
                        "id": pid, "title": title, "d_day": d_day, "link": link,
                        "is_multi": is_multi, "sub_items": [], "multi_calc": {},
                        "apply_raw": "", "oper_raw": "", "capacity": ""
                    }

                    try:
                        more = item.find_elements(By.CLASS_NAME, "class_more_open")
                        if more and more[0].is_displayed():
                            driver.execute_script("arguments[0].click();", more[0])
                            time.sleep(0.5)
                    except: pass

                    if is_multi:
                        for sub in item.find_elements(By.CLASS_NAME, "class_cont"):
                            if not sub.get_attribute("textContent").strip(): continue
                            try:
                                s_title = sub.find_element(By.CSS_SELECTOR, "a.tit").get_attribute("textContent")
                                try: s_title = s_title.replace(sub.find_element(By.CLASS_NAME, "label").get_attribute("textContent"), "")
                                except: pass
                                p_data['sub_items'].append({"title": clean_text(s_title), **extract_details(sub)})
                            except: continue
                        p_data['multi_calc'] = calculate_multi_info(p_data['sub_items'])
                    else:
                        p_data.update(extract_details(item))
                    new_items.append(p_data)
                except: continue
        
        if is_first:
            if top_id:
                with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump({"last_read_id": top_id}, f)
            print("☐ 최초 실행 - 기준점 설정 완료")
        elif new_items:
            print(f"● {len(new_items)}개 새 글 -> 묶음 전송")
            send_batch_messages(new_items)
            if top_id:
                with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump({"last_read_id": top_id}, f)
        else:
            print("☒ 새 글 없음")

    except Exception as e:
        print(f"⚠ 에러: {e}")
        traceback.print_exc()
        send_simple_error_log()
    finally:
        if 'driver' in locals(): driver.quit()

if __name__ == "__main__":
    run_selenium_scraper()