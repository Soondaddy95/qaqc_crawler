# ============================================================
# [Attendance Bot] 출석/지각/조퇴 자동 집계 (Smart Attach Ver.)
# ============================================================

# 👇 [수집 날짜 설정] None = 자동(오늘), "2025-11-30" = 특정 날짜
TARGET_DATE_OVERRIDE = None 

import time
import os
import sys
import json
import socket
import subprocess
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
    
    COURSE_NAME = "QA 4기"
    COURSE_KEYWORDS = ["KDT", "QA", "4"]
    BATCH_NAME = "4회차"
    CATEGORY = "QA/QC"

    LATE_CUTOFF = "09:10"
    LEAVE_CUTOFF = "21:00"
    
    USER_DATA_DIR = os.path.expanduser("~/apm_profile")
    CHROME_DEBUG_PORT = 9222 # 포트 고정
    
    if sys.platform == "darwin":
        CHROME_APP_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    else:
        CHROME_APP_PATH = "/usr/bin/google-chrome"

    WAIT_TIMEOUT = 20
    DATA_COLLECTION_WAIT = 0.5
    MODAL_WAIT = 0.8

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
        """
        실행 시점(한국 시간 KST)을 기준으로 수집 여부 판단
        """
        # 1. [핵심 수정] UTC 시간에 9시간을 더해 한국 시간(KST)을 만듭니다.
        kst_now = datetime.utcnow() + timedelta(hours=9)
        today = kst_now.date()
        today_str = today.strftime("%Y-%m-%d")
        
        # 디버깅용 로그 (서버 시간이 맞는지 확인)
        print(f"🕒 [Timezone] 한국 시간(KST): {kst_now.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # ---------------------------------------------------------
        # 2. 오늘이 주말/공휴일인지 체크
        # ---------------------------------------------------------
        
        # 주말 체크 (토=5, 일=6)
        # 월요일(0) 00시 50분에 실행되면 -> 통과!
        if today.weekday() >= 5:
            print(f"🛌 오늘은 주말({today_str})입니다. 봇이 쉽니다.")
            return None
            
        # 공휴일 체크
        if today_str in config.HOLIDAYS_KR:
            print(f"🏖️ 오늘은 공휴일({config.HOLIDAYS_KR[today_str]})입니다. 봇이 쉽니다.")
            return None
            
        # 3. 평일이면 -> '오늘' 날짜를 타겟으로 반환
        return today_str

# ============================================================
# 3. ChromeManager (스마트 연결 모드)
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
        
        # [서버] Headless
        if config.IS_SERVER:
            print("☁️ [서버 모드] Headless 실행")
            options.add_argument("--headless=new") 
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920,1080")
            user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            options.add_argument(f"user-agent={user_agent}")
            
            try:
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
                driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                return driver
            except Exception as e:
                print(f"❌ 크롬 실행 실패: {e}")
                sys.exit(1)

        # [로컬] 스마트 연결 (포트 9222)
        else:
            print("🍎 [로컬 모드] 스마트 연결 시도...")
            
            # 포트가 닫혀있으면 새로 실행
            if not ChromeManager.is_port_open(config.CHROME_DEBUG_PORT):
                print(f"   💨 크롬이 꺼져있습니다. 디버깅 모드로 실행합니다...")
                cmd = [
                    config.CHROME_APP_PATH,
                    f"--remote-debugging-port={config.CHROME_DEBUG_PORT}",
                    f"--user-data-dir={config.USER_DATA_DIR}"
                ]
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(3)
            else:
                print(f"   ⚡ 이미 켜진 크롬에 연결합니다.")

            # 디버깅 포트로 연결
            options.add_experimental_option("debuggerAddress", f"127.0.0.1:{config.CHROME_DEBUG_PORT}")
            
            try:
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
                return driver
            except Exception as e:
                print(f"❌ 연결 실패: {e}")
                print("💡 팁: 터미널에서 'pkill Chrome' 입력 후 다시 시도해보세요.")
                sys.exit(1)

# ============================================================
# 4. Attendance Crawler
# ============================================================
class AttendanceCrawler:
    def __init__(self, driver, config: Config):
        self.driver = driver
        self.config = config
        self.wait = WebDriverWait(driver, config.WAIT_TIMEOUT)
    
    def force_click(self, element):
        self.driver.execute_script("arguments[0].click();", element)

    def navigate_to_attendance(self):
        print("\n🔗 백오피스 진입 중...")
        self.driver.get(self.config.BACKOFFICE_URL)
        
        # ---------------------------------------------------------
        # 🍪 [추가된 부분] 쿠키 주입 로직 (서버 환경 필수)
        # ---------------------------------------------------------
        if self.config.IS_SERVER:
            cookies_json = os.environ.get("BACKOFFICE_COOKIES")
            if cookies_json:
                print("🍪 [서버] 쿠키 주입 시도...")
                try:
                    cookies = json.loads(cookies_json)
                    for cookie in cookies:
                        if 'expiry' in cookie: del cookie['expiry']
                        if 'sameSite' in cookie: del cookie['sameSite']
                        if 'domain' in cookie: del cookie['domain'] # 핵심!
                        try: self.driver.add_cookie(cookie)
                        except: pass
                    
                    print("🔄 쿠키 적용 후 새로고침...")
                    self.driver.refresh()
                    time.sleep(5) # 로그인 적용 대기
                    
                    # 경고창 있으면 닫기
                    try:
                        alert = self.driver.switch_to.alert
                        alert.accept()
                    except: pass
                    
                except Exception as e: print(f"⚠️ 쿠키 에러: {e}")
        else:
            # 로컬
            time.sleep(3)

        # ---------------------------------------------------------
        # 메뉴 이동 로직 (기존과 동일)
        # ---------------------------------------------------------
        print("👉 메뉴 이동 시작...")
        try:
            # 1. '내배캠 운영' 펼치기
            try:
                parent_menu = self.driver.find_element(By.XPATH, "//*[contains(text(), '내배캠 운영')]")
                if parent_menu.is_displayed():
                    self.force_click(parent_menu)
                    time.sleep(1)
            except: pass

            # 2. '출결 관리' 클릭
            att_menu = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), '출결 관리')]")))
            self.force_click(att_menu)
            time.sleep(1)
            
            # 3. '본캠프 출결 대시보드' 클릭
            dashboard_menu = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), '본캠프 출결 대시보드')]")))
            self.force_click(dashboard_menu)
            
            time.sleep(3) # 페이지 로딩 대기
            print("✅ 출결 대시보드 진입 성공")

        except Exception as e:
            print(f"❌ 메뉴 이동 실패: {e}")
            # 로그인 실패 시 여기서 멈추도록 에러 던지기
            if "로그인이 필요합니다" in str(e):
                raise Exception("LOGIN_FAILED: 쿠키 만료됨.")

    def select_options(self):
        print("👉 [출석부] 옵션 선택 시작...")
        try:
            try:
                cat_xpath = "//span[contains(text(), 'QA/QC')]"
                cat_elem = self.wait.until(EC.element_to_be_clickable((By.XPATH, cat_xpath)))
                self.force_click(cat_elem)
                print("   ✅ 카테고리 'QA/QC' 선택")
                time.sleep(1)
            except: pass

            print("   ⏳ 기수(KDT) 드롭다운 여는 중...")
            try:
                course_box = self.wait.until(EC.visibility_of_element_located((
                    By.XPATH, "//div[contains(@class, 'ant-select-selector') and .//span[contains(@title, 'KDT')]]"
                )))
                actions = ActionChains(self.driver)
                actions.move_to_element(course_box).click().perform()
                print("   🖱️ [ActionChains] 기수 드롭다운 클릭")
                time.sleep(1)

                target_course = "4회차"
                course_opt = self.wait.until(EC.element_to_be_clickable((
                    By.XPATH, f"//div[contains(@class, 'ant-select-item-option') and contains(., '{target_course}')]"
                )))
                self.force_click(course_opt)
                print(f"   ✅ 기수 '{target_course}' 선택 완료")
            except Exception as e: print(f"   ⚠️ 기수 선택 패스: {e}")
            
            time.sleep(2)

            print("   ⏳ 마케팅 기수 선택 중...")
            dropdowns = self.driver.find_elements(By.CSS_SELECTOR, ".ant-select-selector")
            if len(dropdowns) >= 2:
                marketing_box = dropdowns[1]
                try:
                    actions = ActionChains(self.driver)
                    actions.move_to_element(marketing_box).click().perform()
                    print("   🖱️ [ActionChains] 마케팅 드롭다운 클릭")
                except: self.force_click(marketing_box)
                time.sleep(1)
                
                marketing_target = "품질관리(QAQC)" 
                try:
                    marketing_opt = self.wait.until(EC.element_to_be_clickable((
                        By.XPATH, f"//div[contains(@class, 'ant-select-item-option') and contains(., '{marketing_target}')]"
                    )))
                    self.force_click(marketing_opt)
                    print(f"   ✅ 마케팅 기수 '{marketing_target}' 선택 완료")
                except: pass
            else: print("   ⚠️ 두 번째 드롭다운 못 찾음")

            time.sleep(1)
            print("   🔍 조회 버튼 클릭...")
            try:
                search_btn = self.driver.find_element(By.XPATH, "//button[contains(., '조회')]")
                self.force_click(search_btn)
                print("   ✅ 조회 버튼 클릭 완료")
            except: pass
            time.sleep(3)
        except Exception as e: print(f"❌ 옵션 선택 중 오류: {e}")

    def collect_data(self, target_date) -> list:
        print(f"\n🐢 출석 데이터 수집 시작 (타겟: {target_date})")
        total_data = []
        
        print("   ⏳ 테이블 로딩 중...")
        try:
            WebDriverWait(self.driver, 15).until(EC.presence_of_element_located((By.CLASS_NAME, "css-1xm32e0")))
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

                name = text_list[0].strip()
                in_time = text_list[3].strip()
                out_time = text_list[4].strip()

                if in_time == "-": in_time = ""
                if out_time == "-": out_time = ""
                
                status = 0
                if in_time:
                    if in_time <= self.config.LATE_CUTOFF:
                        status = 1 # 정상
                        if out_time and out_time < self.config.LEAVE_CUTOFF:
                            status = 0.5 # 조퇴
                        elif not out_time:
                             # 밤 늦게 돌릴 때 퇴실 없으면 미체크(0.5)
                             status = 0.5 
                    else:
                        status = 0.5 # 지각
                        
                if i % 5 == 0:
                    print(f"   🔍 {name}: {in_time if in_time else '-'} ~ {out_time if out_time else '-'} -> 점수: {status}")

                total_data.append({
                    "이름": name,
                    "날짜": target_date,
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
                # driver.quit() 
                pass
    else:
        print("😴 주말/공휴일입니다.")