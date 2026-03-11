import os
import time
import json
import requests
import re
import urllib3
import random
import subprocess  
import logging     
import sys        
from datetime import datetime
from dotenv import load_dotenv

# ===[셀레니움 관련 라이브러리]===
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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

USER_ID = os.environ.get("CNU_ID")
USER_PW = os.environ.get("CNU_PW")
WITH_WEBHOOK = os.environ.get("with_WEBHOOK")
MONITOR_WEBHOOK = os.environ.get("MONITOR_WEBHOOK")

LIST_URL = "https://with.cnu.ac.kr/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmList.do"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
DATA_FILE = os.path.join(DATA_DIR, "with_data.json")

PROFILE_DIR = os.path.join(BASE_DIR, "chrome_profile")
if not os.path.exists(PROFILE_DIR):
    os.makedirs(PROFILE_DIR)


def force_kill_browsers():
    try:
        subprocess.run(["pkill", "-f", "chrome"], check=False)
        subprocess.run(["pkill", "-f", "chromedriver"], check=False)
        logger.info("잔여 브라우저 프로세스 정리")
    except Exception as e:
        logger.error(f"프로세스 정리 중 에러: {e}")

# === [유틸 함수] ===
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
    time_values = [] 

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
        
        if item['time_raw']:
            t_nums = re.findall(r"[\d\.]+", item['time_raw'])
            if t_nums:
                try: time_values.append(float(t_nums[0]))
                except: pass

    result = {"apply": "", "oper": "", "capacity": "", "max_time": ""}
    
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
    
    if time_values:
        max_t = max(time_values)
        if max_t.is_integer():
            result['max_time'] = f"{int(max_t)}시간"
        else:
            result['max_time'] = f"{max_t}시간"
            
    return result

def extract_details(container):
    data = {"apply_raw": "", "oper_raw": "", "capacity": "", "time_raw": ""}
    
    try:
        for dl in container.find_elements(By.CSS_SELECTOR, ".etc_info_txt dl"):
            dt = dl.find_element(By.TAG_NAME, "dt").get_attribute("textContent")
            dd = dl.find_element(By.TAG_NAME, "dd").get_attribute("textContent")
            
            if "신청" in dt: 
                data["apply_raw"] = clean_text(dd)
            elif "운영" in dt or "교육기간" in dt: 
                data["oper_raw"] = clean_text(dd)
    except: pass

    try:
        rq_desc = container.find_element(By.CSS_SELECTOR, ".rq_desc")
        for dl in rq_desc.find_elements(By.TAG_NAME, "dl"):
            dt_text = dl.find_element(By.TAG_NAME, "dt").get_attribute("textContent")
            if "모집" in dt_text or "정원" in dt_text:
                data["capacity"] = clean_text(dl.find_element(By.TAG_NAME, "dd").get_attribute("textContent"))

        try:
            mileage_dl = rq_desc.find_element(By.CLASS_NAME, "mileage")
            data["time_raw"] = clean_text(mileage_dl.find_element(By.TAG_NAME, "dd").get_attribute("textContent"))
        except: pass
    except: pass
    
    return data

def post_to_discord_safe(content):
    if not WITH_WEBHOOK: return
    try:
        requests.post(WITH_WEBHOOK, json={"content": content}, timeout=5)
    except Exception as e:
        logger.error(f"[전송 실패] {e}") 

def send_simple_error_log(error_msg=None, is_fatal=False):
    if not MONITOR_WEBHOOK: return
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    title = "🚨 **[비교과 봇 치명적 오류]**" if is_fatal else "⚠️ **[비교과 봇 단순 경고]**"
    content = f"{title}\n시간: {now}\n"
    if error_msg: content += f"에러: ```{error_msg}```"
    try: requests.post(MONITOR_WEBHOOK, json={"content": content}, timeout=5)
    except: pass

def create_message_content(info):
    icon = "▶" if info['is_multi'] else "▷"
    d_day_part = f"{info['d_day']} | " if info['d_day'] else ""
    header = f"{icon} **{d_day_part}[{info['title']}](<{info['link']}>)**\n"
    body_lines = []

    if info['is_multi'] and info['sub_items']:
        first_sub = info['sub_items'][0]['title']
        count = len(info['sub_items']) - 1
        sub_text = f"[{first_sub}] 외 {count}개 반" if count > 0 else f"[{first_sub}]"
        body_lines.append(sub_text)

    parts = []
    def simple_date(raw):
        m = re.search(r'\d{4}\.(\d{2}\.\d{2})', raw)
        return m.group(1) if m else raw

    def format_single_period(raw, is_apply=False):
        if not raw: return ""
        p = raw.split('~')
        if len(p) < 2: return raw
        s, e = simple_date(p[0]), simple_date(p[1])
        return f"~{e}" if is_apply else f"{s}~{e}"

    if info['is_multi']:
        apply_txt = info['multi_calc']['apply']
        oper_txt = info['multi_calc']['oper']
        cap_txt = info['multi_calc']['capacity']
        time_txt = info['multi_calc']['max_time'] 
    else:
        apply_txt = format_single_period(info['apply_raw'], True)
        oper_txt = format_single_period(info['oper_raw'], False)
        cap_txt = info['capacity']
        time_txt = info['time_raw'] 

    if apply_txt: parts.append(f"신청: {apply_txt}")
    if oper_txt: parts.append(f"운영: {oper_txt}")
    if cap_txt: parts.append(f"정원: {cap_txt}")
    if time_txt: parts.append(f"인정: {time_txt}") 
    if parts: body_lines.append(" | ".join(parts))

    body_text = ""
    for line in body_lines:
        body_text += f"> {line}\n"
    return header + body_text + "\n"

def send_batch_messages(new_items):
    if not new_items: return
    count = len(new_items)
    full_message = f"### 📢 [WITH 비교과] 새 글 {count}건\n\n"
    for item in reversed(new_items):
        content_chunk = create_message_content(item)
        if len(full_message) + len(content_chunk) > 1900:
            post_to_discord_safe(full_message)
            full_message = ""
        full_message += content_chunk
    if full_message: 
        post_to_discord_safe(full_message)
        logger.info(f"[전송 완료] WITH 비교과 - {count}건") 

# === [셀레니움 구동기] ===
def create_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # 프로필 유지 (세션 저장)
    chrome_options.add_argument(f"user-data-dir={PROFILE_DIR}")

    if os.path.exists("/usr/bin/chromium"):
        chrome_options.binary_location = "/usr/bin/chromium"
    elif os.path.exists("/usr/bin/chromium-browser"):
        chrome_options.binary_location = "/usr/bin/chromium-browser"

    system_driver_path = "/usr/bin/chromedriver"
    if os.path.exists(system_driver_path): service = Service(system_driver_path)
    else: service = Service() 

    return webdriver.Chrome(service=service, options=chrome_options)

def login_process(driver, wait):
    driver.get("https://with.cnu.ac.kr/index.do")
    try:
        if len(driver.find_elements(By.CLASS_NAME, "login_btn")) == 0:
            logger.info("☑️ 세션 유지 중 - 자동 로그인 패스") 
            return
    except: pass
    
    logger.info("● 로그인 시도 중...") 
    try:
        login_btn = wait.until(EC.element_to_be_clickable((By.CLASS_NAME, "login_btn")))
        driver.execute_script("arguments[0].click();", login_btn)
    except: pass
    try:
        try:
            wait.until(EC.visibility_of_element_located((By.NAME, "userId"))).send_keys(USER_ID)
            driver.find_element(By.NAME, "password").send_keys(USER_PW + Keys.RETURN)
        except:
            raise Exception("로그인 폼을 찾을 수 없음")
        try:
            wait.until(EC.invisibility_of_element_located((By.CLASS_NAME, "login_btn")))
            logger.info("☑️ 로그인 성공")
        except:
            raise Exception("로그인 버튼 미소멸 (로그인 실패 의심)")
    except Exception as e:
        raise e

def perform_scraping_cycle():
    driver = None
    try:
        driver = create_driver()
        wait = WebDriverWait(driver, 20)
        login_process(driver, wait)

        last_id = None
        is_first = False
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    last_id = json.load(f).get("last_id")
            except json.JSONDecodeError as e:
                logger.error(f"데이터 파일 파싱 오류: {e}") 
            except Exception as e:
                logger.error(f"데이터 파일 읽기 오류: {e}") 

        if not last_id: is_first = True

        driver.get(LIST_URL)
        time.sleep(random.uniform(2, 4))

        new_items = []
        stop = False
        top_id = None

        for page in range(1, 4):
            if stop: break
            if page > 1:
                try:
                    driver.execute_script(f"global.page({page});")
                    time.sleep(random.uniform(2, 4))
                except: break

            items = driver.find_elements(By.CSS_SELECTOR, "li:has(div.cont_box)")
            if not items:
                items = [li for li in driver.find_elements(By.CSS_SELECTOR, "li") if li.find_elements(By.CLASS_NAME, "cont_box")]
            if not items: continue

            for item in items:
                try:
                    a_tag = item.find_element(By.CSS_SELECTOR, "a.tit")
                    pid = ""
                    try: pid = json.loads(a_tag.get_attribute("data-params")).get("encSddpbSeq")
                    except: pass
                    if not pid: continue
                    if top_id is None: top_id = pid
                    if pid == last_id:
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
                        "apply_raw": "", "oper_raw": "", "capacity": "", "time_raw": ""
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
                with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump({"last_id": top_id}, f, ensure_ascii=False, indent=4)
            logger.info("[WITH 비교과] 최초 실행 - 기준점 설정 완료")
        elif new_items:
            send_batch_messages(new_items)
            if top_id:
                with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump({"last_id": top_id}, f, ensure_ascii=False, indent=4)
            logger.info("데이터 저장 완료") 
        else:
            logger.info("변동 사항 없음") 

    except Exception as e:
        raise e
    finally:
        if driver:
            try: driver.quit()
            except: pass

def run_bot():
    logger.info("━" * 20) 
    logger.info("WITH(비교과) 공지봇 검사 시작") 

    try:
        success = False
        last_error = ""
        
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                perform_scraping_cycle()
                success = True
                break
            except Exception as e:
                last_error = str(e)
                logger.warning(f"[WITH 비교과] 실패 ({attempt}/{MAX_RETRIES}): {e}") 
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                    
        if not success:
            error_msg = f"{MAX_RETRIES}회 재시도 실패.\n마지막 에러: {last_error}"
            send_simple_error_log(error_msg, is_fatal=True)

        logger.info("프로그램 정상 종료") 
            
    except Exception as e:
        logger.error(f"치명적 오류: {e}") 
        send_simple_error_log(f"오류로 인한 MAIN 종료\n{e}", is_fatal=True)
    finally:
        force_kill_browsers()

if __name__ == "__main__":
    run_bot()