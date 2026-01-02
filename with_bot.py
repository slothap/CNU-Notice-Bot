import os
import time
import json
import requests
import re
import traceback
from datetime import datetime

# ===[셀레니움(Selenium) 관련 라이브러리]===
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

# ===[설정 영역]==========================
USER_ID = os.environ.get("CNU_ID")
USER_PW = os.environ.get("CNU_PW")
DISCORD_WEBHOOK_URL = os.environ.get("with_WEBHOOK_URL")   # 학생 공지용
MONITOR_WEBHOOK_URL = os.environ.get("MONITOR_WEBHOOK_URL") # 관리자 알림용

# [Only local_test] (주석화 필요)
# DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks"
# MONITOR_WEBHOOK_URL = "https://discord.com/api/webhooks"
# USER_ID = "..."
# USER_PW = "..."

LIST_URL = "https://with.cnu.ac.kr/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmList.do"
DATA_FILE = "with_data.json"
# ==========================================

# ===[텍스트 정리기]=========================
def clean_text(text):
    if not text: return ""
    return re.sub(r'\s+', ' ', text).strip()

# ===[날짜 변환기]=========================
def parse_str_to_dt(date_str):
    if not date_str: return None
    try:
        if ":" in date_str:
            return datetime.strptime(date_str, "%Y.%m.%d %H:%M")
        else:
            return datetime.strptime(date_str, "%Y.%m.%d")
    except:
        return None

# ===[멀티 프로그램 정보 계산]=========================
def calculate_multi_info(sub_items):
    if not sub_items: return None
    app_ends = []
    oper_starts = []
    oper_ends = []
    capacities = []
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
            if nums:
                capacities.append(int(nums[0]))
    result = {"apply": "", "oper": "", "capacity": ""}
    if app_ends:
        min_app = min(app_ends)
        result['apply'] = f"~{min_app.strftime('%m.%d')}"
    if oper_starts and oper_ends:
        min_start = min(oper_starts)
        max_end = max(oper_ends)
        if min_start.date() == max_end.date():
            result['oper'] = f"{min_start.strftime('%m.%d %H:%M')}~{max_end.strftime('%H:%M')}"
        else:
            result['oper'] = f"{min_start.strftime('%m.%d')}~{max_end.strftime('%m.%d')}"
    if capacities:
        min_cap = min(capacities)
        result['capacity'] = f"{min_cap}명"
    return result

# ===[HTML 세부 정보 추출]=========================
def extract_details(container):
    data = {"apply_raw": "", "oper_raw": "", "capacity": ""}
    try:
        info_dls = container.find_elements(By.CSS_SELECTOR, ".etc_info_txt dl")
        for dl in info_dls:
            dt_text = dl.find_element(By.TAG_NAME, "dt").get_attribute("textContent")
            dd_text = dl.find_element(By.TAG_NAME, "dd").get_attribute("textContent")
            if "신청" in dt_text: data["apply_raw"] = clean_text(dd_text)
            elif "운영" in dt_text or "교육기간" in dt_text: data["oper_raw"] = clean_text(dd_text)
    except: pass
    try:
        cap_dls = container.find_elements(By.CSS_SELECTOR, ".rq_desc dl")
        for dl in cap_dls:
            dt_text = dl.find_element(By.TAG_NAME, "dt").get_attribute("textContent")
            if "모집" in dt_text or "정원" in dt_text:
                data["capacity"] = clean_text(dl.find_element(By.TAG_NAME, "dd").get_attribute("textContent"))
    except: pass
    return data

# ===[디코 전송기]=========================
def post_to_discord_safe(content):
    if not DISCORD_WEBHOOK_URL or "http" not in DISCORD_WEBHOOK_URL: 
        print("⚠ 공지용 웹후크 주소가 설정되지 않음")
        return
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=1)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    try:
        response = session.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=10)
        response.raise_for_status()
        print("✉ [전송 성공]")
    except Exception as e:
        print(f"⚠ [전송 실패] {e}")

# ===[메시지 생성 및 전송]=========================
def send_discord_message(info):
    d_day_part = f"{info['d_day']} | " if info['d_day'] else ""
    header = f"### 📢 [{d_day_part}{info['title']}]({info['link']})\n"
    body = ""
    if info['is_multi']:
        if info['sub_items']:
            first_sub = info['sub_items'][0]['title']
            count = len(info['sub_items']) - 1
            if count > 0:
                body += f"- {first_sub} 외 {count}개\n"
            else:
                body += f"- {first_sub}\n"
        parts = []
        if info['multi_calc']['apply']: parts.append(f"**신청**: {info['multi_calc']['apply']}")
        if info['multi_calc']['oper']: parts.append(f"**운영**: {info['multi_calc']['oper']}")
        if info['multi_calc']['capacity']: parts.append(f"**정원**: {info['multi_calc']['capacity']}")
        if parts: body += " | ".join(parts) + "\n"
    else:
        def simple_date(raw):
            m = re.search(r'\d{4}\.(\d{2}\.\d{2})', raw)
            return m.group(1) if m else raw
        def format_single_period(raw, is_apply=False):
            if not raw: return ""
            parts = raw.split('~')
            if len(parts) < 2: return raw
            s = simple_date(parts[0])
            e = simple_date(parts[1])
            return f"~{e}" if is_apply else f"{s}~{e}"
        parts = []
        if info['apply_raw']: parts.append(f"**신청**: {format_single_period(info['apply_raw'], True)}")
        if info['oper_raw']: parts.append(f"**운영**: {format_single_period(info['oper_raw'], False)}")
        if info['capacity']: parts.append(f"**정원**: {info['capacity']}")
        if parts: body += " | ".join(parts) + "\n"
    post_to_discord_safe(header + body)
    print(f"☑ 처리 완료: {info['title']}")

# 관리자 오류 알림 함수
def send_simple_error_log():
    if not MONITOR_WEBHOOK_URL: return 

    now = time.strftime('%Y-%m-%d %H:%M:%S')
    
    # 메시지 내용
    content = f"🚨 **[WITH 봇 오류 발생]** \n{now}"
    
    try:
        requests.post(MONITOR_WEBHOOK_URL, json={"content": content})
        print("✉ [관리자 알림 전송 완료]")
    except:
        print("⚠ 관리자 알림 전송 실패")

# ===[셀레니움 크롤러: 로그인~]=========================
def run_selenium_scraper():
    print("\n" + "━" * 40)
    print("🤖 WITH(비교과) 알람봇 실행")

    try:
        # 1. 크롬 옵션 설정
        chrome_options = Options()
        chrome_options.add_argument("--headless") # 서버용 (필수)
        # chrome_options.add_experimental_option("detach", True) # 서버용 주석처리

        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        wait = WebDriverWait(driver, 20)

        # 2. 로그인
        print(f"☐ 로그인 페이지 접속...")
        driver.get("https://with.cnu.ac.kr/index.do")
        
        try:
            login_btn = wait.until(EC.element_to_be_clickable((By.CLASS_NAME, "login_btn")))
            driver.execute_script("arguments[0].click();", login_btn)
        except Exception as e:
            print(f"⚠ 로그인 버튼 없음: {e}")

        try:
            try:
                id_input = wait.until(EC.visibility_of_element_located((By.NAME, "userId")))
                id_input.clear()
                id_input.send_keys(USER_ID)
                driver.find_element(By.NAME, "password").send_keys(USER_PW)
                driver.find_element(By.NAME, "password").send_keys(Keys.RETURN)
            except:
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                found_iframe = False
                for frame in iframes:
                    driver.switch_to.default_content()
                    driver.switch_to.frame(frame)
                    try:
                        id_input = driver.find_element(By.NAME, "userId")
                        id_input.clear()
                        id_input.send_keys(USER_ID)
                        driver.find_element(By.NAME, "password").send_keys(USER_PW)
                        driver.find_element(By.NAME, "password").send_keys(Keys.RETURN)
                        found_iframe = True
                        driver.switch_to.default_content()
                        print("☑ iframe 내부 로그인 폼 감지")
                        break
                    except: continue
                
                if not found_iframe:
                    raise Exception("로그인 입력창을 찾을 수 없습니다.")
            
            print("☐ 로그인 정보 입력 완료, 대기 중...")
            
            try:
                wait.until(EC.invisibility_of_element_located((By.CLASS_NAME, "login_btn")))
                print("☑ 로그인 성공 확인")
            except:
                print("⚠ 로그인 실패 (로그인 버튼 존재)")

        except Exception as e:
            raise e

        # 3. 데이터 로드
        last_read_id = None
        is_first_run = False
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    last_read_id = data.get("last_read_id")
            except: pass
        if not last_read_id:
            print("☐ [WITH 봇] 최초 실행")
            is_first_run = True

        # 4. 목록 페이지 이동
        driver.get(LIST_URL)
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "li div.cont_box")))
        except:
            raise Exception("목록 로딩 실패 (타임아웃)")

        new_items = []
        stop_scanning = False
        latest_id_on_top = None

        # 5. 스캔
        for page in range(1, 4): 
            if stop_scanning: break
            print(f"\n☐ [페이지 {page}] 분석 중...")
            if page > 1:
                try:
                    driver.execute_script(f"global.page({page});")
                    time.sleep(2)
                except: break
            items = driver.find_elements(By.CSS_SELECTOR, "li:has(div.cont_box)")
            if not items:
                all_lis = driver.find_elements(By.CSS_SELECTOR, "li")
                items = [li for li in all_lis if li.find_elements(By.CLASS_NAME, "cont_box")]

            for item in items:
                try:
                    title_link = item.find_element(By.CSS_SELECTOR, "a.tit")
                    data_params = title_link.get_attribute("data-params")
                    program_id = ""
                    if data_params and "encSddpbSeq" in data_params:
                        import json as pyjson
                        try:
                            program_id = pyjson.loads(data_params).get("encSddpbSeq")
                        except: pass
                    
                    if not program_id: continue
                    if latest_id_on_top is None: latest_id_on_top = program_id
                    if program_id == last_read_id:
                        print("⊙ 기존 글 도착. 스캔 종료.")
                        stop_scanning = True
                        break
                    
                    if is_first_run: continue

                    real_link = f"https://with.cnu.ac.kr/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmInfo.do?encSddpbSeq={program_id}&paginationInfo.currentPageNo=1"
                    full_title_text = title_link.get_attribute("textContent")
                    try:
                        label_text = title_link.find_element(By.CLASS_NAME, "label").get_attribute("textContent")
                        final_title = full_title_text.replace(label_text, "")
                    except:
                        final_title = full_title_text
                    title = clean_text(final_title)
                    try: d_day = clean_text(item.find_element(By.CSS_SELECTOR, "span.day").get_attribute("textContent"))
                    except: d_day = ""
                    is_multi = "multi_class" in item.get_attribute("class")

                    parsed_data = {
                        "id": program_id, "title": title, "d_day": d_day, "link": real_link,
                        "is_multi": is_multi, "sub_items": [], "apply_raw": "", "oper_raw": "", "capacity": "", "multi_calc": {}
                    }
                    try:
                        more_btns = item.find_elements(By.CLASS_NAME, "class_more_open")
                        if more_btns and more_btns[0].is_displayed():
                            driver.execute_script("arguments[0].click();", more_btns[0])
                            time.sleep(0.5)
                    except: pass

                    if is_multi:
                        sub_conts = item.find_elements(By.CLASS_NAME, "class_cont")
                        for sub in sub_conts:
                            if not sub.get_attribute("textContent").strip(): continue
                            try:
                                sub_a = sub.find_element(By.CSS_SELECTOR, "a.tit")
                                sub_full = sub_a.get_attribute("textContent")
                                try:
                                    lbl = sub_a.find_element(By.CLASS_NAME, "label").get_attribute("textContent")
                                    sub_title = sub_full.replace(lbl, "")
                                except: sub_title = sub_full
                                sub_title = clean_text(sub_title)
                            except: continue
                            details = extract_details(sub)
                            parsed_data['sub_items'].append({"title": sub_title, **details})
                        parsed_data['multi_calc'] = calculate_multi_info(parsed_data['sub_items'])
                    else:
                        details = extract_details(item)
                        parsed_data.update(details)
                    new_items.append(parsed_data)
                except Exception as e:
                    print(f"⚠ 파싱 오류: {e}")
                    continue
        
        # 6. 결과 처리
        if is_first_run:
            if latest_id_on_top:
                with open(DATA_FILE, "w", encoding="utf-8") as f:
                    json.dump({"last_read_id": latest_id_on_top}, f, indent=4)
                print(f"☑ 기준점 설정 완료 (ID: {latest_id_on_top})")
            else:
                print("⚠ 게시글 없음")
        elif new_items:
            print(f"● {len(new_items)}개 새 글 전송")
            for item in reversed(new_items):
                send_discord_message(item)
                time.sleep(1)
            if latest_id_on_top:
                with open(DATA_FILE, "w", encoding="utf-8") as f:
                    json.dump({"last_read_id": latest_id_on_top}, f, indent=4)
                print("☑ 데이터 저장 완료")
        else:
            print("☒ 새 글 없음")

    # 에러 발생 시 처리
    except Exception as e:
        print(f"⚠ 오류 발생: {e}")
        
        # 1. 깃허브 로그용으로 상세 에러는 콘솔에 찍음
        traceback.print_exc() 
        
        # 2. 관리자 알림은 간단하게
        send_simple_error_log()

    finally:
        if 'driver' in locals():
            driver.quit()

if __name__ == "__main__":
    run_selenium_scraper()