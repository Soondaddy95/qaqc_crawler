# ============================================================
# [Attendance Bot] 출석 자동 집계 (Direct URL Ver.)
# ============================================================

# 👇 [수집 날짜 설정] None = 자동(오늘/어제), "2025-12-01" = 특정 날짜
TARGET_DATE_OVERRIDE = None 

import time
import os
import sys
import json
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException

load_dotenv() 

# ============================================================
# 1. Config
# ============================================================
class Config:
    IS_SERVER = os.environ.get("GITHUB_ACTIONS") == "true"
    BACKOFFICE_URL = os.environ.get("BACKOFFICE_URL", "https://h99backoffice.spartaclub.kr/")
    
    # 👇 [핵심] 메뉴 클릭 없이 바로 가는 주소
    ATTENDANCE_URL = "https://h99backoffice.spartaclub.kr/nbcamp/users/dashboard"
    
    COURSE_NAME = "QA 4기"
    COURSE_KEYWORDS = ["KDT", "QA", "4"]
    BATCH_NAME = "4회차"
    CATEGORY = "QA/QC"

    LATE_CUTOFF = "09:10"
    LEAVE_CUTOFF = "21:00"
    
    USER_DATA_DIR = os.path.expanduser("~/apm_profile")
    CHROME_DEBUG_PORT = 9222 
    
    if sys.platform == "darwin":
        CHROME_APP_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    else:
        CHROME_APP_PATH = "/usr/bin/google-chrome"

    WAIT_TIMEOUT = 30
    CHROME_LAUNCH_WAIT = 4
    DATA_COLLECTION_WAIT = 1.0
    MODAL_WAIT = 1.5

    HOLIDAYS_KR = {
        "2025-01-01": "신정", "2025-01-27": "설날 연휴", "2025-01-28": "설날", 
        "2025-01-29": "설날 연휴", "2025-01-30": "설날 대체공휴일",
        "2025-03-01": "삼일절", "2025-03-03": "삼일절 대체공휴일", 
        "2025-05-05": "어린이날", "2025-05-06": "부처님오신날 대체공휴일",
        "2025-06-03": "대통령선거(임시)", "2025-06-06": "현충일", 
        "2025-08-15": "광복절", "2025-10-03": "개천절",
        "2025-10-05": "추석 연휴", "2025-10-06": "추석", "2025-10-07": "추석 연휴", 
        "2025-10-08": "추석 대체공휴일", "2025-10-09": "한글날", "2025-12-25": "크리스마스"
    }

# ============================================================
# 2. DateCalculator
# ============================================================
class DateCalculator:
    @staticmethod
    def get_target_date(config: Config) -> str:
        # KST 시간 보정
        kst_now = datetime.utcnow() + timedelta(hours=9)
        today = kst_now.date()
        today_str = today.strftime("%Y-%m-%d")
        
        print(f"🕒 [Timezone] 한국 시간(KST): {kst_now.strftime('%Y-%m-%d %H:%M:%S')}")
        
        if today.weekday() >= 5:
            print(f"🛌 오늘은 주말({today_str})입니다. 봇이 쉽니다.")
            return None
        if today_str in config.HOLIDAYS_KR:
            print(f"🏖️ 오늘은 공휴일({config.HOLIDAYS_KR[today_str]})입니다. 봇이 쉽니다.")
            return None
            
        return today_str

# ============================================================
# 3. ChromeManager
# ============================================================
class ChromeManager:
    @staticmethod
    def is_port_open(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(('127.0.0.1', port)) == 0

    @staticmethod
    def launch_chrome(config: Config):
        options = webdriver.ChromeOptions()
        
        if config.IS_SERVER:
            print("☁️ [서버 모드] Headless 실행")
            options.add_argument("--headless=new") 
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920,1080")
            user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            options.add_argument(f"user-agent={user_agent}")
            
            try:
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
                driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                return driver
            except Exception as e:
                print(f"❌ 크롬 실행 실패: {e}")
                sys.exit(1)

        else:
            print("🍎 [로컬 모드] 스마트 연결 시도...")
            if not ChromeManager.is_port_open(config.CHROME_DEBUG_PORT):
                print(f"   💨 크롬 실행...")
                cmd = [
                    config.CHROME_APP_PATH,
                    f"--remote-debugging-port={config.CHROME_DEBUG_PORT}",
                    f"--user-data-dir={config.USER_DATA_DIR}"
                ]
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(3)
            else:
                print(f"   ⚡ 기존 크롬 연결")

            options.add_experimental_option("debuggerAddress", f"127.0.0.1:{config.CHROME_DEBUG_PORT}")
            try:
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
                return driver
            except Exception as e:
                print(f"❌ 연결 실패: {e}")
                sys.exit(1)

# ============================================================
# 4. Attendance Crawler (직통 URL 적용)
# ============================================================
class AttendanceCrawler:
    def __init__(self, driver, config: Config):
        self.driver = driver
        self.config = config
        self.wait = WebDriverWait(driver, config.WAIT_TIMEOUT)
    
    def force_click(self, element):
        self.driver.execute_script("arguments[0].click();", element)

    def navigate_to_attendance(self):
        """쿠키 주입 후 직통 URL로 이동 (메뉴 클릭 삭제)"""
        print("\n🔗 백오피스 진입 (쿠키 작업 시작)...")
        
        # 1. 도메인 설정을 위해 메인 페이지 먼저 접속 (빈 페이지라도 가야 함)
        self.driver.get(self.config.BACKOFFICE_URL)
        
        # [서버] 쿠키 주입
        if self.config.IS_SERVER:
            cookies_json = os.environ.get("BACKOFFICE_COOKIES")
            if cookies_json:
                print("🍪 [서버] 쿠키 주입 시도...")
                try:
                    cookies = json.loads(cookies_json)
                    for cookie in cookies:
                        if 'expiry' in cookie: del cookie['expiry']
                        if 'sameSite' in cookie: del cookie['sameSite']
                        if 'domain' in cookie: del cookie['domain']
                        try: self.driver.add_cookie(cookie)
                        except: pass
                    
                    print("✅ 쿠키 주입 완료.")
                except Exception as e: print(f"⚠️ 쿠키 에러: {e}")
        
        # 2. [핵심] 직통 URL로 점프!
        print(f"🚀 대시보드로 순간이동: {self.config.ATTENDANCE_URL}")
        self.driver.get(self.config.ATTENDANCE_URL)
        
        # 3. 로컬/서버 모두 로딩 대기
        time.sleep(5) 

        # 4. 로그인 성공 여부 확인
        current_url = self.driver.current_url
        print(f"👀 현재 페이지: {current_url}")
        
        if "login" in current_url or "google.com" in current_url:
            print("🚨 [치명적 오류] 로그인 페이지로 튕겼습니다. (쿠키 만료 또는 세션 없음)")
            if self.config.IS_SERVER:
                raise Exception("LOGIN_FAILED")
            else:
                print("👉 [로컬] 직접 로그인 후 터미널에서 엔터를 치세요.")
                input()
        else:
            print("✅ 로그인 유지 성공!")

    def select_options(self):
        print("👉 [출석부] 옵션 선택 시작...")
        try:
            # 1. [카테고리] QA/QC
            try:
                cat_xpath = "//span[contains(text(), 'QA/QC')]"
                cat_elem = self.wait.until(EC.element_to_be_clickable((By.XPATH, cat_xpath)))
                self.force_click(cat_elem)
                print("   ✅ 카테고리 'QA/QC' 선택")
                time.sleep(1)
            except: pass

            # 2. [기수 선택] ActionChains
            print("   ⏳ 기수(KDT) 선택 중...")
            try:
                course_box = self.wait.until(EC.visibility_of_element_located((
                    By.XPATH, 
                    "//div[contains(@class, 'ant-select-selector') and .//span[contains(@title, 'KDT')]]"
                )))
                actions = ActionChains(self.driver)
                actions.move_to_element(course_box).click().perform()
                time.sleep(1)

                target_course = "4회차"
                course_opt = self.wait.until(EC.element_to_be_clickable((
                    By.XPATH, 
                    f"//div[contains(@class, 'ant-select-item-option') and contains(., '{target_course}')]"
                )))
                self.force_click(course_opt)
                print(f"   ✅ 기수 '{target_course}' 선택 완료")
            except Exception as e:
                print(f"   ⚠️ 기수 선택 패스: {e}")
            
            time.sleep(2)

            # 3. [마케팅 기수 선택]
            print("   ⏳ 마케팅 기수 선택 중...")
            dropdowns = self.driver.find_elements(By.CSS_SELECTOR, ".ant-select-selector")
            if len(dropdowns) >= 2:
                marketing_box = dropdowns[1]
                try:
                    actions = ActionChains(self.driver)
                    actions.move_to_element(marketing_box).click().perform()
                except:
                    self.force_click(marketing_box)
                time.sleep(1)
                
                marketing_target = "품질관리(QAQC)" 
                try:
                    marketing_opt = self.wait.until(EC.element_to_be_clickable((
                        By.XPATH, 
                        f"//div[contains(@class, 'ant-select-item-option') and contains(., '{marketing_target}')]"
                    )))
                    self.force_click(marketing_opt)
                    print(f"   ✅ 마케팅 기수 '{marketing_target}' 선택 완료")
                except: pass
            else:
                print("   ⚠️ 두 번째 드롭다운 못 찾음")

            time.sleep(1)

            # 4. [조회] 버튼
            print("   🔍 조회 버튼 클릭...")
            try:
                search_btn = self.driver.find_element(By.XPATH, "//button[contains(., '조회')]")
                self.force_click(search_btn)
                print("   ✅ 조회 버튼 클릭 완료")
            except: pass
            
            time.sleep(5)

        except Exception as e:
            print(f"❌ 옵션 선택 중 오류: {e}")

    def collect_data(self, target_date) -> list:
        print(f"\n🐢 출석 데이터 수집 시작 (타겟: {target_date})")
        total_data = []
        
        print("   ⏳ 테이블 로딩 중...")
        try:
            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "css-1xm32e0")))
            time.sleep(2)
        except:
            print("   ⚠️ 데이터 로딩 실패 or 없음")
            return []
        
        rows = self.driver.find_elements(By.CLASS_NAME, "css-1xm32e0")
        print(f"   📄 총 {len(rows)}명의 데이터 발견")

        for i, row in enumerate(rows):
            try:
                text_list = row.text.split('\n')
                if len(text_list) < 5: continue

                name = text_list[0].strip()     # 0번: 이름
                in_time = text_list[3].strip()  # 3번: 입실
                out_time = text_list[4].strip() # 4번: 퇴실

                if in_time == "-": in_time = ""
                if out_time == "-": out_time = ""
                
                status = 0
                if in_time:
                    if in_time <= self.config.LATE_CUTOFF:
                        status = 1 # 정상
                        if out_time and out_time < self.config.LEAVE_CUTOFF:
                            status = 0.5 # 조퇴
                        elif not out_time:
                             status = 0.5 
                    else:
                        status = 0.5 # 지각
                        
                if i % 5 == 0:
                    print(f"   🔍 {name}: {in_time if in_time else '-'} ~ {out_time if out_time else '-'} -> 점수: {status}")

                total_data.append({
                    "날짜": target_date,
                    "이름": name,
                    "입실시간": in_time if in_time else "-",
                    "퇴실시간": out_time if out_time else "-",
                    "상태": status
                })
            except Exception as e:
                print(f"   ❌ {i+1}번째 행 에러: {e}")
                continue
        return total_data

# ============================================================
# 5. 구글 시트 업로더
# ============================================================
class AttendanceSheetManager:
    def __init__(self):
        json_file = "qaqc-pipeline.json"
        sheet_url = os.environ.get("TIL_SHEET_URL")
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(json_file, scope)
        client = gspread.authorize(creds)
        self.sheet = client.open_by_url(sheet_url)
        self.worksheet = self.sheet.worksheet("raw_attendance_logs") 

    def save_data(self, new_data):
        df = pd.DataFrame(new_data)
        existing_data = self.worksheet.get_all_records()
        existing_df = pd.DataFrame(existing_data)
        
        target_date = new_data[0]['날짜']
        if not existing_df.empty:
            existing_df['날짜'] = existing_df['날짜'].astype(str)
            existing_df = existing_df[existing_df['날짜'] != target_date]
            
        final_df = pd.concat([df, existing_df], ignore_index=True)
        final_df = final_df.fillna("-")
        if '날짜' in final_df.columns:
            final_df = final_df.sort_values(by='날짜', ascending=False)

        self.worksheet.clear()
        self.worksheet.update([final_df.columns.values.tolist()] + final_df.values.tolist())
        print("✅ 출석 데이터 저장 완료!")

# ============================================================
# 6. 실행부
# ============================================================
if __name__ == "__main__":
    print("🔥 [출석 봇] 가동 시작")
    config = Config()
    
    if TARGET_DATE_OVERRIDE:
        target_date = TARGET_DATE_OVERRIDE
        print(f"🛠️ [수동 모드] 날짜: {target_date}")
    else:
        target_date = DateCalculator.get_target_date(config)
        print(f"🤖 [자동 모드] 날짜: {target_date}")

    if target_date:
        driver = ChromeManager.launch_chrome(config)
        if driver:
            try:
                crawler = AttendanceCrawler(driver, config)
                crawler.navigate_to_attendance() 
                crawler.select_options()         
                data = crawler.collect_data(target_date) 
                if data:
                    print(f"📊 {len(data)}건 수집 완료")
                    try:
                        uploader = AttendanceSheetManager()
                        uploader.save_data(data)
                    except Exception as e:
                        print(f"❌ 시트 저장 실패: {e}")
                else:
                    print("⚠️ 수집된 데이터 없음")
            except Exception as e:
                print(f"❌ 에러 발생: {e}")
            finally:
                pass
    else:
        print("😴 주말/공휴일입니다.")